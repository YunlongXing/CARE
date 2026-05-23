"""Test-suite validation."""

from __future__ import annotations

from care.core.models import StageResult
from care.validation.common import run_shell_stage


class TestRunner:
    """Run the project's configured test command when present."""

    def run(self, project: object) -> StageResult:
        return run_shell_stage(
            name="tests",
            command=getattr(project, "test_cmd", None),
            cwd=getattr(project, "root", "."),
            missing_message="no test command provided; test execution skipped",
        )


TestValidator = TestRunner
