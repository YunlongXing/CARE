"""Run LLM-assisted patch review and build Table 3.

The reviewer is intentionally framed as a simulated expert reviewer. It audits
patches that passed validation for likely correctness, and classifies failed
candidates into failure categories using the patch and validator evidence.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from care.llm.client import LLMClient


DEFAULT_SYSTEMS = (
    "CARE=benchmarks/oss50/llm-validation-critical-only,"
    "LLM-only=benchmarks/oss50/llm-validation-critical-llm-only-shards/shard-*"
)
DEFAULT_OUTPUT_DIR = "benchmarks/oss50/llm-review-critical"
DEFAULT_TABLE_DIR = "benchmarks/oss50/paper-tables"

FAILURE_CATEGORIES = [
    "apply_failure",
    "compile_or_test_failure",
    "resource_lifecycle_still_unsafe",
    "semantic_change",
    "security_regression",
    "overbroad_or_unrelated_patch",
    "detector_false_positive",
    "llm_api_or_empty_patch",
    "validator_infrastructure_failure",
    "unclear",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--systems", default=DEFAULT_SYSTEMS)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--table-dir", default=DEFAULT_TABLE_DIR)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--max-passed", type=int, default=None)
    parser.add_argument("--max-failed", type=int, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--skip-llm", action="store_true", help="Only rebuild tables from existing review JSONL.")
    args = parser.parse_args(argv)

    root = Path.cwd().resolve()
    output_dir = (root / args.output_dir).resolve()
    table_dir = (root / args.table_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    table_dir.mkdir(parents=True, exist_ok=True)

    systems = parse_systems(root, args.systems)
    records = load_records(systems)
    passed = [record for record in records if record["validation_passed"]]
    failed = [record for record in records if not record["validation_passed"]]
    if args.max_passed is not None:
        passed = passed[: args.max_passed]
    if args.max_failed is not None:
        failed = failed[: args.max_failed]

    if not args.skip_llm:
        client = LLMClient.from_env(require_real=True)
        review_passed_patches(
            client=client,
            records=passed,
            output_path=output_dir / "passed_patch_reviews.jsonl",
            resume=args.resume,
        )
        review_failed_patches(
            client=client,
            records=failed,
            output_path=output_dir / "failed_patch_reviews.jsonl",
            batch_size=max(1, args.batch_size),
            resume=args.resume,
        )

    write_table3(
        output_dir=output_dir,
        table_dir=table_dir,
        systems=sorted(systems),
    )
    print(f"wrote LLM review and Table 3 artifacts to {output_dir} and {table_dir}")
    return 0


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


def load_records(systems: dict[str, list[Path]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for system, dirs in systems.items():
        for result_dir in dirs:
            for result in read_jsonl(result_dir / "results.jsonl"):
                patch_records = result.get("candidate_patches") or []
                validations = candidate_validations_for(result)
                for index, patch_record in enumerate(patch_records):
                    validation = validations[index] if index < len(validations) else {}
                    candidate_key = f"{result.get('work_id')}::candidate_{index}"
                    system_key = f"{system}::{candidate_key}"
                    if system_key in seen:
                        continue
                    seen.add(system_key)
                    patch_path = resolve_patch_path(result_dir, patch_record)
                    records.append(
                        {
                            "system": system,
                            "candidate_key": candidate_key,
                            "work_id": result.get("work_id"),
                            "project_id": result.get("project_id"),
                            "severity": result.get("severity"),
                            "kind": result.get("kind"),
                            "function": result.get("function"),
                            "file": result.get("file"),
                            "line": result.get("line"),
                            "description": result.get("description"),
                            "validation_passed": validation_passed(validation),
                            "selected": bool(patch_record.get("selected") or result.get("selected_patch")),
                            "patch_path": str(patch_path) if patch_path else "",
                            "diff": read_text(patch_path) if patch_path else "",
                            "validation_summary": result.get("validation_summary") or {},
                            "validation": validation,
                        }
                    )
    return records


def resolve_patch_path(result_dir: Path, patch_record: dict[str, Any]) -> Path | None:
    raw = patch_record.get("selected_path") or patch_record.get("path")
    if not raw:
        return None
    path = Path(raw)
    if path.exists():
        return path
    candidate = result_dir / "patches" / Path(raw).name
    return candidate if candidate.exists() else None


def read_text(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def review_passed_patches(
    client: LLMClient,
    records: list[dict[str, Any]],
    output_path: Path,
    resume: bool,
) -> None:
    done = completed_keys(output_path) if resume else set()
    for index, record in enumerate(records, start=1):
        if record["candidate_key"] in done:
            continue
        prompt = build_passed_review_prompt(record)
        try:
            review = call_json(client, prompt)
        except Exception as exc:
            review = {
                "verdict": "uncertain",
                "security_regression": False,
                "semantic_regression": False,
                "resource_lifecycle_regression": False,
                "reason": f"LLM review failed for this patch: {exc}",
                "confidence": 0.0,
            }
        normalized = normalize_passed_review(record, review)
        append_jsonl(output_path, normalized)
        print(f"reviewed passed patch {index}/{len(records)} {record['system']} {record['work_id']}", flush=True)


def review_failed_patches(
    client: LLMClient,
    records: list[dict[str, Any]],
    output_path: Path,
    batch_size: int,
    resume: bool,
) -> None:
    done = completed_keys(output_path) if resume else set()
    pending = [record for record in records if record["candidate_key"] not in done]
    for offset in range(0, len(pending), batch_size):
        batch = pending[offset : offset + batch_size]
        prompt = build_failed_review_prompt(batch)
        try:
            payload = call_json(client, prompt)
            reviews = payload.get("reviews") if isinstance(payload, dict) else payload
            if not isinstance(reviews, list):
                reviews = []
            by_key = {
                str(review.get("candidate_key")): review
                for review in reviews
                if isinstance(review, dict) and review.get("candidate_key")
            }
        except Exception:
            by_key = {}
        for record in batch:
            review = by_key.get(record["candidate_key"]) or heuristic_failed_review(record)
            append_jsonl(output_path, normalize_failed_review(record, review))
        print(
            f"reviewed failed patches {min(offset + batch_size, len(pending))}/{len(pending)}",
            flush=True,
        )


def build_passed_review_prompt(record: dict[str, Any]) -> str:
    payload = review_record_payload(record, include_diff_chars=6000)
    return (
        "You are simulating a careful senior C/C++ security reviewer for a research artifact.\n"
        "Review the patch that already passed the automated validator. Judge whether the patch is truly "
        "behavior-preserving and security-preserving based on the opportunity, diff, and validation summary.\n\n"
        "Return JSON only with this schema:\n"
        "{\n"
        '  "verdict": "correct|likely_correct|incorrect|uncertain",\n'
        '  "security_regression": true|false,\n'
        '  "semantic_regression": true|false,\n'
        '  "resource_lifecycle_regression": true|false,\n'
        '  "reason": "short explanation",\n'
        '  "confidence": 0.0\n'
        "}\n\n"
        f"{json.dumps(payload, indent=2, sort_keys=True)}"
    )


def build_failed_review_prompt(records: list[dict[str, Any]]) -> str:
    payload = [review_record_payload(record, include_diff_chars=2500) for record in records]
    return (
        "You are simulating a careful senior C/C++ security reviewer for a research artifact.\n"
        "Classify why each patch candidate failed validation, and judge whether accepting it would be safe.\n\n"
        "Allowed failure_category values:\n"
        f"{', '.join(FAILURE_CATEGORIES)}\n\n"
        "Return JSON only with this schema:\n"
        "{\n"
        '  "reviews": [\n'
        "    {\n"
        '      "candidate_key": "string",\n'
        '      "verdict": "correct|likely_correct|incorrect|uncertain",\n'
        '      "failure_category": "one allowed value",\n'
        '      "security_regression": true|false,\n'
        '      "semantic_regression": true|false,\n'
        '      "resource_lifecycle_regression": true|false,\n'
        '      "reason": "short explanation",\n'
        '      "confidence": 0.0\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        f"{json.dumps(payload, indent=2, sort_keys=True)}"
    )


def review_record_payload(record: dict[str, Any], include_diff_chars: int) -> dict[str, Any]:
    validation = record.get("validation") or {}
    return {
        "system": record["system"],
        "candidate_key": record["candidate_key"],
        "work_id": record["work_id"],
        "project_id": record["project_id"],
        "kind": record["kind"],
        "function": record["function"],
        "file": record["file"],
        "line": record["line"],
        "description": record["description"],
        "validation_passed": record["validation_passed"],
        "failed_stages": (record.get("validation_summary") or {}).get("failed_stages") or [],
        "logs_head": (record.get("validation_summary") or {}).get("logs_head") or [],
        "counterexamples_head": (record.get("validation_summary") or {}).get("counterexamples_head") or [],
        "stage_results": compact_stage_results(validation),
        "diff": truncate(record.get("diff") or "", include_diff_chars),
    }


def compact_stage_results(validation: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for name, stage in (validation.get("stage_results") or {}).items():
        if not isinstance(stage, dict):
            continue
        compact[name] = {
            "passed": stage.get("passed"),
            "warnings": stage.get("warnings", [])[:3],
            "logs": stage.get("logs", [])[:3],
            "counterexamples": stage.get("counterexamples", [])[:3],
        }
    return compact


def call_json(client: LLMClient, prompt: str) -> Any:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            raw = client.complete(prompt, temperature=0.0)
            return parse_json_response(raw)
        except Exception as exc:
            last_error = exc
            time.sleep(2 + attempt * 3)
    raise RuntimeError(f"LLM review failed: {last_error}")


def parse_json_response(raw: str) -> Any:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = min([idx for idx in [text.find("{"), text.find("[")] if idx >= 0], default=-1)
        end = max(text.rfind("}"), text.rfind("]"))
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def normalize_passed_review(record: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    return normalize_review(record, review, default_category="passed_patch")


def normalize_failed_review(record: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    category = review.get("failure_category") or heuristic_failure_category(record)
    if category not in FAILURE_CATEGORIES:
        category = "unclear"
    return normalize_review(record, {**review, "failure_category": category}, default_category=category)


def normalize_review(
    record: dict[str, Any],
    review: dict[str, Any],
    default_category: str,
) -> dict[str, Any]:
    verdict = str(review.get("verdict") or "uncertain").lower()
    if verdict not in {"correct", "likely_correct", "incorrect", "uncertain", "unsafe", "reject"}:
        verdict = "uncertain"
    return {
        "system": record["system"],
        "candidate_key": record["candidate_key"],
        "work_id": record["work_id"],
        "project_id": record["project_id"],
        "kind": record["kind"],
        "function": record["function"],
        "file": record["file"],
        "line": record["line"],
        "validation_passed": record["validation_passed"],
        "verdict": verdict,
        "failure_category": review.get("failure_category") or default_category,
        "security_regression": bool(review.get("security_regression")),
        "semantic_regression": bool(review.get("semantic_regression")),
        "resource_lifecycle_regression": bool(review.get("resource_lifecycle_regression")),
        "reason": str(review.get("reason") or "")[:1000],
        "confidence": float_or_zero(review.get("confidence")),
    }


def heuristic_failed_review(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "verdict": "uncertain",
        "failure_category": heuristic_failure_category(record),
        "security_regression": False,
        "semantic_regression": False,
        "resource_lifecycle_regression": False,
        "reason": "LLM review did not return a usable item for this candidate; category inferred from validator logs.",
        "confidence": 0.25,
    }


def heuristic_failure_category(record: dict[str, Any]) -> str:
    text = json.dumps(record.get("validation_summary") or {}, sort_keys=True).lower()
    failed = set((record.get("validation_summary") or {}).get("failed_stages") or [])
    if "restore_clean_project" in text or "git stash create" in text:
        return "validator_infrastructure_failure"
    if "apply_patch" in text or "patch does not apply" in text or "empty patch" in text:
        return "apply_failure"
    if "compile" in failed or "tests" in failed:
        return "compile_or_test_failure"
    if "resource_consistency" in text:
        return "resource_lifecycle_still_unsafe"
    if "semantic_equivalence" in text:
        return "semantic_change"
    if "security_regression" in text:
        return "security_regression"
    if "llm patch generation failed" in text or "llm output was not" in text:
        return "llm_api_or_empty_patch"
    return "unclear"


def write_table3(output_dir: Path, table_dir: Path, systems: list[str]) -> None:
    passed_reviews = read_jsonl(output_dir / "passed_patch_reviews.jsonl")
    failed_reviews = read_jsonl(output_dir / "failed_patch_reviews.jsonl")

    correctness_rows = build_correctness_rows(systems, passed_reviews)
    failure_rows = build_failure_rows(systems, failed_reviews)

    write_table3_correctness_csv(table_dir / "table3_patch_correctness.csv", correctness_rows)
    write_table3_failure_csv(table_dir / "table3_failure_reasons.csv", failure_rows)
    write_table3_markdown(table_dir / "table3_llm_review.md", correctness_rows, failure_rows)
    write_table3_latex(table_dir / "table3_llm_review.tex", correctness_rows, failure_rows)
    (table_dir / "table3_patch_correctness.json").write_text(
        json.dumps(correctness_rows, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (table_dir / "table3_failure_reasons.json").write_text(
        json.dumps(failure_rows, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def build_correctness_rows(systems: list[str], reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_system: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for review in reviews:
        by_system[review.get("system", "unknown")].append(review)
    rows = []
    for system in systems:
        items = by_system.get(system, [])
        counts = Counter(item.get("verdict") for item in items)
        unsafe = sum(1 for item in items if is_review_unsafe(item))
        reviewed = len(items)
        rows.append(
            {
                "system": system,
                "reviewed_patches": reviewed,
                "correct": counts.get("correct", 0),
                "likely_correct": counts.get("likely_correct", 0),
                "incorrect": counts.get("incorrect", 0) + counts.get("unsafe", 0) + counts.get("reject", 0),
                "uncertain": counts.get("uncertain", 0),
                "strict_correctness": rate(counts.get("correct", 0), reviewed),
                "lenient_correctness": rate(counts.get("correct", 0) + counts.get("likely_correct", 0), reviewed),
                "unsafe_regressions": unsafe,
            }
        )
    return rows


def build_failure_rows(systems: list[str], reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_system: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for review in reviews:
        by_system[review.get("system", "unknown")].append(review)
    rows = []
    for system in systems:
        items = by_system.get(system, [])
        counts = Counter(item.get("failure_category") or "unclear" for item in items)
        row = {"system": system, "failed_patches": len(items)}
        for category in FAILURE_CATEGORIES:
            row[category] = counts.get(category, 0)
        rows.append(row)
    return rows


def write_table3_correctness_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "system",
        "reviewed_patches",
        "correct",
        "likely_correct",
        "incorrect",
        "uncertain",
        "strict_correctness",
        "lenient_correctness",
        "unsafe_regressions",
    ]
    write_csv(path, rows, fieldnames)


def write_table3_failure_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = ["system", "failed_patches", *FAILURE_CATEGORIES]
    write_csv(path, rows, fieldnames)


def write_table3_markdown(
    path: Path,
    correctness_rows: list[dict[str, Any]],
    failure_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# Table 3: LLM-Assisted Patch Review",
        "",
        "## Passed Patch Correctness",
        "",
        "| System | Reviewed patches | Correct | Likely correct | Incorrect | Uncertain | Strict correctness | Lenient correctness | Unsafe regressions |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in correctness_rows:
        lines.append(
            "| {system} | {reviewed_patches} | {correct} | {likely_correct} | {incorrect} | "
            "{uncertain} | {strict} | {lenient} | {unsafe} |".format(
                system=row["system"],
                reviewed_patches=row["reviewed_patches"],
                correct=row["correct"],
                likely_correct=row["likely_correct"],
                incorrect=row["incorrect"],
                uncertain=row["uncertain"],
                strict=format_percent(row["strict_correctness"]),
                lenient=format_percent(row["lenient_correctness"]),
                unsafe=row["unsafe_regressions"],
            )
        )
    lines.extend(
        [
            "",
            "## Failure Reason Classification",
            "",
            "| System | Failed patches | Apply | Compile/test | Resource unsafe | Semantic change | Security regression | Overbroad | Detector FP | LLM/API | Validator infra | Unclear |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in failure_rows:
        lines.append(
            "| {system} | {failed_patches} | {apply_failure} | {compile_or_test_failure} | "
            "{resource_lifecycle_still_unsafe} | {semantic_change} | {security_regression} | "
            "{overbroad_or_unrelated_patch} | {detector_false_positive} | {llm_api_or_empty_patch} | "
            "{validator_infrastructure_failure} | {unclear} |".format(**row)
        )
    lines.extend(
        [
            "",
            "Note: this is simulated expert review by an LLM, not a substitute for independent human review.",
        ]
    )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_table3_latex(
    path: Path,
    correctness_rows: list[dict[str, Any]],
    failure_rows: list[dict[str, Any]],
) -> None:
    correctness_body = [
        "        {system} & {reviewed} & {correct} & {likely} & {incorrect} & {uncertain} & {strict} & {lenient} & {unsafe} \\\\".format(
            system=latex_escape(row["system"]),
            reviewed=row["reviewed_patches"],
            correct=row["correct"],
            likely=row["likely_correct"],
            incorrect=row["incorrect"],
            uncertain=row["uncertain"],
            strict=format_percent(row["strict_correctness"]),
            lenient=format_percent(row["lenient_correctness"]),
            unsafe=row["unsafe_regressions"],
        )
        for row in correctness_rows
    ]
    failure_body = [
        "        {system} & {failed_patches} & {apply_failure} & {compile_or_test_failure} & {resource_lifecycle_still_unsafe} & {semantic_change} & {security_regression} & {overbroad_or_unrelated_patch} & {detector_false_positive} & {llm_api_or_empty_patch} & {validator_infrastructure_failure} & {unclear} \\\\".format(
            **{**row, "system": latex_escape(row["system"])},
        )
        for row in failure_rows
    ]
    latex = "\n".join(
        [
            "\\begin{table*}[t]",
            "    \\centering",
            "    \\caption{LLM-assisted review of accepted and rejected patches.}",
            "    \\label{tab:llm-review}",
            "    \\begin{tabular}{lrrrrrrrr}",
            "        \\toprule",
            "        System & Reviewed & Correct & Likely & Incorrect & Uncertain & Strict & Lenient & Unsafe \\\\",
            "        \\midrule",
            *correctness_body,
            "        \\bottomrule",
            "    \\end{tabular}",
            "",
            "    \\vspace{0.5em}",
            "    \\begin{tabular}{lrrrrrrrrrrr}",
            "        \\toprule",
            "        System & Failed & Apply & C/T & Res. & Sem. & Sec. & Broad & FP & LLM & Infra & Unclear \\\\",
            "        \\midrule",
            *failure_body,
            "        \\bottomrule",
            "    \\end{tabular}",
            "\\end{table*}",
            "",
        ]
    )
    path.write_text(latex, encoding="utf-8")


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


def completed_keys(path: Path) -> set[str]:
    return {record.get("candidate_key") for record in read_jsonl(path) if record.get("candidate_key")}


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def is_review_unsafe(review: dict[str, Any]) -> bool:
    return any(
        review.get(field) is True
        for field in [
            "security_regression",
            "semantic_regression",
            "resource_lifecycle_regression",
        ]
    ) or str(review.get("verdict") or "").lower() in {"incorrect", "unsafe", "reject"}


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]..."


def float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)


def format_percent(value: float) -> str:
    return f"{value * 100:.2f}%"


def latex_escape(value: str) -> str:
    return value.replace("_", "\\_").replace("%", "\\%").replace("&", "\\&")


if __name__ == "__main__":
    raise SystemExit(main())
