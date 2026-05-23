"""Command-line interface for CARE."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from care.config import (
    SUPPORTED_ANALYSIS_BACKENDS,
    SUPPORTED_CFG_BACKENDS,
    SUPPORTED_DETECTOR_CONFIRM_BACKENDS,
    SUPPORTED_REFACTORING_MODES,
    CareConfig,
)
from care.core.pipeline import CAREPipeline
from care.reporting.reporter import ConsoleReporter
from care.utils.logging import configure_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="care",
        description="CARE: Context-aware Automated Refactoring Engine.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="Run the CARE refactoring pipeline for a C/C++ project.",
    )
    run_parser.add_argument(
        "--project",
        required=True,
        type=Path,
        help="Path to the target C/C++ codebase.",
    )
    run_parser.add_argument(
        "--build-cmd",
        default=None,
        help='Build command used for validation, for example "make".',
    )
    run_parser.add_argument(
        "--test-cmd",
        default=None,
        help='Test command used for validation, for example "make test".',
    )
    run_parser.add_argument(
        "--compile-db",
        dest="compile_database_path",
        default=None,
        type=Path,
        help="Optional path to compile_commands.json.",
    )
    run_parser.add_argument(
        "--max-opportunities",
        type=int,
        default=None,
        help="Maximum ranked opportunities to plan and refactor.",
    )
    run_parser.add_argument(
        "--max-iters",
        "--max-iterations",
        dest="max_iterations",
        type=int,
        default=3,
        help="Maximum feedback-guided repair iterations per candidate.",
    )
    run_parser.add_argument(
        "--candidates-per-iteration",
        type=int,
        default=3,
        help="Number of LLM patch candidates to generate per repair iteration.",
    )
    run_parser.add_argument(
        "--mode",
        choices=sorted(SUPPORTED_REFACTORING_MODES),
        default="conservative",
        help="Patch generation mode.",
    )
    run_parser.add_argument(
        "--analysis-backend",
        choices=sorted(SUPPORTED_ANALYSIS_BACKENDS),
        default="auto",
        help="Function extraction backend. Use regex for scalable whole-tree scans.",
    )
    run_parser.add_argument(
        "--cfg-backend",
        choices=sorted(SUPPORTED_CFG_BACKENDS),
        default="auto",
        help="CFG backend. Use none or lightweight for scalable whole-tree scans.",
    )
    run_parser.add_argument(
        "--detector-confirm-backend",
        choices=sorted(SUPPORTED_DETECTOR_CONFIRM_BACKENDS),
        default="none",
        help="Optional confirmation pass for detector findings.",
    )
    run_parser.add_argument(
        "--require-real-llm",
        action="store_true",
        help="Fail instead of using CARE's deterministic mock LLM provider.",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Construct context and plans without applying patches.",
    )
    run_parser.add_argument(
        "--context-cache",
        action="store_true",
        help="Cache parsed functions and context graph for repeated project runs.",
    )
    run_parser.add_argument(
        "--context-cache-dir",
        default=None,
        type=Path,
        help="Optional directory for context cache files.",
    )
    run_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging.",
    )

    return parser


def run_command(args: argparse.Namespace) -> int:
    configure_logging(verbose=args.verbose)
    config = CareConfig(
        project_path=args.project,
        build_cmd=args.build_cmd,
        test_cmd=args.test_cmd,
        compile_database_path=args.compile_database_path,
        max_opportunities=args.max_opportunities,
        max_iterations=args.max_iterations,
        candidates_per_iteration=args.candidates_per_iteration,
        mode=args.mode,
        analysis_backend=args.analysis_backend,
        cfg_backend=args.cfg_backend,
        detector_confirm_backend=args.detector_confirm_backend,
        require_real_llm=args.require_real_llm,
        dry_run=args.dry_run,
        context_cache=args.context_cache,
        context_cache_dir=args.context_cache_dir,
    )
    result = CAREPipeline(config).run()
    ConsoleReporter().emit(result)
    return 0 if result.success else 1


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        return run_command(args)

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
