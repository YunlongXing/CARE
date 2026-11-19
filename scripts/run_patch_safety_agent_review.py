"""Run two separately configured agents to curate C/C++ maintenance changes.

The agents select high-confidence candidates for a patch-safety benchmark. They
do not establish ground truth: benchmark safety labels are supplied later by
developer patches and deterministic unsafe mutations.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from care.llm.client import LLMClient, OpenAICompatibleClient


DEFAULT_A_MODEL = "gpt-6-luna"
DEFAULT_B_MODEL = "gpt-6.1-sol"
PRICE_PER_MILLION = {
    "gpt-6-luna": {"input": 0.10, "output": 0.50, "cached": 0.01, "cache_write": 0.125},
    "gpt-6.1-sol": {"input": 2.00, "output": 10.00, "cached": 0.10, "cache_write": 2.50},
}
ALLOWED_VERDICTS = {"refactoring", "security_repair", "not_actionable", "unclear"}
ALLOWED_YES_NO = {"yes", "no", "unclear"}


@dataclasses.dataclass(frozen=True)
class AgentProfile:
    name: str
    model: str
    reasoning_effort: str
    role: str


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--execute", action="store_true", help="Make paid model calls.")
    parser.add_argument("--budget-usd", type=float, default=2.0)
    parser.add_argument("--max-input-chars", type=int, default=48000)
    parser.add_argument("--max-output-tokens", type=int, default=1000)
    parser.add_argument("--agent-a-model", default=DEFAULT_A_MODEL)
    parser.add_argument("--agent-b-model", default=DEFAULT_B_MODEL)
    parser.add_argument("--agent-a-reasoning", default="low")
    parser.add_argument("--agent-b-reasoning", default="medium")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.budget_usd <= 0 or args.max_input_chars < 1000 or args.max_output_tokens < 100:
        parser.error("budget, input size, and output size must be positive")

    root = Path.cwd().resolve()
    manifest_path = resolve_path(root, args.review_manifest)
    output_dir = resolve_path(root, args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = read_json(manifest_path)
    items = list(manifest.get("items") or [])
    if args.limit is not None:
        items = balanced_limit(items, args.limit)

    profiles = (
        AgentProfile("context_classifier", args.agent_a_model, args.agent_a_reasoning, "context"),
        AgentProfile("adversarial_auditor", args.agent_b_model, args.agent_b_reasoning, "adversarial"),
    )
    write_json(
        output_dir / "run_config.json",
        {
            "schema_version": 1,
            "source_manifest": str(manifest_path),
            "source_digest": manifest.get("sample_digest"),
            "item_count": len(items),
            "profiles": [dataclasses.asdict(profile) for profile in profiles],
            "max_input_chars": args.max_input_chars,
            "max_output_tokens": args.max_output_tokens,
            "budget_usd": args.budget_usd,
            "execution_requested": args.execute,
        },
    )

    results = run_reviews(
        items=items,
        review_dir=manifest_path.parent,
        output_dir=output_dir,
        profiles=profiles,
        max_input_chars=args.max_input_chars,
        max_output_tokens=args.max_output_tokens,
        budget_usd=args.budget_usd,
        execute=args.execute,
        resume=args.resume,
    )
    consensus_rows, summary = build_consensus(items, results)
    write_csv(output_dir / "consensus.csv", consensus_rows, consensus_fieldnames())
    write_json(output_dir / "summary.json", summary)
    (output_dir / "summary.md").write_text(render_summary(summary), encoding="utf-8")
    print(
        f"agent curation completed={summary['completed_pairs']} eligible={summary['eligible_for_benchmark']} "
        f"estimated_cost=${summary['estimated_cost_usd']:.4f}",
    )
    return 0


def run_reviews(
    items: list[dict[str, Any]],
    review_dir: Path,
    output_dir: Path,
    profiles: tuple[AgentProfile, AgentProfile],
    max_input_chars: int,
    max_output_tokens: int,
    budget_usd: float,
    execute: bool,
    resume: bool,
) -> dict[str, dict[str, dict[str, Any]]]:
    paths = {profile.name: output_dir / f"{profile.name}.jsonl" for profile in profiles}
    results = {profile.name: read_jsonl_by_id(paths[profile.name]) if resume else {} for profile in profiles}
    estimated_spend = sum(
        float(record.get("estimated_cost_usd") or 0.0)
        for records in results.values()
        for record in records.values()
    )
    clients = make_clients(profiles, max_output_tokens) if execute else {}
    for item in items:
        material = read_material(review_dir, item, max_input_chars)
        for profile in profiles:
            sample_id = str(item["sample_id"])
            if sample_id in results[profile.name]:
                continue
            prompt = build_prompt(profile, item, material)
            estimate = estimate_cost(profile.model, prompt, max_output_tokens)
            if estimated_spend + estimate > budget_usd:
                results[profile.name][sample_id] = budget_limited_result(item, profile, estimate)
                append_jsonl(paths[profile.name], results[profile.name][sample_id])
                continue
            if not execute:
                result = dry_run_result(item, profile, estimate)
            else:
                result = execute_review(clients[profile.name], item, profile, prompt, estimate)
            results[profile.name][sample_id] = result
            append_jsonl(paths[profile.name], result)
            estimated_spend += estimate
    return results


def make_clients(
    profiles: tuple[AgentProfile, AgentProfile], max_output_tokens: int
) -> dict[str, LLMClient]:
    base = LLMClient.from_env(require_real=True)
    if not isinstance(base, OpenAICompatibleClient):
        raise ValueError("agent curation currently requires CARE_LLM_PROVIDER=openai")
    return {
        profile.name: dataclasses.replace(
            base,
            model=profile.model,
            api_style="responses",
            base_url="https://api.openai.com/v1/responses",
            reasoning_effort=profile.reasoning_effort,
            max_output_tokens=max_output_tokens,
        )
        for profile in profiles
    }


def execute_review(
    client: LLMClient,
    item: dict[str, Any],
    profile: AgentProfile,
    prompt: str,
    estimate: float,
) -> dict[str, Any]:
    try:
        raw = client.complete(prompt, temperature=0.0)
        decision = normalize_decision(parse_json_response(raw))
        status = "ok"
        error = ""
    except Exception as exc:
        decision = empty_decision()
        status = "error"
        error = str(exc)
    usage = getattr(client, "last_usage", {})
    return review_record(item, profile, decision, estimate, status, error, usage)


def dry_run_result(item: dict[str, Any], profile: AgentProfile, estimate: float) -> dict[str, Any]:
    return review_record(item, profile, empty_decision(), estimate, "dry_run", "use --execute to call models", {})


def budget_limited_result(item: dict[str, Any], profile: AgentProfile, estimate: float) -> dict[str, Any]:
    return review_record(item, profile, empty_decision(), estimate, "budget_limited", "estimated request exceeds budget", {})


def review_record(
    item: dict[str, Any],
    profile: AgentProfile,
    decision: dict[str, Any],
    estimated_cost: float,
    status: str,
    error: str,
    usage: dict[str, Any],
) -> dict[str, Any]:
    return {
        "sample_id": item["sample_id"],
        "case_id": item["id"],
        "project_id": item["project_id"],
        "agent": dataclasses.asdict(profile),
        "status": status,
        "error": error[:1000],
        "estimated_cost_usd": estimated_cost,
        "usage": usage,
        "decision": decision,
    }


def build_prompt(profile: AgentProfile, item: dict[str, Any], material: str) -> str:
    common = (
        "You curate candidate C/C++ maintenance commits for a research benchmark. "
        "Do not assume that a commit message proves behavior preservation. Use only the supplied patch evidence. "
        "Return JSON only.\n\n"
        "Schema:\n"
        "{\n"
        '  "verdict": "refactoring|security_repair|not_actionable|unclear",\n'
        '  "semantic_preserving": "yes|no|unclear",\n'
        '  "security_relevant": "yes|no|unclear",\n'
        '  "confidence": "integer from 1 through 5",\n'
        '  "evidence": ["file:line or concrete diff fact"],\n'
        '  "reason": "brief reason"\n'
        "}\n\n"
    )
    if profile.role == "context":
        instructions = (
            "Classify whether this looks like a local behavior-preserving refactoring. "
            "Reject repairs, tests-only changes, formatting-only changes, and changes whose intent cannot be inferred. "
            "State the invariants that the patch appears to preserve.\n\n"
        )
    else:
        instructions = (
            "Act as an adversarial reviewer. Look for altered return values, cleanup order, resource ownership, "
            "validation checks, logging, I/O, and API-visible side effects. If any risk cannot be resolved from the "
            "patch, return unclear or semantic_preserving=no. Do not rely on the commit subject.\n\n"
        )
    header = {
        "sample_id": item["sample_id"],
        "project": item["project_id"],
        "candidate_category": item["candidate_category"],
        "commit_subject": item["subject"],
        "changed_files": item.get("changed_files") or [],
    }
    return common + instructions + json.dumps(header, sort_keys=True) + "\n\n" + material


def read_material(review_dir: Path, item: dict[str, Any], max_chars: int) -> str:
    path = review_dir / str(item["material_file"])
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= max_chars:
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return text[:max_chars] + f"\n\n[truncated; full_material_sha256={digest}]\n"


def parse_json_response(raw: str) -> dict[str, Any]:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("agent response must be a JSON object")
    return value


def normalize_decision(value: dict[str, Any]) -> dict[str, Any]:
    verdict = str(value.get("verdict") or "unclear").strip().lower()
    semantic = str(value.get("semantic_preserving") or "unclear").strip().lower()
    security = str(value.get("security_relevant") or "unclear").strip().lower()
    confidence = value.get("confidence", 0)
    try:
        confidence = max(1, min(5, int(confidence)))
    except (TypeError, ValueError):
        confidence = 1
    evidence = value.get("evidence") if isinstance(value.get("evidence"), list) else []
    return {
        "verdict": verdict if verdict in ALLOWED_VERDICTS else "unclear",
        "semantic_preserving": semantic if semantic in ALLOWED_YES_NO else "unclear",
        "security_relevant": security if security in ALLOWED_YES_NO else "unclear",
        "confidence": confidence,
        "evidence": [str(item)[:500] for item in evidence[:6]],
        "reason": str(value.get("reason") or "")[:1200],
    }


def empty_decision() -> dict[str, Any]:
    return {
        "verdict": "unclear",
        "semantic_preserving": "unclear",
        "security_relevant": "unclear",
        "confidence": 1,
        "evidence": [],
        "reason": "",
    }


def estimate_cost(model: str, prompt: str, max_output_tokens: int) -> float:
    prices = PRICE_PER_MILLION.get(model, {"input": 2.0, "output": 10.0})
    input_tokens = max(1, len(prompt) // 4)
    return input_tokens * prices.get("cache_write", prices["input"]) / 1_000_000 + max_output_tokens * prices["output"] / 1_000_000


def build_consensus(
    items: list[dict[str, Any]], results: dict[str, dict[str, dict[str, Any]]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pair_statuses: list[str] = []
    eligible = 0
    estimated_cost = 0.0
    observed_cost = 0.0
    for item in items:
        sample_id = str(item["sample_id"])
        first = results["context_classifier"].get(sample_id)
        second = results["adversarial_auditor"].get(sample_id)
        if first:
            estimated_cost += float(first.get("estimated_cost_usd") or 0.0)
        if second:
            estimated_cost += float(second.get("estimated_cost_usd") or 0.0)
        for record, default_model in ((first, DEFAULT_A_MODEL), (second, DEFAULT_B_MODEL)):
            if record:
                model = str((record.get("agent") or {}).get("model") or default_model)
                observed_cost += usage_cost(model, record.get("usage") or {})
        status = pair_status(first, second)
        pair_statuses.append(status)
        decision_a = (first or {}).get("decision") or empty_decision()
        decision_b = (second or {}).get("decision") or empty_decision()
        is_eligible = consensus_eligible(first, second)
        eligible += int(is_eligible)
        rows.append(
            {
                "sample_id": sample_id,
                "case_id": item["id"],
                "project_id": item["project_id"],
                "agent_a_status": (first or {}).get("status", "missing"),
                "agent_a_verdict": decision_a["verdict"],
                "agent_a_semantic_preserving": decision_a["semantic_preserving"],
                "agent_a_confidence": decision_a["confidence"],
                "agent_b_status": (second or {}).get("status", "missing"),
                "agent_b_verdict": decision_b["verdict"],
                "agent_b_semantic_preserving": decision_b["semantic_preserving"],
                "agent_b_confidence": decision_b["confidence"],
                "pair_status": status,
                "eligible_for_benchmark": "yes" if is_eligible else "no",
            }
        )
    completed = sum(status == "completed" for status in pair_statuses)
    agreement = sum(status == "completed" and row["agent_a_verdict"] == row["agent_b_verdict"] for row, status in zip(rows, pair_statuses))
    return rows, {
        "sample_count": len(items),
        "completed_pairs": completed,
        "agent_verdict_agreement": agreement / completed if completed else None,
        "eligible_for_benchmark": eligible,
        "estimated_cost_usd": estimated_cost,
        "observed_token_cost_usd": observed_cost,
        "pair_status_counts": {status: pair_statuses.count(status) for status in sorted(set(pair_statuses))},
    }


def pair_status(first: dict[str, Any] | None, second: dict[str, Any] | None) -> str:
    if first is None or second is None:
        return "missing"
    if first.get("status") == "ok" and second.get("status") == "ok":
        return "completed"
    if first.get("status") == "budget_limited" or second.get("status") == "budget_limited":
        return "budget_limited"
    if first.get("status") == "dry_run" or second.get("status") == "dry_run":
        return "dry_run"
    return "error"


def consensus_eligible(first: dict[str, Any] | None, second: dict[str, Any] | None) -> bool:
    if pair_status(first, second) != "completed":
        return False
    first_decision = first["decision"]
    second_decision = second["decision"]
    return (
        first_decision["verdict"] == "refactoring"
        and second_decision["verdict"] == "refactoring"
        and first_decision["semantic_preserving"] == "yes"
        and second_decision["semantic_preserving"] == "yes"
        and first_decision["confidence"] >= 4
        and second_decision["confidence"] >= 4
    )


def consensus_fieldnames() -> list[str]:
    return [
        "sample_id", "case_id", "project_id", "agent_a_status", "agent_a_verdict",
        "agent_a_semantic_preserving", "agent_a_confidence", "agent_b_status",
        "agent_b_verdict", "agent_b_semantic_preserving", "agent_b_confidence",
        "pair_status", "eligible_for_benchmark",
    ]


def render_summary(summary: dict[str, Any]) -> str:
    agreement = summary["agent_verdict_agreement"]
    agreement_text = "pending" if agreement is None else f"{agreement:.1%}"
    return (
        "# Agent Curation Summary\n\n"
        f"- Candidate commits: {summary['sample_count']}\n"
        f"- Completed agent pairs: {summary['completed_pairs']}\n"
        f"- Verdict agreement: {agreement_text}\n"
        f"- Eligible refactoring candidates: {summary['eligible_for_benchmark']}\n"
        f"- Estimated request cost: ${summary['estimated_cost_usd']:.4f}\n\n"
        f"- Observed token cost: ${summary['observed_token_cost_usd']:.4f}\n\n"
        "Agent consensus is a curation signal. It is not human review or benchmark ground truth.\n"
    )


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl_by_id(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    records: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            records[str(record["sample_id"])] = record
    return records


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def resolve_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def usage_cost(model: str, usage: dict[str, Any]) -> float:
    prices = PRICE_PER_MILLION.get(model)
    if not prices:
        return 0.0
    details = usage.get("input_tokens_details") or {}
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    cached_tokens = int(details.get("cached_tokens") or 0)
    cache_write_tokens = int(details.get("cache_write_tokens") or 0)
    standard_tokens = max(0, input_tokens - cached_tokens - cache_write_tokens)
    return (
        standard_tokens * prices["input"]
        + cached_tokens * prices["cached"]
        + cache_write_tokens * prices["cache_write"]
        + output_tokens * prices["output"]
    ) / 1_000_000


def balanced_limit(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Take a deterministic, category-balanced prefix for a pilot run."""
    if limit >= len(items):
        return items
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        groups.setdefault(str(item.get("candidate_category") or "other"), []).append(item)
    selected: list[dict[str, Any]] = []
    group_names = sorted(groups)
    positions = {name: 0 for name in group_names}
    while len(selected) < limit:
        progressed = False
        for name in group_names:
            if len(selected) >= limit:
                break
            group = groups[name]
            position = positions[name]
            if position >= len(group):
                continue
            selected.append(group[position])
            positions[name] = position + 1
            progressed = True
        if not progressed:
            break
    return selected


if __name__ == "__main__":
    raise SystemExit(main())
