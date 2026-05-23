from __future__ import annotations

import json
import unittest

from care.core.models import (
    BasicBlockInfo,
    CodeLocation,
    EdgeInfo,
    FunctionInfo,
    PatchCandidate,
    RefactoringOpportunity,
    RefactoringReport,
    ResourceEvent,
    StageResult,
    ValidationResult,
)


class CoreModelSerializationTests(unittest.TestCase):
    def test_requested_models_serialize_to_json(self) -> None:
        location = CodeLocation(file="src/main.c", line=12, column=5)
        opportunity = RefactoringOpportunity(
            id="opp-1",
            kind="goto",
            file="src/main.c",
            function="parse",
            location=location,
            severity="medium",
            description="Replace goto cleanup with structured control flow.",
            evidence={"label": "cleanup"},
            context_summary="Single goto jumps to shared cleanup block.",
        )
        patch = PatchCandidate(
            id="patch-1",
            opportunity_id="opp-1",
            diff="",
            explanation="No patch generated in the skeleton yet.",
            confidence=0.0,
        )
        validation = ValidationResult(
            passed=True,
            stage_results={"build": True},
            logs=["build: passed"],
            counterexamples=[],
        )
        report = RefactoringReport(
            project="/tmp/project",
            opportunities=[opportunity],
            selected_patches=[patch],
            validation_results=[validation],
        )

        models = [
            location,
            FunctionInfo(
                name="parse",
                file="src/main.c",
                start_line=1,
                end_line=20,
                signature="int parse(void)",
                body="{ return 0; }",
            ),
            BasicBlockInfo(id="bb-1", function="parse", statements=["return 0;"]),
            EdgeInfo(src="bb-1", dst="bb-2", edge_type="control"),
            ResourceEvent(kind="open", variable="fd", location=location),
            opportunity,
            patch,
            StageResult(name="build", passed=True, logs=["ok"]),
            validation,
            report,
        ]

        for model in models:
            payload = json.loads(model.to_json())
            self.assertIsInstance(payload, dict)
