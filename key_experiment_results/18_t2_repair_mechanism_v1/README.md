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
No oracle result is reported here: compound seeds 21173 and 21174 exposed the
first primary-outcome differences, but the complete diagnostic shows that all
20 pairs violate the frozen counterfactual closure requirement.

## Failed-oracle diagnosis

`oracle_pair_diagnostic/` strictly validates all 60 STR, primary-only, and
oracle-replay runs, binds every source run tree by checksum, and enumerates all
36,000 paired frames.  The existing replay is not an oracle estimate:

- 12,456 baseline frames record one first primary packet only after its
  deadline.  Lazy receiver-state creation includes that late packet in the
  sidecar, so the plan repairs the other packets but omits this one.
- The frozen sidecar requests 138,523 repair packets.  A deadline-correct plan
  would request 150,979, or 8.99% more.
- 9,172 flawed-replay misses contain exactly the omitted packet.  There are
  23 additional near-deadline primary-set differences and 8,520 T2 snapshot
  differences, so exact per-frame counterfactual closure is unavailable.
- Aggregate primary-link application bytes, PHY TX airtime, successful MPDUs,
  and retransmissions nevertheless match in every pair.  This localizes the
  main defect to deadline/finalization semantics, rather than failed or
  incomplete simulations.
- The flawed replay has 13,245 misses (36.7917%) at 10,440.77 ms/run sender
  airtime: 5.0361 percentage points worse than STR at 1.5679x airtime.  These
  values diagnose the bad plan only and must not be used as a repair ceiling.

The primary-only stream itself costs 1.1682x STR airtime, leaving very little
room under the project's 1.20 engineering target before any repair is sent.

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

Preserve both archives unchanged.  The V1 oracle arm is rejected.  A corrected
replay, if performed, must use packets absent at the deadline, add evidence
that is independent of receiver finalization, and rerun only the minimum
baseline/oracle subset needed to establish same-build closure.  Do not reuse
the flawed oracle outputs, rerun the 100 valid factual arms, train another
predictor, or redesign the action in this iteration.
