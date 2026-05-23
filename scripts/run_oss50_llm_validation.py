"""Run real LLM patch generation and CARE validation for OSS50 opportunities.

This script consumes the dry-run reports produced by ``run_oss50_benchmark.py``
and re-opens each project only when it has critical/high work to process. It is
designed to be resumable because the full OSS50 critical/high queue can be very
large and expensive.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import json
import os
import sys
import time
import traceback
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from care.analysis.ast_parser import ASTParser
from care.analysis.context_graph import ContextGraphBuilder
from care.config import CareConfig
from care.core.context_cache import ProjectContextCache
from care.core.models import (
    CodeLocation,
    FunctionInfo,
    RefactoringOpportunity,
    RefactoringPlan,
    ValidationResult,
)
from care.core.project import ProjectLoader
from care.llm.client import LLMClient
from care.llm.patch_generator import PatchGenerator
from care.llm.planner import RefactoringPlanner
from care.refinement.repair_loop import RepairLoop
from care.validation.taxonomy import classify_validation_result
from care.validation.validator import RefactoringValidator


DEFAULT_SEVERITIES = ("critical", "high")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite-results", default="benchmarks/oss50/results")
    parser.add_argument("--output-dir", default="benchmarks/oss50/llm-validation")
    parser.add_argument("--severities", nargs="+", default=list(DEFAULT_SEVERITIES))
    parser.add_argument(
        "--min-evidence-confidence",
        choices=["low", "medium", "high"],
        default=None,
        help="Optional evidence-confidence gate for reports produced by precision-aware detectors.",
    )
    parser.add_argument("--projects", default=None, help="Comma-separated project ids to include")
    parser.add_argument("--limit-total", type=int, default=None)
    parser.add_argument("--max-per-project", type=int, default=None)
    parser.add_argument("--start-after", default=None, help="Skip queue entries through this work id")
    parser.add_argument("--mode", choices=["conservative", "aggressive"], default="conservative")
    parser.add_argument(
        "--baseline",
        choices=["care", "llm-only"],
        default="care",
        help=(
            "Patch generation mode. 'care' uses CARE planning/context; "
            "'llm-only' gives the LLM only the opportunity and target function."
        ),
    )
    parser.add_argument("--max-iters", type=int, default=1)
    parser.add_argument("--candidates-per-iteration", type=int, default=1)
    parser.add_argument("--analysis-backend", choices=["regex", "auto", "clang"], default="regex")
    parser.add_argument("--cfg-backend", choices=["none", "lightweight", "auto", "clang"], default="none")
    parser.add_argument("--build-cmd", default=None)
    parser.add_argument("--test-cmd", default=None)
    parser.add_argument(
        "--build-profiles",
        default=None,
        help="Optional JSON file mapping project ids to build/test commands.",
    )
    parser.add_argument(
        "--context-cache",
        action="store_true",
        help="Cache and reuse parsed project context between resumable runs.",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--completed-results",
        action="append",
        default=[],
        help=(
            "Additional results.jsonl files whose work ids should be treated as completed. "
            "Useful for sharded/parallel runs."
        ),
    )
    parser.add_argument("--queue-only", action="store_true")
    parser.add_argument("--skip-llm-probe", action="store_true")
    parser.add_argument("--continue-on-llm-error", action="store_true")
    parser.add_argument("--require-real-llm", action="store_true", default=True)
    args = parser.parse_args(argv)

    root = Path.cwd().resolve()
    args.build_profiles_data = load_build_profiles(root / args.build_profiles) if args.build_profiles else {}
    suite_results = (root / args.suite_results).resolve()
    output_dir = (root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "patches").mkdir(parents=True, exist_ok=True)
    (output_dir / "logs").mkdir(parents=True, exist_ok=True)
    run_lock = None if args.queue_only else acquire_run_lock(output_dir / "run.lock")

    selected_projects = parse_project_filter(args.projects)
    severities = {severity.lower() for severity in args.severities}
    records = load_project_records(suite_results, selected_projects)
    queue = build_queue(
        suite_results=suite_results,
        records=records,
        severities=severities,
        start_after=args.start_after,
        max_per_project=args.max_per_project,
        limit_total=args.limit_total,
        min_evidence_confidence=args.min_evidence_confidence,
    )
    write_json(output_dir / "queue.json", {"queue_size": len(queue), "items": queue})
    write_queue_csv(output_dir / "queue.csv", queue)
    if args.queue_only:
        write_summary(output_dir, queue=queue, results=read_jsonl(output_dir / "results.jsonl"))
        print(f"queued {len(queue)} opportunities")
        return 0

    if args.require_real_llm:
        assert_real_llm_available()

    completed = completed_work_ids(output_dir / "results.jsonl") if args.resume else set()
    for completed_path in args.completed_results or []:
        completed.update(completed_work_ids((root / completed_path).resolve()))
    llm_client = LLMClient.from_env(require_real=args.require_real_llm)
    if args.require_real_llm and not args.skip_llm_probe:
        probe_llm_client(llm_client)

    grouped = group_queue_by_project(queue)
    total_pending = sum(1 for item in queue if item["work_id"] not in completed)
    print(f"queue entries: {len(queue)}; pending: {total_pending}; completed: {len(completed)}", flush=True)

    processed = 0
    for project_id, items in grouped.items():
        pending = [item for item in items if item["work_id"] not in completed]
        if not pending:
            continue
        print(f"[{project_id}] loading project context for {len(pending)} pending opportunities", flush=True)
        try:
            project_state = load_project_state(
                project_id=project_id,
                project_path=Path(pending[0]["project_path"]),
                args=args,
                llm_client=llm_client,
            )
        except Exception as exc:
            for item in pending:
                result = error_record(item, "load_project", exc)
                append_jsonl(output_dir / "results.jsonl", result)
                write_summary(output_dir, queue=queue, results=read_jsonl(output_dir / "results.jsonl"))
            continue

        for item in pending:
            started = time.monotonic()
            print(f"  {item['work_id']} {item['severity']} {item['kind']} {item['function']}:{item['line']}", flush=True)
            try:
                result = run_one(item, project_state, output_dir)
            except Exception as exc:
                result = error_record(item, "run_opportunity", exc)
            result["duration_seconds"] = round(time.monotonic() - started, 3)
            append_jsonl(output_dir / "results.jsonl", result)
            completed.add(item["work_id"])
            processed += 1
            write_summary(output_dir, queue=queue, results=read_jsonl(output_dir / "results.jsonl"))
            print(
                "    "
                f"passed={result.get('validation_passed')} "
                f"selected={bool(result.get('selected_patch'))} "
                f"candidates={len(result.get('candidate_patches') or [])}",
                flush=True,
            )
            if has_llm_error(result) and not args.continue_on_llm_error:
                print(
                    "stopping after LLM provider error; use --continue-on-llm-error to keep recording failures",
                    flush=True,
                )
                return 2

    results = read_jsonl(output_dir / "results.jsonl")
    write_summary(output_dir, queue=queue, results=results)
    print(f"processed {processed} new opportunities; results: {output_dir}", flush=True)
    if run_lock is not None:
        run_lock.close()
    return 0


def load_project_records(
    suite_results: Path,
    selected_projects: set[str] | None,
) -> list[dict[str, Any]]:
    summary_path = suite_results / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"missing OSS50 summary: {summary_path}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    records = [
        record
        for record in summary.get("projects", [])
        if record.get("status") == "ok"
        and (selected_projects is None or record.get("id") in selected_projects)
    ]
    return records


def acquire_run_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w", encoding="utf-8")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise RuntimeError(f"another validation run already holds {path}") from exc
    handle.write(f"pid={os.getpid()}\n")
    handle.flush()
    return handle


def build_queue(
    suite_results: Path,
    records: list[dict[str, Any]],
    severities: set[str],
    start_after: str | None,
    max_per_project: int | None,
    limit_total: int | None,
    min_evidence_confidence: str | None = None,
) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    seen_start = start_after is None
    for record in sorted(records, key=lambda item: item.get("id", "")):
        project_id = record["id"]
        report_path = Path(record["report_json"])
        if not report_path.exists():
            report_path = suite_results / "reports" / f"{project_id}-care-report.json"
        if not report_path.exists():
            continue
        report = json.loads(report_path.read_text(encoding="utf-8"))
        project_count = 0
        for index, opportunity in enumerate(report.get("detected_opportunities") or [], start=1):
            severity = str(opportunity.get("severity", "")).lower()
            if severity not in severities:
                continue
            if not _meets_evidence_confidence(opportunity, min_evidence_confidence):
                continue
            if max_per_project is not None and project_count >= max_per_project:
                break
            work_id = f"{project_id}:{index:06d}:{opportunity.get('id')}"
            if not seen_start:
                seen_start = work_id == start_after
                continue
            queue.append(queue_record(record, opportunity, index, work_id))
            project_count += 1
            if limit_total is not None and len(queue) >= limit_total:
                return queue
    return queue


def _meets_evidence_confidence(
    opportunity: dict[str, Any],
    minimum: str | None,
) -> bool:
    if minimum is None:
        return True
    rank = {"low": 1, "medium": 2, "high": 3}
    evidence = opportunity.get("evidence") or {}
    observed = str(evidence.get("evidence_confidence") or "").lower()
    if observed not in rank:
        return False
    return rank[observed] >= rank[minimum]


def queue_record(
    project: dict[str, Any],
    opportunity: dict[str, Any],
    index: int,
    work_id: str,
) -> dict[str, Any]:
    location = opportunity.get("location") or {}
    return {
        "work_id": work_id,
        "project_id": project["id"],
        "project_name": project.get("name", project["id"]),
        "project_path": project["project_path"],
        "report_index": index,
        "opportunity": opportunity,
        "opportunity_id": opportunity.get("id"),
        "severity": opportunity.get("severity"),
        "kind": opportunity.get("kind"),
        "function": opportunity.get("function"),
        "file": opportunity.get("file") or location.get("file"),
        "line": location.get("line"),
        "description": opportunity.get("description"),
    }


def load_project_state(
    project_id: str,
    project_path: Path,
    args: argparse.Namespace,
    llm_client: LLMClient,
) -> dict[str, Any]:
    profile = build_profile_for(args, project_id)
    config = CareConfig(
        project_path=project_path,
        build_cmd=args.build_cmd if args.build_cmd is not None else profile.get("build_cmd"),
        test_cmd=args.test_cmd if args.test_cmd is not None else profile.get("test_cmd"),
        max_iterations=args.max_iters,
        candidates_per_iteration=args.candidates_per_iteration,
        mode=args.mode,
        analysis_backend=args.analysis_backend,
        cfg_backend=args.cfg_backend,
        require_real_llm=args.require_real_llm,
        context_cache=args.context_cache,
    ).normalized()
    project = ProjectLoader().load(
        project_path=config.project_path,
        build_cmd=config.build_cmd,
        test_cmd=config.test_cmd,
        compile_database_path=config.compile_database_path,
    )
    context_graph = None
    if config.context_cache:
        cache = ProjectContextCache.for_project(project, config)
        context_graph = cache.load(project, config)
    if context_graph is not None:
        functions = context_graph.functions
    else:
        parser = ASTParser(prefer_clang=config.analysis_backend != "regex")
        functions = parser.parse_project(project)
    if args.baseline == "llm-only":
        context = project.to_context()
        context.functions = functions
        patch_generator = PatchGenerator(client=llm_client, default_mode=config.mode)
        validator = RefactoringValidator(config=config, project=project)
        return {
            "baseline": args.baseline,
            "project_id": project_id,
            "config": config,
            "project": project,
            "functions": functions,
            "context": context,
            "patch_generator": patch_generator,
            "validator": validator,
        }

    if context_graph is None:
        context_graph = ContextGraphBuilder(
            prefer_clang_cfg=config.cfg_backend not in {"lightweight", "none"},
            include_cfg=config.cfg_backend != "none",
        ).build(project, functions)
        if config.context_cache:
            ProjectContextCache.for_project(project, config).save(project, config, context_graph)
    context = context_graph.to_project_context()
    planner = RefactoringPlanner(build_cmd=config.build_cmd, test_cmd=config.test_cmd)
    patch_generator = PatchGenerator(client=llm_client, default_mode=config.mode)
    validator = RefactoringValidator(config=config, project=project)
    repair_loop = RepairLoop(
        patch_generator=patch_generator,
        validator=validator,
        max_iterations=config.max_iterations,
        planner=planner,
        project=project,
        context=context,
        candidates_per_iteration=config.candidates_per_iteration,
        mode=config.mode,
    )
    return {
        "baseline": args.baseline,
        "project_id": project_id,
        "config": config,
        "project": project,
        "functions": functions,
        "context_graph": context_graph,
        "context": context,
        "planner": planner,
        "patch_generator": patch_generator,
        "validator": validator,
        "repair_loop": repair_loop,
    }


def run_one(
    item: dict[str, Any],
    project_state: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    if project_state.get("baseline") == "llm-only":
        return run_one_llm_only(item, project_state, output_dir)

    opportunity = opportunity_from_dict(item["opportunity"])
    planner: RefactoringPlanner = project_state["planner"]
    context = project_state["context"]
    repair_loop: RepairLoop = project_state["repair_loop"]
    plan = planner.plan(context, opportunity)
    selected, validation = repair_loop.run(opportunity, plan=plan)
    candidates = list(getattr(repair_loop, "last_candidates", []))
    patch_records = write_patch_files(output_dir, item, candidates, selected.id if selected else None)
    return {
        **item_metadata(item),
        "status": "ok",
        "baseline": project_state.get("baseline", "care"),
        "plan": plan.to_dict(),
        "candidate_patches": patch_records,
        "selected_patch": selected.to_dict() if selected else None,
        "validation_passed": validation.passed,
        "validation": validation.to_dict(),
        "validation_summary": summarize_validation(validation),
    }


def run_one_llm_only(
    item: dict[str, Any],
    project_state: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    """Run the direct LLM baseline without CARE context or planner output."""

    opportunity = opportunity_from_dict(item["opportunity"])
    config: CareConfig = project_state["config"]
    project = project_state["project"]
    patch_generator: PatchGenerator = project_state["patch_generator"]
    validator: RefactoringValidator = project_state["validator"]
    function = find_function(project_state["functions"], opportunity)
    plan = llm_only_plan(opportunity)
    source_snippet = source_snippet_for(function, opportunity.location.line) if function else ""
    full_function_body = function.body if function else ""
    context_summary = llm_only_context_summary(opportunity)
    candidates: list[Any] = []
    failed_results: list[ValidationResult] = []
    feedback: ValidationResult | None = None

    for iteration in range(config.max_iterations):
        generated = patch_generator.generate_candidates(
            source_snippet=source_snippet,
            full_function_body=full_function_body,
            plan=plan,
            context_summary=context_summary,
            validation_feedback=llm_only_feedback(feedback) if feedback else None,
            mode=config.mode,
            n=config.candidates_per_iteration,
            project=project,
        )
        for index, candidate in enumerate(generated):
            candidate.id = f"llm-only:{opportunity.id}:{iteration}:{index}:{candidate.id.split(':')[-1]}"
        candidates.extend(generated)

        for candidate in generated:
            validation = validator.validate(project, candidate)
            if validation.passed:
                patch_records = write_patch_files(output_dir, item, candidates, candidate.id)
                return {
                    **item_metadata(item),
                    "status": "ok",
                    "baseline": "llm-only",
                    "plan": plan.to_dict(),
                    "candidate_patches": patch_records,
                    "selected_patch": candidate.to_dict(),
                    "validation_passed": True,
                    "validation": validation.to_dict(),
                    "validation_summary": summarize_validation(validation),
                }
            failed_results.append(validation)

        feedback = merge_candidate_failures(failed_results)

    final_validation = merge_candidate_failures(failed_results)
    patch_records = write_patch_files(output_dir, item, candidates, None)
    return {
        **item_metadata(item),
        "status": "ok",
        "baseline": "llm-only",
        "plan": plan.to_dict(),
        "candidate_patches": patch_records,
        "selected_patch": None,
        "validation_passed": False,
        "validation": final_validation.to_dict(),
        "validation_summary": summarize_validation(final_validation),
    }


def llm_only_plan(opportunity: RefactoringOpportunity) -> RefactoringPlan:
    return RefactoringPlan(
        opportunity_id=opportunity.id,
        intent=opportunity.description or f"Refactor {opportunity.kind}.",
        affected_files=[opportunity.file],
        affected_functions=[opportunity.function] if opportunity.function else [],
        required_invariants=[
            "Preserve functional behavior, return values, and error handling.",
            "Keep the patch local and minimal.",
        ],
        security_constraints=[
            "Do not weaken resource lifecycle handling or input validation.",
            "Do not introduce unchecked return values, leaks, double releases, or unsafe APIs.",
        ],
        behavior_preservation_goals=[
            "The refactored function should be observationally equivalent on normal and error paths.",
        ],
        patch_strategy=(
            "Generate a direct small patch for the reported issue using only the target function "
            "and opportunity description; no CARE context graph, data-flow summary, or planner "
            "strategy is provided."
        ),
        validation_strategy=["Apply the patch and run the same CARE validation pipeline."],
    )


def llm_only_context_summary(opportunity: RefactoringOpportunity) -> str:
    return json.dumps(
        {
            "baseline": "llm-only",
            "opportunity": {
                "kind": opportunity.kind,
                "severity": opportunity.severity,
                "file": opportunity.file,
                "function": opportunity.function,
                "line": opportunity.location.line,
                "description": opportunity.description,
            },
        },
        sort_keys=True,
    )


def llm_only_feedback(validation_result: ValidationResult) -> str:
    logs = "\n".join(validation_result.logs[:20]) if validation_result.logs else "(no logs)"
    counterexamples = (
        "\n".join(validation_result.counterexamples[:20])
        if validation_result.counterexamples
        else "(no counterexamples)"
    )
    return (
        "The previous direct LLM patch failed validation.\n\n"
        f"Failure logs:\n{logs}\n\n"
        f"Counterexamples:\n{counterexamples}\n\n"
        "Generate a smaller safer unified diff. Preserve behavior and avoid unrelated edits."
    )


def find_function(
    functions: list[FunctionInfo],
    opportunity: RefactoringOpportunity,
) -> FunctionInfo | None:
    for function in functions:
        if function.name == opportunity.function and Path(function.file) == Path(opportunity.file):
            return function
    for function in functions:
        if function.name == opportunity.function:
            return function
    for function in functions:
        if (
            Path(function.file) == Path(opportunity.file)
            and function.start_line <= opportunity.location.line <= function.end_line
        ):
            return function
    return None


def source_snippet_for(function: FunctionInfo, line: int, radius: int = 8) -> str:
    lines: list[str] = []
    for offset, raw_line in enumerate(function.body.splitlines()):
        absolute_line = function.start_line + offset
        if abs(absolute_line - line) <= radius:
            lines.append(f"{absolute_line}: {raw_line}")
    return "\n".join(lines)


def merge_candidate_failures(results: list[ValidationResult]) -> ValidationResult:
    if not results:
        return ValidationResult(
            passed=False,
            stage_results={},
            logs=["no patch candidates were generated"],
            counterexamples=["no patch candidates were generated"],
        )
    logs: list[str] = []
    counterexamples: list[str] = []
    stage_results = {}
    for index, result in enumerate(results):
        logs.extend(f"candidate {index}: {log}" for log in result.logs)
        counterexamples.extend(f"candidate {index}: {item}" for item in result.counterexamples)
        stage_results[f"candidate_{index}"] = result.to_dict()
    return ValidationResult(
        passed=any(result.passed for result in results),
        stage_results=stage_results,
        logs=logs,
        counterexamples=counterexamples,
    )


def write_patch_files(
    output_dir: Path,
    item: dict[str, Any],
    candidates: list[Any],
    selected_id: str | None,
) -> list[dict[str, Any]]:
    project_dir = output_dir / "patches" / safe_name(item["project_id"])
    project_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        stem = f"{item['report_index']:06d}-candidate-{index:03d}"
        path = project_dir / f"{stem}.diff"
        path.write_text(candidate.diff, encoding="utf-8")
        record = {
            "id": candidate.id,
            "path": str(path),
            "confidence": candidate.confidence,
            "explanation": candidate.explanation,
            "selected": candidate.id == selected_id,
        }
        if candidate.id == selected_id:
            selected_path = project_dir / f"{item['report_index']:06d}-selected.diff"
            selected_path.write_text(candidate.diff, encoding="utf-8")
            record["selected_path"] = str(selected_path)
        records.append(record)
    return records


def opportunity_from_dict(payload: dict[str, Any]) -> RefactoringOpportunity:
    location = payload.get("location") or {}
    return RefactoringOpportunity(
        id=payload["id"],
        kind=payload["kind"],
        file=payload["file"],
        function=payload.get("function") or "",
        location=CodeLocation(
            file=location.get("file") or payload["file"],
            line=int(location.get("line") or 1),
            column=location.get("column"),
        ),
        severity=payload.get("severity") or "unknown",
        description=payload.get("description") or "",
        evidence=payload.get("evidence") or {},
        context_summary=payload.get("context_summary") or "",
    )


def item_metadata(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "work_id": item["work_id"],
        "project_id": item["project_id"],
        "project_name": item["project_name"],
        "project_path": item["project_path"],
        "report_index": item["report_index"],
        "opportunity_id": item["opportunity_id"],
        "severity": item["severity"],
        "kind": item["kind"],
        "function": item["function"],
        "file": item["file"],
        "line": item["line"],
        "description": item["description"],
    }


def summarize_validation(validation: ValidationResult) -> dict[str, Any]:
    failed = []
    passed = []
    for name, stage in validation.stage_results.items():
        if name == "failure_taxonomy" or not isinstance(stage, dict):
            continue
        (passed if stage.get("passed") else failed).append(name)
    taxonomy = classify_validation_result(validation)
    return {
        "passed": validation.passed,
        "passed_stages": passed,
        "failed_stages": failed,
        "failure_categories": taxonomy.get("categories") or [],
        "primary_failure_category": taxonomy.get("primary_category"),
        "logs_head": validation.logs[:10],
        "counterexamples_head": validation.counterexamples[:10],
    }


def error_record(item: dict[str, Any], stage: str, exc: Exception) -> dict[str, Any]:
    return {
        **item_metadata(item),
        "status": "error",
        "error_stage": stage,
        "error": str(exc),
        "traceback": traceback.format_exc(),
        "candidate_patches": [],
        "selected_patch": None,
        "validation_passed": False,
        "validation": None,
        "validation_summary": {
            "passed": False,
            "failed_stages": [stage],
            "failure_categories": [stage],
            "primary_failure_category": stage,
            "logs_head": [str(exc)],
            "counterexamples_head": [str(exc)],
        },
    }


def write_summary(output_dir: Path, queue: list[dict[str, Any]], results: list[dict[str, Any]]) -> None:
    status_counts = Counter(result.get("status") for result in results)
    severity_counts = Counter(item.get("severity") for item in queue)
    kind_counts = Counter(item.get("kind") for item in queue)
    processed_severity = Counter(result.get("severity") for result in results)
    processed_kind = Counter(result.get("kind") for result in results)
    selected = sum(1 for result in results if result.get("selected_patch"))
    passed = sum(1 for result in results if result.get("validation_passed"))
    failed_stage_counts: Counter[str] = Counter()
    failure_category_counts: Counter[str] = Counter()
    project_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for result in results:
        project_counts[result.get("project_id", "unknown")]["processed"] += 1
        if result.get("validation_passed"):
            project_counts[result.get("project_id", "unknown")]["passed"] += 1
        for stage in (result.get("validation_summary") or {}).get("failed_stages") or []:
            failed_stage_counts[stage] += 1
        for category in (result.get("validation_summary") or {}).get("failure_categories") or []:
            failure_category_counts[category] += 1
    summary = {
        "queue_size": len(queue),
        "processed": len(results),
        "pending": max(0, len(queue) - len({result.get("work_id") for result in results})),
        "selected_patches": selected,
        "validation_passed": passed,
        "status_counts": dict(sorted(status_counts.items())),
        "queue_severity_counts": dict(sorted(severity_counts.items())),
        "queue_kind_counts": dict(sorted(kind_counts.items())),
        "processed_severity_counts": dict(sorted(processed_severity.items())),
        "processed_kind_counts": dict(sorted(processed_kind.items())),
        "failed_stage_counts": dict(sorted(failed_stage_counts.items())),
        "failure_category_counts": dict(sorted(failure_category_counts.items())),
        "projects": {
            project: dict(counts)
            for project, counts in sorted(project_counts.items())
        },
    }
    write_json(output_dir / "summary.json", summary)
    write_summary_md(output_dir / "summary.md", summary, results)
    write_results_csv(output_dir / "results.csv", results)


def write_summary_md(path: Path, summary: dict[str, Any], results: list[dict[str, Any]]) -> None:
    lines = [
        "# CARE OSS50 Real LLM Validation",
        "",
        f"- Queue size: {summary['queue_size']}",
        f"- Processed: {summary['processed']}",
        f"- Pending: {summary['pending']}",
        f"- Selected patches: {summary['selected_patches']}",
        f"- Validation passed: {summary['validation_passed']}",
        f"- Status counts: `{summary['status_counts']}`",
        f"- Queue severities: `{summary['queue_severity_counts']}`",
        f"- Failed stages: `{summary['failed_stage_counts']}`",
        f"- Failure categories: `{summary.get('failure_category_counts', {})}`",
        "",
        "## Recent Results",
        "",
        "| Work ID | Severity | Kind | Selected | Passed | Failed stages |",
        "| --- | --- | --- | ---: | ---: | --- |",
    ]
    for result in results[-25:]:
        failed = ", ".join((result.get("validation_summary") or {}).get("failed_stages") or [])
        lines.append(
            "| {work_id} | {severity} | {kind} | {selected} | {passed} | {failed} |".format(
                work_id=result.get("work_id"),
                severity=result.get("severity"),
                kind=result.get("kind"),
                selected="yes" if result.get("selected_patch") else "no",
                passed="yes" if result.get("validation_passed") else "no",
                failed=failed,
            )
        )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_results_csv(path: Path, results: list[dict[str, Any]]) -> None:
    fieldnames = [
        "work_id",
        "project_id",
        "severity",
        "kind",
        "function",
        "file",
        "line",
        "status",
        "validation_passed",
        "selected_patch",
        "duration_seconds",
        "failed_stages",
        "failure_categories",
        "description",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "work_id": result.get("work_id"),
                    "project_id": result.get("project_id"),
                    "severity": result.get("severity"),
                    "kind": result.get("kind"),
                    "function": result.get("function"),
                    "file": result.get("file"),
                    "line": result.get("line"),
                    "status": result.get("status"),
                    "validation_passed": result.get("validation_passed"),
                    "selected_patch": bool(result.get("selected_patch")),
                    "duration_seconds": result.get("duration_seconds"),
                    "failed_stages": ";".join((result.get("validation_summary") or {}).get("failed_stages") or []),
                    "failure_categories": ";".join(
                        (result.get("validation_summary") or {}).get("failure_categories") or []
                    ),
                    "description": result.get("description"),
                }
            )


def write_queue_csv(path: Path, queue: list[dict[str, Any]]) -> None:
    fieldnames = [
        "work_id",
        "project_id",
        "severity",
        "kind",
        "function",
        "file",
        "line",
        "description",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in queue:
            writer.writerow({key: item.get(key) for key in fieldnames})


def group_queue_by_project(queue: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in queue:
        grouped[item["project_id"]].append(item)
    return dict(grouped)


def completed_work_ids(path: Path) -> set[str]:
    return {record.get("work_id") for record in read_jsonl(path) if record.get("work_id")}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    records = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                if index == len(lines) - 1 and not text.endswith("\n"):
                    continue
                raise
    return records


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(json_safe(record), sort_keys=True) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(json_safe(payload), indent=2, sort_keys=True),
        encoding="utf-8",
    )


def json_safe(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, set):
        return sorted(json_safe(item) for item in value)
    return value


def safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)


def parse_project_filter(raw: str | None) -> set[str] | None:
    if not raw:
        return None
    return {item.strip() for item in raw.split(",") if item.strip()}


def load_build_profiles(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"missing build profile file: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "projects" in payload:
        payload = payload["projects"]
    if not isinstance(payload, dict):
        raise ValueError("build profile file must contain an object")
    return payload


def build_profile_for(args: argparse.Namespace, project_id: str) -> dict[str, Any]:
    profiles = getattr(args, "build_profiles_data", {}) or {}
    profile = profiles.get(project_id) or profiles.get(project_id.lower()) or {}
    if not isinstance(profile, dict):
        return {}
    return profile


def assert_real_llm_available() -> None:
    provider = os.environ.get("CARE_LLM_PROVIDER", "").strip().lower()
    api_key = os.environ.get("CARE_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if provider in {"mock", "local_mock"}:
        raise RuntimeError("CARE_LLM_PROVIDER is set to mock; real LLM validation requires OpenAI-compatible mode")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY or CARE_LLM_API_KEY is required for real LLM validation")


def probe_llm_client(client: LLMClient) -> None:
    try:
        response = client.complete("Return exactly CARE_LLM_READY.", temperature=0.0)
    except Exception as exc:
        raise RuntimeError(f"real LLM provider probe failed: {exc}") from exc
    if "CARE_LLM_READY" not in response:
        raise RuntimeError(f"real LLM provider probe returned unexpected response: {response[:200]}")


def has_llm_error(result: dict[str, Any]) -> bool:
    haystack = json.dumps(result.get("candidate_patches") or [], sort_keys=True)
    haystack += "\n" + json.dumps(result.get("validation_summary") or {}, sort_keys=True)
    markers = [
        "LLM patch generation failed",
        "LLM request failed",
        "insufficient_quota",
        "rate_limit",
        "invalid_api_key",
    ]
    return any(marker in haystack for marker in markers)


if __name__ == "__main__":
    raise SystemExit(main())
