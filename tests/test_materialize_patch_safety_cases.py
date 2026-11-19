from __future__ import annotations

import csv
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.materialize_patch_safety_cases import main


class MaterializePatchSafetyCasesTests(unittest.TestCase):
    def test_materializes_only_adjudicated_safe_refactoring(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, base, patch = self.make_repository(root)
            review_dir = root / "review"
            review_dir.mkdir()
            patch_dir = root / "mined" / "patches"
            patch_dir.mkdir(parents=True)
            patch_file = patch_dir / "demo.diff"
            patch_file.write_text(patch, encoding="utf-8")
            manifest = {
                "items": [
                    {
                        "sample_id": "P0001",
                        "project_id": "demo",
                        "repository": str(repository),
                        "base_commit": base,
                        "patch_commit": "unused-by-test",
                        "changed_files": ["main.c"],
                        "patch_file": "patches/demo.diff",
                    }
                ]
            }
            (review_dir / "review_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.write_adjudication(review_dir / "adjudication.csv")

            output_dir = root / "real"
            exit_code = main(
                [
                    "--review-manifest", str(review_dir / "review_manifest.json"),
                    "--adjudication", str(review_dir / "adjudication.csv"),
                    "--output-dir", str(output_dir),
                ]
            )
            self.assertEqual(exit_code, 0)
            materialized = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(materialized["case_count"], 1)
            self.assertEqual(materialized["cases"][0]["ground_truth"], "safe")
            self.assertEqual((output_dir / "cases" / "p0001" / "main.c").read_text(encoding="utf-8"), "int value(void) { return 1; }\n")
            self.assertTrue((output_dir / "patches" / "p0001.diff").is_file())

    def test_materializes_strict_agent_consensus_without_human_sheet(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, base, patch = self.make_repository(root)
            review_dir = root / "review"
            review_dir.mkdir()
            patch_dir = root / "mined" / "patches"
            patch_dir.mkdir(parents=True)
            (patch_dir / "demo.diff").write_text(patch, encoding="utf-8")
            (review_dir / "review_manifest.json").write_text(
                json.dumps({"items": [{
                    "sample_id": "P0001", "project_id": "demo", "repository": str(repository),
                    "base_commit": base, "patch_commit": "unused", "changed_files": ["main.c"],
                    "patch_file": "patches/demo.diff",
                }]}),
                encoding="utf-8",
            )
            consensus = root / "consensus.csv"
            consensus.write_text("sample_id,eligible_for_benchmark\nP0001,yes\n", encoding="utf-8")
            output_dir = root / "real"
            self.assertEqual(
                main([
                    "--review-manifest", str(review_dir / "review_manifest.json"),
                    "--agent-consensus", str(consensus), "--output-dir", str(output_dir),
                ]),
                0,
            )
            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["label_mode"], "agent_consensus")
            self.assertEqual(manifest["case_count"], 1)

    def test_accepts_the_existing_build_profile_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, base, patch = self.make_repository(root)
            review_dir = root / "review"
            review_dir.mkdir()
            patch_dir = root / "mined" / "patches"
            patch_dir.mkdir(parents=True)
            (patch_dir / "demo.diff").write_text(patch, encoding="utf-8")
            (review_dir / "review_manifest.json").write_text(
                json.dumps({"items": [{
                    "sample_id": "P0001", "project_id": "demo", "repository": str(repository),
                    "base_commit": base, "patch_commit": "unused", "changed_files": ["main.c"],
                    "patch_file": "patches/demo.diff",
                }]}),
                encoding="utf-8",
            )
            consensus = root / "consensus.csv"
            consensus.write_text("sample_id,eligible_for_benchmark\nP0001,yes\n", encoding="utf-8")
            config = root / "profiles.json"
            config.write_text(json.dumps({"projects": {"demo": {"build_cmd": "make", "test_cmd": "make test"}}}), encoding="utf-8")
            output_dir = root / "real"
            main([
                "--review-manifest", str(review_dir / "review_manifest.json"),
                "--agent-consensus", str(consensus), "--project-config", str(config),
                "--output-dir", str(output_dir),
            ])
            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["cases"][0]["build_cmd"], "make")

    def test_skips_profile_excluded_historical_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, base, patch = self.make_repository(root)
            review_dir = root / "review"
            review_dir.mkdir()
            patch_dir = root / "mined" / "patches"
            patch_dir.mkdir(parents=True)
            (patch_dir / "demo.diff").write_text(patch, encoding="utf-8")
            (review_dir / "review_manifest.json").write_text(
                json.dumps({"items": [{
                    "sample_id": "P0001", "project_id": "demo", "repository": str(repository),
                    "base_commit": base, "patch_commit": "unused", "changed_files": ["main.c"],
                    "patch_file": "patches/demo.diff",
                }]}),
                encoding="utf-8",
            )
            consensus = root / "consensus.csv"
            consensus.write_text("sample_id,eligible_for_benchmark\nP0001,yes\n", encoding="utf-8")
            config = root / "profiles.json"
            config.write_text(
                json.dumps({"projects": {"demo": {
                    "patch_safety_exclude": True,
                    "patch_safety_exclusion_reason": "unsupported historical toolchain",
                }}}),
                encoding="utf-8",
            )
            output_dir = root / "real"
            self.assertEqual(
                main([
                    "--review-manifest", str(review_dir / "review_manifest.json"),
                    "--agent-consensus", str(consensus), "--project-config", str(config),
                    "--output-dir", str(output_dir),
                ]),
                0,
            )
            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["case_count"], 0)
            self.assertEqual(manifest["skipped"][0]["reason"], "unsupported historical toolchain")

    def make_repository(self, root: Path) -> tuple[Path, str, str]:
        repository = root / "demo"
        repository.mkdir()
        self.git(repository, "init")
        self.git(repository, "config", "user.email", "care@example.test")
        self.git(repository, "config", "user.name", "CARE Test")
        (repository / "main.c").write_text("int value(void) { return 1; }\n", encoding="utf-8")
        self.git(repository, "add", "main.c")
        self.git(repository, "commit", "-m", "initial")
        base = self.git(repository, "rev-parse", "HEAD").strip()
        (repository / "main.c").write_text("/* refactored */\nint value(void) { return 1; }\n", encoding="utf-8")
        self.git(repository, "add", "main.c")
        self.git(repository, "commit", "-m", "refactor")
        patch = self.git(repository, "diff", "--unified=40", base, "HEAD", "--", "main.c")
        return repository, base, patch

    @staticmethod
    def write_adjudication(path: Path) -> None:
        fields = [
            "sample_id", "case_id", "project_id", "candidate_category", "reviewer_1_verdict",
            "reviewer_2_verdict", "agreement", "final_verdict", "semantic_preserving",
            "security_relevant", "adjudication_reason", "eligible_for_benchmark",
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerow(
                {
                    "sample_id": "P0001",
                    "case_id": "demo:abc",
                    "project_id": "demo",
                    "final_verdict": "refactoring",
                    "semantic_preserving": "yes",
                    "eligible_for_benchmark": "yes",
                }
            )

    @staticmethod
    def git(repository: Path, *arguments: str) -> str:
        completed = subprocess.run(
            ["git", *arguments], cwd=repository, text=True, capture_output=True, check=False
        )
        if completed.returncode:
            raise AssertionError(completed.stderr)
        return completed.stdout


if __name__ == "__main__":
    unittest.main()
