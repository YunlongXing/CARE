# Table 1: CARE vs LLM-only

| System | Opportunities | Generated patches | Applicable patches | Patch apply rate | Validation passed | Validation pass rate | Unsafe regression count |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| CARE | 487 | 487 | 193 | 39.63% | 31 | 6.37% | 4 |
| LLM-only | 487 | 487 | 63 | 12.94% | 5 | 1.03% | 1 |

Definitions:

- Patch apply rate = applicable patches / generated patches.
- Validation pass rate = fully validated patches / generated patches.
- Unsafe regression count = applicable candidates rejected by the security regression checker.
