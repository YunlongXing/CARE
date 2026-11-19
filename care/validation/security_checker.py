"""Security regression validation."""

from __future__ import annotations

import re
from collections import Counter
from typing import Optional

from care.analysis.security_knowledge import SecurityKnowledgeBase
from care.core.models import FunctionInfo, PatchCandidate, StageResult
from care.detection.common import assigned_variables, function_calls, if_condition, iter_code_lines
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
        for check_kind in ["null_checks", "bounds_checks", "status_checks"]:
            if after_checks[check_kind] < before_checks[check_kind]:
                if check_kind == "null_checks" and _only_safe_duplicate_null_checks_removed(before, after):
                    continue
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

    return counterexamples


def _check_counts(function: FunctionInfo) -> dict[str, int]:
    body = function.body
    conditions = re.findall(r"\bif\s*\(([^()]*)\)", body, flags=re.DOTALL)
    return {
        "null_checks": len(
            re.findall(
                r"\bif\s*\([^)]*(?:!\s*[A-Za-z_][A-Za-z_0-9]*|NULL|nullptr)\b[^)]*\)",
                body,
            )
        ),
        "bounds_checks": sum(
            bool(re.search(r"(?:<=|>=|<|>)", condition))
            and bool(
                re.search(
                    r"\b(?:len|size|idx|index|count|bound|capacity|n)\b",
                    condition,
                    flags=re.IGNORECASE,
                )
            )
            for condition in conditions
        ),
        "status_checks": sum(
            bool(re.search(r"(?:==|!=|<=|>=|<|>)", condition))
            and bool(
                re.search(
                    r"\b(?:ret(?:urn)?|err(?:or)?|status|result|rc)\b",
                    condition,
                    flags=re.IGNORECASE,
                )
            )
            for condition in conditions
        ),
    }


def _only_safe_duplicate_null_checks_removed(before: FunctionInfo, after: FunctionInfo) -> bool:
    """Allow removal only when a surviving equivalent guard dominates locally."""

    before_checks = _null_checks(before)
    after_counts = Counter(check["key"] for check in _null_checks(after))
    before_counts = Counter(check["key"] for check in before_checks)
    removed = {
        key: before_counts[key] - after_counts[key]
        for key in before_counts
        if before_counts[key] > after_counts[key]
    }
    if not removed or any(after_counts[key] == 0 for key in removed):
        return False
    for key, count in removed.items():
        checks = [check for check in before_checks if check["key"] == key]
        safe_duplicates = sum(
            1
            for earlier, later in zip(checks, checks[1:])
            if _no_relevant_mutation_between(before, earlier, later)
        )
        if count > safe_duplicates:
            return False
    return True


def _null_checks(function: FunctionInfo) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    for line, _, statement in iter_code_lines(function):
        condition = if_condition(statement)
        if condition is None:
            continue
        variable = _null_checked_variable(condition)
        if variable is not None:
            checks.append({"line": line, "key": variable})
    return checks


def _null_checked_variable(condition: str) -> str | None:
    compact = re.sub(r"\s+", "", condition)
    bang = re.fullmatch(r"!([A-Za-z_][A-Za-z_0-9]*)", compact)
    if bang:
        return bang.group(1)
    match = re.fullmatch(
        r"([A-Za-z_][A-Za-z_0-9]*)(?:==|!=)(?:NULL|nullptr|0)", compact
    )
    if match:
        return match.group(1)
    match = re.fullmatch(
        r"(?:NULL|nullptr|0)(?:==|!=)([A-Za-z_][A-Za-z_0-9]*)", compact
    )
    return match.group(1) if match else None


def _no_relevant_mutation_between(
    function: FunctionInfo,
    earlier: dict[str, object],
    later: dict[str, object],
) -> bool:
    variable = str(earlier["key"])
    for line, _, statement in iter_code_lines(function):
        if not int(earlier["line"]) < line < int(later["line"]):
            continue
        if variable in assigned_variables(statement) or function_calls(statement):
            return False
    return True


def _smell_counts(knowledge_base: SecurityKnowledgeBase, function: FunctionInfo) -> Counter:
    return Counter(smell["kind"] for smell in knowledge_base.match_security_smells(function))


SecurityChecker = SecurityRegressionChecker
