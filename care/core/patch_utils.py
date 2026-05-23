"""Utilities for making LLM-produced unified diffs apply to project roots."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable


def repair_unified_diff_paths(
    diff: str,
    root: Path,
    source_files: Iterable[Path],
) -> tuple[str, list[str]]:
    """Repair common path mistakes in unified diffs.

    LLMs often emit paths that are correct in spirit but not relative to the
    checked-out project root, for example ``openssl/crypto/foo.c`` when the
    current root is already ``openssl``. This normalizer only rewrites file
    header paths when they can be matched to an existing project file.
    """

    if not diff.strip():
        return diff, []

    index = _PathIndex(root=root.resolve(), source_files=list(source_files))
    notes: list[str] = []
    repaired_lines: list[str] = []
    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git "):
            repaired_lines.append(_repair_diff_git_line(line, index, notes))
        elif line.startswith("--- ") or line.startswith("+++ "):
            repaired_lines.append(_repair_file_header_line(line, index, notes))
        elif line.startswith("rename from ") or line.startswith("rename to "):
            repaired_lines.append(_repair_rename_line(line, index, notes))
        else:
            repaired_lines.append(line)
    return "".join(repaired_lines), _unique(notes)


def _repair_diff_git_line(line: str, index: "_PathIndex", notes: list[str]) -> str:
    ending = _line_ending(line)
    body = line[len("diff --git ") : len(line) - len(ending) if ending else len(line)]
    match = re.match(r"^(?P<old>\S+)\s+(?P<new>\S+)$", body)
    if not match:
        return line
    old = _repair_prefixed_token(match.group("old"), index, notes, default_prefix="a/")
    new = _repair_prefixed_token(match.group("new"), index, notes, default_prefix="b/")
    return f"diff --git {old} {new}{ending}"


def _repair_file_header_line(line: str, index: "_PathIndex", notes: list[str]) -> str:
    ending = _line_ending(line)
    prefix = line[:4]
    payload = line[4 : len(line) - len(ending) if ending else len(line)]
    token, suffix = _split_header_payload(payload)
    if token == "/dev/null":
        return line
    default_prefix = "a/" if line.startswith("--- ") else "b/"
    repaired = _repair_prefixed_token(token, index, notes, default_prefix=default_prefix)
    return f"{prefix}{repaired}{suffix}{ending}"


def _repair_rename_line(line: str, index: "_PathIndex", notes: list[str]) -> str:
    ending = _line_ending(line)
    prefix = "rename from " if line.startswith("rename from ") else "rename to "
    token = line[len(prefix) : len(line) - len(ending) if ending else len(line)]
    repaired = index.resolve(token)
    if repaired != _clean_path_token(token):
        notes.append(f"rewrote patch path {token!r} -> {repaired!r}")
    return f"{prefix}{repaired}{ending}"


def _repair_prefixed_token(
    token: str,
    index: "_PathIndex",
    notes: list[str],
    default_prefix: str = "",
) -> str:
    prefix = ""
    raw = _strip_quotes(token)
    if raw.startswith("a/") or raw.startswith("b/"):
        prefix = raw[:2]
        raw = raw[2:]
    repaired = index.resolve(raw)
    if repaired != _clean_path_token(raw):
        notes.append(f"rewrote patch path {raw!r} -> {repaired!r}")
    if not prefix and default_prefix and repaired != "/dev/null":
        prefix = default_prefix
        notes.append(f"added patch path prefix {default_prefix!r} for {repaired!r}")
    return f"{prefix}{repaired}"


def _split_header_payload(payload: str) -> tuple[str, str]:
    if "\t" in payload:
        token, suffix = payload.split("\t", 1)
        return token, "\t" + suffix
    return payload, ""


def _line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return ""


def _strip_quotes(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] == '"':
        return token[1:-1]
    return token


def _clean_path_token(token: str) -> str:
    clean = _strip_quotes(token).replace("\\", "/")
    while clean.startswith("./"):
        clean = clean[2:]
    if clean.startswith("a/") or clean.startswith("b/"):
        clean = clean[2:]
    return clean


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


class _PathIndex:
    def __init__(self, root: Path, source_files: list[Path]) -> None:
        self.root = root
        self.relative_files = sorted(
            {
                path.resolve().relative_to(root).as_posix()
                for path in source_files
                if _is_under(path.resolve(), root)
            }
        )
        self.relative_set = set(self.relative_files)

    def resolve(self, token: str) -> str:
        clean = _clean_path_token(token)
        if clean == "/dev/null":
            return clean

        candidates = self._candidate_paths(clean)
        for candidate in candidates:
            if candidate in self.relative_set or (self.root / candidate).exists():
                return candidate

        suffix_matches = [
            relative
            for relative in self.relative_files
            for candidate in candidates
            if relative.endswith(f"/{candidate}") or candidate.endswith(f"/{relative}")
        ]
        unique_suffix_matches = sorted(set(suffix_matches))
        if len(unique_suffix_matches) == 1:
            return unique_suffix_matches[0]

        basename_matches = [
            relative for relative in self.relative_files if Path(relative).name == Path(clean).name
        ]
        if len(basename_matches) == 1:
            return basename_matches[0]

        return clean

    def _candidate_paths(self, clean: str) -> list[str]:
        candidates: list[str] = []
        raw_path = Path(clean)
        if raw_path.is_absolute():
            try:
                candidates.append(raw_path.resolve().relative_to(self.root).as_posix())
            except (OSError, ValueError):
                parts = raw_path.parts
                if self.root.name in parts:
                    index = len(parts) - 1 - list(reversed(parts)).index(self.root.name)
                    tail = Path(*parts[index + 1 :]).as_posix()
                    if tail:
                        candidates.append(tail)
            clean = raw_path.as_posix().lstrip("/")

        candidates.append(clean)
        parts = Path(clean).parts
        for index in range(1, len(parts)):
            stripped = Path(*parts[index:]).as_posix()
            if stripped:
                candidates.append(stripped)
        return _unique(candidates)


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
