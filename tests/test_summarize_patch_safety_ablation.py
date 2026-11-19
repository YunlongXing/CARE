from __future__ import annotations

from scripts.summarize_patch_safety_ablation import summarize


def _result(case_id: str, truth: str, stages: dict[str, bool]) -> dict:
    return {
        "case_id": case_id,
        "ground_truth": truth,
        "systems": {
            "care": {
                "stage_results": {
                    name: {"passed": passed} for name, passed in stages.items()
                }
            }
        },
    }


def test_ablation_counts_extra_false_accepts() -> None:
    rows = summarize(
        [
            _result("safe", "safe", {"compile": True, "resource_consistency": True}),
            _result("unsafe", "unsafe", {"compile": True, "resource_consistency": False}),
        ],
        "care",
    )
    full, without_resource = rows[:2]
    assert full["false_accepts"] == 0
    assert without_resource["false_accepts"] == 1
    assert without_resource["additional_false_accepts"] == 1
