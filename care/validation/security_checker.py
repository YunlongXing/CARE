"""Security regression validation."""

from __future__ import annotations

import re
from collections import Counter
from typing import Optional

from care.analysis.security_knowledge import SecurityKnowledgeBase
from care.core.models import FunctionInfo, PatchCandidate, StageResult
from care.validation.common import parse_project_functions, stage_from_exception

UNSAFE_APIS = {"strcpy", "strcat", "sprintf", "vsprintf", "gets"}
SAFE_APIS = {"strncpy", "strlcpy", "snprintf", "vsnprintf", "fgets"}


class SecurityRegressionChecker:
    """Detect obvious security regressions introduced by a patch."""

    def __init__(self) -> None:
        self.knowledge_base = SecurityKnowledgeBase()

    def run(
        self,
        project: object,
        patch_candidate: PatchCandidate,
        before_functions: Optional[list[FunctionInfo]] = None,
        after_functions: Optional[list[FunctionInfo]] = None,
    ) -> StageResult:
        try:
            if before_functions is None:
                return StageResult(
                    name="security_regression",
                    passed=True,
                    warnings=["no pre-patch function snapshot provided; security regression comparison skipped"],
                )
            after_functions = after_functions if after_functions is not None else parse_project_functions(project)
            counterexamples = _security_regressions(
                self.knowledge_base,
                before_functions,
                after_functions,
            )
            return StageResult(
                name="security_regression",
                passed=not counterexamples,
                logs=[
                    f"compared security facts for {len(before_functions)} pre-patch and "
                    f"{len(after_functions)} post-patch functions"
                ],
                counterexamples=counterexamples,
            )
        except Exception as exc:
            return stage_from_exception("security_regression", exc)


def _security_regressions(
    knowledge_base: SecurityKnowledgeBase,
    before_functions: list[FunctionInfo],
    after_functions: list[FunctionInfo],
) -> list[str]:
    counterexamples: list[str] = []
    before_by_name = {function.name: function for function in before_functions}
    after_by_name = {function.name: function for function in after_functions}

    for name, before in before_by_name.items():
        after = after_by_name.get(name)
        if after is None:
            continue
        before_checks = _check_counts(before)
        after_checks = _check_counts(after)
        for check_kind in ["null_checks", "bounds_checks"]:
            if after_checks[check_kind] < before_checks[check_kind]:
                counterexamples.append(
                    f"{name}: {check_kind} decreased from {before_checks[check_kind]} to "
                    f"{after_checks[check_kind]}"
                )

        before_calls = Counter(before.calls)
        after_calls = Counter(after.calls)
        if sum(after_calls[api] for api in UNSAFE_APIS) > sum(before_calls[api] for api in UNSAFE_APIS):
            counterexamples.append(f"{name}: unsafe API usage increased")
        if sum(after_calls[api] for api in SAFE_APIS) < sum(before_calls[api] for api in SAFE_APIS) and sum(
            after_calls[api] for api in UNSAFE_APIS
        ) > 0:
            counterexamples.append(f"{name}: safe API usage appears to be replaced with unsafe API usage")

        before_smells = _smell_counts(knowledge_base, before)
        after_smells = _smell_counts(knowledge_base, after)
        for smell in ["unchecked_return_value", "missing_cleanup_on_error_path"]:
            if after_smells[smell] > before_smells[smell]:
                counterexamples.append(
                    f"{name}: {smell} increased from {before_smells[smell]} to {after_smells[smell]}"
                )

        before_releases = _release_count(knowledge_base, before)
        after_releases = _release_count(knowledge_base, after)
        if after_releases < before_releases:
            counterexamples.append(
                f"{name}: cleanup/resource release events decreased from {before_releases} to {after_releases}"
            )
    return counterexamples


def _check_counts(function: FunctionInfo) -> dict[str, int]:
    body = function.body
    return {
        "null_checks": len(
            re.findall(
                r"\bif\s*\([^)]*(?:!\s*[A-Za-z_][A-Za-z_0-9]*|NULL|nullptr)\b[^)]*\)",
                body,
            )
        ),
        "bounds_checks": len(
            re.findall(
                r"\bif\s*\([^)]*(?:<|>|<=|>=)[^)]*(?:len|size|idx|index|count|bound|capacity|n)\b[^)]*\)",
                body,
                flags=re.IGNORECASE,
            )
        ),
    }


def _smell_counts(knowledge_base: SecurityKnowledgeBase, function: FunctionInfo) -> Counter:
    return Counter(smell["kind"] for smell in knowledge_base.match_security_smells(function))


def _release_count(knowledge_base: SecurityKnowledgeBase, function: FunctionInfo) -> int:
    return sum(
        1
        for event in knowledge_base.match_resource_events(function)
        if event.kind in {"free", "close", "unlock"}
    )


SecurityChecker = SecurityRegressionChecker
