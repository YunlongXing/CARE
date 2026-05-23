# Table 2: Validator Ablation

| System | Variant | Resource | Semantic | Security | Generated | Passed | Pass rate | Extra accepted vs full | LLM false accepts | LLM false accept rate |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| CARE | Full validator | on | on | on | 487 | 31 | 6.37% | 0 | 0 | 0.00% |
| CARE | No resource checker | off | on | on | 487 | 189 | 38.81% | 158 | 121 | 76.58% |
| CARE | No semantic checker | on | off | on | 487 | 31 | 6.37% | 0 | 0 | 0.00% |
| CARE | No security checker | on | on | off | 487 | 32 | 6.57% | 1 | 1 | 100.00% |
| CARE | No resource/semantic/security checkers | off | off | off | 487 | 193 | 39.63% | 162 | 125 | 77.16% |
| LLM-only | Full validator | on | on | on | 487 | 5 | 1.03% | 0 | 0 | 0.00% |
| LLM-only | No resource checker | off | on | on | 487 | 62 | 12.73% | 57 | 50 | 87.72% |
| LLM-only | No semantic checker | on | off | on | 487 | 5 | 1.03% | 0 | 0 | 0.00% |
| LLM-only | No security checker | on | on | off | 487 | 5 | 1.03% | 0 | 0 | 0.00% |
| LLM-only | No resource/semantic/security checkers | off | off | off | 487 | 63 | 12.94% | 58 | 51 | 87.93% |

Definitions:

- Extra accepted vs full = patches accepted by the ablated validator but rejected by the full validator.
- LLM false accept rate is populated when Table 3 review outputs are provided.
