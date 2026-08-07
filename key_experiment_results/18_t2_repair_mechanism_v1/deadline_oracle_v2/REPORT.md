# Deadline-correct T2 packet-repair mechanism result

**Status: complete paired-potential replay; stop before the next iteration.**

This report combines the 100 immutable factual runs with 20 corrected
deadline-repair runs. All 20 rejected V1 oracle outputs remain excluded.
The repair arm is privileged and nondeployable. Receiver primary packet
sets may drift, so it is not described as an exact within-run oracle.

Primary outcomes include every generated frame. Completed-frame P99 is
descriptive only because it is survivor-conditioned.

## Aggregate result

| Arm | Misses | Miss rate | Censored mean | Sender airtime |
| --- | ---: | ---: | ---: | ---: |
| STR MLO | 11,432 | 31.7556% | 15.776 ms | 6658.94 ms/run |
| 5 GHz only | 14,777 | 41.0472% | 19.614 ms | 7778.81 ms/run |
| Full copy T0 | 6,767 | 18.7972% | 11.459 ms | 13920.77 ms/run |
| Full copy T2 | 6,832 | 18.9778% | 12.316 ms | 13964.40 ms/run |
| Deadline repair T2 | 6,342 | 17.6167% | 14.345 ms | 10468.22 ms/run |
| Ideal FEC T2 | 14,610 | 40.5833% | 19.398 ms | 9477.21 ms/run |

## Decisive resource question

- Original equal-airtime decision: **FAIL**.
- 1.20 engineering sensitivity: **FAIL**.
- Deadline repair minus STR misses: -14.1389 pp (paired 95% CI -19.4890 to -10.0361).
- Deadline repair / STR sender airtime: 1.5721 (paired 95% CI 1.5064 to 1.6316).
- Equal-airtime joint bootstrap probability: 0.00%.
- 1.20 joint bootstrap probability: 0.00%.

The action improves reliability but fails both the equal-airtime and 1.20 resource limits; prediction cannot remove this action cost.

## Paired-potential boundary

Receiver primary packet sets drift in 8,957 of 36,000 frames (24.88%).
This is privileged replay of the paired no-repair deadline potential, not an implementable causal policy and not a claim that treatment leaves receiver-level primary arrival timing unchanged.

## Primary-only transition decomposition

- primary only deadline misses: 14777
- deadline repair deadline misses: 6342
- primary misses rescued: 8436
- primary successes changed to miss: 1
- both miss: 6341
- both success: 21222
- net misses avoided: 8435
- rescue fraction of primary misses: 0.5708871895513298

## Next boundary

Stop here for review. Do not redesign the action, train another predictor, or open confirmation seeds in this iteration.
