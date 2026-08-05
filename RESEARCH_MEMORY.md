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
  than STR MLO.  EMLSR is not an optimization target or qualification gate;
  its completed-frame P99 is descriptive only because the selected denominator
  rewards its high miss rate.
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

## Score-aware emergency V2 engineering qualification

V2 changes admission only.  If ordinary measured-airtime admission rejects a
threshold-passing frame, an exact high-score tier may borrow at most `60000 us`
against later refill.  Predictor, primary score, P-frame gate, action,
conservative reservation, 0.6% refill, 10-second bucket, and environment are
unchanged.  The frozen contract is
`experiments/model-selection/paired-value-duplication-t2-score-aware-emergency-v2.json`
with SHA-256
`bdc5b2a944475d1cc31749100e333a2eb2059e106eaf86d918855b721ab3fcda`.

The 48-pair campaign on fresh engineering seeds `1251` through `1298` passes
all four frozen gates:

| Metric | Score-aware T2 V2 | STR MLO | Paired policy-minus-STR result |
| --- | ---: | ---: | ---: |
| All-generated miss rate | 0.5729% | 0.7998% | -0.2269 pp, 95% interval [-0.3194, -0.1377] pp |
| Mean per-run completed P99 | 17.192 ms | 18.875 ms | -1.682 ms, 95% interval [-2.643, -0.795] ms |
| Sender-airtime ratio | - | - | 1.1217, 95% interval [1.0847, 1.1565] |
| Background-throughput loss | - | - | 0.0054%, 95% interval [0.0027%, 0.0081%] |

This is the first clear engineering victory against STR.  It reduces misses
by 28.36% and P99 by 8.91% relative to the matched STR points, but it is not
final confirmation and is still short of the longer-term aspiration of more
than 50% fewer misses.  Seeds `1301` through `1348` remain unopened.  The
authoritative compact snapshot is
`key_experiment_results/07_score_aware_t2_str_engineering_v2`.

The mechanism diagnosis is decisive.  Reconstructed primary-copy misses are
1,232 and final union misses are 495.  Actions cover 771 primary misses and
rescue 737 (95.59%).  V2 admits 4,944 threshold passers with 15.59% primary
miss risk and rejects 2,540 with only 6.38% risk, correcting V1's reversed
ordering.  Its emergency tier covers 453 primary misses among 2,116 actions
(21.41% risk), versus 318 among 2,828 strict actions (11.24%).

Remaining final misses are: 170 below threshold, 162 guard rejected, 72
history warmup, 48 I-frame restricted, 34 acted but still late/incomplete,
and 9 outside the decision window.  Mean measured secondary airtime is only
240.43 ms/run.  All score passers carry 355.25 ms/run of learned predicted
cost, below the 360 ms/run generated over 60 seconds, while rejected
candidates account for 120.84 ms/run.  This supports one more isolated guard
experiment: increase causal carry-over toward the full experiment horizon
while freezing the predictor, threshold, emergency tier, conservative
reservation, refill, action, and environment.  Use only already-open
engineering seeds for development; a sensitivity projection is not evidence
until closed-loop contention is measured.

After that guard isolation, prioritize a better treated-outcome/cost ranker
for the 170 below-threshold misses and wasted action airtime.  Make startup
semantics explicit: the first eight frames per run have 72 misses among 384
frames because temporal history is unavailable.  Use either a causal
non-temporal fallback or consistent pre-roll.  Treat the 48 I-frame misses
with an I-specific rule rather than duplicating every large I-frame.

The checksum-bound raw 96-run archive has SHA-256
`382e4a3508cd013dc028b849301096054c12eef4cb302ed101f14d0434d6da3f`.
Its local and experiment-host paths are recorded in the compact snapshot.  It
still needs durable external publication before a release-quality handoff.

## Full-horizon carry-over V3 null result

V3 tested whether V2's unused long-run headroom was stranded by its 10-second
credit capacity.  It raised only the maximum horizon and capacity to 60 seconds
and `360000 us`; startup credit remained `12000 us`.  Predictor, threshold,
P-frame gate, emergency score and `60000 us` debt, 0.6% refill, reservation,
action, and environment were unchanged.  The exact contract is
`experiments/model-selection/paired-value-duplication-t2-full-horizon-carryover-v3.json`.

