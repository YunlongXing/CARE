"""Detect dead and unreachable code refactoring opportunities."""

from __future__ import annotations

import re
from typing import Optional

from care.analysis.context_graph import ContextGraph
from care.analysis.security_knowledge import SecurityKnowledgeBase
from care.core.models import FunctionInfo, RefactoringOpportunity
from care.detection.common import (
    column,
    goto_target,
    iter_code_lines,
    label_locations,
    label_name,
    make_opportunity,
    return_statement,
)


class DeadCodeDetector:
    """Find unreachable statements, orphan labels, and constant branches."""

    def detect(
        self,
        function: FunctionInfo,
        context_graph: ContextGraph,
        knowledge_base: SecurityKnowledgeBase,
    ) -> list[RefactoringOpportunity]:
        opportunities: list[RefactoringOpportunity] = []
        opportunities.extend(self._detect_code_after_unconditional_jump(function, context_graph))
        opportunities.extend(self._detect_unreached_labels(function, context_graph))
        opportunities.extend(self._detect_constant_branches(function, context_graph))
        return opportunities

    def _detect_code_after_unconditional_jump(
        self,
        function: FunctionInfo,
        context_graph: ContextGraph,
    ) -> list[RefactoringOpportunity]:
        opportunities: list[RefactoringOpportunity] = []
        pending_jump: Optional[dict] = None
        previous_statement = ""
        for line_number, raw_line, statement, depth, guarded in _iter_code_lines_with_depth(function):
            if _is_preprocessor_boundary(statement):
                pending_jump = None
                previous_statement = statement
                continue
            label = label_name(statement)
            if label:
                pending_jump = None
                previous_statement = statement
                continue
            if pending_jump is not None and statement not in {"{", "}"}:
                opportunities.append(
                    make_opportunity(
                        function=function,
                        context_graph=context_graph,
                        kind="dead_code",
                        line=line_number,
                        column=column(raw_line, statement[:1]) or 1,
                        severity="high",
                        description=f"Statement after unconditional {pending_jump['kind']} is unreachable.",
                        evidence={
                            "unreachable_statement": statement,
                            "unconditional_jump": pending_jump,
                            "pattern": f"code after unconditional {pending_jump['kind']}",
                            "confidence": 0.88,
                        },
                    )
                )
                pending_jump = None
                previous_statement = statement
                continue
            target = goto_target(statement)
            if target:
                if (
                    _is_complete_jump_statement(statement)
                    and depth <= 1
                    and not guarded
                    and not _is_guarded_by_previous_if(previous_statement)
                ):
                    pending_jump = {
                        "kind": "goto",
                        "line": line_number,
                        "target": target,
                        "statement": statement,
                    }
                previous_statement = statement
                continue
            if return_statement(statement):
                if (
                    _is_complete_jump_statement(statement)
                    and depth <= 1
                    and not guarded
                    and not _is_guarded_by_previous_if(previous_statement)
                ):
                    pending_jump = {
                        "kind": "return",
                        "line": line_number,
                        "statement": statement,
                    }
            previous_statement = statement
        return opportunities

    def _detect_unreached_labels(
        self,
        function: FunctionInfo,
        context_graph: ContextGraph,
    ) -> list[RefactoringOpportunity]:
        opportunities: list[RefactoringOpportunity] = []
        labels = label_locations(function)
        goto_targets = {
            target
            for _, _, statement in iter_code_lines(function)
            for target in [goto_target(statement)]
            if target
        }
        for label, location in labels.items():
            if label in goto_targets:
                continue
            opportunities.append(
                make_opportunity(
                    function=function,
                    context_graph=context_graph,
                    kind="dead_code",
                    line=location.line,
                    column=location.column,
                    severity="medium",
                    description=f"Label {label} is never reached by a goto.",
                    evidence={
                        "label": label,
                        "label_location": location.to_dict(),
                        "goto_targets": sorted(goto_targets),
                        "pattern": "labels never reached",
                        "confidence": 0.66,
                    },
                )
            )
        return opportunities

    def _detect_constant_branches(
        self,
        function: FunctionInfo,
        context_graph: ContextGraph,
    ) -> list[RefactoringOpportunity]:
        opportunities: list[RefactoringOpportunity] = []
        for line_number, raw_line, statement in iter_code_lines(function):
            match = re.search(r"\b(if|while)\s*\((0|1|false|true|FALSE|TRUE)\)", statement)
            if not match:
                continue
            condition = match.group(2)
            opportunities.append(
                make_opportunity(
                    function=function,
                    context_graph=context_graph,
                    kind="dead_code",
                    line=line_number,
                    column=column(raw_line, match.group(1)),
                    severity="medium",
                    description=f"Branch has constant condition {condition}.",
                    evidence={
                        "condition": condition,
                        "statement": statement,
                        "pattern": "branches with constant conditions",
                        "confidence": 0.92,
                    },
                )
            )
        return opportunities


