"""Compiler/build validation."""

from __future__ import annotations

from care.core.models import StageResult
from care.validation.common import run_shell_stage


class CompileChecker:
    """Run the project's configured build command."""

    def run(self, project: object) -> StageResult:
        return run_shell_stage(
            name="compile",
            command=getattr(project, "build_cmd", None),
            cwd=getattr(project, "root", "."),
            missing_message="no build command provided; compile check skipped",
        )


CompilerValidator = CompileChecker
