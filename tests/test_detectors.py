from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from care.analysis.ast_parser import ASTParser
from care.analysis.context_graph import ContextGraphBuilder
from care.core.project import ProjectLoader
from care.detection.detectors import OpportunityDetector, SEVERITY_RANK
from care.detection.common import evidence_confidence_label, gated_severity
from care.detection.post_filter import apply_post_filters, deployment_contexts

DETECTOR_SAMPLE = """#include <stdio.h>
#include <stdlib.h>

int cleanup_case(int fail1, int fail2) {
    char *p = malloc(16);
    FILE *fp = fopen("x", "r");
    int ret = 0;
    if (!p) {
        ret = -1;
        goto out;
    }
    if (!fp) {
        ret = -1;
        goto out;
    }
    if (fail1) {
        free(p);
        goto out;
    }
    if (fail2) {
        ret = -1;
        goto middle;
    }
middle:
    goto out;
out:
    free(p);
    free(p);
    fclose(fp);
    return ret;
}

int leak_case(int x) {
    char *p = malloc(8);
    if (x) {
        return -1;
    }
    return 0;
}

int duplicate_check(char *p, int len, int ret) {
    if (p == NULL) return -1;
    if (!p) return -1;
    if (len < 0) return -1;
    if (len < 0) return -1;
    if (ret != 0) return ret;
    if (ret != 0) return ret;
    return 0;
}

int dead_case(void) {
    return 1;
    int x = 0;
cleanup:
    x++;
    if (0) return 2;
    return x;
}

int smell_case(char *dst, const char *src) {
    strcpy(dst, src);
    sprintf(dst, "%s", src);
    int x = atoi(src);
    scanf("%s", dst);
    return x;
}

int clustered_returns(int a, int b) {
    char *p = malloc(8);
    if (a) {
        return -1;
    }
    if (b) {
        return -2;
    }
    return 0;
}

int ownership_transfer(void) {
    char *p = malloc(8);
    take_ownership(p);
    return 0;
}
"""


class DetectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="care-detector-test-"))
        (self.tmpdir / "sample.c").write_text(DETECTOR_SAMPLE, encoding="utf-8")
        self.project = ProjectLoader(self.tmpdir).load()
        self.functions = ASTParser().parse_project(self.project)
        self.context_graph = ContextGraphBuilder().build(self.project, self.functions)
        self.opportunities = OpportunityDetector().detect(
            self.project,
            self.functions,
            self.context_graph,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir)

    def test_orchestrator_runs_all_detector_families(self) -> None:
        kinds = {opportunity.kind for opportunity in self.opportunities}

        self.assertIn("goto_redundant_cleanup", kinds)
        self.assertIn("goto_redundant_error_block", kinds)
        self.assertIn("goto_duplicated_error_handling", kinds)
        self.assertIn("goto_chain", kinds)
        self.assertIn("goto_single_use_label", kinds)
        self.assertIn("resource_imbalance", kinds)
        self.assertIn("resource_early_return", kinds)
        self.assertIn("duplicate_check", kinds)
        self.assertIn("dead_code", kinds)
        self.assertIn("security_smell", kinds)

    def test_goto_evidence_contains_required_context(self) -> None:
        opportunity = next(
            item for item in self.opportunities if item.kind == "goto_redundant_cleanup"
        )

        self.assertIn("goto_statement_locations", opportunity.evidence)
        self.assertIn("label_locations", opportunity.evidence)
        self.assertIn("duplicated_statements", opportunity.evidence)
        self.assertIn("involved_resource_variables", opportunity.evidence)
        self.assertIn("affected_control_paths", opportunity.evidence)
        self.assertIn("p", opportunity.evidence["involved_resource_variables"])

    def test_duplicate_check_evidence_reports_safe_removal(self) -> None:
        duplicate_opportunities = [
            item for item in self.opportunities if item.kind == "duplicate_check"
        ]
        by_variable = {
            item.evidence["variable"]: item
            for item in duplicate_opportunities
        }

        self.assertTrue(by_variable["p"].evidence["safe_to_remove"])
        self.assertTrue(by_variable["len"].evidence["safe_to_remove"])
        self.assertEqual(by_variable["ret"].evidence["check_kind"], "status_check")

    def test_resource_and_dead_code_patterns_are_reported(self) -> None:
        patterns = {
            item.evidence.get("pattern")
            for item in self.opportunities
            if item.kind in {"resource_imbalance", "resource_early_return", "dead_code"}
        }

        self.assertIn("double free", patterns)
        self.assertIn("missing free", patterns)
        self.assertIn("allocation followed by early return", patterns)
        self.assertIn("code after unconditional return", patterns)
        self.assertIn("branches with constant conditions", patterns)

    def test_security_smells_include_unsafe_and_poor_api_usage(self) -> None:
        smell_patterns = {
            item.evidence.get("pattern")
            for item in self.opportunities
            if item.kind == "security_smell"
        }

        self.assertIn("unsafe API usage", smell_patterns)
        self.assertIn("unchecked return value", smell_patterns)
        self.assertIn("inconsistent error handling", smell_patterns)
        self.assertIn("repeated manual cleanup", smell_patterns)
        self.assertIn("poor API usage", smell_patterns)

    def test_opportunities_are_ranked_by_severity_then_confidence(self) -> None:
        ranking = [
            (
                SEVERITY_RANK[opportunity.severity],
                float(opportunity.evidence["confidence"]),
            )
            for opportunity in self.opportunities
        ]

        self.assertEqual(ranking, sorted(ranking, key=lambda item: (-item[0], -item[1])))

    def test_opportunities_include_evidence_confidence_and_verifier(self) -> None:
        opportunity = next(
            item for item in self.opportunities if item.kind == "resource_imbalance"
        )

        self.assertIn("security_impact", opportunity.evidence)
        self.assertIn("evidence_confidence", opportunity.evidence)
        self.assertIn(opportunity.evidence["evidence_confidence"], {"low", "medium", "high"})
        self.assertIn("verifier", opportunity.evidence)
        self.assertIn("verdict", opportunity.evidence["verifier"])

    def test_resource_opportunities_are_clustered_by_resource_pattern(self) -> None:
        clustered = [
            item
            for item in self.opportunities
            if item.kind == "resource_early_return" and item.function == "clustered_returns"
        ]

        self.assertEqual(len(clustered), 1)
        self.assertEqual(clustered[0].evidence["cluster_size"], 2)
        self.assertEqual(len(clustered[0].evidence["evidence_points"]), 2)

    def test_ownership_transfer_suppresses_missing_release_candidate(self) -> None:
        transfer_findings = [
            item
            for item in self.opportunities
            if item.function == "ownership_transfer" and item.kind == "resource_imbalance"
        ]

        self.assertEqual(transfer_findings, [])

    def test_post_filter_downgrades_test_path_deployment_relevance(self) -> None:
        opportunity = self.opportunities[0]
        opportunity.file = "tests/api/example.c"
        opportunity.severity = "high"
        opportunity.evidence = {
            "confidence": 0.95,
            "evidence_confidence": "high",
            "security_impact": "high",
            "verifier": {"verdict": "true_positive"},
        }

        filtered = apply_post_filters([opportunity])[0]

        self.assertEqual(filtered.severity, "medium")
        self.assertIn("test_or_example_path", filtered.evidence["post_filter_context"])
        self.assertEqual(filtered.evidence["deployment_relevance"], "reduced")

    def test_post_filter_ignores_benchmark_harness_prefix(self) -> None:
        self.assertEqual(
            deployment_contexts("/repo/benchmarks/oss50/sources/openssl/crypto/mem.c"),
            [],
        )
        self.assertIn(
            "test_or_example_path",
            deployment_contexts("/repo/benchmarks/oss50/sources/cjson/tests/minify.c"),
        )
        self.assertIn(
            "test_or_example_path",
            deployment_contexts("/repo/benchmarks/oss50/sources/cjson/test.c"),
        )
        self.assertIn(
            "test_or_example_path",
            deployment_contexts("/repo/benchmarks/oss50/sources/cjson/fuzzing/fuzz_main.c"),
        )

    def test_high_impact_medium_evidence_keeps_high_severity(self) -> None:
        self.assertEqual(evidence_confidence_label(0.58), "medium")
        self.assertEqual(gated_severity("high", "medium"), "high")
        self.assertEqual(gated_severity("critical", "medium"), "high")
