from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from care.core.project import ProjectLoader


class ProjectLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="care-project-test-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir)

    def write(self, relative: str, content: str = "") -> Path:
        path = self.tmpdir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_load_detects_sources_build_files_and_compile_database(self) -> None:
        self.write("src/main.c", "int main(void) { return 0; }\n")
        self.write("src/lib.cc", "int lib() { return 1; }\n")
        self.write("include/lib.hpp", "#pragma once\n")
        self.write("notes.txt", "ignore me\n")
        self.write("Makefile", "all:\n\ttrue\n")
        self.write("CMakeLists.txt", "cmake_minimum_required(VERSION 3.20)\n")
        compile_db = self.write("build/compile_commands.json", "[]\n")

        project = ProjectLoader(
            self.tmpdir,
            build_cmd="make",
            test_cmd="make test",
        ).load()

        source_names = {Path(path).name for path in project.list_source_files()}
        build_names = {path.name for path in project.build_system_files}

        self.assertEqual(project.build_cmd, "make")
        self.assertEqual(project.test_cmd, "make test")
        self.assertEqual(project.compile_database_path, compile_db.resolve())
        self.assertEqual(source_names, {"main.c", "lib.cc", "lib.hpp"})
        self.assertEqual(build_names, {"Makefile", "CMakeLists.txt", "compile_commands.json"})

    def test_read_write_backup_restore_and_diff_without_git(self) -> None:
        self.write("src/main.c", "int main(void) { return 0; }\n")
        project = ProjectLoader(self.tmpdir).load()

        backup = project.backup()
        project.write_file("src/main.c", "int main(void) { return 1; }\n")

        self.assertEqual(backup.kind, "directory")
        self.assertEqual(project.read_file("src/main.c"), "int main(void) { return 1; }\n")
        self.assertIn("+int main(void) { return 1; }", project.git_diff())

        project.restore()

        self.assertEqual(project.read_file("src/main.c"), "int main(void) { return 0; }\n")

    def test_apply_patch_without_git(self) -> None:
        self.write("src/main.c", "int main(void) { return 0; }\n")
        project = ProjectLoader(self.tmpdir).load()

        project.apply_patch(
            """--- a/src/main.c
+++ b/src/main.c
@@ -1 +1 @@
-int main(void) { return 0; }
+int main(void) { return 2; }
"""
        )

        self.assertEqual(project.read_file("src/main.c"), "int main(void) { return 2; }\n")

    def test_check_patch_without_git_does_not_mutate(self) -> None:
        self.write("src/main.c", "int main(void) { return 0; }\n")
        project = ProjectLoader(self.tmpdir).load()

        ok, diagnostics = project.check_patch(
            """--- a/src/main.c
+++ b/src/main.c
@@ -1 +1 @@
-int main(void) { return 0; }
+int main(void) { return 2; }
"""
        )

        self.assertTrue(ok, diagnostics)
        self.assertEqual(project.read_file("src/main.c"), "int main(void) { return 0; }\n")

    def test_check_and_apply_patch_repair_duplicate_project_prefix(self) -> None:
        project_root = self.tmpdir / "wolfssl"
        project_root.mkdir()
        path = project_root / "tests/api/test_ossl_rand.c"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("int f(void) { return 0; }\n", encoding="utf-8")
        project = ProjectLoader(project_root).load()
        diff = """diff --git a/wolfssl/tests/api/test_ossl_rand.c b/wolfssl/tests/api/test_ossl_rand.c
--- a/wolfssl/tests/api/test_ossl_rand.c
+++ b/wolfssl/tests/api/test_ossl_rand.c
@@ -1 +1 @@
-int f(void) { return 0; }
+int f(void) { return 1; }
"""

        ok, diagnostics = project.check_patch(diff)

        self.assertTrue(ok, diagnostics)
        self.assertIn("patch path repair attempted", diagnostics)
        project.apply_patch(diff)
        self.assertEqual(project.read_file("tests/api/test_ossl_rand.c"), "int f(void) { return 1; }\n")

    def test_check_and_apply_patch_repair_unprefixed_paths_for_patch_command(self) -> None:
        self.write("src/main.c", "int main(void) { return 0; }\n")
        project = ProjectLoader(self.tmpdir).load()
        diff = """--- src/main.c
+++ src/main.c
@@ -1 +1 @@
-int main(void) { return 0; }
+int main(void) { return 3; }
"""

        ok, diagnostics = project.check_patch(diff)

        self.assertTrue(ok, diagnostics)
        project.apply_patch(diff)
        self.assertEqual(project.read_file("src/main.c"), "int main(void) { return 3; }\n")

    def test_file_operations_reject_paths_outside_project(self) -> None:
        project = ProjectLoader(self.tmpdir).load()

        with self.assertRaises(ValueError):
            project.read_file("../outside.c")
