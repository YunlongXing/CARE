"""Conservative semantic-equivalence validation."""

from __future__ import annotations

import re
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
        if not _return_forms_equivalent(before, after):
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
    forms: list[str] = []
    for statement in function.return_statements:
        compact = " ".join(statement.split())
        match = re.fullmatch(r"return\s*\(?\s*(.*?)\s*\)?\s*;", compact)
        if match is None:
            forms.append(compact)
            continue
        forms.append(f"return {_strip_outer_parentheses(match.group(1))};")
    return forms


def _strip_outer_parentheses(expression: str) -> str:
    value = expression.strip()
    while value.startswith("(") and value.endswith(")") and _outer_parentheses_enclose(value):
        value = value[1:-1].strip()
    return value


def _outer_parentheses_enclose(value: str) -> bool:
    depth = 0
    for index, character in enumerate(value):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0 and index != len(value) - 1:
                return False
    return depth == 0


def _return_forms_equivalent(before: FunctionInfo, after: FunctionInfo) -> bool:
    """Recognize a small, conservative return-variable cleanup rewrite."""

    before_forms = _return_forms(before)
    after_forms = _return_forms(after)
    if before_forms == after_forms:
        return True

    after_return_variables = [
        form[len("return ") : -1].strip()
        for form in after_forms
        if form.startswith("return ") and form.endswith(";")
        and form[len("return ") : -1].strip().isidentifier()
    ]
    if len(set(after_return_variables)) != 1:
        return False
    variable = after_return_variables[0]
    if after_forms.count(f"return {variable};") != 1:
        return False

    before_values = _simple_return_values(before_forms)
    after_direct_values = _simple_return_values(
        [form for form in after_forms if form != f"return {variable};"]
    )
    assigned_values = _assigned_values(after.body, variable)
    return bool(before_values) and before_values == after_direct_values | assigned_values


def _simple_return_values(forms: list[str]) -> set[str]:
    values: set[str] = set()
    for form in forms:
        if not form.startswith("return ") or not form.endswith(";"):
            return set()
        expression = form[len("return ") : -1].strip()
        if not expression or any(token in expression for token in "()[]{}"):
            return set()
        values.add(expression)
    return values


def _assigned_values(body: str, variable: str) -> set[str]:
    import re

    pattern = re.compile(
        rf"\b{re.escape(variable)}\s*=\s*(?P<value>-?\d+|NULL|[A-Z_][A-Z0-9_]*)\s*;"
    )
    return {match.group("value") for match in pattern.finditer(body)}


def _visible_calls(function: FunctionInfo) -> list[str]:
    return [call for call in function.calls if call.split("::")[-1] in VISIBLE_CALLS]


SemanticChecker = SemanticEquivalenceChecker
