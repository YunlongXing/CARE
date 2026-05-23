from __future__ import annotations

import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from scripts.run_oss50_dynamic_validation import (
    claim_next_item,
    collect_completed_work_ids,
    prepare_project_workspace,
    reservation_path,
)
from scripts.run_oss50_llm_validation import read_jsonl


class DynamicValidationRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="care-dynamic-validation-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir)

    def test_claim_next_item_skips_completed_and_reserves_once(self) -> None:
        output_dir = self.tmpdir / "out"
        reservation_dir = output_dir / "dynamic" / "reservations"
        output_dir.mkdir(parents=True)
        (output_dir / "results.jsonl").write_text(
            '{"work_id":"a:1","validation_passed":true}\n',
            encoding="utf-8",
        )
        queue = [
            {"work_id": "a:1"},
            {"work_id": "b:1"},
            {"work_id": "c:1"},
        ]

        first = claim_next_item(queue, output_dir, reservation_dir, worker_index=0, stale_seconds=3600)
        second = claim_next_item(queue, output_dir, reservation_dir, worker_index=1, stale_seconds=3600)

        self.assertEqual(first["work_id"], "b:1")
        self.assertEqual(second["work_id"], "c:1")
        self.assertTrue(reservation_path(reservation_dir, "b:1").exists())
        self.assertTrue(reservation_path(reservation_dir, "c:1").exists())

    def test_claim_next_item_reclaims_stale_reservation(self) -> None:
        output_dir = self.tmpdir / "out"
        reservation_dir = output_dir / "dynamic" / "reservations"
        reservation_dir.mkdir(parents=True)
        queue = [{"work_id": "stale:1"}]
        path = reservation_path(reservation_dir, "stale:1")
        path.write_text(json.dumps({"reserved_at": time.time() - 100}) + "\n", encoding="utf-8")

        claimed = claim_next_item(queue, output_dir, reservation_dir, worker_index=2, stale_seconds=1)

        self.assertEqual(claimed["work_id"], "stale:1")
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["worker_index"], 2)

    def test_collect_completed_work_ids_reads_worker_outputs(self) -> None:
        output_dir = self.tmpdir / "out"
        worker_dir = output_dir / "workers" / "worker-dynamic-00"
        worker_dir.mkdir(parents=True)
        (output_dir / "results.jsonl").parent.mkdir(parents=True, exist_ok=True)
        (output_dir / "results.jsonl").write_text('{"work_id":"root:1"}\n', encoding="utf-8")
        (worker_dir / "results.jsonl").write_text('{"work_id":"worker:1"}\n', encoding="utf-8")

        completed = collect_completed_work_ids(output_dir)

        self.assertEqual(completed, {"root:1", "worker:1"})

    def test_read_jsonl_ignores_trailing_partial_line(self) -> None:
        path = self.tmpdir / "results.jsonl"
        path.write_text(
            '{"work_id":"complete:1"}\n{"work_id":"partial',
            encoding="utf-8",
        )

        records = read_jsonl(path)

        self.assertEqual(records, [{"work_id": "complete:1"}])

    def test_prepare_project_workspace_copies_source_tree(self) -> None:
        src = self.tmpdir / "src"
        src.mkdir()
        (src / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
        (src / ".git").mkdir()
        (src / ".git" / "config").write_text("[core]\n", encoding="utf-8")

        dst = prepare_project_workspace(
            src=src,
            workspace_root=self.tmpdir / "workspaces",
            worker_index=3,
            project_id="demo/project",
        )

        self.assertEqual((dst / "main.c").read_text(encoding="utf-8"), "int main(void) { return 0; }\n")
        self.assertFalse((dst / ".git").exists())
        self.assertEqual((dst / ".care-workspace-source").read_text(encoding="utf-8").strip(), str(src))


if __name__ == "__main__":
    unittest.main()