def _iter_code_lines_with_depth(function: FunctionInfo) -> list[tuple[int, str, str, int, bool]]:
    result: list[tuple[int, str, str, int, bool]] = []
    depth = 0
    in_block_comment = False
    pending_control_condition = False
    control_paren_balance = 0
    guard_next_statement = False
    for offset, raw_line in enumerate(function.body.splitlines()):
        line_number = function.start_line + offset
        code, in_block_comment = _strip_comments_with_state(raw_line, in_block_comment)
        stripped = code.strip()
        depth_before = depth
        if stripped.startswith("}"):
            depth_before = max(0, depth_before - stripped.count("}"))
        statement = " ".join(stripped.split())

        if pending_control_condition:
            control_paren_balance += stripped.count("(") - stripped.count(")")
            if control_paren_balance <= 0:
                pending_control_condition = False
                guard_next_statement = "{" not in stripped
            depth += code.count("{")
            depth -= code.count("}")
            depth = max(0, depth)
            continue

        if _starts_multiline_control_condition(statement):
            control_paren_balance = statement.count("(") - statement.count(")")
            if control_paren_balance > 0:
                pending_control_condition = True
            else:
                guard_next_statement = "{" not in statement
            depth += code.count("{")
            depth -= code.count("}")
            depth = max(0, depth)
            continue

        if statement and statement not in {"{", "}"}:
            guarded = guard_next_statement
            result.append((line_number, raw_line, statement, depth_before, guarded))
            if guard_next_statement:
                guard_next_statement = False
        depth += code.count("{")
        depth -= code.count("}")
        depth = max(0, depth)
    return result


def _is_guarded_by_previous_if(statement: str) -> bool:
    return bool(
        re.match(r"^(?:}\s*)?else\b", statement)
        or re.match(r"^(?:}\s*else\s+)?if\s*\(", statement)
    )


def _is_complete_jump_statement(statement: str) -> bool:
    return statement.rstrip().endswith(";")


def _is_preprocessor_boundary(statement: str) -> bool:
    return statement.lstrip().startswith("#")


def _starts_multiline_control_condition(statement: str) -> bool:
    return bool(re.match(r"^(?:}\s*else\s+)?(?:if|while|for)\s*\(", statement)) and not re.search(
        r"\b(?:return|goto)\b",
        statement,
    )


def _strip_comments_with_state(line: str, in_block_comment: bool) -> tuple[str, bool]:
    result = []
    index = 0
    while index < len(line):
        if in_block_comment:
            end = line.find("*/", index)
            if end == -1:
                return "".join(result), True
            index = end + 2
            in_block_comment = False
            continue
        if line.startswith("//", index):
            break
        if line.startswith("/*", index):
            in_block_comment = True
            index += 2
            continue
        result.append(line[index])
        index += 1
    return "".join(result), in_block_comment
