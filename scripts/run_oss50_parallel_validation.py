"""Run OSS50 LLM validation safely in multiple project-sharded processes."""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_oss50_llm_validation import (  # noqa: E402
    DEFAULT_SEVERITIES,
    build_queue,
    completed_work_ids,
    group_queue_by_project,
    load_project_records,
    parse_project_filter,
    read_jsonl,
    write_json,
    write_queue_csv,
    write_summary,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite-results", default="benchmarks/oss50/results")
    parser.add_argument("--output-dir", default="benchmarks/oss50/llm-validation-parallel")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--severities", nargs="+", default=list(DEFAULT_SEVERITIES))
    parser.add_argument("--min-evidence-confidence", choices=["low", "medium", "high"], default=None)
    parser.add_argument("--projects", default=None)
    parser.add_argument("--limit-total", type=int, default=None)
    parser.add_argument("--max-per-project", type=int, default=None)
    parser.add_argument("--mode", choices=["conservative", "aggressive"], default="conservative")
    parser.add_argument("--baseline", choices=["care", "llm-only"], default="care")
    parser.add_argument("--max-iters", type=int, default=1)
    parser.add_argument("--candidates-per-iteration", type=int, default=1)
    parser.add_argument("--analysis-backend", choices=["regex", "auto", "clang"], default="regex")
    parser.add_argument("--cfg-backend", choices=["none", "lightweight", "auto", "clang"], default="none")
    parser.add_argument("--build-cmd", default=None)
    parser.add_argument("--test-cmd", default=None)
    parser.add_argument("--build-profiles", default=None)
    parser.add_argument("--context-cache", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--queue-only", action="store_true")
    parser.add_argument("--skip-llm-probe", action="store_true")
    parser.add_argument("--continue-on-llm-error", action="store_true")
    parser.add_argument("--require-real-llm", action="store_true", default=True)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    args = parser.parse_args(argv)

    if args.workers < 1:
        raise ValueError("--workers must be at least 1")

    root = Path.cwd().resolve()
    suite_results = (root / args.suite_results).resolve()
    output_dir = (root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "logs").mkdir(parents=True, exist_ok=True)
    workers_dir = output_dir / "workers"
    workers_dir.mkdir(parents=True, exist_ok=True)

    selected_projects = parse_project_filter(args.projects)
    records = load_project_records(suite_results, selected_projects)
    queue = build_queue(
        suite_results=suite_results,
        records=records,
        severities={severity.lower() for severity in args.severities},
        start_after=None,
        max_per_project=args.max_per_project,
        limit_total=args.limit_total,
        min_evidence_confidence=args.min_evidence_confidence,
    )
    write_json(output_dir / "queue.json", {"queue_size": len(queue), "items": queue})
    write_queue_csv(output_dir / "queue.csv", queue)

    completed = completed_work_ids(output_dir / "results.jsonl") if args.resume else set()
    for worker_results in workers_dir.glob("worker-*/results.jsonl"):
        completed.update(completed_work_ids(worker_results))

    shards = assign_project_shards(queue, workers=args.workers, completed=completed)
    write_shard_manifest(output_dir / "shards.json", shards)
    if args.queue_only:
        merge_worker_outputs(output_dir, queue)
        print(f"queued {len(queue)} opportunities across {len(shards)} shards")
        return 0

    processes = start_workers(
        root=root,
        output_dir=output_dir,
        workers_dir=workers_dir,
        shards=shards,
        args=args,
    )
    try:
        while True:
            merge_worker_outputs(output_dir, queue)
            running = [process for process in processes if process.poll() is None]
            if not running:
                break
            print_progress(output_dir, queue, processes)
            time.sleep(max(1.0, args.poll_seconds))
    finally:
        merge_worker_outputs(output_dir, queue)

    failures = [process for process in processes if process.returncode not in {0, None}]
    print_progress(output_dir, queue, processes)
    return 1 if failures else 0


@dataclass
class WorkerProcess:
    index: int
    projects: list[str]
    process: subprocess.Popen[str]
    log_path: Path

    @property
    def returncode(self) -> int | None:
        return self.process.returncode

    def poll(self) -> int | None:
        return self.process.poll()


def assign_project_shards(
    queue: list[dict[str, Any]],
    workers: int,
    completed: set[str] | None = None,
) -> list[dict[str, Any]]:
    completed = completed or set()
    grouped = group_queue_by_project(item for item in queue if item["work_id"] not in completed)
    worker_items = [
        {"worker": index, "projects": [], "queue_size": 0}
        for index in range(workers)
    ]
    project_sizes = sorted(
        ((project_id, len(items)) for project_id, items in grouped.items()),
        key=lambda item: (-item[1], item[0]),
    )
    for project_id, size in project_sizes:
        target = min(worker_items, key=lambda item: (item["queue_size"], item["worker"]))
        target["projects"].append(project_id)
        target["queue_size"] += size
    return [item for item in worker_items if item["projects"]]


def write_shard_manifest(path: Path, shards: list[dict[str, Any]]) -> None:
    write_json(
        path,
        {
            "workers": len(shards),
            "total_pending": sum(int(shard["queue_size"]) for shard in shards),
            "shards": shards,
        },
    )


def start_workers(
    root: Path,
    output_dir: Path,
    workers_dir: Path,
    shards: list[dict[str, Any]],
    args: argparse.Namespace,
) -> list[WorkerProcess]:
    processes: list[WorkerProcess] = []
    for shard in shards:
        index = int(shard["worker"])
        projects = sorted(shard["projects"])
        worker_dir = workers_dir / f"worker-{index:02d}"
        worker_dir.mkdir(parents=True, exist_ok=True)
        log_path = output_dir / "logs" / f"worker-{index:02d}.log"
        command = worker_command(
            worker_dir=worker_dir,
            projects=projects,
            args=args,
            completed_results=output_dir / "results.jsonl",
        )
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n=== worker {index} start {time.strftime('%Y-%m-%dT%H:%M:%S')} ===\n")
            log.write(" ".join(command) + "\n")
        handle = log_path.open("a", encoding="utf-8")
        process = subprocess.Popen(
            command,
            cwd=str(root),
            env=os.environ.copy(),
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        processes.append(
            WorkerProcess(index=index, projects=projects, process=process, log_path=log_path)
        )
    return processes


def worker_command(
    worker_dir: Path,
    projects: list[str],
    args: argparse.Namespace,
    completed_results: Path,
) -> list[str]:
    command = [
        sys.executable,
        "scripts/run_oss50_llm_validation.py",
        "--suite-results",
        args.suite_results,
        "--output-dir",
        str(worker_dir),
        "--projects",
        ",".join(projects),
        "--severities",
        *args.severities,
        "--mode",
        args.mode,
        "--baseline",
        args.baseline,
        "--max-iters",
        str(args.max_iters),
        "--candidates-per-iteration",
        str(args.candidates_per_iteration),
        "--analysis-backend",
        args.analysis_backend,
        "--cfg-backend",
        args.cfg_backend,
        "--completed-results",
        str(completed_results),
    ]
    if args.min_evidence_confidence:
        command.extend(["--min-evidence-confidence", args.min_evidence_confidence])
    if args.max_per_project is not None:
        command.extend(["--max-per-project", str(args.max_per_project)])
    if args.build_cmd:
        command.extend(["--build-cmd", args.build_cmd])
    if args.test_cmd:
        command.extend(["--test-cmd", args.test_cmd])
    if args.build_profiles:
        command.extend(["--build-profiles", args.build_profiles])
    if args.context_cache:
        command.append("--context-cache")
    if args.resume:
        command.append("--resume")
    if args.skip_llm_probe:
        command.append("--skip-llm-probe")
    if args.continue_on_llm_error:
        command.append("--continue-on-llm-error")
    if args.require_real_llm:
        command.append("--require-real-llm")
    return command


def merge_worker_outputs(output_dir: Path, queue: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records_by_work_id: dict[str, dict[str, Any]] = {}
    existing = read_jsonl(output_dir / "results.jsonl")
    for record in existing:
        work_id = record.get("work_id")
        if work_id:
            records_by_work_id[str(work_id)] = record
    for worker_results in sorted((output_dir / "workers").glob("worker-*/results.jsonl")):
        for record in read_jsonl(worker_results):
            work_id = record.get("work_id")
            if work_id:
                records_by_work_id[str(work_id)] = record

    ordered_work_ids = [item["work_id"] for item in queue]
    ordered = [
        records_by_work_id[work_id]
        for work_id in ordered_work_ids
        if work_id in records_by_work_id
    ]
    extras = [
        record
        for work_id, record in sorted(records_by_work_id.items())
        if work_id not in set(ordered_work_ids)
    ]
    merged = ordered + extras
    write_results_jsonl(output_dir / "results.jsonl", merged)
    write_summary(output_dir, queue=queue, results=merged)
    write_worker_status(output_dir, queue=queue, results=merged)
    return merged


def write_results_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")


def write_worker_status(
    output_dir: Path,
    queue: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> None:
    by_project: dict[str, Counter[str]] = defaultdict(Counter)
    for item in queue:
        by_project[item["project_id"]]["queued"] += 1
    for result in results:
        project = result.get("project_id", "unknown")
        by_project[project]["processed"] += 1
        if result.get("validation_passed"):
            by_project[project]["passed"] += 1
    rows = [
        {
            "project_id": project,
            "queued": counts.get("queued", 0),
            "processed": counts.get("processed", 0),
            "passed": counts.get("passed", 0),
        }
        for project, counts in sorted(by_project.items())
    ]
    with (output_dir / "project_status.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["project_id", "queued", "processed", "passed"])
        writer.writeheader()
        writer.writerows(rows)


def print_progress(
    output_dir: Path,
    queue: list[dict[str, Any]],
    processes: list[WorkerProcess],
) -> None:
    results = read_jsonl(output_dir / "results.jsonl")
    processed = len({result.get("work_id") for result in results if result.get("work_id")})
    passed = sum(1 for result in results if result.get("validation_passed"))
    running = sum(1 for process in processes if process.poll() is None)
    failed = sum(1 for process in processes if process.returncode not in {0, None})
    print(
        f"parallel validation: processed={processed}/{len(queue)} "
        f"passed={passed} running_workers={running} failed_workers={failed}",
        flush=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
