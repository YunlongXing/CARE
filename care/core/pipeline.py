"""End-to-end CARE pipeline orchestration."""

from __future__ import annotations

import logging
from typing import Optional

from care.analysis.ast_parser import ASTParser
from care.analysis.context_graph import ContextGraphBuilder
from care.config import CareConfig
from care.core.context_cache import ProjectContextCache
from care.core.models import PipelineResult, RefactoringOpportunity
from care.core.project import ProjectLoader
from care.detection.detectors import OpportunityDetector
from care.llm.client import LLMClient
from care.llm.patch_generator import PatchGenerator
from care.llm.planner import RefactoringPlanner
from care.refinement.repair_loop import RepairLoop
from care.reporting.diff_report import DiffReportBuilder
from care.validation.validator import RefactoringValidator

logger = logging.getLogger(__name__)


class CAREPipeline:
    """Coordinate analysis, planning, patching, validation, and reporting."""

    def __init__(self, config: Optional[CareConfig] = None) -> None:
        self.config = config.normalized() if config is not None else None
        self.project_loader = ProjectLoader()
        self.ast_parser: Optional[ASTParser] = None
        self.context_builder: Optional[ContextGraphBuilder] = None
        self.detector = OpportunityDetector()
        self.planner: Optional[RefactoringPlanner] = None
        self.patch_generator: Optional[PatchGenerator] = None
        self.report_builder = DiffReportBuilder()
        if self.config is not None:
            self._configure_llm_components(self.config)

    def run(self, config: Optional[CareConfig] = None) -> PipelineResult:
        active_config = self._active_config(config)
        self._configure_llm_components(active_config)
        self._configure_analysis_components(active_config)
        assert self.planner is not None
        assert self.patch_generator is not None
        assert self.ast_parser is not None
        assert self.context_builder is not None

        logger.info("loading project: %s", active_config.project_path)
        project = self.project_loader.load(
            project_path=active_config.project_path,
            build_cmd=active_config.build_cmd,
            test_cmd=active_config.test_cmd,
            compile_database_path=active_config.compile_database_path,
        )

        logger.info("constructing program context")
        context_graph = None
        if active_config.context_cache:
            cache = ProjectContextCache.for_project(project, active_config)
            try:
                context_graph = cache.load(project, active_config)
                if context_graph is not None:
                    logger.info("loaded context graph from cache")
            except Exception as exc:
                logger.warning("failed to load context cache: %s", exc)

        if context_graph is None:
            functions = self.ast_parser.parse_project(project)
            logger.info("parsed %d functions", len(functions))
            context_graph = self.context_builder.build(project, functions)
            if active_config.context_cache:
                try:
                    cache = ProjectContextCache.for_project(project, active_config)
                    path = cache.save(project, active_config, context_graph)
                    logger.info("saved context graph cache: %s", path)
                except Exception as exc:
                    logger.warning("failed to save context cache: %s", exc)
        else:
            functions = context_graph.functions
            self.context_builder.graph = context_graph
        context = context_graph.to_project_context()

        logger.info("detecting refactoring opportunities")
        opportunities = self.detector.detect(project, functions, context_graph)
        opportunities = self._select_opportunities(
            opportunities,
            max_opportunities=active_config.max_opportunities,
        )

        plans = []
        patches = []
        candidate_patches = []
        validations = []
        if not active_config.dry_run:
            validator = RefactoringValidator(config=active_config, project=project)
            repair_loop = RepairLoop(
                patch_generator=self.patch_generator,
                validator=validator,
                max_iterations=active_config.max_iterations,
                planner=self.planner,
                project=project,
                context=context,
                candidates_per_iteration=active_config.candidates_per_iteration,
                mode=active_config.mode,
            )

        for opportunity in opportunities:
            logger.info("planning refactoring opportunity: %s", opportunity.id)
            plan = self.planner.plan(context, opportunity)
            plans.append(plan)

            if active_config.dry_run:
                continue

            candidate, validation = repair_loop.run(opportunity, plan=plan)
            candidate_patches.extend(getattr(repair_loop, "last_candidates", []))
            if candidate is not None:
                patches.append(candidate)
            validations.append(validation)

        report = self.report_builder.build(
            context=context,
            opportunities=opportunities,
            plans=plans,
            patches=patches,
            validations=validations,
        )
        structured_report = self.report_builder.build_structured(
            context=context,
            opportunities=opportunities,
            patches=patches,
            validations=validations,
        )
        success = all(validation.passed for validation in validations) if validations else True

        return PipelineResult(
            success=success,
            project=context.root,
            opportunities=opportunities,
            plans=plans,
            patches=patches,
            candidate_patches=candidate_patches,
            validations=validations,
            structured_report=structured_report,
            report=report,
        )

    def _active_config(self, config: Optional[CareConfig]) -> CareConfig:
        if config is not None:
            self.config = config.normalized()
        if self.config is None:
            raise ValueError("CAREPipeline.run requires a CareConfig")
        return self.config

    def _configure_llm_components(self, config: CareConfig) -> None:
        self.planner = RefactoringPlanner(
            build_cmd=config.build_cmd,
            test_cmd=config.test_cmd,
        )
        self.patch_generator = PatchGenerator(
            client=LLMClient.from_env(require_real=config.require_real_llm),
            default_mode=config.mode,
        )

    def _configure_analysis_components(self, config: CareConfig) -> None:
        self.ast_parser = ASTParser(prefer_clang=config.analysis_backend != "regex")
        self.context_builder = ContextGraphBuilder(
            prefer_clang_cfg=config.cfg_backend not in {"lightweight", "none"},
            include_cfg=config.cfg_backend != "none",
        )
        if hasattr(self.detector, "confirm_backend"):
            self.detector.confirm_backend = config.detector_confirm_backend

    @staticmethod
    def _select_opportunities(
        opportunities: list[RefactoringOpportunity],
        max_opportunities: Optional[int],
    ) -> list[RefactoringOpportunity]:
        if max_opportunities is None:
            return opportunities
        return opportunities[:max_opportunities]


CarePipeline = CAREPipeline
