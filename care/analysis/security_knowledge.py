"""Security-aware refactoring pattern library."""

from __future__ import annotations

import re
from typing import Optional

from care.core.models import CodeLocation, FunctionInfo, ResourceEvent

ALLOC_CALLS = {
    "malloc": "free",
    "calloc": "free",
    "realloc": "free",
}
OPEN_CALLS = {
    "fopen": "fclose",
    "open": "close",
}
LOCK_CALLS = {
    "lock_acquire": "lock_release",
    "lock": "unlock",
    "mutex_lock": "mutex_unlock",
    "pthread_mutex_lock": "pthread_mutex_unlock",
}
FREE_CALLS = {"free"}
CLOSE_CALLS = {"fclose", "close"}
UNLOCK_CALLS = {"lock_release", "unlock", "mutex_unlock", "pthread_mutex_unlock"}
UNSAFE_CALLS = {"strcpy", "strcat", "sprintf", "vsprintf", "gets"}
CHECK_REQUIRED_CALLS = set(ALLOC_CALLS) | set(OPEN_CALLS)
ERROR_LABEL_HINTS = {"cleanup", "out", "error", "err", "fail", "failed"}
STATUS_NAMES = {"ret", "rc", "err", "error", "status", "result"}


class SecurityKnowledgeBase:
    """Pattern library and rules for security-aware refactoring."""

    def __init__(self) -> None:
        self._rules = _build_rules()

    def match_resource_events(self, function: FunctionInfo) -> list[ResourceEvent]:
        """Find allocation/open/lock and matching release-style events."""

        events: list[ResourceEvent] = []
        for line_number, raw_line in _iter_code_lines(function):
            statement = _normalize_statement(raw_line)
            if not statement:
                continue

            assignment = _assignment_call(statement)
            assigned_call = assignment[1] if assignment else None
            if assignment:
                variable, call = assignment
                kind = _resource_acquire_kind(call)
                if kind:
                    events.append(_resource_event(kind, variable, function, line_number, raw_line, call))

            for call, args, column in _iter_calls(raw_line):
                normalized_call = _short_name(call)
                if normalized_call == assigned_call:
                    continue
                variable = _first_variable_arg(args)
                kind = _resource_release_kind(normalized_call)
                if kind:
                    events.append(
                        ResourceEvent(
                            kind=kind,
                            variable=variable,
                            location=CodeLocation(
                                file=function.file,
                                line=line_number,
                                column=column,
                            ),
                        )
                    )
                    continue

                acquire_kind = _resource_acquire_kind(normalized_call)
                if acquire_kind and not assignment:
                    events.append(
                        ResourceEvent(
                            kind=acquire_kind,
                            variable=variable,
                            location=CodeLocation(
                                file=function.file,
                                line=line_number,
                                column=column,
                            ),
                        )
                    )

        return _dedupe_resource_events(events)

    def match_security_smells(self, function: FunctionInfo) -> list[dict]:
        """Find security-relevant smells that refactoring must preserve or fix."""

        smells: list[dict] = []
        events = self.match_resource_events(function)
        lines = list(_iter_code_lines(function))
        smells.extend(_unchecked_return_value_smells(function, lines))
        smells.extend(_unsafe_call_smells(function, lines))
        smells.extend(_duplicate_validation_smells(function, lines))
        smells.extend(_error_handling_smells(function, lines, events))
        return _dedupe_smells(smells)

    def get_refactoring_rules(self, kind: str) -> list[str]:
        """Return security-aware guidance for a refactoring kind or pattern."""

        normalized = _normalize_rule_key(kind)
        if normalized in self._rules:
            return list(self._rules[normalized])

        aliases = {
            "malloc_free": "resource_management",
            "calloc_free": "resource_management",
            "realloc_free": "resource_management",
            "fopen_fclose": "resource_management",
            "open_close": "resource_management",
            "lock_unlock": "resource_management",
            "mutex_lock_mutex_unlock": "resource_management",
            "goto_cleanup": "error_handling",
            "goto_out": "error_handling",
            "duplicate_check": "validation",
            "duplicate_checks": "validation",
            "repeated_null_checks": "validation",
            "repeated_bounds_checks": "validation",
            "repeated_status_checks": "validation",
            "duplicated_sanity_checks": "validation",
            "security_smell": "security_smells",
            "smell": "security_smells",
        }
        aliased = aliases.get(normalized)
        if aliased:
            return list(self._rules[aliased])

        return list(self._rules["general"])

    def default_rules(self) -> dict[str, list[str]]:
        """Return the full rule library grouped by pattern family."""

        return {
            "general": self.get_refactoring_rules("general"),
            "resource_management": self.get_refactoring_rules("resource_management"),
            "error_handling": self.get_refactoring_rules("error_handling"),
            "validation": self.get_refactoring_rules("validation"),
            "security_smells": self.get_refactoring_rules("security_smells"),
        }


