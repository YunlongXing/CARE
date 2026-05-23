"""Lightweight resource consistency validation."""

from __future__ import annotations

import re
from collections import defaultdict

from care.analysis.security_knowledge import SecurityKnowledgeBase
from care.core.models import FunctionInfo, PatchCandidate, ResourceEvent, StageResult
from care.detection.common import iter_code_lines, return_statement
from care.validation.common import parse_project_functions, stage_from_exception

ACQUIRE_TO_RELEASE = {"alloc": "free", "open": "close", "lock": "unlock"}
RELEASE_TO_ACQUIRE = {"free": "alloc", "close": "open", "unlock": "lock"}


class ResourceConsistencyChecker:
    """Check obvious resource leaks, double releases, and early return bypasses."""

    def __init__(self) -> None:
        self.knowledge_base = SecurityKnowledgeBase()

    def run(
        self,
        project: object,
        patch_candidate: PatchCandidate,
        functions: list[FunctionInfo] | None = None,
        before_functions: list[FunctionInfo] | None = None,
        prefer_clang: bool = True,
    ) -> StageResult:
        try:
            functions = functions if functions is not None else parse_project_functions(
                project,
                prefer_clang=prefer_clang,
            )
            scoped_functions = _functions_touched_by_diff(
                functions,
                patch_candidate.diff,
                use_new_ranges=True,
            )
            scoped_before_functions = (
                _functions_touched_by_diff(
                    before_functions,
                    patch_candidate.diff,
                    use_new_ranges=False,
                )
                if before_functions is not None
                else None
            )
            if scoped_functions:
                functions = scoped_functions
                if scoped_before_functions is not None:
                    before_functions = scoped_before_functions
            counterexamples, logs = _collect_resource_findings(self.knowledge_base, functions)
            if scoped_functions:
                logs.insert(
                    0,
                    "resource scope: "
                    + ", ".join(
                        f"{function.name}@{function.file}:{function.start_line}"
                        for function in scoped_functions
                    ),
                )
            if before_functions is not None:
                before_issues, _ = _collect_resource_findings(self.knowledge_base, before_functions)
                before_issue_set = set(before_issues)
                counterexamples = [
                    issue for issue in counterexamples if issue not in before_issue_set
                ]
                logs.append(
                    f"resource regression check: {len(before_issues)} pre-existing issue(s), "
                    f"{len(counterexamples)} new issue(s)"
                )
            return StageResult(
                name="resource_consistency",
                passed=not counterexamples,
                logs=logs,
                counterexamples=counterexamples,
            )
        except Exception as exc:
            return stage_from_exception("resource_consistency", exc)


_HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)


def _functions_touched_by_diff(
    functions: list[FunctionInfo] | None,
    diff: str,
    *,
    use_new_ranges: bool,
) -> list[FunctionInfo]:
    if not functions:
        return []
    ranges_by_file = _changed_ranges_by_file(diff, use_new_ranges=use_new_ranges)
    if not ranges_by_file:
        return []

    touched: list[FunctionInfo] = []
    for function in functions:
        function_path = _normalise_path(function.file)
        for diff_path, ranges in ranges_by_file.items():
            if not (
                function_path.endswith(diff_path)
                or diff_path.endswith(function_path)
            ):
                continue
            if any(
                function.start_line <= end and function.end_line >= start
                for start, end in ranges
            ):
                touched.append(function)
                break
    return touched


def _changed_ranges_by_file(
    diff: str,
    *,
    use_new_ranges: bool,
) -> dict[str, list[tuple[int, int]]]:
    ranges_by_file: dict[str, list[tuple[int, int]]] = defaultdict(list)
    current_file: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip().split("\t", 1)[0]
            current_file = None if path == "/dev/null" else _strip_diff_prefix(path)
            continue
        match = _HUNK_RE.match(line)
        if match is None or current_file is None:
            continue
        prefix = "new" if use_new_ranges else "old"
        start = int(match.group(f"{prefix}_start"))
        count_text = match.group(f"{prefix}_count")
        count = int(count_text) if count_text is not None else 1
        end = start + max(count, 1) - 1
        ranges_by_file[current_file].append((start, end))
    return ranges_by_file


def _strip_diff_prefix(path: str) -> str:
    normalised = _normalise_path(path)
    for prefix in ("a/", "b/"):
        if normalised.startswith(prefix):
            return normalised[len(prefix):]
    return normalised


def _normalise_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _collect_resource_findings(
    knowledge_base: SecurityKnowledgeBase,
    functions: list[FunctionInfo],
) -> tuple[list[str], list[str]]:
    counterexamples: list[str] = []
    logs: list[str] = []
    for function in functions:
        events = knowledge_base.match_resource_events(function)
        issues = _resource_issues(function, events)
        counterexamples.extend(issues)
        if events:
            logs.append(
                f"{function.name}: resource events "
                + ", ".join(f"{event.kind}:{event.variable}@{event.location.line}" for event in events)
            )
    return counterexamples, logs


def _resource_issues(function: object, events: list[ResourceEvent]) -> list[str]:
    issues: list[str] = []
    grouped: dict[str, list[ResourceEvent]] = defaultdict(list)
    for event in events:
        if event.variable:
            grouped[event.variable].append(event)

    for variable, variable_events in grouped.items():
        variable_events.sort(key=lambda event: event.location.line)
        for acquire in [event for event in variable_events if event.kind in ACQUIRE_TO_RELEASE]:
            release_kind = ACQUIRE_TO_RELEASE[acquire.kind]
            releases = [
                event
                for event in variable_events
                if event.kind == release_kind and event.location.line > acquire.location.line
            ]
            if not releases:
                issues.append(
                    f"{function.name}: {variable} acquired via {acquire.kind} at line "
                    f"{acquire.location.line} lacks matching {release_kind}"
                )
            issues.extend(_early_return_issues(function, variable, acquire, releases))

        for release_kind in RELEASE_TO_ACQUIRE:
            releases = [event for event in variable_events if event.kind == release_kind]
            if len(releases) > 1:
                lines = ", ".join(str(event.location.line) for event in releases)
                issues.append(f"{function.name}: obvious double {release_kind} of {variable} at lines {lines}")
    return issues


def _early_return_issues(
    function: object,
    variable: str,
    acquire: ResourceEvent,
    releases: list[ResourceEvent],
) -> list[str]:
    issues: list[str] = []
    if acquire.kind not in {"alloc", "lock"}:
        return issues
    release_line = releases[0].location.line if releases else getattr(function, "end_line", 10**9) + 1
    returns = [
        (line_number, statement)
        for line_number, _, statement in iter_code_lines(function)
        if return_statement(statement) or " return " in f" {statement} "
    ]
    final_return = returns[-1][0] if returns else None
    for line_number, statement in returns:
        if releases == [] and line_number == final_return:
            continue
        if acquire.location.line < line_number < release_line:
            issues.append(
                f"{function.name}: early return at line {line_number} may bypass cleanup of "
                f"{variable} acquired at line {acquire.location.line}: {statement}"
            )
    return issues


ResourceChecker = ResourceConsistencyChecker
