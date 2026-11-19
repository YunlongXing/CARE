from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from scripts import run_oss50_llm_validation as validation


class OSS50PathNormalizationTests(unittest.TestCase):
    def test_old_oss50_source_path_is_relocated(self) -> None:
        root = Path("/new/CARE")
        old_path = Path("/home/dragon/CARE/benchmarks/oss50/sources/curl/lib/url.c")
        with patch.object(validation, "ROOT", root):
            self.assertEqual(
                validation.map_oss50_path(old_path, expect_project_root=False),
                root / "benchmarks" / "oss50" / "sources" / "curl" / "lib" / "url.c",
            )

    def test_old_openssl_source_path_is_relocated(self) -> None:
        root = Path("/new/CARE")
        old_path = Path("/home/dragon/CARE/benchmarks/openssl/crypto/mem.c")
        with patch.object(validation, "ROOT", root):
            self.assertEqual(
                validation.map_oss50_path(old_path, expect_project_root=False),
                root / "benchmarks" / "openssl" / "crypto" / "mem.c",
            )

    def test_openssl_project_fallback_uses_reused_checkout(self) -> None:
        root = Path("/new/CARE")
        with patch.object(validation, "ROOT", root):
            self.assertEqual(
                validation.normalize_project_path(None, "openssl"),
                str(root / "benchmarks" / "openssl"),
            )

    def test_build_queue_skips_stale_missing_source_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "benchmarks" / "oss50" / "sources" / "demo"
            source = project / "src" / "present.c"
            source.parent.mkdir(parents=True)
            source.write_text("int present(void) { return 0; }\n", encoding="utf-8")
            report_path = root / "demo-care-report.json"
            report_path.write_text(
                json.dumps(
                    {
                        "detected_opportunities": [
                            self._opportunity("present", "/old/CARE/benchmarks/oss50/sources/demo/src/present.c"),
                            self._opportunity("missing", "/old/CARE/benchmarks/oss50/sources/demo/src/missing.c"),
                        ]
                    }
                ),
                encoding="utf-8",
            )
            records = [
                {
                    "id": "demo",
                    "name": "Demo",
                    "project_path": "/old/CARE/benchmarks/oss50/sources/demo",
                    "report_json": str(report_path),
                }
            ]
            with patch.object(validation, "ROOT", root):
                queue = validation.build_queue(
                    suite_results=root,
                    records=records,
                    severities={"high"},
                    start_after=None,
                    max_per_project=None,
                    limit_total=None,
                )
            self.assertEqual([item["opportunity_id"] for item in queue], ["present"])

    @staticmethod
    def _opportunity(opportunity_id: str, file_path: str) -> dict[str, object]:
        return {
            "id": opportunity_id,
            "kind": "resource_imbalance",
            "file": file_path,
            "function": "target",
            "location": {"file": file_path, "line": 1, "column": None},
            "severity": "high",
            "description": "test",
            "evidence": {"evidence_confidence": "high"},
            "context_summary": "test",
        }


if __name__ == "__main__":
    unittest.main()
