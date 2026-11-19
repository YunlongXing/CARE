"""Create independent human-review materials for mined maintenance commits."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VERDICTS = "refactoring|security_repair|not_actionable|unclear"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", required=True, help="Manifest from mine_c_cpp_maintenance_commits.py")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--sample-size", type=int, default=None)
    parser.add_argument("--seed", type=int, default=20261113)
    parser.add_argument("--max-per-project", type=int, default=30)
    args = parser.parse_args(argv)

    root = Path.cwd().resolve()
    candidate_path = resolve_path(root, args.candidates)
    output_dir = resolve_path(root, args.output_dir)
    payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidates = sorted(payload.get("candidates") or [], key=lambda item: item["id"])
    candidates = stratified_sample(
        candidates,
        sample_size=args.sample_size,
        seed=args.seed,
        max_per_project=args.max_per_project,
    )
    materials_dir = output_dir / "materials"
    materials_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, str]] = []
    manifest_items: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        sample_id = f"P{index:04d}"
        material_path = materials_dir / f"{sample_id}.md"
        material_path.write_text(render_material(candidate, candidate_path.parent), encoding="utf-8")
        relative_material = str(material_path.relative_to(output_dir))
        rows.append(
            {
                "sample_id": sample_id,
                "case_id": str(candidate["id"]),
                "material_file": relative_material,
                "project_id": str(candidate["project_id"]),
                "candidate_category": str(candidate["candidate_category"]),
                "verdict": "",
                "semantic_preserving": "",
                "security_relevant": "",
                "reason": "",
                "reviewer_confidence": "",
            }
        )
        manifest_items.append({**candidate, "sample_id": sample_id, "material_file": relative_material})

    fieldnames = list(rows[0]) if rows else review_fieldnames()
    write_csv(output_dir / "reviewer_1.csv", rows, fieldnames)
    write_csv(output_dir / "reviewer_2.csv", rows, fieldnames)
    write_csv(output_dir / "adjudication.csv", adjudication_rows(rows), adjudication_fieldnames())
    write_json(
        output_dir / "review_manifest.json",
        {
            "schema_version": 1,
            "seed": args.seed,
            "sample_count": len(manifest_items),
            "sample_digest": sample_digest(manifest_items),
            "candidate_manifest": str(candidate_path),
            "items": manifest_items,
        },
    )
    (output_dir / "REVIEW_PROTOCOL.md").write_text(review_protocol(), encoding="utf-8")
    print(f"prepared {len(rows)} independent patch-safety review materials")
    return 0


def stratified_sample(
    candidates: list[dict[str, Any]],
    sample_size: int | None,
    seed: int,
    max_per_project: int,
) -> list[dict[str, Any]]:
    if sample_size is None or sample_size >= len(candidates):
        return candidates
    if sample_size < 1 or max_per_project < 1:
        raise ValueError("sample size and max-per-project must be positive")

    groups: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        groups.setdefault(str(candidate.get("candidate_category") or "other"), []).append(candidate)
    quotas = {
        category: sample_size // len(groups) + (index < sample_size % len(groups))
        for index, category in enumerate(sorted(groups))
    }
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    project_counts: dict[str, int] = {}
    for category in sorted(groups):
        for candidate in stable_candidates(groups[category], seed):
            project = str(candidate["project_id"])
            if project_counts.get(project, 0) >= max_per_project:
                continue
            selected.append(candidate)
            selected_ids.add(str(candidate["id"]))
            project_counts[project] = project_counts.get(project, 0) + 1
            if sum(1 for item in selected if item["candidate_category"] == category) >= quotas[category]:
                break
    for candidate in stable_candidates(candidates, seed):
        if len(selected) >= sample_size:
            break
        project = str(candidate["project_id"])
        if str(candidate["id"]) in selected_ids or project_counts.get(project, 0) >= max_per_project:
            continue
        selected.append(candidate)
        selected_ids.add(str(candidate["id"]))
        project_counts[project] = project_counts.get(project, 0) + 1
    return sorted(selected, key=lambda item: str(item["id"]))


def stable_candidates(candidates: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    decorated = [
        (hashlib.sha256(f"{seed}:{item['id']}".encode("utf-8")).hexdigest(), rng.random(), item)
        for item in candidates
    ]
    return [item for _, _, item in sorted(decorated)]


def sample_digest(items: list[dict[str, Any]]) -> str:
    source = "\n".join(str(item["id"]) for item in items)
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def render_material(candidate: dict[str, Any], manifest_dir: Path) -> str:
    patch_path = manifest_dir / str(candidate["patch_file"])
    diff = patch_path.read_text(encoding="utf-8", errors="replace") if patch_path.exists() else "[patch unavailable]"
    changed_files = "\n".join(f"- `{path}`" for path in candidate.get("changed_files") or [])
    return (
        "# C/C++ Maintenance Change Review\n\n"
        f"- Project: `{candidate['project_id']}`\n"
        f"- Commit subject: {candidate['subject']}\n"
        f"- Candidate category: `{candidate['candidate_category']}`\n"
        f"- Base revision: `{candidate['base_commit']}`\n"
        f"- Patch revision: `{candidate['patch_commit']}`\n\n"
        "## Changed C/C++ Files\n\n"
        f"{changed_files or '- None'}\n\n"
        "## Patch\n\n"
        "```diff\n"
        f"{diff}\n"
        "```\n"
    )


def review_fieldnames() -> list[str]:
    return [
        "sample_id",
        "case_id",
        "material_file",
        "project_id",
        "candidate_category",
        "verdict",
        "semantic_preserving",
        "security_relevant",
        "reason",
        "reviewer_confidence",
    ]


def adjudication_fieldnames() -> list[str]:
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


def adjudication_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {
            "sample_id": row["sample_id"],
            "case_id": row["case_id"],
            "project_id": row["project_id"],
            "candidate_category": row["candidate_category"],
            "reviewer_1_verdict": "",
            "reviewer_2_verdict": "",
            "agreement": "",
            "final_verdict": "",
            "semantic_preserving": "",
            "security_relevant": "",
            "adjudication_reason": "",
            "eligible_for_benchmark": "",
        }
        for row in rows
    ]


def review_protocol() -> str:
    return f"""# CARE Patch-Safety Review Protocol

## Goal

Construct a ground-truth corpus of C/C++ maintenance changes. This review labels
the developer change itself; it does not evaluate CARE or an LLM proposal.

## Allowed Verdicts

- `refactoring`: the patch improves structure while preserving intended behavior.
- `security_repair`: the patch deliberately changes behavior to fix a concrete
  reliability or security issue.
- `not_actionable`: the patch is not a suitable local maintenance/refactoring
  benchmark case.
- `unclear`: context outside the material is required to decide.

Use exactly: `{VERDICTS}`.

## Procedure

1. Two reviewers independently fill `reviewer_1.csv` and `reviewer_2.csv`.
2. Set `semantic_preserving` and `security_relevant` to `yes`, `no`, or
   `unclear`; set confidence from 1 through 5.
3. Before adjudication, reviewers may inspect the repository at the recorded
   base revision, but must record any additional evidence in `reason`.
4. Retain only adjudicated refactorings and repairs whose intended behavior and
   relevant validation commands can be established.

LLM classifications are not a substitute for these labels.
"""


def resolve_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
