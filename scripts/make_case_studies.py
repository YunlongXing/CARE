"""Generate concise paper case studies from CARE/LLM-only results."""

from __future__ import annotations

import argparse
import glob
import json
import re
from pathlib import Path
from typing import Any


DEFAULT_CARE_DIR = "benchmarks/oss50/llm-validation-critical-only"
DEFAULT_LLM_ONLY_DIR = "benchmarks/oss50/llm-validation-critical-llm-only-shards/shard-*"
DEFAULT_REVIEW_DIR = "benchmarks/oss50/llm-review-critical"
DEFAULT_OUTPUT = "benchmarks/oss50/paper-tables/case_studies.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--care-dir", default=DEFAULT_CARE_DIR)
    parser.add_argument("--llm-only-dir", default=DEFAULT_LLM_ONLY_DIR)
    parser.add_argument("--review-dir", default=DEFAULT_REVIEW_DIR)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    root = Path.cwd().resolve()
    care_records = load_records("CARE", parse_result_dirs(root, args.care_dir), root)
    llm_records = load_records("LLM-only", parse_result_dirs(root, args.llm_only_dir), root)
    reviews = load_review_index((root / args.review_dir).resolve())

    case1 = select_care_success_vs_llm_failure(care_records, llm_records, reviews)
    case2 = select_ablation_false_accept(care_records, reviews)
    case3 = select_llm_only_apply_failure(llm_records, reviews)

    output = (root / args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_cases([case1, case2, case3]), encoding="utf-8")
    print(f"wrote case studies to {output}")
    return 0


