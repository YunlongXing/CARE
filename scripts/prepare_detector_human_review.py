"""Prepare a deterministic, stratified human review set for CARE detectors.

This script performs no LLM calls. It samples detector candidates from the
OSS50 dry-scan reports, writes immutable review materials, and creates two
independent reviewer sheets plus a separate adjudication sheet.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from care.detection.post_filter import deployment_contexts
from care.detection.common import evidence_confidence_label
from scripts.run_oss50_llm_validation import load_project_records


DEFAULT_OUTPUT_DIR = "benchmarks/oss50/issta-detector-review"
DEFAULT_SUITE_RESULTS = "benchmarks/oss50/results"
FAMILY_ORDER = ("goto", "resource", "duplicate_check", "dead_code", "security_smell")
REVIEW_LABELS = "true_positive|false_positive|unclear"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite-results", default=DEFAULT_SUITE_RESULTS)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--sample-size", type=int, default=400)
    parser.add_argument("--seed", type=int, default=20261013)
    parser.add_argument("--max-per-project-family", type=int, default=8)
    parser.add_argument("--snippet-radius", type=int, default=45)
    args = parser.parse_args(argv)

    if args.sample_size < len(FAMILY_ORDER):
        parser.error(f"--sample-size must be at least {len(FAMILY_ORDER)}")

    root = Path.cwd().resolve()
    suite_results = (root / args.suite_results).resolve()
    output_dir = (root / args.output_dir).resolve()
    materials_dir = output_dir / "materials"
    materials_dir.mkdir(parents=True, exist_ok=True)

    records = load_project_records(suite_results, selected_projects=None)
    candidates = load_candidates(suite_results, records, root)
    quotas = family_quotas(args.sample_size)
    sample = stratified_sample(
        candidates,
        quotas=quotas,
        seed=args.seed,
        max_per_project_family=args.max_per_project_family,
    )
    sample = replace_unavailable_sources(
        sample,
        candidates,
        seed=args.seed,
        max_per_project_family=args.max_per_project_family,
    )
    annotate_sampling_weights(candidates, sample)
    hydrate_selected_items(sample, root)

    for position, item in enumerate(sample, start=1):
        item["sample_id"] = f"D{position:04d}"
        material_path = materials_dir / f"{item['sample_id']}.md"
        item["material_file"] = str(material_path.relative_to(output_dir))
        material_path.write_text(
            render_material(item, snippet_radius=args.snippet_radius),
            encoding="utf-8",
        )

    manifest_items = [portable_manifest_item(item, root) for item in sample]
    manifest = {
        "schema_version": 1,
        "seed": args.seed,
        "requested_sample_size": args.sample_size,
        "actual_sample_size": len(sample),
        "family_quotas": quotas,
        "source_summary": str((suite_results / "summary.json").relative_to(root)),
        "sample_digest": sample_digest(sample),
        "items": manifest_items,
    }
    write_json(output_dir / "sample_manifest.json", manifest)
    write_review_sheet(output_dir / "reviewer_1.csv", sample)
    write_review_sheet(output_dir / "reviewer_2.csv", sample)
    write_adjudication_sheet(output_dir / "adjudication.csv", sample)
    write_summary(output_dir / "sample_summary.json", candidates, sample, quotas)
    (output_dir / "REVIEW_PROTOCOL.md").write_text(review_protocol(), encoding="utf-8")

    print(f"prepared {len(sample)} detector candidates in {output_dir}")
    print(f"sample digest: {manifest['sample_digest']}")
    return 0


def detector_family(kind: str | None) -> str | None:
    value = str(kind or "")
    if value.startswith("goto_"):
        return "goto"
    if value.startswith("resource_"):
        return "resource"
    if value in {"duplicate_check", "dead_code", "security_smell"}:
        return value
    return None


def family_quotas(sample_size: int) -> dict[str, int]:
    base, remainder = divmod(sample_size, len(FAMILY_ORDER))
    return {
        family: base + (1 if index < remainder else 0)
        for index, family in enumerate(FAMILY_ORDER)
    }


def load_candidates(
    suite_results: Path,
    records: list[dict[str, Any]],
    root: Path,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda item: item.get("id", "")):
        project_id = str(record["id"])
        report_path = suite_results / "reports" / f"{project_id}-care-report.json"
        if not report_path.exists():
            continue
        report = json.loads(report_path.read_text(encoding="utf-8"))
        for index, opportunity in enumerate(report.get("detected_opportunities") or [], start=1):
            kind = str(opportunity.get("kind") or "")
            family = detector_family(kind)
            if family is None:
                continue
            evidence = opportunity.get("evidence") or {}
            location = opportunity.get("location") or {}
            source_path = local_source_path(
                opportunity.get("file") or location.get("file"),
                project_id,
                root,
            )
            contexts = deployment_contexts(str(source_path))
            candidates.append(
                {
                    "work_id": f"{project_id}:{index:06d}:{opportunity.get('id')}",
                    "project_id": project_id,
                    "report_file": str(report_path),
                    "report_index": index,
                    "kind": kind,
                    "family": family,
                    "severity": str(opportunity.get("severity") or "unknown"),
                    "function": str(opportunity.get("function") or ""),
                    "file": str(source_path),
                    "line": location.get("line"),
                    "description": str(opportunity.get("description") or ""),
                    "evidence_confidence": observed_evidence_confidence(evidence),
                    "deployment_context": contexts[0] if contexts else "production",
                    "relative_source": repository_relative_path(source_path),
                    "root_relative_file": root_relative(source_path, root),
                }
            )
    return candidates


def hydrate_selected_items(sample: list[dict[str, Any]], root: Path) -> None:
    by_report: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in sample:
        by_report[item["report_file"]].append(item)

    for report_file, items in by_report.items():
        report = json.loads(Path(report_file).read_text(encoding="utf-8"))
        opportunities = report.get("detected_opportunities") or []
        for item in items:
            opportunity = opportunities[int(item["report_index"]) - 1]
            evidence = opportunity.get("evidence") or {}
            source_path = Path(item["file"])
            item["detector_confidence"] = evidence.get("confidence")
            item["analysis_backend"] = (evidence.get("verifier") or {}).get(
                "analysis_backend", "unknown"
            )
            item["source_exists"] = source_path.exists()
            item["context_summary"] = opportunity.get("context_summary") or ""
            item["evidence"] = evidence
            item["root_relative_file"] = root_relative(source_path, root)


def observed_evidence_confidence(evidence: dict[str, Any]) -> str:
    observed = str(evidence.get("evidence_confidence") or "").lower()
    if observed in {"low", "medium", "high"}:
        return observed
    try:
        return evidence_confidence_label(float(evidence.get("confidence", 0.5)))
    except (TypeError, ValueError):
        return "unknown"


def replace_unavailable_sources(
    sample: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    seed: int,
    max_per_project_family: int,
) -> list[dict[str, Any]]:
    selected_ids = {item["work_id"] for item in sample}
    initially_available = [item for item in sample if Path(item["file"]).exists()]
    project_counts = Counter((item["family"], item["project_id"]) for item in initially_available)
    seen_functions = {function_identity(item) for item in initially_available}
    replacements_by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in candidates:
        if item["work_id"] not in selected_ids:
            replacements_by_family[item["family"]].append(item)

    available: list[dict[str, Any]] = []
    for item in sample:
        if Path(item["file"]).exists():
            available.append(item)
            continue
        candidates_for_family = replacements_by_family[item["family"]]
        candidates_for_family.sort(
            key=lambda candidate: (
                stratum_key(candidate) != stratum_key(item),
                stable_rank(seed, candidate["work_id"]),
            )
        )
        replacement = next(
            (
                candidate
                for candidate in candidates_for_family
                if Path(candidate["file"]).exists()
                and project_counts[(candidate["family"], candidate["project_id"])]
                < max_per_project_family
                and function_identity(candidate) not in seen_functions
            ),
            None,
        )
        if replacement is None:
            continue
        candidates_for_family.remove(replacement)
        selected_ids.add(replacement["work_id"])
        project_counts[(replacement["family"], replacement["project_id"])] += 1
        seen_functions.add(function_identity(replacement))
        available.append(replacement)
    available.sort(key=lambda item: (FAMILY_ORDER.index(item["family"]), item["work_id"]))
    return available


def annotate_sampling_weights(
    candidates: list[dict[str, Any]],
    sample: list[dict[str, Any]],
) -> None:
    population_counts = Counter((item["family"], stratum_key(item)) for item in candidates)
    sample_counts = Counter((item["family"], stratum_key(item)) for item in sample)
    for item in sample:
        key = (item["family"], stratum_key(item))
        item["stratum_population"] = population_counts[key]
        item["stratum_sample"] = sample_counts[key]
        item["sampling_weight"] = round(population_counts[key] / sample_counts[key], 6)


def local_source_path(value: Any, project_id: str, root: Path) -> Path:
    path = Path(str(value or ""))
    parts = path.parts
    for index in range(len(parts) - 2):
        if parts[index : index + 3] == ("benchmarks", "oss50", "sources"):
            return root / Path(*parts[index:])
    for index in range(len(parts) - 1):
        if parts[index : index + 2] == ("benchmarks", "openssl"):
            return root / Path(*parts[index:])
    fallback_root = root / "benchmarks" / "oss50" / "sources" / project_id
    if project_id == "openssl":
        fallback_root = root / "benchmarks" / "openssl"
    if not path.is_absolute():
        return fallback_root / path
    return path


def enrich_item(item: dict[str, Any], root: Path) -> dict[str, Any]:
    """Backward-compatible helper retained for external callers and tests."""

    opportunity = item.get("opportunity") or {}
    evidence = opportunity.get("evidence") or {}
    source_path = Path(str(item.get("file") or ""))
    relative_source = repository_relative_path(source_path)
    contexts = deployment_contexts(str(source_path))
    deployment_context = contexts[0] if contexts else "production"
    return {
        **item,
        "family": detector_family(item.get("kind")),
        "evidence_confidence": str(evidence.get("evidence_confidence") or "unknown"),
        "detector_confidence": evidence.get("confidence"),
        "analysis_backend": (evidence.get("verifier") or {}).get("analysis_backend", "unknown"),
        "deployment_context": deployment_context,
        "relative_source": relative_source,
        "source_exists": source_path.exists(),
        "context_summary": opportunity.get("context_summary") or "",
        "evidence": evidence,
        "root_relative_file": root_relative(source_path, root),
    }


def repository_relative_path(path: Path) -> str:
    parts = path.parts
    for index in range(len(parts) - 1):
        if parts[index : index + 2] == ("benchmarks", "openssl"):
            return str(Path(*parts[index + 2 :]))
    for marker in ("sources", "srcs", "projects"):
        if marker not in parts:
            continue
        index = len(parts) - 1 - tuple(reversed(parts)).index(marker)
        if index + 2 < len(parts):
            return str(Path(*parts[index + 2 :]))
    return str(path)


def root_relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root))
    except (OSError, ValueError):
        return str(path)


def portable_manifest_item(item: dict[str, Any], root: Path) -> dict[str, Any]:
    """Store checkout-owned paths relative to the repository root."""

    portable = dict(item)
    for key in ("file", "report_file"):
        value = portable.get(key)
        if value:
            portable[key] = root_relative(Path(str(value)), root)
    return portable


def stratum_key(item: dict[str, Any]) -> tuple[str, str, str]:
    severity = str(item.get("severity") or "unknown")
    severity_bucket = "high_impact" if severity in {"critical", "high"} else "other_impact"
    evidence = str(item.get("evidence_confidence") or "unknown")
    deployment = "production" if item.get("deployment_context") == "production" else "non_production"
    return severity_bucket, evidence, deployment


def stratified_sample(
    candidates: Iterable[dict[str, Any]],
    quotas: dict[str, int],
    seed: int,
    max_per_project_family: int,
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in candidates:
        family = item.get("family")
        if family in quotas:
            by_family[family].append(item)

    selected: list[dict[str, Any]] = []
    for family in FAMILY_ORDER:
        quota = quotas.get(family, 0)
        family_items = list(by_family.get(family, []))
        rng.shuffle(family_items)
        family_items.sort(
            key=lambda item: (
                stratum_key(item),
                stable_rank(seed, item["work_id"]),
            )
        )
        selected.extend(
            select_family_items(
                family_items,
                quota=quota,
                max_per_project=max_per_project_family,
            )
        )

    selected.sort(key=lambda item: (FAMILY_ORDER.index(item["family"]), item["work_id"]))
    return selected


def select_family_items(
    items: list[dict[str, Any]],
    quota: int,
    max_per_project: int,
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        groups[stratum_key(item)].append(item)

    ordered_groups = sorted(groups)
    project_counts: Counter[str] = Counter()
    seen_functions: set[tuple[str, str, str]] = set()
    selected: list[dict[str, Any]] = []

    while len(selected) < quota:
        made_progress = False
        for key in ordered_groups:
            group = groups[key]
            choice_index = next(
                (
                    index
                    for index, item in enumerate(group)
                    if project_counts[item["project_id"]] < max_per_project
                    and function_identity(item) not in seen_functions
                ),
                None,
            )
            if choice_index is None:
                continue
            item = group.pop(choice_index)
            selected.append(item)
            project_counts[item["project_id"]] += 1
            seen_functions.add(function_identity(item))
            made_progress = True
            if len(selected) >= quota:
                break
        if not made_progress:
            break

    if len(selected) < quota:
        selected_ids = {item["work_id"] for item in selected}
        for item in items:
            if item["work_id"] in selected_ids:
                continue
            if project_counts[item["project_id"]] >= max_per_project:
                continue
            selected.append(item)
            selected_ids.add(item["work_id"])
            project_counts[item["project_id"]] += 1
            if len(selected) >= quota:
                break
    return selected


def function_identity(item: dict[str, Any]) -> tuple[str, str, str]:
    return item["project_id"], str(item.get("relative_source") or ""), str(item.get("function") or "")


def stable_rank(seed: int, work_id: str) -> str:
    return hashlib.sha256(f"{seed}:{work_id}".encode("utf-8")).hexdigest()


def sample_digest(items: list[dict[str, Any]]) -> str:
    payload = "\n".join(item["work_id"] for item in items)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def render_material(item: dict[str, Any], snippet_radius: int) -> str:
    snippet = source_snippet(Path(str(item.get("file") or "")), int(item.get("line") or 1), snippet_radius)
    evidence = json.dumps(blinded_evidence(item.get("evidence") or {}), indent=2, sort_keys=True, default=str)
    return (
        f"# Detector Review {item['sample_id']}\n\n"
        f"- Project: `{item['project_id']}`\n"
        f"- File: `{item['relative_source']}`\n"
        f"- Function: `{item.get('function') or ''}`\n"
        f"- Location: line {item.get('line') or ''}\n"
        f"- Candidate description: {item.get('description') or ''}\n\n"
        "## Source Context\n\n"
        "```c\n"
        f"{snippet}\n"
        "```\n\n"
        "## Detector Evidence\n\n"
        "```json\n"
        f"{evidence}\n"
        "```\n"
    )


def blinded_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    hidden = {
        "confidence",
        "evidence_confidence",
        "security_impact",
        "severity_before_post_filter",
        "verifier",
    }
    return {key: value for key, value in evidence.items() if key not in hidden}


def source_snippet(path: Path, line: int, radius: int) -> str:
    if not path.exists():
        return "[source file unavailable in this checkout]"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, line - radius)
    end = min(len(lines), line + radius)
    return "\n".join(f"{number:6d} | {lines[number - 1]}" for number in range(start, end + 1))


def write_review_sheet(path: Path, sample: list[dict[str, Any]]) -> None:
    fieldnames = [
        "sample_id",
        "material_file",
        "project_id",
        "relative_source",
        "function",
        "line",
        "candidate_description",
        "verdict",
        "actionable_refactoring",
        "safety_relevant",
        "reason",
        "reviewer_confidence",
    ]
    rows = [
        {
            "sample_id": item["sample_id"],
            "material_file": item["material_file"],
            "project_id": item["project_id"],
            "relative_source": item["relative_source"],
            "function": item.get("function") or "",
            "line": item.get("line") or "",
            "candidate_description": item.get("description") or "",
            "verdict": "",
            "actionable_refactoring": "",
            "safety_relevant": "",
            "reason": "",
            "reviewer_confidence": "",
        }
        for item in sample
    ]
    write_csv(path, rows, fieldnames)


def write_adjudication_sheet(path: Path, sample: list[dict[str, Any]]) -> None:
    fieldnames = [
        "sample_id",
        "family",
        "kind",
        "severity",
        "evidence_confidence",
        "deployment_context",
        "reviewer_1_verdict",
        "reviewer_2_verdict",
        "agreement",
        "final_verdict",
        "adjudication_reason",
        "eligible_for_patch_benchmark",
    ]
    rows = [
        {
            "sample_id": item["sample_id"],
            "family": item["family"],
            "kind": item["kind"],
            "severity": item["severity"],
            "evidence_confidence": item["evidence_confidence"],
            "deployment_context": item["deployment_context"],
            "reviewer_1_verdict": "",
            "reviewer_2_verdict": "",
            "agreement": "",
            "final_verdict": "",
            "adjudication_reason": "",
            "eligible_for_patch_benchmark": "",
        }
        for item in sample
    ]
    write_csv(path, rows, fieldnames)


def write_summary(
    path: Path,
    candidates: list[dict[str, Any]],
    sample: list[dict[str, Any]],
    quotas: dict[str, int],
) -> None:
    summary = {
        "candidate_count": len(candidates),
        "sample_count": len(sample),
        "requested_family_quotas": quotas,
        "sample_by_family": dict(sorted(Counter(item["family"] for item in sample).items())),
        "sample_by_kind": dict(sorted(Counter(item["kind"] for item in sample).items())),
        "sample_by_severity": dict(sorted(Counter(item["severity"] for item in sample).items())),
        "sample_by_evidence_confidence": dict(
            sorted(Counter(item["evidence_confidence"] for item in sample).items())
        ),
        "sample_by_deployment_context": dict(
            sorted(Counter(item["deployment_context"] for item in sample).items())
        ),
        "projects_represented": len({item["project_id"] for item in sample}),
        "source_files_available": sum(bool(item["source_exists"]) for item in sample),
    }
    write_json(path, summary)


def review_protocol() -> str:
    return f"""# CARE Detector Human Review Protocol

