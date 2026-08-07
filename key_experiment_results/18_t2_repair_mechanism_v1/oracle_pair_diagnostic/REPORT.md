# T2 oracle pair-closure diagnosis

**Status: the existing oracle arm is not a valid deadline-repair oracle.**

All 60 STR/baseline/oracle runs pass individual strict validation. All oracle actions exactly match the frozen baseline sidecar, but the sidecar is not consistently deadline-censored.

## Exact closure result

- 9195 of 36,000 paired frames have primary-set drift, across 20 pairs.
- Exactly 12456 frames omit 12456 packets that arrived only after the deadline in the baseline.
- The sidecar requests 138,523 repair packets. A deadline-correct plan requests 150,979, an increase of 8.99%.
- 9172 primary-set differences have the direct late-first censoring signature; 23 do not.
- Pre-T2 snapshot drift occurs in 8520 frames.
- Primary-link aggregate application bytes, PHY TX airtime, successful MPDUs, and retransmissions are identical in every pair: True.

The receiver creates frame state on first packet arrival. If the first primary packet arrives after the deadline, the primary-only run records that late packet before immediate finalization. A timely secondary repair creates state earlier, so the paired oracle run finalizes at the deadline and ignores the same late primary packet. The sidecar consequently omits that packet from the repair plan. Later receiver-set and snapshot differences cannot be treated as an exact packet counterfactual, even though the aggregate primary transmission counters remain identical.

## Observed flawed replay (not an oracle estimate)

| Arm | Misses | Miss rate | Sender airtime |
| --- | ---: | ---: | ---: |
| STR MLO | 11,432 | 31.7556% | 6658.94 ms/run |
| 5 GHz only | 14,777 | 41.0472% | 7778.81 ms/run |
| Existing flawed repair replay | 13,245 | 36.7917% | 10440.77 ms/run |

The flawed replay is +5.0361 percentage points versus STR at 1.5679x sender airtime. The primary-only airtime floor is already 1.1682x STR.

These observed oracle-arm outcomes are retained only to diagnose the failure. They must not be used as the privileged packet-repair ceiling.

## Repair boundary

A valid replay must define the privileged plan as every primary packet absent at the frame deadline. For a baseline whose first arrival is late, that means all source packets, not the sidecar complement after lazy state creation. The 100 factual runs remain valid and need no rerun.
