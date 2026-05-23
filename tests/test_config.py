from __future__ import annotations

from pathlib import Path
import unittest

from care.config import CareConfig


class ConfigTests(unittest.TestCase):
    def test_normalized_preserves_step_13_config_fields(self) -> None:
        config = CareConfig(
            project_path=Path("."),
            max_opportunities=5,
            max_iterations=3,
            candidates_per_iteration=1,
            mode="aggressive",
            analysis_backend="regex",
            cfg_backend="none",
            detector_confirm_backend="clang",
            require_real_llm=True,
            dry_run=True,
        ).normalized()

        self.assertEqual(config.max_opportunities, 5)
        self.assertEqual(config.max_iterations, 3)
        self.assertEqual(config.candidates_per_iteration, 1)
        self.assertEqual(config.mode, "aggressive")
        self.assertEqual(config.analysis_backend, "regex")
        self.assertEqual(config.cfg_backend, "none")
        self.assertEqual(config.detector_confirm_backend, "clang")
        self.assertTrue(config.require_real_llm)
        self.assertTrue(config.project_path.is_absolute())

    def test_normalized_rejects_invalid_limits_and_modes(self) -> None:
        with self.assertRaises(ValueError):
            CareConfig(project_path=Path("."), max_opportunities=-1).normalized()
        with self.assertRaises(ValueError):
            CareConfig(project_path=Path("."), max_iterations=0).normalized()
        with self.assertRaises(ValueError):
            CareConfig(project_path=Path("."), candidates_per_iteration=0).normalized()
        with self.assertRaises(ValueError):
            CareConfig(project_path=Path("."), mode="wild").normalized()
        with self.assertRaises(ValueError):
            CareConfig(project_path=Path("."), analysis_backend="wild").normalized()
        with self.assertRaises(ValueError):
            CareConfig(project_path=Path("."), cfg_backend="wild").normalized()
        with self.assertRaises(ValueError):
            CareConfig(project_path=Path("."), detector_confirm_backend="wild").normalized()
