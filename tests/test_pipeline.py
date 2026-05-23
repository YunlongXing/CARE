from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from care.config import CareConfig
from care.core.models import (
    CodeLocation,
    FunctionInfo,
    PatchCandidate,
    ProjectContext,
    RefactoringOpportunity,
    RefactoringPlan,
    ValidationResult,
)
from care.core.pipeline import CAREPipeline, CarePipeline


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeProjectLoader.calls = []
        FakePlanner.instances = []
        FakePatchGenerator.instances = []
        FakeRepairLoop.instances = []

    def test_pipeline_limits_opportunities_and_passes_repair_config(self) -> None:
        with _patched_pipeline_dependencies():
            result = CAREPipeline(
                CareConfig(
                    project_path=Path("/tmp/example"),
                    build_cmd="make",
                    test_cmd="make test",
                    max_opportunities=2,
                    max_iterations=4,
                    candidates_per_iteration=1,
                    mode="aggressive",
                )
            ).run()

        self.assertTrue(result.success)
        self.assertEqual(
            [opportunity.id for opportunity in result.opportunities],
            ["opp-1", "opp-2"],
        )
        self.assertEqual(
            [plan.opportunity_id for plan in result.plans],
            ["opp-1", "opp-2"],
        )
        self.assertEqual(
            [patch.opportunity_id for patch in result.patches],
            ["opp-1", "opp-2"],
        )
        self.assertEqual(len(result.validations), 2)

        self.assertEqual(FakeProjectLoader.calls[0]["build_cmd"], "make")
        self.assertEqual(FakeProjectLoader.calls[0]["test_cmd"], "make test")
        self.assertEqual(FakePlanner.instances[0].build_cmd, "make")
        self.assertEqual(FakePlanner.instances[0].test_cmd, "make test")
        self.assertEqual(FakePatchGenerator.instances[0].default_mode, "aggressive")
        self.assertEqual(FakeRepairLoop.instances[0].max_iterations, 4)
        self.assertEqual(FakeRepairLoop.instances[0].candidates_per_iteration, 1)
        self.assertEqual(FakeRepairLoop.instances[0].mode, "aggressive")
        self.assertEqual(
            [call["plan"].opportunity_id for call in FakeRepairLoop.instances[0].calls],
            ["opp-1", "opp-2"],
        )

    def test_pipeline_supports_run_config_style_and_legacy_alias(self) -> None:
        with _patched_pipeline_dependencies():
            result = CAREPipeline().run(
                CareConfig(
                    project_path=Path("/tmp/example"),
                    max_opportunities=1,
                    dry_run=True,
                )
            )

        self.assertIs(CarePipeline, CAREPipeline)
        self.assertTrue(result.success)
        self.assertEqual(len(result.opportunities), 1)
        self.assertEqual(len(result.plans), 1)
        self.assertEqual(result.patches, [])
        self.assertEqual(result.validations, [])


def _patched_pipeline_dependencies():
    return patch.multiple(
        "care.core.pipeline",
        ProjectLoader=FakeProjectLoader,
        ASTParser=FakeASTParser,
        ContextGraphBuilder=FakeContextGraphBuilder,
        OpportunityDetector=FakeOpportunityDetector,
        RefactoringPlanner=FakePlanner,
        PatchGenerator=FakePatchGenerator,
        RefactoringValidator=FakeValidator,
        RepairLoop=FakeRepairLoop,
    )


class FakeProject:
    pass


class FakeProjectLoader:
    calls: list[dict] = []

    def load(self, **kwargs):
        self.calls.append(kwargs)
        return FakeProject()


class FakeASTParser:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def parse_project(self, project):
        return [
            FunctionInfo(
                name="handle",
                file="src/main.c",
                start_line=1,
                end_line=4,
                signature="int handle(void)",
                body="{ return 0; }",
            )
        ]


class FakeContextGraph:
    def to_project_context(self):
        return ProjectContext(
            root="/tmp/example",
            functions=[
                FunctionInfo(
                    name="handle",
                    file="src/main.c",
                    start_line=1,
                    end_line=4,
                    signature="int handle(void)",
                    body="{ return 0; }",
                )
            ],
        )


class FakeContextGraphBuilder:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def build(self, project, functions):
        return FakeContextGraph()


class FakeOpportunityDetector:
    def detect(self, project, functions, context_graph):
        return [
            _opportunity("opp-1", "resource_imbalance"),
            _opportunity("opp-2", "duplicate_check"),
            _opportunity("opp-3", "dead_code"),
        ]


class FakePlanner:
    instances: list["FakePlanner"] = []

    def __init__(self, build_cmd=None, test_cmd=None):
        self.build_cmd = build_cmd
        self.test_cmd = test_cmd
        self.planned: list[str] = []
        self.instances.append(self)

    def plan(self, context, opportunity):
        self.planned.append(opportunity.id)
        return RefactoringPlan(
            opportunity_id=opportunity.id,
            intent=f"Plan {opportunity.id}",
            affected_files=[opportunity.file],
            affected_functions=[opportunity.function],
            required_invariants=["preserve behavior"],
            security_constraints=["preserve checks"],
            behavior_preservation_goals=["same behavior"],
            patch_strategy="small local rewrite",
            validation_strategy=["compile"],
        )


class FakePatchGenerator:
    instances: list["FakePatchGenerator"] = []

    def __init__(
        self,
        client=None,
        default_mode="conservative",
        default_candidates=3,
    ):
        self.client = client
        self.default_mode = default_mode
        self.default_candidates = default_candidates
        self.instances.append(self)


class FakeValidator:
    def __init__(self, config, project):
        self.config = config
        self.project = project


class FakeRepairLoop:
    instances: list["FakeRepairLoop"] = []

    def __init__(
        self,
        patch_generator,
        validator,
        max_iterations,
        planner=None,
        project=None,
        context=None,
        candidates_per_iteration=3,
        mode="conservative",
    ):
        self.patch_generator = patch_generator
        self.validator = validator
        self.max_iterations = max_iterations
        self.planner = planner
        self.project = project
        self.context = context
        self.candidates_per_iteration = candidates_per_iteration
        self.mode = mode
        self.calls: list[dict] = []
        self.instances.append(self)

    def run(self, opportunity, plan=None):
        self.calls.append({"opportunity": opportunity, "plan": plan})
        return (
            PatchCandidate(
                id=f"candidate-{opportunity.id}",
                opportunity_id=opportunity.id,
                diff=(
                    "--- a/src/main.c\n"
                    "+++ b/src/main.c\n"
                    "@@ -1 +1 @@\n"
                    "-return 0;\n"
                    "+return 0;\n"
                ),
                explanation="test patch",
                confidence=0.8,
            ),
            ValidationResult(
                passed=True,
                stage_results={"compile": {"passed": True}},
                logs=["ok"],
                counterexamples=[],
            ),
        )


def _opportunity(identifier: str, kind: str) -> RefactoringOpportunity:
    return RefactoringOpportunity(
        id=identifier,
        kind=kind,
        file="src/main.c",
        function="handle",
        location=CodeLocation(file="src/main.c", line=1, column=1),
        severity="medium",
        description=f"{kind} opportunity",
        evidence={"confidence": 0.8},
        context_summary="summary",
    )
