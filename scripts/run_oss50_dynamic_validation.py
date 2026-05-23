"""Run OSS50 LLM validation with dynamic per-opportunity work stealing.

The project-sharded runner is efficient while projects are balanced, but it can
leave CPU idle near the end of a long run. This runner safely drains the
remaining queue by reserving individual work items and validating each worker in
its own project copy, so workers never mutate the same source tree at once.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from care.llm.client import LLMClient  # noqa: E402
from scripts.run_oss50_llm_validation import (  # noqa: E402
    DEFAULT_SEVERITIES,
    append_jsonl,
    assert_real_llm_available,
    build_queue,
    completed_work_ids,
    error_record,
    load_build_profiles,
    load_project_records,
    load_project_state,
    parse_project_filter,
    probe_llm_client,
    read_jsonl,
    run_one,
    safe_name,
    write_json,
    write_queue_csv,
    write_summary,
)
from scripts.run_oss50_parallel_validation import merge_worker_outputs  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.worker:
        return worker_main(args)
    return coordinator_main(args)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite-results", default="benchmarks/oss50/results")
    parser.add_argument("--output-dir", default="benchmarks/oss50/llm-validation")
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
    parser.add_argument(
        "--reservation-stale-seconds",
        type=float,
        default=12 * 60 * 60,
        help="Allow a worker to reclaim a reservation older than this many seconds.",
    )
    parser.add_argument(
        "--workspace-root",
        default=None,
        help="Directory for isolated per-worker project copies; defaults under output-dir.",
    )
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--worker-index", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--queue-file", default=None, help=argparse.SUPPRESS)
    return parser


def coordinator_main(args: argparse.Namespace) -> int:
    if args.workers < 1:
        raise ValueError("--workers must be at least 1")

    root = Path.cwd().resolve()
    output_dir = (root / args.output_dir).resolve()
    suite_results = (root / args.suite_results).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "logs").mkdir(parents=True, exist_ok=True)
    (output_dir / "workers").mkdir(parents=True, exist_ok=True)

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
    dynamic_dir = output_dir / "dynamic"
    dynamic_dir.mkdir(parents=True, exist_ok=True)
    queue_file = dynamic_dir / "queue.json"
    write_json(queue_file, {"queue_size": len(queue), "items": queue})

    completed = collect_completed_work_ids(output_dir) if args.resume else set()
    pending = [item for item in queue if item["work_id"] not in completed]
    write_json(
        dynamic_dir / "manifest.json",
        {
            "queue_size": len(queue),
            "completed_at_start": len(completed),
            "pending_at_start": len(pending),
            "workers": args.workers,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
    )
    if args.queue_only:
        merge_worker_outputs(output_dir, queue)
        print(f"queued {len(queue)} opportunities; pending dynamic work: {len(pending)}")
        return 0

    if args.require_real_llm:
        assert_real_llm_available()
        if not args.skip_llm_probe:
            probe_llm_client(LLMClient.from_env(require_real=True))

    processes = start_workers(root=root, output_dir=output_dir, queue_file=queue_file, args=args)
    try:
        while True:
            merge_worker_outputs(output_dir, queue)
            running = [process for process in processes if process.poll() is None]
            print_dynamic_progress(output_dir, queue, processes)
            if not running:
                break
            time.sleep(max(1.0, args.poll_seconds))
    finally:
        merge_worker_outputs(output_dir, queue)
        write_dynamic_status(output_dir, queue, processes)

    failures = [process for process in processes if process.returncode not in {0, None}]
    print_dynamic_progress(output_dir, queue, processes)
    return 1 if failures else 0


@dataclass
class DynamicWorkerProcess:
    index: int
    process: subprocess.Popen[str]
    log_path: Path

    @property
    def returncode(self) -> int | None:
        return self.process.returncode

    def poll(self) -> int | None:
        return self.process.poll()


def start_workers(
    root: Path,
    output_dir: Path,
    queue_file: Path,
    args: argparse.Namespace,
) -> list[DynamicWorkerProcess]:
    processes: list[DynamicWorkerProcess] = []
    for index in range(args.workers):
        log_path = output_dir / "logs" / f"worker-dynamic-{index:02d}.log"
        command = worker_command(index=index, queue_file=queue_file, args=args)
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n=== dynamic worker {index} start {time.strftime('%Y-%m-%dT%H:%M:%S')} ===\n")
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
        processes.append(DynamicWorkerProcess(index=index, process=process, log_path=log_path))
    return processes


def worker_command(index: int, queue_file: Path, args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        "scripts/run_oss50_dynamic_validation.py",
        "--worker",
        "--worker-index",
        str(index),
        "--queue-file",
        str(queue_file),
        "--suite-results",
        args.suite_results,
        "--output-dir",
        args.output_dir,
        "--workers",
        str(args.workers),
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
        "--reservation-stale-seconds",
        str(args.reservation_stale_seconds),
    ]
    if args.min_evidence_confidence:
        command.extend(["--min-evidence-confidence", args.min_evidence_confidence])
    if args.projects:
        command.extend(["--projects", args.projects])
    if args.limit_total is not None:
        command.extend(["--limit-total", str(args.limit_total)])
    if args.max_per_project is not None:
        command.extend(["--max-per-project", str(args.max_per_project)])
    if args.build_cmd:
        command.extend(["--build-cmd", args.build_cmd])
    if args.test_cmd:
        command.extend(["--test-cmd", args.test_cmd])
    if args.build_profiles:
        command.extend(["--build-profiles", args.build_profiles])
    if args.workspace_root:
        command.extend(["--workspace-root", args.workspace_root])
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


def worker_main(args: argparse.Namespace) -> int:
    if args.queue_file is None:
        raise ValueError("--queue-file is required for dynamic workers")

    root = Path.cwd().resolve()
    output_dir = (root / args.output_dir).resolve()
    worker_dir = output_dir / "workers" / f"worker-dynamic-{args.worker_index:02d}"
    worker_dir.mkdir(parents=True, exist_ok=True)
    (worker_dir / "patches").mkdir(parents=True, exist_ok=True)
    (worker_dir / "logs").mkdir(parents=True, exist_ok=True)
    reservation_dir = output_dir / "dynamic" / "reservations"
    reservation_dir.mkdir(parents=True, exist_ok=True)
    workspace_root = (
        (root / args.workspace_root).resolve()
        if args.workspace_root
        else output_dir / "dynamic" / "workspaces"
    )
    workspace_root.mkdir(parents=True, exist_ok=True)

    if args.build_profiles:
        args.build_profiles_data = load_build_profiles(root / args.build_profiles)
    else:
        args.build_profiles_data = {}

    if args.require_real_llm:
        assert_real_llm_available()
    llm_client = LLMClient.from_env(require_real=args.require_real_llm)
    queue = json.loads(Path(args.queue_file).read_text(encoding="utf-8")).get("items") or []
    project_states: dict[str, dict[str, Any]] = {}
    project_paths: dict[str, Path] = {}
    processed = 0

    while True:
        item = claim_next_item(
            queue=queue,
            output_dir=output_dir,
            reservation_dir=reservation_dir,
            worker_index=args.worker_index,
            stale_seconds=args.reservation_stale_seconds,
        )
        if item is None:
            break
        started = time.monotonic()
        print(
            f"dynamic worker {args.worker_index}: {item['work_id']} "
            f"{item['severity']} {item['kind']} {item['function']}:{item['line']}",
            flush=True,
        )
        original_project_path = Path(item["project_path"]).resolve()
        run_item = dict(item)
        try:
            project_path = project_paths.get(item["project_id"])
            if project_path is None:
                project_path = prepare_project_workspace(
                    src=original_project_path,
                    workspace_root=workspace_root,
                    worker_index=args.worker_index,
                    project_id=item["project_id"],
                )
                project_paths[item["project_id"]] = project_path
            run_item["original_project_path"] = str(original_project_path)
            run_item["project_path"] = str(project_path)
            project_state = project_states.get(item["project_id"])
            if project_state is None:
                project_state = load_project_state(
                    project_id=item["project_id"],
                    project_path=project_path,
                    args=args,
                    llm_client=llm_client,
                )
                project_states[item["project_id"]] = project_state
            result = run_one(run_item, project_state, worker_dir)
            result["original_project_path"] = str(original_project_path)
            result["validation_project_path"] = str(project_path)
            result["project_path"] = str(original_project_path)
        except Exception as exc:
            result = error_record(item, "dynamic_worker", exc)
            result["traceback"] = traceback.format_exc()
        result["duration_seconds"] = round(time.monotonic() - started, 3)
        append_jsonl(worker_dir / "results.jsonl", result)
        processed += 1
        print(
            "    "
            f"passed={result.get('validation_passed')} "
            f"selected={bool(result.get('selected_patch'))} "
            f"candidates={len(result.get('candidate_patches') or [])}",
            flush=True,
        )
        if has_llm_error(result) and not args.continue_on_llm_error:
            print("stopping dynamic worker after LLM provider error", flush=True)
            return 2

    print(f"dynamic worker {args.worker_index}: processed {processed} opportunities", flush=True)
    return 0


def claim_next_item(
    queue: list[dict[str, Any]],
    output_dir: Path,
    reservation_dir: Path,
    worker_index: int,
    stale_seconds: float,
) -> dict[str, Any] | None:
    reservation_dir.mkdir(parents=True, exist_ok=True)
    lock_path = reservation_dir / "queue.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        completed = collect_completed_work_ids(output_dir)
        for item in queue:
            work_id = str(item["work_id"])
            if work_id in completed:
                continue
            reservation = reservation_path(reservation_dir, work_id)
            if reservation.exists():
                if is_stale(reservation, stale_seconds):
                    reservation.unlink(missing_ok=True)
                else:
                    continue
            try:
                fd = os.open(str(reservation), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            except FileExistsError:
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "work_id": work_id,
                        "worker_index": worker_index,
                        "pid": os.getpid(),
                        "reserved_at": time.time(),
                    },
                    handle,
                    sort_keys=True,
                )
                handle.write("\n")
            return item
        return None


def collect_completed_work_ids(output_dir: Path) -> set[str]:
    completed = completed_work_ids(output_dir / "results.jsonl")
    for worker_results in sorted((output_dir / "workers").glob("worker-*/results.jsonl")):
        completed.update(completed_work_ids(worker_results))
    return completed


def reservation_path(reservation_dir: Path, work_id: str) -> Path:
    digest = hashlib.sha256(work_id.encode("utf-8")).hexdigest()[:16]
    prefix = safe_name(work_id)[:120]
    return reservation_dir / f"{prefix}-{digest}.lock"


def is_stale(path: Path, stale_seconds: float) -> bool:
    if stale_seconds <= 0:
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        reserved_at = float(payload.get("reserved_at") or 0)
    except Exception:
        reserved_at = path.stat().st_mtime
    return time.time() - reserved_at > stale_seconds


def prepare_project_workspace(
    src: Path,
    workspace_root: Path,
    worker_index: int,
    project_id: str,
) -> Path:
    dst = workspace_root / f"worker-{worker_index:02d}" / safe_name(project_id)
    marker = dst / ".care-workspace-source"
    if dst.exists() and marker.exists() and marker.read_text(encoding="utf-8").strip() == str(src):
        return dst
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    copy_project_tree(src, dst)
    marker.write_text(str(src) + "\n", encoding="utf-8")
    return dst


def copy_project_tree(src: Path, dst: Path) -> None:
    rsync = shutil.which("rsync")
    if rsync:
        dst.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                rsync,
                "-a",
                "--delete",
                "--exclude",
                ".git/",
                f"{src}/",
                f"{dst}/",
            ],
            check=True,
        )
        return
    shutil.copytree(
        src,
        dst,
        symlinks=True,
        ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"),
    )


def has_llm_error(result: dict[str, Any]) -> bool:
    text = " ".join(
        [
            str(result.get("error") or ""),
            " ".join((result.get("validation_summary") or {}).get("logs_head") or []),
            " ".join((result.get("validation_summary") or {}).get("counterexamples_head") or []),
        ]
    ).lower()
    markers = [
        "rate limit",
        "insufficient_quota",
        "api key",
        "authentication",
        "openai",
        "llm provider",
    ]
    return any(marker in text for marker in markers)


def print_dynamic_progress(
    output_dir: Path,
    queue: list[dict[str, Any]],
    processes: list[DynamicWorkerProcess],
) -> None:
    results = read_jsonl(output_dir / "results.jsonl")
    processed = len({result.get("work_id") for result in results if result.get("work_id")})
    passed = sum(1 for result in results if result.get("validation_passed"))
    running = sum(1 for process in processes if process.poll() is None)
    failed = sum(1 for process in processes if process.returncode not in {0, None})
    print(
        f"dynamic validation: processed={processed}/{len(queue)} "
        f"passed={passed} running_workers={running} failed_workers={failed}",
        flush=True,
    )


def write_dynamic_status(
    output_dir: Path,
    queue: list[dict[str, Any]],
    processes: list[DynamicWorkerProcess],
) -> None:
    reservations = list((output_dir / "dynamic" / "reservations").glob("*.lock"))
    results = read_jsonl(output_dir / "results.jsonl")
    write_json(
        output_dir / "dynamic" / "status.json",
        {
            "queue_size": len(queue),
            "processed": len({result.get("work_id") for result in results if result.get("work_id")}),
            "passed": sum(1 for result in results if result.get("validation_passed")),
            "reservations": max(0, len(reservations) - 1),
            "workers": [
                {
                    "worker": process.index,
                    "returncode": process.returncode,
                    "log_path": str(process.log_path),
                }
                for process in processes
            ],
        },
    )
    write_summary(output_dir, queue=queue, results=results)


if __name__ == "__main__":
    raise SystemExit(main())
