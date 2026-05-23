"""Security-aware contextual refactoring graph."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from care.analysis.call_graph import CallGraphBuilder
from care.analysis.cfg_builder import CFGBuilder
from care.analysis.dataflow import DataFlowAnalyzer
from care.analysis.security_knowledge import SecurityKnowledgeBase
from care.core.models import (
    BasicBlockInfo,
    CodeLocation,
    EdgeInfo,
    FunctionInfo,
    ProjectContext,
    ResourceEvent,
    SourceFile,
)


@dataclass
class ContextGraph:
    """Unified graph of function, control-flow, call, data, and resource facts."""

    project: str
    nodes: dict[str, dict] = field(default_factory=dict)
    edges: list[EdgeInfo] = field(default_factory=list)
    source_files: list[SourceFile] = field(default_factory=list)
    functions: list[FunctionInfo] = field(default_factory=list)
    basic_blocks: list[BasicBlockInfo] = field(default_factory=list)
    dataflow: dict[str, dict] = field(default_factory=dict)
    resource_events: list[ResourceEvent] = field(default_factory=list)
    error_handling_paths: dict[str, list[str]] = field(default_factory=dict)
    side_effect_summaries: dict[str, str] = field(default_factory=dict)
    return_condition_summaries: dict[str, str] = field(default_factory=dict)
    security_facts: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> "ContextGraph":
        """Rehydrate a cached context graph from JSON-compatible data."""

        return cls(
            project=payload.get("project", ""),
            nodes=payload.get("nodes", {}),
            edges=[
                edge if isinstance(edge, EdgeInfo) else EdgeInfo(**edge)
                for edge in payload.get("edges", [])
            ],
            source_files=[
                source if isinstance(source, SourceFile) else SourceFile(**source)
                for source in payload.get("source_files", [])
            ],
            functions=[
                function if isinstance(function, FunctionInfo) else FunctionInfo(**function)
                for function in payload.get("functions", [])
            ],
            basic_blocks=[
                block if isinstance(block, BasicBlockInfo) else BasicBlockInfo(**block)
                for block in payload.get("basic_blocks", [])
            ],
            dataflow=payload.get("dataflow", {}),
            resource_events=[
                event
                if isinstance(event, ResourceEvent)
                else ResourceEvent(
                    kind=event["kind"],
                    variable=event["variable"],
                    location=CodeLocation(**event["location"]),
                )
                for event in payload.get("resource_events", [])
            ],
            error_handling_paths=payload.get("error_handling_paths", {}),
            side_effect_summaries=payload.get("side_effect_summaries", {}),
            return_condition_summaries=payload.get("return_condition_summaries", {}),
            security_facts=payload.get("security_facts", {}),
        )

    def to_project_context(self) -> ProjectContext:
        source_files = list(self.source_files)
        source_files.extend(
            SourceFile(path=function.file, language=_language_for_path(function.file))
            for function in self.functions
        )
        unique_sources: dict[str, SourceFile] = {source.path: source for source in source_files}
        call_graph: dict[str, list[str]] = {}
        for edge in self.edges:
            if edge.edge_type == "call":
                call_graph.setdefault(edge.src, []).append(edge.dst)

        return ProjectContext(
            root=self.project,
            source_files=list(unique_sources.values()),
            functions=self.functions,
            basic_blocks=self.basic_blocks,
            edges=self.edges,
            resource_events=self.resource_events,
            ast_index={
                "function_count": len(self.functions),
                "functions": [function.name for function in self.functions],
                "backends": _backend_counts(self.functions),
            },
            cfg_index={
                "basic_block_count": len(self.basic_blocks),
                "edge_count": len(self.edges),
            },
            call_graph=call_graph,
            dataflow_facts=self.dataflow,
            security_facts=self.security_facts,
        )


class ContextGraphBuilder:
    """Build and summarize CARE's unified contextual graph."""

    def __init__(
        self,
        prefer_clang_cfg: bool = True,
        include_cfg: bool = True,
    ) -> None:
        self.cfg_builder = CFGBuilder(prefer_clang=prefer_clang_cfg) if include_cfg else None
        self.call_graph_builder = CallGraphBuilder()
        self.dataflow_analyzer = DataFlowAnalyzer()
        self.security_kb = SecurityKnowledgeBase()
        self.graph: Optional[ContextGraph] = None

    def build(self, project: object, functions: list[FunctionInfo]) -> ContextGraph:
        project_root = _project_root(project)
        graph = ContextGraph(
            project=project_root,
            source_files=_project_source_files(project),
            functions=functions,
            security_facts=self.security_kb.default_rules(),
        )

        for function in functions:
            function_node = f"function:{function.name}"
            graph.nodes[function_node] = {
                "type": "function",
                "name": function.name,
                "file": function.file,
                "start_line": function.start_line,
                "end_line": function.end_line,
                "signature": function.signature,
            }

            blocks, cfg_edges = (
                self.cfg_builder.build(function)
                if self.cfg_builder is not None
                else ([], [])
            )
            graph.basic_blocks.extend(blocks)
            graph.edges.extend(cfg_edges)
            for block in blocks:
                graph.nodes[f"block:{block.id}"] = {
                    "type": "basic_block",
                    "id": block.id,
                    "function": block.function,
                    "statements": block.statements,
                }

            dataflow = self.dataflow_analyzer.analyze(function)
            graph.dataflow[function.name] = dataflow
            self._add_data_nodes_and_edges(graph, function, dataflow)
            graph.error_handling_paths[function.name] = _error_paths(function, dataflow)
            graph.side_effect_summaries[function.name] = _side_effect_summary(function, dataflow)
            graph.return_condition_summaries[function.name] = _return_summary(function)

        graph.edges.extend(self.call_graph_builder.build(functions))
        graph.edges = _dedupe_edges(graph.edges)
        self.graph = graph
        return graph

    def summarize_for_function(self, function_name: str) -> str:
        if self.graph is None:
            return f"No context graph has been built for function {function_name}."

        function = next(
            (candidate for candidate in self.graph.functions if candidate.name == function_name),
            None,
        )
        if function is None:
            return f"No function context found for {function_name}."

        call_edges = [
            edge.dst for edge in self.graph.edges if edge.edge_type == "call" and edge.src == function.name
        ]
        cfg_edges = [
            edge for edge in self.graph.edges if edge.src.startswith(f"{function.name}:")
        ]
        goto_edges = [edge for edge in cfg_edges if edge.edge_type == "goto"]
        dataflow = self.graph.dataflow.get(function.name, {})
        resource_variables = dataflow.get("resource_variables", [])
        status_variables = dataflow.get("return_status_variables", [])
        return_summary = self.graph.return_condition_summaries.get(function.name, "No returns found.")
        side_effects = self.graph.side_effect_summaries.get(function.name, "No side effects detected.")
        errors = self.graph.error_handling_paths.get(function.name, [])

        return (
            f"{function.name} in {function.file}:{function.start_line}-{function.end_line}. "
            f"Blocks={len([b for b in self.graph.basic_blocks if b.function == function.name])}, "
            f"calls={_format_list(call_edges)}, gotos={len(goto_edges)}, "
            f"resources={_format_list(resource_variables)}, status_vars={_format_list(status_variables)}. "
            f"Returns: {return_summary}. Side effects: {side_effects}. "
            f"Error-handling paths: {_format_list(errors)}."
        )

    def summarize_for_location(self, location: CodeLocation) -> str:
        if self.graph is None:
            return f"No context graph has been built for {location.file}:{location.line}."

        function = next(
            (
                candidate
                for candidate in self.graph.functions
                if Path(candidate.file).resolve() == Path(location.file).resolve()
                and candidate.start_line <= location.line <= candidate.end_line
            ),
            None,
        )
        if function is None:
            return f"No function context found at {location.file}:{location.line}."

        nearby = _nearby_statements(function, location.line)
        return f"{self.summarize_for_function(function.name)} Nearby statements: {_format_list(nearby)}."

    def _add_data_nodes_and_edges(
        self,
        graph: ContextGraph,
        function: FunctionInfo,
        dataflow: dict,
    ) -> None:
        for fact in dataflow.get("definitions", []):
            variable = fact["variable"]
            variable_node = f"var:{function.name}:{variable}"
            graph.nodes.setdefault(
                variable_node,
                {"type": "variable", "function": function.name, "name": variable},
            )
            block_id = _block_for_line(graph.basic_blocks, function.name, fact["line"])
            if block_id:
                graph.edges.append(EdgeInfo(src=block_id, dst=variable_node, edge_type="data"))

        for fact in dataflow.get("uses", []):
            variable = fact["variable"]
            variable_node = f"var:{function.name}:{variable}"
            if variable_node not in graph.nodes:
                continue
            block_id = _block_for_line(graph.basic_blocks, function.name, fact["line"])
            if block_id:
                graph.edges.append(EdgeInfo(src=variable_node, dst=block_id, edge_type="data"))

        for event in dataflow.get("resource_events", []):
            variable = event["variable"]
            resource_node = f"resource:{function.name}:{variable or event['kind']}:{event['line']}"
            graph.nodes[resource_node] = {
                "type": "resource_event",
                "function": function.name,
                "kind": event["kind"],
                "variable": variable,
                "line": event["line"],
            }
            block_id = _block_for_line(graph.basic_blocks, function.name, event["line"])
            if block_id:
                graph.edges.append(EdgeInfo(src=block_id, dst=resource_node, edge_type="resource"))
            graph.resource_events.append(
                ResourceEvent(
                    kind=event["kind"],
                    variable=variable,
                    location=CodeLocation(file=function.file, line=event["line"], column=None),
                )
            )


