"""Summarize security relevance and CWE-style impact for validated patches."""

from __future__ import annotations

import argparse
import csv
import glob
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DEFAULT_SYSTEMS = "CARE=benchmarks/oss50/llm-validation-critical-only"
DEFAULT_OUTPUT_DIR = "benchmarks/oss50/paper-tables"


CWE_MAP = {
    "resource_imbalance": ("CWE-772/CWE-415", "missing release, leak, double release, or inconsistent ownership"),
    "resource_early_return": ("CWE-772", "early return may bypass cleanup"),
    "goto_cleanup_consolidation": ("CWE-703/CWE-772", "error path cleanup and exceptional-condition handling"),
    "goto_redundant_error_block": ("CWE-703", "inconsistent error handling or cleanup duplication"),
    "duplicate_check": ("CWE-20", "input validation semantics"),
    "security_smell": ("CWE-676/CWE-252", "unsafe API or unchecked return value"),
    "dead_code": ("CWE-561", "unreachable code may hide stale cleanup or diagnostics"),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--systems", default=DEFAULT_SYSTEMS)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)

    root = Path.cwd().resolve()
    output_dir = (root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    systems = parse_systems(root, args.systems)
    patch_rows: list[dict[str, Any]] = []
    for system, dirs in systems.items():
        patch_rows.extend(validated_patch_rows(system, dirs))
    summary_rows = summarize(patch_rows)

    write_csv(output_dir / "table_security_impact_patches.csv", patch_rows)
    write_csv(output_dir / "table_security_impact_summary.csv", summary_rows)
    write_markdown(output_dir / "table_security_impact.md", summary_rows, patch_rows)
    write_latex(output_dir / "table_security_impact.tex", summary_rows)
    (output_dir / "table_security_impact.json").write_text(
        json.dumps({"summary": summary_rows, "patches": patch_rows}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"wrote security impact artifacts to {output_dir}")
    return 0


def validated_patch_rows(system: str, result_dirs: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result_dir in result_dirs:
        for result in read_jsonl(result_dir / "results.jsonl"):
            if not result.get("validation_passed") or not result.get("selected_patch"):
                continue
            kind = str(result.get("kind") or "unknown")
            cwe, impact = CWE_MAP.get(kind, ("CWE-Other", "security-relevant maintainability"))
            validation = result.get("validation") or {}
            patch_record = selected_patch_record(result)
            diff_path = resolve_patch_path(result_dir, patch_record)
            diff = diff_path.read_text(encoding="utf-8", errors="replace") if diff_path else ""
            rows.append(
                {
                    "system": system,
                    "work_id": result.get("work_id"),
                    "project_id": result.get("project_id"),
                    "kind": kind,
                    "severity": result.get("severity"),
                    "cwe": cwe,
                    "impact": impact,
                    "function": result.get("function"),
                    "file": result.get("file"),
                    "line": result.get("line"),
                    "diff_added_lines": count_diff_lines(diff, "+"),
                    "diff_removed_lines": count_diff_lines(diff, "-"),
                    "resource_checker_passed": stage_passed(validation, "resource_consistency"),
                    "semantic_checker_passed": stage_passed(validation, "semantic_equivalence"),
                    "security_checker_passed": stage_passed(validation, "security_regression"),
                    "patch_path": str(diff_path) if diff_path else "",
                }
            )
    return rows


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: Counter[tuple[str, str, str]] = Counter()
    line_delta: defaultdict[tuple[str, str, str], list[int]] = defaultdict(list)
    for row in rows:
        key = (row["system"], row["kind"], row["cwe"])
        counts[key] += 1
        line_delta[key].append(int(row["diff_added_lines"]) + int(row["diff_removed_lines"]))
    summary = []
    for (system, kind, cwe), count in sorted(counts.items()):
        deltas = line_delta[(system, kind, cwe)]
        summary.append(
            {
                "system": system,
                "kind": kind,
                "cwe": cwe,
                "validated_patches": count,
                "median_changed_lines": median(deltas),
                "impact": CWE_MAP.get(kind, ("", "security-relevant maintainability"))[1],
            }
        )
    return summary


def selected_patch_record(result: dict[str, Any]) -> dict[str, Any]:
    for record in result.get("candidate_patches") or []:
        if record.get("selected"):
            return record
    return (result.get("candidate_patches") or [{}])[0]


def resolve_patch_path(result_dir: Path, patch_record: dict[str, Any]) -> Path | None:
    raw = patch_record.get("selected_path") or patch_record.get("path")
    if not raw:
        return None
    path = Path(raw)
    if path.exists():
        return path
    marker = f"{Path('patches')}/"
    raw_text = str(raw)
    if marker in raw_text:
        suffix = raw_text.split(marker, 1)[1]
        candidate = result_dir / "patches" / suffix
        if candidate.exists():
            return candidate
    candidate = result_dir / raw
    if candidate.exists():
        return candidate
    return None


def count_diff_lines(diff: str, prefix: str) -> int:
    return sum(
        1
        for line in diff.splitlines()
        if line.startswith(prefix) and not line.startswith(prefix * 3)
    )


def stage_passed(validation: dict[str, Any], stage_name: str) -> bool:
    stage = (validation.get("stage_results") or {}).get(stage_name)
    return isinstance(stage, dict) and stage.get("passed") is True


def median(values: list[int]) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    mid = len(values) // 2
    if len(values) % 2:
        return float(values[mid])
    return round((values[mid - 1] + values[mid]) / 2.0, 2)


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


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(path: Path, summary_rows: list[dict[str, Any]], patch_rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Security Impact Summary",
        "",
        "| System | Kind | CWE | Validated patches | Median changed lines | Impact |",
        "| --- | --- | --- | ---: | ---: | --- |",
    ]
    for row in summary_rows:
        lines.append(
            "| {system} | `{kind}` | {cwe} | {validated_patches} | {median_changed_lines} | {impact} |".format(**row)
        )
    lines.extend(["", "## Validated Patch Inventory", ""])
    for row in patch_rows:
        lines.append(
            "- {system} `{work_id}` `{cwe}` {project_id}:{line} ({impact})".format(**row)
        )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_latex(path: Path, rows: list[dict[str, Any]]) -> None:
    body = []
    for row in rows:
        body.append(
            "        {system} & {kind} & {cwe} & {count} & {lines} \\\\".format(
                system=latex_escape(row["system"]),
                kind=latex_escape(row["kind"].replace("_", " ")),
                cwe=latex_escape(row["cwe"]),
                count=row["validated_patches"],
                lines=row["median_changed_lines"],
            )
        )
    latex = "\n".join(
        [
            "\\begin{table}[t]",
            "    \\centering",
            "    \\caption{Security relevance of validator-accepted patches.}",
            "    \\label{tab:security-impact}",
            "    \\footnotesize",
            "    \\begin{tabular}{lllrr}",
            "        \\toprule",
            "        System & Kind & CWE class & Patches & Med. changed lines \\\\",
            "        \\midrule",
            *body,
            "        \\bottomrule",
            "    \\end{tabular}",
            "\\end{table}",
            "",
        ]
    )
    path.write_text(latex, encoding="utf-8")


def latex_escape(value: str) -> str:
    return value.replace("_", "\\_").replace("%", "\\%").replace("&", "\\&")


if __name__ == "__main__":
    raise SystemExit(main())
