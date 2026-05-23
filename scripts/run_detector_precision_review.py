"""LLM-assisted detector precision review for sampled opportunities.

This produces a paper-facing precision estimate for the detector output. The
review is explicitly LLM-assisted; for a final submission, the JSONL/CSV output
can be used as the seed for independent human annotation.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from care.llm.client import LLMClient


DEFAULT_QUEUE = "benchmarks/oss50/llm-validation-critical-only/queue.json"
DEFAULT_OUTPUT_DIR = "benchmarks/oss50/detector-precision-critical"
DEFAULT_TABLE_DIR = "benchmarks/oss50/paper-tables"
LABELS = {"true_positive", "likely_true_positive", "false_positive", "unclear"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", default=DEFAULT_QUEUE)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--table-dir", default=DEFAULT_TABLE_DIR)
    parser.add_argument("--sample-size", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260517)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument(
        "--review-scope",
        choices=["resource", "general"],
        default="resource",
        help="Review resource-lifecycle findings or general security/reliability refactoring findings.",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--skip-llm", action="store_true")
    args = parser.parse_args(argv)

    root = Path.cwd().resolve()
    output_dir = (root / args.output_dir).resolve()
    table_dir = (root / args.table_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    table_dir.mkdir(parents=True, exist_ok=True)

    queue_path = (root / args.queue).resolve()
    queue_items = json.loads(queue_path.read_text(encoding="utf-8"))["items"]
    sample_path = output_dir / "sample.json"
    if args.resume and sample_path.exists():
        sample = json.loads(sample_path.read_text(encoding="utf-8"))["items"]
    else:
        sample = sample_items(queue_items, args.sample_size, args.seed)
        sample_path.write_text(
            json.dumps({"seed": args.seed, "sample_size": len(sample), "items": sample}, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    review_path = output_dir / "detector_precision_reviews.jsonl"
    if not args.skip_llm:
        client = LLMClient.from_env(require_real=True)
        completed = completed_work_ids(review_path) if args.resume else set()
        pending = [item for item in sample if item["work_id"] not in completed]
        for offset in range(0, len(pending), max(1, args.batch_size)):
            batch = pending[offset : offset + max(1, args.batch_size)]
            prompt = build_prompt(batch, review_scope=args.review_scope)
            try:
                payload = call_json(client, prompt)
                reviews = payload.get("reviews") if isinstance(payload, dict) else payload
                if not isinstance(reviews, list):
                    reviews = []
            except Exception:
                reviews = []
            by_work_id = {
                str(review.get("work_id")): review
                for review in reviews
                if isinstance(review, dict) and review.get("work_id")
            }
            for item in batch:
                review = normalize_review(item, by_work_id.get(item["work_id"]) or fallback_review(item))
                append_jsonl(review_path, review)
            print(f"reviewed detector sample {min(offset + len(batch), len(pending))}/{len(pending)}", flush=True)
            time.sleep(0.2)

    reviews = read_jsonl(review_path)
    write_summary(output_dir, table_dir, reviews, review_scope=args.review_scope)
    print(f"wrote detector precision artifacts to {output_dir} and {table_dir}")
    return 0


def sample_items(items: list[dict[str, Any]], sample_size: int, seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    candidates = list(items)
    rng.shuffle(candidates)
    sample = candidates[: min(sample_size, len(candidates))]
    return sorted(sample, key=lambda item: item["work_id"])


def build_prompt(batch: list[dict[str, Any]], review_scope: str = "resource") -> str:
    payload = [sample_payload(item) for item in batch]
    if review_scope == "general":
        task = (
            "For each CARE detector finding, decide whether the finding represents a real "
            "security/reliability-relevant refactoring opportunity in the shown source context. "
            "Treat unsafe API use, unreachable code, duplicated cleanup, duplicate checks, and "
            "resource lifecycle issues according to their detector kind and evidence."
        )
        positive_label = "the finding is clearly a real security/reliability refactoring opportunity."
        likely_label = "the finding is plausible and relevant, but full proof needs more project context."
        false_label = "the evidence/snippet shows the detector is likely wrong, benign, or too weak."
    else:
        task = (
            "For each CARE detector finding, decide whether the finding represents a real resource-lifecycle "
            "refactoring opportunity in the shown function context."
        )
        positive_label = "the finding is clearly a real lifecycle/refactoring opportunity."
        likely_label = "the finding is plausible and security/reliability relevant, but full proof needs more context."
        false_label = "the evidence/snippet shows the detector is likely wrong or benign."
    return (
        "You are simulating a careful C/C++ security-program-analysis reviewer.\n"
        f"{task}\n\n"
        "Labels:\n"
        f"- true_positive: {positive_label}\n"
        f"- likely_true_positive: {likely_label}\n"
        f"- false_positive: {false_label}\n"
        "- unclear: insufficient context to decide.\n\n"
        "Return JSON only with this schema:\n"
        "{\n"
        '  "reviews": [\n'
        "    {\n"
        '      "work_id": "string",\n'
        '      "label": "true_positive|likely_true_positive|false_positive|unclear",\n'
        '      "resource_lifecycle_relevant": true|false,\n'
        '      "security_relevant": true|false,\n'
        '      "reason": "short explanation",\n'
        '      "confidence": 0.0\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        f"{json.dumps(payload, indent=2, sort_keys=True)}"
    )


def sample_payload(item: dict[str, Any]) -> dict[str, Any]:
    opportunity = item.get("opportunity") or {}
    return {
        "work_id": item["work_id"],
        "project_id": item["project_id"],
        "kind": item["kind"],
        "severity": item["severity"],
        "function": item["function"],
        "file": item["file"],
        "line": item["line"],
        "description": item["description"],
        "context_summary": opportunity.get("context_summary") or "",
        "evidence": compact_json(opportunity.get("evidence") or {}, max_chars=3500),
        "source_snippet": source_snippet(item.get("file"), int(item.get("line") or 1)),
    }


def source_snippet(path_text: str | None, line: int, radius: int = 35) -> str:
    if not path_text:
        return ""
    path = Path(path_text)
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    start = max(1, line - radius)
    end = min(len(lines), line + radius)
    return "\n".join(f"{number}: {lines[number - 1]}" for number in range(start, end + 1))


def call_json(client: LLMClient, prompt: str) -> Any:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            raw = client.complete(prompt, temperature=0.0)
            return parse_json_response(raw)
        except Exception as exc:
            last_error = exc
            time.sleep(2 + attempt * 3)
    raise RuntimeError(f"LLM detector review failed: {last_error}")


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


def normalize_review(item: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    label = str(review.get("label") or "unclear").lower()
    if label not in LABELS:
        label = "unclear"
    return {
        "work_id": item["work_id"],
        "project_id": item["project_id"],
        "kind": item["kind"],
        "severity": item["severity"],
        "function": item["function"],
        "file": item["file"],
        "line": item["line"],
        "label": label,
        "resource_lifecycle_relevant": bool(review.get("resource_lifecycle_relevant")),
        "security_relevant": bool(review.get("security_relevant")),
        "reason": str(review.get("reason") or "")[:1000],
        "confidence": float_or_zero(review.get("confidence")),
    }


def fallback_review(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": "unclear",
        "resource_lifecycle_relevant": item.get("kind") == "resource_imbalance",
        "security_relevant": item.get("severity") in {"critical", "high"},
        "reason": "LLM review did not return a usable classification; marked unclear.",
        "confidence": 0.0,
    }


def write_summary(
    output_dir: Path,
    table_dir: Path,
    reviews: list[dict[str, Any]],
    review_scope: str = "resource",
) -> None:
    counts = Counter(review.get("label") for review in reviews)
    total = len(reviews)
    strict = counts["true_positive"]
    lenient = counts["true_positive"] + counts["likely_true_positive"]
    false_positive = counts["false_positive"]
    unclear = counts["unclear"]
    security_relevant = sum(1 for review in reviews if review.get("security_relevant"))
    resource_relevant = sum(1 for review in reviews if review.get("resource_lifecycle_relevant"))
    summary = {
        "reviewed": total,
        "true_positive": strict,
        "likely_true_positive": counts["likely_true_positive"],
        "false_positive": false_positive,
        "unclear": unclear,
        "strict_precision": rate(strict, total),
        "lenient_precision": rate(lenient, total),
        "false_positive_rate": rate(false_positive, total),
        "security_relevant": security_relevant,
        "resource_lifecycle_relevant": resource_relevant,
        "review_scope": review_scope,
        "review_scope_note": (
            "LLM-assisted review over all high-severity, high-evidence-confidence CARE findings."
            if review_scope == "general"
            else "LLM-assisted review over high-priority resource/goto-cleanup findings."
        ),
    }
    (output_dir / "detector_precision_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    write_reviews_csv(output_dir / "detector_precision_reviews.csv", reviews)
    write_markdown(table_dir / "table4_detector_precision.md", summary)
    write_latex(table_dir / "table4_detector_precision.tex", summary)
    write_csv(table_dir / "table4_detector_precision.csv", [summary])
    (table_dir / "table4_detector_precision.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def write_reviews_csv(path: Path, reviews: list[dict[str, Any]]) -> None:
    fieldnames = [
        "work_id",
        "project_id",
        "kind",
        "severity",
        "function",
        "file",
        "line",
        "label",
        "resource_lifecycle_relevant",
        "security_relevant",
        "confidence",
        "reason",
    ]
    write_csv(path, reviews, fieldnames=fieldnames)


def write_markdown(path: Path, summary: dict[str, Any]) -> None:
    scope_note = summary.get("review_scope_note") or "LLM-assisted detector precision sample."
    lines = [
        "# Table 4: Detector Precision Sample",
        "",
        "| Sample | True positive | Likely true | False positive | Unclear | Strict precision | Lenient precision | Security relevant |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        "| {reviewed} | {true_positive} | {likely_true_positive} | {false_positive} | {unclear} | {strict} | {lenient} | {security_relevant} |".format(
            reviewed=summary["reviewed"],
            true_positive=summary["true_positive"],
            likely_true_positive=summary["likely_true_positive"],
            false_positive=summary["false_positive"],
            unclear=summary["unclear"],
            strict=format_percent(summary["strict_precision"]),
            lenient=format_percent(summary["lenient_precision"]),
            security_relevant=summary["security_relevant"],
        ),
        "",
        f"Note: {scope_note}",
    ]
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_latex(path: Path, summary: dict[str, Any]) -> None:
    latex = "\n".join(
        [
            "\\begin{table}[t]",
            "    \\centering",
            "    \\caption{LLM-assisted detector precision sample for critical findings.}",
            "    \\label{tab:detector-precision}",
            "    \\begin{tabular}{rrrrrrr}",
            "        \\toprule",
            "        Sample & TP & Likely & FP & Unclear & Strict & Lenient \\\\",
            "        \\midrule",
            "        {reviewed} & {true_positive} & {likely_true_positive} & {false_positive} & {unclear} & {strict} & {lenient} \\\\".format(
                reviewed=summary["reviewed"],
                true_positive=summary["true_positive"],
                likely_true_positive=summary["likely_true_positive"],
                false_positive=summary["false_positive"],
                unclear=summary["unclear"],
                strict=format_percent(summary["strict_precision"]),
                lenient=format_percent(summary["lenient_precision"]),
            ),
            "        \\bottomrule",
            "    \\end{tabular}",
            "\\end{table}",
            "",
        ]
    )
    path.write_text(latex, encoding="utf-8")


def completed_work_ids(path: Path) -> set[str]:
    return {record.get("work_id") for record in read_jsonl(path) if record.get("work_id")}


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


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    if fieldnames is None:
        fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def compact_json(value: Any, max_chars: int) -> Any:
    text = json.dumps(value, sort_keys=True)
    if len(text) <= max_chars:
        return value
    return text[:max_chars] + "...[truncated]"


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


if __name__ == "__main__":
    raise SystemExit(main())