## Goal

Estimate detector precision independently of the LLM and construct a curated
pool for later patch-generation experiments. Reviewers must work independently
and must not inspect the other reviewer's sheet before both sheets are frozen.

## Unit of Review

Each row identifies one detector candidate and a corresponding Markdown file in
`materials/`. Judge the candidate shown at the reported source revision. Do not
judge whether an LLM could repair it.

## Verdicts

- `true_positive`: the reported condition exists and represents an actionable
  behavior-preserving refactoring or a concrete reliability/security defect.
- `false_positive`: the reported condition does not exist, is explained by the
  shown control/resource semantics, or is too weak to justify a source change.
- `unclear`: the available local and project context is insufficient. Use this
  only after inspecting the containing function and directly relevant helpers.

The allowed `verdict` values are: `{REVIEW_LABELS}`.

Set `actionable_refactoring` and `safety_relevant` to `yes`, `no`, or `unclear`.
Set `reviewer_confidence` to an integer from 1 (low) through 5 (high). Give a
specific reason grounded in control flow, data flow, ownership, or API behavior.

## Independence and Adjudication

1. Reviewer 1 completes `reviewer_1.csv`; Reviewer 2 completes
   `reviewer_2.csv` independently.
2. Freeze both files before comparison.
3. Merge verdicts into `adjudication.csv` and mark exact agreement.
4. Discuss disagreements and all `unclear` rows; record one final verdict and
   an adjudication reason.
5. Only adjudicated true positives from production code with sufficient context
   may enter the main patch benchmark. Non-production cases are reported
   separately.

## Reporting

Report overall and per-family precision with Wilson 95% confidence intervals,
raw agreement, Cohen's kappa, unclear rate, production/non-production results,
and the number of adjudicated findings eligible for patch generation.
"""


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
