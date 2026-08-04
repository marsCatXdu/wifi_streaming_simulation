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

Current development priority (2026-08-04): do not let EMLSR behavior steer
policy design or consume iteration effort.  First qualify the frozen policy
against STR MLO alone, using all-generated-frame deadline-miss rate, mean
per-run completed-frame P99, and the sender-airtime target.  EMLSR may remain
a later descriptive/reference arm, but it is not an optimization target and
must not delay improvements against STR MLO.

Primary evidence:

- `results/primary_tail_t4_remote_a7ac4ae4da42/campaign/analysis.json`
- `results/primary_tail_t4_remote_a7ac4ae4da42/campaign/analysis.md`
- `results/primary_tail_t4_remote_a7ac4ae4da42/campaign/runs/aggregate.json`
- per-run `frames.csv` files under the campaign's `runs` directory

The 95% whole-run bootstrap intervals from the audit were 6.269--14.292% for
EMLSR misses and 10.712--14.241 ms for its mean per-run completed P99.  Re-run
the paired analysis on the final fresh-seed, same-build campaign rather than
treating these engineering seeds as final confirmation evidence.

## Temporal-T2 48-pair STR qualification

The compiled primary-only temporal-T2 policy was run against STR MLO on the
48 matched engineering seeds `1201` through `1248` in the unchanged neutral
mixed-4x4 environment.  All 96 runs from commit `da48d7d` passed strict raw
validation.  The authoritative compact snapshot is
`key_experiment_results/06_paired_value_t2_str_qualification_v1`.

The candidate failed both performance gates while passing both resource
targets:

| Metric | Temporal-T2 | STR MLO | Paired policy-minus-STR result |
| --- | ---: | ---: | ---: |
| All-generated miss rate | 0.7882% | 0.7049% | +0.0833 pp, 95% interval [-0.0567, +0.2269] pp |
| Mean per-run completed P99 | 17.416 ms | 18.113 ms | -0.697 ms, 95% interval [-1.816, +0.310] ms |
| Sender-airtime ratio | - | - | 1.1324, 95% interval [1.0963, 1.1660] |
| Background-throughput loss | - | - | 0.0014%, 95% interval [-0.0017%, 0.0048%] |

The negative result does not mean that duplication or the primary-risk model
failed.  Of 551 admitted actions whose primary copy would miss, 538 were
rescued.  Across evaluated frames, the primary-miss ranking AUC is 0.922 and
bad12 AUC is 0.875.  The dominant defect is chronological guard allocation:
2,276 higher-scoring threshold passers were rejected after credit was spent,
and those frames contain 349 primary misses.  Guard-rejected frames have a
15.33% primary miss rate versus 11.12% for admitted actions.  The learned cost
head matches total cost but has effectively zero per-action rank correlation,
so dividing benefit by that estimate does not allocate individual actions
well.

Fix admission before replacing the predictor: preserve airtime for the
highest scores, or allow a bounded high-score emergency tier/future-credit
borrowing, while retaining canonical conservative cost reservation.  Explore
this only on development/engineering seeds.  Seeds `1301` through `1348`
remain reserved for final confirmation and must stay unopened until a revised
candidate passes engineering qualification.

The performance regression relative to
`primary_tail_t4_remote_a7ac4ae4da42` is predominantly lost action coverage.
The old closest-airtime arm acts on 8.06% of frames and covers 85.3% of its
observable primary misses; the current policy acts on 5.73% and covers 45.2%.
Current rescue efficacy is higher (97.6% versus 94.5%).  Both campaigns use
the same nominal mixed-4x4/STR configuration, and a seed-43 STR control
reproduces exactly across builds.  The old campaign has only 12 different,
post-selection development seeds, however, so use an old-T4 arm on current
engineering seeds as a mechanism control rather than claiming a causal
cross-campaign treatment effect.

Do not interpret the smaller maximum miss burst (10 versus STR's 14) as a
victory.  Temporal-T2 has 106 multi-frame miss episodes containing 289 missed
frames, versus STR's 57 episodes and 164 frames.  Absolute miss reduction
remains the objective; replacing rare long bursts with more frequent shorter
bursts is not sufficient.

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

## Frozen primary-only temporal T2 policy

The canonical offline fit was run from clean commit `9b9ee02` after freezing
`experiments/model-selection/temporal-t2-primary-only-two-objective-v1.json`.
Its on-disk artifact is
`results/randomized_full_copy_exploration_collection_v1/temporal_t2_primary_only_two_objective_v1`.
The artifact-manifest SHA-256 is
`b3af02b647c7671a631f3d43ebece75781989889358c845335d4003610a8208f`.
The model, candidate table, and metrics hashes recorded by that manifest are:

- model: `dff01b0f8319320489709c4039d97011f35439aa92adedbe167fe61b9de7bcb8`
- candidates: `7cbd5c622838df0a2f752c3bf9f4c54f333f7d280a9240cb80eda19efb1c28bb`
- metrics: `35929f0638b03ec79f2f3967dd947265c3d73b7fa51f487299cc1d96a555a014`

The selected policy uses the 246-input primary-only compact temporal family,
the legacy-bad12 value-per-cost ranker, a P-frame-only gate, and a float32
score threshold of `8.952784264693037e-05`.  The bad12 ranker is only a way to
rank eligible frames to construct candidate policies; calibration admission
and selection were still governed by the separate deadline-miss and
completed-late18 objectives.

On calibration runs it acts on 15.0077% of eligible frames, estimates 372.63
us of secondary airtime per eligible frame, reduces deadline misses from
2.0216% to 0.4044%, and reduces the completion-conditioned late18 ratio from
3.6248% to 1.3852%.  On the previously opened engineering test runs it acts on
16.1868%, estimates 365.49 us per eligible frame, reduces misses from 2.7876%
to 0.5229%, and reduces the late18 ratio from 4.2205% to 1.5918%.  Test
run-cluster 95% intervals are 0.3436--0.6736% for policy misses,
1.1764--1.9689% for its late18 ratio, and 259.02--476.13 us per eligible frame
for airtime.

Interpret these as engineering evidence only.  The raw values still apply to
the action-clean common-T2-eligible population, not all generated frames.  The
airtime point estimate passes the 400 us selection ceiling but its uncertainty
interval does not.  The selected P-only candidate beat the corresponding
all-frame candidate by only about `1.7e-5` in the maximin objective, so that
gate is fragile.  The compiled policy was subsequently judged on the 48
engineering seeds `1201` through `1248` with the 0.6% measured-airtime guard
and failed both STR performance gates; see the qualification section above.
Do not consume the reserved `1301` through `1348` confirmation seeds for this
failed version.  Retain the confirmation contract for a revised candidate:
beat STR decisively on all-frame miss rate and mean per-run completed-frame
P99, and never use EMLSR's survivor-conditioned completed P99 as a target.
