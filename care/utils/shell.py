"""Shell command helpers."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CommandResult:
    command: str
    returncode: int
    stdout: str
    stderr: str

    def summary(self, label: str) -> str:
        status = "passed" if self.returncode == 0 else "failed"
        return f"{label}: {status} ({self.command})"


def run_command(command: str, cwd: Path) -> CommandResult:
    completed = subprocess.run(
        command,
        cwd=cwd,
        shell=True,
        check=False,
        text=True,
        capture_output=True,
    )
    return CommandResult(
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
