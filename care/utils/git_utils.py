"""Git helper functions."""

from __future__ import annotations

from pathlib import Path

from care.utils.shell import CommandResult, run_command


def git_diff(root: Path) -> CommandResult:
    return run_command("git diff --no-ext-diff", cwd=root)


def git_status(root: Path) -> CommandResult:
    return run_command("git status --short", cwd=root)
