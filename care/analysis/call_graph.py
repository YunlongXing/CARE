"""Interprocedural call graph construction."""

from __future__ import annotations

import re

from care.core.models import EdgeInfo, FunctionInfo

CALL_EXCLUSIONS = {
    "if",
    "else",
    "for",
    "while",
    "switch",
    "return",
    "sizeof",
    "alignof",
    "decltype",
    "catch",
    "new",
    "delete",
}


class CallGraphBuilder:
    """Build call edges from extracted function bodies."""

    def build(self, functions: list[FunctionInfo]) -> list[EdgeInfo]:
        edges: list[EdgeInfo] = []
        known_functions = {function.name for function in functions}
        known_short_names = {name.split("::")[-1] for name in known_functions}

        for function in functions:
            calls = function.calls or _extract_calls(function.body)
            for call in calls:
                short_call = call.split("::")[-1]
                if short_call in CALL_EXCLUSIONS:
                    continue
                dst = _resolve_call_name(call, known_functions, known_short_names)
                edges.append(EdgeInfo(src=function.name, dst=dst, edge_type="call"))

        return _dedupe_edges(edges)


def _extract_calls(body: str) -> list[str]:
    calls: list[str] = []
    for match in re.finditer(
        r"\b([A-Za-z_][A-Za-z_0-9]*(?:::[A-Za-z_][A-Za-z_0-9]*)*)\s*\(",
        _mask_comments_and_strings(body),
    ):
        call = match.group(1)
        if call.split("::")[-1] not in CALL_EXCLUSIONS:
            calls.append(call)
    return calls


def _resolve_call_name(
    call: str,
    known_functions: set[str],
    known_short_names: set[str],
) -> str:
    if call in known_functions:
        return call
    short_call = call.split("::")[-1]
    if short_call in known_short_names:
        for known in sorted(known_functions):
            if known.split("::")[-1] == short_call:
                return known
    return call


def _dedupe_edges(edges: list[EdgeInfo]) -> list[EdgeInfo]:
    seen: set[tuple[str, str, str]] = set()
    result: list[EdgeInfo] = []
    for edge in edges:
        key = (edge.src, edge.dst, edge.edge_type)
        if key not in seen:
            seen.add(key)
            result.append(edge)
    return result


def _mask_comments_and_strings(content: str) -> str:
    chars = list(content)
    index = 0
    state = "code"
    while index < len(chars):
        current = chars[index]
        nxt = chars[index + 1] if index + 1 < len(chars) else ""
        if state == "code":
            if current == "/" and nxt == "/":
                chars[index] = chars[index + 1] = " "
                index += 2
                state = "line_comment"
                continue
            if current == "/" and nxt == "*":
                chars[index] = chars[index + 1] = " "
                index += 2
                state = "block_comment"
                continue
            if current == '"':
                chars[index] = " "
                index += 1
                state = "string"
                continue
            if current == "'":
                chars[index] = " "
                index += 1
                state = "char"
                continue
        elif state == "line_comment":
            if current == "\n":
                state = "code"
            else:
                chars[index] = " "
        elif state == "block_comment":
            if current == "*" and nxt == "/":
                chars[index] = chars[index + 1] = " "
                index += 2
                state = "code"
                continue
            if current != "\n":
                chars[index] = " "
        elif state in {"string", "char"}:
            quote = '"' if state == "string" else "'"
            if current == "\\":
                chars[index] = " "
                if index + 1 < len(chars) and chars[index + 1] != "\n":
                    chars[index + 1] = " "
                    index += 2
                    continue
            if current == quote:
                state = "code"
            if current != "\n":
                chars[index] = " "
        index += 1
    return "".join(chars)
