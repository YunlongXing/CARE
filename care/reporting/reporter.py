"""Human-readable and machine-readable CARE reporting."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, Union

from care.core.models import (
    PatchCandidate,
    PipelineResult,
    RefactoringOpportunity,
    RefactoringPlan,
    ValidationResult,
)


class ConsoleReporter:
    """Emit the concise console report and write CARE report artifacts."""

    def __init__(
        self,
        output_dir: Optional[Union[str, Path]] = None,
        write_files: bool = True,
    ) -> None:
        self.output_dir = Path(output_dir).expanduser() if output_dir is not None else None
        self.write_files = write_files

    def emit(self, result: PipelineResult) -> None:
        print(result.report)
        if self.write_files:
            output_dir = self.output_dir or Path(result.project)
            artifacts = CareReportWriter(output_dir).write(result)
            print(f"Reports written to: {artifacts['report_json']}")


class CareReportWriter:
    """Generate care-report.json, care-report.md, and patch diff artifacts."""

    def __init__(self, output_dir: Union[str, Path]) -> None:
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.patch_dir = self.output_dir / "patches"

    def write(self, result: PipelineResult) -> dict[str, str]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.patch_dir.mkdir(parents=True, exist_ok=True)

        report_payload = self.build_report(result)
        report_json = self.output_dir / "care-report.json"
        report_md = self.output_dir / "care-report.md"
        report_json.write_text(
            json.dumps(report_payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        report_md.write_text(self.build_markdown(report_payload), encoding="utf-8")
        return {
            "report_json": str(report_json),
            "report_markdown": str(report_md),
            "patch_dir": str(self.patch_dir),
        }

    def build_report(self, result: PipelineResult) -> dict[str, Any]:
        opportunity_numbers = {
            opportunity.id: index
            for index, opportunity in enumerate(result.opportunities, start=1)
        }
        plans_by_opportunity = {
            plan.opportunity_id: plan
            for plan in result.plans
        }
        validations_by_index = {
            index: validation
            for index, validation in enumerate(result.validations)
        }
        validation_by_opportunity = {
            opportunity.id: result.validations[index]
            for index, opportunity in enumerate(result.opportunities)
            if index < len(result.validations)
        }
        artifact_patches = result.candidate_patches or result.patches
        if result.candidate_patches:
            selected_patch_ids = {patch.id for patch in result.patches}
        else:
            selected_patch_ids = {
                patch.id
                for patch in result.patches
                if (
                    patch.opportunity_id not in validation_by_opportunity
                    or validation_by_opportunity[patch.opportunity_id].passed
                )
            }

        patch_records = self._write_patch_artifacts(
            patches=artifact_patches,
            opportunity_numbers=opportunity_numbers,
            selected_patch_ids=selected_patch_ids,
        )

        opportunity_records = []
        for index, opportunity in enumerate(result.opportunities, start=1):
            plan = plans_by_opportunity.get(opportunity.id)
            related_patches = [
                record for record in patch_records if record["opportunity_id"] == opportunity.id
            ]
            selected = [record for record in related_patches if record["selected"]]
            validation = validations_by_index.get(index - 1)
            opportunity_records.append(
                {
                    "number": index,
                    "opportunity": opportunity.to_dict(),
                    "plan": plan.to_dict() if plan else None,
                    "selected_refactorings": selected,
                    "explanation": _explanation(opportunity, plan, selected),
                    "before_after_summary": _before_after_summary(selected),
                    "validation": validation.to_dict() if validation else None,
                    "failed_candidates": _failed_candidates(opportunity, related_patches, validation),
                    "security_impact_summary": _security_impact_summary(opportunity, plan, validation),
                    "performance_impact_estimate": _performance_impact_estimate(plan, selected),
                    "traceability": {
                        "opportunity_id": opportunity.id,
                        "plan_opportunity_id": plan.opportunity_id if plan else None,
                        "patch_candidate_ids": [record["candidate_id"] for record in related_patches],
                        "selected_patch_paths": [record["selected_patch_path"] for record in selected],
                    },
                }
            )

        return {
            "project": result.project,
            "success": result.success,
            "summary": {
                "opportunities": len(result.opportunities),
                "plans": len(result.plans),
                "patches": len(result.patches),
                "patch_candidates": len(patch_records),
                "validations": len(result.validations),
                "selected_refactorings": len([record for record in patch_records if record["selected"]]),
                "failed_validations": len([validation for validation in result.validations if not validation.passed]),
            },
            "detected_opportunities": [opportunity.to_dict() for opportunity in result.opportunities],
            "selected_refactorings": [record for record in patch_records if record["selected"]],
            "patch_candidates": patch_records,
            "validation_results": [validation.to_dict() for validation in result.validations],
            "failed_candidates": _global_failed_candidates(result, patch_records),
            "before_after_summary": [
                {
                    "opportunity_id": report["opportunity"]["id"],
                    **report["before_after_summary"],
                }
                for report in opportunity_records
            ],
            "security_impact_summary": [
                {
                    "opportunity_id": report["opportunity"]["id"],
                    "summary": report["security_impact_summary"],
                }
                for report in opportunity_records
            ],
            "performance_impact_estimate": [
                {
                    "opportunity_id": report["opportunity"]["id"],
                    "estimate": report["performance_impact_estimate"],
                }
                for report in opportunity_records
            ],
            "traceability": [report["traceability"] for report in opportunity_records],
            "opportunity_reports": opportunity_records,
        }

    def build_markdown(self, payload: dict[str, Any]) -> str:
        lines = [
            "# CARE Refactoring Report",
            "",
            f"Project: `{payload['project']}`",
            f"Pipeline success: `{payload['success']}`",
            "",
            "## Summary",
            "",
        ]
        for key, value in payload["summary"].items():
            lines.append(f"- {key.replace('_', ' ').title()}: {value}")

        lines.extend(["", "## Opportunities", ""])
        for report in payload["opportunity_reports"]:
            opportunity = report["opportunity"]
            location = opportunity["location"]
            lines.extend(
                [
                    f"### Opportunity {report['number']:03d}: {opportunity['kind']}",
                    "",
                    f"- ID: `{opportunity['id']}`",
                    f"- Function: `{opportunity['function']}`",
                    f"- Location: `{location['file']}:{location['line']}`",
                    f"- Severity: `{opportunity['severity']}`",
                    f"- Description: {opportunity['description']}",
                    f"- Explanation: {report['explanation']}",
                    f"- Before/After: {report['before_after_summary']['summary']}",
                    f"- Security Impact: {report['security_impact_summary']}",
                    f"- Performance Impact: {report['performance_impact_estimate']}",
                    "",
                ]
            )
            if report["selected_refactorings"]:
                lines.append("Selected patches:")
                for patch in report["selected_refactorings"]:
                    lines.append(f"- `{patch['selected_patch_path']}`")
                lines.append("")
            if report["failed_candidates"]:
                lines.append("Failed candidates:")
                for failed in report["failed_candidates"]:
                    reason = failed.get("failure_reason", "validation failed")
                    candidate = failed.get("candidate_id", "unavailable")
                    lines.append(f"- `{candidate}`: {reason}")
                lines.append("")

            validation = report.get("validation")
            if validation:
                lines.append("Validation:")
                lines.append(f"- Passed: `{validation['passed']}`")
                for log in validation.get("logs", [])[:8]:
                    lines.append(f"- {log}")
                lines.append("")

            traceability = report["traceability"]
            lines.append("Traceability:")
            lines.append(f"- Opportunity: `{traceability['opportunity_id']}`")
            if traceability["plan_opportunity_id"]:
                lines.append(f"- Plan: `{traceability['plan_opportunity_id']}`")
            for path in traceability["selected_patch_paths"]:
                lines.append(f"- Patch: `{path}`")
            lines.append("")

        lines.extend(["## Failed Candidates", ""])
        failed_candidates = payload["failed_candidates"]
        if not failed_candidates:
            lines.append("No failed candidates recorded.")
        else:
            for failed in failed_candidates:
                lines.append(
                    f"- `{failed.get('candidate_id', 'unavailable')}` for "
                    f"`{failed.get('opportunity_id', 'unknown')}`: {failed.get('failure_reason', 'validation failed')}"
                )

        return "\n".join(lines).rstrip() + "\n"

    def _write_patch_artifacts(
        self,
        patches: list[PatchCandidate],
        opportunity_numbers: dict[str, int],
        selected_patch_ids: set[str],
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        candidate_counts: dict[str, int] = {}

        for patch in patches:
            opportunity_number = opportunity_numbers.get(
                patch.opportunity_id,
                len(opportunity_numbers) + 1,
            )
            candidate_counts[patch.opportunity_id] = (
                candidate_counts.get(patch.opportunity_id, 0) + 1
            )
            candidate_number = candidate_counts[patch.opportunity_id]
            candidate_name = (
                f"opportunity-{opportunity_number:03d}-"
                f"candidate-{candidate_number:03d}.diff"
            )
            selected_name = f"opportunity-{opportunity_number:03d}-selected.diff"
            candidate_path = self.patch_dir / candidate_name
            selected_path = self.patch_dir / selected_name
            candidate_path.write_text(patch.diff, encoding="utf-8")

            selected = patch.id in selected_patch_ids
            if selected:
                selected_path.write_text(patch.diff, encoding="utf-8")

            records.append(
                {
                    "opportunity_id": patch.opportunity_id,
                    "candidate_id": patch.id,
                    "candidate_number": candidate_number,
                    "candidate_patch_path": _relative_path(candidate_path, self.output_dir),
                    "selected": selected,
                    "selected_patch_path": (
                        _relative_path(selected_path, self.output_dir) if selected else None
                    ),
                    "explanation": patch.explanation,
                    "confidence": patch.confidence,
                    "diff_summary": _diff_summary(patch.diff),
                }
            )
        return records


def _explanation(
    opportunity: RefactoringOpportunity,
    plan: Optional[RefactoringPlan],
    selected: list[dict[str, Any]],
) -> str:
    if selected:
        patch_explanation = selected[0].get("explanation") or "Selected candidate patch."
    else:
        patch_explanation = "No patch selected for this opportunity."
    if plan:
        return f"{plan.intent} {patch_explanation}"
    return f"{opportunity.description} {patch_explanation}"


def _before_after_summary(selected: list[dict[str, Any]]) -> dict[str, Any]:
    if not selected:
        return {
            "summary": "No selected patch; before/after comparison unavailable.",
            "files_changed": [],
            "lines_added": 0,
            "lines_removed": 0,
        }
    summary = selected[0]["diff_summary"]
    return {
        "summary": (
            f"{len(summary['files_changed'])} file(s) changed, "
            f"{summary['lines_added']} added line(s), {summary['lines_removed']} removed line(s)."
        ),
        **summary,
    }


def _failed_candidates(
    opportunity: RefactoringOpportunity,
    related_patches: list[dict[str, Any]],
    validation: Optional[ValidationResult],
) -> list[dict[str, Any]]:
    if validation is None or validation.passed:
        return []
    if related_patches:
        return [
            {
                "opportunity_id": opportunity.id,
                "candidate_id": patch["candidate_id"],
                "candidate_patch_path": patch["candidate_patch_path"],
                "failure_reason": _failure_reason(validation),
                "validation": validation.to_dict(),
            }
            for patch in related_patches
            if not patch["selected"]
        ] or [
            {
                "opportunity_id": opportunity.id,
                "candidate_id": related_patches[-1]["candidate_id"],
                "candidate_patch_path": related_patches[-1]["candidate_patch_path"],
                "failure_reason": _failure_reason(validation),
                "validation": validation.to_dict(),
            }
        ]
    return [
        {
            "opportunity_id": opportunity.id,
            "candidate_id": None,
            "candidate_patch_path": None,
            "failure_reason": _failure_reason(validation),
            "validation": validation.to_dict(),
        }
    ]


def _global_failed_candidates(
    result: PipelineResult,
    patch_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for index, validation in enumerate(result.validations):
        if validation.passed:
            continue
        opportunity_id = (
            result.opportunities[index].id
            if index < len(result.opportunities)
            else None
        )
        related = [
            patch for patch in patch_records if patch["opportunity_id"] == opportunity_id
        ]
        if related:
            for patch in related:
                if not patch["selected"]:
                    failures.append(
                        {
                            "opportunity_id": opportunity_id,
                            "candidate_id": patch["candidate_id"],
                            "candidate_patch_path": patch["candidate_patch_path"],
                            "failure_reason": _failure_reason(validation),
                        }
                    )
        else:
            failures.append(
                {
                    "opportunity_id": opportunity_id,
                    "candidate_id": None,
                    "candidate_patch_path": None,
                    "failure_reason": _failure_reason(validation),
                }
            )
    return failures


def _security_impact_summary(
    opportunity: RefactoringOpportunity,
    plan: Optional[RefactoringPlan],
    validation: Optional[ValidationResult],
) -> str:
    constraints = plan.security_constraints if plan else []
    if validation and not validation.passed:
        return f"Potential security impact unresolved; validation failed: {_failure_reason(validation)}"
    if opportunity.kind in {"resource_imbalance", "resource_early_return"} or "resource" in opportunity.kind:
        return "Expected to improve resource lifecycle safety while preserving cleanup order."
    if opportunity.kind == "duplicate_check":
        if opportunity.evidence.get("safe_to_remove"):
            return "Duplicate validation may be simplified; report preserves check-removal safety evidence."
        return "Validation duplication is not proven safe to remove; security semantics should remain unchanged."
    if opportunity.kind == "security_smell":
        return "Expected to improve auditability of security-sensitive behavior without weakening checks."
    if constraints:
        return f"Security constraints preserved: {constraints[0]}"
    return "No direct security impact identified beyond preserving existing invariants."


def _performance_impact_estimate(
    plan: Optional[RefactoringPlan],
    selected: list[dict[str, Any]],
) -> str:
    if not selected:
        return "Unknown; no selected patch."
    diff_summary = selected[0]["diff_summary"]
    strategy = plan.patch_strategy.lower() if plan else ""
    if "helper" in strategy or "abstraction" in strategy:
        return "Low to moderate; helper extraction may add a small call overhead but can reduce duplication."
    if diff_summary["lines_added"] + diff_summary["lines_removed"] <= 8:
        return "Negligible; patch is a small local rewrite."
    return "Low; patch size is modest and no expensive operations are indicated."


def _failure_reason(validation: ValidationResult) -> str:
    if validation.counterexamples:
        return "; ".join(validation.counterexamples[:3])
    if validation.logs:
        return "; ".join(validation.logs[:3])
    return "validation failed"


def _diff_summary(diff: str) -> dict[str, Any]:
    files_changed: list[str] = []
    lines_added = 0
    lines_removed = 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            files_changed.append(line[4:].strip())
        elif line.startswith("+") and not line.startswith("+++"):
            lines_added += 1
        elif line.startswith("-") and not line.startswith("---"):
            lines_removed += 1
    return {
        "files_changed": sorted(set(files_changed)),
        "lines_added": lines_added,
        "lines_removed": lines_removed,
        "has_diff": bool(diff.strip()),
    }


def _relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
