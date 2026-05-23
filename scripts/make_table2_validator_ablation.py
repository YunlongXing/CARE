"""Build Table 2: validator ablation over existing CARE/LLM-only candidates.

This script does not re-apply patches. It reinterprets the stage-level
validation records already written by ``run_oss50_llm_validation.py`` and
computes which candidates would have passed if selected validator gates were
disabled.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import re
from pathlib import Path
from typing import Any, Iterable


DEFAULT_CARE_DIR = "benchmarks/oss50/llm-validation-critical-only"
DEFAULT_LLM_ONLY_DIR = "benchmarks/oss50/llm-validation-critical-llm-only-shards/shard-*"
DEFAULT_OUTPUT_DIR = "benchmarks/oss50/paper-tables"

CHECKER_VARIANTS = [
    ("Full validator", ()),
    ("No resource checker", ("resource_consistency",)),
    ("No semantic checker", ("semantic_equivalence",)),
    ("No security checker", ("security_regression",)),
    (
        "No resource/semantic/security checkers",
        ("resource_consistency", "semantic_equivalence", "security_regression"),
    ),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--care-dir", default=DEFAULT_CARE_DIR)
    parser.add_argument("--llm-only-dir", default=DEFAULT_LLM_ONLY_DIR)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--review-dir",
        default=None,
        help="Optional Table 3 LLM-review directory for false-accept estimates.",
    )
    args = parser.parse_args(argv)

    root = Path.cwd().resolve()
    output_dir = (root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    review_index = load_review_index((root / args.review_dir).resolve()) if args.review_dir else {}

    rows: list[dict[str, Any]] = []
    rows.extend(
        summarize_system(
            "CARE",
            parse_result_dirs(root, args.care_dir),
            review_index=review_index,
        )
    )
    rows.extend(
        summarize_system(
            "LLM-only",
            parse_result_dirs(root, args.llm_only_dir),
            review_index=review_index,
        )
    )

    write_csv(output_dir / "table2_validator_ablation.csv", rows)
    write_markdown(output_dir / "table2_validator_ablation.md", rows)
    write_latex(output_dir / "table2_validator_ablation.tex", rows)
    (output_dir / "table2_validator_ablation.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"wrote Table 2 artifacts to {output_dir}")
    return 0


def summarize_system(
    system: str,
    result_dirs: list[Path],
    review_index: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates = load_candidates(result_dirs)
    full_passed_ids = {
        candidate["candidate_key"]
        for candidate in candidates
        if validation_passed(candidate["validation"], disabled=())
    }
    rows: list[dict[str, Any]] = []
    for variant, disabled in CHECKER_VARIANTS:
        disabled_set = set(disabled)
        passed = [
            candidate
            for candidate in candidates
            if validation_passed(candidate["validation"], disabled=disabled_set)
        ]
        extra = [
            candidate
            for candidate in passed
            if candidate["candidate_key"] not in full_passed_ids
        ]
        reviewed_false = [
            candidate
            for candidate in extra
            if is_false_accept(review_index.get(candidate["candidate_key"]))
        ]
        reviewed_extra = [
            candidate
            for candidate in extra
            if candidate["candidate_key"] in review_index
        ]
        rows.append(
            {
                "system": system,
                "variant": variant,
                "resource_checker": "off" if "resource_consistency" in disabled_set else "on",
                "semantic_checker": "off" if "semantic_equivalence" in disabled_set else "on",
                "security_checker": "off" if "security_regression" in disabled_set else "on",
                "generated_patches": len(candidates),
                "passed": len(passed),
                "pass_rate": rate(len(passed), len(candidates)),
                "extra_accepted_vs_full": len(extra),
                "extra_accept_rate": rate(len(extra), len(passed)),
                "llm_reviewed_extra": len(reviewed_extra),
                "llm_false_accepted": len(reviewed_false),
                "llm_false_accept_rate": rate(len(reviewed_false), len(reviewed_extra)),
            }
        )
    return rows


def load_candidates(result_dirs: list[Path]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for result_dir in result_dirs:
        for result in read_jsonl(result_dir / "results.jsonl"):
            patch_records = result.get("candidate_patches") or []
            validations = candidate_validations_for(result)
            for index, validation in enumerate(validations):
                patch_record = patch_records[index] if index < len(patch_records) else {}
                candidate_key = f"{result.get('work_id')}::candidate_{index}"
                if candidate_key in seen:
                    continue
                seen.add(candidate_key)
                candidates.append(
                    {
                        "candidate_key": candidate_key,
                        "work_id": result.get("work_id"),
                        "system": result.get("baseline") or "care",
                        "patch_record": patch_record,
                        "validation": validation,
                    }
                )
    return candidates


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


def validation_passed(validation: dict[str, Any], disabled: set[str]) -> bool:
    stage_results = validation.get("stage_results") or {}
    if not stage_results:
        return False
    for name, stage in stage_results.items():
        if name in disabled:
            continue
        if not isinstance(stage, dict) or stage.get("passed") is not True:
            return False
    return True


def load_review_index(review_dir: Path) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for filename in ["passed_patch_reviews.jsonl", "failed_patch_reviews.jsonl"]:
        for review in read_jsonl(review_dir / filename):
            key = review.get("candidate_key")
            if key:
                index[key] = review
    return index


def is_false_accept(review: dict[str, Any] | None) -> bool:
    if not review:
        return False
    verdict = str(review.get("verdict") or "").lower()
    if verdict in {"incorrect", "unsafe", "reject"}:
        return True
    for field in [
        "security_regression",
        "semantic_regression",
        "resource_lifecycle_regression",
    ]:
        if review.get(field) is True:
            return True
    return False


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


def rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "system",
        "variant",
        "resource_checker",
        "semantic_checker",
        "security_checker",
        "generated_patches",
        "passed",
        "pass_rate",
        "extra_accepted_vs_full",
        "extra_accept_rate",
        "llm_reviewed_extra",
        "llm_false_accepted",
        "llm_false_accept_rate",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def write_markdown(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Table 2: Validator Ablation",
        "",
        "| System | Variant | Resource | Semantic | Security | Generated | Passed | Pass rate | Extra accepted vs full | LLM false accepts | LLM false accept rate |",
        "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| {system} | {variant} | {resource_checker} | {semantic_checker} | {security_checker} | "
            "{generated_patches} | {passed} | {pass_rate} | {extra} | {false} | {false_rate} |".format(
                system=row["system"],
                variant=row["variant"],
                resource_checker=row["resource_checker"],
                semantic_checker=row["semantic_checker"],
                security_checker=row["security_checker"],
                generated_patches=row["generated_patches"],
                passed=row["passed"],
                pass_rate=format_percent(row["pass_rate"]),
                extra=row["extra_accepted_vs_full"],
                false=row["llm_false_accepted"],
                false_rate=format_percent(row["llm_false_accept_rate"]),
            )
        )
    lines.extend(
        [
            "",
            "Definitions:",
            "",
            "- Extra accepted vs full = patches accepted by the ablated validator but rejected by the full validator.",
            "- LLM false accept rate is populated when Table 3 review outputs are provided.",
        ]
    )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_latex(path: Path, rows: list[dict[str, Any]]) -> None:
    body = []
    for row in rows:
        body.append(
            "        {system} & {variant} & {r} & {s} & {sec} & {passed} & {rate} & {extra} & {false_rate} \\\\".format(
                system=latex_escape(row["system"]),
                variant=latex_escape(row["variant"]),
                r=row["resource_checker"],
                s=row["semantic_checker"],
                sec=row["security_checker"],
                passed=row["passed"],
                rate=format_percent(row["pass_rate"]),
                extra=row["extra_accepted_vs_full"],
                false_rate=format_percent(row["llm_false_accept_rate"]),
            )
        )
    latex = "\n".join(
        [
            "\\begin{table*}[t]",
            "    \\centering",
            "    \\caption{Validator ablation on critical OSS50 candidates.}",
            "    \\label{tab:validator-ablation}",
            "    \\begin{tabular}{lllllrrrr}",
            "        \\toprule",
            "        System & Variant & Res. & Sem. & Sec. & Pass & Pass rate & Extra & False rate \\\\",
            "        \\midrule",
            *body,
            "        \\bottomrule",
            "    \\end{tabular}",
            "\\end{table*}",
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
