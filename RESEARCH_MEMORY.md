# Wi-Fi streaming research memory

Read this file before interpreting results or planning a final STR/EMLSR MLO
comparison.  This is durable project context, not a substitute for validating
the underlying artifacts when code, environments, or experiment definitions
change.

## EMLSR completed-P99 survivor bias

The current matched neutral MLO evidence uses 12 paired runs (seeds 43--54)
from `results/primary_tail_t4_remote_a7ac4ae4da42/campaign/runs`:

| Treatment | Deadline-miss rate | Mean per-run completed-frame P99 |
| --- | ---: | ---: |
| STR MLO | 0.7593% | 18.664 ms |
| EMLSR MLO | 10.1574% | 12.470 ms |

EMLSR's apparently fast P99 is computed only over completed frames.  Roughly
one frame in ten misses its deadline and is absent from that latency
distribution, so the value has strong survivor conditioning.  If every miss
is conservatively represented by at least the 33.333 ms frame deadline, the
EMLSR all-generated-frame P99 is at least 33.333 ms, not 12.470 ms.

Consequences for analysis and claims:

- Never report or optimize completed-frame P99 without the deadline-miss rate
  beside it.
- Do not describe EMLSR as unconditionally low latency on the basis of its
  completed-frame P99.
- The controller must beat both baselines simultaneously: STR is currently
  the harder miss-rate baseline, while EMLSR is the harder completed-P99 and
  sender-airtime baseline.
- Keep missed and intentionally abandoned frames in the generated-frame
  denominator.  They must not improve latency metrics by disappearing from
  the evaluated population.
- For an all-frame latency summary, state the miss-handling convention
  explicitly (for example, deadline-censored latency) and keep it separate
  from the completed-frame P99 estimand.

Primary evidence:

- `results/primary_tail_t4_remote_a7ac4ae4da42/campaign/analysis.json`
- `results/primary_tail_t4_remote_a7ac4ae4da42/campaign/analysis.md`
- `results/primary_tail_t4_remote_a7ac4ae4da42/campaign/runs/aggregate.json`
- per-run `frames.csv` files under the campaign's `runs` directory

The 95% whole-run bootstrap intervals from the audit were 6.269--14.292% for
EMLSR misses and 10.712--14.241 ms for its mean per-run completed P99.  Re-run
the paired analysis on the final fresh-seed, same-build campaign rather than
treating these engineering seeds as final confirmation evidence.
