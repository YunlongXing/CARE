"""Configuration for a CARE pipeline run."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

SUPPORTED_REFACTORING_MODES = {"conservative", "aggressive"}
SUPPORTED_ANALYSIS_BACKENDS = {"auto", "clang", "regex"}
SUPPORTED_CFG_BACKENDS = {"auto", "clang", "lightweight", "none"}
SUPPORTED_DETECTOR_CONFIRM_BACKENDS = {"none", "clang"}


@dataclass
class CareConfig:
    """Runtime settings supplied by the CLI or future API integrations."""

    project_path: Path
    build_cmd: Optional[str] = None
    test_cmd: Optional[str] = None
    compile_database_path: Optional[Path] = None
    max_opportunities: Optional[int] = None
    max_iterations: int = 3
    candidates_per_iteration: int = 3
    mode: str = "conservative"
    analysis_backend: str = "auto"
    cfg_backend: str = "auto"
    detector_confirm_backend: str = "none"
    require_real_llm: bool = False
    dry_run: bool = False
    context_cache: bool = False
    context_cache_dir: Optional[Path] = None

    def normalized(self) -> "CareConfig":
        if self.max_opportunities is not None and self.max_opportunities < 0:
            raise ValueError("max_opportunities must be non-negative")
        if self.max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")
        if self.candidates_per_iteration < 1:
            raise ValueError("candidates_per_iteration must be at least 1")
        if self.mode not in SUPPORTED_REFACTORING_MODES:
            raise ValueError(f"unsupported refactoring mode: {self.mode}")
        if self.analysis_backend not in SUPPORTED_ANALYSIS_BACKENDS:
            raise ValueError(f"unsupported analysis backend: {self.analysis_backend}")
        if self.cfg_backend not in SUPPORTED_CFG_BACKENDS:
            raise ValueError(f"unsupported CFG backend: {self.cfg_backend}")
        if self.detector_confirm_backend not in SUPPORTED_DETECTOR_CONFIRM_BACKENDS:
            raise ValueError(f"unsupported detector confirmation backend: {self.detector_confirm_backend}")

        return CareConfig(
            project_path=self.project_path.expanduser().resolve(),
            build_cmd=self.build_cmd,
            test_cmd=self.test_cmd,
            compile_database_path=(
                self.compile_database_path.expanduser().resolve()
                if self.compile_database_path is not None
                else None
            ),
            max_opportunities=self.max_opportunities,
            max_iterations=self.max_iterations,
            candidates_per_iteration=self.candidates_per_iteration,
            mode=self.mode,
            analysis_backend=self.analysis_backend,
            cfg_backend=self.cfg_backend,
            detector_confirm_backend=self.detector_confirm_backend,
            require_real_llm=self.require_real_llm,
            dry_run=self.dry_run,
            context_cache=self.context_cache,
            context_cache_dir=(
                self.context_cache_dir.expanduser().resolve()
                if self.context_cache_dir is not None
                else None
            ),
        )
