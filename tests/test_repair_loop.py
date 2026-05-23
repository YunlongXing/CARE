from __future__ import annotations

import unittest

from care.core.models import (
    CodeLocation,
    PatchCandidate,
    ProjectContext,
    RefactoringOpportunity,
    RefactoringPlan,
    ValidationResult,
)
from care.refinement.repair_loop import RepairLoop


class FakePlanner:
    def __init__(self) -> None:
        self.calls = 0

    def plan(self, context, opportunity) -> RefactoringPlan:
        self.calls += 1
        return RefactoringPlan(
            opportunity_id=opportunity.id,
            intent="fix",
            affected_files=[opportunity.file],
            affected_functions=[opportunity.function],
            required_invariants=["preserve behavior"],
            security_constraints=["no unrelated changes"],
            behavior_preservation_goals=["same returns"],
            patch_strategy="small patch",
            validation_strategy=["validate"],
        )


class FakeGenerator:
    def __init__(self) -> None:
        self.calls = []

    def generate_candidates(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return [_candidate("bad-1"), _candidate("bad-2")]
        return [_candidate("good")]


class FakeValidator:
    def __init__(self) -> None:
        self.calls = []

    def validate(self, project, candidate):
        self.calls.append(candidate)
        if candidate.id == "good":
            return ValidationResult(
                passed=True,
                stage_results={"compile": {"passed": True}},
                logs=["ok"],
                counterexamples=[],
            )
        return ValidationResult(
            passed=False,
            stage_results={"compile": {"passed": False}},
            logs=[f"{candidate.id} failed"],
            counterexamples=[f"{candidate.id} counterexample"],
        )


class RepairLoopTests(unittest.TestCase):
    def test_repair_loop_feeds_back_failures_and_selects_passing_candidate(self) -> None:
        planner = FakePlanner()
        generator = FakeGenerator()
        validator = FakeValidator()
        opportunity = RefactoringOpportunity(
            id="opp-1",
            kind="duplicate_check",
            file="main.c",
            function="f",
            location=CodeLocation(file="main.c", line=2, column=1),
            severity="low",
            description="duplicate check",
            evidence={"confidence": 0.8},
            context_summary="context",
        )
        loop = RepairLoop(
            patch_generator=generator,
            validator=validator,
            max_iterations=2,
            planner=planner,
            project=object(),
            context=ProjectContext(root="."),
            candidates_per_iteration=2,
        )

        candidate, validation = loop.run(opportunity)

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.id, "good")
        self.assertTrue(validation.passed)
        self.assertEqual(planner.calls, 1)
        self.assertEqual(len(generator.calls), 2)
        feedback = generator.calls[1]["validation_feedback"]
        self.assertIn("The previous patch failed validation.", feedback.messages[0])
        self.assertIn("bad-1 failed", feedback.messages[0])
        self.assertIn("bad-2 counterexample", feedback.messages[0])

    def test_repair_loop_returns_none_when_all_candidates_fail(self) -> None:
        class AlwaysBadGenerator:
            def generate_candidates(self, **kwargs):
                return [_candidate("bad")]

        class AlwaysBadValidator:
            def validate(self, project, candidate):
                return ValidationResult(
                    passed=False,
                    stage_results={},
                    logs=["failed"],
                    counterexamples=["counterexample"],
                )

        loop = RepairLoop(
            patch_generator=AlwaysBadGenerator(),
            validator=AlwaysBadValidator(),
            max_iterations=1,
            project=object(),
            candidates_per_iteration=1,
        )

        candidate, validation = loop.run(
            RefactoringPlan(
                opportunity_id="opp",
                intent="intent",
                affected_files=["main.c"],
                affected_functions=["f"],
                required_invariants=[],
                security_constraints=[],
                behavior_preservation_goals=[],
                patch_strategy="strategy",
                validation_strategy=[],
            )
        )

        self.assertIsNone(candidate)
        self.assertFalse(validation.passed)
        self.assertIn("counterexample", "\n".join(validation.counterexamples))


def _candidate(identifier: str) -> PatchCandidate:
    return PatchCandidate(
        id=identifier,
        opportunity_id="opp-1",
        diff="",
        explanation="test",
        confidence=0.5,
    )
