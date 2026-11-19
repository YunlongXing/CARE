# CARE Frozen Handoff

This directory freezes the CARE system and the patch-safety experiments for a
student handoff. The source snapshot is identified in `FREEZE.json`. The full
experiment inputs and outputs live in `data/` and are intentionally excluded
from Git because they contain archived third-party source trees.

## Contents

- `data/mined_v2/`: mined developer-maintenance candidates and patches.
- `data/review_v2/` and `data/agent_review_v2/`: main-corpus curation inputs
  and two-agent outputs.
- `data/real_v3/`: 33 materialized developer patches at archived base revisions.
- `data/mutations_v5/`: 69 explicit unsafe variants derived from those patches.
- `data/results_v6/`: the frozen primary validation run.
- `data/calibration_results_v2/`: small validator-gate calibration run.
- `data/review_security_v1/`, `data/agent_review_security_v1/`,
  `data/real_security_v1/`, `data/mutations_security_v1/`, and
  `data/results_security_v1/`: the security-focused extension.

`data.sha256` records a SHA-256 checksum for every frozen file. The data can be
used without an API key. Re-running the supplied validation benchmark uses the
archived source copies in `mutations_v5/` and does not need the original Git
histories.

## Reproduction

Use Python 3.9 or later. The frozen run was checked with the project test suite
and a Python 3.15 virtual environment.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q

# Recompute the primary summary from the frozen per-case results.
.venv/bin/python scripts/summarize_patch_safety_ablation.py \
  --results-dir artifacts/frozen/CARE-2026-11-19/data/results_v6 \
  --output-dir /tmp/care-freeze-summary
```

To re-run validation from the frozen corpus, use a writable output location.

```bash
.venv/bin/python scripts/run_patch_safety_benchmark.py \
  --manifest artifacts/frozen/CARE-2026-11-19/data/mutations_v5/manifest.json \
  --output-dir /tmp/care-freeze-rerun \
  --systems build_test build_test_static care --workers 4
```

The archived projects rely on the build profiles in `benchmarks/build_profiles.json`.
They were materialized with these profiles already attached to each manifest
case.

## Interpretation

The primary result is a patch-safety result, not an end-to-end LLM generation
claim. It compares build/test, build/test plus static analysis, and CARE on
developer patches plus deterministic unsafe variants. The calibration suite
checks whether resource, semantic, and security gates can each reject an
operator designed for that gate. See `RESULTS.md` for the exact reported
numbers and limitations.
