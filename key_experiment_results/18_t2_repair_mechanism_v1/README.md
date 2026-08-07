# T2 packet-repair mechanism experiment

This directory preserves the valid evidence from the frozen six-arm mechanism
campaign.  All 120 simulations finished and were retrieved.  The protected
factual archive contains the 100 individually strict-valid non-oracle runs;
the 20 oracle runs are excluded because their same-seed primary outcomes did
not pass the predeclared pair-closure audit.

## Protected factual phase 1

`factual_phase1/` contains 20 identical-seed units for STR MLO, single-link
5 GHz, full copy at T0, full copy at T2, and 12.5% ideal systematic FEC at T2.
It includes eight PNG/PDF figures, aggregate/run/scenario tables, the report,
and a checksum manifest.  Every generated frame enters the deadline-miss and
deadline-censored latency outcomes; completed-frame P99 is descriptive only.

| Arm | Deadline misses | Miss rate | Censored mean | Sender airtime |
| --- | ---: | ---: | ---: | ---: |
| STR MLO | 11,432 / 36,000 | 31.7556% | 15.776 ms | 6,658.94 ms/run |
| 5 GHz only | 14,777 / 36,000 | 41.0472% | 19.614 ms | 7,778.81 ms/run |
| Full copy T0 | 6,767 / 36,000 | 18.7972% | 11.459 ms | 13,920.77 ms/run |
| Full copy T2 | 6,832 / 36,000 | 18.9778% | 12.316 ms | 13,964.40 ms/run |
| Ideal FEC T2 | 14,610 / 36,000 | 40.5833% | 19.398 ms | 9,477.21 ms/run |

The small FEC action does not beat STR: its paired miss-rate delta is
`+8.8278` percentage points (95% CI `+3.0667` to `+14.0224`) and its paired
airtime ratio is `1.4232` (95% CI `1.3851` to `1.4614`).  It improves 5 GHz
only by just `0.4639` points while adding about `1,698 ms/run` of secondary
airtime.  Its action-completion rate is 72.60%, but this does not translate
into useful frame reliability.  The secondary T2 queue is generally small,
and the logged primary ACK deficit is saturated at ten packets, so neither
queue overload nor that scalar deficit explains or predicts the failures.

Full copy still demonstrates a large diversity gain, but both launch times
cost about `2.09x` STR airtime.  T2 is slightly worse than T0 and does not save
airtime.  This leaves the privileged packet-repair arm as the decisive test.
No oracle result is reported here: two compound-scenario pairs first exposed
primary-outcome drift (seed 21173 frame 2 and seed 21174 frame 33), violating
the frozen counterfactual closure requirement.

## Partial pre-fix evidence

`partial_pre_fix/` contains 80 strictly validated runs: 20 identical-seed
units for each of STR MLO, single-link 5 GHz, full copy at T0, and full copy at
T2.  The simulation commit is `791bb2d`; the clean analyzer commit is
`e06796f`.  All-generated deadline misses and deadline-censored latency are
the primary estimands.  Completed-frame P99 is descriptive only.

| Arm | Deadline misses | Miss rate | Censored mean | Sender airtime |
| --- | ---: | ---: | ---: | ---: |
| STR MLO | 11,432 / 36,000 | 31.7556% | 15.776 ms | 6,658.94 ms/run |
| 5 GHz only | 14,777 / 36,000 | 41.0472% | 19.614 ms | 7,778.81 ms/run |
| Full copy T0 | 6,767 / 36,000 | 18.7972% | 11.459 ms | 13,920.77 ms/run |
| Full copy T2 | 6,832 / 36,000 | 18.9778% | 12.316 ms | 13,964.40 ms/run |

Full copy confirms a large diversity benefit but uses about `2.09x` STR
sender airtime.  Delaying the full copy by 2 ms does not reduce airtime and is
slightly worse than T0 in aggregate.  OBSS-intensity p17 is the dominant
failure regime: STR has 72.10% misses and full copy still has about 52.6%.

This partial result cannot answer the decisive question.  The oracle phase
did not launch.  One FEC run that required no coded completion was promoted;
19 complete FEC attempts were rejected only because the old invariant required
every completed frame to contain all original source packets.  The promoted
FEC run is excluded here to retain a balanced four-arm panel, and the 19
attempts were retrieved intact before any validator change.

See `partial_pre_fix/REPORT.md` for the paired confidence intervals and
`partial_pre_fix/plots/` for the seven PNG/PDF figures.  The artifact manifest
records the clean analyzer identity and both source-manifest hashes.

## Current boundary

Preserve this five-arm result unchanged while enumerating and diagnosing the
oracle primary-outcome drift.  The oracle can enter a formal six-arm result
only if its privileged action and same-seed counterfactual are scientifically
identified under the frozen contract.  Stop after resolving or rejecting that
oracle construction and archiving the mechanism-gate conclusion; do not train
another predictor or redesign the action in this iteration.
