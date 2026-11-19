"""Create executable unsafe variants from adjudicated safe C/C++ refactorings.

The script applies each developer patch to its archived base, injects one
well-defined unsafe mutation, and emits a combined diff against the base. The
mutation operator supplies the unsafe label; agents are not used as the oracle.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable


SOURCE_EXTENSIONS = {".c", ".cc", ".cpp", ".h", ".hpp"}
DEFAULT_OPERATORS = (
    "disable_null_guard",
    "disable_bounds_guard",
    "disable_status_guard",
    "disable_allocation_guard",
    "remove_null_guard",
    "remove_bounds_guard",
    "remove_allocation_guard",
    "remove_cleanup",
    "change_error_return",
    "duplicate_release",
    "unsafe_api_regression",
)
RELEASE_CALL = re.compile(r"^(?P<indent>\s*)(?P<call>free|fclose|close|unlock|mutex_unlock|pthread_mutex_unlock)\s*\([^;]+\);\s*$", re.M)
NULL_RETURN_GUARD = re.compile(
    r"^(?P<indent>[ \t]*)if\s*\(\s*(?:!\s*(?P<neg>[A-Za-z_]\w*)|(?P<eq>[A-Za-z_]\w*)\s*==\s*NULL)\s*\)\s*\n"
    r"(?P=indent)[ \t]+return\s+-[0-9]+\s*;\s*\n",
    re.M,
)
NULL_RETURN_GUARD_CONDITION = re.compile(
    r"^(?P<indent>[ \t]*)if\s*\(\s*(?:!\s*(?P<neg>[A-Za-z_]\w*)|(?P<eq>[A-Za-z_]\w*)\s*==\s*NULL)\s*\)\s*\n"
    r"(?P=indent)[ \t]+return\s+-[0-9]+\s*;\s*\n",
    re.M,
)
ERROR_RETURN = re.compile(r"\breturn\s+-[0-9]+\s*;")
BOUNDS_RETURN_GUARD = re.compile(
    r"^(?P<indent>[ \t]*)if\s*\((?=[^\n]*\b(?:len|size|idx|index|count|bound|capacity|n)\b)"
    r"[^\n]*(?:<|>|<=|>=)[^\n]*\)\s*\n"
    r"(?P=indent)[ \t]+return\s+-[0-9]+\s*;\s*\n",
    re.M,
)
BOUNDS_RETURN_GUARD_CONDITION = re.compile(
    r"^(?P<indent>[ \t]*)if\s*\((?=[^\n]*\b(?:len|size|idx|index|count|bound|capacity|n)\b)"
    r"[^\n]*(?:<|>|<=|>=)[^\n]*\)\s*\n"
    r"(?P=indent)[ \t]+return\s+-[0-9]+\s*;\s*\n",
    re.M,
)
STATUS_RETURN_GUARD_CONDITION = re.compile(
    r"^(?P<indent>[ \t]*)if\s*\([^\n]*\b(?:ret(?:urn)?|err(?:or)?|status|result|rc)\b[^\n]*(?:==|!=|<=|>=|<|>)[^\n]*\)\s*\n"
    r"(?P=indent)[ \t]+return\s+-[0-9]+\s*;\s*\n",
    re.M | re.IGNORECASE,
)
ALLOCATION_GUARD = re.compile(
    r"(?P<allocation>^[^\n=;]*?\b(?P<var>[A-Za-z_]\w*)\s*=\s*(?:malloc|calloc|realloc)\s*\([^;]+\);\s*\n)"
    r"(?P<guard>^[ \t]*if\s*\(\s*!\s*(?P=var)\s*\)\s*\n[ \t]+return\s+-[0-9]+\s*;\s*\n)",
    re.M,
)
SNPRINTF_CALL = re.compile(r"\bsnprintf\s*\(\s*(?P<destination>[A-Za-z_]\w*)\s*,\s*[^,()]+\s*,\s*")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="Materialized safe-case manifest.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--operators", nargs="+", choices=DEFAULT_OPERATORS, default=list(DEFAULT_OPERATORS))
    parser.add_argument("--max-mutants-per-case", type=int, default=2)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    if args.max_mutants_per_case < 1:
        parser.error("--max-mutants-per-case must be positive")

    root = Path.cwd().resolve()
    manifest_path = resolve_path(root, args.manifest)
    output_dir = resolve_path(root, args.output_dir)
    if output_dir.exists():
        if not args.force:
            parser.error(f"output directory already exists: {output_dir}; use --force to replace it")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    manifest = read_json(manifest_path)
    cases, log = generate_cases(
        manifest.get("cases") or [],
        manifest_path.parent,
        output_dir,
        tuple(args.operators),
        args.max_mutants_per_case,
    )
    write_json(
        output_dir / "manifest.json",
        {
            "schema_version": 1,
            "name": "CARE patch-safety mutation benchmark",
            "source_manifest": str(manifest_path),
            "case_count": len(cases),
            "safe_case_count": sum(case["ground_truth"] == "safe" for case in cases),
            "unsafe_mutant_count": sum(case["ground_truth"] == "unsafe" for case in cases),
            "cases": cases,
        },
    )
    write_json(output_dir / "mutation_log.json", log)
    print(f"generated {len(cases)} benchmark cases with {sum(case['ground_truth'] == 'unsafe' for case in cases)} unsafe mutants")
    return 0


def generate_cases(
    source_cases: list[dict[str, Any]],
    source_root: Path,
    output_dir: Path,
    operators: tuple[str, ...],
    max_mutants_per_case: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cases: list[dict[str, Any]] = []
    log: list[dict[str, Any]] = []
    for index, source_case in enumerate(source_cases, start=1):
        if source_case.get("ground_truth") != "safe":
            continue
        case_slug = f"r{index:04d}"
        source_dir = source_root / str(source_case["source_dir"])
        source_copy = output_dir / "sources" / case_slug
        shutil.copytree(source_dir, source_copy)
        safe_patch = source_root / str(source_case["candidate_diff"])
        safe_patch_copy = output_dir / "patches" / f"{case_slug}-safe.diff"
        safe_patch_copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(safe_patch, safe_patch_copy)
        safe_case = {
            **select_case_fields(source_case),
            "id": f"{source_case['id']}:safe",
            "kind": "semantic_preserving_refactoring",
            "ground_truth": "safe",
            "source_dir": str(source_copy.relative_to(output_dir)),
            "candidate_diff": str(safe_patch_copy.relative_to(output_dir)),
        }
        cases.append(safe_case)
        generated = 0
        for operator in operators:
            if generated >= max_mutants_per_case:
                break
            mutant = generate_mutant(
                source_copy,
                safe_patch_copy,
                operator,
                changed_files=source_case.get("changed_files") or [],
            )
            if mutant is None:
                log.append({"case_id": source_case["id"], "operator": operator, "status": "not_applicable"})
                continue
            relative_file, combined_diff, detail = mutant
            mutant_patch = output_dir / "patches" / f"{case_slug}-{operator}.diff"
            mutant_patch.write_text(combined_diff, encoding="utf-8")
            verify_patch_applies(source_copy, mutant_patch)
            cases.append(
                {
                    **select_case_fields(source_case),
                    "id": f"{source_case['id']}:{operator}",
                    "kind": "unsafe_mutant",
                    "ground_truth": "unsafe",
                    "mutation_operator": operator,
                    "source_dir": str(source_copy.relative_to(output_dir)),
                    "candidate_diff": str(mutant_patch.relative_to(output_dir)),
                    "expected_violations": [operator],
                    "parent_safe_case": safe_case["id"],
                    "mutation_file": relative_file,
                    "mutation_detail": detail,
                }
            )
            log.append({"case_id": source_case["id"], "operator": operator, "status": "generated", "file": relative_file, "detail": detail})
            generated += 1
    return cases, log


def select_case_fields(case: dict[str, Any]) -> dict[str, Any]:
    return {
        key: case[key]
        for key in ("project_id", "base_commit", "patch_commit", "build_cmd", "test_cmd", "changed_files")
        if key in case
    }


def generate_mutant(
    source_dir: Path,
    safe_patch: Path,
    operator: str,
    changed_files: list[str],
) -> tuple[str, str, str] | None:
    with tempfile.TemporaryDirectory(prefix="care-mutation-") as temporary:
        safe_workspace = Path(temporary) / "safe"
        mutated_workspace = Path(temporary) / "mutated"
        shutil.copytree(source_dir, safe_workspace)
        apply_patch(safe_workspace, safe_patch)
        shutil.copytree(safe_workspace, mutated_workspace)
        target = mutate_workspace(mutated_workspace, operator, changed_files=changed_files)
        if target is None:
            return None
        relative_file, detail = target
        combined_diff = unified_diff(source_dir, mutated_workspace)
        if not combined_diff.strip():
            return None
        return relative_file, combined_diff, detail


def mutate_workspace(
    workspace: Path,
    operator: str,
    changed_files: list[str] | None = None,
) -> tuple[str, str] | None:
    mutators: dict[str, Callable[[str], tuple[str, str] | None]] = {
        "disable_null_guard": mutate_disable_null_guard,
        "disable_bounds_guard": mutate_disable_bounds_guard,
        "disable_status_guard": mutate_disable_status_guard,
        "disable_allocation_guard": mutate_disable_allocation_guard,
        "remove_null_guard": mutate_remove_null_guard,
        "remove_bounds_guard": mutate_remove_bounds_guard,
        "remove_allocation_guard": mutate_remove_allocation_guard,
        "remove_cleanup": mutate_remove_cleanup,
        "change_error_return": mutate_error_return,
        "duplicate_release": mutate_duplicate_release,
        "unsafe_api_regression": mutate_unsafe_api,
    }
    mutate = mutators[operator]
    paths = _mutation_targets(workspace, changed_files or [])
    for path in paths:
        try:
            original = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        outcome = mutate(original)
        if outcome is None:
            continue
        changed, detail = outcome
        path.write_text(changed, encoding="utf-8")
        return str(path.relative_to(workspace)), detail
    return None


def _mutation_targets(workspace: Path, changed_files: list[str]) -> list[Path]:
    """Restrict mutations to files edited by the developer patch when known."""

    changed_paths = [
        workspace / relative
        for relative in changed_files
        if Path(relative).suffix.lower() in SOURCE_EXTENSIONS and (workspace / relative).is_file()
    ]
    if changed_paths:
        return sorted(changed_paths)
    return sorted(
        path
        for path in workspace.rglob("*")
        if path.is_file() and path.suffix.lower() in SOURCE_EXTENSIONS
    )


def mutate_remove_null_guard(text: str) -> tuple[str, str] | None:
    match = NULL_RETURN_GUARD.search(text)
    if not match:
        return None
    variable = match.group("neg") or match.group("eq") or "pointer"
    return text[: match.start()] + text[match.end() :], f"removed null guard for {variable} before an error return"


def mutate_disable_null_guard(text: str) -> tuple[str, str] | None:
    match = NULL_RETURN_GUARD_CONDITION.search(text)
    if not match:
        return None
    variable = match.group("neg") or match.group("eq") or "pointer"
    line_end = text.find("\n", match.start()) + 1
    replacement = f"{match.group('indent')}if (0)\n"
    return (
        text[: match.start()] + replacement + text[line_end:],
        f"disabled null guard for {variable} while retaining the error return",
    )


def mutate_remove_cleanup(text: str) -> tuple[str, str] | None:
    match = RELEASE_CALL.search(text)
    if not match:
        return None
    return text[: match.start()] + text[match.end() :], f"removed {match.group('call')} release call"


def mutate_remove_bounds_guard(text: str) -> tuple[str, str] | None:
    match = BOUNDS_RETURN_GUARD.search(text)
    if not match:
        return None
    return text[: match.start()] + text[match.end() :], "removed a relational bounds guard before an error return"


def mutate_disable_bounds_guard(text: str) -> tuple[str, str] | None:
    match = BOUNDS_RETURN_GUARD_CONDITION.search(text)
    if not match:
        return None
    line_end = text.find("\n", match.start()) + 1
    replacement = f"{match.group('indent')}if (0)\n"
    return (
        text[: match.start()] + replacement + text[line_end:],
        "disabled a relational bounds guard while retaining the error return",
    )


def mutate_disable_status_guard(text: str) -> tuple[str, str] | None:
    match = STATUS_RETURN_GUARD_CONDITION.search(text)
    if not match:
        return None
    line_end = text.find("\n", match.start()) + 1
    replacement = f"{match.group('indent')}if (0)\n"
    return (
        text[: match.start()] + replacement + text[line_end:],
        "disabled a status-error guard while retaining the error return",
    )


def mutate_remove_allocation_guard(text: str) -> tuple[str, str] | None:
    match = ALLOCATION_GUARD.search(text)
    if not match:
        return None
    return text[: match.start("guard")] + text[match.end("guard") :], f"removed allocation-failure guard for {match.group('var')}"


def mutate_disable_allocation_guard(text: str) -> tuple[str, str] | None:
    match = ALLOCATION_GUARD.search(text)
    if not match:
        return None
    guard = match.group("guard")
    line_end = guard.find("\n") + 1
    indent = re.match(r"[ \t]*", guard).group(0)
    disabled_guard = f"{indent}if (0)\n{guard[line_end:]}"
    return (
        text[: match.start("guard")] + disabled_guard + text[match.end("guard") :],
        f"disabled allocation-failure guard for {match.group('var')} while retaining the error return",
    )


def mutate_error_return(text: str) -> tuple[str, str] | None:
    match = ERROR_RETURN.search(text)
    if not match:
        return None
    return text[: match.start()] + "return 0;" + text[match.end() :], "changed a negative error return to success"


def mutate_duplicate_release(text: str) -> tuple[str, str] | None:
    match = RELEASE_CALL.search(text)
    if not match:
        return None
    line = match.group(0)
    return text[: match.end()] + "\n" + line + text[match.end() :], f"duplicated {match.group('call')} release call"


def mutate_unsafe_api(text: str) -> tuple[str, str] | None:
    match = SNPRINTF_CALL.search(text)
    if not match:
        return None
    replacement = f"sprintf({match.group('destination')}, "
    return text[: match.start()] + replacement + text[match.end() :], "replaced snprintf with unbounded sprintf"


def unified_diff(base: Path, changed: Path) -> str:
    paths = sorted(
        path.relative_to(base)
        for path in base.rglob("*")
        if path.is_file() and path.suffix.lower() in SOURCE_EXTENSIONS
    )
    chunks: list[str] = []
    for relative in paths:
        before = (base / relative).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        after = (changed / relative).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        if before != after:
            chunks.extend(difflib.unified_diff(before, after, fromfile=f"a/{relative}", tofile=f"b/{relative}"))
    return "".join(chunks)


def apply_patch(workspace: Path, patch: Path) -> None:
    completed = subprocess.run(["git", "apply", str(patch)], cwd=workspace, text=True, capture_output=True, check=False)
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or f"could not apply {patch}")


def verify_patch_applies(workspace: Path, patch: Path) -> None:
    completed = subprocess.run(["git", "apply", "--check", str(patch)], cwd=workspace, text=True, capture_output=True, check=False)
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or f"generated patch does not apply: {patch}")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def resolve_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


if __name__ == "__main__":
    raise SystemExit(main())
