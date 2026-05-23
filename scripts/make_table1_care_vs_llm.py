"""Build Table 1: CARE vs direct LLM-only patching.

The script consumes two result directories produced by
``scripts/run_oss50_llm_validation.py``. The CARE directory is the normal
CARE pipeline. The LLM-only directory should be produced with
``--baseline llm-only`` so that both rows share the same queue and validator.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_CARE_DIR = "benchmarks/oss50/llm-validation-critical-only"
DEFAULT_LLM_ONLY_DIR = "benchmarks/oss50/llm-validation-critical-llm-only"
DEFAULT_OUTPUT_DIR = "benchmarks/oss50/paper-tables"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--care-dir", default=DEFAULT_CARE_DIR)
    parser.add_argument("--llm-only-dir", default=DEFAULT_LLM_ONLY_DIR)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)

    root = Path.cwd().resolve()
    care_dirs = parse_result_dirs(root, args.care_dir)
    llm_only_dirs = parse_result_dirs(root, args.llm_only_dir)
    output_dir = (root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = [
        summarize_system("CARE", care_dirs),
        summarize_system("LLM-only", llm_only_dirs),
    ]
    write_csv(output_dir / "table1_care_vs_llm.csv", rows)
    write_markdown(output_dir / "table1_care_vs_llm.md", rows)
    write_latex(output_dir / "table1_care_vs_llm.tex", rows)
    (output_dir / "table1_care_vs_llm.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"wrote Table 1 artifacts to {output_dir}")
    return 0


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


def summarize_system(name: str, result_dirs: list[Path]) -> dict[str, Any]:
    summaries = [read_json(result_dir / "summary.json") for result_dir in result_dirs]
    queues = [read_json(result_dir / "queue.json") for result_dir in result_dirs]
    results = unique_results(
        result
        for result_dir in result_dirs
        for result in read_jsonl(result_dir / "results.jsonl")
    )
    queue_items = {
        item.get("work_id"): item
        for queue in queues
        for item in (queue.get("items") or [])
        if item.get("work_id")
    }
    opportunities = len(queue_items) or sum(int(summary.get("queue_size") or 0) for summary in summaries)
    processed = len(results)
    generated = sum(len(result.get("candidate_patches") or []) for result in results)
    applicable = 0
    security_regressions = 0
    candidate_validations = 0
    missing_candidate_validations = 0
    status_counts: Counter[str] = Counter()

    for result in results:
        status_counts[str(result.get("status", "unknown"))] += 1
        validations = candidate_validations_for(result)
        candidate_validations += len(validations)
        candidate_count = len(result.get("candidate_patches") or [])
        if candidate_count > len(validations):
            missing_candidate_validations += candidate_count - len(validations)
        for validation in validations:
            if stage_passed(validation, "apply_patch"):
                applicable += 1
            if stage_failed(validation, "security_regression"):
                security_regressions += 1

    validation_passed = sum(1 for result in results if result.get("validation_passed"))
    selected_patches = sum(1 for result in results if result.get("selected_patch"))

    return {
        "system": name,
        "result_dir": ",".join(str(path) for path in result_dirs),
        "opportunities": opportunities,
        "processed": processed,
        "generated_patches": generated,
        "applicable_patches": applicable,
        "patch_apply_rate": rate(applicable, generated),
        "validation_passed": validation_passed,
        "validation_pass_rate": rate(validation_passed, generated),
        "validation_pass_rate_per_opportunity": rate(validation_passed, opportunities),
        "unsafe_regression_count": security_regressions,
        "unsafe_regression_rate": rate(security_regressions, max(applicable, 1)),
        "selected_patches": selected_patches,
        "candidate_validations": candidate_validations,
        "missing_candidate_validations": missing_candidate_validations,
        "status_counts": dict(sorted(status_counts.items())),
    }


def unique_results(results: Any) -> list[dict[str, Any]]:
    by_work_id: dict[str, dict[str, Any]] = {}
    unkeyed: list[dict[str, Any]] = []
    for result in results:
        work_id = result.get("work_id")
        if work_id:
            by_work_id[work_id] = result
        else:
            unkeyed.append(result)
    return list(by_work_id.values()) + unkeyed


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


def stage_failed(validation: dict[str, Any], stage_name: str) -> bool:
    stage = (validation.get("stage_results") or {}).get(stage_name)
    return isinstance(stage, dict) and stage.get("passed") is False


def rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


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
        "opportunities",
        "generated_patches",
        "applicable_patches",
        "patch_apply_rate",
        "validation_passed",
        "validation_pass_rate",
        "unsafe_regression_count",
        "unsafe_regression_rate",
        "processed",
        "selected_patches",
        "candidate_validations",
        "missing_candidate_validations",
        "result_dir",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def write_markdown(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Table 1: CARE vs LLM-only",
        "",
        "| System | Opportunities | Generated patches | Applicable patches | Patch apply rate | Validation passed | Validation pass rate | Unsafe regression count |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| {system} | {opportunities} | {generated_patches} | {applicable_patches} | "
            "{apply_rate} | {validation_passed} | {pass_rate} | {unsafe} |".format(
                system=row["system"],
                opportunities=row["opportunities"],
                generated_patches=row["generated_patches"],
                applicable_patches=row["applicable_patches"],
                apply_rate=format_percent(row["patch_apply_rate"]),
                validation_passed=row["validation_passed"],
                pass_rate=format_percent(row["validation_pass_rate"]),
                unsafe=row["unsafe_regression_count"],
            )
        )
    lines.extend(
        [
            "",
            "Definitions:",
            "",
            "- Patch apply rate = applicable patches / generated patches.",
            "- Validation pass rate = fully validated patches / generated patches.",
            "- Unsafe regression count = applicable candidates rejected by the security regression checker.",
        ]
    )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_latex(path: Path, rows: list[dict[str, Any]]) -> None:
    body = []
    for row in rows:
        body.append(
            "        {system} & {opportunities} & {generated} & {applicable} & "
            "{apply_rate} & {passed} & {pass_rate} & {unsafe} \\\\".format(
                system=latex_escape(row["system"]),
                opportunities=row["opportunities"],
                generated=row["generated_patches"],
                applicable=row["applicable_patches"],
                apply_rate=format_percent(row["patch_apply_rate"]),
                passed=row["validation_passed"],
                pass_rate=format_percent(row["validation_pass_rate"]),
                unsafe=row["unsafe_regression_count"],
            )
        )
    latex = "\n".join(
        [
            "\\begin{table}[t]",
            "    \\centering",
            "    \\caption{CARE vs. direct LLM-only patching on critical OSS50 opportunities.}",
            "    \\label{tab:care-vs-llm}",
            "    \\begin{tabular}{lrrrrrrr}",
            "        \\toprule",
            "        System & Opp. & Gen. & Apply & Apply rate & Valid & Valid rate & Unsafe \\\\",
            "        \\midrule",
            *body,
            "        \\bottomrule",
            "    \\end{tabular}",
            "\\end{table}",
            "",
        ]
    )
    path.write_text(latex, encoding="utf-8")


def format_percent(value: float) -> str:
    return f"{value * 100:.2f}%"


def latex_escape(value: str) -> str:
    return value.replace("_", "\\_").replace("%", "\\%").replace("&", "\\&")


if __name__ == "__main__":
    raise SystemExit(main())
