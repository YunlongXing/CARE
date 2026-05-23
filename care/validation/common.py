"""Shared validation helpers."""

from __future__ import annotations

import subprocess
from typing import Optional

from care.analysis.ast_parser import ASTParser
from care.core.models import FunctionInfo, StageResult


def run_shell_stage(
    name: str,
    command: Optional[str],
    cwd: object,
    missing_message: str,
    timeout_seconds: int = 120,
) -> StageResult:
    if not command:
        return StageResult(name=name, passed=True, warnings=[missing_message])
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            shell=True,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return StageResult(
            name=name,
            passed=False,
            logs=[f"{name} timed out after {timeout_seconds}s: {command}"],
            counterexamples=[str(exc)],
        )
    logs = [f"$ {command}", f"exit code: {completed.returncode}"]
    if completed.stdout.strip():
        logs.append(completed.stdout.strip())
    if completed.stderr.strip():
        logs.append(completed.stderr.strip())
    return StageResult(name=name, passed=completed.returncode == 0, logs=logs)


def parse_project_functions(
    project: object,
    prefer_clang: bool = True,
) -> list[FunctionInfo]:
    return ASTParser(prefer_clang=prefer_clang).parse_project(project)


def stage_from_exception(name: str, exc: Exception) -> StageResult:
    return StageResult(
        name=name,
        passed=False,
        logs=[f"{name} failed with exception: {exc}"],
        counterexamples=[str(exc)],
    )
