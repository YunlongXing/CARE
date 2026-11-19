"""Mine reviewable C/C++ maintenance commits from complete local Git histories.

The script does not infer that a commit is a correct refactoring. It produces a
frozen candidate manifest and per-commit patches for independent human review.
Repositories cloned with depth one are reported and skipped rather than silently
being treated as a representative history.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
C_EXTENSIONS = {".c", ".cc", ".cpp", ".h", ".hpp"}
REFACTOR_TERMS = re.compile(r"\b(refactor|clean(?:\s|-)?up|simplif|restructur|reorgan|moderni[sz]|tidy)\b", re.I)
REPAIR_TERMS = re.compile(r"\b(leak|double[ -]?free|use[ -]?after[ -]?free|uaf|null|error[ -]?path|memory[ -]?safety)\b", re.I)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project",
        action="append",
        default=[],
        metavar="ID=PATH",
        help="Project identifier and local Git checkout. Repeat for each project.",
    )
    parser.add_argument("--output-dir", default="benchmarks/patch_safety/mined")
    parser.add_argument("--max-per-project", type=int, default=50)
    parser.add_argument("--max-history", type=int, default=5000)
    parser.add_argument("--include-repairs", action="store_true")
    args = parser.parse_args(argv)

    if args.max_per_project < 1 or args.max_history < 1:
        parser.error("--max-per-project and --max-history must be positive")
    projects = parse_projects(args.project)
    if not projects:
        parser.error("at least one --project ID=PATH is required")

    output_dir = resolve_path(Path.cwd(), args.output_dir)
    patches_dir = output_dir / "patches"
    patches_dir.mkdir(parents=True, exist_ok=True)

    candidates: list[dict[str, Any]] = []
    repository_summary: list[dict[str, Any]] = []
    for project_id, repository in projects:
        mined, metadata = mine_project(
            project_id=project_id,
            repository=repository,
            patches_dir=patches_dir,
            max_per_project=args.max_per_project,
            max_history=args.max_history,
            include_repairs=args.include_repairs,
        )
        candidates.extend(mined)
        repository_summary.append(metadata)

    payload = {
        "schema_version": 1,
        "candidate_count": len(candidates),
        "repositories": repository_summary,
        "candidates": candidates,
    }
    write_json(output_dir / "maintenance_commit_candidates.json", payload)
    print(f"mined {len(candidates)} review candidates from {len(projects)} repositories")
    return 0


def parse_projects(values: list[str]) -> list[tuple[str, Path]]:
    projects: list[tuple[str, Path]] = []
    for value in values:
        project_id, separator, raw_path = value.partition("=")
        if not separator or not project_id.strip() or not raw_path.strip():
            raise ValueError(f"invalid --project value {value!r}; expected ID=PATH")
        projects.append((project_id.strip(), Path(raw_path).expanduser().resolve()))
    return projects


def mine_project(
    project_id: str,
    repository: Path,
    patches_dir: Path,
    max_per_project: int,
    max_history: int,
    include_repairs: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not (repository / ".git").exists():
        return [], {"project_id": project_id, "repository": str(repository), "status": "not_a_git_repository"}
    commit_count = int(run_git(repository, ["rev-list", "--count", "HEAD"]).strip())
    metadata = {
        "project_id": project_id,
        "repository": str(repository),
        "commit_count": commit_count,
        "status": "ok" if commit_count > 1 else "insufficient_history",
    }
    if commit_count <= 1:
        return [], metadata

    history = run_git(
        repository,
        ["log", f"--max-count={max_history}", "--format=%H%x09%P%x09%s"],
    ).splitlines()
    candidates: list[dict[str, Any]] = []
    for line in history:
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        commit, parents, subject = parts
        parent = parents.split()[0] if parents.strip() else ""
        category = classify_subject(subject, include_repairs)
        if category is None or not parent:
            continue
        changed_files = [
            path
            for path in run_git(repository, ["diff-tree", "--no-commit-id", "--name-only", "-r", commit]).splitlines()
            if Path(path).suffix.lower() in C_EXTENSIONS
        ]
        if not changed_files:
            continue
        diff = run_git(repository, ["diff", "--unified=40", parent, commit, "--", *changed_files])
        if not diff.strip():
            continue
        case_id = f"{project_id}:{commit[:12]}"
        patch_path = patches_dir / f"{project_id}-{commit[:12]}.diff"
        patch_path.write_text(diff, encoding="utf-8")
        candidates.append(
            {
                "id": case_id,
                "project_id": project_id,
                "repository": str(repository),
                "base_commit": parent,
                "patch_commit": commit,
                "subject": subject,
                "candidate_category": category,
                "changed_files": changed_files,
                "patch_file": str(patch_path.relative_to(patches_dir.parent)),
                "review_status": "unreviewed",
            }
        )
        if len(candidates) >= max_per_project:
            break
    return candidates, metadata


def classify_subject(subject: str, include_repairs: bool) -> str | None:
    if REFACTOR_TERMS.search(subject):
        return "candidate_refactoring"
    if include_repairs and REPAIR_TERMS.search(subject):
        return "candidate_security_repair"
    return None


def run_git(repository: Path, arguments: list[str]) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"git {' '.join(arguments)} failed")
    return completed.stdout


def resolve_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
