from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.mine_c_cpp_maintenance_commits import main


class MineMaintenanceCommitsTests(unittest.TestCase):
    def test_mines_c_refactoring_commit_from_complete_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "demo"
            repository.mkdir()
            self.git(repository, "init")
            self.git(repository, "config", "user.email", "care@example.test")
            self.git(repository, "config", "user.name", "CARE Test")
            (repository / "main.c").write_text("int value(void) { return 1; }\n", encoding="utf-8")
            self.git(repository, "add", "main.c")
            self.git(repository, "commit", "-m", "initial implementation")
            (repository / "main.c").write_text(
                "/* refactored implementation */\nint value(void) { return 1; }\n",
                encoding="utf-8",
            )
            self.git(repository, "add", "main.c")
            self.git(repository, "commit", "-m", "refactor cleanup path")

            output = root / "mined"
            exit_code = main(
                [
                    "--project",
                    f"demo={repository}",
                    "--output-dir",
                    str(output),
                ]
            )
            self.assertEqual(exit_code, 0)
            payload = json.loads((output / "maintenance_commit_candidates.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["candidate_count"], 1)
            candidate = payload["candidates"][0]
            self.assertEqual(candidate["candidate_category"], "candidate_refactoring")
            self.assertTrue((output / candidate["patch_file"]).is_file())

    @staticmethod
    def git(repository: Path, *arguments: str) -> None:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=repository,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise AssertionError(completed.stderr)


if __name__ == "__main__":
    unittest.main()
