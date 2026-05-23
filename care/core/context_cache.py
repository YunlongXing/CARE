"""Project context cache for expensive analysis results."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from care.analysis.context_graph import ContextGraph
from care.config import CareConfig


class ProjectContextCache:
    """Persist and reload context graphs keyed by source tree fingerprints."""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir

    @classmethod
    def for_project(cls, project: object, config: CareConfig) -> "ProjectContextCache":
        if config.context_cache_dir is not None:
            cache_dir = config.context_cache_dir
        else:
            cache_dir = Path(getattr(project, "root", config.project_path)) / ".care" / "cache"
        return cls(cache_dir.expanduser().resolve())

    def cache_path(self, key: str) -> Path:
        return self.cache_dir / f"context-{key}.json"

    def key_for(self, project: object, config: CareConfig) -> str:
        root = Path(getattr(project, "root", config.project_path)).resolve()
        records: list[dict[str, object]] = []
        for source in sorted(getattr(project, "source_files", []), key=lambda path: str(path)):
            path = Path(source)
            try:
                stat = path.stat()
                relative = str(path.resolve().relative_to(root))
                records.append(
                    {
                        "path": relative,
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                    }
                )
            except OSError:
                continue
        compile_db = getattr(project, "compile_database_path", None) or config.compile_database_path
        compile_db_record = None
        if compile_db is not None:
            compile_db_path = Path(compile_db)
            try:
                stat = compile_db_path.stat()
                compile_db_record = {
                    "path": str(compile_db_path.resolve()),
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                }
            except OSError:
                compile_db_record = {"path": str(compile_db_path)}
        payload = {
            "root": str(root),
            "analysis_backend": config.analysis_backend,
            "cfg_backend": config.cfg_backend,
            "compile_database": compile_db_record,
            "sources": records,
        }
        encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:24]

    def load(self, project: object, config: CareConfig) -> Optional[ContextGraph]:
        key = self.key_for(project, config)
        path = self.cache_path(key)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ContextGraph.from_dict(payload["context_graph"])

    def save(self, project: object, config: CareConfig, context_graph: ContextGraph) -> Path:
        key = self.key_for(project, config)
        path = self.cache_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "cache_key": key,
                    "project": str(getattr(project, "root", config.project_path)),
                    "context_graph": context_graph.to_dict(),
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return path
