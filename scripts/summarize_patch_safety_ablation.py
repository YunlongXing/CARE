"""Summarize validator ablations from one patch-safety benchmark run."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


VARIANTS = (
    ("Full CARE", frozenset()),
    ("Without resource checker", frozenset({"resource_consistency"})),
    ("Without semantic checker", frozenset({"semantic_equivalence"})),
    ("Without security checker", frozenset({"security_regression"})),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--system", default="care")
    parser.add_argument("--build-qualified", action="store_true")
    args = parser.parse_args(argv)

    results_dir = Path(args.results_dir).resolve()
    output_dir = Path(args.output_dir).resolve() if args.output_dir else results_dir
    results = read_jsonl(results_dir / "results.jsonl")
    if args.build_qualified:
        results = [result for result in results if build_was_executed(result, args.system)]
    rows = summarize(results, args.system)
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = "_build_qualified" if args.build_qualified else ""
    write_csv(output_dir / f"validator_ablation{suffix}.csv", rows)
    write_json(output_dir / f"validator_ablation{suffix}.json", {"rows": rows})
    write_markdown(output_dir / f"validator_ablation{suffix}.md", rows)
    print(f"summarized {len(results)} cases")
    return 0


def summarize(results: list[dict[str, Any]], system: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    full_accepted = {
        str(result["case_id"])
        for result in results
        if accepted_by(result, system, frozenset())
    }
    for name, disabled in VARIANTS:
        safe_total = unsafe_total = safe_accepted = unsafe_rejected = 0
        false_accepts = false_rejects = extra_false_accepts = 0
        for result in results:
            truth = result.get("ground_truth")
            accepted = accepted_by(result, system, disabled)
            if truth == "safe":
                safe_total += 1
                safe_accepted += int(accepted)
                false_rejects += int(not accepted)
            elif truth == "unsafe":
                unsafe_total += 1
                unsafe_rejected += int(not accepted)
                false_accepts += int(accepted)
                extra_false_accepts += int(
                    accepted and str(result["case_id"]) not in full_accepted
                )
        rows.append(
            {
                "variant": name,
                "disabled_stages": ",".join(sorted(disabled)) or "none",
                "safe_total": safe_total,
                "safe_accepted": safe_accepted,
                "safe_accept_rate": rate(safe_accepted, safe_total),
                "unsafe_total": unsafe_total,
                "unsafe_rejected": unsafe_rejected,
                "unsafe_rejection_rate": rate(unsafe_rejected, unsafe_total),
                "false_accepts": false_accepts,
                "false_rejects": false_rejects,
                "additional_false_accepts": extra_false_accepts,
            }
        )
    return rows


def accepted_by(result: dict[str, Any], system: str, disabled: frozenset[str]) -> bool:
    stages = ((result.get("systems") or {}).get(system) or {}).get("stage_results") or {}
    relevant = [
        stage
        for name, stage in stages.items()
        if name != "failure_taxonomy" and name not in disabled and isinstance(stage, dict)
    ]
    return bool(relevant) and all(stage.get("passed") is True for stage in relevant)


def build_was_executed(result: dict[str, Any], system: str) -> bool:
    stage = (((result.get("systems") or {}).get(system) or {}).get("stage_results") or {}).get("compile") or {}
    warnings = "\n".join(stage.get("warnings") or [])
    logs = "\n".join(stage.get("logs") or [])
    return "no build command provided" not in warnings and bool(logs)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else [])
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_markdown(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Validator ablation",
        "",
        "| Variant | Safe accepted | Unsafe rejected | False accepts | False rejects | Additional false accepts |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| {variant} | {safe_accepted}/{safe_total} ({safe_accept_rate:.1%}) | "
            "{unsafe_rejected}/{unsafe_total} ({unsafe_rejection_rate:.1%}) | "
            "{false_accepts} | {false_rejects} | {additional_false_accepts} |".format(**row)
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
