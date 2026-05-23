"""LLM-oriented refactoring planning."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from care.core.models import ProjectContext, RefactoringOpportunity, RefactoringPlan
from care.llm.prompt_builder import PromptBuilder


class RefactoringPlanner:
    """Convert a detected opportunity into a structured JSON-safe plan."""

    def __init__(
        self,
        build_cmd: Optional[str] = None,
        test_cmd: Optional[str] = None,
        existing_tests: Optional[list[str]] = None,
        prompt_builder: Optional[PromptBuilder] = None,
    ) -> None:
        self.build_cmd = build_cmd
        self.test_cmd = test_cmd
        self.existing_tests = existing_tests
        self.prompt_builder = prompt_builder or PromptBuilder()
        self.last_prompt: Optional[str] = None

    def plan(
        self,
        context: ProjectContext,
        opportunity: RefactoringOpportunity,
    ) -> RefactoringPlan:
        self.last_prompt = self.prompt_builder.build_planning_prompt(
            context=context,
            opportunity=opportunity,
            build_cmd=self.build_cmd,
            test_cmd=self.test_cmd,
            existing_tests=self.existing_tests,
        )
        return RefactoringPlan(
            opportunity_id=opportunity.id,
            intent=_intent_for(opportunity),
            affected_files=_affected_files(context, opportunity),
            affected_functions=_affected_functions(context, opportunity),
            required_invariants=_required_invariants(context, opportunity),
            security_constraints=_security_constraints(context, opportunity),
            behavior_preservation_goals=_behavior_preservation_goals(opportunity),
            patch_strategy=_patch_strategy(opportunity),
            validation_strategy=_validation_strategy(self.build_cmd, self.test_cmd, opportunity),
        )

    def build_prompt(
        self,
        context: ProjectContext,
        opportunity: RefactoringOpportunity,
    ) -> str:
        return self.prompt_builder.build_planning_prompt(
            context=context,
            opportunity=opportunity,
            build_cmd=self.build_cmd,
            test_cmd=self.test_cmd,
            existing_tests=self.existing_tests,
        )


def _intent_for(opportunity: RefactoringOpportunity) -> str:
    mapping = {
        "goto_redundant_cleanup": "Remove redundant cleanup around goto-based error handling.",
        "goto_redundant_error_block": "Consolidate repeated cleanup calls in a goto error-handling block.",
        "goto_duplicated_error_handling": "Deduplicate identical branch error-handling paths.",
        "goto_chain": "Collapse unnecessary goto redirection while preserving cleanup flow.",
        "goto_single_use_label": "Replace a single-use goto label with structured control flow when safe.",
        "goto_cleanup_consolidation": "Consolidate repeated manual cleanup returns into one shared goto cleanup path.",
        "resource_imbalance": "Repair resource lifecycle imbalance without changing ownership semantics.",
        "resource_early_return": "Route early returns through required cleanup or equivalent structured cleanup.",
        "duplicate_check": "Remove or consolidate a duplicate validation check only when local safety conditions hold.",
        "dead_code": "Remove unreachable code or simplify constant control flow without altering reachable behavior.",
        "security_smell": "Refactor security-sensitive code to make the risky behavior safer or easier to audit.",
    }
    return mapping.get(opportunity.kind, opportunity.description)


def _affected_files(
    context: ProjectContext,
    opportunity: RefactoringOpportunity,
) -> list[str]:
    files = {opportunity.file}
    for function in context.functions:
        if function.name == opportunity.function:
            files.add(function.file)
    return sorted(files)


def _affected_functions(
    context: ProjectContext,
    opportunity: RefactoringOpportunity,
) -> list[str]:
    functions = {opportunity.function}
    for callee in context.call_graph.get(opportunity.function, []):
        if callee:
            functions.add(callee)
    for caller, callees in context.call_graph.items():
        if opportunity.function in callees:
            functions.add(caller)
    return sorted(function for function in functions if function)


def _required_invariants(
    context: ProjectContext,
    opportunity: RefactoringOpportunity,
) -> list[str]:
    invariants = [
        "Preserve observable behavior on all reachable normal paths.",
        "Preserve the original order of side effects unless an operation is proven redundant.",
        "Preserve API contracts, parameter expectations, and return-value conventions.",
    ]
    if "goto" in opportunity.kind or "cleanup" in opportunity.description.lower():
        invariants.extend(
            [
                "Preserve every existing error-handling path and target cleanup label semantics.",
                "Preserve the relative order of cleanup statements and status-variable updates.",
            ]
        )
    if "resource" in opportunity.kind or _has_resource_evidence(opportunity, context):
        invariants.extend(
            [
                "Every successful acquire/open/lock must have a matching release/close/unlock on all exits.",
                "Do not introduce leaks, double releases, use-after-release, or unlock-without-lock behavior.",
            ]
        )
    if opportunity.kind == "duplicate_check":
        invariants.extend(
            [
                "Remove a duplicate check only when no assignment, mutating call, or aliasing risk occurs between checks.",
                "Preserve the exact error result associated with each validation failure.",
            ]
        )
    if opportunity.kind == "dead_code":
        invariants.append("Only remove code that is unreachable or controlled by a proven constant condition.")
    return _ordered_unique(invariants)


def _security_constraints(
    context: ProjectContext,
    opportunity: RefactoringOpportunity,
) -> list[str]:
    constraints = [
        "Do not weaken input validation, bounds checks, null checks, status checks, or sanity checks.",
        "Do not hide unsafe API usage or unchecked return values inside broader refactors.",
        "Preserve logging and externally visible side effects unless the detector evidence proves redundancy.",
    ]
    evidence = opportunity.evidence
    if evidence.get("safe_to_remove") is False:
        constraints.append("Treat this duplicate check as non-removable until the reported safety blockers are resolved.")
    if evidence.get("aliasing_risk"):
        constraints.append("Account for aliasing before moving or removing checks involving the reported variable.")
    if "resource_variable" in evidence:
        constraints.append(f"Preserve ownership and cleanup semantics for {evidence['resource_variable']}.")
    if "involved_resource_variables" in evidence:
        variables = ", ".join(evidence.get("involved_resource_variables") or [])
        if variables:
            constraints.append(f"Preserve cleanup semantics for resource variables: {variables}.")
    for rule_group in ["resource_management", "error_handling", "validation", "security_smells"]:
        for rule in context.security_facts.get(rule_group, [])[:2]:
            constraints.append(rule)
    return _ordered_unique(constraints)


def _behavior_preservation_goals(opportunity: RefactoringOpportunity) -> list[str]:
    goals = [
        "Functional equivalence for normal inputs.",
        "Equivalent error-handling behavior for failing inputs and resource acquisition failures.",
        "Equivalent return values, status-variable values, and API-visible outcomes.",
        "Equivalent resource lifecycle behavior across success and failure paths.",
        "Equivalent validation semantics and security boundary enforcement.",
        "No change to logging, tracing, or externally visible side effects unless explicitly redundant.",
    ]
    if opportunity.kind == "security_smell":
        goals.append("Improve local safety or auditability without changing intended caller-visible behavior.")
    return goals


def _patch_strategy(opportunity: RefactoringOpportunity) -> str:
    evidence = opportunity.evidence
    if opportunity.kind == "goto_cleanup_consolidation":
        return (
            "Introduce a local return/status variable, set it on each early return path, and jump to a "
            "single out label that performs the duplicated cleanup exactly once in the original order. "
            "Preserve side effects that occur before each original return, and keep the final return value "
            "equivalent to the original branch."
        )
    if opportunity.kind.startswith("goto"):
        duplicated = evidence.get("duplicated_statements") or []
        labels = evidence.get("label_locations") or []
        return (
            "Localize the goto target and incoming paths, then replace redundant or chained goto flow "
            "with a structured branch/cleanup shape. Keep cleanup order intact, remove only duplicated "
            f"statements supported by evidence={duplicated}, and preserve labels/paths={labels} until "
            "equivalence is clear."
        )
    if opportunity.kind == "resource_imbalance":
        return (
            "Add, move, or consolidate the matching cleanup operation for the reported resource variable. "
            "Use a single cleanup path when possible, guard releases as needed, and avoid double release."
        )
    if opportunity.kind == "resource_early_return":
        return (
            "Rewrite early exits after resource acquisition to pass through cleanup or introduce an "
            "equivalent scoped cleanup pattern. Preserve the original return value."
        )
    if opportunity.kind == "duplicate_check":
        if evidence.get("safe_to_remove"):
            return (
                "Remove or merge the later duplicate check while preserving the first check's error behavior. "
                "Keep surrounding side effects and comments near the remaining validation path."
            )
        return (
            "Do not remove the duplicate yet. First resolve reported assignments, mutating calls, or aliasing "
            "risks, then reassess whether the later check is redundant."
        )
    if opportunity.kind == "dead_code":
        return (
            "Delete only unreachable statements or simplify only proven constant branches. Avoid touching "
            "nearby reachable cleanup or error propagation code."
        )
    if opportunity.kind == "security_smell":
        return (
            "Make the risky API/error-handling pattern explicit and safer where possible. Prefer small local "
            "changes that preserve caller-visible behavior and keep security checks auditable."
        )
    return "Apply the smallest local refactor that satisfies detector evidence and validation constraints."


def _validation_strategy(
    build_cmd: Optional[str],
    test_cmd: Optional[str],
    opportunity: RefactoringOpportunity,
) -> list[str]:
    strategy = [
        "Inspect the generated diff to confirm it is limited to affected files/functions.",
        "Re-run CARE static/security validation on the changed function.",
        "Check that resource acquire/release and error paths match the required invariants.",
    ]
    if build_cmd:
        strategy.append(f"Run build command: {build_cmd}")
    else:
        strategy.append("Run the configured build command when available.")
    if test_cmd:
        strategy.append(f"Run test command: {test_cmd}")
    else:
        strategy.append("Run the configured test suite when available.")
    if opportunity.kind == "duplicate_check":
        strategy.append("Add or run tests covering both the original and duplicate validation failure paths.")
    if opportunity.kind.startswith("goto") or "resource" in opportunity.kind:
        strategy.append("Exercise success, allocation/open/lock failure, and cleanup/error-return paths.")
    if opportunity.kind == "security_smell":
        strategy.append("Run security checks focused on unsafe API usage, unchecked returns, and validation semantics.")
    return _ordered_unique(strategy)


def _has_resource_evidence(
    opportunity: RefactoringOpportunity,
    context: ProjectContext,
) -> bool:
    if "resource" in opportunity.evidence:
        return True
    if "resource_variable" in opportunity.evidence:
        return True
    if opportunity.evidence.get("involved_resource_variables"):
        return True
    return any(
        Path(event.location.file) == Path(opportunity.file)
        and event.location.line >= opportunity.location.line - 20
        and event.location.line <= opportunity.location.line + 20
        for event in context.resource_events
    )


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
