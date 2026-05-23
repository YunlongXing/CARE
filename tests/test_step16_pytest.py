from __future__ import annotations

import shutil
from pathlib import Path

from care.analysis.ast_parser import ASTParser
from care.analysis.cfg_builder import CFGBuilder
from care.analysis.context_graph import ContextGraphBuilder
from care.analysis.security_knowledge import SecurityKnowledgeBase
from care.core.models import (
    CodeLocation,
    PatchCandidate,
    PipelineResult,
    RefactoringOpportunity,
    ValidationResult,
)
from care.core.project import ProjectLoader
from care.detection.duplicate_check_detector import DuplicateCheckDetector
from care.detection.goto_detector import GotoDetector
from care.reporting.reporter import CareReportWriter
from care.validation.compiler import CompileChecker
from care.validation.resource_checker import ResourceConsistencyChecker
from care.validation.semantic_checker import SemanticEquivalenceChecker


ROOT = Path(__file__).resolve().parents[1]


def test_ast_parsing_and_cfg_construction(tmp_path: Path) -> None:
    project = _project_with_source(
        tmp_path,
        """
        int parsed(int x) {
            if (x)
                return 1;
            return 0;
        }
        """,
    )
    functions = ASTParser().parse_project(project)

    assert [function.name for function in functions] == ["parsed"]
    assert functions[0].return_statements == ["return 1;", "return 0;"]

    blocks, edges = CFGBuilder().build(functions[0])
    assert blocks
    assert any(edge.edge_type == "control" for edge in edges)
    assert any(edge.edge_type == "return" for edge in edges)


def test_resource_event_detection_tracks_alloc_free_and_lock_release(tmp_path: Path) -> None:
    project = _project_with_source(
        tmp_path,
        """
        void lock_acquire(int *lock);
        void lock_release(int *lock);

        int resources(int fail) {
            int lock = 0;
            char *p = malloc(8);
            lock_acquire(&lock);
            if (fail) {
                free(p);
                lock_release(&lock);
                return -1;
            }
            free(p);
            lock_release(&lock);
            return 0;
        }
        """,
    )
    function = ASTParser().parse_project(project)[0]
    events = SecurityKnowledgeBase().match_resource_events(function)
    event_pairs = {(event.kind, event.variable) for event in events}

    assert ("alloc", "p") in event_pairs
    assert ("free", "p") in event_pairs
    assert ("lock", "lock") in event_pairs
    assert ("unlock", "lock") in event_pairs


def test_goto_redundancy_detection_and_cleanup_consolidation_example() -> None:
    project = ProjectLoader(ROOT / "examples" / "goto_refactor").load()
    functions = ASTParser().parse_project(project)
    context_graph = ContextGraphBuilder().build(project, functions)
    process_function = next(function for function in functions if function.name == "process")

    opportunities = GotoDetector().detect(
        process_function,
        context_graph,
        SecurityKnowledgeBase(),
    )
    consolidation = next(
        opportunity
        for opportunity in opportunities
        if opportunity.kind == "goto_cleanup_consolidation"
    )

    assert "free(p);" in consolidation.evidence["duplicated_statements"]
    assert "lock_release(&lock);" in consolidation.evidence["duplicated_statements"]
    assert "ret variable" in consolidation.evidence["suggested_refactoring"]


def test_duplicate_check_detection_allows_only_unmutated_checks(tmp_path: Path) -> None:
    project = _project_with_source(
        tmp_path,
        """
        void mutate(char **p);

        int safe(char *p, int len) {
            if (!p)
                return -1;
            if (len <= 0)
                return -2;
            if (!p)
                return -1;
            return p[0] + len;
        }

        int unsafe(char *p) {
            if (!p)
                return -1;
            mutate(&p);
            if (!p)
                return -1;
            return p[0];
        }
        """,
    )
    functions = ASTParser().parse_project(project)
    context_graph = ContextGraphBuilder().build(project, functions)
    detector = DuplicateCheckDetector()
    opportunities = [
        opportunity
        for function in functions
        for opportunity in detector.detect(function, context_graph, SecurityKnowledgeBase())
    ]
    by_function = {opportunity.function: opportunity for opportunity in opportunities}

    assert by_function["safe"].evidence["safe_to_remove"] is True
    assert by_function["unsafe"].evidence["safe_to_remove"] is False
    assert by_function["unsafe"].evidence["function_calls_between_checks"]


