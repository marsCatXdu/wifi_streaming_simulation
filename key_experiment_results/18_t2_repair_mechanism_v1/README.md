# T2 packet-repair mechanism experiment

This directory will hold the frozen six-arm mechanism result.  It currently
contains an explicitly partial, pre-fix archive produced before correcting a
generic validator that did not understand coded completion.

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

## Next boundary

Correct coded-completion validation without weakening non-FEC checks, recover
the 19 existing attempts without simulation reruns, execute only the 20
paired oracle runs, then generate and archive the complete six-arm analysis.
Stop for review after that result; do not train another predictor or redesign
the action in this iteration.
