from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.prepare_patch_safety_human_review import main, stratified_sample


class PreparePatchSafetyHumanReviewTests(unittest.TestCase):
    def test_creates_independent_blank_review_sheets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            patches = root / "patches"
            patches.mkdir()
            (patches / "demo.diff").write_text("--- a/main.c\n+++ b/main.c\n", encoding="utf-8")
            candidates = {
                "candidates": [
                    {
                        "id": "demo:abc",
                        "project_id": "demo",
                        "base_commit": "base",
                        "patch_commit": "patch",
                        "subject": "refactor demo",
                        "candidate_category": "candidate_refactoring",
                        "changed_files": ["main.c"],
                        "patch_file": "patches/demo.diff",
                    }
                ]
            }
            candidate_path = root / "candidates.json"
            candidate_path.write_text(json.dumps(candidates), encoding="utf-8")
            output = root / "review"
            exit_code = main(["--candidates", str(candidate_path), "--output-dir", str(output)])
            self.assertEqual(exit_code, 0)
            for name in ("reviewer_1.csv", "reviewer_2.csv", "REVIEW_PROTOCOL.md"):
                self.assertTrue((output / name).is_file())
            self.assertTrue((output / "adjudication.csv").is_file())
            material = (output / "materials" / "P0001.md").read_text(encoding="utf-8")
            self.assertIn("refactor demo", material)

    def test_stratified_sampling_is_deterministic_and_balanced(self) -> None:
        candidates = []
        for category in ("candidate_refactoring", "candidate_security_repair"):
            for index in range(20):
                candidates.append(
                    {
                        "id": f"{category}:{index}",
                        "project_id": f"project-{index % 4}",
                        "candidate_category": category,
                    }
                )
        first = stratified_sample(candidates, sample_size=16, seed=7, max_per_project=8)
        second = stratified_sample(candidates, sample_size=16, seed=7, max_per_project=8)
        self.assertEqual([item["id"] for item in first], [item["id"] for item in second])
        self.assertEqual(len(first), 16)
        self.assertEqual(sum(item["candidate_category"] == "candidate_refactoring" for item in first), 8)


if __name__ == "__main__":
    unittest.main()
