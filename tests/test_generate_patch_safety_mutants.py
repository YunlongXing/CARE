from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.generate_patch_safety_mutants import main
from scripts.generate_patch_safety_mutants import mutate_workspace


class GeneratePatchSafetyMutantsTests(unittest.TestCase):
    def test_generates_labeled_mutants_from_a_safe_patch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "main.c").write_text(
                "#include <stdlib.h>\nint f(char *p) {\n    if (!p)\n        return -1;\n    free(p);\n    return 0;\n}\n",
                encoding="utf-8",
            )
            patch = root / "safe.diff"
            patch.write_text(
                "--- a/main.c\n+++ b/main.c\n@@ -1,7 +1,8 @@\n #include <stdlib.h>\n int f(char *p) {\n     if (!p)\n         return -1;\n+    /* preserve ownership */\n     free(p);\n     return 0;\n }\n",
                encoding="utf-8",
            )
            manifest = {
                "cases": [{
                    "id": "demo:refactor", "ground_truth": "safe", "source_dir": "source",
                    "candidate_diff": "safe.diff", "build_cmd": None, "test_cmd": None,
                }]
            }
            source_manifest = root / "safe_manifest.json"
            source_manifest.write_text(json.dumps(manifest), encoding="utf-8")
            output = root / "mutants"
            self.assertEqual(
                main([
                    "--manifest", str(source_manifest), "--output-dir", str(output),
                    "--operators", "remove_null_guard", "duplicate_release", "--max-mutants-per-case", "2",
                ]),
                0,
            )
            generated = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(generated["safe_case_count"], 1)
            self.assertEqual(generated["unsafe_mutant_count"], 2)
            for case in generated["cases"]:
                self.assertTrue((output / case["candidate_diff"]).is_file())

    def test_supports_allocation_bounds_and_api_regressions(self) -> None:
        source = (
            "#include <stdio.h>\n#include <stdlib.h>\n"
            "int f(int idx, int size, char *buf) {\n"
            "    char *p = malloc(8);\n    if (!p)\n        return -1;\n"
            "    if (idx >= size)\n        return -2;\n"
            "    snprintf(buf, 8, \"%d\", idx);\n    free(p);\n    return 0;\n}\n"
        )
        from scripts.generate_patch_safety_mutants import (
            mutate_disable_allocation_guard,
            mutate_disable_bounds_guard,
            mutate_disable_null_guard,
            mutate_disable_status_guard,
            mutate_remove_allocation_guard,
            mutate_remove_bounds_guard,
            mutate_unsafe_api,
        )

        self.assertIsNotNone(mutate_remove_allocation_guard(source))
        self.assertIsNotNone(mutate_remove_bounds_guard(source))
        self.assertIsNotNone(mutate_unsafe_api(source))
        for mutation in (
            mutate_disable_null_guard(source),
            mutate_disable_bounds_guard(source),
            mutate_disable_allocation_guard(source),
            mutate_disable_status_guard("int f(int status) {\n    if (status != 0)\n        return -1;\n    return 0;\n}\n"),
        ):
            self.assertIsNotNone(mutation)
            changed, _ = mutation
            self.assertIn("if (0)", changed)
            self.assertIn("return -1;", changed)

    def test_restricts_mutation_to_developer_changed_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "changed.c").write_text("int f(void) { return 0; }\n", encoding="utf-8")
            (workspace / "other.c").write_text(
                "#include <stdlib.h>\nint g(char *p) { free(p); return 0; }\n",
                encoding="utf-8",
            )
            self.assertIsNone(
                mutate_workspace(workspace, "remove_cleanup", changed_files=["changed.c"])
            )


if __name__ == "__main__":
    unittest.main()
