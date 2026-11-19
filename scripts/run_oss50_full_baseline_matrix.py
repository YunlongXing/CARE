"""Prepare or run full OSS50 baseline comparisons.

The full matrix can be expensive: critical+high contains 11,515 opportunities,
so CARE + context-only + LLM-only + LLM-agent can require tens of thousands of
LLM calls. By default this script prepares queues and prints reproducible
commands. Pass ``--execute`` to launch the dynamic runner for each baseline.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


DEFAULT_BASELINES = "care,context-only,llm-only,llm-agent"
DEFAULT_OUTPUT_ROOT = "benchmarks/oss50/full-baselines"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite-results", default="benchmarks/oss50/results")
    parser.add_argument("--output-root", default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--baselines", default=DEFAULT_BASELINES)
    parser.add_argument("--severities", nargs="+", default=["critical", "high"])
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--mode", choices=["conservative", "aggressive"], default="conservative")
    parser.add_argument("--max-iters", type=int, default=3)
    parser.add_argument("--candidates-per-iteration", type=int, default=1)
    parser.add_argument("--analysis-backend", choices=["regex", "auto", "clang"], default="regex")
    parser.add_argument("--cfg-backend", choices=["none", "lightweight", "auto", "clang"], default="none")
    parser.add_argument("--build-profiles", default="benchmarks/build_profiles.json")
    parser.add_argument("--context-cache", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--queue-only", action="store_true", default=True)
    parser.add_argument("--execute", action="store_true", help="Actually launch LLM runs instead of queue preparation.")
    parser.add_argument("--skip-llm-probe", action="store_true")
    parser.add_argument("--continue-on-llm-error", action="store_true")
    args = parser.parse_args(argv)

    root = Path.cwd().resolve()
    output_root = (root / args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    commands = [command_for(args, baseline.strip(), output_root) for baseline in args.baselines.split(",") if baseline.strip()]
    manifest = output_root / "commands.txt"
    manifest.write_text("\n\n".join(" ".join(command) for command in commands) + "\n", encoding="utf-8")

    if not args.execute:
        for command in commands:
            queue_command = command + ["--queue-only"]
            print("preparing:", " ".join(queue_command), flush=True)
            subprocess.run(queue_command, cwd=str(root), check=True)
        print(f"prepared full baseline queues; commands written to {manifest}")
        print("rerun with --execute to launch the full LLM comparison.")
        return 0

    exit_code = 0
    for command in commands:
        print("running:", " ".join(command), flush=True)
        completed = subprocess.run(command, cwd=str(root), check=False)
        if completed.returncode != 0:
            exit_code = completed.returncode
            if not args.continue_on_llm_error:
                break
    return exit_code


def command_for(args: argparse.Namespace, baseline: str, output_root: Path) -> list[str]:
    output_dir = output_root / baseline
    effective_iters = "1" if baseline in {"llm-only", "context-only"} else str(args.max_iters)
    command = [
        sys.executable,
        "scripts/run_oss50_dynamic_validation.py",
        "--suite-results",
        args.suite_results,
        "--output-dir",
        str(output_dir),
        "--workers",
        str(args.workers),
        "--severities",
        *args.severities,
        "--mode",
        args.mode,
        "--baseline",
        baseline,
        "--max-iters",
        effective_iters,
        "--candidates-per-iteration",
        str(args.candidates_per_iteration),
        "--analysis-backend",
        args.analysis_backend,
        "--cfg-backend",
        args.cfg_backend,
        "--build-profiles",
        args.build_profiles,
        "--require-real-llm",
    ]
    if args.context_cache:
        command.append("--context-cache")
    if args.resume:
        command.append("--resume")
    if args.skip_llm_probe:
        command.append("--skip-llm-probe")
    if args.continue_on_llm_error:
        command.append("--continue-on-llm-error")
    return command


if __name__ == "__main__":
    raise SystemExit(main())
