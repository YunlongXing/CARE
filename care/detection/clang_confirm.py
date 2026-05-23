"""Optional clang-backed confirmation for detector findings."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from care.analysis.ast_parser import ASTParser
from care.analysis.clang_tools import compile_args_for_project
from care.core.models import FunctionInfo, RefactoringOpportunity
from care.detection.common import (
    confidence,
    evidence_confidence_label,
    gated_severity,
    verifier_verdict,
)


class ClangConfirmationPass:
    """Confirm high-value findings against clang-extracted function ranges."""

    def __init__(self, parser: ASTParser | None = None) -> None:
        self.parser = parser or ASTParser(prefer_clang=True)

    def confirm(
        self,
        project: object,
        opportunities: Iterable[RefactoringOpportunity],
        priority_only: bool = True,
    ) -> list[RefactoringOpportunity]:
        items = list(opportunities)
        if not items:
            return items
        if priority_only:
            candidates = [item for item in items if _should_confirm(item)]
            for opportunity in items:
                if opportunity not in candidates:
                    _mark_skipped(opportunity, "below clang confirm priority threshold")
        else:
            candidates = items
        if not candidates:
            return items
        if getattr(self.parser, "clang_bin", None) is None:
            for opportunity in candidates:
                _mark_unavailable(opportunity, "clang binary unavailable")
            return items

        functions_by_file = self._parse_candidate_files(project, candidates)
        for opportunity in candidates:
            clang_functions = functions_by_file.get(_canonical_file(opportunity.file), [])
            confirmed = _matches_clang_function(opportunity, clang_functions)
            if confirmed:
                _mark_confirmed(opportunity)
            else:
                _mark_unconfirmed(opportunity)
        return items

    def _parse_candidate_files(
        self,
        project: object,
        opportunities: list[RefactoringOpportunity],
    ) -> dict[str, list[FunctionInfo]]:
        compile_args = compile_args_for_project(project)
        project_root = Path(getattr(project, "root", ".")).resolve()
        results: dict[str, list[FunctionInfo]] = {}
        for file_path in sorted({_canonical_file(item.file) for item in opportunities}):
            try:
                content = project.read_file(file_path)
            except (OSError, UnicodeDecodeError, ValueError):
                results[file_path] = []
                continue
            resolved = Path(file_path).resolve()
            args = compile_args.get(str(resolved))
            functions = self.parser.parse_file(
                resolved,
                content,
                compile_args=args,
                project_root=project_root,
            )
            results[file_path] = [
                function
                for function in functions
                if function.metadata.get("ast_backend") == "clang"
            ]
        return results


def _matches_clang_function(
    opportunity: RefactoringOpportunity,
    functions: list[FunctionInfo],
) -> bool:
    for function in functions:
        if function.name == opportunity.function and (
            function.start_line <= opportunity.location.line <= function.end_line
        ):
            return True
    for function in functions:
        if function.start_line <= opportunity.location.line <= function.end_line:
            return True
    return False


def _mark_confirmed(opportunity: RefactoringOpportunity) -> None:
    evidence = dict(opportunity.evidence)
    score = confidence(float(evidence.get("confidence", 0.5)) * 1.05)
    evidence["confidence"] = round(score, 3)
    evidence["evidence_confidence"] = evidence_confidence_label(score)
    verifier = dict(evidence.get("verifier") or {})
    verifier["clang_confirm_mode"] = "confirmed"
    verifier["clang_ast_confirmed"] = True
    verifier["verdict"] = verifier_verdict(score)
    evidence["verifier"] = verifier
    opportunity.evidence = evidence
    opportunity.severity = gated_severity(
        impact=str(evidence.get("security_impact", opportunity.severity)),
        evidence_confidence=str(evidence.get("evidence_confidence", "low")),
    )


def _mark_unconfirmed(opportunity: RefactoringOpportunity) -> None:
    evidence = dict(opportunity.evidence)
    score = confidence(float(evidence.get("confidence", 0.5)) * 0.75)
    evidence["confidence"] = round(score, 3)
    evidence["evidence_confidence"] = evidence_confidence_label(score)
    verifier = dict(evidence.get("verifier") or {})
    verifier["clang_confirm_mode"] = "unconfirmed"
    verifier["clang_ast_confirmed"] = False
    verifier["verdict"] = verifier_verdict(score)
    evidence["verifier"] = verifier
    opportunity.evidence = evidence
    opportunity.severity = gated_severity(
        impact=str(evidence.get("security_impact", opportunity.severity)),
        evidence_confidence=str(evidence.get("evidence_confidence", "low")),
    )


def _mark_unavailable(opportunity: RefactoringOpportunity, reason: str) -> None:
    evidence = dict(opportunity.evidence)
    verifier = dict(evidence.get("verifier") or {})
    verifier["clang_confirm_mode"] = "unavailable"
    verifier["clang_confirm_reason"] = reason
    evidence["verifier"] = verifier
    opportunity.evidence = evidence


def _mark_skipped(opportunity: RefactoringOpportunity, reason: str) -> None:
    evidence = dict(opportunity.evidence)
    verifier = dict(evidence.get("verifier") or {})
    verifier["clang_confirm_mode"] = "skipped"
    verifier["clang_confirm_reason"] = reason
    evidence["verifier"] = verifier
    opportunity.evidence = evidence


def _should_confirm(opportunity: RefactoringOpportunity) -> bool:
    evidence = opportunity.evidence
    impact = str(evidence.get("security_impact") or opportunity.severity).lower()
    evidence_confidence = str(evidence.get("evidence_confidence") or "").lower()
    confidence_score = float(evidence.get("confidence") or 0.0)
    if opportunity.severity in {"critical", "high"}:
        return True
    if impact in {"critical", "high"} and evidence_confidence == "high":
        return True
    return confidence_score >= 0.95 and impact in {"critical", "high", "medium"}


def _canonical_file(path: str) -> str:
    return str(Path(path).expanduser().resolve())