def _build_rules() -> dict[str, list[str]]:
    return {
        "general": [
            "Preserve observable behavior, error propagation, cleanup ordering, and security checks.",
            "Prefer small transformations that keep validation and cleanup close to their original control flow.",
            "Do not remove a defensive check unless equivalence is established from local context.",
        ],
        "resource_management": [
            "Preserve every successful malloc/calloc/realloc with a matching free on all exit paths.",
            "Preserve every successful fopen/open with a matching fclose/close on all exit paths.",
            "Preserve every lock/mutex_lock with a matching unlock/mutex_unlock on all exit paths.",
            "Do not move release operations before the last use of the resource.",
            "Avoid introducing leaks, double frees, double closes, double unlocks, or use-after-release behavior.",
        ],
        "error_handling": [
            "Keep goto cleanup and goto out paths semantically equivalent when replacing them with structured control flow.",
            "Repeated cleanup blocks may be consolidated only when release order and null/error guards are preserved.",
            "Error paths that acquire resources must still reach the required cleanup before returning.",
            "Preserve status variables and return-code conventions used by callers.",
        ],
        "validation": [
            "Repeated null, bounds, status, and sanity checks may be deduplicated only when dominance and side effects are clear.",
            "Do not hoist checks across statements that mutate checked variables or depend on checked state.",
            "Preserve the exact error result associated with each validation failure.",
            "Keep checks that document security boundaries unless they are provably redundant.",
        ],
        "security_smells": [
            "Unchecked return values should be guarded before dependent use or cleanup refactoring.",
            "Unsafe strcpy/sprintf-style calls should not be made harder to audit during unrelated refactoring.",
            "Inconsistent error propagation should be normalized only when caller-visible behavior is preserved.",
            "Unreachable cleanup should be removed or reconnected only after proving resources are still released.",
        ],
    }


def _unchecked_return_value_smells(function: FunctionInfo, lines: list[tuple[int, str]]) -> list[dict]:
    smells: list[dict] = []
    for index, (line_number, raw_line) in enumerate(lines):
        statement = _normalize_statement(raw_line)
        assignment = _assignment_call(statement)
        if assignment:
            variable, call = assignment
            if call in CHECK_REQUIRED_CALLS and not _has_followup_check(variable, call, lines, index):
                smells.append(
                    _smell(
                        function=function,
                        line=line_number,
                        column=_column(raw_line, call),
                        kind="unchecked_return_value",
                        severity="high",
                        description=f"Return value from {call} assigned to {variable} is not checked.",
                        evidence={"call": call, "variable": variable, "statement": statement},
                    )
                )
            continue

        for call, args, column in _iter_calls(raw_line):
            normalized_call = _short_name(call)
            if normalized_call in CHECK_REQUIRED_CALLS and not statement.startswith(("if ", "if(", "return ")):
                smells.append(
                    _smell(
                        function=function,
                        line=line_number,
                        column=column,
                        kind="unchecked_return_value",
                        severity="medium",
                        description=f"Return value from {normalized_call} is ignored.",
                        evidence={"call": normalized_call, "args": args, "statement": statement},
                    )
                )
    return smells


def _unsafe_call_smells(function: FunctionInfo, lines: list[tuple[int, str]]) -> list[dict]:
    smells: list[dict] = []
    for line_number, raw_line in lines:
        statement = _normalize_statement(raw_line)
        for call, args, column in _iter_calls(raw_line):
            normalized_call = _short_name(call)
            if normalized_call in UNSAFE_CALLS:
                smells.append(
                    _smell(
                        function=function,
                        line=line_number,
                        column=column,
                        kind="unsafe_function",
                        severity="high",
                        description=f"Unsafe function {normalized_call} appears in refactoring context.",
                        evidence={"call": normalized_call, "args": args, "statement": statement},
                    )
                )
    return smells


def _duplicate_validation_smells(function: FunctionInfo, lines: list[tuple[int, str]]) -> list[dict]:
    smells: list[dict] = []
    seen: dict[str, int] = {}
    for line_number, raw_line in lines:
        statement = _normalize_statement(raw_line)
        condition = _if_condition(statement)
        if condition is None:
            continue
        kind = _validation_kind(condition)
        if kind is None:
            continue
        normalized = _normalize_condition(condition)
        previous = seen.get(normalized)
        if previous is not None:
            smells.append(
                _smell(
                    function=function,
                    line=line_number,
                    column=_column(raw_line, "if"),
                    kind=kind,
                    severity="medium",
                    description=f"Repeated validation check also appears at line {previous}.",
                    evidence={
                        "condition": condition,
                        "first_line": previous,
                        "statement": statement,
                    },
                )
            )
        else:
            seen[normalized] = line_number
    return smells