def _project_root(project: object) -> str:
    root = getattr(project, "root", project)
    return str(root)


def _backend_counts(functions: list[FunctionInfo]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for function in functions:
        backend = function.metadata.get("ast_backend", "unknown")
        counts[backend] = counts.get(backend, 0) + 1
    return counts


def _project_source_files(project: object) -> list[SourceFile]:
    if not hasattr(project, "list_source_files"):
        return []
    source_files = []
    for path in project.list_source_files():
        source_files.append(SourceFile(path=str(path), language=_language_for_path(str(path))))
    return source_files


def _error_paths(function: FunctionInfo, dataflow: dict) -> list[str]:
    paths: list[str] = []
    for goto in function.goto_statements:
        lowered = goto.lower()
        if any(token in lowered for token in ["error", "err", "fail", "cleanup", "out"]):
            paths.append(goto)
    for label in function.labels:
        if any(token in label.lower() for token in ["error", "err", "fail", "cleanup", "out"]):
            paths.append(f"label {label}")
    for statement in function.return_statements:
        normalized = statement.lower()
        if any(token in normalized for token in ["null", "-1", "false", "err", "error"]):
            paths.append(statement)
    for event in dataflow.get("resource_events", []):
        if event["kind"] in {"free", "close", "unlock"}:
            paths.append(f"{event['kind']} {event['variable']} at line {event['line']}")
    return paths


def _side_effect_summary(function: FunctionInfo, dataflow: dict) -> str:
    calls = function.calls
    resources = [
        f"{event['kind']}({event['variable']})"
        for event in dataflow.get("resource_events", [])
    ]
    pieces = []
    if calls:
        pieces.append(f"calls {_format_list(calls)}")
    if resources:
        pieces.append(f"resource events {_format_list(resources)}")
    return "; ".join(pieces) if pieces else "No obvious calls or resource events."


def _return_summary(function: FunctionInfo) -> str:
    if not function.return_statements:
        return "No explicit return statements."
    return _format_list(function.return_statements)


def _nearby_statements(function: FunctionInfo, line: int, radius: int = 2) -> list[str]:
    nearby: list[str] = []
    for offset, raw_line in enumerate(function.body.splitlines()):
        absolute_line = function.start_line + offset
        if abs(absolute_line - line) <= radius:
            statement = " ".join(raw_line.strip().split())
            if statement:
                nearby.append(f"L{absolute_line}: {statement}")
    return nearby


def _block_for_line(blocks: list[BasicBlockInfo], function_name: str, line: int) -> Optional[str]:
    # BasicBlockInfo does not yet store source ranges. Approximate by selecting
    # the first non-entry block for the function; later steps can replace this
    # with line-precise CFG nodes.
    for block in blocks:
        if block.function == function_name and block.statements != ["<entry>"]:
            if block.statements != ["<exit>"]:
                return block.id
    return None


def _language_for_path(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in {".c", ".h"}:
        return "c"
    if suffix in {".cc", ".cpp", ".hpp"}:
        return "cpp"
    return "unknown"


def _format_list(values: list, limit: int = 5) -> str:
    if not values:
        return "none"
    rendered = [str(value) for value in values[:limit]]
    if len(values) > limit:
        rendered.append(f"+{len(values) - limit} more")
    return ", ".join(rendered)


def _dedupe_edges(edges: list[EdgeInfo]) -> list[EdgeInfo]:
    seen: set[tuple[str, str, str]] = set()
    result: list[EdgeInfo] = []
    for edge in edges:
        key = (edge.src, edge.dst, edge.edge_type)
        if key not in seen:
            seen.add(key)
            result.append(edge)
    return result
