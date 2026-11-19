from __future__ import annotations

import unittest
from pathlib import Path

from scripts.prepare_detector_human_review import (
    detector_family,
    family_quotas,
    local_source_path,
    portable_manifest_item,
    repository_relative_path,
    stratified_sample,
)


class PrepareDetectorHumanReviewTests(unittest.TestCase):
    def test_detector_family_groups_related_kinds(self) -> None:
        self.assertEqual(detector_family("goto_cleanup_consolidation"), "goto")
        self.assertEqual(detector_family("resource_imbalance"), "resource")
        self.assertEqual(detector_family("duplicate_check"), "duplicate_check")
        self.assertIsNone(detector_family("unknown"))

    def test_family_quotas_sum_to_requested_size(self) -> None:
        quotas = family_quotas(403)
        self.assertEqual(sum(quotas.values()), 403)
        self.assertLessEqual(max(quotas.values()) - min(quotas.values()), 1)

    def test_stratified_sample_is_deterministic_and_caps_projects(self) -> None:
        candidates = []
        for family, kind in [
            ("goto", "goto_cleanup_consolidation"),
            ("resource", "resource_imbalance"),
            ("duplicate_check", "duplicate_check"),
            ("dead_code", "dead_code"),
            ("security_smell", "security_smell"),
        ]:
            for index in range(12):
                candidates.append(
                    {
                        "work_id": f"{family}:{index}",
                        "project_id": f"p{index % 3}",
                        "family": family,
                        "kind": kind,
                        "severity": "high" if index % 2 else "medium",
                        "evidence_confidence": "high" if index % 3 else "medium",
                        "deployment_context": "production" if index % 4 else "test_or_example_path",
                        "relative_source": f"src/f{index}.c",
                        "function": f"fn{index}",
                    }
                )
        quotas = family_quotas(30)
        first = stratified_sample(candidates, quotas, seed=7, max_per_project_family=3)
        second = stratified_sample(candidates, quotas, seed=7, max_per_project_family=3)
        self.assertEqual([item["work_id"] for item in first], [item["work_id"] for item in second])
        self.assertEqual(len(first), 30)
        for family in quotas:
            project_counts = {}
            for item in first:
                if item["family"] != family:
                    continue
                project_counts[item["project_id"]] = project_counts.get(item["project_id"], 0) + 1
            self.assertLessEqual(max(project_counts.values()), 3)

    def test_old_openssl_path_is_relocated_to_current_checkout(self) -> None:
        root = Path("/new/CARE")
        old_path = "/home/dragon/CARE/benchmarks/openssl/crypto/mem.c"
        self.assertEqual(
            local_source_path(old_path, "openssl", root),
            root / "benchmarks" / "openssl" / "crypto" / "mem.c",
        )

    def test_openssl_review_path_is_repository_relative(self) -> None:
        path = Path("/new/CARE/benchmarks/openssl/crypto/mem.c")
        self.assertEqual(repository_relative_path(path), "crypto/mem.c")

    def test_manifest_paths_are_portable_between_checkouts(self) -> None:
        root = Path("/new/CARE")
        item = {
            "file": "/new/CARE/benchmarks/openssl/crypto/mem.c",
            "report_file": "/new/CARE/benchmarks/oss50/results/reports/openssl-care-report.json",
            "work_id": "openssl:1:test",
        }
        portable = portable_manifest_item(item, root)
        self.assertEqual(portable["file"], "benchmarks/openssl/crypto/mem.c")
        self.assertEqual(
            portable["report_file"],
            "benchmarks/oss50/results/reports/openssl-care-report.json",
        )


if __name__ == "__main__":
    unittest.main()
