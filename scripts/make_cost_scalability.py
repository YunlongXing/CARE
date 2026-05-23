"""Summarize CARE cost and scalability metrics for the paper."""

from __future__ import annotations

import argparse
import csv
import glob
import json
import statistics
from pathlib import Path
from typing import Any


DEFAULT_SUITE_SUMMARY = "benchmarks/oss50/results/summary.json"
DEFAULT_CARE_DIR = "benchmarks/oss50/llm-validation-critical-only"
DEFAULT_LLM_ONLY_DIR = "benchmarks/oss50/llm-validation-critical-llm-only-shards/shard-*"
DEFAULT_REVIEW_DIR = "benchmarks/oss50/llm-review-critical"
DEFAULT_DETECTOR_REVIEW_DIR = "benchmarks/oss50/detector-precision-critical"
DEFAULT_OUTPUT_DIR = "benchmarks/oss50/paper-tables"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite-summary", default=DEFAULT_SUITE_SUMMARY)
    parser.add_argument("--care-dir", default=DEFAULT_CARE_DIR)
    parser.add_argument("--llm-only-dir", default=DEFAULT_LLM_ONLY_DIR)
    parser.add_argument("--review-dir", default=DEFAULT_REVIEW_DIR)
    parser.add_argument("--detector-review-dir", default=DEFAULT_DETECTOR_REVIEW_DIR)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)

    root = Path.cwd().resolve()
    output_dir = (root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = [
        dry_scan_row((root / args.suite_summary).resolve()),
        llm_validation_row("CARE critical patch/validation", parse_result_dirs(root, args.care_dir)),
        llm_validation_row("LLM-only critical patch/validation", parse_result_dirs(root, args.llm_only_dir)),
        review_row("LLM-assisted patch review", (root / args.review_dir).resolve()),
        detector_review_row("LLM-assisted detector precision review", (root / args.detector_review_dir).resolve()),
    ]
    write_csv(output_dir / "table5_cost_scalability.csv", rows)
    write_markdown(output_dir / "table5_cost_scalability.md", rows)
    write_latex(output_dir / "table5_cost_scalability.tex", rows)
    (output_dir / "table5_cost_scalability.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"wrote cost/scalability artifacts to {output_dir}")
    return 0


def dry_scan_row(summary_path: Path) -> dict[str, Any]:
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    summary = payload["summary"]
    projects = payload.get("projects") or []
    durations = [float(project.get("duration_seconds") or 0) for project in projects if project.get("duration_seconds")]
    return {
        "phase": "OSS50 dry scan",
        "projects": summary.get("projects", 0),
        "source_files": summary.get("source_files", 0),
        "functions": summary.get("functions", 0),
        "opportunities": summary.get("opportunities", 0),
        "generated_or_reviewed": summary.get("opportunities", 0),
        "passed_or_positive": "",
        "llm_calls": 0,
        "aggregate_seconds": round(float(summary.get("duration_seconds") or sum(durations)), 3),
        "mean_seconds": round(statistics.mean(durations), 3) if durations else 0,
        "median_seconds": round(statistics.median(durations), 3) if durations else 0,
        "p95_seconds": percentile(durations, 0.95),
        "notes": f"{summary.get('ok_projects', 0)} ok projects, {summary.get('failed_projects', 0)} failed projects",
    }


def llm_validation_row(phase: str, result_dirs: list[Path]) -> dict[str, Any]:
    results = [
        result
        for result_dir in result_dirs
        for result in read_jsonl(result_dir / "results.jsonl")
    ]
    # De-duplicate when globbed directories overlap.
    by_work_id = {result.get("work_id"): result for result in results if result.get("work_id")}
    results = list(by_work_id.values())
    durations = [float(result.get("duration_seconds") or 0) for result in results if result.get("duration_seconds")]
    generated = sum(len(result.get("candidate_patches") or []) for result in results)
    passed = sum(1 for result in results if result.get("validation_passed"))
    return {
        "phase": phase,
        "projects": len({result.get("project_id") for result in results}),
        "source_files": "",
        "functions": "",
        "opportunities": len(results),
        "generated_or_reviewed": generated,
        "passed_or_positive": passed,
        "llm_calls": generated,
        "aggregate_seconds": round(sum(durations), 3),
        "mean_seconds": round(statistics.mean(durations), 3) if durations else 0,
        "median_seconds": round(statistics.median(durations), 3) if durations else 0,
        "p95_seconds": percentile(durations, 0.95),
        "notes": "aggregate candidate runtime; wall-clock depends on parallelism and API rate limits",
    }


def review_row(phase: str, review_dir: Path) -> dict[str, Any]:
    passed = read_jsonl(review_dir / "passed_patch_reviews.jsonl")
    failed = read_jsonl(review_dir / "failed_patch_reviews.jsonl")
    reviewed = len(passed) + len(failed)
    batch_size = 12
    return {
        "phase": phase,
        "projects": len({record.get("project_id") for record in [*passed, *failed]}),
        "source_files": "",
        "functions": "",
        "opportunities": "",
        "generated_or_reviewed": reviewed,
        "passed_or_positive": len(passed),
        "llm_calls": len(passed) + ceil_div(len(failed), batch_size),
        "aggregate_seconds": "",
        "mean_seconds": "",
        "median_seconds": "",
        "p95_seconds": "",
        "notes": f"{len(passed)} passed-patch reviews plus {len(failed)} failed-patch classifications",
    }


def detector_review_row(phase: str, review_dir: Path) -> dict[str, Any]:
    reviews = read_jsonl(review_dir / "detector_precision_reviews.jsonl")
    positives = sum(1 for record in reviews if record.get("label") in {"true_positive", "likely_true_positive"})
    return {
        "phase": phase,
        "projects": len({record.get("project_id") for record in reviews}),
        "source_files": "",
        "functions": "",
        "opportunities": "",
        "generated_or_reviewed": len(reviews),
        "passed_or_positive": positives,
        "llm_calls": ceil_div(len(reviews), 10) if reviews else 0,
        "aggregate_seconds": "",
        "mean_seconds": "",
        "median_seconds": "",
        "p95_seconds": "",
        "notes": "sampled detector precision; exact token/cost telemetry was not recorded",
    }


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


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * q)))
    return round(ordered[index], 3)


