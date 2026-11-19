"""Summarize independent human labels for the CARE patch-safety corpus."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reviewer-1", required=True)
    parser.add_argument("--reviewer-2", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)

    root = Path.cwd().resolve()
    reviewer_1 = read_csv(resolve_path(root, args.reviewer_1))
    reviewer_2 = read_csv(resolve_path(root, args.reviewer_2))
    output_dir = resolve_path(root, args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rows, summary = summarize(reviewer_1, reviewer_2)
    write_csv(output_dir / "agreement.csv", rows, list(rows[0]) if rows else agreement_fieldnames())
    write_json(output_dir / "review_summary.json", summary)
    (output_dir / "review_summary.md").write_text(render_summary(summary), encoding="utf-8")
    print(f"summarized {summary['labeled_pairs']} independently labeled pairs")
    return 0


def summarize(
    reviewer_1: list[dict[str, str]], reviewer_2: list[dict[str, str]]
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    by_id_1 = {row["sample_id"]: row for row in reviewer_1}
    by_id_2 = {row["sample_id"]: row for row in reviewer_2}
    shared_ids = sorted(set(by_id_1) & set(by_id_2))
    rows: list[dict[str, str]] = []
    labels_1: list[str] = []
    labels_2: list[str] = []
    for sample_id in shared_ids:
        first = by_id_1[sample_id]
        second = by_id_2[sample_id]
        first_label = first.get("verdict", "").strip()
        second_label = second.get("verdict", "").strip()
        labeled = bool(first_label and second_label)
        if labeled:
            labels_1.append(first_label)
            labels_2.append(second_label)
        rows.append(
            {
                "sample_id": sample_id,
                "case_id": first.get("case_id", ""),
                "project_id": first.get("project_id", ""),
                "candidate_category": first.get("candidate_category", ""),
                "reviewer_1_verdict": first_label,
                "reviewer_2_verdict": second_label,
                "agreement": "yes" if labeled and first_label == second_label else "no" if labeled else "pending",
                "final_verdict": "",
                "semantic_preserving": "",
                "security_relevant": "",
                "adjudication_reason": "",
                "eligible_for_benchmark": "",
            }
        )
    summary = {
        "sample_count": len(shared_ids),
        "labeled_pairs": len(labels_1),
        "raw_agreement": agreement(labels_1, labels_2),
        "cohens_kappa": cohens_kappa(labels_1, labels_2),
        "reviewer_1_counts": dict(sorted(Counter(labels_1).items())),
        "reviewer_2_counts": dict(sorted(Counter(labels_2).items())),
        "pending_pairs": len(shared_ids) - len(labels_1),
    }
    return rows, summary


def agreement(first: list[str], second: list[str]) -> float | None:
    if not first:
        return None
    return sum(left == right for left, right in zip(first, second)) / len(first)


def cohens_kappa(first: list[str], second: list[str]) -> float | None:
    observed = agreement(first, second)
    if observed is None:
        return None
    total = len(first)
    first_counts = Counter(first)
    second_counts = Counter(second)
    expected = sum(first_counts[label] * second_counts[label] for label in set(first_counts) | set(second_counts)) / (total * total)
    if expected == 1.0:
        return 1.0 if observed == 1.0 else None
    return (observed - expected) / (1.0 - expected)


def agreement_fieldnames() -> list[str]:
    return [
        "sample_id",
        "case_id",
        "project_id",
        "candidate_category",
        "reviewer_1_verdict",
        "reviewer_2_verdict",
        "agreement",
        "final_verdict",
        "semantic_preserving",
        "security_relevant",
        "adjudication_reason",
        "eligible_for_benchmark",
    ]


def render_summary(summary: dict[str, Any]) -> str:
    agreement_text = "pending" if summary["raw_agreement"] is None else f"{summary['raw_agreement']:.3f}"
    kappa_text = "pending" if summary["cohens_kappa"] is None else f"{summary['cohens_kappa']:.3f}"
    return (
        "# Patch-Safety Human Review Summary\n\n"
        f"- Frozen sample: {summary['sample_count']}\n"
        f"- Independently labeled pairs: {summary['labeled_pairs']}\n"
        f"- Pending pairs: {summary['pending_pairs']}\n"
        f"- Raw agreement: {agreement_text}\n"
        f"- Cohen's kappa: {kappa_text}\n"
    )


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def resolve_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
