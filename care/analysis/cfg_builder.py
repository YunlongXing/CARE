"""Lightweight intra-procedural CFG construction."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from care.analysis.clang_tools import ClangToolError, dump_cfg_text, find_clang
from care.core.models import BasicBlockInfo, EdgeInfo, FunctionInfo


class CFGBuilder:
    """Build an intra-procedural CFG.

    clang analyzer CFG is preferred when clang can parse the source file. CARE
    falls back to the older compact heuristic CFG when clang is unavailable or a
    project cannot be parsed standalone.
    """

    def __init__(
        self,
        prefer_clang: bool = True,
        clang_bin: Optional[str] = None,
        timeout_seconds: int = 30,
    ) -> None:
        self.prefer_clang = prefer_clang
        self.clang_bin = find_clang(clang_bin) if prefer_clang else None
        self.timeout_seconds = timeout_seconds
        self._cfg_cache: dict[str, str] = {}

    def build(self, function: FunctionInfo) -> tuple[list[BasicBlockInfo], list[EdgeInfo]]:
        if self.clang_bin:
            try:
                result = self._build_with_clang(function)
                if result is not None:
                    return result
            except ClangToolError:
                pass
        return self._build_lightweight(function)

    def dump_clang_cfg(self, function: FunctionInfo) -> str:
        """Return clang's raw CFG dump for a function's translation unit."""

        path = str(Path(function.file).resolve())
        compile_args = _compile_args_from_metadata(function.metadata)
        project_root = _project_root_from_metadata(function.metadata)
        cache_key = json.dumps(
            {
                "path": path,
                "compile_args": compile_args,
                "project_root": str(project_root) if project_root is not None else None,
            },
            sort_keys=True,
        )
        if cache_key not in self._cfg_cache:
            self._cfg_cache[cache_key] = dump_cfg_text(
                source_path=Path(function.file),
                project_root=project_root,
                compile_args=compile_args,
                clang_bin=self.clang_bin,
                timeout_seconds=self.timeout_seconds,
            )
        return self._cfg_cache[cache_key]

    def _build_with_clang(
        self,
        function: FunctionInfo,
    ) -> Optional[tuple[list[BasicBlockInfo], list[EdgeInfo]]]:
        path = Path(function.file)
        if not path.exists():
            return None
        raw_cfg = self.dump_clang_cfg(function)
        section = _clang_cfg_section(raw_cfg, function.name)
        if not section:
            return None
        blocks, edges = _parse_clang_cfg_section(function, section)
        if not blocks:
            return None
        return blocks, edges

    def _build_lightweight(self, function: FunctionInfo) -> tuple[list[BasicBlockInfo], list[EdgeInfo]]:
        statements = _extract_statement_units(function.body)
        blocks = [
            BasicBlockInfo(id=f"{function.name}:entry", function=function.name, statements=["<entry>"])
        ]
        blocks.extend(
            BasicBlockInfo(
                id=f"{function.name}:bb{index}",
                function=function.name,
                statements=[statement],
            )
            for index, statement in enumerate(statements, start=1)
        )
        blocks.append(BasicBlockInfo(id=f"{function.name}:exit", function=function.name, statements=["<exit>"]))

        edges: list[EdgeInfo] = []
        if len(blocks) == 2:
            edges.append(EdgeInfo(src=blocks[0].id, dst=blocks[-1].id, edge_type="control"))
            return blocks, edges

        edges.append(EdgeInfo(src=blocks[0].id, dst=blocks[1].id, edge_type="control"))

        label_targets = _label_targets(blocks)
        for index in range(1, len(blocks) - 1):
            block = blocks[index]
            statement = block.statements[0]
            next_block = blocks[index + 1]
            normalized = statement.strip()

            goto_target = _goto_target(normalized)
            if goto_target:
                target_id = label_targets.get(goto_target)
                if target_id:
                    edges.append(EdgeInfo(src=block.id, dst=target_id, edge_type="goto"))
                continue

            if _is_return(normalized):
                edges.append(EdgeInfo(src=block.id, dst=blocks[-1].id, edge_type="return"))
                continue

            edges.append(EdgeInfo(src=block.id, dst=next_block.id, edge_type="control"))

            if _is_branch(normalized) and index + 2 < len(blocks):
                edges.append(EdgeInfo(src=block.id, dst=blocks[index + 2].id, edge_type="control"))
            if _is_loop(normalized) and index + 1 < len(blocks) - 1:
                edges.append(EdgeInfo(src=blocks[index + 1].id, dst=block.id, edge_type="control"))
            if _is_switch(normalized) and index + 2 < len(blocks):
                edges.append(EdgeInfo(src=block.id, dst=blocks[index + 2].id, edge_type="control"))

        return blocks, _dedupe_edges(edges)


def _clang_cfg_section(raw_cfg: str, function_name: str) -> str:
    lines = raw_cfg.splitlines()
    sections: list[tuple[str, list[str]]] = []
    current_name: Optional[str] = None
    current_lines: list[str] = []
    for line in lines:
        if line and not line.startswith(" ") and not line.startswith("[") and "(" in line:
            if current_name and current_lines:
                sections.append((current_name, current_lines))
            current_name = _function_name_from_cfg_header(line)
            current_lines = [line]
            continue
        if current_name:
            current_lines.append(line)
    if current_name and current_lines:
        sections.append((current_name, current_lines))

    for name, section_lines in sections:
        if name == function_name or name.endswith(f"::{function_name}"):
            return "\n".join(section_lines)
    return ""


def _compile_args_from_metadata(metadata: dict[str, Any]) -> Optional[list[str]]:
    args = metadata.get("compile_args")
    if not isinstance(args, list):
        return None
    return [str(arg) for arg in args]


def _project_root_from_metadata(metadata: dict[str, Any]) -> Optional[Path]:
    project_root = metadata.get("project_root")
    if not project_root:
        return None
    return Path(str(project_root))


def _function_name_from_cfg_header(line: str) -> str:
    prefix = line.split("(", 1)[0].strip()
    match = re.search(r"([A-Za-z_~][A-Za-z_0-9~]*(?:::[A-Za-z_~][A-Za-z_0-9~]*)?)$", prefix)
    return match.group(1) if match else prefix


def _parse_clang_cfg_section(
    function: FunctionInfo,
    section: str,
) -> tuple[list[BasicBlockInfo], list[EdgeInfo]]:
    blocks: list[BasicBlockInfo] = []
    edges: list[EdgeInfo] = []
    current_block: Optional[BasicBlockInfo] = None
    for raw_line in section.splitlines():
        header = re.match(r"\s*\[(B\d+)(?:\s+\((ENTRY|EXIT)\))?\]", raw_line)
        if header:
            block_id = f"{function.name}:{header.group(1)}"
            label = header.group(2)
            statements = [f"<{label.lower()}>"] if label else []
            current_block = BasicBlockInfo(
                id=block_id,
                function=function.name,
                statements=statements,
            )
            blocks.append(current_block)
            continue
        if current_block is None:
            continue
        statement = re.match(r"\s+(?:\d+|T):\s+(.*)$", raw_line)
        if statement:
            current_block.statements.append(statement.group(1).strip())
            continue
        successors = re.search(r"Succs \(\d+\):\s*(.*)$", raw_line)
        if successors:
            for target in re.findall(r"\bB\d+\b", successors.group(1)):
                block_text = " ".join(current_block.statements)
                if target == "B0":
                    edge_type = "return"
                elif re.search(r"\bgoto\b", block_text):
                    edge_type = "goto"
                else:
                    edge_type = "control"
                edges.append(
                    EdgeInfo(
                        src=current_block.id,
                        dst=f"{function.name}:{target}",
                        edge_type=edge_type,
                    )
                )
    for block in blocks:
        if not block.statements:
            block.statements.append("<empty>")
    return blocks, _dedupe_edges(edges)


def _extract_statement_units(body: str) -> list[str]:
    inner = body.strip()
    if inner.startswith("{") and inner.endswith("}"):
        inner = inner[1:-1]

    statements: list[str] = []
    current = ""
    paren_depth = 0
    bracket_depth = 0

    for raw_line in inner.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        for char in line:
            current += char
            if char == "(":
                paren_depth += 1
            elif char == ")" and paren_depth:
                paren_depth -= 1
            elif char == "[":
                bracket_depth += 1
            elif char == "]" and bracket_depth:
                bracket_depth -= 1

            if char == ";" and paren_depth == 0 and bracket_depth == 0:
                _append_statement(statements, current)
                current = ""
        if current.strip() in {"{", "}"}:
            current = ""
        elif _line_is_control_boundary(line):
            _append_statement(statements, current)
            current = ""
        else:
            current += " "

    _append_statement(statements, current)
    return statements


def _append_statement(statements: list[str], statement: str) -> None:
    normalized = " ".join(statement.replace("{", " { ").replace("}", " } ").split())
    normalized = normalized.strip()
    if normalized and normalized not in {"{", "}"}:
        statements.append(normalized)


def _line_is_control_boundary(line: str) -> bool:
    stripped = line.strip()
    if stripped.endswith("{") and re.match(r"^(if|else|for|while|switch|do)\b", stripped):
        return True
    if re.match(r"^(case\b.*:|default\s*:|[A-Za-z_][A-Za-z_0-9]*\s*:)$", stripped):
        return True
    return False


def _label_targets(blocks: list[BasicBlockInfo]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for block in blocks:
        statement = block.statements[0].strip()
        match = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)\s*:", statement)
        if match and match.group(1) not in {"case", "default"}:
            labels[match.group(1)] = block.id
    return labels


def _goto_target(statement: str) -> Optional[str]:
    match = re.search(r"\bgoto\s+([A-Za-z_][A-Za-z_0-9]*)\s*;", statement)
    return match.group(1) if match else None


def _is_return(statement: str) -> bool:
    return bool(re.match(r"^return\b", statement))


def _is_branch(statement: str) -> bool:
    return bool(re.match(r"^(if|else\s+if|else)\b", statement))


def _is_loop(statement: str) -> bool:
    return bool(re.match(r"^(for|while|do)\b", statement))


def _is_switch(statement: str) -> bool:
    return bool(re.match(r"^switch\b", statement))


def _dedupe_edges(edges: list[EdgeInfo]) -> list[EdgeInfo]:
    seen: set[tuple[str, str, str]] = set()
    result: list[EdgeInfo] = []
    for edge in edges:
        key = (edge.src, edge.dst, edge.edge_type)
        if key not in seen:
            seen.add(key)
            result.append(edge)
    return result


CfgBuilder = CFGBuilder
