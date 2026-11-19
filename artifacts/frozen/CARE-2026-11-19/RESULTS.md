# Frozen Results

## Primary Patch-Safety Corpus

The primary corpus contains 33 behavior-preserving developer maintenance
patches and 69 deterministic unsafe variants. It covers cJSON, libpng, lz4,
and zlib. Every case has an archived source tree, a patch, build metadata, and
per-stage validation logs.

| System | Safe accepted | Unsafe rejected | False accepts | False rejects |
| --- | ---: | ---: | ---: | ---: |
| Build/test | 28/33 (84.8%) | 8/69 (11.6%) | 61 | 5 |
| Build/test + static | 28/33 (84.8%) | 8/69 (11.6%) | 61 | 5 |
| CARE | 22/33 (66.7%) | 53/69 (76.8%) | 16 | 11 |

CARE removes 45 of the 61 build/test false accepts, a 73.8% reduction. It also
rejects six additional safe developer patches. This is the central trade-off:
the validator is useful as a conservative safety gate, but its current semantic
checker remains stricter than desired.

## Primary Validator Ablation

| Variant | Unsafe rejected | False accepts | Additional false accepts |
| --- | ---: | ---: | ---: |
| Full CARE | 53/69 (76.8%) | 16 | 0 |
| Without resource checker | 40/69 (58.0%) | 29 | 13 |
| Without semantic checker | 23/69 (33.3%) | 46 | 30 |
| Without security checker | 53/69 (76.8%) | 16 | 0 |

The resource and semantic gates have independent effect in the primary corpus.
The selected developer files contain too few suitable security-guard targets to
isolate the security gate there. The security claim is therefore supported by
the separate calibration result below, not by the primary ablation alone.

## Validator-Gate Calibration

The executable calibration suite has two safe refactorings and nine unsafe
variants. It includes cleanup removal, double release, return-code changes,
unsafe API replacement, and weakened null, bounds, allocation, and status
guards. CARE accepts both safe patches and rejects all nine unsafe variants.

| Disabled gate | Unsafe rejected | Additional false accepts |
| --- | ---: | ---: |
| None | 9/9 | 0 |
| Resource | 7/9 | 2 |
| Semantic | 8/9 | 1 |
| Security | 6/9 | 3 |

This is a targeted calibration result. It confirms that each gate fires on its
intended regression class. It is not a substitute for a larger real-project
security corpus.

## Security-Focused Extension

Two agents independently reviewed 47 security-focused maintenance candidates.
Three strict-consensus patches from cJSON and lz4 were materialized, producing
five aligned unsafe variants. CARE rejected all five variants and accepted one
of the three developer patches. This extension is preserved for error analysis
and future corpus growth. Its sample size is too small for a headline result.

## Frozen Claims

The frozen data supports these claims:

1. Build/test acceptance is insufficient for the deterministic unsafe variants
   in the primary corpus.
2. CARE substantially reduces false acceptance through resource and semantic
   validation.
3. Resource, semantic, and security gates each detect their intended regression
   type in the calibration suite.

It does not yet support a broad claim that CARE has high safe-patch acceptance,
that the security gate independently dominates in real projects, or that CARE
outperforms all external LLM refactoring systems.
