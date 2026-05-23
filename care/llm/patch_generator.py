"""LLM-backed unified-diff patch generation."""

from __future__ import annotations

import hashlib
import difflib
import json
import re
from pathlib import Path
from typing import Any, Optional

from care.core.models import PatchCandidate, RefactoringPlan, ValidationResult
from care.llm.client import LLMClient
from care.refinement.feedback import RepairFeedback

SUPPORTED_MODES = {"conservative", "aggressive"}


class PatchGenerator:
    """Generate one or more unified-diff patch candidates for a plan."""

    def __init__(
        self,
        client: Optional[LLMClient] = None,
        default_mode: str = "conservative",
        default_candidates: int = 3,
    ) -> None:
        self.client = client
        self.default_mode = _validate_mode(default_mode)
        self.default_candidates = max(1, default_candidates)
        self.last_prompts: list[str] = []

    def build_prompt(
        self,
        source_snippet: str,
        full_function_body: str,
        plan: RefactoringPlan,
        context_summary: str,
        validation_feedback: Optional[Any] = None,
        mode: str = "conservative",
        candidate_index: int = 0,
    ) -> str:
        mode = _validate_mode(mode)
        payload = {
            "mode": mode,
            "candidate_index": candidate_index,
            "source_snippet": source_snippet,
            "full_function_body": full_function_body,
            "refactoring_plan": plan.to_dict(),
            "context_summary": context_summary,
            "validation_feedback": _feedback_text(validation_feedback),
        }
        mode_guidance = (
            "small local rewrite only"
            if mode == "conservative"
            else "may introduce helper function or structured cleanup abstraction"
        )
        return (
            "You are a security-aware C/C++ refactoring engine.\n\n"
            "Generate a minimal unified diff.\n"
            "Preferred workflow: produce a machine-readable JSON edit plan; CARE will convert it "
            "to a unified diff and run git apply --check before validation.\n\n"
            "Rules:\n"
            "- Preserve program behavior.\n"
            "- Preserve return values and error codes.\n"
            "- Preserve resource release order unless intentionally improved.\n"
            "- Do not remove security checks unless proven redundant.\n"
            "- Do not introduce new allocations unless necessary.\n"
            "- Do not introduce new global state.\n"
            "- Do not silently swallow errors.\n"
            "- Keep the patch small.\n"
            "- Prefer structured cleanup and helper functions only when they reduce duplication.\n"
            "- Include comments only when they clarify non-obvious safety constraints.\n\n"
            "JSON edit plan requirements:\n"
            "- Prefer JSON with this schema: {\"edits\": [{\"file\": \"path\", "
            "\"operation\": \"replace|delete|insert_before|insert_after\", "
            "\"old\": \"exact old text\", \"text\": \"new text\", "
            "\"start_line\": 10, \"end_line\": 12, \"anchor\": \"exact anchor\"}]}.\n"
            "- Use exact file paths relative to the project root.\n"
            "- Use exact old text or exact line ranges from the provided source.\n"
            "- Keep edits small and local to affected files/functions.\n\n"
            "Unified diff requirements:\n"
            "- Use real file paths relative to the project root, for example --- a/path/file.c and +++ b/path/file.c.\n"
            "- Use exact hunk headers such as @@ -10,7 +10,8 @@; never use placeholders like @@ ... @@.\n"
            "- Include enough unchanged context lines for patch(1) or git apply to locate the change.\n"
            "- Do not include prose, markdown, JSON, or explanations outside the diff.\n\n"
            "Support two modes:\n"
            "mode=conservative:\n"
            "  small local rewrite only\n\n"
            "mode=aggressive:\n"
            "  may introduce helper function or structured cleanup abstraction\n\n"
            f"Mode: {mode}\n"
            f"Mode guidance: {mode_guidance}.\n\n"
            "Fallback: if a precise JSON edit plan is not possible, output only a valid unified diff.\n"
            "Output only a valid unified diff.\n\n"
            f"{json.dumps(payload, indent=2, sort_keys=True)}"
        )

    def generate_candidates(
        self,
        source_snippet: str,
        full_function_body: str,
        plan: RefactoringPlan,
        context_summary: str,
        validation_feedback: Optional[Any] = None,
        mode: str = "conservative",
        n: int = 3,
        project: Optional[Any] = None,
    ) -> list[PatchCandidate]:
        mode = _validate_mode(mode)
        count = max(1, n)
        candidates: list[PatchCandidate] = []
        self.last_prompts = []

        for index in range(count):
            prompt = self.build_prompt(
                source_snippet=source_snippet,
                full_function_body=full_function_body,
                plan=plan,
                context_summary=context_summary,
                validation_feedback=validation_feedback,
                mode=mode,
                candidate_index=index,
            )
            self.last_prompts.append(prompt)
            diff, explanation, confidence = self._generate_diff(
                prompt=prompt,
                mode=mode,
                project=project,
                plan=plan,
            )
            candidates.append(
                PatchCandidate(
                    id=_candidate_id(plan, mode, index, diff),
                    opportunity_id=plan.opportunity_id,
                    diff=diff,
                    explanation=explanation,
                    confidence=confidence,
                )
            )
        return candidates

    def generate(
        self,
        plan: RefactoringPlan,
        iteration: int = 0,
        source_snippet: str = "",
        full_function_body: str = "",
        context_summary: str = "",
        validation_feedback: Optional[Any] = None,
        mode: Optional[str] = None,
        project: Optional[Any] = None,
    ) -> PatchCandidate:
        candidates = self.generate_candidates(
            source_snippet=source_snippet,
            full_function_body=full_function_body,
            plan=plan,
            context_summary=context_summary,
            validation_feedback=validation_feedback,
            mode=mode or self.default_mode,
            n=1,
            project=project,
        )
        candidate = candidates[0]
        candidate.id = f"patch:{plan.opportunity_id}:{iteration}:{candidate.id.split(':')[-1]}"
        return candidate

    def _generate_diff(
        self,
        prompt: str,
        mode: str,
        project: Optional[Any],
        plan: RefactoringPlan,
    ) -> tuple[str, str, float]:
        if self.client is None:
            return (
                "",
                "No LLM client configured; produced an empty no-op unified diff fallback.",
                0.0,
            )

        try:
            raw_output = self.client.complete(prompt)
        except NotImplementedError:
            return (
                "",
                "LLM client is not implemented; produced an empty no-op unified diff fallback.",
                0.0,
            )
        except Exception as exc:
            return (
                "",
                f"LLM patch generation failed: {exc}",
                0.0,
            )

        diff = ""
        explanation = ""
        confidence_boost = 0.0
        edit_plan = _extract_json_edit_plan(raw_output)
        if edit_plan is not None and project is not None:
            try:
                diff = _diff_from_edit_plan(project=project, plan=plan, edit_plan=edit_plan)
                explanation = "LLM-generated JSON edit plan converted to a unified diff by CARE."
                confidence_boost = 0.05
            except Exception as exc:
                explanation = f"JSON edit plan could not be converted to a diff: {exc}; fell back to unified diff parsing."

        if not diff:
            diff = _extract_unified_diff(raw_output)
            explanation = explanation or "LLM-generated unified diff candidate."

        diff = _ensure_trailing_newline(_normalize_hunk_headers(diff))
        if not _looks_like_unified_diff(diff):
            return (
                "",
                explanation
                if explanation.startswith("JSON edit plan")
                else "LLM output was not a valid JSON edit plan or unified diff; candidate was discarded.",
                0.05,
            )

        confidence = (0.72 if mode == "conservative" else 0.62) + confidence_boost
        return diff, explanation, min(confidence, 0.95)


