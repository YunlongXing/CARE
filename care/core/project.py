"""Project loading and workspace operations for C/C++ targets."""

from __future__ import annotations

import difflib
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

from care.core.models import ProjectContext, SourceFile
from care.core.patch_utils import repair_unified_diff_paths

SOURCE_EXTENSIONS = {".c", ".cc", ".cpp", ".h", ".hpp"}
BUILD_SYSTEM_FILENAMES = {"Makefile", "CMakeLists.txt", "compile_commands.json"}
EXCLUDED_DIRS = {".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache"}


@dataclass
class ProjectBackup:
    """Record enough state to restore a project workspace."""

    kind: str
    backup_dir: Optional[Path] = None
    git_ref: Optional[str] = None


@dataclass
class CareProject:
    """Loaded C/C++ project plus safe file and patch operations."""

    root: Path
    build_cmd: Optional[str] = None
    test_cmd: Optional[str] = None
    compile_database_path: Optional[Path] = None
    source_files: list[Path] = field(default_factory=list)
    build_system_files: list[Path] = field(default_factory=list)
    _backup: Optional[ProjectBackup] = None
    _git_root: Optional[Path] = None

    def __post_init__(self) -> None:
        self.root = self.root.expanduser().resolve()
        if self.compile_database_path is not None:
            self.compile_database_path = self._resolve_project_path(self.compile_database_path)
        self.source_files = sorted(path.resolve() for path in self.source_files)
        self.build_system_files = sorted(path.resolve() for path in self.build_system_files)
        self._git_root = self._detect_git_root()

    @property
    def git_available(self) -> bool:
        return self._git_root is not None

    def list_source_files(self) -> list[str]:
        """Return detected C/C++ source and header files as absolute paths."""

        return [str(path) for path in self.source_files]

    def read_file(self, path: Union[str, Path]) -> str:
        """Read a project file as UTF-8 text."""

        resolved = self._resolve_project_path(path)
        return resolved.read_text(encoding="utf-8")

    def write_file(self, path: Union[str, Path], content: str) -> None:
        """Write UTF-8 text to a project file, creating parent directories."""

        resolved = self._resolve_project_path(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        self._refresh_detected_files()

    def backup(self) -> ProjectBackup:
        """Create a restore point using git when possible, else a temp copy."""

        if self.git_available and self._has_git_head():
            ref = self._git_stdout(["stash", "create", "care-backup"]).strip()
            if not ref:
                ref = self._git_stdout(["rev-parse", "HEAD"]).strip()
            self._backup = ProjectBackup(kind="git", git_ref=ref)
            return self._backup

        backup_root = Path(tempfile.mkdtemp(prefix="care-project-backup-"))
        backup_dir = backup_root / self.root.name
        shutil.copytree(self.root, backup_dir, ignore=shutil.ignore_patterns(*EXCLUDED_DIRS))
        self._backup = ProjectBackup(kind="directory", backup_dir=backup_dir)
        return self._backup

    def restore(self) -> None:
        """Restore the most recent backup created by ``backup``."""

        if self._backup is None:
            raise RuntimeError("no project backup has been created")

        if self._backup.kind == "git" and self._backup.git_ref:
            patch = self._git_stdout(["diff", "--binary", self._backup.git_ref])
            if patch.strip():
                self._run_git(["apply", "-R", "--whitespace=nowarn"], input_text=patch)
            self._refresh_detected_files()
            return

        if self._backup.kind == "directory" and self._backup.backup_dir:
            self._restore_directory_backup(self._backup.backup_dir)
            self._refresh_detected_files()
            return

        raise RuntimeError("project backup is incomplete")

    def apply_patch(self, diff: str) -> None:
        """Apply a unified diff to the project workspace."""

        if not diff.strip():
            return

        diff_to_apply, _ = self.repair_patch_paths(diff)
        if self.git_available:
            self._run_git(["apply", "--whitespace=nowarn"], input_text=diff_to_apply)
        else:
            self._run(["patch", "-p1"], input_text=diff_to_apply)
        self._refresh_detected_files()

    def check_patch(self, diff: str) -> tuple[bool, str]:
        """Check whether a unified diff can apply without mutating the project."""

        if not diff.strip():
            return False, "empty patch"

        ok, output = self._check_patch_once(diff)
        if ok:
            return True, output

        repaired_diff, repair_notes = self.repair_patch_paths(diff)
        if repaired_diff == diff:
            return False, output

        repaired_ok, repaired_output = self._check_patch_once(repaired_diff)
        repair_log = "patch path repair attempted"
        if repair_notes:
            repair_log += ":\n" + "\n".join(f"- {note}" for note in repair_notes)
        diagnostics = "\n".join(
            part for part in [output, repair_log, repaired_output] if part.strip()
        ).strip()
        return repaired_ok, diagnostics

    def repair_patch_paths(self, diff: str) -> tuple[str, list[str]]:
        """Return a patch with file paths normalized to this project root."""

        return repair_unified_diff_paths(diff, self.root, self.source_files)

    def _check_patch_once(self, diff: str) -> tuple[bool, str]:
        if self.git_available:
            completed = self._run_git(
                ["apply", "--check", "--whitespace=nowarn"],
                input_text=diff,
                check=False,
            )
        else:
            completed = self._run(
                ["patch", "--dry-run", "-p1"],
                input_text=diff,
                check=False,
            )
        output = "\n".join(part for part in [completed.stdout, completed.stderr] if part.strip())
        return completed.returncode == 0, output.strip()

    def git_diff(self) -> str:
        """Return a git-style diff for the current project state."""

        if self.git_available:
            return self._git_stdout(["diff", "--no-ext-diff", "--binary"])

        if self._backup and self._backup.backup_dir:
            return self._directory_diff(self._backup.backup_dir, self.root)
        return ""

    def to_context(self) -> ProjectContext:
        """Convert project metadata into the analysis context model."""

        return ProjectContext(
            root=str(self.root),
            source_files=[
                SourceFile(path=str(path), language=self._language_for(path) or "unknown")
                for path in self.source_files
            ],
        )

    def _refresh_detected_files(self) -> None:
        self.source_files = ProjectLoader.discover_source_files(self.root)
        self.build_system_files = ProjectLoader.discover_build_system_files(self.root)
        if self.compile_database_path is None:
            self.compile_database_path = ProjectLoader.find_compile_database(self.build_system_files)

    def _resolve_project_path(self, path: Union[str, Path]) -> Path:
        raw_path = Path(path).expanduser()
        resolved = raw_path.resolve() if raw_path.is_absolute() else (self.root / raw_path).resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"path is outside project root: {path}") from exc
        return resolved

    def _detect_git_root(self) -> Optional[Path]:
        result = self._run(["git", "rev-parse", "--show-toplevel"], check=False)
        if result.returncode != 0:
            return None
        git_root = Path(result.stdout.strip()).resolve()
        if git_root != self.root:
            return None
        return git_root

    def _has_git_head(self) -> bool:
        return self._run_git(["rev-parse", "--verify", "HEAD"], check=False).returncode == 0

    def _git_stdout(self, args: list[str]) -> str:
        return self._run_git(args).stdout

    def _run_git(
        self,
        args: list[str],
        input_text: Optional[str] = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return self._run(["git", *args], input_text=input_text, check=check)

    def _run(
        self,
        args: list[str],
        input_text: Optional[str] = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        try:
            completed = subprocess.run(
                args,
                cwd=self.root,
                input=input_text,
                text=True,
                capture_output=True,
                check=False,
            )
        except FileNotFoundError as exc:
            if check:
                raise RuntimeError(f"command not found: {args[0]}") from exc
            return subprocess.CompletedProcess(
                args=args,
                returncode=127,
                stdout="",
                stderr=f"command not found: {args[0]}",
            )
        if check and completed.returncode != 0:
            command = " ".join(args)
            raise RuntimeError(
                f"command failed ({completed.returncode}): {command}\n{completed.stderr.strip()}"
            )
        return completed

    def _restore_directory_backup(self, backup_dir: Path) -> None:
        for child in self.root.iterdir():
            if child.name in EXCLUDED_DIRS:
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

        for child in backup_dir.iterdir():
            destination = self.root / child.name
            if child.is_dir():
                shutil.copytree(child, destination)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(child, destination)

    @staticmethod
    def _directory_diff(before: Path, after: Path) -> str:
        before_files = _text_file_index(before)
        after_files = _text_file_index(after)
        diff_parts: list[str] = []
        for relative in sorted(set(before_files) | set(after_files)):
            before_lines = before_files.get(relative, [])
            after_lines = after_files.get(relative, [])
            if before_lines == after_lines:
                continue
            diff_parts.extend(
                difflib.unified_diff(
                    before_lines,
                    after_lines,
                    fromfile=f"a/{relative}",
                    tofile=f"b/{relative}",
                    lineterm="",
                )
            )
            diff_parts.append("")
        return "\n".join(diff_parts)

    @staticmethod
    def _language_for(path: Path) -> Optional[str]:
        suffix = path.suffix.lower()
        if suffix in {".c", ".h"}:
            return "c"
        if suffix in {".cc", ".cpp", ".hpp"}:
            return "cpp"
        return None


class ProjectLoader:
    """Load project metadata and create a ``CareProject`` handle."""

    def __init__(
        self,
        project_path: Optional[Union[str, Path]] = None,
        build_cmd: Optional[str] = None,
        test_cmd: Optional[str] = None,
        compile_database_path: Optional[Union[str, Path]] = None,
    ) -> None:
        self.project_path = Path(project_path).expanduser() if project_path is not None else None
        self.build_cmd = build_cmd
        self.test_cmd = test_cmd
        self.compile_database_path = (
            Path(compile_database_path).expanduser()
            if compile_database_path is not None
            else None
        )

    def load(
        self,
        project_path: Optional[Union[str, Path]] = None,
        build_cmd: Optional[str] = None,
        test_cmd: Optional[str] = None,
        compile_database_path: Optional[Union[str, Path]] = None,
    ) -> CareProject:
        root = self._select_project_path(project_path)
        root = root.expanduser().resolve()
        if not root.exists():
            raise FileNotFoundError(f"project path does not exist: {root}")
        if not root.is_dir():
            raise NotADirectoryError(f"project path is not a directory: {root}")

        build_files = self.discover_build_system_files(root)
        selected_compile_db = self._select_compile_database(compile_database_path, build_files, root)
        return CareProject(
            root=root,
            build_cmd=build_cmd if build_cmd is not None else self.build_cmd,
            test_cmd=test_cmd if test_cmd is not None else self.test_cmd,
            compile_database_path=selected_compile_db,
            source_files=self.discover_source_files(root),
            build_system_files=build_files,
        )

    @staticmethod
    def discover_source_files(root: Path) -> list[Path]:
        return sorted(
            path.resolve()
            for path in _walk_project_files(root)
            if path.suffix.lower() in SOURCE_EXTENSIONS
        )

    @staticmethod
    def discover_build_system_files(root: Path) -> list[Path]:
        return sorted(
            path.resolve()
            for path in _walk_project_files(root)
            if path.name in BUILD_SYSTEM_FILENAMES
        )

    @staticmethod
    def find_compile_database(build_system_files: list[Path]) -> Optional[Path]:
        for path in build_system_files:
            if path.name == "compile_commands.json":
                return path
        return None

    def _select_project_path(self, project_path: Optional[Union[str, Path]]) -> Path:
        if project_path is not None:
            return Path(project_path)
        if self.project_path is not None:
            return self.project_path
        raise ValueError("project path is required")

    def _select_compile_database(
        self,
        compile_database_path: Optional[Union[str, Path]],
        build_system_files: list[Path],
        root: Path,
    ) -> Optional[Path]:
        selected = compile_database_path if compile_database_path is not None else self.compile_database_path
        if selected is None:
            return self.find_compile_database(build_system_files)

        path = Path(selected).expanduser()
        resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"compile database path is outside project root: {selected}") from exc
        return resolved


def _walk_project_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in EXCLUDED_DIRS)
        current = Path(current_root)
        for filename in sorted(filenames):
            files.append(current / filename)
    return files


def _text_file_index(root: Path) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for path in _walk_project_files(root):
        relative = path.relative_to(root).as_posix()
        try:
            index[relative] = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        except OSError:
            continue
    return index