The 48-pair closed-loop campaign reused opened seeds `1251` through `1298`.
All 96 runs passed strict validation.  V3's headline result is exactly V2's:
495 misses, 17.192 ms mean per-run completed P99, sender-airtime ratio 1.1217,
and background loss 0.0054%.  This is a genuine null intervention, not merely
an underpowered comparison:

- zero of 86,400 policy decisions differ from V2;
- zero of 86,400 frame outcomes differ, excluding `run_id`;
- the two aggregate CSV files are byte-identical;
- 29,673 guard-balance rows do differ, and V3 reaches its `360000 us` cap.

The extra credit is mistimed.  It changes available credit on 29,295
noncandidate rows and 378 actions V2 already admits strictly.  It changes
available credit on none of the 2,116 emergency actions and none of the 2,540
guard rejections.  Do not rerun larger bucket capacities or claim that V3
improves V2.  The compact evidence is
`key_experiment_results/08_full_horizon_t2_str_engineering_v3`.

The next admission mechanism should make credit available at the constrained
decision: allow conservative reservations to borrow only causally remaining
refill before measurement stop, reduce that allowance with time, and enforce
repayment at the stop.  Test it only on opened engineering seeds.  If it does
not turn rejected candidates into useful rescues while keeping the sender
airtime interval below 1.20, stop admission tuning and move to better
treated-outcome/cost ranking, startup fallback, and an I-frame-specific rule.
Reserved confirmation seeds `1301` through `1348` remain unopened.

## Remaining-refill borrowing V4 negative result

V4 tested a final admission tier that, after inherited strict and high-score
emergency admission fail, may reserve against refill causally remaining before
the measurement stop.  It kept V3's predictor, threshold, P-frame gate,
reservation estimator, 0.6% refill, startup credit, action, and environment
fixed.  The frozen contract is
`experiments/model-selection/paired-value-duplication-t2-remaining-refill-borrowing-v4.json`.

The 48-pair campaign reused opened seeds `1251` through `1298`; all 96 raw
runs passed strict validation both on the VM and after local restoration.  V4
still passes every frozen gate against STR: 585/86,400 misses (0.6771%) versus
691 (0.7998%), 17.395 ms mean per-run completed P99 versus 18.875 ms, sender
airtime ratio 1.1255, and background loss 0.0050%.  It is nevertheless worse
than V2/V3's 495 misses and 17.192 ms P99, so it is a negative policy
iteration.  The compact evidence is
`key_experiment_results/09_remaining_refill_t2_str_engineering_v4`.

The exact V3/V4 comparison isolates admission chronology:

- all 86,400 predictor scores and threshold memberships are identical;
- primary-copy outcome changes on only one frame;
- V4 has 5,272 actions versus V3's 4,944, but only 3,910 are common;
- 1,034 V3-only actions contain 172 primary misses (16.63%), while 1,362
  V4-only actions contain only 76 (5.58%);
- the V3-only median score and frame time are `1.7865e-4` and 46.45 s, versus
  `1.1684e-4` and 15.27 s for V4-only actions;
- V4 fixes 82 V3 misses but creates 172 new misses, for a net increase of 90.

Conditional rescue remains excellent: V4 rescues 646 of 675 acted primary
misses (95.70%), essentially the same as V3's 737 of 771 (95.59%).  The new
tier locally adds an action, but the early reservation changes later balance
and globally displaces higher-score actions.  Do not infer an action superset
from the contract's pointwise `new_tier_can_only_add_actions` statement.

Do not promote or rerun V4 on new seeds.  The guard-isolation sequence is now
closed: bounded high-score emergency borrowing is useful, more stored capacity
is inert, and chronological borrowing against all remaining refill is harmful.
Return to V2 as the best implementation and move next to predictor/ranker
quality.  Prioritize the 170 V2 below-threshold misses and airtime spent on
on-time primaries.  Keep startup fallback and an I-frame-specific policy as
separate later interventions.  Reserved confirmation seeds `1301` through
`1348` remain unopened.

## Frozen-head cost-denominator ablation

