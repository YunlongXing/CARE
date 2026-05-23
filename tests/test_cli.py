from __future__ import annotations

import unittest

from care.cli import build_parser


class CliParserTests(unittest.TestCase):
    def test_run_parser_accepts_expected_command_shape(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "run",
                "--project",
                "/tmp/example",
                "--build-cmd",
                "make",
                "--test-cmd",
                "make test",
                "--compile-db",
                "build/compile_commands.json",
                "--max-opportunities",
                "5",
                "--max-iters",
                "3",
                "--candidates-per-iteration",
                "1",
                "--mode",
                "conservative",
                "--analysis-backend",
                "regex",
                "--cfg-backend",
                "none",
                "--detector-confirm-backend",
                "clang",
                "--require-real-llm",
            ]
        )

        self.assertEqual(args.command, "run")
        self.assertEqual(str(args.project), "/tmp/example")
        self.assertEqual(args.build_cmd, "make")
        self.assertEqual(args.test_cmd, "make test")
        self.assertEqual(str(args.compile_database_path), "build/compile_commands.json")
        self.assertEqual(args.max_opportunities, 5)
        self.assertEqual(args.max_iterations, 3)
        self.assertEqual(args.candidates_per_iteration, 1)
        self.assertEqual(args.mode, "conservative")
        self.assertEqual(args.analysis_backend, "regex")
        self.assertEqual(args.cfg_backend, "none")
        self.assertEqual(args.detector_confirm_backend, "clang")
        self.assertTrue(args.require_real_llm)

    def test_run_parser_accepts_legacy_max_iterations_alias(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "run",
                "--project",
                "/tmp/example",
                "--max-iterations",
                "2",
            ]
        )

        self.assertEqual(args.max_iterations, 2)
