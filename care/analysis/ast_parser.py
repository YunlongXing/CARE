"""Lightweight C/C++ function extraction.

The parser tries to use a tree-sitter stack when one is installed, but the
research prototype intentionally ships with a deterministic regex/brace
fallback so CARE works in a fresh Python environment.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional, Union

from care.analysis.clang_tools import (
    ClangToolError,
    compile_args_for_project,
    dump_ast_json,
    find_clang,
)
from care.core.models import FunctionInfo

CONTROL_KEYWORDS = {
    "if",
    "else",
    "for",
    "while",
    "switch",
    "catch",
    "sizeof",
    "return",
    "do",
}
LABEL_EXCLUSIONS = {"case", "default", "public", "private", "protected"}
CALL_EXCLUSIONS = CONTROL_KEYWORDS | {
    "alignof",
    "decltype",
    "delete",
    "new",
    "static_assert",
}


class ASTParser:
    """Extract function-level facts from C/C++ source files.

    clang is used when available, because its JSON AST gives CARE a real
    translation-unit view. The regex parser remains as a deterministic fallback
    for incomplete projects, missing generated headers, and fresh environments.
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
        self.backend = "clang" if self.clang_bin else "regex"

    def parse_project(self, project: object) -> list[FunctionInfo]:
        """Parse every source file exposed by a ``CareProject``-like object."""

        functions: list[FunctionInfo] = []
        compile_args_by_file = compile_args_for_project(project)
        project_root = Path(getattr(project, "root", "."))
        for source_path in project.list_source_files():
            try:
                content = project.read_file(source_path)
            except (OSError, UnicodeDecodeError):
                continue
            functions.extend(
                self.parse_file(
                    source_path,
                    content,
                    compile_args=compile_args_by_file.get(str(Path(source_path).resolve())),
                    project_root=project_root,
                )
            )
        return functions

    def parse_file(
        self,
        path: Union[str, Path],
        content: str,
        compile_args: Optional[list[str]] = None,
        project_root: Optional[Union[str, Path]] = None,
    ) -> list[FunctionInfo]:
        """Parse one C/C++ file and return extracted function facts."""

        source_path = Path(path)
        resolved_project_root = Path(project_root) if project_root is not None else None
        if self.clang_bin:
            try:
                functions = self._parse_with_clang(
                    source_path,
                    content,
                    compile_args=compile_args,
                    project_root=resolved_project_root,
                )
                if functions:
                    _attach_compile_context(functions, compile_args, resolved_project_root)
                    return functions
            except ClangToolError:
                pass
        functions = self._parse_with_regex(source_path, content)
        _attach_compile_context(functions, compile_args, resolved_project_root)
        return functions

    def dump_translation_unit_ast(
        self,
        path: Union[str, Path],
        compile_args: Optional[list[str]] = None,
        project_root: Optional[Union[str, Path]] = None,
    ) -> dict[str, Any]:
        """Return clang's full JSON AST for a source file."""

        source_path = Path(path)
        return dump_ast_json(
            source_path=source_path,
            project_root=Path(project_root) if project_root is not None else None,
            compile_args=compile_args,
            clang_bin=self.clang_bin,
            timeout_seconds=self.timeout_seconds,
        )

    def _parse_with_clang(
        self,
        path: Path,
        content: str,
        compile_args: Optional[list[str]],
        project_root: Optional[Path],
    ) -> list[FunctionInfo]:
        ast = dump_ast_json(
            source_path=path,
            project_root=project_root,
            compile_args=compile_args,
            clang_bin=self.clang_bin,
            timeout_seconds=self.timeout_seconds,
        )
        functions: list[FunctionInfo] = []
        for node in _walk_ast(ast):
            if node.get("kind") != "FunctionDecl":
                continue
            if not _is_function_definition(node):
                continue
            if not _node_belongs_to_file(node, path):
                continue
            function = _function_from_clang_node(path, content, node)
            if function is not None:
                functions.append(function)
        return functions

    def _parse_with_regex(self, path: Path, content: str) -> list[FunctionInfo]:
        masked = _mask_comments_and_strings(content)
        functions: list[FunctionInfo] = []
        index = 0

        while index < len(masked):
            brace_index = masked.find("{", index)
            if brace_index == -1:
                break

            signature_span = _find_function_signature_span(masked, brace_index)
            if signature_span is None:
                index = brace_index + 1
                continue

            signature_start, open_paren, close_paren = signature_span
            name = _extract_function_name(masked[:open_paren])
            if name is None:
                index = brace_index + 1
                continue

            end_index = _find_matching_forward(masked, brace_index, "{", "}")
            if end_index is None:
                break

            signature = content[signature_start:brace_index].strip()
            if not _looks_like_function_signature(signature, name):
                index = end_index + 1
                continue

            body = content[brace_index : end_index + 1]
            start_line = _line_number(content, signature_start)
            end_line = _line_number(content, end_index)
            facts = _extract_body_facts(body)
            functions.append(
                FunctionInfo(
                    name=name,
                    file=str(path),
                    start_line=start_line,
                    end_line=end_line,
                    signature=_normalize_whitespace(signature),
                    body=body,
                    local_variables=facts["local_variables"],
                    return_statements=facts["return_statements"],
                    goto_statements=facts["goto_statements"],
                    labels=facts["labels"],
                    calls=facts["calls"],
                    metadata={"ast_backend": "regex"},
                )
            )
            index = end_index + 1

        return functions


