"""Aggregate refactoring validation pipeline."""

from __future__ import annotations

from typing import Optional

from care.config import CareConfig
from care.core.models import FunctionInfo, PatchCandidate, StageResult, ValidationResult
from care.core.project import ProjectLoader
from care.validation.common import parse_project_functions, stage_from_exception
from care.validation.compiler import CompileChecker
from care.validation.resource_checker import ResourceConsistencyChecker
from care.validation.security_checker import SecurityRegressionChecker
from care.validation.semantic_checker import SemanticEquivalenceChecker
from care.validation.static_analysis import StaticAnalysisRunner
from care.validation.taxonomy import classify_stages
from care.validation.tests import TestRunner


class RefactoringValidator:
    """Apply a patch to a clean project and run all validation gates."""

    def __init__(
        self,
        config: Optional[CareConfig] = None,
        project: Optional[object] = None,
        disabled_stages: set[str] | None = None,
    ) -> None:
        self.config = config.normalized() if config is not None else None
        self.project = project
        self.compile_checker = CompileChecker()
        self.test_runner = TestRunner()
        self.static_analysis = StaticAnalysisRunner()
        self.resource_checker = ResourceConsistencyChecker()
        self.semantic_checker = SemanticEquivalenceChecker()
        self.security_checker = SecurityRegressionChecker()
        self.disabled_stages = frozenset(disabled_stages or ())
        self._backup_created = False

    def validate(
        self,
        project: object,
        patch_candidate: Optional[PatchCandidate] = None,
    ) -> ValidationResult:
        if patch_candidate is None:
            if self.project is None:
                raise ValueError("project is required")
            patch_candidate = project  # type: ignore[assignment]
            project = self.project

        stages: list[StageResult] = []
        before_functions = []
        after_functions = []
        prefer_clang = self._prefer_clang_analysis()
        try:
            if not self._backup_created:
                project.backup()
                self._backup_created = True
            project.restore()
            before_functions = parse_project_functions(project, prefer_clang=prefer_clang)
        except Exception as exc:
            stages.append(stage_from_exception("restore_clean_project", exc))
            return _validation_from_stages(stages)

        patch_applied = False
        patch_repair_notes: list[str] = []
        try:
            if not patch_candidate.diff.strip():
                stages.append(
                    StageResult(
                        name="apply_patch",
                        passed=False,
                        logs=[
                            f"patch candidate {patch_candidate.id} is empty: "
                            f"{patch_candidate.explanation}"
                        ],
                        counterexamples=["empty patch candidate"],
                    )
                )
                return _validation_from_stages(stages)
            repair_patch_paths = getattr(project, "repair_patch_paths", None)
            if callable(repair_patch_paths):
                repaired_diff, patch_repair_notes = repair_patch_paths(patch_candidate.diff)
                if repaired_diff != patch_candidate.diff:
                    patch_candidate.diff = repaired_diff
            check_patch = getattr(project, "check_patch", None)
            if callable(check_patch):
                check_ok, check_log = check_patch(patch_candidate.diff)
                if not check_ok:
                    stages.append(
                        StageResult(
                            name="apply_patch",
                            passed=False,
                            logs=[
                                f"git apply --check failed for patch candidate {patch_candidate.id}",
                                *[f"patch path repair: {note}" for note in patch_repair_notes],
                                check_log or "patch check failed without diagnostics",
                            ],
                            counterexamples=[check_log or "patch failed pre-apply check"],
                        )
                    )
                    return _validation_from_stages(stages)
            project.apply_patch(patch_candidate.diff)
            patch_applied = True
            effective_diff = project.git_diff()
            if not effective_diff.strip():
                stages.append(
                    StageResult(
                        name="apply_patch",
                        passed=False,
                        logs=[
                            f"patch candidate {patch_candidate.id} applied but produced no project changes"
                        ],
                        counterexamples=["patch produced no project changes"],
                    )
                )
                return _validation_from_stages(stages)
            stages.append(
                StageResult(
                    name="apply_patch",
                    passed=True,
                    logs=[
                        f"git apply --check passed for patch candidate {patch_candidate.id}",
                        *[f"patch path repair: {note}" for note in patch_repair_notes],
                        f"applied patch candidate {patch_candidate.id}",
                    ],
                )
            )
        except Exception as exc:
            stages.append(
                StageResult(
                    name="apply_patch",
                    passed=False,
                    logs=[f"failed to apply patch candidate {patch_candidate.id}: {exc}"],
                    counterexamples=[str(exc)],
                )
            )
            return _validation_from_stages(stages)

        try:
            stages.append(self.compile_checker.run(project))
            stages.append(self.test_runner.run(project))
            try:
                static_result = self.static_analysis.run(
                    project,
                    changed_files=_changed_source_files(patch_candidate.diff),
                )
            except TypeError as exc:
                if "changed_files" not in str(exc):
                    raise
                static_result = self.static_analysis.run(project)
            stages.append(static_result)
            try:
                after_functions = parse_project_functions(project, prefer_clang=prefer_clang)
            except Exception:
                after_functions = []
            stages.append(self._resource_stage(
                project,
                patch_candidate,
                after_functions,
                before_functions,
                prefer_clang,
            ))
            stages.append(self._semantic_stage(
                project,
                patch_candidate,
                before_functions,
                after_functions,
            ))
            stages.append(self._security_stage(
                project,
                patch_candidate,
                before_functions,
                after_functions,
            ))
        finally:
            if patch_applied:
                try:
                    project.restore()
                except Exception as exc:
                    stages.append(stage_from_exception("restore_after_validation", exc))
        return _validation_from_stages(stages)

    def _prefer_clang_analysis(self) -> bool:
        return self.config is None or self.config.analysis_backend != "regex"

    def _resource_stage(
        self,
        project: object,
        patch_candidate: PatchCandidate,
        after_functions: list[FunctionInfo],
        before_functions: list[FunctionInfo],
        prefer_clang: bool,
    ) -> StageResult:
        if "resource_consistency" in self.disabled_stages:
            return _disabled_stage("resource_consistency")
        return self.resource_checker.run(
            project,
            patch_candidate,
            functions=after_functions,
            before_functions=before_functions,
            prefer_clang=prefer_clang,
        )

    def _semantic_stage(
        self,
        project: object,
        patch_candidate: PatchCandidate,
        before_functions: list[FunctionInfo],
        after_functions: list[FunctionInfo],
    ) -> StageResult:
        if "semantic_equivalence" in self.disabled_stages:
            return _disabled_stage("semantic_equivalence")
        return self.semantic_checker.run(
            project,
            patch_candidate,
            before_functions=before_functions,
            after_functions=after_functions,
        )

    def _security_stage(
        self,
        project: object,
        patch_candidate: PatchCandidate,
        before_functions: list[FunctionInfo],
        after_functions: list[FunctionInfo],
    ) -> StageResult:
        if "security_regression" in self.disabled_stages:
            return _disabled_stage("security_regression")
        return self.security_checker.run(
            project,
            patch_candidate,
            before_functions=before_functions,
            after_functions=after_functions,
        )

    @classmethod
    def from_config(cls, config: CareConfig) -> "RefactoringValidator":
        normalized = config.normalized()
        project = ProjectLoader().load(
            project_path=normalized.project_path,
            build_cmd=normalized.build_cmd,
            test_cmd=normalized.test_cmd,
            compile_database_path=normalized.compile_database_path,
        )
        return cls(config=normalized, project=project)


