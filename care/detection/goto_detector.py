"""Goto-based redundancy and cleanup refactoring detector."""

from __future__ import annotations

from collections import Counter, defaultdict

from care.analysis.context_graph import ContextGraph
from care.analysis.security_knowledge import SecurityKnowledgeBase
from care.core.models import FunctionInfo, RefactoringOpportunity
from care.detection.common import (
    cleanup_call,
    column,
    goto_locations,
    goto_target,
    is_error_label,
    iter_code_lines,
    label_blocks,
    label_locations,
    label_name,
    make_opportunity,
    normalize_statement,
    return_statement,
)


class GotoDetector:
    """Detect goto-centered cleanup and error-handling refactoring opportunities."""

    def detect(
        self,
        function: FunctionInfo,
        context_graph: ContextGraph,
        knowledge_base: SecurityKnowledgeBase,
    ) -> list[RefactoringOpportunity]:
        opportunities: list[RefactoringOpportunity] = []
        resource_variables = sorted(
            {
                event.variable
                for event in knowledge_base.match_resource_events(function)
                if event.variable
            }
        )
        opportunities.extend(
            self._detect_manual_cleanup_consolidation(
                function,
                context_graph,
                resource_variables,
            )
        )

        gotos = goto_locations(function)
        if not gotos:
            return opportunities

        labels = label_locations(function)
        blocks = label_blocks(function)

        opportunities.extend(
            self._detect_repeated_cleanup_before_goto(
                function,
                context_graph,
                gotos,
                blocks,
                resource_variables,
            )
        )
        opportunities.extend(
            self._detect_redundant_error_block_calls(
                function,
                context_graph,
                gotos,
                labels,
                blocks,
                resource_variables,
            )
        )
        opportunities.extend(
            self._detect_duplicated_branch_error_handling(
                function,
                context_graph,
                gotos,
                labels,
                resource_variables,
            )
        )
        opportunities.extend(
            self._detect_goto_chains(function, context_graph, gotos, labels, blocks, resource_variables)
        )
        opportunities.extend(
            self._detect_single_use_replaceable_labels(
                function,
                context_graph,
                gotos,
                labels,
                blocks,
                resource_variables,
            )
        )
        return opportunities

    def _detect_manual_cleanup_consolidation(
        self,
        function: FunctionInfo,
        context_graph: ContextGraph,
        resource_variables: list[str],
    ) -> list[RefactoringOpportunity]:
        cleanup_blocks = _cleanup_blocks_before_returns(function)
        if len(cleanup_blocks) < 2:
            return []

        cleanup_sets = [set(block["cleanup_statements"]) for block in cleanup_blocks]
        duplicated = sorted(set.intersection(*cleanup_sets)) if cleanup_sets else []
        if not duplicated:
            return []

        first = cleanup_blocks[0]
        return [
            make_opportunity(
                function=function,
                context_graph=context_graph,
                kind="goto_cleanup_consolidation",
                line=first["return_location"]["line"],
                column=first["return_location"]["column"],
                severity="high",
                description=(
                    "Multiple return paths repeat cleanup; consolidate them through a shared "
                    "goto out cleanup path."
                ),
                evidence={
                    "return_statement_locations": [
                        block["return_location"] for block in cleanup_blocks
                    ],
                    "duplicated_statements": duplicated,
                    "involved_resource_variables": resource_variables,
                    "affected_control_paths": [
                        f"return at line {block['return_location']['line']}"
                        for block in cleanup_blocks
                    ],
                    "suggested_refactoring": (
                        "introduce a local ret variable, set it on each error path, jump to "
                        "out, and release shared resources once before returning ret"
                    ),
                    "confidence": 0.88,
                },
            )
        ]

    def _detect_repeated_cleanup_before_goto(
        self,
        function: FunctionInfo,
        context_graph: ContextGraph,
        gotos: list[dict],
        blocks: dict[str, list[tuple[int, str]]],
        resource_variables: list[str],
    ) -> list[RefactoringOpportunity]:
        opportunities: list[RefactoringOpportunity] = []
        lines = iter_code_lines(function)
        for goto in gotos:
            target = goto["target"]
            if target not in blocks:
                continue
            block_cleanup = {
                normalize_statement(statement)
                for _, statement in blocks[target]
                if cleanup_call(statement)
            }
            if not block_cleanup:
                continue
            goto_line = goto["location"]["line"]
            before_cleanup = {
                normalize_statement(statement)
                for line_number, _, statement in lines
                if goto_line - 6 <= line_number < goto_line and cleanup_call(statement)
            }
            duplicated = sorted(block_cleanup & before_cleanup)
            if not duplicated:
                continue
            opportunities.append(
                make_opportunity(
                    function=function,
                    context_graph=context_graph,
                    kind="goto_redundant_cleanup",
                    line=goto_line,
                    column=goto["location"]["column"],
                    severity="medium",
                    description=f"Cleanup before goto {target} duplicates cleanup in the target block.",
                    evidence={
                        "goto_statement_locations": [goto["location"]],
                        "label_locations": _label_evidence(function, [target]),
                        "duplicated_statements": duplicated,
                        "involved_resource_variables": resource_variables,
                        "affected_control_paths": [f"goto {target}"],
                        "confidence": 0.78,
                    },
                )
            )
        return opportunities

    def _detect_redundant_error_block_calls(
        self,
        function: FunctionInfo,
        context_graph: ContextGraph,
        gotos: list[dict],
        labels: dict,
        blocks: dict[str, list[tuple[int, str]]],
        resource_variables: list[str],
    ) -> list[RefactoringOpportunity]:
        opportunities: list[RefactoringOpportunity] = []
        goto_targets = {goto["target"] for goto in gotos}
        for label, statements in blocks.items():
            if label not in goto_targets and not is_error_label(label):
                continue
            cleanup_counts: Counter[tuple[str, str]] = Counter()
            first_line: dict[tuple[str, str], int] = {}
            duplicated: list[str] = []
            for line_number, statement in statements:
                cleanup = cleanup_call(statement)
                if not cleanup:
                    continue
                cleanup_counts[cleanup] += 1
                first_line.setdefault(cleanup, line_number)
                if cleanup_counts[cleanup] == 2:
                    duplicated.append(statement)
            if not duplicated:
                continue
            location = labels.get(label)
            opportunities.append(
                make_opportunity(
                    function=function,
                    context_graph=context_graph,
                    kind="goto_redundant_error_block",
                    line=location.line if location else statements[0][0],
                    column=location.column if location else None,
                    severity="high",
                    description=f"Error-handling label {label} repeats cleanup calls.",
                    evidence={
                        "goto_statement_locations": [
                            goto["location"] for goto in gotos if goto["target"] == label
                        ],
                        "label_locations": _label_evidence(function, [label]),
                        "duplicated_statements": duplicated,
                        "involved_resource_variables": resource_variables,
                        "affected_control_paths": [f"label {label}"],
                        "confidence": 0.82,
                    },
                )
            )
        return opportunities

    def _detect_duplicated_branch_error_handling(
        self,
        function: FunctionInfo,
        context_graph: ContextGraph,
        gotos: list[dict],
        labels: dict,
        resource_variables: list[str],
    ) -> list[RefactoringOpportunity]:
        opportunities: list[RefactoringOpportunity] = []
        grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for goto in gotos:
            previous = goto.get("previous_statement") or ""
            grouped[(goto["target"], previous)].append(goto)

        for (target, previous), entries in grouped.items():
            if len(entries) < 2 or not previous:
                continue
            opportunities.append(
                make_opportunity(
                    function=function,
                    context_graph=context_graph,
                    kind="goto_duplicated_error_handling",
                    line=entries[0]["location"]["line"],
                    column=entries[0]["location"]["column"],
                    severity="medium",
                    description=f"Multiple branches perform the same error handling before goto {target}.",
                    evidence={
                        "goto_statement_locations": [entry["location"] for entry in entries],
                        "label_locations": _label_evidence(function, [target]),
                        "duplicated_statements": [previous],
                        "involved_resource_variables": resource_variables,
                        "affected_control_paths": [f"branch -> goto {target}" for _ in entries],
                        "confidence": 0.72,
                    },
                )
            )
        return opportunities

    def _detect_goto_chains(
        self,
        function: FunctionInfo,
        context_graph: ContextGraph,
        gotos: list[dict],
        labels: dict,
        blocks: dict[str, list[tuple[int, str]]],
        resource_variables: list[str],
    ) -> list[RefactoringOpportunity]:
        opportunities: list[RefactoringOpportunity] = []
        for label, statements in blocks.items():
            nonempty = [(line, statement) for line, statement in statements if statement]
            if not nonempty:
                continue
            first_line, first_statement = nonempty[0]
            chained_target = goto_target(first_statement)
            if not chained_target:
                continue
            location = labels.get(label)
            opportunities.append(
                make_opportunity(
                    function=function,
                    context_graph=context_graph,
                    kind="goto_chain",
                    line=location.line if location else first_line,
                    column=location.column if location else column(first_statement, "goto"),
                    severity="medium",
                    description=f"Label {label} only redirects to {chained_target}.",
                    evidence={
                        "goto_statement_locations": [
                            goto["location"] for goto in gotos if goto["target"] in {label, chained_target}
                        ],
                        "label_locations": _label_evidence(function, [label, chained_target]),
                        "duplicated_statements": [],
                        "involved_resource_variables": resource_variables,
                        "affected_control_paths": [f"{label} -> {chained_target}"],
                        "confidence": 0.86,
                    },
                )
            )
        return opportunities

    def _detect_single_use_replaceable_labels(
        self,
        function: FunctionInfo,
        context_graph: ContextGraph,
        gotos: list[dict],
        labels: dict,
        blocks: dict[str, list[tuple[int, str]]],
        resource_variables: list[str],
    ) -> list[RefactoringOpportunity]:
        opportunities: list[RefactoringOpportunity] = []
        target_counts = Counter(goto["target"] for goto in gotos)
        for target, count in target_counts.items():
            if count != 1 or target not in labels:
                continue
            if target not in blocks:
                continue
            block_statements = [statement for _, statement in blocks[target] if statement]
            if len(block_statements) > 6 and not is_error_label(target):
                continue
            goto = next(entry for entry in gotos if entry["target"] == target)
            opportunities.append(
                make_opportunity(
                    function=function,
                    context_graph=context_graph,
                    kind="goto_single_use_label",
                    line=goto["location"]["line"],
                    column=goto["location"]["column"],
                    severity="low",
                    description=f"Label {target} is used once and may be replaceable by structured control flow.",
                    evidence={
                        "goto_statement_locations": [goto["location"]],
                        "label_locations": _label_evidence(function, [target]),
                        "duplicated_statements": [],
                        "involved_resource_variables": resource_variables,
                        "affected_control_paths": [f"single goto {target}"],
                        "confidence": 0.62,
                    },
                )
            )
        return opportunities


def _label_evidence(function: FunctionInfo, labels: list[str]) -> list[dict]:
    locations = label_locations(function)
    return [
        locations[label].to_dict()
        for label in labels
        if label in locations
    ]


def _cleanup_blocks_before_returns(function: FunctionInfo) -> list[dict]:
    lines = iter_code_lines(function)
    blocks: list[dict] = []
    for index, (line_number, raw_line, statement) in enumerate(lines):
        if not return_statement(statement):
            continue
        cleanup_statements: list[str] = []
        for prior_line, _, prior_statement in reversed(lines[max(0, index - 8) : index]):
            if return_statement(prior_statement):
                break
            if cleanup_call(prior_statement):
                cleanup_statements.append(normalize_statement(prior_statement))
        if not cleanup_statements:
            continue
        blocks.append(
            {
                "return_location": {
                    "file": function.file,
                    "line": line_number,
                    "column": column(raw_line, "return"),
                },
                "cleanup_statements": list(reversed(cleanup_statements)),
            }
        )
    return blocks
