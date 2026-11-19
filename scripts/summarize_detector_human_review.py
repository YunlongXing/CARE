"""Merge and summarize independent human reviews of CARE detector findings."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


LABELS = {"true_positive", "false_positive", "unclear"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-dir", default="benchmarks/oss50/issta-detector-review")
    parser.add_argument("--merge-adjudication", action="store_true")
    args = parser.parse_args(argv)

    review_dir = Path(args.review_dir).resolve()
    manifest = json.loads((review_dir / "sample_manifest.json").read_text(encoding="utf-8"))
    items = {item["sample_id"]: item for item in manifest["items"]}
    reviewer_1 = read_csv_indexed(review_dir / "reviewer_1.csv")
    reviewer_2 = read_csv_indexed(review_dir / "reviewer_2.csv")
    ensure_same_ids(items, reviewer_1, reviewer_2)

    completeness = {
        "reviewer_1_completed": completed_count(reviewer_1),
        "reviewer_2_completed": completed_count(reviewer_2),
        "expected": len(items),
    }
    summary: dict[str, Any] = {"completeness": completeness}

    both_complete_ids = [
        sample_id
        for sample_id in items
        if normalized_label(reviewer_1[sample_id].get("verdict")) in LABELS
        and normalized_label(reviewer_2[sample_id].get("verdict")) in LABELS
    ]
    if both_complete_ids:
        labels_1 = [normalized_label(reviewer_1[sample_id]["verdict"]) for sample_id in both_complete_ids]
        labels_2 = [normalized_label(reviewer_2[sample_id]["verdict"]) for sample_id in both_complete_ids]
        agreement = sum(left == right for left, right in zip(labels_1, labels_2)) / len(both_complete_ids)
        summary["independent_review"] = {
            "both_completed": len(both_complete_ids),
            "raw_agreement": round(agreement, 6),
            "cohens_kappa": round(cohens_kappa(labels_1, labels_2), 6),
            "reviewer_1_labels": dict(sorted(Counter(labels_1).items())),
            "reviewer_2_labels": dict(sorted(Counter(labels_2).items())),
        }

    adjudication_path = review_dir / "adjudication.csv"
    if args.merge_adjudication:
        merge_adjudication(adjudication_path, items, reviewer_1, reviewer_2)
    adjudication = read_csv_indexed(adjudication_path)
    final_records = []
    for sample_id, item in items.items():
        row = adjudication.get(sample_id) or {}
        verdict = normalized_label(row.get("final_verdict"))
        if verdict not in LABELS:
            continue
        final_records.append({**item, "final_verdict": verdict})

    if final_records:
        summary["adjudicated"] = summarize_final_records(final_records)
        write_eligible_manifest(review_dir / "patch_benchmark_candidates.json", final_records)

    (review_dir / "human_review_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def completed_count(rows: dict[str, dict[str, str]]) -> int:
    return sum(normalized_label(row.get("verdict")) in LABELS for row in rows.values())


def normalized_label(value: Any) -> str:
    return str(value or "").strip().lower()


def ensure_same_ids(
    items: dict[str, Any],
    reviewer_1: dict[str, Any],
    reviewer_2: dict[str, Any],
) -> None:
    expected = set(items)
    for name, rows in (("reviewer_1", reviewer_1), ("reviewer_2", reviewer_2)):
        observed = set(rows)
        if observed != expected:
            missing = sorted(expected - observed)[:5]
            extra = sorted(observed - expected)[:5]
            raise ValueError(f"{name} sample ids differ; missing={missing}, extra={extra}")


def cohens_kappa(left: list[str], right: list[str]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    total = len(left)
    observed = sum(a == b for a, b in zip(left, right)) / total
    left_counts = Counter(left)
    right_counts = Counter(right)
    expected = sum(
        (left_counts[label] / total) * (right_counts[label] / total)
        for label in LABELS
    )
    if math.isclose(expected, 1.0):
        return 1.0 if math.isclose(observed, 1.0) else 0.0
    return (observed - expected) / (1.0 - expected)


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 0.0
    proportion = successes / total
    denominator = 1.0 + z * z / total
    center = (proportion + z * z / (2.0 * total)) / denominator
    margin = (
        z
        * math.sqrt((proportion * (1.0 - proportion) + z * z / (4.0 * total)) / total)
        / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)


def precision_record(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(record["final_verdict"] for record in records)
    determinate = counts["true_positive"] + counts["false_positive"]
    precision = counts["true_positive"] / determinate if determinate else 0.0
    low, high = wilson_interval(counts["true_positive"], determinate)
    weighted_tp = sum(
        float(record.get("sampling_weight") or 1.0)
        for record in records
        if record["final_verdict"] == "true_positive"
    )
    weighted_determinate = sum(
        float(record.get("sampling_weight") or 1.0)
        for record in records
        if record["final_verdict"] in {"true_positive", "false_positive"}
    )
    return {
        "reviewed": len(records),
        "true_positive": counts["true_positive"],
        "false_positive": counts["false_positive"],
        "unclear": counts["unclear"],
        "precision_excluding_unclear": round(precision, 6),
        "wilson_95_low": round(low, 6),
        "wilson_95_high": round(high, 6),
        "weighted_precision_excluding_unclear": round(
            weighted_tp / weighted_determinate if weighted_determinate else 0.0,
            6,
        ),
    }


def summarize_final_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {
        "family": defaultdict(list),
        "severity": defaultdict(list),
        "evidence_confidence": defaultdict(list),
        "deployment_context": defaultdict(list),
    }
    for record in records:
        for field in grouped:
            grouped[field][str(record.get(field) or "unknown")].append(record)
    return {
        "overall": precision_record(records),
        **{
            f"by_{field}": {
                key: precision_record(values)
                for key, values in sorted(groups.items())
            }
            for field, groups in grouped.items()
        },
        "eligible_for_patch_benchmark": sum(
            record["final_verdict"] == "true_positive"
            and record.get("deployment_context") == "production"
            and record.get("evidence_confidence") == "high"
            for record in records
        ),
    }


def merge_adjudication(
    path: Path,
    items: dict[str, dict[str, Any]],
    reviewer_1: dict[str, dict[str, str]],
    reviewer_2: dict[str, dict[str, str]],
) -> None:
    rows = read_csv(path)
    fieldnames = list(rows[0].keys()) if rows else []
    for row in rows:
        sample_id = row["sample_id"]
        left = normalized_label(reviewer_1[sample_id].get("verdict"))
        right = normalized_label(reviewer_2[sample_id].get("verdict"))
        row["reviewer_1_verdict"] = left
        row["reviewer_2_verdict"] = right
        row["agreement"] = "yes" if left in LABELS and left == right else "no"
        if left in LABELS and left == right and not normalized_label(row.get("final_verdict")):
            row["final_verdict"] = left
        item = items[sample_id]
        if normalized_label(row.get("final_verdict")) == "true_positive":
            eligible = (
                item.get("deployment_context") == "production"
                and item.get("evidence_confidence") == "high"
            )
            row["eligible_for_patch_benchmark"] = "yes" if eligible else "no"
    write_csv(path, rows, fieldnames)


def write_eligible_manifest(path: Path, records: list[dict[str, Any]]) -> None:
    eligible = [
        record
        for record in records
        if record["final_verdict"] == "true_positive"
        and record.get("deployment_context") == "production"
        and record.get("evidence_confidence") == "high"
    ]
    payload = {
        "count": len(eligible),
        "work_ids": [record["work_id"] for record in eligible],
        "items": eligible,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def read_csv_indexed(path: Path) -> dict[str, dict[str, str]]:
    return {row["sample_id"]: row for row in read_csv(path)}


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