def _find_function_signature_span(
    masked: str,
    brace_index: int,
) -> Optional[tuple[int, int, int]]:
    close_paren = _previous_nonspace_index(masked, brace_index - 1)
    if close_paren is None or masked[close_paren] != ")":
        return None

    open_paren = _find_matching_backward(masked, close_paren, "(", ")")
    if open_paren is None:
        return None

    name = _extract_function_name_before_paren(masked, open_paren)
    if name is None or name.split("::")[-1] in CONTROL_KEYWORDS:
        return None

    signature_start = _find_signature_start(masked, open_paren)
    return signature_start, open_paren, close_paren


def _previous_nonspace_index(text: str, start: int) -> Optional[int]:
    for index in range(start, -1, -1):
        if not text[index].isspace():
            return index
    return None


def _extract_function_name_before_paren(text: str, open_paren: int) -> Optional[str]:
    index = _previous_nonspace_index(text, open_paren - 1)
    if index is None:
        return None
    end = index + 1
    while index >= 0 and re.match(r"[A-Za-z_0-9:~]", text[index]):
        index -= 1
    name = text[index + 1 : end]
    if not name or not re.match(r"^[A-Za-z_~]", name):
        return None
    return name


def _walk_ast(node: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = [node]
    for child in node.get("inner", []) or []:
        if isinstance(child, dict):
            nodes.extend(_walk_ast(child))
    return nodes


def _attach_compile_context(
    functions: list[FunctionInfo],
    compile_args: Optional[list[str]],
    project_root: Optional[Path],
) -> None:
    for function in functions:
        if compile_args:
            function.metadata["compile_args"] = list(compile_args)
        if project_root is not None:
            function.metadata["project_root"] = str(project_root)


def _is_function_definition(node: dict[str, Any]) -> bool:
    if node.get("isThisDeclarationADefinition") is True:
        return True
    return any(child.get("kind") == "CompoundStmt" for child in node.get("inner", []) or [])


def _node_belongs_to_file(node: dict[str, Any], path: Path) -> bool:
    expected = path.resolve()
    for location in [_location_dict(node.get("loc")), _location_dict(node.get("range", {}).get("begin"))]:
        file_value = location.get("file")
        if not file_value:
            if location.get("line") is not None and "includedFrom" not in location:
                return True
            continue
        try:
            if Path(file_value).resolve() == expected:
                return True
        except OSError:
            continue
    return False


def _function_from_clang_node(
    path: Path,
    content: str,
    node: dict[str, Any],
) -> Optional[FunctionInfo]:
    name = node.get("name")
    if not name:
        return None
    compound = next(
        (child for child in node.get("inner", []) or [] if child.get("kind") == "CompoundStmt"),
        None,
    )
    function_range = _range_lines(node.get("range"), content)
    body_range = _range_lines(compound.get("range") if compound else None, content)
    if function_range is None:
        return None

    function_text = _slice_by_lines(content, function_range[0], function_range[1])
    if body_range is not None:
        body = _slice_by_lines(content, body_range[0], body_range[1])
    else:
        brace_index = function_text.find("{")
        body = function_text[brace_index:] if brace_index >= 0 else function_text

    signature = function_text.split("{", 1)[0].strip()
    if not signature:
        signature = node.get("type", {}).get("qualType", name)
    facts = _extract_body_facts(body)
    return FunctionInfo(
        name=name,
        file=str(path),
        start_line=function_range[0],
        end_line=function_range[1],
        signature=_normalize_whitespace(signature),
        body=body,
        local_variables=facts["local_variables"],
        return_statements=facts["return_statements"],
        goto_statements=facts["goto_statements"],
        labels=facts["labels"],
        calls=facts["calls"],
        metadata={
            "ast_backend": "clang",
            "clang_kind": node.get("kind"),
            "clang_id": node.get("id"),
            "clang_type": node.get("type", {}).get("qualType"),
            "clang_range": node.get("range", {}),
            "clang_ast_subtree": _compact_ast_node(node),
        },
    )


def _range_lines(
    range_value: Optional[dict[str, Any]],
    content: Optional[str] = None,
) -> Optional[tuple[int, int]]:
    if not range_value:
        return None
    begin = _location_dict(range_value.get("begin"))
    end = _location_dict(range_value.get("end"))
    start_line = begin.get("line")
    end_line = end.get("line")
    if (start_line is None or end_line is None) and content is not None:
        if "offset" in begin:
            start_line = _line_number(content, int(begin["offset"]))
        if "offset" in end:
            end_line = _line_number(content, int(end["offset"]))
    if start_line is None or end_line is None:
        return None
    return int(start_line), int(end_line)


def _location_dict(value: Optional[dict[str, Any]]) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _slice_by_lines(content: str, start_line: int, end_line: int) -> str:
    lines = content.splitlines()
    start = max(1, start_line)
    end = max(start, end_line)
    return "\n".join(lines[start - 1 : end])


def _compact_ast_node(node: dict[str, Any], depth: int = 0, max_depth: int = 6) -> dict[str, Any]:
    keys = ["id", "kind", "name", "type", "loc", "range", "opcode", "valueCategory"]
    compact = {key: node[key] for key in keys if key in node}
    if depth >= max_depth:
        if node.get("inner"):
            compact["inner_truncated"] = len(node.get("inner", []))
        return compact
    inner = [
        _compact_ast_node(child, depth + 1, max_depth)
        for child in node.get("inner", []) or []
        if isinstance(child, dict)
    ]
    if inner:
        compact["inner"] = inner
    return compact


def _find_signature_start(masked: str, open_paren: int) -> int:
    index = open_paren - 1
    depth = 0
    candidate = 0
    while index >= 0:
        char = masked[index]
        if char == ">":
            depth += 1
        elif char == "<" and depth:
            depth -= 1
        elif depth == 0 and char in ";}":
            candidate = index + 1
            break
        index -= 1

    cursor = candidate
    for line in masked[candidate:open_paren].splitlines(keepends=True):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            cursor += len(line)
            continue
        break
    return cursor


def _extract_function_name(prefix: str) -> Optional[str]:
    match = re.search(
        r"([A-Za-z_~][A-Za-z_0-9~]*(?:::[A-Za-z_~][A-Za-z_0-9~]*)*)\s*$",
        prefix,
    )
    if not match:
        return None
    return match.group(1)


def _looks_like_function_signature(signature: str, name: str) -> bool:
    if not signature or "=" in signature.split("(", 1)[0]:
        return False
    head = name.split("::")[-1]
    if head in CONTROL_KEYWORDS:
        return False
    if signature.rstrip().endswith(";"):
        return False
    return True


def _extract_body_facts(body: str) -> dict[str, list[str]]:
    masked_body = _mask_comments_and_strings(body)
    local_variables = _ordered_unique(_extract_local_variables(masked_body))
    return_statements = _ordered_unique(
        _normalize_whitespace(match.group(0))
        for match in re.finditer(r"\breturn\b[^;]*;", masked_body, flags=re.MULTILINE)
    )
    goto_statements = _ordered_unique(
        _normalize_whitespace(match.group(0))
        for match in re.finditer(r"\bgoto\s+[A-Za-z_][A-Za-z_0-9]*\s*;", masked_body)
    )
    labels = _ordered_unique(
        match.group(1)
        for match in re.finditer(
            r"^\s*([A-Za-z_][A-Za-z_0-9]*)\s*:\s*(?!:)",
            masked_body,
            flags=re.MULTILINE,
        )
        if match.group(1) not in LABEL_EXCLUSIONS
    )
    calls = _ordered_unique(
        call
        for call in _extract_calls(masked_body)
        if call.split("::")[-1] not in CALL_EXCLUSIONS
    )
    return {
        "local_variables": local_variables,
        "return_statements": return_statements,
        "goto_statements": goto_statements,
        "labels": labels,
        "calls": calls,
    }


def _extract_local_variables(masked_body: str) -> list[str]:
    variables: list[str] = []
    declaration_pattern = re.compile(
        r"^\s*(?:const\s+|static\s+|volatile\s+|unsigned\s+|signed\s+|long\s+|short\s+|"
        r"struct\s+[A-Za-z_][A-Za-z_0-9]*\s+|enum\s+[A-Za-z_][A-Za-z_0-9]*\s+|"
        r"union\s+[A-Za-z_][A-Za-z_0-9]*\s+|[A-Za-z_][A-Za-z_0-9:<>]*\s+)+"
        r"(?P<decls>[*&\s]*[A-Za-z_][A-Za-z_0-9]*.*?);",
    )
    for line in masked_body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("return", "goto", "if", "for", "while", "switch")):
            continue
        first_semicolon = stripped.find(";")
        first_colon = stripped.find(":")
        if first_colon != -1 and (first_semicolon == -1 or first_colon < first_semicolon):
            continue
        match = declaration_pattern.match(stripped)
        if not match:
            continue
        declaration = match.group("decls")
        for part in declaration.split(","):
            var_match = re.search(r"[*&\s]*([A-Za-z_][A-Za-z_0-9]*)\s*(?:=|\[|$)", part.strip())
            if var_match:
                variable = var_match.group(1)
                if variable not in CONTROL_KEYWORDS:
                    variables.append(variable)
    return variables


