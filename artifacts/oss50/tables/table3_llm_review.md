# Table 3: LLM-Assisted Patch Review

## Passed Patch Correctness

| System | Reviewed patches | Correct | Likely correct | Incorrect | Uncertain | Strict correctness | Lenient correctness | Unsafe regressions |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| CARE | 31 | 7 | 24 | 0 | 0 | 22.58% | 100.00% | 0 |
| LLM-only | 5 | 2 | 3 | 0 | 0 | 40.00% | 100.00% | 0 |

## Failure Reason Classification

| System | Failed patches | Apply | Compile/test | Resource unsafe | Semantic change | Security regression | Overbroad | Detector FP | LLM/API | Validator infra | Unclear |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| CARE | 456 | 291 | 0 | 160 | 0 | 1 | 0 | 0 | 4 | 0 | 0 |
| LLM-only | 482 | 408 | 0 | 60 | 0 | 0 | 2 | 0 | 6 | 6 | 0 |

Note: this is simulated expert review by an LLM, not a substitute for independent human review.
