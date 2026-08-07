# T2 mechanism campaign: valid pre-fix prefix

**Status: partial diagnostic, not the decisive six-arm result.**

This report uses the 80 strictly valid, balanced runs for STR, 5 GHz
only, full-copy T0, and full-copy T2. The oracle phase was not launched.
Nineteen FEC outputs were preserved but excluded because the old generic
validator rejects coded completion; the one promoted FEC run is also
excluded to keep the comparison balanced.

Primary outcomes include every generated frame. Completed-frame P99 is
descriptive only because it is survivor-conditioned.

## Aggregate result

| Arm | Misses | Miss rate | Censored mean | Sender airtime |
| --- | ---: | ---: | ---: | ---: |
| STR MLO | 11,432 | 31.7556% | 15.776 ms | 6658.94 ms/run |
| 5 GHz only | 14,777 | 41.0472% | 19.614 ms | 7778.81 ms/run |
| Full copy T0 | 6,767 | 18.7972% | 11.459 ms | 13920.77 ms/run |
| Full copy T2 | 6,832 | 18.9778% | 12.316 ms | 13964.40 ms/run |

## Paired contrasts versus STR

- 5 GHz only: miss delta +9.2917 pp (95% CI +3.5610 to +14.4667); airtime ratio 1.1682 (95% CI 1.1158 to 1.2240).
- Full copy T0: miss delta -12.9583 pp (95% CI -18.1944 to -8.8944); airtime ratio 2.0905 (95% CI 2.0488 to 2.1370).
- Full copy T2: miss delta -12.7778 pp (95% CI -17.9806 to -8.8277); airtime ratio 2.0971 (95% CI 2.0527 to 2.1437).

## Interpretation boundary

These four arms provide an early mechanism baseline only. They cannot
answer whether oracle packet-level repair beats STR at equal airtime.
The preserved FEC outputs must first be revalidated with coded-aware
completion accounting, then the paired oracle phase must run.
