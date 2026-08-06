# Environment-generalization policy replay

This is a randomized, leave-one-family-out resource-ceiling analysis on action-clean eligible frames. It is not an all-generated-frame or closed-loop qualification result.

| Policy | Deadline miss (95% CI) | Completed late18 (HT) | Actions/run | Reservation/run |
| --- | ---: | ---: | ---: | ---: |
| No secondary copy | 26.0618% [20.6929%, 31.5182%] | 4.8145% | 0.00 | 0.00 ms |
| Uniform random | 21.7955% [17.2334%, 26.4642%] | 4.8770% | 178.38 | 360.42 ms |
| Myopic primary risk | 19.3400% [14.7175%, 24.0723%] | 4.3592% | 188.19 | 360.59 ms |
| Cross-fitted resource oracle | 18.7993% [14.3204%, 23.4304%] | 4.1620% | 187.90 | 360.44 ms |

## Ceiling decision

The cross-fitted resource oracle changes the eligible-frame deadline-miss estimate by 27.87% relative to no copy. This oracle sees all predicted scores in a run and is not deployable or perfect-information.

It selected 72153 rows; 10463 were marked for conservative OOD fallback.

Completed-late18 uses the declared high-variance Horvitz-Thompson estimator. Completed-frame P99 still requires actual closed-loop simulation.
