"""Aggregate validation failure reasons for CARE/baseline experiments."""

from __future__ import annotations

import argparse
import csv
import glob
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DEFAULT_SYSTEMS = (
    "CARE=benchmarks/oss50/llm-validation-critical-only,"
    "LLM-only=benchmarks/oss50/llm-validation-critical-llm-only-shards/shard-*"
)
DEFAULT_OUTPUT_DIR = "benchmarks/oss50/paper-tables"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--systems", default=DEFAULT_SYSTEMS)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)

    root = Path.cwd().resolve()
    systems = parse_systems(root, args.systems)
    output_dir = (root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for system, dirs in systems.items():
        rows.extend(summarize_failures(system, dirs))

    write_csv(output_dir / "table_failure_taxonomy.csv", rows)
    write_markdown(output_dir / "table_failure_taxonomy.md", rows)
    write_latex(output_dir / "table_failure_taxonomy.tex", rows)
    (output_dir / "table_failure_taxonomy.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"wrote failure taxonomy artifacts to {output_dir}")
    return 0


def summarize_failures(system: str, result_dirs: list[Path]) -> list[dict[str, Any]]:
    results = unique_results(
        result
        for result_dir in result_dirs
        for result in read_jsonl(result_dir / "results.jsonl")
    )
    candidate_total = sum(len(result.get("candidate_patches") or []) for result in results)
    passed = sum(1 for result in results if result.get("validation_passed"))
    primary_counts: Counter[str] = Counter()
    failed_stage_counts: Counter[str] = Counter()
    applicable_failures = 0
    nonapplicable_failures = 0
    for result in results:
        if result.get("validation_passed"):
            continue
        summary = result.get("validation_summary") or {}
        primary = summary.get("primary_failure_category") or infer_primary_failure(result)
        primary_counts[primary] += 1
        for stage in summary.get("failed_stages") or []:
            failed_stage_counts[stage] += 1
        if candidate_applied(result):
            applicable_failures += 1
        else:
            nonapplicable_failures += 1

    rows: list[dict[str, Any]] = []
    for category, count in primary_counts.most_common():
        rows.append(
            {
                "system": system,
                "failure_category": category,
                "count": count,
                "rate_per_processed": rate(count, len(results)),
                "candidate_total": candidate_total,
                "processed": len(results),
                "validated": passed,
                "applicable_failed": applicable_failures,
                "nonapplicable_failed": nonapplicable_failures,
            }
        )
    if not rows:
        rows.append(
            {
                "system": system,
                "failure_category": "none",
                "count": 0,
                "rate_per_processed": 0.0,
                "candidate_total": candidate_total,
                "processed": len(results),
                "validated": passed,
                "applicable_failed": applicable_failures,
                "nonapplicable_failed": nonapplicable_failures,
            }
        )
    stage_rows = [
        {
            "system": system,
            "failure_category": f"stage:{stage}",
            "count": count,
            "rate_per_processed": rate(count, len(results)),
            "candidate_total": candidate_total,
            "processed": len(results),
            "validated": passed,
            "applicable_failed": applicable_failures,
            "nonapplicable_failed": nonapplicable_failures,
        }
        for stage, count in failed_stage_counts.most_common()
    ]
    return rows + stage_rows


def candidate_applied(result: dict[str, Any]) -> bool:
    validations = candidate_validations_for(result)
    return any(stage_passed(validation, "apply_patch") for validation in validations)


def infer_primary_failure(result: dict[str, Any]) -> str:
    validations = candidate_validations_for(result)
    if not validations:
        return "llm_api_or_empty_patch"
    for validation in validations:
        stages = validation.get("stage_results") or {}
        for stage in [
            "apply_patch",
            "compile",
            "tests",
            "resource_consistency",
            "semantic_equivalence",
            "security_regression",
            "static_analysis",
        ]:
            payload = stages.get(stage)
            if isinstance(payload, dict) and payload.get("passed") is False:
                return stage
    return "unknown_validation_failure"


def candidate_validations_for(result: dict[str, Any]) -> list[dict[str, Any]]:
    validation = result.get("validation")
    if not isinstance(validation, dict):
        return []
    stage_results = validation.get("stage_results") or {}
    nested = [
        stage
        for key, stage in sorted(stage_results.items())
        if re.fullmatch(r"candidate_\d+", str(key))
        and isinstance(stage, dict)
        and isinstance(stage.get("stage_results"), dict)
    ]
    if nested:
        return nested
    if result.get("candidate_patches"):
        return [validation]
    return []


def stage_passed(validation: dict[str, Any], stage_name: str) -> bool:
    stage = (validation.get("stage_results") or {}).get(stage_name)
    return isinstance(stage, dict) and stage.get("passed") is True


def parse_systems(root: Path, raw: str) -> dict[str, list[Path]]:
    systems: dict[str, list[Path]] = defaultdict(list)
    current_name: str | None = None
    current_parts: list[str] = []
    for token in [part.strip() for part in raw.split(",") if part.strip()]:
        if "=" in token:
            if current_name is not None:
                systems[current_name].extend(parse_result_dirs(root, ",".join(current_parts)))
            current_name, first = token.split("=", 1)
            current_parts = [first]
        elif current_name is not None:
            current_parts.append(token)
        else:
            raise ValueError(f"system entry is missing NAME= prefix: {token}")
    if current_name is not None:
        systems[current_name].extend(parse_result_dirs(root, ",".join(current_parts)))
    return dict(systems)


def parse_result_dirs(root: Path, raw: str) -> list[Path]:
    dirs: list[Path] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        pattern = str((root / item).resolve()) if not Path(item).is_absolute() else item
        if any(ch in pattern for ch in "*?[]"):
            dirs.extend(sorted(Path(path).resolve() for path in glob.glob(pattern) if Path(path).is_dir()))
        else:
            dirs.append(Path(pattern).resolve())
    return dirs


def unique_results(results: Any) -> list[dict[str, Any]]:
    by_work_id: dict[str, dict[str, Any]] = {}
    unkeyed: list[dict[str, Any]] = []
    for result in results:
        work_id = result.get("work_id")
        if work_id:
            by_work_id[str(work_id)] = result
        else:
            unkeyed.append(result)
    return list(by_work_id.values()) + unkeyed


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
        "failure_category",
        "count",
        "rate_per_processed",
        "candidate_total",
        "processed",
        "validated",
        "applicable_failed",
        "nonapplicable_failed",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows({key: row.get(key) for key in fieldnames} for row in rows)


def write_markdown(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Failure Taxonomy",
        "",
        "| System | Failure category | Count | Rate |",
        "| --- | --- | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| {system} | `{failure_category}` | {count} | {rate} |".format(
                system=row["system"],
                failure_category=row["failure_category"],
                count=row["count"],
                rate=format_percent(row["rate_per_processed"]),
            )
        )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_latex(path: Path, rows: list[dict[str, Any]]) -> None:
    body = []
    for row in rows:
        if str(row["failure_category"]).startswith("stage:"):
            continue
        body.append(
            "        {system} & {category} & {count} & {rate} \\\\".format(
                system=latex_escape(row["system"]),
                category=latex_escape(row["failure_category"].replace("_", " ")),
                count=row["count"],
                rate=format_percent(row["rate_per_processed"]),
            )
        )
    latex = "\n".join(
        [
            "\\begin{table}[t]",
            "    \\centering",
            "    \\caption{Primary validation failure reasons.}",
            "    \\label{tab:failure-taxonomy}",
            "    \\footnotesize",
            "    \\begin{tabular}{llrr}",
            "        \\toprule",
            "        System & Primary failure & Count & Rate \\\\",
            "        \\midrule",
            *body,
            "        \\bottomrule",
            "    \\end{tabular}",
            "\\end{table}",
            "",
        ]
    )
    path.write_text(latex, encoding="utf-8")


def rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def format_percent(value: float) -> str:
    return f"{value * 100:.2f}%"


def latex_escape(value: str) -> str:
    return value.replace("_", "\\_").replace("%", "\\%").replace("&", "\\&")


if __name__ == "__main__":
    raise SystemExit(main())
