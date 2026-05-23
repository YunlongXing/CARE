"""Lightweight data-flow and resource fact extraction."""

from __future__ import annotations

import re

from care.core.models import FunctionInfo

C_KEYWORDS = {
    "auto",
    "break",
    "case",
    "char",
    "const",
    "continue",
    "default",
    "do",
    "double",
    "else",
    "enum",
    "extern",
    "float",
    "for",
    "goto",
    "if",
    "inline",
    "int",
    "long",
    "register",
    "return",
    "short",
    "signed",
    "sizeof",
    "static",
    "struct",
    "switch",
    "typedef",
    "union",
    "unsigned",
    "void",
    "volatile",
    "while",
}
RESOURCE_ALLOCATORS = {"malloc", "calloc", "realloc", "new", "strdup"}
RESOURCE_OPENERS = {"open", "fopen", "socket", "accept", "opendir"}
RESOURCE_FREERS = {"free", "delete"}
RESOURCE_CLOSERS = {"close", "fclose", "closedir"}
RESOURCE_LOCKERS = {"pthread_mutex_lock", "mutex_lock", "lock"}
RESOURCE_UNLOCKERS = {"pthread_mutex_unlock", "mutex_unlock", "unlock"}
STATUS_NAMES = {"ret", "rc", "err", "error", "status", "result"}


class DataFlowAnalyzer:
    """Track approximate definitions, uses, and resource variables."""

    def analyze(self, function: FunctionInfo) -> dict:
        statements = _statement_lines(function)
        definitions: list[dict] = []
        uses: list[dict] = []
        pointer_variables: set[str] = set()
        resource_variables: set[str] = set()
        resource_events: list[dict] = []

        for line_number, statement in statements:
            defined = _definitions_in_statement(statement)
            for variable in defined:
                definitions.append(_fact(variable, line_number, statement))

            for variable in _pointer_variables_in_statement(statement):
                pointer_variables.add(variable)
                resource_variables.add(variable)

            for event in _resource_events_in_statement(statement, line_number):
                resource_events.append(event)
                if event["variable"]:
                    resource_variables.add(event["variable"])

            for token in _identifier_uses(statement):
                if token not in C_KEYWORDS and token != function.name:
                    uses.append(_fact(token, line_number, statement))

        return_status_variables = sorted(
            variable
            for variable in {fact["variable"] for fact in definitions}
            if variable.lower() in STATUS_NAMES
        )

        return {
            "function": function.name,
            "definitions": definitions,
            "uses": uses,
            "pointer_variables": sorted(pointer_variables),
            "return_status_variables": return_status_variables,
            "resource_variables": sorted(resource_variables),
            "resource_events": resource_events,
        }


def _statement_lines(function: FunctionInfo) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    for offset, raw_line in enumerate(function.body.splitlines()):
        line = _strip_comments(raw_line).strip()
        if not line or line in {"{", "}"}:
            continue
        absolute_line = function.start_line + offset
        lines.append((absolute_line, " ".join(line.split())))
    return lines


def _definitions_in_statement(statement: str) -> list[str]:
    definitions: list[str] = []
    if statement.startswith(("return", "goto", "if", "for", "while", "switch", "case", "default")):
        return definitions
    declaration = re.match(
        r"^(?:const\s+|static\s+|volatile\s+|unsigned\s+|signed\s+|long\s+|short\s+|"
        r"struct\s+[A-Za-z_][A-Za-z_0-9]*\s+|enum\s+[A-Za-z_][A-Za-z_0-9]*\s+|"
        r"union\s+[A-Za-z_][A-Za-z_0-9]*\s+|[A-Za-z_][A-Za-z_0-9:<>]*\s+)+"
        r"(?P<decls>[^;{}()]+);?",
        statement,
    )
    if declaration:
        for part in declaration.group("decls").split(","):
            match = re.search(r"[*&\s]*([A-Za-z_][A-Za-z_0-9]*)\s*(?:=|\[|;|$)", part.strip())
            if match:
                variable = match.group(1)
                if variable not in C_KEYWORDS:
                    definitions.append(variable)

    for match in re.finditer(
        r"\b([A-Za-z_][A-Za-z_0-9]*)\s*(?:=|\+=|-=|\*=|/=|%=|\+\+|--)",
        statement,
    ):
        variable = match.group(1)
        if variable not in C_KEYWORDS:
            definitions.append(variable)
    return _ordered_unique(definitions)


def _pointer_variables_in_statement(statement: str) -> list[str]:
    variables: list[str] = []
    declaration = re.match(
        r"^(?:const\s+|static\s+|volatile\s+|unsigned\s+|signed\s+|long\s+|short\s+|"
        r"struct\s+[A-Za-z_][A-Za-z_0-9]*\s+|[A-Za-z_][A-Za-z_0-9:<>]*\s+)+(?P<decls>[^;]+);?",
        statement,
    )
    if not declaration:
        return variables
    for part in declaration.group("decls").split(","):
        match = re.search(r"\*\s*([A-Za-z_][A-Za-z_0-9]*)", part)
        if match:
            variables.append(match.group(1))
    return variables


def _resource_events_in_statement(statement: str, line_number: int) -> list[dict]:
    events: list[dict] = []
    assignment = re.search(r"\b([A-Za-z_][A-Za-z_0-9]*)\s*=\s*([A-Za-z_][A-Za-z_0-9]*)\s*\(", statement)
    if assignment:
        variable, call = assignment.group(1), assignment.group(2)
        if call in RESOURCE_ALLOCATORS:
            events.append(_event("alloc", variable, line_number, statement))
        elif call in RESOURCE_OPENERS:
            events.append(_event("open", variable, line_number, statement))

    for match in re.finditer(r"\b([A-Za-z_][A-Za-z_0-9]*)\s*\(([^)]*)\)", statement):
        call = match.group(1)
        args = [arg.strip().lstrip("&*") for arg in match.group(2).split(",") if arg.strip()]
        variable = args[0] if args else ""
        if call in RESOURCE_FREERS:
            events.append(_event("free", variable, line_number, statement))
        elif call in RESOURCE_CLOSERS:
            events.append(_event("close", variable, line_number, statement))
        elif call in RESOURCE_LOCKERS:
            events.append(_event("lock", variable, line_number, statement))
        elif call in RESOURCE_UNLOCKERS:
            events.append(_event("unlock", variable, line_number, statement))
    return events


def _identifier_uses(statement: str) -> list[str]:
    return [
        token
        for token in re.findall(r"\b[A-Za-z_][A-Za-z_0-9]*\b", statement)
        if token not in C_KEYWORDS
    ]


def _fact(variable: str, line_number: int, statement: str) -> dict:
    return {
        "variable": variable,
        "line": line_number,
        "statement": statement,
    }


def _event(kind: str, variable: str, line_number: int, statement: str) -> dict:
    return {
        "kind": kind,
        "variable": variable,
        "line": line_number,
        "statement": statement,
    }


def _strip_comments(line: str) -> str:
    return line.split("//", 1)[0]


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


DataflowAnalyzer = DataFlowAnalyzer
