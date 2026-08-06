# Complete held-out environment reliability result

**COMPLETE POST-OUTCOME RELIABILITY ANALYSIS: all 576 frozen runs are included, but the preregistered completed-frame P99 estimand is not assessable because some valid runs have fewer than 100 completions.**

## Complete-campaign results

| Arm | Deadline miss rate | Sender airtime | Background throughput | P99 support |
| --- | ---: | ---: | ---: | ---: |
| STR MLO | 16.0443% | 5157.587 ms | 32.298 Mbit/s | 184/192 runs |
| Score-aware T2 V2 | 19.1308% | 5792.251 ms | 32.232 Mbit/s | 181/192 runs |
| Distributional-shadow T2 | 18.7582% | 5946.699 ms | 32.242 Mbit/s | 183/192 runs |

## Comparisons

### Score-aware T2 V2 versus STR MLO

- Miss delta: 3.0865 percentage points (95% interval [1.5367, 4.6140]).
- Relative miss reduction: -19.24%.
- Sender-airtime ratio: 1.1231 (95% interval [1.0584, 1.1859]).
- Background-throughput loss: 0.206% (95% interval [-0.402%, 0.751%]).

### Distributional-shadow T2 versus STR MLO

- Miss delta: 2.7139 percentage points (95% interval [1.1790, 4.2135]).
- Relative miss reduction: -16.91%.
- Sender-airtime ratio: 1.1530 (95% interval [1.0887, 1.2146]).
- Background-throughput loss: 0.175% (95% interval [-0.428%, 0.739%]).

### Distributional-shadow T2 versus Score-aware T2 V2

- Miss delta: -0.3726 percentage points (95% interval [-0.5596, -0.1960]).
- Relative miss reduction: 1.95%.
- Sender-airtime ratio: 1.0267 (95% interval [1.0201, 1.0336]).
- Background-throughput loss: -0.031% (95% interval [-0.102%, 0.025%]).

## Interpretation boundary

- All 576 canonical runs passed strict validation.
- Reliability and resource estimands include every frozen run.
- The frozen run-level completed-frame P99 estimand is not assessable.
- No P99 gate or policy-promotion decision is inferred from this report.
- Completed-frame CDF/PDF plots are survivor-conditioned descriptive views.
