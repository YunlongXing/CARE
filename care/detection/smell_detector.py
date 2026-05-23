"""Detect general security smells suitable for refactoring."""

from __future__ import annotations

import re

from care.analysis.context_graph import ContextGraph
from care.analysis.security_knowledge import SecurityKnowledgeBase
from care.core.models import FunctionInfo, RefactoringOpportunity
from care.detection.common import column, iter_code_lines, make_opportunity


class SmellDetector:
    """Convert security-knowledge matches and API heuristics into opportunities."""

    def detect(
        self,
        function: FunctionInfo,
        context_graph: ContextGraph,
        knowledge_base: SecurityKnowledgeBase,
    ) -> list[RefactoringOpportunity]:
        opportunities: list[RefactoringOpportunity] = []
        for smell in knowledge_base.match_security_smells(function):
            location = smell["location"]
            opportunities.append(
                make_opportunity(
                    function=function,
                    context_graph=context_graph,
                    kind="security_smell",
                    line=location["line"],
                    column=location["column"],
                    severity=smell["severity"],
                    description=smell["description"],
                    evidence={
                        "smell_kind": smell["kind"],
                        "smell_evidence": smell["evidence"],
                        "pattern": _pattern_for_smell(smell["kind"]),
                        "rules": knowledge_base.get_refactoring_rules("security_smells"),
                        "confidence": _confidence_for_smell(smell["kind"]),
                    },
                )
            )

        opportunities.extend(self._detect_poor_api_usage(function, context_graph))
        return opportunities

    def _detect_poor_api_usage(
        self,
        function: FunctionInfo,
        context_graph: ContextGraph,
    ) -> list[RefactoringOpportunity]:
        opportunities: list[RefactoringOpportunity] = []
        for line_number, raw_line, statement in iter_code_lines(function):
            if re.search(r"\batoi\s*\(", statement):
                opportunities.append(
                    make_opportunity(
                        function=function,
                        context_graph=context_graph,
                        kind="security_smell",
                        line=line_number,
                        column=column(raw_line, "atoi"),
                        severity="medium",
                        description="atoi has poor error reporting; refactoring should preserve or improve parsing semantics.",
                        evidence={
                            "smell_kind": "poor_api_usage",
                            "api": "atoi",
                            "statement": statement,
                            "pattern": "poor API usage",
                            "confidence": 0.68,
                        },
                    )
                )
            if re.search(r"\bscanf\s*\([^)]*%s", statement):
                opportunities.append(
                    make_opportunity(
                        function=function,
                        context_graph=context_graph,
                        kind="security_smell",
                        line=line_number,
                        column=column(raw_line, "scanf"),
                        severity="high",
                        description="scanf with %s may be unbounded; refactoring should avoid obscuring this risk.",
                        evidence={
                            "smell_kind": "poor_api_usage",
                            "api": "scanf",
                            "statement": statement,
                            "pattern": "poor API usage",
                            "confidence": 0.74,
                        },
                    )
                )
        return opportunities


def _pattern_for_smell(smell_kind: str) -> str:
    mapping = {
        "unsafe_function": "unsafe API usage",
        "unchecked_return_value": "unchecked return value",
        "inconsistent_error_propagation": "inconsistent error handling",
        "repeated_cleanup_blocks": "repeated manual cleanup",
        "missing_cleanup_on_error_path": "missing cleanup on error path",
        "unreachable_cleanup": "unreachable cleanup",
    }
    return mapping.get(smell_kind, smell_kind)


def _confidence_for_smell(smell_kind: str) -> float:
    mapping = {
        "unsafe_function": 0.9,
        "unchecked_return_value": 0.82,
        "inconsistent_error_propagation": 0.65,
        "repeated_cleanup_blocks": 0.78,
        "missing_cleanup_on_error_path": 0.84,
        "unreachable_cleanup": 0.8,
    }
    return mapping.get(smell_kind, 0.58)
