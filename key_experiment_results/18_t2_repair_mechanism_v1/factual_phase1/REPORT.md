# T2 mechanism campaign: valid factual phase 1

**Status: protected diagnostic before oracle-pair diagnosis.**

This report uses all 100 individually strict-valid factual runs: STR,
5 GHz only, full-copy T0, full-copy T2, and ideal 12.5% FEC T2.
All 20 oracle runs are excluded because their same-seed primary packet
outcomes did not satisfy the frozen pair-closure requirement.

Primary outcomes include every generated frame. Completed-frame P99 is
descriptive only because it is survivor-conditioned.

## Aggregate result

| Arm | Misses | Miss rate | Censored mean | Sender airtime |
| --- | ---: | ---: | ---: | ---: |
| STR MLO | 11,432 | 31.7556% | 15.776 ms | 6658.94 ms/run |
| 5 GHz only | 14,777 | 41.0472% | 19.614 ms | 7778.81 ms/run |
| Full copy T0 | 6,767 | 18.7972% | 11.459 ms | 13920.77 ms/run |
| Full copy T2 | 6,832 | 18.9778% | 12.316 ms | 13964.40 ms/run |
| Ideal FEC T2 | 14,610 | 40.5833% | 19.398 ms | 9477.21 ms/run |

## Paired contrasts versus STR

- 5 GHz only: miss delta +9.2917 pp (95% CI +3.5610 to +14.4667); airtime ratio 1.1682 (95% CI 1.1158 to 1.2240).
- Full copy T0: miss delta -12.9583 pp (95% CI -18.1944 to -8.8944); airtime ratio 2.0905 (95% CI 2.0488 to 2.1370).
- Full copy T2: miss delta -12.7778 pp (95% CI -17.9806 to -8.8277); airtime ratio 2.0971 (95% CI 2.0527 to 2.1437).
- Ideal FEC T2: miss delta +8.8278 pp (95% CI +3.0667 to +14.0224); airtime ratio 1.4232 (95% CI 1.3851 to 1.4614).

## Interpretation boundary

oracle primary packet outcomes failed the frozen same-seed pair closure; oracle outcomes are not used in this factual-arm report.
This report preserves valid FEC and baseline evidence, but it does not
answer the decisive privileged-oracle equal-airtime question. No oracle
repair claim or next-action decision may be made from this artifact.