def load_records(system: str, result_dirs: list[Path], root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for result_dir in result_dirs:
        for result in read_jsonl(result_dir / "results.jsonl"):
            patch_records = result.get("candidate_patches") or []
            validations = candidate_validations_for(result)
            for index, patch_record in enumerate(patch_records):
                validation = validations[index] if index < len(validations) else {}
                patch_path = resolve_patch_path(root, result_dir, patch_record)
                records.append(
                    {
                        "system": system,
                        "candidate_key": f"{result.get('work_id')}::candidate_{index}",
                        "work_id": result.get("work_id"),
                        "project_id": result.get("project_id"),
                        "kind": result.get("kind"),
                        "function": result.get("function"),
                        "file": result.get("file"),
                        "line": result.get("line"),
                        "description": result.get("description"),
                        "validation_passed": validation_passed(validation),
                        "selected": bool(patch_record.get("selected") or result.get("selected_patch")),
                        "validation": validation,
                        "validation_summary": result.get("validation_summary") or {},
                        "diff": read_text(patch_path),
                        "patch_path": str(patch_path) if patch_path else "",
                    }
                )
    return records


def select_care_success_vs_llm_failure(
    care_records: list[dict[str, Any]],
    llm_records: list[dict[str, Any]],
    reviews: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    llm_by_work = {record["work_id"]: record for record in llm_records}
    candidates = [
        record
        for record in care_records
        if record["validation_passed"]
        and record["selected"]
        and record["work_id"] in llm_by_work
        and not llm_by_work[record["work_id"]]["validation_passed"]
    ]
    record = min(candidates, key=lambda item: diff_line_count(item["diff"]))
    llm_record = llm_by_work[record["work_id"]]
    return {
        "title": "CARE succeeds where direct LLM-only fails",
        "claim": (
            "CARE produced a small validator-passing resource-lifecycle patch, while the direct LLM-only "
            "baseline failed on the same detector finding."
        ),
        "record": record,
        "comparison": llm_record,
        "review": reviews.get(("CARE", record["candidate_key"])),
    }


def select_ablation_false_accept(
    care_records: list[dict[str, Any]],
    reviews: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    candidates = []
    for record in care_records:
        if record["validation_passed"]:
            continue
        if passes_with_disabled(record["validation"], {"resource_consistency"}):
            candidates.append(record)
    record = min(candidates, key=lambda item: diff_line_count(item["diff"]))
    return {
        "title": "Resource checker prevents an unsafe acceptance",
        "claim": (
            "This patch applied cleanly and passed the non-resource stages, but the full validator rejected it "
            "because the resource-lifecycle issue remained or worsened."
        ),
        "record": record,
        "comparison": None,
        "review": reviews.get(("CARE", record["candidate_key"])),
    }


def select_llm_only_apply_failure(
    llm_records: list[dict[str, Any]],
    reviews: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    candidates = [
        record
        for record in llm_records
        if not record["validation_passed"]
        and not stage_passed(record["validation"], "apply_patch")
        and record["diff"].strip()
    ]
    record = min(candidates, key=lambda item: diff_line_count(item["diff"]))
    return {
        "title": "Direct LLM-only patch fails to apply",
        "claim": (
            "The direct LLM baseline often emitted plausible-looking diffs whose hunks did not match the real "
            "project context, explaining much of its lower apply rate."
        ),
        "record": record,
        "comparison": None,
        "review": reviews.get(("LLM-only", record["candidate_key"])),
    }


def render_cases(cases: list[dict[str, Any]]) -> str:
    lines = ["# CARE Paper Case Studies", ""]
    for index, case in enumerate(cases, start=1):
        record = case["record"]
        lines.extend(
            [
                f"## Case {index}: {case['title']}",
                "",
                case["claim"],
                "",
                f"- System: {record['system']}",
                f"- Project: `{record['project_id']}`",
                f"- Finding: `{record['work_id']}`",
                f"- Function/location: `{record['function']}` line `{record['line']}`",
                f"- Description: {record['description']}",
                f"- Patch path: `{record['patch_path']}`",
                "",
                "Validation evidence:",
                "",
                render_validation(record["validation"]),
                "",
            ]
        )
        review = case.get("review")
        if review:
            lines.extend(
                [
                    "LLM-assisted review:",
                    "",
                    f"- Verdict/category: `{review.get('verdict') or review.get('failure_category')}`",
                    f"- Reason: {review.get('reason')}",
                    "",
                ]
            )
        comparison = case.get("comparison")
        if comparison:
            lines.extend(
                [
                    "Baseline comparison:",
                    "",
                    f"- LLM-only passed validation: `{comparison['validation_passed']}`",
                    f"- LLM-only failed stages: `{', '.join((comparison.get('validation_summary') or {}).get('failed_stages') or [])}`",
                    "",
                ]
            )
        lines.extend(
            [
                "Diff excerpt:",
                "",
                "```diff",
                truncate_diff(record["diff"], max_lines=40),
                "```",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def render_validation(validation: dict[str, Any]) -> str:
    rows = []
    for name, stage in (validation.get("stage_results") or {}).items():
        if not isinstance(stage, dict):
            continue
        status = "pass" if stage.get("passed") else "fail"
        detail = ""
        if stage.get("counterexamples"):
            detail = str(stage["counterexamples"][0]).replace("\n", " ")[:220]
        elif stage.get("logs"):
            detail = str(stage["logs"][0]).replace("\n", " ")[:220]
        rows.append(f"- `{name}`: {status}. {detail}".rstrip())
    return "\n".join(rows) if rows else "- No validation detail available."


def load_review_index(review_dir: Path) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for filename in ["passed_patch_reviews.jsonl", "failed_patch_reviews.jsonl"]:
        for review in read_jsonl(review_dir / filename):
            system = review.get("system")
            key = review.get("candidate_key")
            if system and key:
                index[(system, key)] = review
    return index


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


def validation_passed(validation: dict[str, Any]) -> bool:
    stage_results = validation.get("stage_results") or {}
    return bool(stage_results) and all(
        isinstance(stage, dict) and stage.get("passed") is True
        for stage in stage_results.values()
    )


def passes_with_disabled(validation: dict[str, Any], disabled: set[str]) -> bool:
    stage_results = validation.get("stage_results") or {}
    if not stage_results:
        return False
    for name, stage in stage_results.items():
        if name in disabled:
            continue
        if not isinstance(stage, dict) or stage.get("passed") is not True:
            return False
    return True


def stage_passed(validation: dict[str, Any], name: str) -> bool:
    stage = (validation.get("stage_results") or {}).get(name)
    return isinstance(stage, dict) and stage.get("passed") is True


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


def resolve_patch_path(root: Path, result_dir: Path, patch_record: dict[str, Any]) -> Path | None:
    raw = patch_record.get("selected_path") or patch_record.get("path")
    if not raw:
        return None
    path = Path(raw)
    if path.exists():
        return path
    text = str(raw)
    marker = "benchmarks/"
    if marker in text:
        candidate = root / text[text.index(marker) :]
        if candidate.exists():
            return candidate
    if "patches/" in text:
        candidate = result_dir / text[text.index("patches/") :]
        if candidate.exists():
            return candidate
    return None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def read_text(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def diff_line_count(diff: str) -> int:
    return len([line for line in diff.splitlines() if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))])


def truncate_diff(diff: str, max_lines: int) -> str:
    lines = diff.splitlines()
    if len(lines) <= max_lines:
        return diff
    return "\n".join(lines[:max_lines] + ["...[truncated]..."])


if __name__ == "__main__":
    raise SystemExit(main())
