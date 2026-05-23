"""Heuristics for project-local resource ownership and wrapper APIs."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from care.core.models import FunctionInfo

RELEASE_HINTS = (
    "free",
    "destroy",
    "dispose",
    "cleanup",
    "close",
    "release",
    "unref",
    "decref",
)
TRANSFER_HINTS = (
    "take",
    "adopt",
    "steal",
    "own",
    "append",
    "push",
    "insert",
    "add",
    "register",
    "store",
    "put",
    "set",
)
BORROW_HINTS = ("get", "peek", "find", "lookup", "borrow")


@dataclass
class FunctionOwnershipSummary:
    """Conservative summary of a function's ownership behavior."""

    name: str
    releases_argument: bool = False
    transfers_argument: bool = False
    borrows_argument: bool = False
    evidence: list[str] = field(default_factory=list)


def summarize_function_ownership(function: FunctionInfo) -> FunctionOwnershipSummary:
    """Summarize likely ownership behavior from naming and body evidence."""

    lowered = function.name.lower()
    summary = FunctionOwnershipSummary(name=function.name)
    if any(hint in lowered for hint in RELEASE_HINTS):
        summary.releases_argument = True
        summary.evidence.append("release-like function name")
    if any(hint in lowered for hint in TRANSFER_HINTS):
        summary.transfers_argument = True
        summary.evidence.append("ownership-transfer-like function name")
    if any(hint in lowered for hint in BORROW_HINTS):
        summary.borrows_argument = True
        summary.evidence.append("borrow-like function name")
    if re.search(r"\b(free|fclose|close|unlock|mutex_unlock|pthread_mutex_unlock)\s*\(", function.body):
        summary.releases_argument = True
        summary.evidence.append("body contains release primitive")
    return summary


def is_release_wrapper_call(statement: str, variable: str) -> bool:
    """Return True when a statement appears to release ``variable`` through a wrapper."""

    return _matches_call_with_variable(statement, variable, RELEASE_HINTS)


def is_ownership_transfer_call(statement: str, variable: str) -> bool:
    """Return True when a statement appears to transfer ownership of ``variable``."""

    if re.search(rf"\breturn\s+{re.escape(variable)}\s*;", statement):
        return True
    return _matches_call_with_variable(statement, variable, TRANSFER_HINTS)


def is_borrow_call(statement: str, variable: str) -> bool:
    """Return True when a statement appears to borrow rather than consume ``variable``."""

    return _matches_call_with_variable(statement, variable, BORROW_HINTS)


def _matches_call_with_variable(statement: str, variable: str, hints: tuple[str, ...]) -> bool:
    if not variable:
        return False
    for call, args in _iter_calls(statement):
        lowered = call.lower()
        if not any(hint in lowered for hint in hints):
            continue
        if _arg_mentions_variable(args, variable):
            return True
    return False


def _iter_calls(statement: str) -> list[tuple[str, list[str]]]:
    calls: list[tuple[str, list[str]]] = []
    for match in re.finditer(r"\b([A-Za-z_][A-Za-z_0-9:]*)\s*\(([^()]*)\)", statement):
        call = match.group(1).split("::")[-1]
        args = [part.strip() for part in match.group(2).split(",") if part.strip()]
        calls.append((call, args))
    return calls


def _arg_mentions_variable(args: list[str], variable: str) -> bool:
    escaped = re.escape(variable)
    return any(re.search(rf"(?:^|[^A-Za-z_0-9]){escaped}(?:$|[^A-Za-z_0-9])", arg) for arg in args)
