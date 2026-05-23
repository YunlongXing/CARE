# CARE Artifacts

This directory contains the lightweight public artifacts for the CARE paper
draft. The full benchmark source trees and large raw JSON reports are excluded
from the repository because they contain third-party source checkouts and
multi-gigabyte intermediate outputs.

## Contents

- `figures/`: paper-ready architecture and evaluation figures.
- `oss50/scan-summary.md`: full OSS50 scan summary.
- `oss50/critical-validation-summary.md`: real LLM patch and validation summary
  for the 487 critical resource-lifecycle findings.
- `oss50/tables/`: paper tables in Markdown, CSV, JSON, and LaTeX form.
- `oss50/selected-patches/`: 31 selected patches that passed CARE validation.
- `openssl/top5-realval/`: small OpenSSL real-validation report and patch
  candidates from the earlier focused experiment.

## Reproducibility Notes

The OSS50 raw project checkouts are not vendored. To reproduce the benchmark,
use `scripts/oss50_manifest.json` with the benchmark runners in `scripts/`.
Real LLM validation requires an OpenAI-compatible API key supplied through
environment variables; no key is included in this repository.