def _error_handling_smells(
    function: FunctionInfo,
    lines: list[tuple[int, str]],
    events: list[ResourceEvent],
) -> list[dict]:
    smells: list[dict] = []
    smells.extend(_repeated_cleanup_smells(function, lines))
    smells.extend(_unreachable_cleanup_smells(function, lines))
    smells.extend(_missing_cleanup_on_error_path_smells(function, lines, events))
    smells.extend(_inconsistent_error_propagation_smells(function))
    return smells


def _repeated_cleanup_smells(function: FunctionInfo, lines: list[tuple[int, str]]) -> list[dict]:
    cleanup_calls: dict[tuple[str, str], int] = {}
    smells: list[dict] = []
    for line_number, raw_line in lines:
        statement = _normalize_statement(raw_line)
        for call, args, column in _iter_calls(raw_line):
            normalized_call = _short_name(call)
            if normalized_call not in FREE_CALLS | CLOSE_CALLS | UNLOCK_CALLS:
                continue
            variable = _first_variable_arg(args)
            key = (normalized_call, variable)
            previous = cleanup_calls.get(key)
            if previous is not None:
                smells.append(
                    _smell(
                        function=function,
                        line=line_number,
                        column=column,
                        kind="repeated_cleanup_blocks",
                        severity="medium",
                        description=f"Cleanup for {variable} via {normalized_call} repeats line {previous}.",
                        evidence={
                            "call": normalized_call,
                            "variable": variable,
                            "first_line": previous,
                            "statement": statement,
                        },
                    )
                )
            else:
                cleanup_calls[key] = line_number
    return smells


def _unreachable_cleanup_smells(function: FunctionInfo, lines: list[tuple[int, str]]) -> list[dict]:
    smells: list[dict] = []
    goto_targets = {
        match.group(1)
        for _, raw_line in lines
        for match in [re.search(r"\bgoto\s+([A-Za-z_][A-Za-z_0-9]*)\s*;", raw_line)]
        if match
    }
    for line_number, raw_line in lines:
        statement = _normalize_statement(raw_line)
        label = _label_name(statement)
        if label and _is_error_label(label):
            if label not in goto_targets:
                smells.append(
                    _smell(
                        function=function,
                        line=line_number,
                        column=_column(raw_line, label),
                        kind="unreachable_cleanup",
                        severity="high",
                        description=f"Cleanup label {label} has no goto target.",
                        evidence={"label": label, "statement": statement},
                    )
                )
    return smells


def _missing_cleanup_on_error_path_smells(
    function: FunctionInfo,
    lines: list[tuple[int, str]],
    events: list[ResourceEvent],
) -> list[dict]:
    smells: list[dict] = []
    acquisitions = [
        event
        for event in events
        if event.kind in {"alloc", "open", "lock"} and event.variable
    ]
    releases_by_variable: dict[str, list[ResourceEvent]] = {}
    for event in events:
        if event.kind in {"free", "close", "unlock"}:
            releases_by_variable.setdefault(event.variable, []).append(event)

    for acquired in acquisitions:
        release_lines = [
            event.location.line
            for event in releases_by_variable.get(acquired.variable, [])
            if event.location.line > acquired.location.line
        ]
        for line_number, raw_line in lines:
            if line_number <= acquired.location.line:
                continue
            statement = _normalize_statement(raw_line)
            if not _is_error_return(statement):
                continue
            if any(acquired.location.line < release_line < line_number for release_line in release_lines):
                continue
            if _has_cleanup_goto_between(lines, acquired.location.line, line_number):
                continue
            smells.append(
                _smell(
                    function=function,
                    line=line_number,
                    column=_column(raw_line, "return"),
                    kind="missing_cleanup_on_error_path",
                    severity="high",
                    description=(
                        f"Error return may bypass cleanup for {acquired.variable} acquired "
                        f"at line {acquired.location.line}."
                    ),
                    evidence={
                        "variable": acquired.variable,
                        "acquired_line": acquired.location.line,
                        "statement": statement,
                    },
                )
            )
    return smells


