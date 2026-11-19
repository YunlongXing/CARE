"""Materialize adjudicated C/C++ refactorings into reproducible benchmark cases.

Each selected case is archived at its recorded base revision, accompanied by
the developer patch and a manifest entry. The source archive remains unpatched:
the benchmark runner applies the patch in an isolated temporary copy.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Any


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-manifest", required=True)
    label_source = parser.add_mutually_exclusive_group(required=True)
    label_source.add_argument("--adjudication", help="Human adjudication CSV.")
    label_source.add_argument("--agent-consensus", help="Consensus CSV from run_patch_safety_agent_review.py.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--project-config",
        help="Optional JSON mapping project IDs to build_cmd and test_cmd values.",
    )
    parser.add_argument("--force", action="store_true", help="Replace an existing output directory.")
    args = parser.parse_args(argv)

    root = Path.cwd().resolve()
    review_manifest_path = resolve_path(root, args.review_manifest)
    output_dir = resolve_path(root, args.output_dir)
    if output_dir.exists():
        if not args.force:
            parser.error(f"output directory already exists: {output_dir}; use --force to replace it")
        shutil.rmtree(output_dir)

    review_manifest = read_json(review_manifest_path)
    if args.adjudication:
        reviewed = {row["sample_id"]: row for row in read_csv(resolve_path(root, args.adjudication))}
        label_mode = "human_adjudication"
    else:
        reviewed = agent_consensus_rows(read_csv(resolve_path(root, args.agent_consensus)))
        label_mode = "agent_consensus"
    raw_project_config = read_json(resolve_path(root, args.project_config)) if args.project_config else {}
    project_config = raw_project_config.get("projects", raw_project_config)
    output_dir.mkdir(parents=True)

    cases: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for item in review_manifest.get("items") or []:
        sample_id = str(item["sample_id"])
        row = reviewed.get(sample_id)
        reason = eligibility_reason(row)
        if reason:
            skipped.append({"sample_id": sample_id, "reason": reason})
            continue
        config = project_config.get(str(item["project_id"]), {})
        if config.get("patch_safety_exclude"):
            skipped.append(
                {
                    "sample_id": sample_id,
                    "reason": str(config.get("patch_safety_exclusion_reason") or "project_excluded"),
                }
            )
            continue
        cases.append(materialize_case(item, review_manifest, review_manifest_path.parent, output_dir, config))

    manifest = {
        "schema_version": 1,
        "name": "CARE adjudicated C/C++ refactoring benchmark",
        "source_review_manifest": str(review_manifest_path),
            "label_mode": label_mode,
        "case_count": len(cases),
        "cases": cases,
        "skipped": skipped,
    }
    write_json(output_dir / "manifest.json", manifest)
    write_json(output_dir / "materialization_summary.json", {"materialized": len(cases), "skipped": skipped})
    print(f"materialized {len(cases)} adjudicated refactoring cases; skipped {len(skipped)}")
    return 0


def eligibility_reason(row: dict[str, str] | None) -> str | None:
    if row is None:
        return "missing_adjudication"
    if row.get("final_verdict", "").strip() != "refactoring":
        return "not_an_adjudicated_refactoring"
    if row.get("semantic_preserving", "").strip().lower() != "yes":
        return "semantic_preservation_not_confirmed"
    if row.get("eligible_for_benchmark", "").strip().lower() != "yes":
        return "not_eligible_for_benchmark"
    return None


def agent_consensus_rows(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    """Translate strict agent agreement into the same eligibility schema.

    Agent consensus selects a conservative corpus. It does not turn agent output
    into a safety label; the materialized developer patch is still only the
    source for a later safe case and mutation study.
    """
    translated: dict[str, dict[str, str]] = {}
    for row in rows:
        eligible = row.get("eligible_for_benchmark", "").strip().lower() == "yes"
        translated[row["sample_id"]] = {
            "final_verdict": "refactoring" if eligible else "unclear",
            "semantic_preserving": "yes" if eligible else "unclear",
            "eligible_for_benchmark": "yes" if eligible else "no",
        }
    return translated


def materialize_case(
    item: dict[str, Any],
    review_manifest: dict[str, Any],
    review_dir: Path,
    output_dir: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    sample_id = str(item["sample_id"])
    case_name = sample_id.lower()
    source_dir = output_dir / "cases" / case_name
    patch_path = output_dir / "patches" / f"{case_name}.diff"
    repository = Path(str(item["repository"])).expanduser()
    base_commit = str(item["base_commit"])
    source_dir.mkdir(parents=True)
    archive_revision(repository, base_commit, source_dir)

    candidate_manifest = review_manifest.get("candidate_manifest")
    recorded_manifest = Path(str(candidate_manifest)).expanduser() if candidate_manifest else None
    patch_root = recorded_manifest.resolve().parent if recorded_manifest and recorded_manifest.is_file() else review_dir.parent / "mined"
    original_patch = patch_root / str(item["patch_file"])
    if not original_patch.is_file():
        raise FileNotFoundError(f"missing developer patch for {sample_id}: {original_patch}")
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(original_patch, patch_path)
    verify_patch_applies(source_dir, patch_path)

    return {
        "id": sample_id,
        "kind": "semantic_preserving_refactoring",
        "ground_truth": "safe",
        "source_dir": str(source_dir.relative_to(output_dir)),
        "candidate_diff": str(patch_path.relative_to(output_dir)),
        "build_cmd": config.get("build_cmd"),
        "test_cmd": config.get("test_cmd"),
        "expected_invariants": ["developer-reviewed semantic preservation"],
        "project_id": item["project_id"],
        "base_commit": base_commit,
        "patch_commit": item["patch_commit"],
        "changed_files": item.get("changed_files") or [],
    }


def archive_revision(repository: Path, revision: str, destination: Path) -> None:
    completed = subprocess.run(
        ["git", "archive", "--format=tar", revision],
        cwd=repository,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(message or f"could not archive {revision} from {repository}")
    with tarfile.open(fileobj=io.BytesIO(completed.stdout), mode="r:") as archive:
        archive.extractall(destination, filter="data")


def verify_patch_applies(source_dir: Path, patch_path: Path) -> None:
    completed = subprocess.run(
        ["git", "apply", "--check", str(patch_path)],
        cwd=source_dir,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(
            f"developer patch does not apply to archived base for {patch_path.name}: "
            f"{completed.stderr.strip()}"
        )


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_path(root: Path, value: str | None) -> Path:
    if value is None:
        raise ValueError("path cannot be None")
    path = Path(value)
    return path if path.is_absolute() else root / path


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
