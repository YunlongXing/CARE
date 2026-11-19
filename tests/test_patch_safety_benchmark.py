from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.run_patch_safety_benchmark import _temporary_prefix, main


class PatchSafetyBenchmarkTests(unittest.TestCase):
    def test_temporary_prefix_does_not_embed_path_separator_characters(self) -> None:
        self.assertEqual(_temporary_prefix("P0002:safe"), "care-patch-safety-P0002-safe-")

    def test_seed_benchmark_distinguishes_build_tests_from_care(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary) / "results"
            exit_code = main(
                [
                    "--manifest",
                    str(root / "benchmarks" / "patch_safety" / "manifest.json"),
                    "--output-dir",
                    str(output_dir),
                ]
            )
            self.assertEqual(exit_code, 0)
            summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["case_count"], 11)
            self.assertGreater(
                summary["systems"]["build_test"]["false_accepts"],
                0,
            )
            self.assertGreater(
                summary["systems"]["care"]["unsafe_rejected"],
                0,
            )
            self.assertEqual(summary["systems"]["care"]["build_executed"], 11)
            self.assertEqual(summary["systems"]["care"]["build_qualified"]["safe_total"], 2)
            self.assertTrue((output_dir / "results.jsonl").is_file())

    def test_validator_ablations_are_reported_as_distinct_systems(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary) / "results"
            exit_code = main(
                [
                    "--manifest",
                    str(root / "benchmarks" / "patch_safety" / "manifest.json"),
                    "--output-dir",
                    str(output_dir),
                    "--systems",
                    "care_no_resource",
                    "care_no_semantic",
                    "care_no_security",
                ]
            )
            self.assertEqual(exit_code, 0)
            summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(
                set(summary["systems"]),
                {"care_no_resource", "care_no_semantic", "care_no_security"},
            )
            first = json.loads((output_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertIn(
                "disabled for ablation",
                "\n".join(
                    first["systems"]["care_no_resource"]["stage_results"]
                    ["resource_consistency"]["warnings"]
                ),
            )

    def test_calibration_cases_exercise_resource_and_security_gates(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary) / "results"
            self.assertEqual(
                main([
                    "--manifest", str(root / "benchmarks" / "patch_safety" / "manifest.json"),
                    "--output-dir", str(output_dir), "--systems", "care",
                ]),
                0,
            )
            rows = {
                row["case_id"]: row
                for row in (
                    json.loads(line)
                    for line in (output_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
                )
            }
            for case_id in (
                "unsafe_disabled_null_guard",
                "unsafe_disabled_bounds_guard",
                "unsafe_disabled_status_guard",
            ):
                stages = rows[case_id]["systems"]["care"]["stage_results"]
                self.assertFalse(stages["security_regression"]["passed"])
                self.assertTrue(stages["semantic_equivalence"]["passed"])
            for case_id in ("unsafe_cleanup_removal", "unsafe_double_free"):
                stages = rows[case_id]["systems"]["care"]["stage_results"]
                self.assertFalse(stages["resource_consistency"]["passed"])


if __name__ == "__main__":
    unittest.main()
