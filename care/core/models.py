"""Core JSON-serializable dataclasses used across CARE."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


class JsonSerializableMixin:
    """Small helper shared by CARE models for JSON-oriented output."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, **kwargs: Any) -> str:
        return json.dumps(self.to_dict(), **kwargs)


@dataclass
class CodeLocation(JsonSerializableMixin):
    file: str
    line: int
    column: Optional[int]


@dataclass
class FunctionInfo(JsonSerializableMixin):
    name: str
    file: str
    start_line: int
    end_line: int
    signature: str
    body: str
    local_variables: list[str] = field(default_factory=list)
    return_statements: list[str] = field(default_factory=list)
    goto_statements: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BasicBlockInfo(JsonSerializableMixin):
    id: str
    function: str
    statements: list[str]


@dataclass
class EdgeInfo(JsonSerializableMixin):
    src: str
    dst: str
    edge_type: str  # call, data, control, goto, return, resource


@dataclass
class ResourceEvent(JsonSerializableMixin):
    kind: str  # alloc, free, lock, unlock, open, close
    variable: str
    location: CodeLocation


@dataclass
class RefactoringOpportunity(JsonSerializableMixin):
    id: str
    kind: str
    file: str
    function: str
    location: CodeLocation
    severity: str
    description: str
    evidence: dict[str, Any]
    context_summary: str


@dataclass
class PatchCandidate(JsonSerializableMixin):
    id: str
    opportunity_id: str
    diff: str
    explanation: str
    confidence: float


@dataclass
class StageResult(JsonSerializableMixin):
    name: str
    passed: bool
    logs: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    counterexamples: list[str] = field(default_factory=list)


@dataclass
class ValidationResult(JsonSerializableMixin):
    passed: bool
    stage_results: dict[str, Any]
    logs: list[str]
    counterexamples: list[str]


@dataclass
class RefactoringReport(JsonSerializableMixin):
    project: str
    opportunities: list[RefactoringOpportunity]
    selected_patches: list[PatchCandidate]
    validation_results: list[ValidationResult]


@dataclass
class SourceFile(JsonSerializableMixin):
    """Internal source-file record used while constructing project context."""

    path: str
    language: str


@dataclass
class ProjectContext(JsonSerializableMixin):
    """Internal aggregate context assembled before opportunity detection."""

    root: str
    source_files: list[SourceFile] = field(default_factory=list)
    functions: list[FunctionInfo] = field(default_factory=list)
    basic_blocks: list[BasicBlockInfo] = field(default_factory=list)
    edges: list[EdgeInfo] = field(default_factory=list)
    resource_events: list[ResourceEvent] = field(default_factory=list)
    ast_index: dict[str, Any] = field(default_factory=dict)
    cfg_index: dict[str, Any] = field(default_factory=dict)
    call_graph: dict[str, list[str]] = field(default_factory=dict)
    dataflow_facts: dict[str, Any] = field(default_factory=dict)
    security_facts: dict[str, Any] = field(default_factory=dict)


@dataclass
class RefactoringPlan(JsonSerializableMixin):
    """Structured refactoring plan produced for an opportunity."""

    opportunity_id: str
    intent: str
    affected_files: list[str]
    affected_functions: list[str]
    required_invariants: list[str]
    security_constraints: list[str]
    behavior_preservation_goals: list[str]
    patch_strategy: str
    validation_strategy: list[str]


@dataclass
class PipelineResult(JsonSerializableMixin):
    """Internal result object used by the CLI reporter."""

    success: bool
    project: str
    opportunities: list[RefactoringOpportunity] = field(default_factory=list)
    plans: list[RefactoringPlan] = field(default_factory=list)
    patches: list[PatchCandidate] = field(default_factory=list)
    candidate_patches: list[PatchCandidate] = field(default_factory=list)
    validations: list[ValidationResult] = field(default_factory=list)
    structured_report: Optional[RefactoringReport] = None
    report: str = ""