def test_patch_application_uses_project_loader(tmp_path: Path) -> None:
    project = _project_with_source(
        tmp_path,
        "int value(void) {\n    return 0;\n}\n",
    )
    project.backup()

    project.apply_patch(
        "--- a/sample.c\n"
        "+++ b/sample.c\n"
        "@@ -1,3 +1,3 @@\n"
        " int value(void) {\n"
        "-    return 0;\n"
        "+    return 1;\n"
        " }\n"
    )

    assert "return 1;" in project.read_file("sample.c")


def test_compile_validation_runs_example_makefile(tmp_path: Path) -> None:
    example = ROOT / "examples" / "duplicate_check"
    target = tmp_path / "duplicate_check"
    shutil.copytree(example, target)

    project = ProjectLoader(target, build_cmd="make").load()
    result = CompileChecker().run(project)

    assert result.passed, result.logs + result.counterexamples


def test_resource_consistency_validation_reports_missing_cleanup(tmp_path: Path) -> None:
    project = _project_with_source(
        tmp_path,
        """
        int leak(int fail) {
            char *p = malloc(8);
            if (fail)
                return -1;
            return 0;
        }
        """,
    )

    result = ResourceConsistencyChecker().run(project, _patch())

    assert result.passed is False
    assert any("lacks matching free" in item for item in result.counterexamples)


def test_semantic_regression_validation_detects_return_change() -> None:
    parser = ASTParser()
    before = parser.parse_file(
        "before.c",
        "int f(int x) { if (x) return -1; return 0; }\n",
    )
    after = parser.parse_file(
        "after.c",
        "int f(int x) { if (x) return -2; return 0; }\n",
    )

    result = SemanticEquivalenceChecker().run(
        project=object(),
        patch_candidate=_patch(),
        before_functions=before,
        after_functions=after,
    )

    assert result.passed is False
    assert any("return statement forms changed" in item for item in result.counterexamples)


def test_report_generation_writes_json_markdown_and_patch(tmp_path: Path) -> None:
    opportunity = RefactoringOpportunity(
        id="opp-1",
        kind="duplicate_check",
        file="sample.c",
        function="foo",
        location=CodeLocation(file="sample.c", line=3, column=5),
        severity="low",
        description="duplicate null check",
        evidence={"safe_to_remove": True, "confidence": 0.8},
        context_summary="summary",
    )
    patch = _patch(opportunity_id="opp-1")
    result = PipelineResult(
        success=True,
        project=str(tmp_path),
        opportunities=[opportunity],
        patches=[patch],
        validations=[
            ValidationResult(
                passed=True,
                stage_results={"compile": {"passed": True}},
                logs=["compile passed"],
                counterexamples=[],
            )
        ],
        report="CARE run summary",
    )

    artifacts = CareReportWriter(tmp_path).write(result)

    assert Path(artifacts["report_json"]).exists()
    assert Path(artifacts["report_markdown"]).exists()
    assert (tmp_path / "patches" / "opportunity-001-candidate-001.diff").exists()
    assert (tmp_path / "patches" / "opportunity-001-selected.diff").exists()


def _project_with_source(tmp_path: Path, source: str):
    (tmp_path / "sample.c").write_text(source.strip() + "\n", encoding="utf-8")
    return ProjectLoader(tmp_path).load()


def _patch(opportunity_id: str = "opp") -> PatchCandidate:
    return PatchCandidate(
        id="candidate-1",
        opportunity_id=opportunity_id,
        diff=(
            "--- a/sample.c\n"
            "+++ b/sample.c\n"
            "@@ -1 +1 @@\n"
            "-return 0;\n"
            "+return 0;\n"
        ),
        explanation="test patch",
        confidence=0.8,
    )