def _validate_mode(mode: str) -> str:
    if mode not in SUPPORTED_MODES:
        raise ValueError(f"unsupported patch generation mode: {mode}")
    return mode


def _feedback_text(validation_feedback: Optional[Any]) -> str:
    if validation_feedback is None:
        return ""
    if isinstance(validation_feedback, str):
        return validation_feedback
    if isinstance(validation_feedback, RepairFeedback):
        return "\n".join(validation_feedback.messages)
    if isinstance(validation_feedback, ValidationResult):
        parts = list(validation_feedback.logs)
        parts.extend(validation_feedback.counterexamples)
        return "\n".join(parts)
    if isinstance(validation_feedback, list):
        return "\n".join(str(item) for item in validation_feedback)
    if isinstance(validation_feedback, dict):
        return json.dumps(validation_feedback, sort_keys=True)
    return str(validation_feedback)


def _extract_unified_diff(output: str) -> str:
    stripped = output.strip()
    fenced = re.search(r"```(?:diff)?\s*(.*?)```", stripped, flags=re.DOTALL)
    if fenced:
        stripped = fenced.group(1).strip()

    diff_start_candidates = [
        index
        for index in [stripped.find("diff --git"), stripped.find("--- ")]
        if index >= 0
    ]
    if diff_start_candidates:
        stripped = stripped[min(diff_start_candidates) :].strip()
    return stripped


