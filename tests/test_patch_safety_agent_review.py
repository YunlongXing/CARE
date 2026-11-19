from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.run_patch_safety_agent_review import balanced_limit, build_consensus, estimate_cost, main, normalize_decision, usage_cost


class PatchSafetyAgentReviewTests(unittest.TestCase):
    def test_dry_run_writes_auditable_agent_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            review = root / "review"
            (review / "materials").mkdir(parents=True)
            (review / "materials" / "P0001.md").write_text("# patch\n```diff\n+int x;\n```\n", encoding="utf-8")
            manifest = {
                "sample_digest": "digest",
                "items": [{
                    "sample_id": "P0001", "id": "demo:1", "project_id": "demo",
                    "candidate_category": "candidate_refactoring", "subject": "refactor demo",
                    "changed_files": ["main.c"], "material_file": "materials/P0001.md",
                }],
            }
            (review / "review_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            output = root / "output"
            self.assertEqual(
                main(["--review-manifest", str(review / "review_manifest.json"), "--output-dir", str(output)]),
                0,
            )
            summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["completed_pairs"], 0)
            self.assertEqual(summary["pair_status_counts"]["dry_run"], 1)
            self.assertTrue((output / "context_classifier.jsonl").is_file())

    def test_consensus_requires_two_high_confidence_refactoring_decisions(self) -> None:
        items = [{"sample_id": "P0001", "id": "demo:1", "project_id": "demo"}]
        decision = {
            "verdict": "refactoring", "semantic_preserving": "yes", "security_relevant": "yes",
            "confidence": 4, "evidence": [], "reason": "",
        }
        results = {
            "context_classifier": {"P0001": {"status": "ok", "estimated_cost_usd": 0.1, "decision": decision}},
            "adversarial_auditor": {"P0001": {"status": "ok", "estimated_cost_usd": 0.1, "decision": decision}},
        }
        rows, summary = build_consensus(items, results)
        self.assertEqual(rows[0]["eligible_for_benchmark"], "yes")
        self.assertEqual(summary["eligible_for_benchmark"], 1)

    def test_normalization_and_cost_are_bounded(self) -> None:
        decision = normalize_decision({"verdict": "bad", "confidence": 8})
        self.assertEqual(decision["verdict"], "unclear")
        self.assertEqual(decision["confidence"], 5)
        self.assertGreater(estimate_cost("gpt-6.1-sol", "x" * 4000, 1000), 0.0)
        self.assertGreater(
            usage_cost(
                "gpt-6-luna",
                {"input_tokens": 1000, "output_tokens": 100, "input_tokens_details": {"cache_write_tokens": 1000}},
            ),
            0.0,
        )

    def test_pilot_selection_balances_candidate_categories(self) -> None:
        items = [
            {"sample_id": f"R{index}", "candidate_category": category}
            for category in ("candidate_refactoring", "candidate_security_repair")
            for index in range(20)
        ]
        selected = balanced_limit(items, 10)
        self.assertEqual(len(selected), 10)
        self.assertEqual(sum(item["candidate_category"] == "candidate_refactoring" for item in selected), 5)


if __name__ == "__main__":
    unittest.main()
