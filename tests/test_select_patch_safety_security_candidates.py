from __future__ import annotations

from pathlib import Path

from scripts.select_patch_safety_security_candidates import select_candidates


def test_selects_each_security_pattern_with_project_cap(tmp_path: Path) -> None:
    patches = tmp_path / "patches"
    patches.mkdir()
    content = {
        "null": "+ if (!ptr)\n+    return -1;\n",
        "bounds": "+ if (idx >= size)\n+    return -1;\n",
        "status": "+ if (status != 0)\n+    return -1;\n",
        "copy": "+ snprintf(buf, size, \"%s\", src);\n",
    }
    candidates = []
    for index, (name, diff) in enumerate(content.items(), start=1):
        path = patches / f"{name}.diff"
        path.write_text(diff, encoding="utf-8")
        candidates.append({"id": name, "project_id": f"p{index}", "patch_file": f"patches/{name}.diff"})
    selected, labels = select_candidates(
        candidates,
        tmp_path,
        per_class=1,
        max_per_project=1,
        seed=7,
    )
    assert {candidate["id"] for candidate in selected} == set(content)
    assert labels["null"] == ["null_guard"]
    assert labels["bounds"] == ["bounds_guard"]