Commit `8d8f246` adds a checksum-bound retrospective ablation that reuses the
canonical primary-only temporal-T2 model without fitting any head.  It pairs
each existing value signal with a cost-free version, selects only among the
cost-free candidates on the original calibration runs, and evaluates the
winner once on the already-opened engineering-test role.  Seeds `1301`
through `1348` remain untouched.

The learned per-frame secondary-airtime divisor harms rank order.  At the
same 15% requested action fraction, temporal family, and P-frame gate, raw
bad12 value improves deadline misses by 82.40% and completed-late18 by 65.40%,
versus 79.99% and 61.79% for bad12 value divided by learned cost.  Raw value
also uses less calibrated DR airtime: 360.05 versus 372.63 us per eligible
frame.  This matched-budget result isolates the denominator from action-count
changes.

The cost-free grid selects raw bad12 value at 16.5% requested actions.  Its
calibrated worst-objective improvement is 68.43% and its DR airtime is 398.05
us per eligible frame.  On the opened engineering test, the selected policy
has 0.4723% estimated misses versus 0.5229% for the frozen source.  The paired
winner-minus-source 95% interval is `[-0.1016, -0.0006]` percentage points.
Its completed-late18 ratio is 1.4889% versus 1.5918%, with paired interval
`[-0.1861, -0.0313]` percentage points.  It uses 1.49 percentage points more
actions and 30.47 us more DR airtime per eligible frame.

This result motivated the V5 closed-loop test; it did not establish a runtime
improvement or justify removing conservative cost reservation.  V5 later
falsified the assumption that a better static offline ranker necessarily
improves a chronological budgeted controller.  The compact ablation artifact
is
`key_experiment_results/10_temporal_t2_cost_denominator_ablation_v1`.

## Cost-free score-aware V5 closed-loop result

V5 exported the cost-ablation winner while retaining V2's exact guard,
reservation, frame gate, action, and environment.  Its 48-pair campaign on
the already-opened seeds `1251` through `1298` passes every frozen STR gate:

| Metric | Cost-free T2 V5 | STR MLO | Paired V5-minus-STR result |
| --- | ---: | ---: | ---: |
| All-generated miss rate | 0.5741% (496/86,400) | 0.7998% (691/86,400) | 95% interval [-0.3113, -0.1435] pp |
| Mean per-run completed P99 | 17.081 ms | 18.875 ms | 95% interval [-2.756, -0.886] ms |
| Sender-airtime ratio | - | - | 1.1236, 95% interval [1.0865, 1.1587] |
| Background-throughput loss | - | - | 0.0047%, 95% interval [0.0023%, 0.0073%] |

V5 does not improve V2 on these paired development seeds.  V2 has 495 misses,
17.192 ms P99, and 4,944 actions; V5 has 496 misses, 17.081 ms P99, and 5,147
actions.  The direct V5-minus-V2 95% intervals include zero for both misses
(`[-0.0255, +0.0312]` percentage points) and P99
(`[-0.296, +0.057]` ms).  V5 also consumes 3.42% more measured secondary
airtime.  Freeze V2 as the engineering champion and do not create another
score/threshold-only V6.

The exact miss decomposition explains the null result.  Below-threshold
misses improve from 170 to 147, guard-rejected misses worsen from 162 to 193,
and residual misses after acting improve from 34 to 27.  Thus the final
change is `-23 + 31 - 7 = +1` miss.  V2-only actions contain 57/518 primary
misses (11.00%), while V5-only actions contain only 49/721 (6.80%).  The
offline ranker improved threshold filtering but passed a worse population to
the sequential budget controller.

This establishes only a local ceiling for the current scalar ranking plus
chronological airtime admission.  It does not establish a fundamental ceiling
for selective full-copy duplication.  V5 rescues 736 of 763 acted primary
misses (96.46%).  Applying that factual rate to all 193 guard-rejected primary
misses gives the optimistic sensitivity `496 - 193 * 736 / 763 = 309.83`
misses, or 0.3586%, 55.16% below STR.  This is not an oracle estimate: rejected
frames lack observed secondary-copy outcomes, may have different costs and
rescue probabilities, and admitting them would displace actions and change
contention.

