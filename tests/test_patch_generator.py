from __future__ import annotations

import unittest

from care.core.models import RefactoringPlan, ValidationResult
from care.llm.patch_generator import PatchGenerator

VALID_DIFF = """--- a/src/example.c
+++ b/src/example.c
@@ -1,4 +1,3 @@
 int f(int x) {
-    if (x == 0) return -1;
     return x;
 }
"""


class FakeLlmClient:
    def __init__(self, output: str) -> None:
        self.output = output
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.output


def make_plan() -> RefactoringPlan:
    return RefactoringPlan(
        opportunity_id="opp-1",
        intent="Remove duplicate null check.",
        affected_files=["src/example.c"],
        affected_functions=["f"],
        required_invariants=["Preserve return values."],
        security_constraints=["Do not remove non-redundant checks."],
        behavior_preservation_goals=["Functional equivalence for normal inputs."],
        patch_strategy="Remove only the later duplicate check.",
        validation_strategy=["Run tests."],
    )


class PatchGeneratorTests(unittest.TestCase):
    def test_build_prompt_contains_required_rules_and_mode(self) -> None:
        prompt = PatchGenerator().build_prompt(
            source_snippet="if (!p) return -1;",
            full_function_body="int f(char *p) { if (!p) return -1; return 0; }",
            plan=make_plan(),
            context_summary="f has duplicate checks",
            validation_feedback=["previous compile failed"],
            mode="aggressive",
            candidate_index=2,
        )

        self.assertIn("You are a security-aware C/C++ refactoring engine.", prompt)
        self.assertIn("Generate a minimal unified diff.", prompt)
        self.assertIn("- Preserve program behavior.", prompt)
        self.assertIn("- Preserve return values and error codes.", prompt)
        self.assertIn("- Preserve resource release order unless intentionally improved.", prompt)
        self.assertIn("- Do not remove security checks unless proven redundant.", prompt)
        self.assertIn("- Do not introduce new allocations unless necessary.", prompt)
        self.assertIn("- Do not introduce new global state.", prompt)
        self.assertIn("- Do not silently swallow errors.", prompt)
        self.assertIn("- Keep the patch small.", prompt)
        self.assertIn("Mode: aggressive", prompt)
        self.assertIn("mode=conservative:", prompt)
        self.assertIn("small local rewrite only", prompt)
        self.assertIn("mode=aggressive:", prompt)
        self.assertIn("may introduce helper function or structured cleanup abstraction", prompt)
        self.assertIn("Output only a valid unified diff.", prompt)
        self.assertIn("previous compile failed", prompt)

    def test_generate_candidates_calls_llm_n_times_and_returns_diffs(self) -> None:
        client = FakeLlmClient(f"```diff\n{VALID_DIFF}\n```")
        generator = PatchGenerator(client=client)

        candidates = generator.generate_candidates(
            source_snippet="if (x == 0) return -1;",
            full_function_body="int f(int x) { if (x == 0) return -1; return x; }",
            plan=make_plan(),
            context_summary="duplicate check is safe to remove",
            n=3,
        )

        self.assertEqual(len(candidates), 3)
        self.assertEqual(len(client.prompts), 3)
        self.assertEqual(len(generator.last_prompts), 3)
        for candidate in candidates:
            self.assertEqual(candidate.opportunity_id, "opp-1")
            self.assertTrue(candidate.diff.startswith("--- a/src/example.c"))
            self.assertIn("@@ -1,4 +1,3 @@", candidate.diff)
            self.assertTrue(candidate.diff.endswith("\n"))
            self.assertGreater(candidate.confidence, 0.7)

    def test_generate_uses_validation_feedback_and_mode(self) -> None:
        client = FakeLlmClient(VALID_DIFF)
        generator = PatchGenerator(client=client, default_mode="aggressive")
        feedback = ValidationResult(
            passed=False,
            stage_results={"build": False},
            logs=["build failed"],
            counterexamples=["missing include"],
        )

        candidate = generator.generate(
            plan=make_plan(),
            iteration=4,
            source_snippet="snippet",
            full_function_body="body",
            context_summary="summary",
            validation_feedback=feedback,
        )

        self.assertIn(":4:", candidate.id)
        self.assertIn("Mode: aggressive", client.prompts[0])
        self.assertIn("build failed", client.prompts[0])
        self.assertIn("missing include", client.prompts[0])
        self.assertGreater(candidate.confidence, 0.6)

    def test_generate_recounts_bad_hunk_line_counts(self) -> None:
        bad_counts = VALID_DIFF.replace("@@ -1,4 +1,3 @@", "@@ -1,99 +1,42 @@")
        generator = PatchGenerator(client=FakeLlmClient(bad_counts))

        candidate = generator.generate_candidates(
            source_snippet="snippet",
            full_function_body="body",
            plan=make_plan(),
            context_summary="summary",
            n=1,
        )[0]

        self.assertIn("@@ -1,4 +1,3 @@", candidate.diff)
        self.assertGreater(candidate.confidence, 0.7)

    def test_invalid_llm_output_is_discarded(self) -> None:
        generator = PatchGenerator(client=FakeLlmClient("not a diff"))

        candidate = generator.generate_candidates(
            source_snippet="snippet",
            full_function_body="body",
            plan=make_plan(),
            context_summary="summary",
            n=1,
        )[0]

        self.assertEqual(candidate.diff, "")
        self.assertLess(candidate.confidence, 0.1)

    def test_json_edit_plan_is_converted_to_unified_diff(self) -> None:
        class FakeProject:
            root = "."
            source_files = []

            def read_file(self, path: str) -> str:
                self.path = path
                return "int f(int x) {\n    return x;\n}\n"

        edit_plan = {
            "edits": [
                {
                    "file": "src/example.c",
                    "operation": "replace",
                    "old": "    return x;",
                    "text": "    return x + 1;",
                }
            ]
        }
        generator = PatchGenerator(client=FakeLlmClient(json_dumps(edit_plan)))

        candidate = generator.generate_candidates(
            source_snippet="snippet",
            full_function_body="body",
            plan=make_plan(),
            context_summary="summary",
            n=1,
            project=FakeProject(),
        )[0]

        self.assertTrue(candidate.diff.startswith("--- a/src/example.c"))
        self.assertIn("-    return x;", candidate.diff)
        self.assertIn("+    return x + 1;", candidate.diff)
        self.assertIn("JSON edit plan", candidate.explanation)

    def test_placeholder_hunk_header_is_discarded(self) -> None:
        placeholder_diff = """--- a/src/example.c
+++ b/src/example.c
@@ ... @@
-    return 0;
+    return 1;
"""
        generator = PatchGenerator(client=FakeLlmClient(placeholder_diff))

        candidate = generator.generate_candidates(
            source_snippet="snippet",
            full_function_body="body",
            plan=make_plan(),
            context_summary="summary",
            n=1,
        )[0]

        self.assertEqual(candidate.diff, "")
        self.assertLess(candidate.confidence, 0.1)

    def test_no_client_returns_noop_candidates(self) -> None:
        candidates = PatchGenerator().generate_candidates(
            source_snippet="snippet",
            full_function_body="body",
            plan=make_plan(),
            context_summary="summary",
            n=2,
        )

        self.assertEqual(len(candidates), 2)
        self.assertTrue(all(candidate.diff == "" for candidate in candidates))
        self.assertTrue(all(candidate.confidence == 0.0 for candidate in candidates))

    def test_invalid_mode_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            PatchGenerator().generate_candidates(
                source_snippet="snippet",
                full_function_body="body",
                plan=make_plan(),
                context_summary="summary",
                mode="wild",
            )


def json_dumps(value: object) -> str:
    import json

    return json.dumps(value)
