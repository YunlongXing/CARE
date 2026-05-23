"""Conservative semantic-equivalence validation."""

from __future__ import annotations

from typing import Optional

from care.core.models import FunctionInfo, PatchCandidate, StageResult
from care.validation.common import parse_project_functions, stage_from_exception

VISIBLE_CALLS = {
    "printf",
    "fprintf",
    "sprintf",
    "snprintf",
    "puts",
    "putchar",
    "scanf",
    "fscanf",
    "read",
    "write",
    "open",
    "close",
    "fopen",
    "fclose",
    "perror",
    "syslog",
    "log",
    "free",
    "delete",
    "unlock",
    "mutex_unlock",
    "pthread_mutex_unlock",
}


class SemanticEquivalenceChecker:
    """Compare public signatures, return forms, and visible side-effect calls."""

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
                    name="semantic_equivalence",
                    passed=True,
                    warnings=["no pre-patch function snapshot provided; semantic comparison skipped"],
                )
            after_functions = after_functions if after_functions is not None else parse_project_functions(project)
            counterexamples = _compare_functions(before_functions, after_functions)
            return StageResult(
                name="semantic_equivalence",
                passed=not counterexamples,
                logs=[
                    f"compared {len(before_functions)} pre-patch functions with "
                    f"{len(after_functions)} post-patch functions"
                ],
                counterexamples=counterexamples,
            )
        except Exception as exc:
            return stage_from_exception("semantic_equivalence", exc)


def _compare_functions(
    before_functions: list[FunctionInfo],
    after_functions: list[FunctionInfo],
) -> list[str]:
    counterexamples: list[str] = []
    before_by_name = {function.name: function for function in before_functions}
    after_by_name = {function.name: function for function in after_functions}

    for name, before in before_by_name.items():
        after = after_by_name.get(name)
        if after is None:
            counterexamples.append(f"function removed: {name}")
            continue
        if _is_public(before) and _signature_core(before.signature) != _signature_core(after.signature):
            counterexamples.append(
                f"public signature changed for {name}: {before.signature!r} -> {after.signature!r}"
            )
        if _return_forms(before) != _return_forms(after):
            counterexamples.append(
                f"return statement forms changed for {name}: {_return_forms(before)} -> {_return_forms(after)}"
            )
        if _visible_calls(before) != _visible_calls(after):
            counterexamples.append(
                f"externally visible calls changed for {name}: {_visible_calls(before)} -> {_visible_calls(after)}"
            )

    added_public = [
        name
        for name, function in after_by_name.items()
        if name not in before_by_name and _is_public(function)
    ]
    if added_public:
        counterexamples.append(f"new public functions introduced: {', '.join(sorted(added_public))}")
    return counterexamples


def _is_public(function: FunctionInfo) -> bool:
    return not function.signature.strip().startswith("static ")


def _signature_core(signature: str) -> str:
    return " ".join(signature.replace("\n", " ").split())


def _return_forms(function: FunctionInfo) -> list[str]:
    return [" ".join(statement.split()) for statement in function.return_statements]


def _visible_calls(function: FunctionInfo) -> list[str]:
    return [call for call in function.calls if call.split("::")[-1] in VISIBLE_CALLS]


SemanticChecker = SemanticEquivalenceChecker
