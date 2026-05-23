from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from care.core.models import (
    CodeLocation,
    PatchCandidate,
    PipelineResult,
    RefactoringOpportunity,
    RefactoringPlan,
    ValidationResult,
)
from care.reporting.reporter import ConsoleReporter


class ReporterTests(unittest.TestCase):
    def test_reporter_writes_json_markdown_and_patch_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary)
            result = PipelineResult(
                success=False,
                project="/tmp/example-project",
                report="CARE run summary",
                opportunities=[
                    _opportunity(
                        identifier="opp-resource",
                        kind="resource_imbalance",
                        description="malloc can bypass cleanup on an error path.",
                    ),
                    _opportunity(
                        identifier="opp-duplicate",
                        kind="duplicate_check",
                        description="duplicate null check can be reviewed.",
                    ),
                ],
                plans=[
                    _plan("opp-resource", "Normalize cleanup path."),
                    _plan("opp-duplicate", "Review duplicated validation."),
                ],
                patches=[_patch("candidate-resource", "opp-resource")],
                validations=[
                    ValidationResult(
                        passed=True,
                        stage_results={"compile": {"passed": True}},
                        logs=["compile passed", "tests passed"],
                        counterexamples=[],
                    ),
                    ValidationResult(
                        passed=False,
                        stage_results={"semantic": {"passed": False}},
                        logs=["semantic checker found changed return behavior"],
                        counterexamples=["return form changed on error path"],
                    ),
                ],
            )

            with contextlib.redirect_stdout(io.StringIO()):
                ConsoleReporter(output_dir=output_dir).emit(result)

            report_json = output_dir / "care-report.json"
            report_md = output_dir / "care-report.md"
            candidate_diff = output_dir / "patches" / "opportunity-001-candidate-001.diff"
            selected_diff = output_dir / "patches" / "opportunity-001-selected.diff"

            self.assertTrue(report_json.exists())
            self.assertTrue(report_md.exists())
            self.assertTrue(candidate_diff.exists())
            self.assertTrue(selected_diff.exists())
            self.assertEqual(
                candidate_diff.read_text(encoding="utf-8"),
                selected_diff.read_text(encoding="utf-8"),
            )

            payload = json.loads(report_json.read_text(encoding="utf-8"))
            for key in [
                "detected_opportunities",
                "selected_refactorings",
                "validation_results",
                "failed_candidates",
                "before_after_summary",
                "security_impact_summary",
                "performance_impact_estimate",
                "traceability",
            ]:
                self.assertIn(key, payload)

            self.assertEqual(payload["summary"]["opportunities"], 2)
            self.assertEqual(payload["summary"]["selected_refactorings"], 1)
            self.assertEqual(
                payload["selected_refactorings"][0]["selected_patch_path"],
                "patches/opportunity-001-selected.diff",
            )
            self.assertEqual(payload["failed_candidates"][0]["opportunity_id"], "opp-duplicate")
            self.assertIn("return form changed", payload["failed_candidates"][0]["failure_reason"])

            markdown = report_md.read_text(encoding="utf-8")
            self.assertIn("# CARE Refactoring Report", markdown)
            self.assertIn("Security Impact", markdown)
            self.assertIn("Failed Candidates", markdown)
            self.assertIn("resource_imbalance", markdown)

    def test_selected_patch_traceability_is_opportunity_based(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary)
            result = PipelineResult(
                success=False,
                project="/tmp/example-project",
                opportunities=[
                    _opportunity("opp-failed", "dead_code", "unreachable label"),
                    _opportunity("opp-passed", "security_smell", "unchecked return"),
                ],
                plans=[
                    _plan("opp-failed", "Remove unreachable code."),
                    _plan("opp-passed", "Preserve return checks."),
                ],
                patches=[
                    _patch("candidate-failed", "opp-failed"),
                    _patch("candidate-passed", "opp-passed"),
                ],
                validations=[
                    ValidationResult(
                        passed=False,
                        stage_results={},
                        logs=["all generated candidates failed"],
                        counterexamples=[],
                    ),
                    ValidationResult(
                        passed=True,
                        stage_results={"compile": {"passed": True}},
                        logs=["ok"],
                        counterexamples=[],
                    ),
                ],
            )

            with contextlib.redirect_stdout(io.StringIO()):
                ConsoleReporter(output_dir=output_dir).emit(result)

            payload = json.loads(
                (output_dir / "care-report.json").read_text(encoding="utf-8")
            )
            selected = payload["selected_refactorings"]
            self.assertEqual(len(selected), 1)
            self.assertEqual(selected[0]["opportunity_id"], "opp-passed")
            self.assertEqual(
                selected[0]["selected_patch_path"],
                "patches/opportunity-002-selected.diff",
            )
            self.assertFalse((output_dir / "patches" / "opportunity-001-selected.diff").exists())
            self.assertTrue((output_dir / "patches" / "opportunity-002-selected.diff").exists())


def _opportunity(identifier: str, kind: str, description: str) -> RefactoringOpportunity:
    return RefactoringOpportunity(
        id=identifier,
        kind=kind,
        file="src/main.c",
        function="handle",
        location=CodeLocation(file="src/main.c", line=12, column=3),
        severity="medium",
        description=description,
        evidence={"confidence": 0.8, "safe_to_remove": False},
        context_summary="single function context",
    )


def _plan(opportunity_id: str, intent: str) -> RefactoringPlan:
    return RefactoringPlan(
        opportunity_id=opportunity_id,
        intent=intent,
        affected_files=["src/main.c"],
        affected_functions=["handle"],
        required_invariants=["preserve return values"],
        security_constraints=["preserve cleanup on error paths"],
        behavior_preservation_goals=["same externally visible behavior"],
        patch_strategy="small local rewrite",
        validation_strategy=["compile", "test", "security regression"],
    )


def _patch(identifier: str, opportunity_id: str) -> PatchCandidate:
    return PatchCandidate(
        id=identifier,
        opportunity_id=opportunity_id,
        diff=(
            "diff --git a/src/main.c b/src/main.c\n"
            "--- a/src/main.c\n"
            "+++ b/src/main.c\n"
            "@@ -1,3 +1,4 @@\n"
            " int handle(void) {\n"
            "+  cleanup();\n"
            "   return 0;\n"
            " }\n"
        ),
        explanation="Route error exits through the existing cleanup path.",
        confidence=0.86,
    )
