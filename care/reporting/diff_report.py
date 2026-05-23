"""Diff and run summary report generation."""

from __future__ import annotations

from care.core.models import (
    PatchCandidate,
    ProjectContext,
    RefactoringReport,
    RefactoringOpportunity,
    RefactoringPlan,
    ValidationResult,
)


class DiffReportBuilder:
    """Build a simple text report for prototype runs."""

    def build(
        self,
        context: ProjectContext,
        opportunities: list[RefactoringOpportunity],
        plans: list[RefactoringPlan],
        patches: list[PatchCandidate],
        validations: list[ValidationResult],
    ) -> str:
        lines = [
            "CARE run summary",
            f"Project: {context.root}",
            f"Source files: {len(context.source_files)}",
            f"Opportunities: {len(opportunities)}",
            f"Plans: {len(plans)}",
            f"Patches: {len(patches)}",
            f"Validations: {len(validations)}",
        ]
        for opportunity in opportunities:
            location = (
                f"{opportunity.location.file}:{opportunity.location.line}"
                if opportunity.location.line
                else opportunity.file
            )
            lines.append(f"- [{opportunity.kind}] {location} - {opportunity.description}")
        return "\n".join(lines)

    def build_structured(
        self,
        context: ProjectContext,
        opportunities: list[RefactoringOpportunity],
        patches: list[PatchCandidate],
        validations: list[ValidationResult],
    ) -> RefactoringReport:
        return RefactoringReport(
            project=context.root,
            opportunities=opportunities,
            selected_patches=patches,
            validation_results=validations,
        )
