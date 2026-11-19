"""Create a manual-review CSV for validator-accepted patches."""

from __future__ import annotations

import argparse
import csv
import glob
import json
from pathlib import Path
from typing import Any


DEFAULT_SYSTEMS = (
    "CARE=benchmarks/oss50/llm-validation-critical-only,"
    "LLM-only=benchmarks/oss50/llm-validation-critical-llm-only-shards/shard-*"
)
DEFAULT_OUTPUT = "benchmarks/oss50/paper-tables/manual_patch_review.csv"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--systems", default=DEFAULT_SYSTEMS)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    root = Path.cwd().resolve()
    rows = []
    for system, dirs in parse_systems(root, args.systems).items():
        rows.extend(review_rows(system, dirs))
    output = (root / args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    write_csv(output, rows)
    print(f"wrote manual review sheet with {len(rows)} rows to {output}")
    return 0


def review_rows(system: str, result_dirs: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result_dir in result_dirs:
        for result in read_jsonl(result_dir / "results.jsonl"):
            if not result.get("validation_passed"):
                continue
            patch_record = selected_patch_record(result)
            rows.append(
                {
                    "system": system,
                    "work_id": result.get("work_id"),
                    "project_id": result.get("project_id"),
                    "severity": result.get("severity"),
                    "kind": result.get("kind"),
                    "function": result.get("function"),
                    "file": result.get("file"),
                    "line": result.get("line"),
                    "description": result.get("description"),
                    "patch_path": patch_record.get("selected_path") or patch_record.get("path") or "",
                    "reviewer_1_verdict": "",
                    "reviewer_1_reason": "",
                    "reviewer_2_verdict": "",
                    "reviewer_2_reason": "",
                    "final_verdict": "",
                    "failure_or_risk_category": "",
                    "notes": "",
                }
            )
    return rows


def selected_patch_record(result: dict[str, Any]) -> dict[str, Any]:
    for record in result.get("candidate_patches") or []:
        if record.get("selected"):
            return record
    return (result.get("candidate_patches") or [{}])[0]


def parse_systems(root: Path, raw: str) -> dict[str, list[Path]]:
    systems: dict[str, list[Path]] = {}
    current_name: str | None = None
    current_parts: list[str] = []
    for token in [part.strip() for part in raw.split(",") if part.strip()]:
        if "=" in token:
            if current_name is not None:
                systems[current_name] = parse_dirs(root, ",".join(current_parts))
            current_name, first = token.split("=", 1)
            current_parts = [first]
        elif current_name is not None:
            current_parts.append(token)
        else:
            raise ValueError(f"system entry is missing NAME= prefix: {token}")
    if current_name is not None:
        systems[current_name] = parse_dirs(root, ",".join(current_parts))
    return systems


def parse_dirs(root: Path, raw: str) -> list[Path]:
    dirs: list[Path] = []
    for item in [part.strip() for part in raw.split(",") if part.strip()]:
        pattern = str((root / item).resolve()) if not Path(item).is_absolute() else item
        if any(ch in pattern for ch in "*?[]"):
            dirs.extend(sorted(Path(path).resolve() for path in glob.glob(pattern) if Path(path).is_dir()))
        else:
            dirs.append(Path(pattern).resolve())
    return dirs


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "system",
        "work_id",
        "project_id",
        "severity",
        "kind",
        "function",
        "file",
        "line",
        "description",
        "patch_path",
        "reviewer_1_verdict",
        "reviewer_1_reason",
        "reviewer_2_verdict",
        "reviewer_2_reason",
        "final_verdict",
        "failure_or_risk_category",
        "notes",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


if __name__ == "__main__":
    raise SystemExit(main())