def ceil_div(numerator: int, denominator: int) -> int:
    return (numerator + denominator - 1) // denominator if denominator else 0


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "phase",
        "projects",
        "source_files",
        "functions",
        "opportunities",
        "generated_or_reviewed",
        "passed_or_positive",
        "llm_calls",
        "aggregate_seconds",
        "mean_seconds",
        "median_seconds",
        "p95_seconds",
        "notes",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def write_markdown(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Table 5: Cost and Scalability",
        "",
        "| Phase | Projects | Files | Functions | Opportunities | Generated/reviewed | Passed/positive | LLM calls | Aggregate sec | Mean sec | Median sec | P95 sec | Notes |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            "| {phase} | {projects} | {source_files} | {functions} | {opportunities} | {generated_or_reviewed} | {passed_or_positive} | {llm_calls} | {aggregate_seconds} | {mean_seconds} | {median_seconds} | {p95_seconds} | {notes} |".format(
                **row
            )
        )
    lines.extend(
        [
            "",
            "Note: exact prompt/response token telemetry and API billing were not recorded in this prototype run; LLM call counts are reported as the reproducible cost proxy.",
        ]
    )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_latex(path: Path, rows: list[dict[str, Any]]) -> None:
    body = [
        "        {phase} & {projects} & {opportunities} & {generated_or_reviewed} & {passed_or_positive} & {llm_calls} & {aggregate_seconds} & {median_seconds} \\\\".format(
            **{**row, "phase": latex_escape(str(row["phase"]))},
        )
        for row in rows
    ]
    latex = "\n".join(
        [
            "\\begin{table*}[t]",
            "    \\centering",
            "    \\caption{Cost and scalability summary.}",
            "    \\label{tab:cost-scalability}",
            "    \\begin{tabular}{lrrrrrrr}",
            "        \\toprule",
            "        Phase & Projects & Opp. & Gen./Rev. & Pass/Pos. & LLM calls & Agg. sec & Med. sec \\\\",
            "        \\midrule",
            *body,
            "        \\bottomrule",
            "    \\end{tabular}",
            "\\end{table*}",
            "",
        ]
    )
    path.write_text(latex, encoding="utf-8")


def latex_escape(value: str) -> str:
    return value.replace("_", "\\_").replace("%", "\\%").replace("&", "\\&")


if __name__ == "__main__":
    raise SystemExit(main())
