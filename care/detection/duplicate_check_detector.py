"""Detect repeated validation checks over the same variable."""

from __future__ import annotations

import re
from typing import Optional

from care.analysis.context_graph import ContextGraph
from care.analysis.security_knowledge import SecurityKnowledgeBase
from care.core.models import FunctionInfo, RefactoringOpportunity
from care.detection.common import (
    assigned_variables,
    column,
    function_calls,
    if_condition,
    iter_code_lines,
    make_opportunity,
)


class DuplicateCheckDetector:
    """Find duplicate checks and classify whether removal looks locally safe."""

    def detect(
        self,
        function: FunctionInfo,
        context_graph: ContextGraph,
        knowledge_base: SecurityKnowledgeBase,
    ) -> list[RefactoringOpportunity]:
        opportunities: list[RefactoringOpportunity] = []
        checks = _extract_checks(function)
        by_key: dict[str, list[dict]] = {}
        for check in checks:
            by_key.setdefault(check["key"], []).append(check)

        for same_checks in by_key.values():
            if len(same_checks) < 2:
                continue
            first = same_checks[0]
            for duplicate in same_checks[1:]:
                safety = _safe_to_remove_duplicate(function, first, duplicate, context_graph)
                severity = "low" if safety["safe_to_remove"] else "medium"
                confidence = 0.78 if safety["safe_to_remove"] else 0.56
                opportunities.append(
                    make_opportunity(
                        function=function,
                        context_graph=context_graph,
                        kind="duplicate_check",
                        line=duplicate["line"],
                        column=duplicate["column"],
                        severity=severity,
                        description=(
                            f"Repeated {duplicate['check_kind']} over {duplicate['variable']} "
                            f"also appears at line {first['line']}."
                        ),
                        evidence={
                            "variable": duplicate["variable"],
                            "check_kind": duplicate["check_kind"],
                            "first_check": first,
                            "duplicate_check": duplicate,
                            "safe_to_remove": safety["safe_to_remove"],
                            "safety_reasons": safety["reasons"],
                            "relevant_assignments_between_checks": safety["assignments"],
                            "function_calls_between_checks": safety["calls"],
                            "aliasing_risk": safety["aliasing_risk"],
                            "confidence": confidence,
                        },
                    )
                )
        return opportunities


def _extract_checks(function: FunctionInfo) -> list[dict]:
    checks: list[dict] = []
    for line_number, raw_line, statement in iter_code_lines(function):
        condition = if_condition(statement)
        if condition is None:
            continue
        classified = _classify_condition(condition)
        if classified is None:
            continue
        variable, check_kind, key = classified
        checks.append(
            {
                "line": line_number,
                "column": column(raw_line, "if"),
                "statement": statement,
                "condition": condition,
                "variable": variable,
                "check_kind": check_kind,
                "key": key,
            }
        )
    return checks


def _classify_condition(condition: str) -> Optional[tuple[str, str, str]]:
    normalized = re.sub(r"\s+", "", condition)
    status_match = re.match(
        r"^(?P<var>ret|rc|err|error|status|result)\s*(?P<op>==|!=|<|>)\s*(?P<value>-?\d+|[A-Za-z_][A-Za-z_0-9]*)$",
        condition.strip(),
        flags=re.IGNORECASE,
    )
    if status_match:
        variable = status_match.group("var")
        return variable, "status_check", f"status:{normalized}"

    null_match = re.match(
        r"^(?:!\s*(?P<bang>[A-Za-z_][A-Za-z_0-9]*)|"
        r"(?P<left>[A-Za-z_][A-Za-z_0-9]*)\s*(?:==|!=)\s*(?:NULL|nullptr|0)|"
        r"(?:NULL|nullptr|0)\s*(?:==|!=)\s*(?P<right>[A-Za-z_][A-Za-z_0-9]*))$",
        condition.strip(),
    )
    if null_match:
        variable = next(value for value in null_match.groupdict().values() if value)
        return variable, "null_check", f"null:{variable}"

    bounds_match = re.match(
        r"^(?P<left>[A-Za-z_][A-Za-z_0-9]*)\s*(?P<op><=|>=|<|>)\s*(?P<right>[A-Za-z_][A-Za-z_0-9]*|-?\d+)$",
        condition.strip(),
    )
    if bounds_match:
        left = bounds_match.group("left")
        right = bounds_match.group("right")
        if _looks_bounds_related(left) or _looks_bounds_related(right):
            return left, "bounds_check", f"bounds:{normalized}"

    sanity_match = re.match(
        r"^(?P<var>[A-Za-z_][A-Za-z_0-9]*(?:_valid|_ok|_magic|_version|_type)?)\s*(==|!=)\s*(?P<value>[^&|]+)$",
        condition.strip(),
    )
    if sanity_match and any(
        token in condition.lower()
        for token in ["valid", "magic", "version", "type", "sanity"]
    ):
        variable = sanity_match.group("var")
        return variable, "sanity_check", f"sanity:{normalized}"

    return None


def _safe_to_remove_duplicate(
    function: FunctionInfo,
    first: dict,
    duplicate: dict,
    context_graph: ContextGraph,
) -> dict:
    variable = duplicate["variable"]
    between = [
        (line_number, raw_line, statement)
        for line_number, raw_line, statement in iter_code_lines(function)
        if first["line"] < line_number < duplicate["line"]
    ]
    assignments = [
        {"line": line_number, "statement": statement}
        for line_number, _, statement in between
        if variable in assigned_variables(statement)
    ]
    calls = [
        {"line": line_number, "calls": function_calls(statement), "statement": statement}
        for line_number, _, statement in between
        if function_calls(statement)
    ]
    aliasing_risk = _has_aliasing_risk(variable, between, context_graph, function)

    reasons: list[str] = []
    if assignments:
        reasons.append("relevant assignment occurs between checks")
    if calls:
        reasons.append("function call between checks may mutate checked state")
    if aliasing_risk:
        reasons.append("obvious aliasing risk is present")
    if not reasons:
        reasons.append("no assignments, mutating calls, or obvious aliasing risk between checks")

    return {
        "safe_to_remove": not assignments and not calls and not aliasing_risk,
        "reasons": reasons,
        "assignments": assignments,
        "calls": calls,
        "aliasing_risk": aliasing_risk,
    }


def _has_aliasing_risk(
    variable: str,
    between: list[tuple[int, str, str]],
    context_graph: ContextGraph,
    function: FunctionInfo,
) -> bool:
    dataflow = context_graph.dataflow.get(function.name, {})
    pointer_variables = set(dataflow.get("pointer_variables", []))
    for _, raw_line, statement in between:
        if re.search(rf"&\s*{re.escape(variable)}\b", raw_line):
            return True
        if variable in pointer_variables and re.search(rf"\b{re.escape(variable)}\s*->", statement):
            return True
    return False


def _looks_bounds_related(name: str) -> bool:
    lowered = name.lower()
    return any(
        token in lowered
        for token in ["len", "size", "idx", "index", "count", "bound", "capacity", "n"]
    )
