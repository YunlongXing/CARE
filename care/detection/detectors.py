"""Detector orchestration and ranking."""

from __future__ import annotations

from typing import Optional, Protocol

from care.analysis.context_graph import ContextGraph
from care.analysis.security_knowledge import SecurityKnowledgeBase
from care.core.models import FunctionInfo, RefactoringOpportunity
from care.detection.clang_confirm import ClangConfirmationPass
from care.detection.dead_code_detector import DeadCodeDetector
from care.detection.duplicate_check_detector import DuplicateCheckDetector
from care.detection.goto_detector import GotoDetector
from care.detection.post_filter import apply_post_filters
from care.detection.resource_detector import ResourceDetector
from care.detection.smell_detector import SmellDetector

SEVERITY_RANK = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
    "info": 0,
}


class FunctionDetector(Protocol):
    def detect(
        self,
        function: FunctionInfo,
        context_graph: ContextGraph,
        knowledge_base: SecurityKnowledgeBase,
    ) -> list[RefactoringOpportunity]:
        """Return opportunities for a single function."""


class OpportunityDetector:
    """Run all function-level detectors and rank opportunities."""

    def __init__(
        self,
        detectors: Optional[list[FunctionDetector]] = None,
        knowledge_base: Optional[SecurityKnowledgeBase] = None,
        confirm_backend: str = "none",
    ) -> None:
        self.detectors = detectors if detectors is not None else self.default_detectors()
        self.knowledge_base = knowledge_base or SecurityKnowledgeBase()
        self.confirm_backend = confirm_backend

    @classmethod
    def default_detectors(cls) -> list[FunctionDetector]:
        return [
            GotoDetector(),
            ResourceDetector(),
            DuplicateCheckDetector(),
            DeadCodeDetector(),
            SmellDetector(),
        ]

    def detect(
        self,
        project: object,
        functions: list[FunctionInfo],
        context_graph: ContextGraph,
    ) -> list[RefactoringOpportunity]:
        opportunities: list[RefactoringOpportunity] = []
        for function in functions:
            for detector in self.detectors:
                opportunities.extend(detector.detect(function, context_graph, self.knowledge_base))
        filtered = apply_post_filters(self._cluster(self._dedupe(opportunities)))
        if self.confirm_backend == "clang":
            filtered = ClangConfirmationPass().confirm(project, filtered)
        return self._rank(filtered)

    def _rank(self, opportunities: list[RefactoringOpportunity]) -> list[RefactoringOpportunity]:
        return sorted(
            opportunities,
            key=lambda opportunity: (
                -SEVERITY_RANK.get(opportunity.severity, 0),
                -float(opportunity.evidence.get("confidence", 0.0)),
                opportunity.file,
                opportunity.location.line,
                opportunity.kind,
            ),
        )

    def _dedupe(self, opportunities: list[RefactoringOpportunity]) -> list[RefactoringOpportunity]:
        seen: set[tuple[str, str, int, str]] = set()
        result: list[RefactoringOpportunity] = []
        for opportunity in opportunities:
            key = (
                opportunity.kind,
                opportunity.function,
                opportunity.location.line,
                opportunity.description,
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(opportunity)
        return result

    def _cluster(self, opportunities: list[RefactoringOpportunity]) -> list[RefactoringOpportunity]:
        clusters: dict[tuple[str, str, str, str, str, str], list[RefactoringOpportunity]] = {}
        passthrough: list[RefactoringOpportunity] = []
        for opportunity in opportunities:
            if opportunity.kind not in {"resource_imbalance", "resource_early_return"}:
                passthrough.append(opportunity)
                continue
            evidence = opportunity.evidence
            resource = str(evidence.get("resource_key") or evidence.get("resource_variable") or "")
            pattern = str(evidence.get("pattern") or "")
            if not resource or not pattern:
                passthrough.append(opportunity)
                continue
            key = (
                opportunity.kind,
                opportunity.file,
                opportunity.function,
                resource,
                pattern,
                str(evidence.get("expected_release") or evidence.get("expected_acquire") or ""),
            )
            clusters.setdefault(key, []).append(opportunity)

        clustered = [_merge_cluster(items) for items in clusters.values()]
        return passthrough + clustered


def _merge_cluster(items: list[RefactoringOpportunity]) -> RefactoringOpportunity:
    if len(items) == 1:
        return items[0]
    ordered = sorted(
        items,
        key=lambda opportunity: (
            -SEVERITY_RANK.get(opportunity.severity, 0),
            -float(opportunity.evidence.get("confidence", 0.0)),
            opportunity.location.line,
        ),
    )
    representative = ordered[0]
    evidence_points = [
        {
            "id": item.id,
            "line": item.location.line,
            "severity": item.severity,
            "confidence": item.evidence.get("confidence"),
            "description": item.description,
        }
        for item in sorted(items, key=lambda item: item.location.line)
    ]
    representative.evidence = dict(representative.evidence)
    representative.evidence["cluster_size"] = len(items)
    representative.evidence["clustered_opportunity_ids"] = [item.id for item in items]
    representative.evidence["evidence_points"] = evidence_points
    representative.description = (
        f"{representative.description} Clustered {len(items)} similar findings "
        f"for the same function/resource/pattern."
    )
    return representative


class CompositeDetector(OpportunityDetector):
    """Backward-compatible alias for the Step 6 orchestrator."""