def _inconsistent_error_propagation_smells(function: FunctionInfo) -> list[dict]:
    error_forms: dict[str, str] = {}
    for statement in function.return_statements:
        category = _return_error_category(statement)
        if category:
            error_forms[category] = statement
    if len(error_forms) <= 1:
        return []
    return [
        _smell(
            function=function,
            line=function.start_line,
            column=1,
            kind="inconsistent_error_propagation",
            severity="medium",
            description="Function mixes multiple error return conventions.",
            evidence={"return_categories": error_forms},
        )
    ]


def _iter_code_lines(function: FunctionInfo) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    for offset, raw_line in enumerate(function.body.splitlines()):
        code = _strip_comments_and_strings(raw_line)
        if code.strip() and code.strip() not in {"{", "}"}:
            lines.append((function.start_line + offset, code))
    return lines


def _assignment_call(statement: str) -> Optional[tuple[str, str]]:
    match = re.search(
        r"(?P<lhs>[A-Za-z_][A-Za-z_0-9:<>*\s&.\-\>\[\]]*?)\s*=\s*"
        r"(?:\([^)]*\)\s*)?(?P<call>[A-Za-z_][A-Za-z_0-9]*)\s*\(",
        statement,
    )
    if not match:
        return None
    variable = _resource_expression(match.group("lhs"))
    call = _short_name(match.group("call"))
    if not variable:
        return None
    return variable, call


def _resource_acquire_kind(call: str) -> Optional[str]:
    if call in ALLOC_CALLS:
        return "alloc"
    if call in OPEN_CALLS:
        return "open"
    if call in LOCK_CALLS:
        return "lock"
    return None


def _resource_release_kind(call: str) -> Optional[str]:
    if call in FREE_CALLS:
        return "free"
    if call in CLOSE_CALLS:
        return "close"
    if call in UNLOCK_CALLS:
        return "unlock"
    if call in LOCK_CALLS:
        return "lock"
    return None


def _resource_event(
    kind: str,
    variable: str,
    function: FunctionInfo,
    line_number: int,
    raw_line: str,
    call: str,
) -> ResourceEvent:
    return ResourceEvent(
        kind=kind,
        variable=variable,
        location=CodeLocation(file=function.file, line=line_number, column=_column(raw_line, call)),
    )


def _iter_calls(raw_line: str) -> list[tuple[str, list[str], int]]:
    calls: list[tuple[str, list[str], int]] = []
    line = _strip_comments_and_strings(raw_line)
    for match in re.finditer(r"\b([A-Za-z_][A-Za-z_0-9:]*)\s*\(([^()]*)\)", line):
        call = match.group(1)
        args = [part.strip() for part in match.group(2).split(",") if part.strip()]
        calls.append((call, args, match.start(1) + 1))
    return calls


def _has_followup_check(
    variable: str,
    call: str,
    lines: list[tuple[int, str]],
    current_index: int,
) -> bool:
    window = lines[current_index + 1 : current_index + 6]
    for _, raw_line in window:
        statement = _normalize_statement(raw_line)
        if _checks_variable(statement, variable, call):
            return True
        if _mutates_variable(statement, variable):
            return False
    return False


def _checks_variable(statement: str, variable: str, call: str) -> bool:
    escaped = re.escape(variable)
    if re.search(rf"\bif\s*\(\s*!\s*{escaped}\b", statement):
        return True
    if re.search(rf"\bif\s*\([^)]*\b{escaped}\s*(==|!=)\s*(NULL|nullptr|0|-1)", statement):
        return True
    if call in OPEN_CALLS and re.search(rf"\bif\s*\([^)]*\b{escaped}\s*<\s*0", statement):
        return True
    if call in ALLOC_CALLS and re.search(rf"\bif\s*\([^)]*\b{escaped}\s*==\s*(NULL|nullptr|0)", statement):
        return True
    return False


def _mutates_variable(statement: str, variable: str) -> bool:
    return bool(re.search(rf"\b{re.escape(variable)}\s*=", statement))


def _if_condition(statement: str) -> Optional[str]:
    match = re.search(r"\bif\s*\((.*)\)", statement)
    return match.group(1).strip() if match else None


def _validation_kind(condition: str) -> Optional[str]:
    lowered = condition.lower()
    if re.search(r"!\s*[A-Za-z_][A-Za-z_0-9]*|\bnull\b|\bnullptr\b", lowered):
        return "repeated_null_checks"
    if any(operator in condition for operator in ["<", ">", "<=", ">="]) and any(
        token in lowered for token in ["len", "size", "count", "idx", "index", "n", "bound", "capacity"]
    ):
        return "repeated_bounds_checks"
    if any(name in lowered for name in STATUS_NAMES):
        return "repeated_status_checks"
    if any(token in lowered for token in ["magic", "version", "type", "valid", "sanity"]):
        return "duplicated_sanity_checks"
    return None