def _extract_json_edit_plan(output: str) -> Optional[dict[str, Any]]:
    stripped = output.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", stripped, flags=re.DOTALL)
    if fenced:
        stripped = fenced.group(1).strip()
    candidates = [stripped]
    first_object = stripped.find("{")
    last_object = stripped.rfind("}")
    if 0 <= first_object < last_object:
        candidates.append(stripped[first_object : last_object + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("edits"), list):
            return parsed
        if isinstance(parsed, list):
            return {"edits": parsed}
    return None


def _diff_from_edit_plan(project: Any, plan: RefactoringPlan, edit_plan: dict[str, Any]) -> str:
    edits = edit_plan.get("edits")
    if not isinstance(edits, list) or not edits:
        raise ValueError("edit plan has no edits")

    grouped: dict[str, list[dict[str, Any]]] = {}
    for edit in edits:
        if not isinstance(edit, dict):
            raise ValueError("edit entries must be objects")
        file_path = edit.get("file") or edit.get("path")
        if not file_path and len(plan.affected_files) == 1:
            file_path = plan.affected_files[0]
        if not file_path:
            raise ValueError("edit entry is missing file")
        relative = _project_relative_path(project, str(file_path))
        grouped.setdefault(relative, []).append(edit)

    diff_parts: list[str] = []
    for relative, file_edits in grouped.items():
        original = project.read_file(relative)
        modified = _apply_json_edits(original, file_edits)
        if modified == original:
            continue
        diff_parts.extend(
            difflib.unified_diff(
                original.splitlines(),
                modified.splitlines(),
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
                lineterm="",
            )
        )
    if not diff_parts:
        raise ValueError("edit plan produced no file changes")
    return "\n".join(diff_parts) + "\n"


def _project_relative_path(project: Any, file_path: str) -> str:
    cleaned = file_path.strip()
    if cleaned.startswith(("a/", "b/")):
        cleaned = cleaned[2:]
    root = Path(getattr(project, "root", ".")).resolve()
    path = Path(cleaned).expanduser()
    if path.is_absolute():
        try:
            return str(path.resolve().relative_to(root))
        except ValueError:
            pass
    try:
        project.read_file(cleaned)
        return cleaned
    except Exception:
        pass
    basename = Path(cleaned).name
    for candidate in getattr(project, "source_files", []):
        candidate_path = Path(candidate)
        if candidate_path.name == basename:
            try:
                return str(candidate_path.resolve().relative_to(root))
            except ValueError:
                return str(candidate_path)
    raise ValueError(f"cannot resolve edit file: {file_path}")


def _apply_json_edits(content: str, edits: list[dict[str, Any]]) -> str:
    updated = content
    line_edits = [edit for edit in edits if _has_line_range(edit)]
    text_edits = [edit for edit in edits if not _has_line_range(edit)]
    for edit in sorted(line_edits, key=lambda item: int(item["start_line"]), reverse=True):
        updated = _apply_line_edit(updated, edit)
    for edit in text_edits:
        updated = _apply_text_edit(updated, edit)
    return updated


def _has_line_range(edit: dict[str, Any]) -> bool:
    return edit.get("start_line") is not None or edit.get("line") is not None


def _apply_line_edit(content: str, edit: dict[str, Any]) -> str:
    operation = _edit_operation(edit)
    start = int(edit.get("start_line") or edit.get("line"))
    end = int(edit.get("end_line") or start)
    lines = content.splitlines()
    if start < 1 or end < start or end > len(lines):
        raise ValueError(f"invalid line range {start}-{end}")
    replacement = _edit_text_lines(edit)
    before = lines[: start - 1]
    after = lines[end:]
    if operation == "delete":
        new_lines = before + after
    elif operation == "insert_before":
        new_lines = before + replacement + lines[start - 1 :]
    elif operation == "insert_after":
        new_lines = lines[:end] + replacement + lines[end:]
    elif operation == "replace":
        new_lines = before + replacement + after
    else:
        raise ValueError(f"unsupported edit operation: {operation}")
    return "\n".join(new_lines) + ("\n" if content.endswith("\n") else "")


def _apply_text_edit(content: str, edit: dict[str, Any]) -> str:
    operation = _edit_operation(edit)
    old = edit.get("old")
    anchor = edit.get("anchor")
    text = str(edit.get("text") if edit.get("text") is not None else edit.get("new", ""))
    if operation == "replace":
        target = str(old or anchor or "")
        if not target:
            raise ValueError("replace edit requires old text or anchor")
        if target not in content:
            raise ValueError("replace target not found")
        return content.replace(target, text, 1)
    if operation == "delete":
        target = str(old or anchor or "")
        if not target:
            raise ValueError("delete edit requires old text or anchor")
        if target not in content:
            raise ValueError("delete target not found")
        return content.replace(target, "", 1)
    if operation in {"insert_before", "insert_after"}:
        target = str(anchor or old or "")
        if not target:
            raise ValueError(f"{operation} edit requires anchor")
        if target not in content:
            raise ValueError("insert anchor not found")
        insertion = text if text.endswith("\n") else f"{text}\n"
        if operation == "insert_before":
            return content.replace(target, f"{insertion}{target}", 1)
        return content.replace(target, f"{target}{insertion}", 1)
    raise ValueError(f"unsupported edit operation: {operation}")


def _edit_operation(edit: dict[str, Any]) -> str:
    operation = str(edit.get("operation") or edit.get("type") or "replace").strip().lower()
    aliases = {
        "insert-before": "insert_before",
        "insert-after": "insert_after",
        "remove": "delete",
    }
    return aliases.get(operation, operation)


def _edit_text_lines(edit: dict[str, Any]) -> list[str]:
    text = str(edit.get("text") if edit.get("text") is not None else edit.get("new", ""))
    return text.splitlines()


def _looks_like_unified_diff(diff: str) -> bool:
    if not diff.strip():
        return False
    lines = diff.splitlines()
    has_old = any(line.startswith("--- ") for line in lines)
    has_new = any(line.startswith("+++ ") for line in lines)
    has_hunk = any(
        re.match(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@", line)
        for line in lines
    )
    return has_old and has_new and has_hunk


def _normalize_hunk_headers(diff: str) -> str:
    """Recompute hunk line counts when an LLM emits valid lines but bad counts."""

    lines = diff.splitlines()
    result: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        hunk = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$", line)
        if not hunk:
            result.append(line)
            index += 1
            continue

        old_start = hunk.group(1)
        new_start = hunk.group(2)
        suffix = hunk.group(3)
        body: list[str] = []
        index += 1
        while index < len(lines) and not lines[index].startswith("@@ "):
            if (
                lines[index].startswith("--- ")
                and index + 1 < len(lines)
                and lines[index + 1].startswith("+++ ")
            ):
                break
            body.append(lines[index])
            index += 1

        old_count = 0
        new_count = 0
        for body_line in body:
            if body_line.startswith("\\"):
                continue
            if body_line.startswith(" "):
                old_count += 1
                new_count += 1
            elif body_line.startswith("-"):
                old_count += 1
            elif body_line.startswith("+"):
                new_count += 1
            else:
                result.append(line)
                result.extend(body)
                break
        else:
            result.append(
                f"@@ -{_range_text(old_start, old_count)} "
                f"+{_range_text(new_start, new_count)} @@{suffix}"
            )
            result.extend(body)
    return "\n".join(result).strip()


def _ensure_trailing_newline(diff: str) -> str:
    return diff if not diff or diff.endswith("\n") else f"{diff}\n"


def _range_text(start: str, count: int) -> str:
    if count == 1:
        return start
    return f"{start},{count}"


def _candidate_id(plan: RefactoringPlan, mode: str, index: int, diff: str) -> str:
    digest = hashlib.sha1(diff.encode("utf-8")).hexdigest()[:10] if diff else "empty"
    return f"patch:{plan.opportunity_id}:{mode}:{index}:{digest}"
