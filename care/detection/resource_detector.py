"""Detect resource lifetime imbalance opportunities."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Optional

from care.analysis.context_graph import ContextGraph
from care.analysis.ownership import (
    is_ownership_transfer_call,
    is_release_wrapper_call,
)
from care.analysis.security_knowledge import SecurityKnowledgeBase
from care.core.models import FunctionInfo, RefactoringOpportunity, ResourceEvent
from care.detection.common import (
    analysis_backend,
    confidence,
    evidence_confidence_label,
    has_clang_cfg,
    iter_code_lines,
    make_opportunity,
    return_statement,
    verifier_verdict,
)

ACQUIRE_TO_RELEASE = {
    "alloc": "free",
    "open": "close",
    "lock": "unlock",
}
RELEASE_TO_ACQUIRE = {
    "free": "alloc",
    "close": "open",
    "unlock": "lock",
}
MISSING_DESCRIPTIONS = {
    "alloc": "missing free",
    "open": "missing close/fclose",
    "lock": "missing unlock",
}
DOUBLE_DESCRIPTIONS = {
    "free": "double free",
    "close": "double close/fclose",
    "unlock": "double unlock",
}


class ResourceDetector:
    """Detect leaks, double releases, and early returns with light path sensitivity."""

    def detect(
        self,
        function: FunctionInfo,
        context_graph: ContextGraph,
        knowledge_base: SecurityKnowledgeBase,
    ) -> list[RefactoringOpportunity]:
        events = sorted(
            knowledge_base.match_resource_events(function),
            key=lambda event: (event.location.line, event.location.column or 0, event.kind),
        )
        opportunities: list[RefactoringOpportunity] = []
        opportunities.extend(self._detect_missing_releases(function, context_graph, events))
        opportunities.extend(self._detect_double_releases(function, context_graph, events))
        opportunities.extend(self._detect_early_returns(function, context_graph, events))
        return opportunities

    def _detect_missing_releases(
        self,
        function: FunctionInfo,
        context_graph: ContextGraph,
        events: list[ResourceEvent],
    ) -> list[RefactoringOpportunity]:
        opportunities: list[RefactoringOpportunity] = []
        events_by_resource = _events_by_resource(events)
        for resource_key, variable_events in events_by_resource.items():
            variable = _display_variable(variable_events, resource_key)
            for acquire in [event for event in variable_events if event.kind in ACQUIRE_TO_RELEASE]:
                release_kind = ACQUIRE_TO_RELEASE[acquire.kind]
                release = _first_event_after(variable_events, release_kind, acquire.location.line)
                if release is not None:
                    continue
                if _ownership_consumed_after(function, resource_key, acquire.location.line):
                    continue
                evidence_meta = _verified_evidence(
                    function=function,
                    context_graph=context_graph,
                    base=0.58,
                    path_confirmed=False,
                    checks=[
                        "acquire event has no matching primitive release",
                        "no wrapper release or ownership transfer seen after acquire",
                    ],
                )
                opportunities.append(
                    make_opportunity(
                        function=function,
                        context_graph=context_graph,
                        kind="resource_imbalance",
                        line=acquire.location.line,
                        column=acquire.location.column,
                        severity="high",
                        description=(
                            f"Resource {variable} has {MISSING_DESCRIPTIONS[acquire.kind]} "
                            f"after {acquire.kind}."
                        ),
                        evidence={
                            "resource_variable": variable,
                            "resource_key": resource_key,
                            "resource_event": acquire.to_dict(),
                            "expected_release": release_kind,
                            "pattern": MISSING_DESCRIPTIONS[acquire.kind],
                            "path_sensitive": True,
                            "resource_state_trace": _state_trace(variable_events),
                            **evidence_meta,
                        },
                    )
                )
        return opportunities

    def _detect_double_releases(
        self,
        function: FunctionInfo,
        context_graph: ContextGraph,
        events: list[ResourceEvent],
    ) -> list[RefactoringOpportunity]:
        opportunities: list[RefactoringOpportunity] = []
        events_by_resource = _events_by_resource(events)
        for resource_key, variable_events in events_by_resource.items():
            variable = _display_variable(variable_events, resource_key)
            for release_kind in RELEASE_TO_ACQUIRE:
                releases = [event for event in variable_events if event.kind == release_kind]
                if len(releases) < 2:
                    continue
                acquire_kind = RELEASE_TO_ACQUIRE[release_kind]
                for previous, release in zip(releases, releases[1:]):
                    if _has_reacquire_between(variable_events, acquire_kind, previous.location.line, release.location.line):
                        continue
                    if _has_resource_reset_between(function, resource_key, previous.location.line, release.location.line):
                        continue
                    same_path = _same_must_path(function, previous.location.line, release.location.line)
                    if not same_path:
                        continue
                    evidence_meta = _verified_evidence(
                        function=function,
                        context_graph=context_graph,
                        base=0.72,
                        path_confirmed=True,
                        checks=[
                            "two releases of the same canonical resource expression",
                            "no reacquire/reset/null assignment between releases",
                            "no obvious branch/goto/return boundary between releases",
                        ],
                    )
                    opportunities.append(
                        make_opportunity(
                            function=function,
                            context_graph=context_graph,
                            kind="resource_imbalance",
                            line=release.location.line,
                            column=release.location.column,
                            severity="critical",
                            description=f"Resource {variable} may have {DOUBLE_DESCRIPTIONS[release_kind]}.",
                            evidence={
                                "resource_variable": variable,
                                "resource_key": resource_key,
                                "resource_events": [event.to_dict() for event in releases],
                                "expected_acquire": acquire_kind,
                                "pattern": DOUBLE_DESCRIPTIONS[release_kind],
                                "path_sensitive": True,
                                "resource_state_trace": _state_trace(variable_events),
                                **evidence_meta,
                            },
                        )
                    )
        return opportunities

    def _detect_early_returns(
        self,
        function: FunctionInfo,
        context_graph: ContextGraph,
        events: list[ResourceEvent],
    ) -> list[RefactoringOpportunity]:
        opportunities: list[RefactoringOpportunity] = []
        returns = [
            (line_number, raw_line, statement)
            for line_number, raw_line, statement in iter_code_lines(function)
            if return_statement(statement)
        ]
        final_return_line = returns[-1][0] if returns else None
        events_by_resource = _events_by_resource(events)
        for resource_key, variable_events in events_by_resource.items():
            variable = _display_variable(variable_events, resource_key)
            for acquire in [event for event in variable_events if event.kind in {"alloc", "open", "lock"}]:
                release = _first_event_after(
                    variable_events,
                    ACQUIRE_TO_RELEASE[acquire.kind],
                    acquire.location.line,
                )
                release_line = release.location.line if release else function.end_line + 1
                for line_number, raw_line, statement in returns:
                    if release is None and line_number == final_return_line:
                        continue
                    if not (acquire.location.line < line_number < release_line):
                        continue
                    if _has_cleanup_goto_before(function, acquire.location.line, line_number):
                        continue
                    if _released_or_transferred_between(function, resource_key, acquire.location.line, line_number):
                        continue
                    pattern = (
                        "allocation followed by early return"
                        if acquire.kind == "alloc"
                        else "open followed by early return"
                        if acquire.kind == "open"
                        else "lock followed by early return"
                    )
                    evidence_meta = _verified_evidence(
                        function=function,
                        context_graph=context_graph,
                        base=0.6,
                        path_confirmed=True,
                        checks=[
                            "acquire happens before early return",
                            "matching primitive release appears only after that return or is absent",
                            "no cleanup goto, wrapper release, or ownership transfer before return",
                        ],
                    )
                    opportunities.append(
                        make_opportunity(
                            function=function,
                            context_graph=context_graph,
                            kind="resource_early_return",
                            line=line_number,
                            column=raw_line.find("return") + 1 if "return" in raw_line else None,
                            severity="high",
                            description=f"Early return may bypass cleanup for {variable}.",
                            evidence={
                                "resource_variable": variable,
                                "resource_key": resource_key,
                                "acquire_event": acquire.to_dict(),
                                "release_event": release.to_dict() if release else None,
                                "return_statement": statement,
                                "pattern": pattern,
                                "path_sensitive": True,
                                "resource_state_trace": _state_trace(variable_events),
                                **evidence_meta,
                            },
                        )
                    )
        return opportunities


def _events_by_resource(events: list[ResourceEvent]) -> dict[str, list[ResourceEvent]]:
    grouped: dict[str, list[ResourceEvent]] = defaultdict(list)
    for event in events:
        key = _resource_key(event.variable)
        if key:
            grouped[key].append(event)
    for key in grouped:
        grouped[key].sort(key=lambda event: (event.location.line, event.location.column or 0, event.kind))
    return grouped


def _first_event_after(
    events: list[ResourceEvent],
    kind: str,
    line: int,
) -> Optional[ResourceEvent]:
    for event in events:
        if event.kind == kind and event.location.line > line:
            return event
    return None


def _same_must_path(function: FunctionInfo, first_line: int, second_line: int) -> bool:
    for line_number, _, statement in iter_code_lines(function):
        if line_number <= first_line or line_number >= second_line:
            continue
        if return_statement(statement) or statement.startswith("goto "):
            return False
        if statement.startswith(
            ("if ", "if(", "else", "switch ", "switch(", "case ", "default:", "for ", "for(", "while ", "while(")
        ):
            return False
    return True


def _returned_after(function: FunctionInfo, variable: str, line: int) -> bool:
    for line_number, _, statement in iter_code_lines(function):
        if line_number <= line:
            continue
        if statement == f"return {variable};":
            return True
    return False


def _verified_evidence(
    function: FunctionInfo,
    context_graph: ContextGraph,
    base: float,
    path_confirmed: bool,
    checks: list[str],
) -> dict[str, object]:
    clang_ast = analysis_backend(function) == "clang"
    clang_cfg = has_clang_cfg(function, context_graph)
    score = base
    if path_confirmed:
        score += 0.08
    if clang_ast:
        score += 0.12
    if clang_cfg:
        score += 0.06
    score = confidence(score)
    return {
        "confidence": score,
        "evidence_confidence": evidence_confidence_label(score),
        "security_impact": "critical" if base >= 0.7 else "high",
        "verifier": {
            "verdict": verifier_verdict(score),
            "analysis_backend": analysis_backend(function),
            "clang_ast_confirmed": clang_ast,
            "clang_cfg_confirmed": clang_cfg,
            "path_confirmed": path_confirmed,
            "checks": checks,
        },
    }


def _resource_key(variable: str) -> str:
    normalized = variable.strip()
    normalized = normalized.lstrip("&*").strip()
    normalized = re.sub(r"^\([^)]*\)\s*", "", normalized)
    normalized = re.sub(r"\s+", "", normalized)
    return normalized


def _display_variable(events: list[ResourceEvent], fallback: str) -> str:
    for event in events:
        if event.variable:
            return event.variable
    return fallback


def _state_trace(events: list[ResourceEvent]) -> list[dict[str, object]]:
    state = "unknown"
    trace: list[dict[str, object]] = []
    for event in events:
        before = state
        if event.kind in ACQUIRE_TO_RELEASE:
            state = "owned"
        elif event.kind in RELEASE_TO_ACQUIRE:
            state = "released" if state in {"owned", "released", "unknown"} else state
        trace.append(
            {
                "line": event.location.line,
                "kind": event.kind,
                "variable": event.variable,
                "state_before": before,
                "state_after": state,
            }
        )
    return trace


def _has_reacquire_between(
    events: list[ResourceEvent],
    acquire_kind: str,
    first_line: int,
    second_line: int,
) -> bool:
    return any(
        event.kind == acquire_kind and first_line < event.location.line < second_line
        for event in events
    )


def _has_resource_reset_between(
    function: FunctionInfo,
    resource_key: str,
    first_line: int,
    second_line: int,
) -> bool:
    variable_patterns = _resource_alias_patterns(resource_key)
    for line_number, _, statement in iter_code_lines(function):
        if line_number <= first_line or line_number >= second_line:
            continue
        compact = re.sub(r"\s+", "", statement)
        for pattern in variable_patterns:
            if re.search(rf"{pattern}=(?:NULL|nullptr|0)", compact):
                return True
            if re.search(rf"{pattern}=[A-Za-z_][A-Za-z_0-9]*\(", compact):
                return True
    return False


def _resource_alias_patterns(resource_key: str) -> list[str]:
    escaped = re.escape(resource_key)
    patterns = [escaped]
    tail = re.search(r"([A-Za-z_][A-Za-z_0-9]*)$", resource_key)
    if tail:
        patterns.append(re.escape(tail.group(1)))
    return list(dict.fromkeys(patterns))


def _has_cleanup_goto_before(function: FunctionInfo, acquire_line: int, return_line: int) -> bool:
    for line_number, _, statement in iter_code_lines(function):
        if line_number <= acquire_line or line_number >= return_line:
            continue
        match = re.search(r"\bgoto\s+([A-Za-z_][A-Za-z_0-9]*)", statement)
        if match and any(token in match.group(1).lower() for token in ["out", "cleanup", "err", "error", "fail"]):
            return True
    return False


def _ownership_consumed_after(function: FunctionInfo, resource_key: str, line: int) -> bool:
    for line_number, _, statement in iter_code_lines(function):
        if line_number <= line:
            continue
        if is_release_wrapper_call(statement, resource_key):
            return True
        if is_ownership_transfer_call(statement, resource_key):
            return True
    return False


def _released_or_transferred_between(
    function: FunctionInfo,
    resource_key: str,
    start_line: int,
    end_line: int,
) -> bool:
    for line_number, _, statement in iter_code_lines(function):
        if line_number <= start_line or line_number >= end_line:
            continue
        if is_release_wrapper_call(statement, resource_key):
            return True
        if is_ownership_transfer_call(statement, resource_key):
            return True
    return False
