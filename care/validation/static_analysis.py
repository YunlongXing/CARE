"""Optional static-analysis validation."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from care.core.models import StageResult


class StaticAnalysisRunner:
    """Run installed C/C++ static-analysis tools and record missing tools."""

    def run(self, project: object, changed_files: list[str] | None = None) -> StageResult:
        root = Path(getattr(project, "root", "."))
        source_files = changed_files or [str(path) for path in getattr(project, "source_files", [])]
        logs: list[str] = []
        warnings: list[str] = []
        counterexamples: list[str] = []
        passed = True

        tool_commands = self._tool_commands(project, root, source_files, changed_files is not None)
        for tool, command in tool_commands:
            if shutil.which(tool) is None:
                warnings.append(f"{tool} not installed; skipped")
                continue
            result = _run(command, root)
            logs.extend([f"$ {' '.join(command)}", f"exit code: {result.returncode}"])
            if result.stdout.strip():
                logs.append(result.stdout.strip())
            if result.stderr.strip():
                logs.append(result.stderr.strip())
            if result.returncode != 0:
                passed = False
                counterexamples.append(f"{tool} reported issues")

        return StageResult(
            name="static_analysis",
            passed=passed,
            logs=logs,
            warnings=warnings,
            counterexamples=counterexamples,
        )

    def _tool_commands(
        self,
        project: object,
        root: Path,
        source_files: list[str],
        changed_only: bool,
    ) -> list[tuple[str, list[str]]]:
        commands: list[tuple[str, list[str]]] = []
        compile_db = getattr(project, "compile_database_path", None)
        if source_files:
            clang_command = ["clang-tidy", *source_files[:5]]
            if compile_db:
                clang_command.extend(["-p", str(Path(compile_db).parent)])
            else:
                clang_command.append("--")
            commands.append(("clang-tidy", clang_command))
        else:
            commands.append(("clang-tidy", ["clang-tidy", "--version"]))

        cppcheck_command = [
            "cppcheck",
            "--enable=warning,style,performance,portability",
            "--quiet",
            *(source_files if changed_only and source_files else [str(root)]),
        ]
        commands.append(("cppcheck", cppcheck_command))

        build_cmd = getattr(project, "build_cmd", None)
        if build_cmd and not changed_only:
            commands.append(("scan-build", ["scan-build", "--status-bugs", *build_cmd.split()]))
        elif not changed_only:
            commands.append(("scan-build", ["scan-build", "--version"]))
        return commands


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
            timeout=int(os.environ.get("CARE_STATIC_ANALYSIS_TIMEOUT", "60")),
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            args=command,
            returncode=124,
            stdout="",
            stderr=f"timed out: {exc}",
        )


StaticAnalysisValidator = StaticAnalysisRunner
