"""Download and run CARE dry-run scans over a 50-project C/C++ suite."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any


SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".h", ".hpp"}
EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="scripts/oss50_manifest.json")
    parser.add_argument("--suite-dir", default="benchmarks/oss50")
    parser.add_argument(
        "--results-dir",
        default=None,
        help="Optional output directory for scan reports; defaults to <suite-dir>/results.",
    )
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--clone-timeout", type=int, default=1200)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--projects", default=None, help="Comma-separated project ids to scan")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--scan-only", action="store_true")
    parser.add_argument("--analysis-backend", choices=["regex", "auto", "clang"], default="regex")
    parser.add_argument("--cfg-backend", choices=["none", "lightweight", "auto", "clang"], default="none")
    parser.add_argument("--detector-confirm-backend", choices=["none", "clang"], default="none")
    parser.add_argument("--context-cache", action="store_true")
    args = parser.parse_args(argv)

    root = Path.cwd().resolve()
    manifest_path = (root / args.manifest).resolve()
    suite_dir = (root / args.suite_dir).resolve()
    sources_dir = suite_dir / "sources"
    results_dir = (root / args.results_dir).resolve() if args.results_dir else suite_dir / "results"
    logs_dir = results_dir / "logs"
    reports_dir = results_dir / "reports"
    for directory in [sources_dir, results_dir, logs_dir, reports_dir]:
        directory.mkdir(parents=True, exist_ok=True)

    projects = json.loads(manifest_path.read_text(encoding="utf-8"))
    selected_projects = parse_project_filter(args.projects)
    if selected_projects is not None:
        projects = [project for project in projects if project["id"] in selected_projects]
    if args.limit is not None:
        projects = projects[: args.limit]

    records: list[dict[str, Any]] = []
    summary_jsonl = results_dir / "project_summary.jsonl"
    if not args.download_only and args.resume and summary_jsonl.exists():
        seen = {
            json.loads(line).get("id")
            for line in summary_jsonl.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    else:
        seen = set()
        if not args.download_only:
            summary_jsonl.write_text("", encoding="utf-8")

    for index, project in enumerate(projects, start=1):
        project_id = project["id"]
        print(f"[{index:02d}/{len(projects):02d}] {project_id}", flush=True)

        project_dir = project_path(root, sources_dir, project)
        clone_record = {
            "id": project_id,
            "name": project.get("name", project_id),
            "url": project.get("url"),
            "project_path": str(project_dir),
        }
        if not args.scan_only:
            clone_record.update(
                ensure_source(project, project_dir, root, args.clone_timeout, logs_dir)
            )
        else:
            clone_record.update({"download_status": "skipped"})

        if args.download_only:
            records.append(clone_record)
            continue

        if project_id in seen:
            print(f"  scan skipped by --resume", flush=True)
            continue
        if clone_record.get("download_status") == "failed":
            record = {
                **clone_record,
                "status": "download_failed",
                "source_files": 0,
                "functions": 0,
                "opportunities": 0,
                "severity_counts": {},
                "kind_counts": {},
                "duration_seconds": 0.0,
            }
        else:
            record = {
                **clone_record,
                **run_care_scan(
                    root=root,
                    project_dir=project_dir,
                    project_id=project_id,
                    timeout=args.timeout,
                    logs_dir=logs_dir,
                    reports_dir=reports_dir,
                    analysis_backend=args.analysis_backend,
                    cfg_backend=args.cfg_backend,
                    detector_confirm_backend=args.detector_confirm_backend,
                    context_cache=args.context_cache,
                ),
            }
        records.append(record)
        append_jsonl(summary_jsonl, record)
        write_aggregate(results_dir, records_from_jsonl(summary_jsonl))

    if args.download_only:
        write_aggregate(results_dir, records)
    return 0


def project_path(root: Path, sources_dir: Path, project: dict[str, Any]) -> Path:
    reuse_path = project.get("reuse_path")
    if reuse_path:
        return (root / reuse_path).resolve()
    return (sources_dir / project["id"]).resolve()


def ensure_source(
    project: dict[str, Any],
    project_dir: Path,
    root: Path,
    timeout: int,
    logs_dir: Path,
) -> dict[str, Any]:
    project_id = project["id"]
    if project_dir.exists() and any(project_dir.iterdir()):
        return {"download_status": "existing"}
    url = project["url"]
    project_dir.parent.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{project_id}.clone.log"
    command = ["git", "clone", "--depth", "1", url, str(project_dir)]
    started = time.monotonic()
    result = run_command(command, cwd=root, timeout=timeout)
    log_path.write_text(result["output"], encoding="utf-8", errors="replace")
    status = "ok" if result["returncode"] == 0 else "failed"
    return {
        "download_status": status,
        "download_seconds": round(time.monotonic() - started, 3),
        "download_returncode": result["returncode"],
        "download_log": str(log_path),
    }


def run_care_scan(
    root: Path,
    project_dir: Path,
    project_id: str,
    timeout: int,
    logs_dir: Path,
    reports_dir: Path,
    analysis_backend: str = "regex",
    cfg_backend: str = "none",
    detector_confirm_backend: str = "none",
    context_cache: bool = False,
) -> dict[str, Any]:
    log_path = logs_dir / f"{project_id}.care.log"
    source_file_estimate = count_source_files(project_dir)
    command = [
        sys.executable,
        "-m",
        "care.cli",
        "run",
        "--project",
        str(project_dir),
        "--analysis-backend",
        analysis_backend,
        "--cfg-backend",
        cfg_backend,
        "--detector-confirm-backend",
        detector_confirm_backend,
        "--dry-run",
        "-v",
    ]
    if context_cache:
        command.append("--context-cache")
    started = time.monotonic()
    result = run_command(command, cwd=root, timeout=timeout)
    duration = round(time.monotonic() - started, 3)
    log_path.write_text(result["output"], encoding="utf-8", errors="replace")

    report_path = project_dir / "care-report.json"
    report_md_path = project_dir / "care-report.md"
    copied_json = reports_dir / f"{project_id}-care-report.json"
    copied_md = reports_dir / f"{project_id}-care-report.md"

    if result["timeout"]:
        return scan_failure(
            "timeout",
            result,
            duration,
            source_file_estimate,
            log_path,
        )
    if result["returncode"] != 0:
        return scan_failure(
            "care_failed",
            result,
            duration,
            source_file_estimate,
            log_path,
        )
    if not report_path.exists():
        return scan_failure(
            "missing_report",
            result,
            duration,
            source_file_estimate,
            log_path,
        )

    shutil.copy2(report_path, copied_json)
    if report_md_path.exists():
        shutil.copy2(report_md_path, copied_md)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    opportunities = report.get("detected_opportunities", [])
    severity_counts = Counter(opportunity.get("severity", "unknown") for opportunity in opportunities)
    kind_counts = Counter(opportunity.get("kind", "unknown") for opportunity in opportunities)
    top_opportunities = []
    for opportunity in opportunities[:5]:
        location = opportunity.get("location", {})
        top_opportunities.append(
            {
                "id": opportunity.get("id"),
                "kind": opportunity.get("kind"),
                "severity": opportunity.get("severity"),
                "file": location.get("file") or opportunity.get("file"),
                "line": location.get("line"),
                "description": opportunity.get("description"),
            }
        )

    parsed_functions = parse_first_int(result["output"], r"parsed (\d+) functions")
    source_files = parse_first_int(result["output"], r"Source files:\s+(\d+)")
    return {
        "status": "ok",
        "returncode": result["returncode"],
        "duration_seconds": duration,
        "source_files": source_files if source_files is not None else source_file_estimate,
        "source_file_estimate": source_file_estimate,
        "functions": parsed_functions or 0,
        "opportunities": len(opportunities),
        "plans": report.get("summary", {}).get("plans", 0),
        "severity_counts": dict(sorted(severity_counts.items())),
        "kind_counts": dict(sorted(kind_counts.items())),
        "top_opportunities": top_opportunities,
        "log": str(log_path),
        "report_json": str(copied_json),
        "report_md": str(copied_md) if copied_md.exists() else None,
    }


def scan_failure(
    status: str,
    result: dict[str, Any],
    duration: float,
    source_files: int,
    log_path: Path,
) -> dict[str, Any]:
    return {
        "status": status,
        "returncode": result["returncode"],
        "duration_seconds": duration,
        "source_files": source_files,
        "source_file_estimate": source_files,
        "functions": parse_first_int(result["output"], r"parsed (\d+) functions") or 0,
        "opportunities": 0,
        "plans": 0,
        "severity_counts": {},
        "kind_counts": {},
        "top_opportunities": [],
        "log": str(log_path),
    }


def run_command(command: list[str], cwd: Path, timeout: int) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        return {
            "returncode": completed.returncode,
            "timeout": False,
            "output": completed.stdout,
        }
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return {
            "returncode": 124,
            "timeout": True,
            "output": output + f"\nTIMEOUT after {timeout}s\n",
        }


def count_source_files(project_dir: Path) -> int:
    count = 0
    for dirpath, dirnames, filenames in os.walk(project_dir):
        dirnames[:] = [dirname for dirname in dirnames if dirname not in EXCLUDED_DIRS]
        for filename in filenames:
            if Path(filename).suffix.lower() in SOURCE_SUFFIXES:
                count += 1
    return count


def parse_first_int(text: str, pattern: str) -> int | None:
    match = re.search(pattern, text)
    return int(match.group(1)) if match else None


def parse_project_filter(raw: str | None) -> set[str] | None:
    if not raw:
        return None
    return {item.strip() for item in raw.split(",") if item.strip()}


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def records_from_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_aggregate(results_dir: Path, records: list[dict[str, Any]]) -> None:
    totals = {
        "projects": len(records),
        "ok_projects": sum(1 for record in records if record.get("status") == "ok"),
        "failed_projects": sum(1 for record in records if record.get("status") != "ok"),
        "source_files": sum(int(record.get("source_files") or 0) for record in records),
        "functions": sum(int(record.get("functions") or 0) for record in records),
        "opportunities": sum(int(record.get("opportunities") or 0) for record in records),
        "duration_seconds": round(sum(float(record.get("duration_seconds") or 0) for record in records), 3),
        "severity_counts": {},
        "kind_counts": {},
    }
    severity_counter: Counter[str] = Counter()
    kind_counter: Counter[str] = Counter()
    for record in records:
        severity_counter.update(record.get("severity_counts") or {})
        kind_counter.update(record.get("kind_counts") or {})
    totals["severity_counts"] = dict(sorted(severity_counter.items()))
    totals["kind_counts"] = dict(sorted(kind_counter.items()))

    payload = {
        "summary": totals,
        "projects": sorted(records, key=lambda item: item.get("id", "")),
    }
    (results_dir / "summary.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    write_csv(results_dir / "summary.csv", records)
    write_markdown(results_dir / "summary.md", payload)


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fieldnames = [
        "id",
        "name",
        "status",
        "download_status",
        "source_files",
        "functions",
        "opportunities",
        "duration_seconds",
        "critical",
        "high",
        "medium",
        "low",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            severity = record.get("severity_counts") or {}
            writer.writerow(
                {
                    "id": record.get("id"),
                    "name": record.get("name"),
                    "status": record.get("status"),
                    "download_status": record.get("download_status"),
                    "source_files": record.get("source_files"),
                    "functions": record.get("functions"),
                    "opportunities": record.get("opportunities"),
                    "duration_seconds": record.get("duration_seconds"),
                    "critical": severity.get("critical", 0),
                    "high": severity.get("high", 0),
                    "medium": severity.get("medium", 0),
                    "low": severity.get("low", 0),
                }
            )


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    lines = [
        "# CARE OSS50 Benchmark Summary",
        "",
        "## Totals",
        "",
        f"- Projects scanned: {summary['ok_projects']}/{summary['projects']}",
        f"- Source files: {summary['source_files']}",
        f"- Parsed functions: {summary['functions']}",
        f"- Opportunities: {summary['opportunities']}",
        f"- Duration seconds: {summary['duration_seconds']}",
        f"- Severity counts: `{summary['severity_counts']}`",
        "",
        "## Projects",
        "",
        "| Project | Status | Source Files | Functions | Opportunities | Critical | High | Medium | Low |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for record in payload["projects"]:
        severity = record.get("severity_counts") or {}
        lines.append(
            "| {id} | {status} | {source_files} | {functions} | {opportunities} | "
            "{critical} | {high} | {medium} | {low} |".format(
                id=record.get("id"),
                status=record.get("status"),
                source_files=record.get("source_files", 0),
                functions=record.get("functions", 0),
                opportunities=record.get("opportunities", 0),
                critical=severity.get("critical", 0),
                high=severity.get("high", 0),
                medium=severity.get("medium", 0),
                low=severity.get("low", 0),
            )
        )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
