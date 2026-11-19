"""Evaluate CARE validation gates on labeled C/C++ patch-safety cases.

The runner is intentionally independent of opportunity detection and LLM patch
generation. Each manifest case supplies a patch plus a known-safe or known-
unsafe label, enabling direct measurement of false accepts and false rejects.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from care.core.models import PatchCandidate, StageResult
from care.core.project import ProjectLoader
from care.validation.compiler import CompileChecker
from care.validation.static_analysis import StaticAnalysisRunner
from care.validation.tests import TestRunner
from care.validation.validator import RefactoringValidator

SYSTEMS = (
    "build_test",
    "build_test_static",
    "care",
    "care_no_resource",
    "care_no_semantic",
    "care_no_security",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="benchmarks/patch_safety/manifest.json")
    parser.add_argument("--output-dir", default="benchmarks/patch_safety/results")
    parser.add_argument("--systems", nargs="+", choices=SYSTEMS, default=list(SYSTEMS))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args(argv)

    root = Path.cwd().resolve()
    manifest_path = resolve_path(root, args.manifest)
    output_dir = resolve_path(root, args.output_dir)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases = manifest.get("cases") or []
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.workers < 1:
        raise ValueError("--workers must be at least 1")

    results_path = output_dir / "results.jsonl"
    completed = read_jsonl_by_case(results_path) if args.resume else {}
    pending = [case for case in cases if str(case["id"]) not in completed]
    if args.workers == 1:
        for case in pending:
            case_id = str(case["id"])
            completed[case_id] = run_case(case, manifest_path.parent, args.systems)
            append_jsonl(results_path, completed[case_id])
    else:
        with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(run_case, case, manifest_path.parent, args.systems): str(case["id"])
                for case in pending
            }
            for future in concurrent.futures.as_completed(futures):
                case_id = futures[future]
                completed[case_id] = future.result()
                append_jsonl(results_path, completed[case_id])
    results = [completed[str(case["id"])] for case in cases if str(case["id"]) in completed]
    write_json(output_dir / "results.json", {"manifest": str(manifest_path), "results": results})
    summary = summarize(results, args.systems)
    write_json(output_dir / "summary.json", summary)
    (output_dir / "summary.md").write_text(render_summary(summary), encoding="utf-8")
    print(f"evaluated {len(results)} patch-safety cases")
    return 0


def run_case(case: dict[str, Any], benchmark_root: Path, systems: list[str]) -> dict[str, Any]:
    case_id = str(case["id"])
    source_dir = benchmark_root / str(case["source_dir"])
    diff_path = benchmark_root / str(case["candidate_diff"])
    diff = diff_path.read_text(encoding="utf-8")
    result: dict[str, Any] = {
        "case_id": case_id,
        "project_id": case.get("project_id"),
        "base_commit": case.get("base_commit"),
        "patch_commit": case.get("patch_commit"),
        "parent_safe_case": case.get("parent_safe_case"),
        "kind": case.get("kind"),
        "ground_truth": case.get("ground_truth"),
        "mutation_operator": case.get("mutation_operator"),
        "expected_invariants": case.get("expected_invariants") or case.get("expected_violations") or [],
        "systems": {},
    }
    for system in systems:
        with tempfile.TemporaryDirectory(prefix=_temporary_prefix(case_id)) as temporary:
            workspace = Path(temporary) / "project"
            shutil.copytree(source_dir, workspace)
            project = ProjectLoader(
                workspace,
                build_cmd=case.get("build_cmd"),
                test_cmd=case.get("test_cmd"),
            ).load()
            candidate = PatchCandidate(
                id=f"patch-safety:{case_id}",
                opportunity_id=case_id,
                diff=diff,
                explanation=f"patch-safety benchmark case {case_id}",
                confidence=1.0,
            )
            validation = run_system(system, project, candidate)
            result["systems"][system] = validation
    return result


def _temporary_prefix(case_id: str) -> str:
    """Build a path-safe temporary prefix from a benchmark case identifier."""

    safe_case_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", case_id).strip("-") or "case"
    return f"care-patch-safety-{safe_case_id}-"


def run_system(system: str, project: object, candidate: PatchCandidate) -> dict[str, Any]:
    disabled_stages = {
        "care_no_resource": {"resource_consistency"},
        "care_no_semantic": {"semantic_equivalence"},
        "care_no_security": {"security_regression"},
    }.get(system)
    if system == "care" or disabled_stages is not None:
        return RefactoringValidator(
            project=project,
            disabled_stages=disabled_stages,
        ).validate(project, candidate).to_dict()

    stages: list[StageResult] = []
    try:
        project.backup()
        project.restore()
        patch_ok, patch_log = project.check_patch(candidate.diff)
        if not patch_ok:
            stages.append(
                StageResult(
                    name="apply_patch",
                    passed=False,
                    logs=[patch_log or "patch pre-apply check failed"],
                )
            )
            return stages_to_result(stages)
        project.apply_patch(candidate.diff)
        stages.append(StageResult(name="apply_patch", passed=True, logs=["patch applied"]))
        stages.append(CompileChecker().run(project))
        stages.append(TestRunner().run(project))
        if system == "build_test_static":
            stages.append(
                StaticAnalysisRunner().run(
                    project,
                    changed_files=changed_source_files(candidate.diff),
                )
            )
    except Exception as exc:
        stages.append(StageResult(name="benchmark_runner", passed=False, logs=[str(exc)]))
    finally:
        try:
            project.restore()
        except Exception as exc:
            stages.append(StageResult(name="restore", passed=False, logs=[str(exc)]))
    return stages_to_result(stages)


def stages_to_result(stages: list[StageResult]) -> dict[str, Any]:
    return {
        "passed": all(stage.passed for stage in stages),
        "stage_results": {stage.name: stage.to_dict() for stage in stages},
        "logs": [f"{stage.name}: {line}" for stage in stages for line in stage.logs],
        "counterexamples": [item for stage in stages for item in stage.counterexamples],
    }


def summarize(results: list[dict[str, Any]], systems: list[str]) -> dict[str, Any]:
    summary: dict[str, Any] = {"case_count": len(results), "systems": {}}
    for system in systems:
        counts = {
            "safe_total": 0,
            "unsafe_total": 0,
            "safe_accepted": 0,
            "unsafe_rejected": 0,
            "false_accepts": 0,
            "false_rejects": 0,
            "build_configured": 0,
            "build_executed": 0,
            "tests_configured": 0,
            "tests_executed": 0,
        }
        build_qualified = {
            "safe_total": 0,
            "unsafe_total": 0,
            "safe_accepted": 0,
            "unsafe_rejected": 0,
            "false_accepts": 0,
            "false_rejects": 0,
        }
        for case in results:
            truth = case.get("ground_truth")
            accepted = bool(case["systems"][system].get("passed"))
            if truth == "safe":
                counts["safe_total"] += 1
                counts["safe_accepted"] += int(accepted)
                counts["false_rejects"] += int(not accepted)
            elif truth == "unsafe":
                counts["unsafe_total"] += 1
                counts["unsafe_rejected"] += int(not accepted)
                counts["false_accepts"] += int(accepted)
            build_configured, build_executed = stage_coverage(case["systems"][system], "compile")
            test_configured, test_executed = stage_coverage(case["systems"][system], "tests")
            counts["build_configured"] += int(build_configured)
            counts["build_executed"] += int(build_executed)
            counts["tests_configured"] += int(test_configured)
            counts["tests_executed"] += int(test_executed)
            if build_executed:
                add_truth_count(build_qualified, truth, accepted)
        counts["safe_accept_rate"] = rate(counts["safe_accepted"], counts["safe_total"])
        counts["unsafe_rejection_rate"] = rate(counts["unsafe_rejected"], counts["unsafe_total"])
        build_qualified["safe_accept_rate"] = rate(build_qualified["safe_accepted"], build_qualified["safe_total"])
        build_qualified["unsafe_rejection_rate"] = rate(build_qualified["unsafe_rejected"], build_qualified["unsafe_total"])
        counts["build_qualified"] = build_qualified
        summary["systems"][system] = counts
    return summary


def add_truth_count(counts: dict[str, int], truth: str, accepted: bool) -> None:
    if truth == "safe":
        counts["safe_total"] += 1
        counts["safe_accepted"] += int(accepted)
        counts["false_rejects"] += int(not accepted)
    elif truth == "unsafe":
        counts["unsafe_total"] += 1
        counts["unsafe_rejected"] += int(not accepted)
        counts["false_accepts"] += int(accepted)


def stage_coverage(result: dict[str, Any], stage_name: str) -> tuple[bool, bool]:
    stage = (result.get("stage_results") or {}).get(stage_name) or {}
    warnings = "\n".join(stage.get("warnings") or [])
    logs = "\n".join(stage.get("logs") or [])
    configured = "no build command provided" not in warnings and "no test command provided" not in warnings
    executed = configured and ("$ " in logs or not stage.get("passed", False) or bool(logs))
    return configured, executed


def changed_source_files(diff: str) -> list[str]:
    extensions = (".c", ".cc", ".cpp", ".h", ".hpp")
    files: list[str] = []
    for line in diff.splitlines():
        if not line.startswith("+++ "):
            continue
        path = line[4:].strip()
        if path.startswith("b/"):
            path = path[2:]
        if path.endswith(extensions) and path not in files:
            files.append(path)
    return files


def render_summary(summary: dict[str, Any]) -> str:
    lines = [
        "# CARE Patch-Safety Benchmark",
        "",
        "| System | Safe accepted | Unsafe rejected | False accepts | False rejects | Build run | Tests run |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for system, counts in summary["systems"].items():
        lines.append(
            "| {system} | {safe}/{safe_total} ({safe_rate:.1%}) | "
            "{unsafe}/{unsafe_total} ({unsafe_rate:.1%}) | {false_accepts} | {false_rejects} | "
            "{build}/{case_count} | {tests}/{case_count} |".format(
                system=system,
                safe=counts["safe_accepted"],
                safe_total=counts["safe_total"],
                safe_rate=counts["safe_accept_rate"],
                unsafe=counts["unsafe_rejected"],
                unsafe_total=counts["unsafe_total"],
                unsafe_rate=counts["unsafe_rejection_rate"],
                false_accepts=counts["false_accepts"],
                false_rejects=counts["false_rejects"],
                build=counts["build_executed"],
                tests=counts["tests_executed"],
                case_count=summary["case_count"],
            )
        )
        qualified = counts["build_qualified"]
        lines.append(
            "| {system} (build-qualified) | {safe}/{safe_total} ({safe_rate:.1%}) | "
            "{unsafe}/{unsafe_total} ({unsafe_rate:.1%}) | {false_accepts} | {false_rejects} | -- | -- |".format(
                system=system,
                safe=qualified["safe_accepted"],
                safe_total=qualified["safe_total"],
                safe_rate=qualified["safe_accept_rate"],
                unsafe=qualified["unsafe_rejected"],
                unsafe_total=qualified["unsafe_total"],
                unsafe_rate=qualified["unsafe_rejection_rate"],
                false_accepts=qualified["false_accepts"],
                false_rejects=qualified["false_rejects"],
            )
        )
    return "\n".join(lines) + "\n"


def rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def resolve_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_jsonl_by_case(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    records: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            records[str(record["case_id"])] = record
    return records


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
