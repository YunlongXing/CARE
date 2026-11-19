from __future__ import annotations

import unittest

from scripts.summarize_detector_human_review import cohens_kappa, precision_record, wilson_interval


class SummarizeDetectorHumanReviewTests(unittest.TestCase):
    def test_kappa_is_one_for_identical_labels(self) -> None:
        labels = ["true_positive", "false_positive", "unclear"]
        self.assertEqual(cohens_kappa(labels, labels), 1.0)

    def test_kappa_accounts_for_chance_agreement(self) -> None:
        left = ["true_positive", "true_positive", "false_positive", "false_positive"]
        right = ["true_positive", "false_positive", "true_positive", "false_positive"]
        self.assertEqual(cohens_kappa(left, right), 0.0)

    def test_wilson_interval_contains_observed_rate(self) -> None:
        low, high = wilson_interval(70, 100)
        self.assertLess(low, 0.7)
        self.assertGreater(high, 0.7)

    def test_precision_excludes_unclear_and_supports_weights(self) -> None:
        records = [
            {"final_verdict": "true_positive", "sampling_weight": 2.0},
            {"final_verdict": "false_positive", "sampling_weight": 1.0},
            {"final_verdict": "unclear", "sampling_weight": 10.0},
        ]
        summary = precision_record(records)
        self.assertAlmostEqual(summary["precision_excluding_unclear"], 0.5)
        self.assertAlmostEqual(summary["weighted_precision_excluding_unclear"], 2.0 / 3.0, places=6)


if __name__ == "__main__":
    unittest.main()
