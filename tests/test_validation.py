from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from care.core.models import FunctionInfo, PatchCandidate, StageResult
from care.core.project import ProjectLoader
from care.validation.compiler import CompileChecker
from care.validation.resource_checker import ResourceConsistencyChecker
from care.validation.security_checker import SecurityRegressionChecker
from care.validation.semantic_checker import SemanticEquivalenceChecker
from care.validation.static_analysis import StaticAnalysisRunner
from care.validation.taxonomy import classify_validation_result
from care.validation.tests import TestRunner
from care.validation.validator import RefactoringValidator


class StaticPass:
    def run(self, project) -> StageResult:
        return StageResult(name="static_analysis", passed=True, warnings=["stubbed"])


class ValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="care-validation-test-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir)

    def write(self, relative: str, content: str) -> Path:
        path = self.tmpdir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_compile_and_test_stages_run_project_commands(self) -> None:
        project = ProjectLoader(self.tmpdir, build_cmd="true", test_cmd="true").load()

        compile_result = CompileChecker().run(project)
        test_result = TestRunner().run(project)

        self.assertTrue(compile_result.passed)
        self.assertTrue(test_result.passed)

    def test_static_analysis_records_missing_tools_without_failure(self) -> None:
        project = ProjectLoader(self.tmpdir).load()

        with patch("shutil.which", return_value=None):
            result = StaticAnalysisRunner().run(project)

        self.assertTrue(result.passed)
        self.assertIn("clang-tidy not installed; skipped", result.warnings)
        self.assertIn("cppcheck not installed; skipped", result.warnings)
        self.assertIn("scan-build not installed; skipped", result.warnings)

    def test_resource_checker_reports_double_free_and_missing_free(self) -> None:
        self.write(
            "main.c",
            """#include <stdlib.h>
int f(int x) {
    char *p = malloc(8);
    if (x) return -1;
    free(p);
    free(p);
    return 0;
}
""",
        )
        project = ProjectLoader(self.tmpdir).load()
        result = ResourceConsistencyChecker().run(project, _candidate(""))

        self.assertFalse(result.passed)
        self.assertTrue(any("double free" in item for item in result.counterexamples))
        self.assertTrue(any("early return" in item for item in result.counterexamples))

    def test_semantic_checker_detects_public_signature_change(self) -> None:
        before = [
            FunctionInfo(
                name="f",
                file="main.c",
                start_line=1,
                end_line=3,
                signature="int f(int x)",
                body="{ return x; }",
                return_statements=["return x;"],
                calls=[],
            )
        ]
        after = [
            FunctionInfo(
                name="f",
                file="main.c",
                start_line=1,
                end_line=3,
                signature="long f(int x)",
                body="{ return x; }",
                return_statements=["return x;"],
                calls=[],
            )
        ]

        result = SemanticEquivalenceChecker().run(
            project=object(),
            patch_candidate=_candidate(""),
            before_functions=before,
            after_functions=after,
        )

        self.assertFalse(result.passed)
        self.assertTrue(any("public signature changed" in item for item in result.counterexamples))

    def test_security_checker_detects_removed_null_check(self) -> None:
        before = [
            FunctionInfo(
                name="f",
                file="main.c",
                start_line=1,
                end_line=5,
                signature="int f(char *p)",
                body="{ if (p == NULL) return -1; return 0; }",
                return_statements=["return -1;", "return 0;"],
                calls=[],
            )
        ]
        after = [
            FunctionInfo(
                name="f",
                file="main.c",
                start_line=1,
                end_line=4,
                signature="int f(char *p)",
                body="{ return 0; }",
                return_statements=["return 0;"],
                calls=[],
            )
        ]

        result = SecurityRegressionChecker().run(
            project=object(),
            patch_candidate=_candidate(""),
            before_functions=before,
            after_functions=after,
        )

        self.assertFalse(result.passed)
        self.assertTrue(any("null_checks decreased" in item for item in result.counterexamples))

    def test_refactoring_validator_restores_applies_and_aggregates(self) -> None:
        self.write(
            "main.c",
            """int main(void) {
    return 0;
}
""",
        )
        project = ProjectLoader(self.tmpdir, build_cmd="true", test_cmd="true").load()
        validator = RefactoringValidator(project=project)
        validator.static_analysis = StaticPass()
        patch = _candidate(
            """--- a/main.c
+++ b/main.c
@@ -1,3 +1,4 @@
+/* checked by CARE */
 int main(void) {
     return 0;
 }
"""
        )

        result = validator.validate(project, patch)

        self.assertTrue(result.passed)
        self.assertIn("apply_patch", result.stage_results)
        self.assertIn("compile", result.stage_results)
        self.assertIn("tests", result.stage_results)
        self.assertIn("semantic_equivalence", result.stage_results)
        self.assertIn("security_regression", result.stage_results)
        self.assertEqual(result.stage_results["failure_taxonomy"]["categories"], [])
        self.assertNotIn("/* checked by CARE */", project.read_file("main.c"))

    def test_refactoring_validator_reports_patch_apply_failure(self) -> None:
        self.write("main.c", "int main(void) { return 0; }\n")
        project = ProjectLoader(self.tmpdir).load()
        validator = RefactoringValidator(project=project)
        validator.static_analysis = StaticPass()

        result = validator.validate(project, _candidate("not a diff"))

        self.assertFalse(result.passed)
        self.assertIn("apply_patch", result.stage_results)
        self.assertIn(
            "patch_does_not_apply",
            result.stage_results["failure_taxonomy"]["categories"],
        )
        self.assertTrue(result.counterexamples)

    def test_refactoring_validator_rejects_noop_patch(self) -> None:
        self.write("main.c", "int main(void) { return 0; }\n")
        project = ProjectLoader(self.tmpdir).load()
        validator = RefactoringValidator(project=project)
        validator.static_analysis = StaticPass()
        patch = _candidate(
            """--- a/main.c
+++ b/main.c
@@ -1 +1 @@
-int main(void) { return 0; }
+int main(void) { return 0; }
"""
        )

        result = validator.validate(project, patch)

        self.assertFalse(result.passed)
        self.assertIn("no_effect_patch", result.stage_results["failure_taxonomy"]["categories"])
        self.assertIn("patch produced no project changes", result.counterexamples)

    def test_taxonomy_recurses_into_candidate_failures(self) -> None:
        taxonomy = classify_validation_result(
            {
                "passed": False,
                "stage_results": {
                    "candidate_0": {
                        "passed": False,
                        "stage_results": {
                            "apply_patch": {
                                "passed": False,
                                "logs": ["git apply --check failed"],
                                "counterexamples": ["patch does not apply"],
                            }
                        },
                        "logs": ["candidate failed"],
                        "counterexamples": [],
                    }
                },
                "logs": [],
                "counterexamples": [],
            }
        )

        self.assertIn("patch_does_not_apply", taxonomy["categories"])
        self.assertIn("candidate_0.apply_patch", taxonomy["failed_stages"])


def _candidate(diff: str) -> PatchCandidate:
    return PatchCandidate(
        id="patch-test",
        opportunity_id="opp-test",
        diff=diff,
        explanation="test",
        confidence=1.0,
    )