The first ceiling-decomposition stage is complete at `aa9a20d`, with compact
evidence under
`key_experiment_results/12_temporal_t2_ceiling_decomposition_v1`.  Every
evaluated P-frame costs exactly `1983.760667318285 us` under the canonical
reservation.  Separate 360 ms refill-only and 372 ms finite-run proxies permit
181 and 187 actions per run.  V5's scalar score captures only 858 and 867
primary misses at those limits; even perfect rescue leaves 374 and 365 misses.
At the factual 96.46% rescue rate, the projections are 404.36 and 395.68
misses, above the target maximum of 345.  The V5 score first captures the
required 920 primary misses at 236 actions/run and up to 468.168 ms/run.

The 309.83-miss sensitivity is not a feasible per-run frontier.  V5's 8,218
threshold passers average 339.636 ms/run, but 26 of 48 runs exceed both the
360 ms and 372 ms proxies.  The aggregate calculation implicitly transfers
quiet-run credit to congested runs.

Perfect primary information selects all 1,103 eligible primary misses with
only 45.585 ms/run mean and 130.928 ms maximum reservation.  Perfect rescue
leaves the 129 misses outside the current candidate population; applying V5's
factual rescue rate projects 168.03 misses.  Therefore the full-copy action and
canonical reservation capacity do not yet limit the target.  The current
information/ranking does.

This is an exact primary-miss capture oracle, not an exact secondary-outcome or
P99 oracle.  Only 871/1,103 eligible primary misses have an action observation
in V2, V4, or V5; 232 are unobserved, and 18 observed frames change rescue
outcome across policies because the action sets interfere.  Do not relabel the
constant-rescue projections as performance estimates.

The distributional decomposition is complete through `dd9be5f`, with compact
evidence under
`key_experiment_results/13_temporal_t2_distributional_online_ceiling_v1`.
It estimates separate completion distributions with and without duplication
at `12`, `18`, `24`, `30`, and `33.333 ms`, keeps deadline rescue, tail
acceleration, and conservative cost separate, and treats passive secondary
features as an ablation.  The selected `primary_secondary_hgb64` model has a
0.9317 no-action deadline-risk AUC.  Its exact 372 ms/run P-frame static
frontier captures 1,634/2,056 observed primary misses (79.47%) and estimates a
0.588% doubly robust miss risk, versus 2.805% for treat none.

The frozen nonclairvoyant allocator realizes only part of that headroom.  Its
global shadow price captures 989 primary misses (48.10%) and estimates 1.479%
misses; learned congestion tertiles improve this to 1,056 captures (51.36%)
and 1.389% misses.  The latter remains 578 captures behind the identical
predictor's static frontier and reserves only 320.71 of the available 372
ms/run.  Thus the current replay ceiling is sequential credit allocation, not
the predictor's static ranking or selective full-copy action.

An exact decision audit explains the loss.  In the global replay, current
credit rejects 5,649 frames containing 813 primary misses (14.39%), whereas
15,392 admitted actions contain 989 misses (6.43%) and the 49,977
opportunity-price rejections contain only 146 (0.29%).  Congestion state moves
some of those valuable frames into the action set but leaves 748 misses among
5,442 current-credit rejections.  The high-value decisions arrive in bursts
faster than the strict 0.6% token refill makes them spendable.

Do not respond by restoring V4's unpriced chronological borrowing.  V4 already
showed that such borrowing displaces later, better actions and regresses.  The
next isolated mechanism should allow repayment-enforced future credit only
after the current reward clears the fold-honest opportunity price.  It must
retain per-run conservative reservation, close with nonnegative balance at
measurement stop, and pass a closed-loop sender-airtime/background check before
promotion.  Sixteen of 48 V5 runs individually exceed a 1.20 sender-airtime
ratio and have descriptively worse miss and P99 deltas, which supports retaining
congestion state without claiming causality.

The frozen V5 JSON contains an unsupported note that unarchived
secondary-feature and larger-model prototypes were null.  Its adjacent
erratum withdraws that evidence claim while preserving the checksum-bound
contract bytes.  Do not infer that either model family has been ruled out.
The compact V5 evidence is
`key_experiment_results/11_cost_free_t2_str_engineering_v5`.

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
evidence.  The final STR decision must come from the untouched 1301+ seeds
with the compiled policy and STR MLO built from the same commit.  An EMLSR arm
may be retained as a descriptive reference, but it must not steer or block
the STR-focused iteration.
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
