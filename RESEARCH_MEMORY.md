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
- The primary victory gates are lower miss rate and lower completed-frame P99
  than STR MLO, plus a decisively lower miss rate than EMLSR MLO.  EMLSR's
  completed-frame P99 is descriptive only and is not a victory gate because
  its selected denominator rewards the high miss rate.  EMLSR remains the
  harder sender-airtime baseline.
- If a latency comparison against EMLSR is needed, prefer an explicitly
  defined all-generated-frame deadline-censored metric that cannot improve by
  dropping difficult frames.
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

## Randomized-policy population mapping

The randomized T2 intervention dataset estimates effects only among frames
that are still actionable at T2 and satisfy the common intervention window.
Its raw policy risks and tail distribution must not be compared directly with
the all-generated-frame STR or EMLSR summaries.

On the fixed 16-run engineering test split there are 28,800 generated frames:

- 14,242 (49.4514%) are common-T2-eligible.
- 14,302 are already nonactionable at T2.  All complete by about 2.2 ms and
  have zero deadline misses and zero latency tails at 12 ms or above.
- 256 are last-16-per-run intervention-window boundary frames.  They receive
  no intervention and have zero misses in this split.

Consequently, the difficult-subset outcome must be combined with the factual
outcomes of the nonintervenable frames before interpreting an all-frame miss
rate or completed-frame P99.  For the frozen exploratory T2-only policy, that
mapping changes the estimated miss rate from 0.657% on eligible frames to
0.3248% on all frames.  A monotone known-propensity HT CDF sensitivity places
its pooled completed P99 in (17.25, 17.50] ms, directionally better than the
historical STR point of 18.664 ms.  The whole-run bootstrap remains too wide
to establish the P99 win, and the offline pooled quantile is not the same
estimand as a mean of per-run P99 values.

The exploratory T4 continuation is not currently worth its complexity.  In
the corrected all-frame population it adds about 0.457 percentage points of
launches and 8.44 us of airtime per generated frame, while improving the miss
rate by only 0.0116 percentage points and shifting the point P99 by roughly
0.25 ms.  Keep T4 as an optional future margin rather than part of the first
runtime implementation.

These numbers are post-selection engineering diagnostics, not confirmation
evidence.  The final decision must come from the untouched 1301+ seeds with
the compiled T2 policy, STR MLO, and EMLSR MLO built from the same commit.
When revisiting the offline audit, retain every generated frame, keep the
completed-tail numerator and completion denominator explicit, and use
whole-run resampling.
