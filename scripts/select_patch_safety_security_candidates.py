"""Select security-check-focused C/C++ maintenance candidates for curation."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


PATTERNS = {
    "null_guard": r"^[+-].*\bif\s*\([^\n]*(?:!\s*[A-Za-z_]|\bNULL\b|\bnullptr\b)",
    "bounds_guard": r"^[+-].*\bif\s*\([^\n]*(?:\blen\b|\bsize\b|\bidx\b|\bindex\b|\bcount\b|\bcapacity\b).*(?:<=|>=|<|>)",
    "status_guard": r"^[+-].*\bif\s*\([^\n]*(?:\bret(?:urn)?\b|\berr(?:or)?\b|\bstatus\b|\brc\b).*(?:==|!=|<=|>=|<|>)",
    "bounded_copy": r"^[+-].*\b(?:snprintf|vsnprintf|strncpy|strlcpy|fgets)\s*\(",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--per-class", type=int, default=20)
    parser.add_argument("--max-per-project", type=int, default=12)
    parser.add_argument("--seed", type=int, default=20261115)
    args = parser.parse_args(argv)
    if args.per_class < 1 or args.max_per_project < 1:
        parser.error("--per-class and --max-per-project must be positive")

    root = Path.cwd().resolve()
    candidate_path = resolve_path(root, args.candidates)
    payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidates = list(payload.get("candidates") or [])
    selected, labels = select_candidates(
        candidates,
        candidate_path.parent,
        per_class=args.per_class,
        max_per_project=args.max_per_project,
        seed=args.seed,
    )
    output = resolve_path(root, args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_manifest": str(candidate_path),
                "seed": args.seed,
                "per_class": args.per_class,
                "max_per_project": args.max_per_project,
                "selection_count": len(selected),
                "class_counts": dict(Counter(label for values in labels.values() for label in values)),
                "candidates": [{**candidate, "security_focus": labels[str(candidate["id"])]} for candidate in selected],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"selected {len(selected)} security-focused candidates")
    return 0


def select_candidates(
    candidates: list[dict[str, Any]],
    patch_root: Path,
    *,
    per_class: int,
    max_per_project: int,
    seed: int,
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    labels = {str(candidate["id"]): classify(candidate, patch_root) for candidate in candidates}
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    project_counts: Counter[str] = Counter()
    for category in PATTERNS:
        matching = [candidate for candidate in candidates if category in labels[str(candidate["id"])]]
        for candidate in stable_order(matching, seed):
            if sum(category in labels[str(item["id"])] for item in selected) >= per_class:
                break
            candidate_id = str(candidate["id"])
            project_id = str(candidate["project_id"])
            if candidate_id in selected_ids or project_counts[project_id] >= max_per_project:
                continue
            selected.append(candidate)
            selected_ids.add(candidate_id)
            project_counts[project_id] += 1
    return sorted(selected, key=lambda candidate: str(candidate["id"])), labels


def classify(candidate: dict[str, Any], patch_root: Path) -> list[str]:
    patch_file = patch_root / str(candidate.get("patch_file") or "")
    if not patch_file.is_file():
        return []
    diff = patch_file.read_text(encoding="utf-8", errors="replace")
    return [
        name for name, pattern in PATTERNS.items()
        if re.search(pattern, diff, flags=re.MULTILINE | re.IGNORECASE)
    ]


def stable_order(candidates: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    return sorted(
        candidates,
        key=lambda candidate: hashlib.sha256(f"{seed}:{candidate['id']}".encode("utf-8")).hexdigest(),
    )


def resolve_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


if __name__ == "__main__":
    raise SystemExit(main())
