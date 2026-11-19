# Student Starting Point

This freeze establishes the baseline. Keep the frozen corpus and reported
tables unchanged. Put every new experiment in a new directory and state the
commit, manifest, model, prompt template, seed, and cost record that produced
it.

## First: reproduce the baseline

1. Verify `data.sha256` and `archive.sha256`.
2. Run `pytest` and recompute the summary from `data/results_v6/`.
3. Re-run a small stratified subset of `data/mutations_v5/` into a new output
   directory. Compare its per-stage decisions with the frozen run.

## Highest-value system work

1. **Reduce false rejects.** Analyze the eleven safe patches rejected by CARE.
   Add narrowly scoped normalization or path reasoning only when a regression
   test and a calibration case show that it does not admit an unsafe patch.
2. **Grow the real security corpus.** Mine and independently curate at least
   80 security-relevant developer patches across more projects. Keep strict
   agreement and archive every source revision before generating variants.
3. **Strengthen security evaluation.** Add real patches involving bounds,
   status propagation, and allocation guards. Report each class separately;
   do not infer broad security performance from the calibration suite.
4. **Evaluate LLM generation separately.** Compare direct prompting,
   context-only prompting, and CARE's planning plus validation using the same
   model, token budget, and candidate budget. Repeat each condition with fixed
   seeds when the provider supports them, then report variance and cost.

## Reporting rule

Use the primary corpus to support the claim that CARE reduces false acceptance
of unsafe variants. Use the calibration suite only to show that individual
validator gates are exercised. Do not combine these results into an unsupported
claim about real-world security vulnerability repair.
