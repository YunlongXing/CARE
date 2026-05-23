from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from care.analysis.ast_parser import ASTParser
from care.analysis.call_graph import CallGraphBuilder
from care.analysis.cfg_builder import CFGBuilder
from care.analysis.clang_tools import clang_available
from care.analysis.context_graph import ContextGraphBuilder
from care.analysis.dataflow import DataFlowAnalyzer
from care.core.models import CodeLocation
from care.core.project import ProjectLoader

SAMPLE_C = """#include <stdlib.h>
static int helper(int x) {
    int y = x + 1;
    return y;
}

int parse(int ok) {
    char *buf = malloc(10);
    int rc = 0;
    if (!buf || !ok) {
        rc = -1;
        goto cleanup;
    }
    helper(rc);
cleanup:
    free(buf);
    return rc;
}
"""


class AnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="care-analysis-test-"))
        source = self.tmpdir / "sample.c"
        source.write_text(SAMPLE_C, encoding="utf-8")
        self.project = ProjectLoader(self.tmpdir).load()
        self.functions = ASTParser().parse_project(self.project)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir)

    def test_ast_parser_extracts_function_facts(self) -> None:
        by_name = {function.name: function for function in self.functions}

        self.assertEqual(set(by_name), {"helper", "parse"})
        self.assertEqual(by_name["helper"].start_line, 2)
        self.assertEqual(by_name["parse"].start_line, 7)
        self.assertIn("y", by_name["helper"].local_variables)
        self.assertIn("buf", by_name["parse"].local_variables)
        self.assertIn("rc", by_name["parse"].local_variables)
        self.assertEqual(by_name["parse"].goto_statements, ["goto cleanup;"])
        self.assertEqual(by_name["parse"].labels, ["cleanup"])
        self.assertIn("helper", by_name["parse"].calls)

    @unittest.skipUnless(clang_available(), "clang executable is not available")
    def test_clang_ast_and_cfg_are_available_when_clang_exists(self) -> None:
        source = self.tmpdir / "sample.c"
        parser = ASTParser(prefer_clang=True)
        functions = parser.parse_project(self.project)
        by_name = {function.name: function for function in functions}
        translation_unit_ast = parser.dump_translation_unit_ast(
            source,
            project_root=self.tmpdir,
        )

        self.assertEqual(translation_unit_ast["kind"], "TranslationUnitDecl")
        self.assertEqual(by_name["parse"].metadata["ast_backend"], "clang")
        self.assertIn("clang_ast_subtree", by_name["parse"].metadata)

        blocks, edges = CFGBuilder(prefer_clang=True).build(by_name["parse"])
        self.assertTrue(any(block.id.endswith(":B0") for block in blocks))
        self.assertTrue(any(edge.edge_type == "return" for edge in edges))

    def test_cfg_builder_adds_control_goto_and_return_edges(self) -> None:
        function = next(item for item in self.functions if item.name == "parse")
        blocks, edges = CFGBuilder().build(function)

        edge_types = {edge.edge_type for edge in edges}

        self.assertGreaterEqual(len(blocks), 5)
        self.assertIn("control", edge_types)
        self.assertIn("goto", edge_types)
        self.assertIn("return", edge_types)

    def test_call_graph_builder_adds_interprocedural_edges(self) -> None:
        edges = CallGraphBuilder().build(self.functions)

        self.assertIn(("parse", "helper", "call"), {(edge.src, edge.dst, edge.edge_type) for edge in edges})

    def test_dataflow_tracks_status_and_resource_variables(self) -> None:
        function = next(item for item in self.functions if item.name == "parse")
        facts = DataFlowAnalyzer().analyze(function)

        self.assertIn("rc", facts["return_status_variables"])
        self.assertIn("buf", facts["pointer_variables"])
        self.assertIn("buf", facts["resource_variables"])
        self.assertEqual(
            [event["kind"] for event in facts["resource_events"]],
            ["alloc", "free"],
        )

    def test_context_graph_builds_prompt_summaries(self) -> None:
        graph_builder = ContextGraphBuilder()
        graph = graph_builder.build(self.project, self.functions)
        context = graph.to_project_context()
        summary = graph_builder.summarize_for_function("parse")
        location_summary = graph_builder.summarize_for_location(
            CodeLocation(file=str(self.tmpdir / "sample.c"), line=12, column=None)
        )

        self.assertEqual(len(context.functions), 2)
        self.assertEqual(len(context.source_files), 1)
        self.assertGreater(len(graph.basic_blocks), 0)
        self.assertGreater(len(graph.edges), 0)
        self.assertIn("resources=buf", summary)
        self.assertIn("gotos=1", summary)
        self.assertIn("Nearby statements", location_summary)
