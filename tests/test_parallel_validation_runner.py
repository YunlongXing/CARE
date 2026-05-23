from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.run_oss50_parallel_validation import (
    assign_project_shards,
    merge_worker_outputs,
)


class ParallelValidationRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="care-parallel-validation-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir)

    def test_assign_project_shards_keeps_projects_together(self) -> None:
        queue = []
        for project_id, count in [("openssl", 5), ("curl", 3), ("sqlite", 2)]:
            for index in range(count):
                queue.append({"project_id": project_id, "work_id": f"{project_id}:{index}"})

        shards = assign_project_shards(queue, workers=2, completed={"openssl:0"})

        project_to_worker = {}
        for shard in shards:
            for project_id in shard["projects"]:
                self.assertNotIn(project_id, project_to_worker)
                project_to_worker[project_id] = shard["worker"]
        self.assertEqual(set(project_to_worker), {"openssl", "curl", "sqlite"})
        self.assertEqual(sum(shard["queue_size"] for shard in shards), 9)

    def test_merge_worker_outputs_deduplicates_by_work_id(self) -> None:
        output_dir = self.tmpdir / "out"
        worker_dir = output_dir / "workers" / "worker-00"
        worker_dir.mkdir(parents=True)
        queue = [
            {"work_id": "a:1", "project_id": "a", "severity": "high", "kind": "dead_code"},
            {"work_id": "b:1", "project_id": "b", "severity": "high", "kind": "security_smell"},
        ]
        (output_dir / "results.jsonl").parent.mkdir(parents=True, exist_ok=True)
        (output_dir / "results.jsonl").write_text(
            '{"work_id":"a:1","project_id":"a","validation_passed":false}\n',
            encoding="utf-8",
        )
        (worker_dir / "results.jsonl").write_text(
            '{"work_id":"a:1","project_id":"a","validation_passed":true,"selected_patch":{}}\n'
            '{"work_id":"b:1","project_id":"b","validation_passed":false}\n',
            encoding="utf-8",
        )

        merged = merge_worker_outputs(output_dir, queue)

        self.assertEqual([record["work_id"] for record in merged], ["a:1", "b:1"])
        self.assertTrue(merged[0]["validation_passed"])
        self.assertTrue((output_dir / "summary.json").exists())
        self.assertTrue((output_dir / "project_status.csv").exists())


if __name__ == "__main__":
    unittest.main()
