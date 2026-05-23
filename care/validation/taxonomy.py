"""Failure taxonomy for CARE validation outcomes."""

from __future__ import annotations

from collections import Counter
from typing import Any

from care.core.models import StageResult, ValidationResult


def classify_stages(stages: list[StageResult]) -> dict[str, Any]:
    """Classify validation failures into paper/report friendly buckets."""

    categories: list[str] = []
    failed_stages: list[str] = []
    for stage in stages:
        if stage.passed:
            continue
        failed_stages.append(stage.name)
        categories.extend(_classify_stage(stage))

    if failed_stages and not categories:
        categories.append("unknown_validation_failure")

    counts = Counter(categories)
    return {
        "categories": sorted(counts),
        "category_counts": dict(sorted(counts.items())),
        "failed_stages": failed_stages,
        "primary_category": sorted(counts)[0] if counts else None,
    }


def classify_validation_result(validation: ValidationResult | dict[str, Any] | None) -> dict[str, Any]:
    """Classify an existing ``ValidationResult`` or serialized validation payload."""

    if validation is None:
        return {
            "categories": ["missing_validation"],
            "category_counts": {"missing_validation": 1},
            "failed_stages": [],
            "primary_category": "missing_validation",
        }
    if isinstance(validation, ValidationResult):
        stage_results = validation.stage_results
        logs = validation.logs
        counterexamples = validation.counterexamples
        passed = validation.passed
    else:
        stage_results = validation.get("stage_results") or {}
        logs = validation.get("logs") or []
        counterexamples = validation.get("counterexamples") or []
        passed = bool(validation.get("passed"))

    existing = stage_results.get("failure_taxonomy") if isinstance(stage_results, dict) else None
    if isinstance(existing, dict) and existing.get("categories") is not None:
        return existing

    if passed:
        return {
            "categories": [],
            "category_counts": {},
            "failed_stages": [],
            "primary_category": None,
        }

    categories: list[str] = []
    failed_stages: list[str] = []
    for name, stage in stage_results.items():
        if name == "failure_taxonomy" or not isinstance(stage, dict):
            continue
        if stage.get("passed"):
            continue
        if isinstance(stage.get("stage_results"), dict):
            nested = classify_validation_result(stage)
            categories.extend(nested.get("categories") or [])
            failed_stages.extend(
                f"{name}.{stage_name}"
                for stage_name in (nested.get("failed_stages") or [])
            )
            continue
        failed_stages.append(name)
        categories.extend(_classify_stage_payload(name, stage))
    if not categories:
        haystack = _text(logs, counterexamples)
        categories.extend(_classify_haystack(haystack))
    if failed_stages and not categories:
        categories.append("unknown_validation_failure")
    counts = Counter(categories)
    return {
        "categories": sorted(counts),
        "category_counts": dict(sorted(counts.items())),
        "failed_stages": failed_stages,
        "primary_category": sorted(counts)[0] if counts else None,
    }


def _classify_stage(stage: StageResult) -> list[str]:
    return _classify_stage_payload(
        stage.name,
        {
            "logs": stage.logs,
            "warnings": stage.warnings,
            "counterexamples": stage.counterexamples,
        },
    )


def _classify_stage_payload(name: str, stage: dict[str, Any]) -> list[str]:
    haystack = _text(
        stage.get("logs") or [],
        stage.get("warnings") or [],
        stage.get("counterexamples") or [],
    )
    if name in {"restore_clean_project", "restore_after_validation"}:
        return ["restore_failure"]
    if name == "apply_patch":
        return _classify_apply_patch(haystack)
    if name == "compile":
        return ["compile_failure"]
    if name == "tests":
        return ["test_failure"]
    if name == "static_analysis":
        return ["static_analysis_regression"]
    if name == "resource_consistency":
        return ["resource_regression"]
    if name == "semantic_equivalence":
        return ["semantic_regression"]
    if name == "security_regression":
        return ["security_regression"]
    return _classify_haystack(haystack)


def _classify_apply_patch(haystack: str) -> list[str]:
    if "empty patch" in haystack:
        return ["empty_patch"]
    if "produced no project changes" in haystack or "no project changes" in haystack:
        return ["no_effect_patch"]
    if "path repair" in haystack or "no such file" in haystack or "does not exist" in haystack:
        return ["path_mismatch"]
    if (
        "patch does not apply" in haystack
        or "patch failed" in haystack
        or "hunk" in haystack
        or "garbage" in haystack
    ):
        return ["patch_does_not_apply"]
    if "git apply" in haystack or "apply" in haystack:
        return ["patch_does_not_apply"]
    return ["unknown_validation_failure"]


def _classify_haystack(haystack: str) -> list[str]:
    if "insufficient_quota" in haystack or "rate_limit" in haystack:
        return ["llm_rate_limit"]
    if "invalid_api_key" in haystack or "llm request failed" in haystack:
        return ["llm_api_error"]
    if "compile" in haystack and "failed" in haystack:
        return ["compile_failure"]
    if "test" in haystack and "failed" in haystack:
        return ["test_failure"]
    return []


def _text(*parts: object) -> str:
    flattened: list[str] = []
    for part in parts:
        if isinstance(part, list):
            flattened.extend(str(item) for item in part)
        elif part is not None:
            flattened.append(str(part))
    return "\n".join(flattened).lower()
