"""Compare pre/post-upgrade CARE validation result directories."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True, help="Original results directory")
    parser.add_argument("--after", required=True, help="Post-upgrade results directory")
    parser.add_argument("--output", required=True, help="Markdown table output path")
    args = parser.parse_args(argv)

    before = summarize(Path(args.before))
    after = summarize(Path(args.after))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_markdown(before, after), encoding="utf-8")
    print(output)
    return 0


def summarize(result_dir: Path) -> dict[str, Any]:
    results = read_jsonl(result_dir / "results.jsonl")
    summary_path = result_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    generated = sum(len(result.get("candidate_patches") or []) for result in results)
    applicable = sum(1 for result in results if first_candidate_applied(result))
    passed = sum(1 for result in results if result.get("validation_passed"))
    unsafe = sum(1 for result in results if has_unsafe_regression(result))
    return {
        "directory": str(result_dir),
        "queue_size": summary.get("queue_size", 0),
        "processed": len(results),
        "pending": summary.get("pending", 0),
        "generated": generated,
        "applicable": applicable,
        "passed": passed,
        "unsafe_regressions": unsafe,
        "apply_rate": rate(applicable, generated),
        "pass_rate": rate(passed, generated),
        "failed_stage_counts": summary.get("failed_stage_counts", {}),
    }


def first_candidate_applied(result: dict[str, Any]) -> bool:
    validation = result.get("validation") or {}
    stages = validation.get("stage_results") or {}
    candidate_stages = [
        value.get("stage_results", {})
        for key, value in stages.items()
        if key.startswith("candidate_") and isinstance(value, dict)
    ]
    if candidate_stages:
        return any((stages.get("apply_patch") or {}).get("passed") for stages in candidate_stages)
    return bool((stages.get("apply_patch") or {}).get("passed"))


def has_unsafe_regression(result: dict[str, Any]) -> bool:
    validation = result.get("validation") or {}
    failed_stages = set(collect_failed_stages(validation.get("stage_results") or {}))
    if failed_stages & {"resource_consistency", "security_regression", "semantic_equivalence"}:
        return True
    haystack = "\n".join(str(item) for item in validation.get("counterexamples") or []).lower()
    markers = [
        "double free",
        "double close",
        "double unlock",
        "missing cleanup",
        "null_checks decreased",
        "bounds_checks decreased",
    ]
    return any(marker in haystack for marker in markers)


def collect_failed_stages(stage_results: dict[str, Any]) -> list[str]:
    failed: list[str] = []
    for name, stage in stage_results.items():
        if not isinstance(stage, dict):
            continue
        if stage.get("passed") is False:
            failed.append(name)
        nested = stage.get("stage_results")
        if isinstance(nested, dict):
            failed.extend(collect_failed_stages(nested))
    return failed


def rate(numerator: int, denominator: int) -> float:
    return round(100.0 * numerator / denominator, 2) if denominator else 0.0


def render_markdown(before: dict[str, Any], after: dict[str, Any]) -> str:
    rows = [
        ("Queue size", before["queue_size"], after["queue_size"], after["queue_size"] - before["queue_size"]),
        ("Processed", before["processed"], after["processed"], after["processed"] - before["processed"]),
        ("Generated candidates", before["generated"], after["generated"], after["generated"] - before["generated"]),
        ("Applicable patches", before["applicable"], after["applicable"], after["applicable"] - before["applicable"]),
        ("Validation passed", before["passed"], after["passed"], after["passed"] - before["passed"]),
        (
            "Unsafe regressions flagged",
            before["unsafe_regressions"],
            after["unsafe_regressions"],
            after["unsafe_regressions"] - before["unsafe_regressions"],
        ),
        ("Patch apply rate", f"{before['apply_rate']}%", f"{after['apply_rate']}%", f"{after['apply_rate'] - before['apply_rate']:.2f} pp"),
        ("Validation pass rate", f"{before['pass_rate']}%", f"{after['pass_rate']}%", f"{after['pass_rate'] - before['pass_rate']:.2f} pp"),
    ]
    lines = [
        "# CARE Upgrade Comparison",
        "",
        f"- Before: `{before['directory']}`",
        f"- After: `{after['directory']}`",
        "",
        "| Metric | Before | After | Delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    for metric, old, new, delta in rows:
        lines.append(f"| {metric} | {old} | {new} | {delta} |")
    lines.extend(
        [
            "",
            "## Failed Stage Counts",
            "",
            "| Stage | Before | After | Delta |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    stage_names = sorted(set(before["failed_stage_counts"]) | set(after["failed_stage_counts"]))
    for stage in stage_names:
        old = before["failed_stage_counts"].get(stage, 0)
        new = after["failed_stage_counts"].get(stage, 0)
        lines.append(f"| {stage} | {old} | {new} | {new - old} |")
    return "\n".join(lines).rstrip() + "\n"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
