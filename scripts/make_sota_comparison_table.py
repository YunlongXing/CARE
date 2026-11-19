"""Generate a paper-ready comparison of CARE with LLM repair/refactoring systems."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


DEFAULT_OUTPUT_DIR = "benchmarks/oss50/paper-tables"


SYSTEMS: list[dict[str, Any]] = [
    {
        "system": "ChatRepair",
        "primary_task": "APR for failing tests",
        "typical_input": "buggy program plus test feedback",
        "language_scope": "Java/Defects4J-style APR",
        "context": "conversation and failed-test feedback",
        "validation": "test-suite guided repair",
        "security_resource_checks": "no dedicated resource/security-preservation gates",
        "refactoring_safety": "not targeted",
        "care_difference": "CARE targets behavior-preserving refactoring, not only bug repair.",
    },
    {
        "system": "RepairAgent",
        "primary_task": "agentic APR",
        "typical_input": "bug report or failing tests",
        "language_scope": "benchmark APR tasks",
        "context": "LLM-selected tools and repair ingredients",
        "validation": "agent-invoked tests/tools",
        "security_resource_checks": "not the central acceptance criterion",
        "refactoring_safety": "not targeted",
        "care_difference": "CARE uses a fixed fail-closed validator for safety-preserving edits.",
    },
    {
        "system": "AutoCodeRover",
        "primary_task": "issue-driven program improvement",
        "typical_input": "GitHub issue and repository",
        "language_scope": "general repository tasks",
        "context": "code search and agentic localization",
        "validation": "project tests when available",
        "security_resource_checks": "not specialized for C/C++ resource lifecycles",
        "refactoring_safety": "not targeted",
        "care_difference": "CARE starts from static refactoring opportunities and validates resource/error-path invariants.",
    },
    {
        "system": "D4C-style LLM APR",
        "primary_task": "LLM-based APR prompting",
        "typical_input": "buggy code and repair prompt",
        "language_scope": "APR benchmarks",
        "context": "prompt-aligned code context",
        "validation": "test-suite validation",
        "security_resource_checks": "no dedicated security-preservation gates",
        "refactoring_safety": "not targeted",
        "care_difference": "CARE treats validation semantics and resource lifecycle as first-class invariants.",
    },
    {
        "system": "RefactoringMirror",
        "primary_task": "LLM-assisted refactoring recommendation",
        "typical_input": "refactoring examples/documents",
        "language_scope": "Java refactoring study",
        "context": "expected refactoring category and source context",
        "validation": "reapply through tested refactoring engines",
        "security_resource_checks": "not focused on C/C++ resource/error paths",
        "refactoring_safety": "yes, via refactoring-engine reapplication",
        "care_difference": "CARE handles C/C++ source diffs with resource, semantic, and security-regression gates.",
    },
    {
        "system": "CARE",
        "primary_task": "security-preserving C/C++ refactoring",
        "typical_input": "source tree, detector finding, build/test commands",
        "language_scope": "C/C++ systems projects",
        "context": "security-aware contextual refactoring graph serialized into compact prompts",
        "validation": "patch apply, build/test, static, resource, semantic, and security checks",
        "security_resource_checks": "first-class acceptance gates",
        "refactoring_safety": "central objective",
        "care_difference": "baseline system",
    },
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)

    output_dir = (Path.cwd() / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "table_sota_comparison.csv", SYSTEMS)
    write_markdown(output_dir / "table_sota_comparison.md", SYSTEMS)
    write_latex(output_dir / "table_sota_comparison.tex", SYSTEMS)
    (output_dir / "table_sota_comparison.json").write_text(
        json.dumps(SYSTEMS, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"wrote SOTA comparison artifacts to {output_dir}")
    return 0


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = [
        "# SOTA Positioning Table",
        "",
        "| System | Primary task | Input | Context | Validation | Security/resource gates | CARE distinction |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| {system} | {primary_task} | {typical_input} | {context} | {validation} | "
            "{security_resource_checks} | {care_difference} |".format(**row)
        )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_latex(path: Path, rows: list[dict[str, Any]]) -> None:
    body = []
    for row in rows:
        body.append(
            "        {system} & {task} & {context} & {validation} & {security} \\\\".format(
                system=latex_escape(row["system"]),
                task=latex_escape(row["primary_task"]),
                context=latex_escape(row["context"]),
                validation=latex_escape(row["validation"]),
                security=latex_escape(row["security_resource_checks"]),
            )
        )
    latex = "\n".join(
        [
            "\\begin{table*}[t]",
            "    \\centering",
            "    \\caption{Positioning \\SysName{} against LLM-based repair and refactoring systems.}",
            "    \\label{tab:sota-positioning}",
            "    \\footnotesize",
            "    \\begin{tabularx}{\\textwidth}{@{}lXXXX@{}}",
            "        \\toprule",
            "        System & Task & Context & Validation & Security/resource gates \\\\",
            "        \\midrule",
            *body,
            "        \\bottomrule",
            "    \\end{tabularx}",
            "\\end{table*}",
            "",
        ]
    )
    path.write_text(latex, encoding="utf-8")


def latex_escape(value: str) -> str:
    return (
        value.replace("\\", "\\textbackslash{}")
        .replace("_", "\\_")
        .replace("%", "\\%")
        .replace("&", "\\&")
    )


if __name__ == "__main__":
    raise SystemExit(main())
