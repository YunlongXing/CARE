# Benchmark Data Policy

The repository keeps only lightweight benchmark configuration in this directory.
Downloaded third-party source trees, raw CARE reports, raw validation queues,
and run logs are intentionally ignored by Git.

Curated paper artifacts are published under `artifacts/`.

To recreate local benchmark data, use the scripts in `scripts/`, especially
`scripts/run_oss50_benchmark.py`, `scripts/run_oss50_llm_validation.py`, and
`scripts/run_oss50_parallel_validation.py`.
