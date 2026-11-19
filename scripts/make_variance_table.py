"""Summarize run-to-run variance for repeated LLM patch-generation runs."""

from __future__ import annotations

import argparse
import csv
import glob
import json
import statistics
from pathlib import Path
from typing import Any


DEFAULT_RUNS = "CARE=benchmarks/oss50/llm-validation-critical-only"
DEFAULT_OUTPUT_DIR = "benchmarks/oss50/paper-tables"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs",
        default=DEFAULT_RUNS,
        help=(
            "Semicolon-separated entries of NAME=DIR[,DIR...]. Multiple dirs under a name are treated "
            "as repeated runs for variance; globs are supported."
        ),
    )
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)

    root = Path.cwd().resolve()
    output_dir = (root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = [summarize_system(name, dirs) for name, dirs in parse_runs(root, args.runs).items()]
    write_csv(output_dir / "table_run_variance.csv", rows)
    write_markdown(output_dir / "table_run_variance.md", rows)
    write_latex(output_dir / "table_run_variance.tex", rows)
    (output_dir / "table_run_variance.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"wrote variance artifacts to {output_dir}")
    return 0


def summarize_system(name: str, dirs: list[Path]) -> dict[str, Any]:
    run_rows = [summarize_run(path) for path in dirs if (path / "results.jsonl").exists()]
    apply_rates = [row["apply_rate"] for row in run_rows]
    valid_rates = [row["valid_rate"] for row in run_rows]
    valid_counts = [row["valid"] for row in run_rows]
    return {
        "system": name,
        "runs": len(run_rows),
        "opportunities_mean": mean([row["opportunities"] for row in run_rows]),
        "generated_mean": mean([row["generated"] for row in run_rows]),
        "apply_rate_mean": mean(apply_rates),
        "apply_rate_std": stdev(apply_rates),
        "valid_rate_mean": mean(valid_rates),
        "valid_rate_std": stdev(valid_rates),
        "validated_mean": mean(valid_counts),
        "validated_std": stdev(valid_counts),
        "run_dirs": ",".join(str(path) for path in dirs),
        "note": "single run only; repeat this row with additional output dirs for variance" if len(run_rows) == 1 else "",
    }


def summarize_run(path: Path) -> dict[str, Any]:
    queue = read_json(path / "queue.json")
    results = read_jsonl(path / "results.jsonl")
    generated = sum(len(result.get("candidate_patches") or []) for result in results)
    apply_count = 0
    for result in results:
        for validation in candidate_validations_for(result):
            if stage_passed(validation, "apply_patch"):
                apply_count += 1
    valid = sum(1 for result in results if result.get("validation_passed"))
    opportunities = int(queue.get("queue_size") or len(results))
    return {
        "opportunities": opportunities,
        "generated": generated,
        "apply": apply_count,
        "valid": valid,
        "apply_rate": rate(apply_count, generated),
        "valid_rate": rate(valid, generated),
    }


def candidate_validations_for(result: dict[str, Any]) -> list[dict[str, Any]]:
    import re

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


def parse_runs(root: Path, raw: str) -> dict[str, list[Path]]:
    systems: dict[str, list[Path]] = {}
    for entry in [item.strip() for item in raw.split(";") if item.strip()]:
        if "=" not in entry:
            raise ValueError(f"run entry must be NAME=DIR[,DIR]: {entry}")
        name, dirs = entry.split("=", 1)
        systems[name] = parse_dirs(root, dirs)
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


def rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def mean(values: list[float | int]) -> float:
    return round(float(statistics.mean(values)), 6) if values else 0.0


def stdev(values: list[float | int]) -> float:
    return round(float(statistics.stdev(values)), 6) if len(values) > 1 else 0.0


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Run-to-run Variance",
        "",
        "| System | Runs | Apply rate mean | Apply std | Valid rate mean | Valid std | Validated mean | Note |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            "| {system} | {runs} | {apply_mean} | {apply_std} | {valid_mean} | {valid_std} | {validated_mean} | {note} |".format(
                system=row["system"],
                runs=row["runs"],
                apply_mean=format_percent(row["apply_rate_mean"]),
                apply_std=format_percent(row["apply_rate_std"]),
                valid_mean=format_percent(row["valid_rate_mean"]),
                valid_std=format_percent(row["valid_rate_std"]),
                validated_mean=row["validated_mean"],
                note=row["note"],
            )
        )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_latex(path: Path, rows: list[dict[str, Any]]) -> None:
    body = []
    for row in rows:
        body.append(
            "        {system} & {runs} & {apply_mean} $\\pm$ {apply_std} & {valid_mean} $\\pm$ {valid_std} & {validated_mean} \\\\".format(
                system=latex_escape(row["system"]),
                runs=row["runs"],
                apply_mean=format_percent(row["apply_rate_mean"]),
                apply_std=format_percent(row["apply_rate_std"]),
                valid_mean=format_percent(row["valid_rate_mean"]),
                valid_std=format_percent(row["valid_rate_std"]),
                validated_mean=row["validated_mean"],
            )
        )
    latex = "\n".join(
        [
            "\\begin{table}[t]",
            "    \\centering",
            "    \\caption{Run-to-run variance for repeated LLM runs.}",
            "    \\label{tab:run-variance}",
            "    \\footnotesize",
            "    \\begin{tabular}{llrrr}",
            "        \\toprule",
            "        System & Runs & Apply rate & Valid rate & Validated \\\\",
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
    return f"{value * 100:.2f}\\%"


def latex_escape(value: str) -> str:
    return value.replace("_", "\\_").replace("%", "\\%").replace("&", "\\&")


if __name__ == "__main__":
    raise SystemExit(main())
