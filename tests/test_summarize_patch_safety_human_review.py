from __future__ import annotations

import unittest

from scripts.summarize_patch_safety_human_review import cohens_kappa, summarize


class SummarizePatchSafetyHumanReviewTests(unittest.TestCase):
    def test_summary_reports_agreement_and_kappa(self) -> None:
        reviewer_1 = [
            {"sample_id": "P0001", "verdict": "refactoring"},
            {"sample_id": "P0002", "verdict": "security_repair"},
            {"sample_id": "P0003", "verdict": "unclear"},
        ]
        reviewer_2 = [
            {"sample_id": "P0001", "verdict": "refactoring"},
            {"sample_id": "P0002", "verdict": "not_actionable"},
            {"sample_id": "P0003", "verdict": "unclear"},
        ]
        rows, summary = summarize(reviewer_1, reviewer_2)
        self.assertEqual(len(rows), 3)
        self.assertEqual(summary["labeled_pairs"], 3)
        self.assertAlmostEqual(summary["raw_agreement"], 2 / 3)
        self.assertIsNotNone(summary["cohens_kappa"])

    def test_kappa_is_one_for_identical_labels(self) -> None:
        self.assertEqual(cohens_kappa(["refactoring", "unclear"], ["refactoring", "unclear"]), 1.0)


if __name__ == "__main__":
    unittest.main()
