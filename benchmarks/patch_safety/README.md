# CARE Patch-Safety Benchmark

This benchmark measures whether a patch-validation configuration accepts
archived C/C++ maintenance changes and rejects explicit unsafe variants. It is
separate from CARE's opportunity detector. Safe cases use a developer patch at
its recorded base revision. Unsafe labels come from documented mutation
operators.

## Case Types

- `semantic_preserving_refactoring`: an archived developer maintenance change
  selected by strict agent consensus and verified to apply to its base revision.
- `unsafe_mutant`: a deliberately unsafe variant that represents a validation
  failure mode, such as removing a null check, omitting cleanup, changing an
  error code, adding a double release, or introducing an unsafe API.

The checked-in cases are small executable seed cases for regression testing of
the benchmark runner and the validator gates. They are not reported as the
paper's real-world study.
The paper benchmark is expanded with archived C/C++ project changes imported
with `scripts/mine_c_cpp_maintenance_commits.py`.

## Running

```bash
python scripts/run_patch_safety_benchmark.py \
  --manifest benchmarks/patch_safety/manifest.json \
  --output-dir benchmarks/patch_safety/results
```

The output includes per-case stage results and summary metrics for unsafe-change
rejection and safe-change acceptance. No LLM call is made.

The seed suite includes a gate-calibration subset. It weakens null, bounds,
allocation-failure, and status-error guards while retaining the original error
returns. These variants check that the security gate can reject weakened guards
without relying on a changed return form. Cleanup removal and double-release
cases similarly calibrate the resource gate. Calibration results are reported
separately from the archived-project benchmark.

Raw mined histories and experiment outputs are local experiment data and are
ignored by Git. The case schema and seed definitions are versioned with the
repository.

To construct a real-world candidate pool, clone complete histories, mine
candidate changes, and prepare the agent input materials.

```bash
python scripts/mine_c_cpp_maintenance_commits.py \
  --project cjson=benchmarks/patch_safety/history/cjson \
  --project jansson=benchmarks/patch_safety/history/jansson \
  --output-dir benchmarks/patch_safety/mined

python scripts/prepare_patch_safety_human_review.py \
  --candidates benchmarks/patch_safety/mined/maintenance_commit_candidates.json \
  --output-dir benchmarks/patch_safety/review
```

After strict agent consensus, materialize eligible refactorings. This archives
the recorded base revision and verifies that the developer patch applies to it.
It does not alter the archived source tree.

```bash
python scripts/materialize_patch_safety_cases.py \
  --review-manifest benchmarks/patch_safety/review/review_manifest.json \
  --agent-consensus benchmarks/patch_safety/agent_review/consensus.csv \
  --output-dir benchmarks/patch_safety/real
```

The resulting `real/manifest.json` can be passed to the benchmark runner. Add
project build and test commands through `--project-config` before reporting
build/test results. The materialized corpus remains ignored until its review
provenance and licensing are ready for artifact release.

## Agent Curation and Mutations

The optional curation stage uses two separately configured agents. Their
agreement selects conservative candidates. It does not establish correctness.
The command below runs a 25-item pilot with a request budget cap; omit
`--execute` to inspect the planned run without model calls.

```bash
python scripts/run_patch_safety_agent_review.py \
  --review-manifest benchmarks/patch_safety/review/review_manifest.json \
  --output-dir benchmarks/patch_safety/agent_review \
  --limit 25 --budget-usd 2 --execute
```

After materializing eligible safe cases, generate unsafe variants with explicit
operators. Each mutant is a combined diff against the archived base, so the
benchmark runner can evaluate safe acceptance and unsafe rejection on the same
project version.

```bash
python scripts/generate_patch_safety_mutants.py \
  --manifest benchmarks/patch_safety/real/manifest.json \
  --output-dir benchmarks/patch_safety/mutations \
  --max-mutants-per-case 4
```
