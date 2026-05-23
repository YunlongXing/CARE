"""Post-detection filters that improve benchmark precision."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from care.core.models import RefactoringOpportunity
from care.detection.common import (
    confidence,
    evidence_confidence_label,
    gated_severity,
    verifier_verdict,
)

LOW_RELEVANCE_SEGMENTS = {
    "test",
    "tests",
    "testing",
    "example",
    "examples",
    "sample",
    "samples",
    "demo",
    "demos",
    "benchmark",
    "benchmarks",
    "bench",
    "fuzz",
    "fuzzing",
    "fuzzer",
}
VENDOR_SEGMENTS = {
    "third_party",
    "3rdparty",
    "external",
    "vendor",
    "vendors",
    "deps",
    "dependencies",
}
GENERATED_MARKERS = {
    "generated",
    "autogen",
    "autom4te",
    "configure",
    "config.h",
}


def apply_post_filters(
    opportunities: Iterable[RefactoringOpportunity],
) -> list[RefactoringOpportunity]:
    """Annotate and conservatively downgrade low-deployment-relevance findings."""

    return [_apply_path_relevance_filter(opportunity) for opportunity in opportunities]


def _apply_path_relevance_filter(opportunity: RefactoringOpportunity) -> RefactoringOpportunity:
    contexts = deployment_contexts(opportunity.file)
    if not contexts:
        return opportunity

    evidence = dict(opportunity.evidence)
    existing = list(evidence.get("post_filter_context") or [])
    for context in contexts:
        if context not in existing:
            existing.append(context)
    evidence["post_filter_context"] = existing
    evidence["deployment_relevance"] = "low" if "generated_code" in contexts else "reduced"

    original_confidence = float(evidence.get("confidence", 0.5))
    adjusted_confidence = confidence(original_confidence * _confidence_multiplier(contexts))
    evidence["confidence"] = round(adjusted_confidence, 3)
    evidence["evidence_confidence"] = evidence_confidence_label(adjusted_confidence)
    evidence["security_impact"] = _cap_impact(str(evidence.get("security_impact", opportunity.severity)), contexts)
    evidence["severity_before_post_filter"] = opportunity.severity

    verifier = dict(evidence.get("verifier") or {})
    verifier["verdict"] = verifier_verdict(adjusted_confidence)
    verifier["post_filter_applied"] = True
    verifier["post_filter_context"] = existing
    evidence["verifier"] = verifier

    opportunity.evidence = evidence
    opportunity.severity = gated_severity(
        impact=str(evidence["security_impact"]),
        evidence_confidence=str(evidence["evidence_confidence"]),
    )
    return opportunity


def deployment_contexts(path: str) -> list[str]:
    relative_parts = _project_relative_parts(path)
    parts = set(relative_parts)
    lowered = "/".join(relative_parts)
    contexts: list[str] = []
    filename_stem = Path(relative_parts[-1]).stem if relative_parts else ""
    if parts & LOW_RELEVANCE_SEGMENTS or filename_stem in LOW_RELEVANCE_SEGMENTS:
        contexts.append("test_or_example_path")
    if parts & VENDOR_SEGMENTS:
        contexts.append("vendored_or_external_path")
    if any(marker in lowered for marker in GENERATED_MARKERS):
        contexts.append("generated_code")
    return contexts


def _project_relative_parts(path: str) -> tuple[str, ...]:
    """Return path parts that describe the target project, not the benchmark harness."""

    normalized = path.replace("\\", "/").lower()
    parts = tuple(part for part in Path(normalized).parts if part not in {"/", ""})
    for marker in ("sources", "srcs", "projects"):
        if marker not in parts:
            continue
        index = len(parts) - 1 - tuple(reversed(parts)).index(marker)
        if index + 2 < len(parts):
            return parts[index + 2 :]
    return parts


def _confidence_multiplier(contexts: list[str]) -> float:
    if "generated_code" in contexts:
        return 0.65
    if "vendored_or_external_path" in contexts:
        return 0.75
    return 0.8


def _cap_impact(impact: str, contexts: list[str]) -> str:
    if "generated_code" in contexts:
        return _min_impact(impact, "low")
    return _min_impact(impact, "medium")


def _min_impact(left: str, right: str) -> str:
    order = ["info", "low", "medium", "high", "critical"]
    left = left if left in order else "medium"
    right = right if right in order else "medium"
    return left if order.index(left) <= order.index(right) else right
