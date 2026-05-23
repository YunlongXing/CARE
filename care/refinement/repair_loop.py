"""Feedback-guided iterative patch repair."""

from __future__ import annotations

from typing import Optional, Union

from care.core.models import (
    FunctionInfo,
    PatchCandidate,
    ProjectContext,
    RefactoringOpportunity,
    RefactoringPlan,
    ValidationResult,
)
from care.llm.patch_generator import PatchGenerator
from care.llm.planner import RefactoringPlanner
from care.refinement.feedback import RepairFeedback
from care.validation.validator import RefactoringValidator


class RepairLoop:
    """Generate, validate, and repair patch candidates using validator feedback."""

    def __init__(
        self,
        patch_generator: PatchGenerator,
        validator: RefactoringValidator,
        max_iterations: int,
        planner: Optional[RefactoringPlanner] = None,
        project: Optional[object] = None,
        context: Optional[ProjectContext] = None,
        candidates_per_iteration: int = 3,
        mode: str = "conservative",
    ) -> None:
        self.patch_generator = patch_generator
        self.validator = validator
        self.max_iterations = max(1, max_iterations)
        self.planner = planner
        self.project = project
        self.context = context
        self.candidates_per_iteration = max(1, candidates_per_iteration)
        self.mode = mode
        self.last_candidates: list[PatchCandidate] = []

    def run(
        self,
        opportunity: Union[RefactoringOpportunity, RefactoringPlan],
        plan: Optional[RefactoringPlan] = None,
    ) -> tuple[Optional[PatchCandidate], ValidationResult]:
        plan = plan or self._plan_for(opportunity)
        source_snippet, full_function_body, context_summary = self._patch_context(opportunity)
        last_validation = _empty_failure("no patch candidates were generated")
        feedback: Optional[RepairFeedback] = None
        self.last_candidates = []

        for iteration in range(self.max_iterations):
            candidates = self.patch_generator.generate_candidates(
                source_snippet=source_snippet,
                full_function_body=full_function_body,
                plan=plan,
                context_summary=context_summary,
                validation_feedback=feedback,
                mode=self.mode,
                n=self.candidates_per_iteration,
                project=self.project,
            )
            if not candidates:
                last_validation = _empty_failure("patch generator returned no candidates")
                feedback = RepairFeedback.from_messages([self.build_feedback_prompt(last_validation)])
                continue
            self.last_candidates.extend(candidates)

            failed_results: list[ValidationResult] = []
            for candidate in candidates:
                if self.project is None:
                    last_validation = _empty_failure("repair loop has no project for validation")
                else:
                    last_validation = self.validator.validate(self.project, candidate)
                if last_validation.passed:
                    return candidate, last_validation
                failed_results.append(last_validation)

            last_validation = _merge_failures(failed_results)
            feedback = RepairFeedback.from_messages([self.build_feedback_prompt(last_validation)])

        return None, last_validation

    def build_feedback_prompt(self, validation_result: ValidationResult) -> str:
        logs = "\n".join(validation_result.logs) if validation_result.logs else "(no logs)"
        counterexamples = (
            "\n".join(validation_result.counterexamples)
            if validation_result.counterexamples
            else "(no counterexamples)"
        )
        return (
            "The previous patch failed validation.\n\n"
            "Failure logs:\n"
            f"{logs}\n\n"
            "Counterexamples:\n"
            f"{counterexamples}\n\n"
            "Revise the refactoring plan and generate a smaller safer patch.\n\n"
            "Constraints:\n"
            "- Fix the validation failure.\n"
            "- Preserve all previously stated invariants.\n"
            "- Do not introduce unrelated changes.\n"
            "- Prefer reverting risky edits.\n"
            "- Prefer JSON edit plans that CARE can convert to a diff; unified diff fallback is acceptable."
        )

    def _plan_for(
        self,
        opportunity: Union[RefactoringOpportunity, RefactoringPlan],
    ) -> RefactoringPlan:
        if isinstance(opportunity, RefactoringPlan):
            return opportunity
        if self.planner is None or self.context is None:
            raise ValueError("planner and context are required when running repair loop from an opportunity")
        return self.planner.plan(self.context, opportunity)

    def _patch_context(
        self,
        opportunity: Union[RefactoringOpportunity, RefactoringPlan],
    ) -> tuple[str, str, str]:
        if isinstance(opportunity, RefactoringPlan):
            return "", "", ""
        function = self._function_for(opportunity)
        if function is None:
            return "", "", opportunity.context_summary
        return (
            _source_snippet(function, opportunity.location.line),
            function.body,
            opportunity.context_summary,
        )

    def _function_for(self, opportunity: RefactoringOpportunity) -> Optional[FunctionInfo]:
        if self.context is None:
            return None
        for function in self.context.functions:
            if function.name == opportunity.function and function.file == opportunity.file:
                return function
        for function in self.context.functions:
            if function.name == opportunity.function:
                return function
        return None


def _source_snippet(function: FunctionInfo, line: int, radius: int = 4) -> str:
    lines: list[str] = []
    for offset, raw_line in enumerate(function.body.splitlines()):
        absolute_line = function.start_line + offset
        if abs(absolute_line - line) <= radius:
            lines.append(f"{absolute_line}: {raw_line}")
    return "\n".join(lines)


def _empty_failure(message: str) -> ValidationResult:
    return ValidationResult(
        passed=False,
        stage_results={},
        logs=[message],
        counterexamples=[message],
    )


def _merge_failures(results: list[ValidationResult]) -> ValidationResult:
    logs: list[str] = []
    counterexamples: list[str] = []
    stage_results = {}
    for index, result in enumerate(results):
        logs.extend(f"candidate {index}: {log}" for log in result.logs)
        counterexamples.extend(f"candidate {index}: {item}" for item in result.counterexamples)
        stage_results[f"candidate_{index}"] = result.to_dict()
    return ValidationResult(
        passed=any(result.passed for result in results),
        stage_results=stage_results,
        logs=logs,
        counterexamples=counterexamples,
    )