def _extract_calls(masked_body: str) -> list[str]:
    calls: list[str] = []
    for match in re.finditer(
        r"\b([A-Za-z_][A-Za-z_0-9]*(?:::[A-Za-z_][A-Za-z_0-9]*)*)\s*\(",
        masked_body,
    ):
        calls.append(match.group(1))
    return calls


def _find_matching_forward(
    text: str,
    start: int,
    open_char: str,
    close_char: str,
) -> Optional[int]:
    depth = 0
    for index in range(start, len(text)):
        if text[index] == open_char:
            depth += 1
        elif text[index] == close_char:
            depth -= 1
            if depth == 0:
                return index
    return None


def _find_matching_backward(
    text: str,
    start: int,
    open_char: str,
    close_char: str,
) -> Optional[int]:
    depth = 0
    for index in range(start, -1, -1):
        if text[index] == close_char:
            depth += 1
        elif text[index] == open_char:
            depth -= 1
            if depth == 0:
                return index
    return None


def _mask_comments_and_strings(content: str) -> str:
    chars = list(content)
    index = 0
    state = "code"
    while index < len(chars):
        current = chars[index]
        nxt = chars[index + 1] if index + 1 < len(chars) else ""
        if state == "code":
            if current == "/" and nxt == "/":
                chars[index] = chars[index + 1] = " "
                index += 2
                state = "line_comment"
                continue
            if current == "/" and nxt == "*":
                chars[index] = chars[index + 1] = " "
                index += 2
                state = "block_comment"
                continue
            if current == '"':
                chars[index] = " "
                index += 1
                state = "string"
                continue
            if current == "'":
                chars[index] = " "
                index += 1
                state = "char"
                continue
        elif state == "line_comment":
            if current == "\n":
                state = "code"
            else:
                chars[index] = " "
        elif state == "block_comment":
            if current == "*" and nxt == "/":
                chars[index] = chars[index + 1] = " "
                index += 2
                state = "code"
                continue
            if current != "\n":
                chars[index] = " "
        elif state == "string":
            if current == "\\":
                chars[index] = " "
                if index + 1 < len(chars) and chars[index + 1] != "\n":
                    chars[index + 1] = " "
                    index += 2
                    continue
            if current == '"':
                state = "code"
            if current != "\n":
                chars[index] = " "
        elif state == "char":
            if current == "\\":
                chars[index] = " "
                if index + 1 < len(chars) and chars[index + 1] != "\n":
                    chars[index + 1] = " "
                    index += 2
                    continue
            if current == "'":
                state = "code"
            if current != "\n":
                chars[index] = " "
        index += 1
    return "".join(chars)


def _line_number(content: str, index: int) -> int:
    return content.count("\n", 0, index) + 1


def _normalize_whitespace(text: str) -> str:
    return " ".join(text.strip().split())


def _ordered_unique(values: object) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


AstParser = ASTParser
