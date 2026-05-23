"""Shared helpers for refactoring opportunity detectors."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Optional

from care.analysis.context_graph import ContextGraph
from care.core.models import CodeLocation, FunctionInfo, RefactoringOpportunity

ERROR_LABEL_HINTS = {"cleanup", "out", "error", "err", "fail", "failed"}
CONTROL_CALLS = {"if", "for", "while", "switch", "return", "sizeof"}
SEVERITY_ORDER = ["info", "low", "medium", "high", "critical"]


def make_opportunity(
    function: FunctionInfo,
    context_graph: ContextGraph,
    kind: str,
    line: int,
    column: Optional[int],
    severity: str,
    description: str,
    evidence: dict[str, Any],
) -> RefactoringOpportunity:
    enriched_evidence = dict(evidence)
    enriched_evidence.setdefault("confidence", 0.5)
    enriched_evidence.setdefault("security_impact", severity)
    enriched_evidence.setdefault(
        "evidence_confidence",
        evidence_confidence_label(float(enriched_evidence["confidence"])),
    )
    enriched_evidence.setdefault(
        "verifier",
        {
            "verdict": verifier_verdict(float(enriched_evidence["confidence"])),
            "analysis_backend": analysis_backend(function),
            "clang_ast_confirmed": analysis_backend(function) == "clang",
        },
    )
    severity = gated_severity(
        impact=str(enriched_evidence.get("security_impact", severity)),
        evidence_confidence=str(enriched_evidence["evidence_confidence"]),
    )
    digest_source = json.dumps(enriched_evidence, sort_keys=True, default=str)
    digest = hashlib.sha1(digest_source.encode("utf-8")).hexdigest()[:10]
    return RefactoringOpportunity(
        id=f"{kind}:{function.name}:{line}:{digest}",
        kind=kind,
        file=function.file,
        function=function.name,
        location=CodeLocation(file=function.file, line=line, column=column),
        severity=severity,
        description=description,
        evidence=enriched_evidence,
        context_summary=summarize_function_context(function, context_graph),
    )


def summarize_function_context(function: FunctionInfo, context_graph: ContextGraph) -> str:
    call_edges = [
        edge.dst
        for edge in context_graph.edges
        if edge.edge_type == "call" and edge.src == function.name
    ]
    goto_edges = [
        edge
        for edge in context_graph.edges
        if edge.edge_type == "goto" and edge.src.startswith(f"{function.name}:")
    ]
    dataflow = context_graph.dataflow.get(function.name, {})
    resources = dataflow.get("resource_variables", [])
    error_paths = context_graph.error_handling_paths.get(function.name, [])
    side_effects = context_graph.side_effect_summaries.get(function.name, "No side effects detected.")
    return (
        f"{function.name} spans {function.file}:{function.start_line}-{function.end_line}; "
        f"calls={format_list(call_edges)}, gotos={len(goto_edges)}, "
        f"resources={format_list(resources)}, error_paths={format_list(error_paths)}. "
        f"Side effects: {side_effects}"
    )


def iter_code_lines(function: FunctionInfo) -> list[tuple[int, str, str]]:
    lines: list[tuple[int, str, str]] = []
    for offset, raw_line in enumerate(function.body.splitlines()):
        line_number = function.start_line + offset
        normalized = normalize_statement(strip_comments_and_strings(raw_line))
        if normalized and normalized not in {"{", "}"}:
            lines.append((line_number, raw_line, normalized))
    return lines


def normalize_statement(statement: str) -> str:
    return " ".join(statement.strip().split())


def strip_comments_and_strings(line: str) -> str:
    code = line.split("//", 1)[0]
    code = re.sub(r"/\*.*?\*/", " ", code)
    code = re.sub(r'"(?:\\.|[^"\\])*"', '""', code)
    code = re.sub(r"'(?:\\.|[^'\\])*'", "''", code)
    return code


def column(raw_line: str, needle: str) -> Optional[int]:
    index = raw_line.find(needle)
    return index + 1 if index >= 0 else None


def goto_target(statement: str) -> Optional[str]:
    match = re.search(r"\bgoto\s+([A-Za-z_][A-Za-z_0-9]*)\s*;", statement)
    return match.group(1) if match else None


def label_name(statement: str) -> Optional[str]:
    match = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)\s*:\s*(?!:)", statement)
    if not match:
        return None
    label = match.group(1)
    if label in {"case", "default"}:
        return None
    return label


def label_locations(function: FunctionInfo) -> dict[str, CodeLocation]:
    labels: dict[str, CodeLocation] = {}
    for line_number, raw_line, statement in iter_code_lines(function):
        label = label_name(statement)
        if label:
            labels[label] = CodeLocation(
                file=function.file,
                line=line_number,
                column=column(raw_line, label),
            )
    return labels


def goto_locations(function: FunctionInfo) -> list[dict[str, Any]]:
    locations: list[dict[str, Any]] = []
    for line_number, raw_line, statement in iter_code_lines(function):
        target = goto_target(statement)
        if target:
            locations.append(
                {
                    "target": target,
                    "location": CodeLocation(
                        file=function.file,
                        line=line_number,
                        column=column(raw_line, "goto"),
                    ).to_dict(),
                    "statement": statement,
                    "previous_statement": previous_statement(function, line_number),
                }
            )
    return locations


def previous_statement(function: FunctionInfo, before_line: int) -> str:
    previous = ""
    for line_number, _, statement in iter_code_lines(function):
        if line_number >= before_line:
            break
        if statement not in {"{", "}"}:
            previous = statement
    return previous


def label_blocks(function: FunctionInfo) -> dict[str, list[tuple[int, str]]]:
    blocks: dict[str, list[tuple[int, str]]] = {}
    current_label: Optional[str] = None
    for line_number, _, statement in iter_code_lines(function):
        label = label_name(statement)
        if label:
            current_label = label
            blocks.setdefault(label, [])
            remainder = statement.split(":", 1)[1].strip()
            if remainder:
                blocks[label].append((line_number, remainder))
            continue
        if current_label:
            if statement == "}":
                continue
            next_label = label_name(statement)
            if next_label:
                current_label = next_label
                blocks.setdefault(next_label, [])
            else:
                blocks[current_label].append((line_number, statement))
    return blocks


def is_error_label(label: str) -> bool:
    lowered = label.lower()
    return any(hint in lowered for hint in ERROR_LABEL_HINTS)


def cleanup_call(statement: str) -> Optional[tuple[str, str]]:
    for match in re.finditer(
        r"\b(free|fclose|close|lock_release|unlock|mutex_unlock|pthread_mutex_unlock)\s*\(([^)]*)\)",
        statement,
    ):
        call = match.group(1)
        variable = first_identifier(match.group(2))
        return call, variable
    return None


def function_calls(statement: str) -> list[str]:
    calls: list[str] = []
    for match in re.finditer(r"\b([A-Za-z_][A-Za-z_0-9:]*)\s*\(", statement):
        call = match.group(1).split("::")[-1]
        if call not in CONTROL_CALLS:
            calls.append(call)
    return calls


def first_identifier(text: str) -> str:
    match = re.search(r"[A-Za-z_][A-Za-z_0-9]*", text)
    return match.group(0) if match else ""


def if_condition(statement: str) -> Optional[str]:
    match = re.search(r"\bif\s*\((.*)\)", statement)
    return match.group(1).strip() if match else None


def assigned_variables(statement: str) -> list[str]:
    variables: list[str] = []
    for match in re.finditer(
        r"\b([A-Za-z_][A-Za-z_0-9]*)\s*(?:=|\+=|-=|\*=|/=|%=|\+\+|--)",
        statement,
    ):
        variables.append(match.group(1))
    return variables


def return_statement(statement: str) -> bool:
    return bool(re.match(r"^return\b", statement))


def format_list(values: list[Any], limit: int = 5) -> str:
    if not values:
        return "none"
    rendered = [str(value) for value in values[:limit]]
    if len(values) > limit:
        rendered.append(f"+{len(values) - limit} more")
    return ", ".join(rendered)


def confidence(value: float) -> float:
    return max(0.0, min(1.0, value))


def evidence_confidence_label(score: float) -> str:
    score = confidence(score)
    if score >= 0.80:
        return "high"
    if score >= 0.55:
        return "medium"
    return "low"


def verifier_verdict(score: float) -> str:
    score = confidence(score)
    if score >= 0.85:
        return "true_positive"
    if score >= 0.65:
        return "likely"
    if score >= 0.45:
        return "unclear"
    return "false_positive"


def gated_severity(impact: str, evidence_confidence: str) -> str:
    normalized_impact = impact if impact in SEVERITY_ORDER else "medium"
    if evidence_confidence == "high":
        return normalized_impact
    if evidence_confidence == "medium":
        return min_severity(normalized_impact, "high")
    return min_severity(normalized_impact, "medium")


def min_severity(left: str, right: str) -> str:
    if left not in SEVERITY_ORDER:
        left = "medium"
    if right not in SEVERITY_ORDER:
        right = "medium"
    return left if SEVERITY_ORDER.index(left) <= SEVERITY_ORDER.index(right) else right


def analysis_backend(function: FunctionInfo) -> str:
    return str(function.metadata.get("ast_backend", "unknown"))


def has_clang_cfg(function: FunctionInfo, context_graph: ContextGraph) -> bool:
    return any(
        block.function == function.name
        and any(statement.startswith("<entry>") or statement.startswith("<exit>") for statement in block.statements)
        and ":B" in block.id
        for block in context_graph.basic_blocks
    )