def _validation_from_stages(stages: list[StageResult]) -> ValidationResult:
    stage_results = {stage.name: stage.to_dict() for stage in stages}
    stage_results["failure_taxonomy"] = classify_stages(stages)
    logs: list[str] = []
    counterexamples: list[str] = []
    for stage in stages:
        logs.extend(f"{stage.name}: {log}" for log in stage.logs)
        logs.extend(f"{stage.name} warning: {warning}" for warning in stage.warnings)
        counterexamples.extend(stage.counterexamples)
    return ValidationResult(
        passed=all(stage.passed for stage in stages),
        stage_results=stage_results,
        logs=logs,
        counterexamples=counterexamples,
    )


def _disabled_stage(name: str) -> StageResult:
    return StageResult(name=name, passed=True, warnings=[f"{name} disabled for ablation"])


def _changed_source_files(diff: str) -> list[str]:
    extensions = (".c", ".cc", ".cpp", ".h", ".hpp")
    files: list[str] = []
    for line in diff.splitlines():
        if not line.startswith("+++ "):
            continue
        path = line[4:].strip()
        if path == "/dev/null":
            continue
        if path.startswith("b/"):
            path = path[2:]
        if path.endswith(extensions) and path not in files:
            files.append(path)
    return files


Validator = RefactoringValidator