def _normalize_condition(condition: str) -> str:
    return re.sub(r"\s+", "", condition)


def _label_name(statement: str) -> Optional[str]:
    match = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)\s*:", statement)
    return match.group(1) if match else None


def _is_error_label(label: str) -> bool:
    return any(hint in label.lower() for hint in ERROR_LABEL_HINTS)


def _is_error_return(statement: str) -> bool:
    lowered = statement.lower()
    return bool(
        re.match(r"^return\b", lowered)
        and any(token in lowered for token in ["-1", "null", "nullptr", "false", "err", "error"])
    )


def _has_cleanup_goto_between(lines: list[tuple[int, str]], start_line: int, end_line: int) -> bool:
    for line_number, raw_line in lines:
        if start_line < line_number < end_line:
            match = re.search(r"\bgoto\s+([A-Za-z_][A-Za-z_0-9]*)\s*;", raw_line)
            if match and _is_error_label(match.group(1)):
                return True
    return False


def _return_error_category(statement: str) -> Optional[str]:
    lowered = statement.lower()
    if "null" in lowered or "nullptr" in lowered:
        return "null"
    if "false" in lowered:
        return "false"
    if re.search(r"return\s+-\d+", lowered):
        return "negative"
    if any(name in lowered for name in STATUS_NAMES):
        return "status_variable"
    return None


def _smell(
    function: FunctionInfo,
    line: int,
    column: Optional[int],
    kind: str,
    severity: str,
    description: str,
    evidence: dict,
) -> dict:
    return {
        "kind": kind,
        "severity": severity,
        "function": function.name,
        "location": CodeLocation(file=function.file, line=line, column=column).to_dict(),
        "description": description,
        "evidence": evidence,
    }


def _dedupe_smells(smells: list[dict]) -> list[dict]:
    seen: set[tuple[str, int, str]] = set()
    result: list[dict] = []
    for smell in smells:
        key = (
            smell["kind"],
            smell["location"]["line"],
            str(smell.get("evidence", {})),
        )
        if key not in seen:
            seen.add(key)
            result.append(smell)
    return result


def _dedupe_resource_events(events: list[ResourceEvent]) -> list[ResourceEvent]:
    seen: set[tuple[str, str, int, Optional[int]]] = set()
    result: list[ResourceEvent] = []
    for event in events:
        key = (event.kind, event.variable, event.location.line, event.location.column)
        if key not in seen:
            seen.add(key)
            result.append(event)
    return result


def _first_variable_arg(args: list[str]) -> str:
    if not args:
        return ""
    return _resource_expression(args[0])


def _resource_expression(text: str) -> str:
    """Return a stable resource expression instead of only the final field name."""

    candidate = text.strip().rstrip(";")
    candidate = re.sub(r"^\([^)]*\)\s*", "", candidate)
    candidate = candidate.lstrip("&*").strip()
    candidate = re.sub(r"\s+", " ", candidate)
    if not candidate:
        return ""

    # For simple declarations such as "char *p", keep the declared identifier.
    if not any(token in candidate for token in ("->", ".", "[")):
        return _last_identifier(candidate) or ""

    pattern = (
        r"[A-Za-z_][A-Za-z_0-9]*"
        r"(?:\s*(?:->|\.)\s*[A-Za-z_][A-Za-z_0-9]*|\s*\[[^\]]+\])*"
    )
    matches = re.findall(pattern, candidate)
    if not matches:
        return _last_identifier(candidate) or ""
    expression = matches[-1]
    expression = re.sub(r"\s*(->|\.)\s*", r"\1", expression)
    expression = re.sub(r"\s+", "", expression)
    return expression


def _last_identifier(text: str) -> Optional[str]:
    matches = re.findall(r"[A-Za-z_][A-Za-z_0-9]*", text)
    return matches[-1] if matches else None


def _short_name(call: str) -> str:
    return call.split("::")[-1]


def _column(raw_line: str, needle: str) -> Optional[int]:
    index = raw_line.find(needle)
    return index + 1 if index >= 0 else None


def _normalize_statement(statement: str) -> str:
    return " ".join(statement.strip().split())


def _strip_comments_and_strings(line: str) -> str:
    code = line.split("//", 1)[0]
    code = re.sub(r"/\*.*?\*/", " ", code)
    code = re.sub(r'"(?:\\.|[^"\\])*"', '""', code)
    code = re.sub(r"'(?:\\.|[^'\\])*'", "''", code)
    return code


def _normalize_rule_key(kind: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", kind.strip().lower()).strip("_")
