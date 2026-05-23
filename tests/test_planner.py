from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from care.analysis.ast_parser import ASTParser
from care.analysis.context_graph import ContextGraphBuilder
from care.core.project import ProjectLoader
from care.detection.detectors import OpportunityDetector
from care.llm.planner import RefactoringPlanner
from care.llm.prompt_builder import PromptBuilder

PLANNER_SAMPLE = """#include <stdlib.h>

int parse(char *p, int len) {
    if (p == NULL) return -1;
    if (!p) return -1;
    if (len < 0) return -1;
    if (len < 0) return -1;
    return 0;
}
"""


class PlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="care-planner-test-"))
        (self.tmpdir / "parse.c").write_text(PLANNER_SAMPLE, encoding="utf-8")
        self.project = ProjectLoader(
            self.tmpdir,
            build_cmd="make",
            test_cmd="make test",
        ).load()
        self.functions = ASTParser().parse_project(self.project)
        self.context_graph = ContextGraphBuilder().build(self.project, self.functions)
        self.context = self.context_graph.to_project_context()
        self.opportunity = next(
            item
            for item in OpportunityDetector().detect(self.project, self.functions, self.context_graph)
            if item.kind == "duplicate_check" and item.evidence["variable"] == "p"
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir)

    def test_prompt_builder_contains_required_planning_contract(self) -> None:
        prompt = PromptBuilder().build_planning_prompt(
            context=self.context,
            opportunity=self.opportunity,
            build_cmd="make",
            test_cmd="make test",
            existing_tests=["tests/test_parse.c"],
        )

        self.assertIn("You are a security-aware automated refactoring planner.", prompt)
        self.assertIn("Your task is to refactor code without changing intended behavior.", prompt)
        self.assertIn("Functional equivalence for normal inputs.", prompt)
        self.assertIn("Error-handling behavior.", prompt)
        self.assertIn("Resource lifecycle correctness.", prompt)
        self.assertIn("Security checks and validation semantics.", prompt)
        self.assertIn("API contracts and return-value conventions.", prompt)
        self.assertIn("Logging and externally visible side effects", prompt)
        self.assertIn("- Refactoring opportunity", prompt)
        self.assertIn("- Relevant source code", prompt)
        self.assertIn("- Context graph summary", prompt)
        self.assertIn("- Data-flow summary", prompt)
        self.assertIn("- Resource events", prompt)
        self.assertIn("- Existing tests", prompt)
        self.assertIn("- Build/test commands", prompt)
        self.assertIn("- Patch intent", prompt)
        self.assertIn("- Refactoring strategy", prompt)
        self.assertIn("- Required invariants", prompt)
        self.assertIn("- Safety constraints", prompt)
        self.assertIn("- Candidate patch plan", prompt)
        self.assertIn("- Validation plan", prompt)

        payload = json.loads(prompt[prompt.index("{") :])
        self.assertEqual(payload["build_command"], "make")
        self.assertEqual(payload["test_command"], "make test")
        self.assertIn("source", payload["relevant_source_code"])
        self.assertEqual(payload["existing_tests"], ["tests/test_parse.c"])

    def test_planner_returns_machine_readable_refactoring_plan(self) -> None:
        planner = RefactoringPlanner(build_cmd="make", test_cmd="make test")
        plan = planner.plan(self.context, self.opportunity)
        payload = json.loads(plan.to_json())

        self.assertEqual(payload["opportunity_id"], self.opportunity.id)
        self.assertIn(str((self.tmpdir / "parse.c").resolve()), payload["affected_files"])
        self.assertEqual(payload["affected_functions"], ["parse"])
        self.assertIn("duplicate validation check", payload["intent"])
        self.assertTrue(payload["required_invariants"])
        self.assertTrue(payload["security_constraints"])
        self.assertTrue(payload["behavior_preservation_goals"])
        self.assertIn("Remove or merge", payload["patch_strategy"])
        self.assertIn("Run build command: make", payload["validation_strategy"])
        self.assertIn("Run test command: make test", payload["validation_strategy"])
        self.assertIsNotNone(planner.last_prompt)
