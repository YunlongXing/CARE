"""Prompt construction for LLM refactoring planning."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from care.core.models import FunctionInfo, ProjectContext, RefactoringOpportunity


class PromptBuilder:
    """Build security-aware planning prompts from CARE context."""

    def build_planning_prompt(
        self,
        context: ProjectContext,
        opportunity: RefactoringOpportunity,
        build_cmd: Optional[str] = None,
        test_cmd: Optional[str] = None,
        existing_tests: Optional[list[str]] = None,
    ) -> str:
        function = _find_function(context, opportunity)
        prompt_payload = {
            "refactoring_opportunity": opportunity.to_dict(),
            "relevant_source_code": _source_payload(function, opportunity),
            "context_graph_summary": opportunity.context_summary,
            "data_flow_summary": _dataflow_summary(context, opportunity),
            "resource_events": _resource_events(context, opportunity),
            "existing_tests": existing_tests if existing_tests is not None else _discover_existing_tests(context),
            "build_command": build_cmd,
            "test_command": test_cmd,
            "required_json_schema": {
                "opportunity_id": "string",
                "intent": "string",
                "affected_files": ["string"],
                "affected_functions": ["string"],
                "required_invariants": ["string"],
                "security_constraints": ["string"],
                "behavior_preservation_goals": ["string"],
                "patch_strategy": "string",
                "validation_strategy": ["string"],
            },
        }
        return (
            "You are a security-aware automated refactoring planner.\n\n"
            "Your task is to refactor code without changing intended behavior.\n\n"
            "You must preserve:\n"
            "1. Functional equivalence for normal inputs.\n"
            "2. Error-handling behavior.\n"
            "3. Resource lifecycle correctness.\n"
            "4. Security checks and validation semantics.\n"
            "5. API contracts and return-value conventions.\n"
            "6. Logging and externally visible side effects unless explicitly redundant.\n\n"
            "Given:\n"
            "- Refactoring opportunity\n"
            "- Relevant source code\n"
            "- Context graph summary\n"
            "- Data-flow summary\n"
            "- Resource events\n"
            "- Existing tests\n"
            "- Build/test commands\n\n"
            "Produce:\n"
            "- Patch intent\n"
            "- Refactoring strategy\n"
            "- Required invariants\n"
            "- Safety constraints\n"
            "- Candidate patch plan\n"
            "- Validation plan\n\n"
            "Return machine-readable JSON only. Do not wrap the JSON in Markdown.\n\n"
            f"{json.dumps(prompt_payload, indent=2, sort_keys=True)}"
        )

    def build_refactoring_prompt(
        self,
        context: ProjectContext,
        opportunity: RefactoringOpportunity,
    ) -> str:
        """Backward-compatible alias for planning prompt construction."""

        return self.build_planning_prompt(context, opportunity)


def _find_function(
    context: ProjectContext,
    opportunity: RefactoringOpportunity,
) -> Optional[FunctionInfo]:
    for function in context.functions:
        if function.name == opportunity.function and Path(function.file) == Path(opportunity.file):
            return function
    for function in context.functions:
        if function.name == opportunity.function:
            return function
    for function in context.functions:
        if (
            Path(function.file) == Path(opportunity.file)
            and function.start_line <= opportunity.location.line <= function.end_line
        ):
            return function
    return None


def _source_payload(
    function: Optional[FunctionInfo],
    opportunity: RefactoringOpportunity,
) -> dict[str, Any]:
    if function is None:
        return {
            "file": opportunity.file,
            "function": opportunity.function,
            "location": opportunity.location.to_dict(),
            "source": "",
        }
    return {
        "file": function.file,
        "function": function.name,
        "signature": function.signature,
        "start_line": function.start_line,
        "end_line": function.end_line,
        "source": function.body,
    }


def _dataflow_summary(
    context: ProjectContext,
    opportunity: RefactoringOpportunity,
) -> dict[str, Any]:
    dataflow = context.dataflow_facts.get(opportunity.function, {})
    return {
        "definitions": dataflow.get("definitions", []),
        "uses": dataflow.get("uses", []),
        "pointer_variables": dataflow.get("pointer_variables", []),
        "return_status_variables": dataflow.get("return_status_variables", []),
        "resource_variables": dataflow.get("resource_variables", []),
    }


def _resource_events(
    context: ProjectContext,
    opportunity: RefactoringOpportunity,
) -> list[dict[str, Any]]:
    return [
        event.to_dict()
        for event in context.resource_events
        if Path(event.location.file) == Path(opportunity.file)
        and _event_matches_function(context, opportunity, event.location.line)
    ]


def _event_matches_function(
    context: ProjectContext,
    opportunity: RefactoringOpportunity,
    line: int,
) -> bool:
    for function in context.functions:
        if function.name == opportunity.function and function.start_line <= line <= function.end_line:
            return True
    return False


def _discover_existing_tests(context: ProjectContext) -> list[str]:
    tests: list[str] = []
    for source in context.source_files:
        path = Path(source.path)
        lowered_parts = [part.lower() for part in path.parts]
        name = path.name.lower()
        if "test" in name or "tests" in lowered_parts or "test" in lowered_parts:
            tests.append(source.path)
    return sorted(tests)
