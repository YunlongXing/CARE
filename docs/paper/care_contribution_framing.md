# CARE Paper Framing

## Working Title

CARE: Context-Aware and Security-Preserving LLM Refactoring for C/C++ Resource Lifecycles

## Thesis

Large language models can generate plausible C/C++ refactorings, but direct prompting is brittle in security-sensitive code because small edits can silently change error handling, resource ownership, validation checks, or cleanup ordering. CARE improves this setting by coupling LLM patch generation with program context construction, resource-aware opportunity detection, and conservative multi-stage validation.

## Scope

The paper should frame CARE as a research prototype for security-aware C/C++ resource-lifecycle refactoring, not as a general-purpose refactoring engine for every smell. The strongest empirical evidence is currently for critical `resource_imbalance` findings over the OSS50 benchmark.

## Contributions

1. A context-aware LLM refactoring architecture for C/C++ that combines function extraction, lightweight control/data/resource context, security pattern knowledge, opportunity detection, patch generation, and validator-guided repair.
2. A security-aware validation stack that checks patch applicability, build/test hooks when available, resource consistency, semantic preservation, and security-regression signals before accepting generated patches.
3. An empirical study over 50 real open-source C/C++ projects covering 25,162 source files, 266,602 functions, and 142,104 detected opportunities.
4. A controlled comparison on 487 critical resource-lifecycle findings showing that CARE substantially improves patch applicability and validation pass rate over direct LLM-only patching.
5. An ablation study showing that resource validation is essential: disabling it greatly increases apparent pass rate but leads to high LLM-reviewed false acceptance.
6. A reproducible artifact containing the pipeline, benchmark scripts, generated patches, validation logs, LLM-assisted review outputs, and paper-ready tables.

## Core Claims Supported by Current Results

- CARE generates more applicable patches than direct LLM-only patching on the same critical findings.
- CARE produces more fully validated patches than direct LLM-only patching.
- Resource consistency checking is the dominant safety gate for this benchmark; removing it admits many patches that LLM-assisted review judges unsafe or still resource-inconsistent.
- Most accepted CARE patches are small local edits that preserve the intended resource lifecycle according to the validator and LLM-assisted review.

## Claims to Avoid or Qualify

- Do not claim fully automated correctness proof. CARE performs conservative validation, not formal equivalence checking.
- Do not call the LLM-assisted review "human review." It is a simulated expert audit and should be presented as supplementary evidence unless replaced by independent human annotation.
- Do not claim broad superiority for all C/C++ refactoring categories. The strongest evidence is for critical resource-lifecycle findings.
- Do not claim precise API cost/token accounting for the existing run; the prototype recorded call counts and runtimes, not provider billing telemetry.

## Suggested Abstract Skeleton

C/C++ refactoring is security-sensitive: edits that simplify code can accidentally change cleanup paths, error propagation, or validation semantics. We present CARE, a context-aware automated refactoring engine that detects security-relevant refactoring opportunities, constructs local program context, uses an LLM to propose minimal patches, and accepts patches only after conservative correctness and security validation. On 50 open-source C/C++ projects, CARE analyzed 25,162 source files and 266,602 functions, identifying 142,104 opportunities. On 487 critical resource-lifecycle findings, CARE generated applicable patches at a substantially higher rate than a direct LLM-only baseline and produced 31 fully validated patches versus 5 for LLM-only. Validator ablations show that removing resource consistency checking increases apparent pass rate but admits many unsafe false acceptances. These results suggest that LLM-based refactoring for systems code benefits from explicit program context and security-aware validation rather than direct patch prompting alone.

## Limitations to State Explicitly

- Many OSS50 projects were evaluated without project-specific build/test commands, so compile/test gates were skipped unless commands were available.
- The parser and CFG are intentionally lightweight in the current prototype, although clang/tree-sitter integration paths exist.
- The detector precision and patch correctness review are LLM-assisted unless replaced by independent human review.
- Current experiments focus on critical resource-lifecycle findings; high-severity and other detector categories require additional evaluation.
