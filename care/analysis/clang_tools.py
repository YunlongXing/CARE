"""Thin wrappers around clang AST and CFG dumps.

CARE intentionally keeps clang integration optional. When the clang executable
is available, these helpers expose full translation-unit JSON AST dumps and
clang analyzer CFG text without requiring the Python libclang bindings.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional


class ClangToolError(RuntimeError):
    """Raised when clang cannot produce the requested analysis artifact."""


def find_clang(explicit: Optional[str] = None) -> Optional[str]:
    """Return a clang executable path if one is available."""

    if explicit:
        return explicit if shutil.which(explicit) or Path(explicit).exists() else None
    return shutil.which("clang")


def clang_available(explicit: Optional[str] = None) -> bool:
    return find_clang(explicit) is not None


def default_compile_args(
    source_path: Path,
    project_root: Optional[Path] = None,
    compile_args: Optional[list[str]] = None,
) -> list[str]:
    """Build conservative clang arguments for standalone parsing."""

    args = list(compile_args or [])
    args.extend(_language_args(source_path, args))
    args.extend(_include_args(source_path, project_root))
    args.append("-Wno-everything")
    return _dedupe_args(args)


def compile_args_for_project(project: object) -> dict[str, list[str]]:
    """Read compile_commands.json when a CARE project exposes one."""

    compile_database_path = getattr(project, "compile_database_path", None)
    if not compile_database_path:
        return {}
    path = Path(compile_database_path)
    if not path.exists():
        return {}
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    result: dict[str, list[str]] = {}
    for entry in entries:
        directory = Path(entry.get("directory") or path.parent)
        file_path = Path(entry.get("file") or "")
        source_path = file_path if file_path.is_absolute() else directory / file_path
        command_args = entry.get("arguments")
        if not command_args and entry.get("command"):
            command_args = shlex.split(entry["command"])
        if not command_args:
            continue
        result[str(source_path.resolve())] = _sanitize_compile_args(command_args, source_path)
    return result


def dump_ast_json(
    source_path: Path,
    project_root: Optional[Path] = None,
    compile_args: Optional[list[str]] = None,
    clang_bin: Optional[str] = None,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Return clang's complete JSON AST for a translation unit."""

    clang = find_clang(clang_bin)
    if clang is None:
        raise ClangToolError("clang executable not found")
    args = default_compile_args(source_path, project_root, compile_args)
    command = [clang, "-Xclang", "-ast-dump=json", "-fsyntax-only", *args, str(source_path)]
    completed = _run(command, source_path.parent, timeout_seconds)
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ClangToolError(f"clang AST output was not valid JSON: {exc}") from exc


def dump_cfg_text(
    source_path: Path,
    project_root: Optional[Path] = None,
    compile_args: Optional[list[str]] = None,
    clang_bin: Optional[str] = None,
    timeout_seconds: int = 30,
) -> str:
    """Return clang analyzer debug.DumpCFG text for a translation unit."""

    clang = find_clang(clang_bin)
    if clang is None:
        raise ClangToolError("clang executable not found")
    args = default_compile_args(source_path, project_root, compile_args)
    command = [
        clang,
        "--analyze",
        "-Xanalyzer",
        "-analyzer-checker=debug.DumpCFG",
        *args,
        str(source_path),
    ]
    completed = _run(command, source_path.parent, timeout_seconds)
    return "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()


def _run(
    command: list[str],
    cwd: Path,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ClangToolError(f"clang invocation failed: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise ClangToolError(f"clang exited with {completed.returncode}: {detail}")
    return completed


def _sanitize_compile_args(args: list[str], source_path: Path) -> list[str]:
    sanitized: list[str] = []
    skip_next = False
    source_resolved = source_path.resolve()
    for index, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if index == 0 and ("clang" in Path(arg).name or "gcc" in Path(arg).name or arg.endswith("cc")):
            continue
        if arg in {"-c", "-S", "-E"}:
            continue
        if arg == "-o":
            skip_next = True
            continue
        if arg.startswith("-o"):
            continue
        if arg.startswith("-M"):
            continue
        try:
            if Path(arg).expanduser().resolve() == source_resolved:
                continue
        except OSError:
            pass
        sanitized.append(arg)
    return sanitized


def _language_args(source_path: Path, existing_args: list[str]) -> list[str]:
    if any(arg == "-x" or arg.startswith("-x") for arg in existing_args):
        return []
    suffix = source_path.suffix.lower()
    if suffix in {".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"}:
        language = "c++-header" if suffix in {".hpp", ".hh", ".hxx"} else "c++"
        std = [] if any(arg.startswith("-std=") for arg in existing_args) else ["-std=c++17"]
        return ["-x", language, *std]
    if suffix == ".h":
        return ["-x", "c-header"]
    std = [] if any(arg.startswith("-std=") for arg in existing_args) else ["-std=c11"]
    return ["-x", "c", *std]


def _include_args(source_path: Path, project_root: Optional[Path]) -> list[str]:
    candidates = [source_path.parent]
    if project_root is not None:
        root = Path(project_root).resolve()
        candidates.extend(
            [
                root,
                root / "include",
                root.parent,
                root.parent / "include",
                root.parent.parent / "include",
            ]
        )
    args: list[str] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.exists():
            continue
        seen.add(resolved)
        args.extend(["-I", str(resolved)])
    return args


def _dedupe_args(args: list[str]) -> list[str]:
    result: list[str] = []
    seen_pairs: set[tuple[str, str]] = set()
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"-I", "-isystem", "-D", "-U", "-x"} and index + 1 < len(args):
            pair = (arg, args[index + 1])
            if pair not in seen_pairs:
                result.extend(pair)
                seen_pairs.add(pair)
            index += 2
            continue
        if arg not in result:
            result.append(arg)
        index += 1
    return result
