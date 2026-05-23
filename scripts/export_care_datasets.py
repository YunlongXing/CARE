"""Export CARE review and validation artifacts as reusable JSONL labels."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from care.validation.taxonomy import classify_validation_result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detector-reviews", nargs="*", default=[])
    parser.add_argument("--validation-results", nargs="*", default=[])
    parser.add_argument("--output-dir", default="benchmarks/oss50/labels")
    args = parser.parse_args(argv)

    root = Path.cwd().resolve()
    output_dir = (root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    detector_records = list(load_detector_reviews(root, args.detector_reviews))
    validation_records = list(load_validation_results(root, args.validation_results))
    taxonomy_records = [failure_taxonomy_record(record) for record in validation_records]

    write_jsonl(output_dir / "detector_precision_v2.jsonl", detector_records)
    write_jsonl(output_dir / "patch_validation_v2.jsonl", validation_records)
    write_jsonl(output_dir / "failure_taxonomy_v2.jsonl", taxonomy_records)
    (output_dir / "manifest.json").write_text(
        json.dumps(
            {
                "detector_precision_records": len(detector_records),
                "patch_validation_records": len(validation_records),
                "failure_taxonomy_records": len(taxonomy_records),
                "detector_review_inputs": args.detector_reviews,
                "validation_result_inputs": args.validation_results,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print(f"wrote CARE datasets to {output_dir}")
    return 0


def load_detector_reviews(root: Path, inputs: list[str]) -> Iterable[dict[str, Any]]:
    for path in expand_inputs(root, inputs, default_name="detector_precision_reviews.jsonl"):
        for record in read_jsonl(path):
            yield {
                "dataset": "detector_precision",
                "work_id": record.get("work_id"),
                "project_id": record.get("project_id"),
                "kind": record.get("kind"),
                "severity": record.get("severity"),
                "function": record.get("function"),
                "file": record.get("file"),
                "line": record.get("line"),
                "label": record.get("label"),
                "strict_positive": record.get("label") == "true_positive",
                "lenient_positive": record.get("label") in {"true_positive", "likely_true_positive"},
                "security_relevant": bool(record.get("security_relevant")),
                "resource_lifecycle_relevant": bool(record.get("resource_lifecycle_relevant")),
                "review_confidence": record.get("confidence"),
                "review_reason": record.get("reason"),
                "source": str(path),
            }


def load_validation_results(root: Path, inputs: list[str]) -> Iterable[dict[str, Any]]:
    for path in expand_inputs(root, inputs, default_name="results.jsonl"):
        for record in read_jsonl(path):
            validation = record.get("validation")
            taxonomy = classify_validation_result(validation)
            summary = record.get("validation_summary") or {}
            yield {
                "dataset": "patch_validation",
                "work_id": record.get("work_id"),
                "project_id": record.get("project_id"),
                "baseline": record.get("baseline", "care"),
                "kind": record.get("kind"),
                "severity": record.get("severity"),
                "function": record.get("function"),
                "file": record.get("file"),
                "line": record.get("line"),
                "status": record.get("status"),
                "selected_patch": bool(record.get("selected_patch")),
                "validation_passed": bool(record.get("validation_passed")),
                "failed_stages": summary.get("failed_stages") or taxonomy.get("failed_stages") or [],
                "failure_categories": summary.get("failure_categories") or taxonomy.get("categories") or [],
                "primary_failure_category": summary.get("primary_failure_category")
                or taxonomy.get("primary_category"),
                "candidate_count": len(record.get("candidate_patches") or []),
                "duration_seconds": record.get("duration_seconds"),
                "source": str(path),
            }


def failure_taxonomy_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "dataset": "failure_taxonomy",
        "work_id": record.get("work_id"),
        "project_id": record.get("project_id"),
        "baseline": record.get("baseline", "care"),
        "validation_passed": record.get("validation_passed"),
        "failed_stages": record.get("failed_stages") or [],
        "failure_categories": record.get("failure_categories") or [],
        "primary_failure_category": record.get("primary_failure_category"),
        "status": record.get("status"),
    }


def expand_inputs(root: Path, inputs: list[str], default_name: str) -> list[Path]:
    paths: list[Path] = []
    for raw in inputs:
        path = (root / raw).resolve()
        if path.is_dir():
            candidate = path / default_name
            if candidate.exists():
                paths.append(candidate)
            continue
        if path.exists():
            paths.append(path)
    return paths


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    rows = list(records)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
