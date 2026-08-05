# Wi-Fi streaming working history

This file is the authoritative execution handoff for the current research
work.  It records what is complete, what is in progress, and what must happen
next.  Read it after `AGENTS.md` and `RESEARCH_MEMORY.md`, especially after a
context compression or a new agent session.

Keep this file factual.  Git, test output, and experiment artifacts remain the
source of truth; reconcile this document with them before continuing work.

## Stable objective

Improve selective duplication until an engineering candidate decisively beats
STR MLO in the unchanged neutral mixed-4x4 environment and demonstrates value
across held-out environment families, then qualify the frozen candidate on
untouched confirmation seeds.

The policy defeats STR MLO only if the paired campaign establishes all of the
following:

- decisively lower all-generated-frame deadline-miss rate;
- decisively lower mean per-run completed-frame HF7 P99, reported together
  with the miss rate;
- sender-airtime ratio versus STR MLO below `1.20`;
- background-throughput loss versus STR MLO no greater than `1%`.

EMLSR is not an optimization target or a qualification gate.  Its low
completed-frame P99 is survivor-conditioned by its high miss rate; see
`RESEARCH_MEMORY.md`.  Do not spend the current iteration trying to beat that
P99.

## Fixed experiment boundary

- Failed V1 engineering units: seeds `1201` through `1248`, ns-3 run `1`.
- Fresh score-aware V2 engineering units: seeds `1251` through `1298`, ns-3
  run `1`.  The matrix has one policy run and one STR MLO run per seed, for
  48 matched pairs and 96 runs.
- Carry-over V3 and remaining-refill V4 reuse the already-opened V2
  development units.  They are candidate-development mechanism tests, not
  independent confirmation.
- Cost-free score-aware V5 also reuses opened seeds `1251` through `1298`.
  It changes only the active ranking score and its calibrated primary and
  emergency thresholds; the V2 guard and environment remain fixed.
- Distributional shadow T2 reuses opened seeds `1251` through `1298` for its
  closed-loop mechanism test.  Its same-build STR arm is a new simulation at
  project commit `e2c770b`; it does not consume confirmation units.
- Reserved final-confirmation seeds: `1301` through `1348`; do not consume
  them during engineering.
- STR uses `NMaxInflights=1`.  Earlier results show MLO collapses at `2`, so it
  is not a tuning dimension for this campaign.
- Qualification host: VM SSH forwarded through
  `jingweili@10.120.16.105:30022`, configured with 64 vCPUs.
- Qualification analysis uses one shared `10000 x 48` whole-run bootstrap
  index matrix for miss rate, P99, airtime, and background throughput.

## Completed milestones

| Milestone | Commit | Evidence |
| --- | --- | --- |
| Freeze temporal-T2 selection | `9b9ee02` | Frozen selection JSON and canonical model identities |
| Freeze paired runtime contract | `35c0a1f` | Hash-pinned runtime contract |
| Export adapter goldens | `0810ac0` | Exact 246-feature adapter/model goldens |
| Add compiled predictor | `169fd44` | Temporal-T2 value predictor and tests |
| Add measured-airtime controller | `45c6ae3` | Paired T2 controller and tests |
| Wire runtime policy | `fe9bd9f` | Executable policy integration |
| Add paired qualification runner | `f3df932` | Frozen 48-pair matrix and two-run preflight |
| Preserve canonical artifacts | `62312a3` | Model, manifest, candidates, and metrics tracked |
| Add strict qualification analyzer | `ba59751` | Exact gates, shared bootstrap, and 28 focused runner/analyzer tests |
| Add bounded-debt guard primitive | `3740694` | Generic debt-limited admission and fail-closed unit tests |
| Add score-aware V2 profile | `a8f6d5a` | Frozen contract, runtime telemetry, and fresh-seed matrices |
| Validate V2 evidence | `24a5774` | Strict replay including serialized float32 profile identity |
| Make paired-meter replay robust | `cff64f7` | Component MILP, exact lattices, and independently rechecked integer witnesses |
| Qualify and archive score-aware V2 | `d114f65` | Strict 48-pair all-gate pass, compact snapshot, and raw-archive identity |
| Add full-horizon carry-over V3 | `1774d5b` | Isolated runtime profile with unchanged startup credit and opened-seed matrices |
| Validate full-horizon evidence | `ad16a84` | Profile-bound raw replay and qualification closure |
| Archive full-horizon V3 | `05f3e4a` | Strict null result and exact V2/V3 behavioral comparison |
| Add remaining-refill V4 | `c08468a` | Final causal borrowing tier with unchanged inherited policy settings |
| Validate remaining-refill evidence | `28c2b1b` | Schema-V3 replay of remaining credit, tier order, and repayment telemetry |
| Diagnose V3/V4 admission shift | `6fed1ca` | Exact 86,400-frame action and outcome transition tool |
| Evaluate frozen-head cost divisor | `8d8f246` | Paired cost-free ranking grid and whole-run delta uncertainty |
| Add cost-free score-aware V5 | `179a283` | Raw-value runtime profile with V2 admission semantics |
| Validate cost-free V5 evidence | `c61fa98` | Schema-V4 replay of both diagnostic and active scores |
| Compare active V2/V5 scores | `dce883f` | Schema-aware exact action and outcome transitions |
| Quantify paired V2/V5 deltas | `a16bcb3` | Shared-bootstrap direct miss and P99 intervals |
| Qualify and archive cost-free V5 | `0528610` | Strict all-gate STR pass and exact V2 null comparison |
| Add closed-loop ceiling decomposition | `e60b519` | Per-run scalar-score and perfect-primary-information frontiers |
| Plot temporal-T2 ceiling gap | `aa9a20d` | Resource-band and miss-sensitivity visualization |
| Archive temporal-T2 ceiling split | `b73dfc2` | Checksum-closed compact stage-one ceiling evidence |
| Freeze distributional T2 screen | `56e4826` | Six-bin CDF, feature-family, model, and cross-fit contract |
| Cross-fit T2 completion distributions | `1e47792` | Memory-bounded four-variant outer-fold fitter |
| Add static distributional frontier | `62bcf82` | Exact two-cost future-score frontier and DR replay |
| Correct distributional DR weighting | `ecc5e0b` | Pooled-frame point estimates with whole-run resampling |
| Freeze online shadow allocator | `04e3f43` | Causal time, credit, regime, and evaluation contract |
| Fit fold-honest shadow references | `473af75` | Exact selected-model reproduction and training-fold scores |
| Add fold-honest online allocator | `1aeddc0` | Nonclairvoyant shadow-price replay, report, and figure |
| Complete distributional ceiling replay | `dd9be5f` | Checksum-closed cross-fit, static, reference, and online artifacts |
| Freeze shadow-priced future credit | `ba4dfe9` | Single-mechanism repayable-credit contract and go/no-go screen |
| Replay shadow-priced future credit | `4a5bf74` | Exact baseline, debt, action-transition, DR, report, and figure evidence |
| Archive shadow borrow/repay screen | `4ffe58e` | Checksum-closed passing mechanism evidence and direct substitutions |
| Freeze distributional shadow runtime | `b6566c5` | Full-refit, reference, accounting, seed, and qualification contract |
| Fit distributional shadow runtime | `cd094f1` | Checksum-closed two-head refit and deployment-reference builder |
| Emit distributional runtime data | `49c7452` | Deterministic generated data, goldens, and compile-correct array contract |
| Compile distributional T2 model | `f233402` | Exact multiclass, shadow-reference, and credit parity in C++ |
| Add paired distribution predictor | `c33ed15` | Exact 308-feature primary-plus-secondary history adapter and tests |
| Add permanent airtime ledger | `49371b0` | Repayment-enforced canonical debit and generated-credit golden parity |
| Add distributional shadow controller | `472ed16` | Exact reward, opportunity-price, congestion, credit, and action telemetry |
| Integrate distributional shadow policy | `e2c770b` | Executable policy, output ownership, and runtime wiring |
| Validate distributional shadow evidence | `34e9296` | Independent model, reference, decision, and permanent-ledger replay |
| Qualify distributional shadow against STR | `bf9e5c1` | Strict 48-pair gates, direct mechanisms, and paired heterogeneity |
| Compare distributional shadow with V2 | `544008c` | Exact action transitions, paired uncertainty, and reviewed figures |
| Freeze environment scenario catalog | `10863e8` | Deterministic six-family matrices, unopened held-out catalog, and checksum manifest |
| Repair generalized-scenario PHY reception | `8c38753` | Exact crash replay, two negative-control regressions, and six full VM preflights |
| Build scenario-aware temporal dataset | `af1f29c` | Streaming manifest-bound builder, sender-known context, and leakage checks |
| Freeze environment LOFO analysis | `76c6872` | Held-out-family distributions, OOD, exploration, and regret contract |
| Add environment LOFO data primitives | `f519710` | Source-closed loader, deterministic splits, and robust OOD calibration |
| Cross-fit environment distributions | `82ca34e` | Two-arm CDF fitting, hierarchical diagnostics, and row-level artifacts |
| Freeze environment policy replay | `16d0e38` | Exact resource-oracle, baseline, uncertainty, and exploration contract |
| Add environment policy primitives | `06d6a81` | Decimal knapsack, uniform replay, DR/HT value, and artifact validation |
| Analyze environment resource policies | `e6ddcf3` | Shared hierarchical bootstrap, regret, confidence gates, and action archive |
| Plot environment policy analysis | `5bfc082` | Five checksum-bound frontier, family, OOD, allocation, and regret figures |
| Add deployment exploration wrapper | `e04093f` | Exact OOD fallback, SplitMix64 forcing, budget, propensity, and compliance logs |
| Orchestrate environment analysis | `3dd6855` | One clean-worktree VM entry point with top-level provenance closure |
| Preserve missing queue-order telemetry | `916bb9a` | Keep valid rows with jointly unavailable FIFO-ahead fields and blank only their derived clearance features |
| Record randomized collection closure | `1742ba1` | Exact 384-run manifest identity and corrected-analysis handoff |
| Freeze held-out qualification execution | `257b194` | Exact 576-run three-arm matrix in nine complete 64-worker waves |

The latest validator milestone is `34e9296`.  It retains the exact historical
paired-T2 checks and adds independent reconstruction of the distributional
multiclass model, completion CDFs, shadow reference, congestion regime,
repayable permanent ledger, decision routes, and runtime summaries.  V2
remains the engineering champion after the distributional closed-loop test.

## One authoritative TODO checklist

- [x] Finish one reviewable paired-T2 output-validator boundary: remove
  avoidable overlap with earlier generic validation, fix the remaining
  positive event-to-frame allocation invariant, run the focused and
  compatibility checks once, then commit and push (`3b8984f`).
- [x] Run and strictly validate the two-run local preflight.  Fix only defects
  that block a trustworthy campaign, and commit/push any such fix at a clean
  boundary (`bc76a13`; fresh manifest recorded below).
- [x] Run the 96-run campaign on the 64-vCPU VM, fetch and checksum the raw
  artifacts, validate every run locally, analyze against STR MLO, and record
  the result and next scientific decision here (`6b822a4`; compact result
  archived under `key_experiment_results/06_paired_value_t2_str_qualification_v1`).
- [x] Implement and evaluate score-aware emergency admission V2 on fresh
  engineering seeds, recover and strictly validate all 96 completed runs,
  archive the all-gate STR victory, and preserve seeds `1301` through `1348`
  (`d114f65`; simulation build `eb7f960`, final validator `cff64f7`; result under
  `key_experiment_results/07_score_aware_t2_str_engineering_v2`).
- [x] Isolate full-horizon causal carry-over as admission V3 on already-open
  engineering seeds while freezing the V2 predictor, threshold, emergency
  tier, refill, reservation, action, and environment.  V3 changed guard state
  but zero decisions or frame outcomes; archive the negative result under
  `key_experiment_results/08_full_horizon_t2_str_engineering_v3`.
- [x] Replace ineffective storage capacity with bounded reservation against
  causally remaining refill, preserving conservative accounting and enforcing
  repayment by measurement stop.  V4 passes against STR but regresses from
  V2/V3 because early lower-risk actions displace later higher-risk actions;
  archive it as a negative result and do not promote it.
- [x] Return to V2 and export the cost-free raw-value winner without changing
  its measured-airtime guard or conservative reservation.  Freeze and
  independently validate the resulting V5 runtime contract, then pass a
  same-commit local preflight (`179a283`, `c61fa98`).
- [x] Run cost-free V5 against STR on the 48 already-opened engineering seeds,
  fetch and checksum the 96 raw runs, strictly revalidate them locally, then
  generate the qualification and historical CDF/PDF/burst/resource figures.
  Archive the result and decide whether V5 replaces V2 (`0528610`).  V5 passes
  STR but does not improve V2; seeds `1301` through `1348` remain unopened.
- [x] Build the exact ceiling decomposition before another simulation
  campaign: factual V2/V5, an identified-outcome resource oracle with explicit
  counterfactual bounds, a cross-fitted causal T2 policy, and an implementable
  nonclairvoyant online allocator.  Replay canonical reservations and resource
  constraints exactly, then decide whether prediction, allocation, or the
  full-copy action is limiting.  The factual, per-run canonical, primary-oracle,
  and action-outcome-support stage is archived through `b73dfc2`.  The
  distributional stage completed from clean commits through `dd9be5f`: all
  artifact hashes close, the static predictor frontier succeeds, and the
  nonclairvoyant no-borrow replay exposes chronological credit as the dominant
  gap.  Do not create a score/threshold-only V6.
- [x] Combine the fold-honest shadow price with exact future-credit borrowing
  that must be repaid by measurement stop.  First replay the single frozen
  mechanism on the already-opened randomized groups and record direct rejection
  outcomes.  The replay passes all five gates through `4a5bf74`, raising
  primary-miss capture from 51.36% to 73.78%.  Next integrate the selected
  congestion policy into the runtime, validate its exported model/reference
  identities and exact repayment accounting, and qualify it against STR on
  already-opened engineering seeds before touching confirmation seeds.  The
  frozen full-data refit, reachable reference curves, generated C++ model,
  independent multiclass/CDF parity, and credit goldens are complete through
  `f233402`.  The paired adapter, permanent ledger, controller, runtime
  integration, and independent validator are complete through `34e9296`.
  The 48-pair closed-loop result passes every STR gate at 0.5266% misses,
  16.832 ms P99, and a sender-airtime upper interval endpoint of 1.1906, but
  reaches only a 34.15% miss reduction and is action-inefficient versus V2.
  The exact qualification and V2 comparison are complete through `544008c`;
  V2 remains champion and confirmation seeds remain unopened.
- [ ] Build environment-level generalization qualification before final
  confirmation.  Freeze a broad randomized scenario-parameter domain and
  explicit scenario-family identities; expose only causally observable
  environment variables to the model; perform leave-one-family-out fitting;
  and measure value in actual randomized and closed-loop held-out runs.  Report
  regret against a scenario-specific resource oracle and simple deployable
  baselines, not only predictor metrics.  Add a telemetry-support/OOD detector
  that falls back to a conservative policy, plus a small logged randomized
  exploration rate for post-deployment treatment-effect recalibration.  Keep
  seeds `1301` through `1348` untouched and freeze all family, split, gate,
  fallback, exploration, and regret contracts before reading held-out results.
  The six-family full preflight passed at `8c38753`, and all 384 randomized
  runs closed as six exact 64-worker waves.  The source-closed scenario-aware
  temporal builder is complete through `af1f29c`; `916bb9a` additionally
  preserves valid rows whose two FIFO-ahead fields are jointly unavailable.
  The corrected 307,689-row dataset and all 12 held-out-family model fits are
  published on the VM; the exact resource-policy stage is running, and no
  partial policy outcome has been inspected.  The frozen LOFO predictor/OOD
  path, exact policy/oracle/regret analyzer, OOD-aware exploration wrapper, and
  single-entry remote pipeline are complete through `3dd6855`.  Fetch,
  checksum, interpret, plot, and archive the analysis only after its top-level
  manifest closes.  The held-out V2/distributional-shadow/STR execution matrix
  is frozen at `257b194` as 576 simulations in nine full 64-worker waves.
  Freeze its analyzer before launch, then use the actual closed-loop family
  results to choose the next predictor/allocation intervention.

Do not replace this checklist with nested planning lists.  Add a new top-level
item only when the research objective genuinely changes.

## Current work boundary

The active boundary is environment-level generalization.  All 384 randomized
collection runs are complete and checksum-bound.  The corrected analysis has
published a 307,689-row action-clean T2 dataset and completed all 12 LOFO arm
fits across the six held-out families; the exact resource-policy replay is now
running on the VM.  Do not interpret partial policy output.  Wait for the
plots and top-level pipeline manifest, fetch and independently validate the
complete output, then archive the compact evidence.

One compound-shift scenario contributes only 16 included rows across four
runs after the frozen warmup and action-contamination exclusions.  Preserve
this as a coverage/uncertainty warning when interpreting family aggregates;
do not silently drop it or change the frozen population after seeing results.

The LOFO completion-distribution, robust OOD, exact decimal resource replay,
uniform/myopic baselines, cross-fitted resource oracle, DR/HT value, shared
10,000-sample hierarchical bootstrap, confidence gates, checksum-closed
row-action output, five-figure plot suite, OOD-aware logged exploration, and
detachable VM pipeline are implemented through `3dd6855`.  The predeclared
held-out execution is frozen at `257b194`: 48 unseen scenarios, 192 paired
units, and three arms produce 576 simulations in nine complete 64-worker
waves.  Build and freeze its analyzer before launch; do not inspect any
qualification outcomes while changing that analyzer.

Score-aware emergency admission V2 is complete and is the first candidate to
pass every frozen engineering gate against STR.  On seeds `1251` through
`1298`, it records 495/86,400 misses (0.5729%) versus STR's 691 (0.7998%), and
17.192 ms mean per-run completed P99 versus 18.875 ms.  The policy-minus-STR
95% intervals are `[-0.3194, -0.1377]` percentage points for misses and
`[-2.643, -0.795]` ms for P99.  Sender airtime is 1.1217 of STR with interval
`[1.0847, 1.1565]`; background loss is 0.0054%.  All four gates pass.

The mechanism evidence supports the score-aware guard.  Of 4,944 actions,
771 primary copies miss and 737 are rescued.  Admitted candidates have 15.59%
primary miss risk versus 6.38% among the 2,540 guard rejections, correcting
V1's admission inversion.  Emergency admissions carry 21.41% primary risk
versus 11.24% for strict admissions.  The remaining 495 misses decompose into
170 below threshold, 162 guard rejected, 72 startup history, 48 restricted
I-frames, 34 acted but still late/incomplete, and 9 outside the window.

Full-horizon V3 closed the stored-credit question with a negative result.  It
changes 29,673 serialized guard-balance rows and reaches `360000 us`, but its
86,400 policy decisions and 86,400 frame outcomes are exactly V2's.  Extra
credit appears on 29,295 noncandidate rows and 378 already-strict actions.  It
appears on none of 2,116 emergency actions and none of 2,540 guard rejections.
Increasing capacity therefore cannot expose the projected admission headroom
for this chronology.

Remaining-refill V4 closes the future-credit question with a negative result.
It passes all four STR gates at 585/86,400 misses (0.6771%), 17.395 ms P99,
1.1255 sender-airtime ratio, and 0.0050% background loss, but it is worse than
V2/V3's 495 misses and 17.192 ms P99.  The regression is not a predictor
change: all 86,400 scores and threshold memberships are identical between V3
and V4, and primary-copy outcomes differ on only one frame.

V4 launches 5,272 copies versus V3's 4,944.  Only 3,910 actions are common;
1,034 displaced V3 actions contain 172 primary misses (16.63%), while 1,362
V4-only actions contain 76 (5.58%).  The displaced actions occur much later
and have higher scores.  V4 fixes 82 V3 misses but creates 172 new misses.
Conditional rescue remains about 95.7%, so chronological future-credit
spending, not duplication, causes the loss.  Do not run another bucket or
remaining-refill variant.

The cost-free predictor boundary is complete.  Its static opened-data
ablation favored raw bad12 value, but closed-loop V5 falsified the assumption
that a better static ranking necessarily improves a chronological budgeted
controller.  V5 preserves V2's guard, reservation, P-frame gate, action, and
environment while replacing only the active ranker and thresholds.

V5 passes every frozen STR gate on the 48 opened engineering pairs: 496/86,400
misses (0.5741%) versus STR's 691 (0.7998%), 17.081 ms mean per-run completed
P99 versus 18.875 ms, 1.1236 sender-airtime ratio, and 0.0047% background
loss.  It nevertheless does not improve V2's 495 misses and 17.192 ms P99.
The direct V5-minus-V2 95% intervals include zero for both misses
(`[-0.0255, +0.0312]` percentage points) and P99
(`[-0.296, +0.057]` ms).  V5 uses 5,147 actions versus V2's 4,944 and 3.42%
more measured secondary airtime.  V2 remains the engineering champion.

The exact component change is decisive: below-threshold misses improve
170 to 147 (-23), guard-rejected misses worsen 162 to 193 (+31), and residual
misses after acting improve 34 to 27 (-7), for one additional final miss.
V2-only actions carry 11.00% primary misses, while V5-only actions carry only
6.80%.  V5 improved the first filter but sent a worse population through the
sequential budget controller.

The first ceiling stage now establishes an information gap, not an action
ceiling.  Separate 360 ms and 372 ms canonical proxies allow 181 and 187
actions/run.  V5's score captures 858 and 867 primary misses at those limits;
even perfect rescue leaves 374 and 365 misses, above the target maximum of
345.  At the factual 736/763 rescue rate, V5 must capture 920 primary misses,
which its current score first reaches at 236 actions/run and up to 468.168 ms
of reservation.

Perfect primary information captures all 1,103 eligible misses with only
45.585 ms/run mean and 130.928 ms maximum reservation.  Applying V5's rescue
rate projects 168.03 misses.  Conversely, the optimistic 309.83-miss
all-threshold calculation is not per-run feasible: 26/48 runs exceed both
canonical budget proxies even though the aggregate mean is 339.636 ms/run.
Only 871/1,103 eligible primary misses have any V2/V4/V5 action outcome, and
18 observed outcomes change across policies, so secondary-outcome and P99
oracles remain unidentified.

The distributional ceiling stage is complete.  On 73,400 action-clean rows
from 96 already-opened randomized groups, all four cross-fitted variants have
no-action deadline-risk AUC between 0.9317 and 0.9335.  The frozen priorities
select `primary_secondary_hgb64`.  At the exact 372 ms/run static P-frame
frontier it takes 17,943 actions and captures 1,634/2,056 primary misses
(79.47%); the pooled doubly robust miss estimate is 0.588% versus 2.805% for
treat none, and completed-late18 falls from 4.336% to 1.839%.

The same predictor under the nonclairvoyant no-borrow allocator exposes an
allocation rather than prediction ceiling.  The global policy captures only
989 primary misses (48.10%), estimates 1.479% misses, and reserves 318.06
ms/run.  Congestion tertiles improve this to 1,056 captures (51.36%), 1.389%
misses, and 320.71 ms/run, but remain 578 captures behind the static frontier.
The global replay rejects 5,649 candidates for current credit and the
congestion replay rejects 5,442 while leaving roughly 50 ms/run unused.
An exact decision re-audit found that the global current-credit rejections
contain 813 primary misses, versus 146 among opportunity-price rejections;
the corresponding congestion counts are 748 and 144.  Credit-rejected frames
are the riskier population.

The isolated shadow-priced borrowing screen now passes.  With congestion
tertiles it captures 1,517 primary misses (73.78%), estimates 0.748% misses and
2.002% completed-late18, averages 324.82 ms/run of reservation, and never
exceeds 370.964 ms/run.  All 96 balances close positive at measurement stop;
the minimum is 1.037 ms.  Relative to strict congestion admission, the shared
bootstrap miss-risk delta is -0.6409 percentage points with interval
[-0.7945, -0.4946], and late18 changes by -0.3933 points with interval
[-0.5339, -0.2734].

This succeeds for the reason V4 failed.  Borrow/repay substitutes 3,648
borrow-only actions containing 628 primary misses (17.21%) for 3,449
strict-only actions containing 167 (4.84%).  Borrow-only frames are earlier
(median frame 551 versus 1,268) and have four times the median predicted
reward.  The opportunity price raises as debt consumes future capacity and
protects later high-value opportunities.

The distributional runtime and its closed-loop test are complete.  On the 48
opened engineering pairs, it records 455/86,400 misses (0.5266%) versus STR's
691 (0.7998%) and 16.832 ms mean per-run P99 versus 18.875 ms.  The paired
miss and P99 intervals are strictly favorable.  Sender-airtime ratio is
1.1662 with interval `[1.1405, 1.1906]`, and background loss is 0.0050%; all
STR gates pass.  Every ledger repays despite 161.309 ms worst transient debt.

This does not promote the candidate.  Its 34.15% relative miss reduction is
short of the greater-than-50% objective.  Against V2 it launches 8,336 versus
4,944 actions but captures only 809 versus 771 primary misses.  Candidate-only
actions carry 3.22% primary-miss risk versus 9.46% for V2-only actions.  The
direct miss interval includes zero, although P99 is decisively lower.  V2
therefore remains the engineering champion; selection efficiency, not the
96.04% conditional rescue action, is limiting.  Compact evidence is under
`key_experiment_results/15_distributional_shadow_t2_str_engineering_v1`.

The next work boundary is environment-level generalization infrastructure,
not another neutral-environment threshold or borrowing variant.  It must
separate random seeds from scenario variation, test held-out scenario
families by direct policy value and regret, and fail conservatively under
unfamiliar telemetry while retaining logged randomized exploration.

The first generalization boundary is frozen and pushed as `10863e8`.  Six
scenario families independently vary radio propagation, OBSS intensity, OBSS
geometry/MAC behavior, video workload, legacy coexistence, and a compound
shift.  SHA-256 Latin-hypercube sampling creates 96 randomized-collection
scenarios with four independent seeds each (384 runs, exactly six 64-worker
waves), 48 unopened closed-loop scenarios with four seeds each (192 paired
units), and six preflight scenarios.  The held-out STR/V2/distributional
comparison is 576 runs, exactly nine 64-worker waves; fixed-5-GHz and full
duplication are intentionally excluded.  All scenario identities remain
manifest metadata rather than simulator/model inputs.

The generated collection and preflight YAMLs, all-phase scenario catalog,
and artifact manifest are byte-reproducible with
`tools/generate_environment_generalization_v1.py --check`.  Phase seed sets
are disjoint and have zero overlap with `1301` through `1348`.  All six full
preflights passed at `8c38753`, and the 384-run collection is running on the VM
with all 64 vCPUs.  The new builder consumes one validated run at a time,
rederives every run ID from the frozen matrix, keeps scenario/family identity
out of the feature allowlist, and adds only five sender-known stream-context
features.  Do not read the 48 qualification scenarios' results before the
model, fallback, exploration, regret, and closed-loop comparison contracts are
implemented and frozen.

Reserved seeds `1301` through `1348` remain unopened.  V2 is an engineering
pass, not final confirmation.  Freeze the generalization domain, candidate,
fallback, exploration, and analyzer before consuming those seeds.

The V1 event schema does not record its per-frame byte split or equal-time
callback sequence, and the portable compiled cost path does not freeze FMA
contraction.  The current campaign has no equal-time decision/event or
decision/settlement collision, and its x86 build exactly matches the scalar
replay.  Add explicit telemetry/FP-contract evidence before a portable-device
qualification; this is not a reason to rerun the current campaign.

## Verification ledger

Do not repeat an entry unless relevant code changed after it ran.

- Paired validator focused suite at `3b8984f`: `10/10` passed.
- Direct validator-import compatibility suites at `3b8984f`: `129/129`
  passed.
- Two retained real artifacts at `3b8984f`: both validated with 1,800 frames;
  the action-bearing artifact had 1,016 evaluated model rows and 133 actions.
- `py_compile` and `git diff --check` passed before `3b8984f`.
- Fresh local preflight at `bc76a13`: policy run
  `6ed9ba7d99a25fd10eb7` and STR run `f393a86fa1727822896f`
  both completed and independently revalidated with 1,800 frames.  Manifest
  project commit and ns-3 upstream commit exactly match the run artifacts.
- Validator fix at `575f171`: `15/15` focused paired tests and `134/134`
  validator-import compatibility tests passed; `py_compile` and
  `git diff --check` passed.
- All nine preserved VM attempts at `da48d7d` passed the fixed validator with
  1,800 frames each.  Both `bc76a13` preflight arms still pass.
- Failed-attempt archive SHA-256:
  `2501cc18b66f91c23c971705e8c730a4e6b1a603cd34f5b7bf916fb72e727586`.
- An independent release audit reproduced and then confirmed rejection of an
  impossible three-byte `0.5/0.5` split and a `5e-8 us` checkpoint mutation.
- Rounded-witness portability fix at `6b822a4`: `16/16` focused paired tests,
  `135/135` validator-import compatibility tests, all 96 campaign runs, and
  both retained preflight arms passed.
- Final 48-pair report: policy 0.7882% misses and 17.416 ms P99; STR 0.7049%
  and 18.113 ms; sender-airtime ratio 1.1324; background loss 0.0014%.
- Qualification plotting checks at `d78c288`: `61/61` combined
  qualification, plotting, and generic analysis-tool tests passed.  Real
  regeneration freshly strict-validated all 96 runs before writing the
  machine-readable admission diagnostic and figures.
- Score-aware V2 implementation through `24a5774`: both focused C++ suites
  passed, as did 112 combined Python controller/runner/validator/qualification
  tests, `py_compile`, and `git diff --check`.
- Same-commit V2 preflight manifest SHA-256:
  `e6141c6bfe6729a7a003ba408d01f00a4291845aa936ebf4b2216b8640001b24`.
  Policy run `c88bb4ff52b2dbe72507` and STR run `0e075faa14035bb7dc22`
  each passed strict validation with 1,800 frames.
- Robust validator at `cff64f7`: `77/77` focused and compatibility tests
  passed.  Both formerly hard real attempts validate in under five seconds;
  a balanced `+0.01/-0.01 us` settlement mutation is rejected by exact
  integer feasibility.
- All 11 preserved V2 attempts passed the final validator and were promoted
  without rerunning simulations.  All 96 canonical run directories then
  passed fresh strict validation for both analysis and plot generation.
- V2 manifest SHA-256:
  `24a87c9ec02e7564116992367754bdcf6ffc7845a1fa26908b8dea92fe316ef2`.
  Strict report SHA-256:
  `75b8797ade357d9877a594be8972fb7a68a84ff197cdea47baca02df6000773a`.
  Aggregate SHA-256:
  `7b4777f423072cd635a00261ff23a5ce17dc460020cab9082acc704750098c6d`.
- The V2 raw archive passes `zstd -t`, contains 96 canonical run directories,
  and has SHA-256
  `382e4a3508cd013dc028b849301096054c12eef4cb302ed101f14d0434d6da3f`
  at 94,939,663 compressed bytes.
- Final V2 48-pair report: policy 0.5729% misses and 17.192 ms P99; STR
  0.7998% and 18.875 ms; sender-airtime ratio 1.1217; background loss
  0.0054%; performance, resource, and overall status all `pass`.
- Full-horizon V3 implementation and evidence closure through `ad16a84`:
  `57/57` focused validator, qualification, plotting, and runner tests passed;
  the C++ controller suite and target build also passed.
- V3 seed-43 preflight passed strict validation but exactly reproduced V2's
  216 actions, 8 misses, and 21.419 ms P99.
- The 96-run V3 campaign manifest SHA-256 is
  `b37f7614d731257f83bcb79af5ab041e88f5619ba987f0fb9475cc9274c17a33`.
  Analysis and plotting each freshly strict-validated all 96 runs.
- The V3 raw archive passes `zstd -t` locally and remotely and has SHA-256
  `a355df37bce69b57a9f8cf0f081b5c66f7ea4563508734ad824abd0fd27eb598`
  at 95,546,464 compressed bytes.
- Exact V2/V3 comparison found zero changed admission rows and zero changed
  frame-outcome rows despite 29,673 changed guard-balance rows.
- Remaining-refill V4 implementation through `28c2b1b`: C++ controller and
  broad wifi-streaming suites passed, as did 54 focused Python tests and 7
  plotter tests.  The seed-43 preflight passed strict validation.
- The 96-run V4 campaign completed at commit `28c2b1b` on the 64-vCPU VM,
  consuming 10 h 57 min of CPU time with 5.4 GB peak memory.  Analysis and
  plotting strictly validated all 96 runs; local restored-archive analysis
  independently passed the same checks.
- V4 manifest SHA-256:
  `1bf8ffe0b3550ad39ff1df43ade8d71ae691aa2f78ac70d7a7d48361eff8d4e6`.
  Strict report SHA-256:
  `26bafbb66c54f060ce6556f7d4ca329f3b6e7e43f436edeb3dcf060c438234d2`.
  Aggregate SHA-256:
  `a6ac67796cfd5c7fcf3d80b92422634fe27c5f47be703377b148cce3c5c12d51`.
- The V4 raw archive passes `zstd -t` locally and remotely and has SHA-256
  `94a440bcf2ca2cb255fe8393ea2b18600196d692411bc97d0d8999a0ace51301`
  at 96,983,224 compressed bytes.
- Final V4 48-pair report: policy 0.6771% misses and 17.395 ms P99; STR
  0.7998% and 18.875 ms; sender-airtime ratio 1.1255; background loss
  0.0050%; performance, resource, and overall status all `pass`.
- Exact V3/V4 comparison at `6fed1ca` found zero changed scores or threshold
  results, 1,034 displaced V3 actions, 1,362 V4-only actions, and a net 90
  additional misses.  All 63 related Python tests passed.
- Frozen-head cost ablation through `8d8f246`: all 11 trainer/ablation tests
  passed, together with `py_compile`, line-length, and diff checks.  The
  clean-worktree output reproduces the frozen calibration and engineering-test
  point estimates before evaluating the cost-free winner.  Its artifact
  manifest SHA-256 is
  `1b082c5d908e00067256512b037871233350ca07df2d6fba9b7fe3f9ddb0b549`.
- The engineering-test paired winner-minus-source intervals are
  `[-0.1016, -0.0006]` percentage points for deadline misses,
  `[-0.1861, -0.0313]` percentage points for completed-late18, and
  `[+20.65, +39.52]` us per eligible frame for DR airtime.  This split was
  already open and remains descriptive.
- Cost-free V5 through `c61fa98`: focused C++ controller and broad
  wifi-streaming suites passed, as did 64 focused Python
  runner/validator/analyzer/plot tests.  Full tool discovery passed all 397
  tests; `py_compile`, line-length, diff, and ASCII checks also passed.
- Same-commit V5 preflight runs `0614d3114e945a784648` and
  `c56c7a0864a01b166def` each passed strict validation with 1,800 frames.
  Schema V4 records and independently replays the raw float32 policy score;
  the policy summary reports all integrity checks true.
- The V5 96-run campaign completed at simulation commit `ed21d0a` on the
  64-vCPU VM, consuming 9 h 53 min of CPU time with 2.4 GB peak memory.  All
  raw runs passed strict validation remotely and again after local archive
  restoration.
- V5 manifest SHA-256:
  `e341438bb43dcef81a62862632bb95b643336dd8e08a4961cf3c58ba2587161a`.
  Strict report SHA-256:
  `d86cee0418069ee5337d9a9a84d8930b96959bf6f22d6529a077d493320fc373`.
  Aggregate SHA-256:
  `4126beba63873cc24b9c33ab017a3131277d751e5dcd9bd5f6835929e2dfe62e`.
- The V5 raw archive passes `zstd -t` locally and remotely, contains exactly
  96 canonical run directories, and has SHA-256
  `fce039fa28e3ecc8ba8c9bee6759eb6ba71f9af0a16277a7e7f834b01fbd5694`
  at 97,159,582 compressed bytes.
- Final V5 48-pair report: policy 0.5741% misses and 17.081 ms P99; STR
  0.7998% and 18.875 ms; sender-airtime ratio 1.1236; background loss
  0.0047%; performance, resource, and overall status all `pass`.
- Exact V2/V5 analysis through `a16bcb3` found 203 additional V5 actions,
  one additional miss, 3.42% more secondary airtime, and direct paired miss
  and P99 intervals containing zero.  Its 3 Python tests, `py_compile`, and
  diff checks passed.  The complete compact result is archived at `0528610`.
- Closed-loop ceiling analysis through `aa9a20d`: 2 focused ceiling tests and
  all 3 comparator compatibility tests passed, as did `py_compile`, line-length,
  and diff checks.  A real-data regeneration consumed the restored,
  checksum-bound V2/V4/V5 raw artifacts and reproduced the report and figure.
- Ceiling JSON SHA-256:
  `ddb8205fbe767389dd557edf32958b22b64abf2083412d97e6ed34d083c5a065`.
  Markdown SHA-256:
  `ecde7ffa6a3267668f487e415fe63aaff2a9b037cc6b43c14d34d7d8981cbc7d`.
  Figure SHA-256:
  `fd8b2d8c8e414a63041f364d2ba8bffde76d3c75fb78cc8975ac7a41620ab755`.
- Distributional/static/reference/online tooling through `ecc5e0b`: 25
  focused tests passed together with `py_compile`, line-length, CLI, report,
  PNG-rendering, and diff checks.  Regression tests enforce held-out-fold
  exclusion, exact frozen predictor-selection order, and frame-ID-first
  score-tie resolution.
- Full Python tool discovery after the pooled-frame correction passed all
  `425/425` tests in 174.621 seconds.
- The canonical cross-fit prediction stream is 25,275,886 bytes with SHA-256
  `32d9cfda32d2dd7d380aaaefc659c3c2ca5d6f38593426135b0bc2b338ffab3b`;
  its manifest is
  `b086f16f2d33cf5404be142d69de32b2ff6602899ea37aa0714c68cc56053414`.
  All source and generated hashes close.
- The fold-honest shadow reference reproduces every selected-model OOF score
  with maximum absolute difference `0.0`.  Its 45 MB prediction stream has
  SHA-256
  `82c48787d877e1d9a492e3234798917b614a0171f98dfe5a7eeabad0cb990cd8`.
- Static and online artifact manifests verify as
  `b6d4231480d1ae5d596df16f08b36373d7559d835c41a087671497eae5d07721`
  and
  `4a023387681441c598deafd706e729dcee2eeae2d006292fb8f950cee9f3c4fd`,
  respectively.  The online primary JSON has SHA-256
  `7e0094efee52512e122b13d8252707e336060332fdd39bff36880e742c6aa074`.
  Both generated figures were visually inspected after checksum verification.
- Shadow-priced borrowing through `4a5bf74`: 7 focused borrow tests and 13
  online/static compatibility tests pass; `py_compile`, 88-column, diff, exact
  online-v1 reproduction, and clean-worktree provenance checks pass.  The
  canonical result and manifest SHA-256 values are
  `134e2632a02fc3979284700e3bd9531353c18d0fa6e4d27f6e1adea4b2d6f4bb`
  and `b21c2109f3d1d0c4ab50e17656302471560804997174a24975d25db81e301fcb`.
  Every source hash closes and the generated figure was visually inspected.
- Paired distribution adapter at `c33ed15`: the module target and both
  `wifi-streaming-temporal-t2-distribution-predictor` test cases passed.  The
  unchanged primary predictor suite and compiled distribution-model suite
  also passed.  The adapter preserves all 246 primary words, appends 62 exact
  current/lagged passive-secondary words, owns caller data, and rejects
  cadence, watermark, paired-capture, support, and exact-lag drift.
- Permanent airtime ledger at `49371b0`: both generated-credit test cases and
  the existing secondary-airtime primitive suite passed.  The public-state
  replay matches all four deployment goldens, including negative debt,
  positive-cap discard, horizon rejection, and stop-time repayment.  Accepted
  canonical reservations have no release or measured-airtime refund path.
- Distributional evidence validation at `34e9296`: 6 new focused tests, 21
  existing paired-validator tests, and 96 adaptive/randomized/analysis
  compatibility tests passed.  All 48 candidate runs passed strict validation
  remotely, covering 86,400 decisions and frame outcomes.
- Final qualification tooling at `544008c`: 41 focused analyzer, comparator,
  plotting-label, and generic analysis tests passed with `py_compile` and
  `git diff --check`.  The real analyzer freshly strict-validated all 96
  candidate/STR runs; the exact V2 comparison freshly validated all 48
  candidate runs and verified V2's checksum-bound archive and embedded strict
  report.
- Distributional raw archive SHA-256:
  `fe1fe1532655ca4d422612b1bbddb8e44869d94535286816941cbef0c3a6cb27`
  at 89,685,591 compressed bytes.  Complete same-build STR archive SHA-256:
  `9ff159b9ce2752da58834c7a3804bdcd52747b76f37c2c2f1bea5754a39038a1`
  at 5,755,083 bytes.  Both pass `zstd -t`.
- Final distributional 48-pair report: policy 0.5266% misses and 16.832 ms
  P99; STR 0.7998% and 18.875 ms; sender-airtime ratio 1.1662; background loss
  0.0050%; STR qualification `pass`, promotion readiness `fail`.
- Exact V2 comparison: primary-copy outcomes match on all 86,400 frames;
  candidate-minus-V2 miss interval `[-0.1181, 0.0255]` percentage points and
  P99 interval `[-0.687, -0.041]` ms.  V2 remains champion.  All five
  qualification plots, all eight historical plots, and the V2 comparison plot
  were visually inspected.

## Work log

### 2026-08-06 - Complete LOFO fitting and freeze 64-way qualification execution

- The corrected dataset stage published atomically from the closed 384-run
  collection: 307,689 action-clean T2 rows, all six families and 64 runs per
  family, with its source and artifact manifests present.  A compound-shift
  scenario has only 16 retained rows across four runs; carry that sparse-cell
  warning into uncertainty and coverage interpretation.
- All 12 separate-arm HGB64 leave-one-family-out fits completed.  The remote
  pipeline then advanced to the exact resource-policy replay.  No partial
  policy metrics were inspected, and the pipeline remains incomplete until
  plots and `analysis_pipeline_manifest.json` are atomically published.
- Froze and committed `257b194`, which mechanically binds the predeclared 48
  held-out scenarios to STR, score-aware V2, and distributional-shadow T2.
  The matrix contains 192 paired units and 576 simulations, exactly nine full
  waves at 64 workers, using seeds `21001` through `21192` and none of the
  reserved `1301` through `1348` confirmation seeds.
- The generator validates source hashes, inherited V2 closure, arm identity,
  exact pairing, worker-wave arithmetic, and seed isolation; 45 focused and
  compatibility tests pass.  The campaign is frozen but not launched.  Build
  and freeze the outcome analyzer after the randomized analysis closes and
  before any held-out qualification result can be read.

### 2026-08-05 - Close the 384-run collection and repair dataset missingness

- Completed all 384 randomized runs as six full 64-worker waves on the VM.
  Every manifest entry is `complete`, no simulator zombies remain, and the
  final experiment-manifest SHA-256 is
  `b306ea8384f99413834760978a2ef76fb9969f35df2cd8ef0930aed3565652c9`.
- The frozen `543f6c1` pipeline stopped before model fitting when a valid raw
  row had jointly null `packets_ahead_of_frame` and
  `mac_service_bytes_ahead_of_frame`.  The telemetry contract permits this
  state when exact FIFO ordering is unavailable; the temporal physics
  augmenter had incorrectly required the byte field to be finite.
- Fixed the augmenter in `916bb9a` by retaining the frame and propagating the
  null only to the two ahead-clearance derivatives.  No outcome-dependent
  row filtering or feature imputation was introduced.  Sixty-five focused
  local tests and sixteen focused VM tests pass, including a paired-null
  regression.  The pushed source bundle SHA-256 is
  `2e65421e7dd0f81aa1e231f4d7e6a9544427da2edb469a26776a0e81057382a8`.
- Relaunched the source-closed dataset-to-plots pipeline from the clean
  detached `916bb9a` checkout into a fresh output root.  Preserve the failed
  `543f6c1` output and log as an audit trail.  Do not interpret policy results
  until the new top-level artifact manifest closes all four stages.

### 2026-08-05 - Complete pre-outcome LOFO and resource replay tooling

- Froze the held-out-family completion-distribution and robust OOD contract at
  `76c6872`, then added the source-closed 313-feature loader, deterministic
  scenario folds, and group-OOF shrinkage-Mahalanobis calibration through
  `f519710`.
- Implemented separate-arm HGB64 completion CDFs, exact class alignment and
  rare-class smoothing, equal family/scenario/replicate diagnostics, row-level
  checksum-closed predictions, and OOD fallback output in `82ca34e`.
- Froze policy replay before reading randomized outcomes in `16d0e38`.  The
  contract SHA-256 is
  `8f797ac303025e0451288d92d8e171bbd8a3f3b333b9e650c3b0bb8b4a92ed69`;
  it defines no-copy, 64 deterministic uniform replays, myopic primary risk,
  and the nondeployable cross-fitted resource oracle under exactly 372,000 us
  per run.
- Added exact decimal two-cost optimization, source-closed prediction joins,
  known-propensity DR deadline value, HT completed-late18 value, and real
  preflight resource replays in `06d6a81`.  Added the shared stratified
  10,000-sample scenario/run bootstrap, confidence-bound gates, partial-ratio
  identification, family values, and row-action archive in `e6ddcf3`.
- Forty-nine related policy, LOFO, OOD, dataset, generator, and SplitMix64
  tests pass.  The six real preflight datasets also satisfy exact cost and
  372,000-us resource bounds.  The last metadata-only VM poll showed 245/384
  collection runs complete.  No randomized outcome file has been opened.
- Added five checksum-closed plots in `5bfc082`, the frozen OOD-aware 1%/1%/
  0.2% deployment-exploration wrapper in `e04093f`, and the clean-worktree
  dataset-to-plots VM entry point in `3dd6855`.  The entry point resolves only
  the exact 384 complete manifest directories and closes every stage manifest
  into one top-level provenance record.

### 2026-08-05 - Pass the scenario preflight and launch the 64-way collection

- Reproduced the generalized-scenario assertion under GDB.  A below-sensitivity
  Wi-Fi arrival refreshed interference CCA with a shorter duration while an
  active PPDU header was still being decoded, exposing `IDLE` before the
  scheduled field or failed-header cleanup.
- Preserved the active header/cleanup CCA reservation in `8c38753`.  Both the
  pending-field and failed-header paths have negative-control regressions; the
  exact radio replay and the `wifi-phy-reception`, `wifi-phy-cca`,
  `wifi-spectrum-phy`, and `wifi-streaming` suites pass.
- Built the fix on the 64-vCPU VM and ran all six frozen 60-second preflight
  scenarios concurrently.  Every run completed and passed an independent raw
  validation locally and remotely.  The fetched experiment-manifest SHA-256 is
  `d8201e660e352dfa742558a3e1dd723abcda1e3149600c865ad1435590ed8445`.
- Launched the 384-run randomized collection from project commit `8c38753`
  with `workers=64`; process inspection confirmed exactly 64 simulators.  Its
  resolved matrix SHA-256 is
  `eb20e7bdefc285b8c757dee946907ea8e45b2aa925c61fc4a9156a34a801ee59`,
  giving six complete 64-run waves.

### 2026-08-05 - Build the scenario-aware temporal dataset boundary

- Added a separate streaming builder in `af1f29c` so the historical
  single-environment builders retain their fail-closed design invariant.
  It rederives run IDs from the hash-verified matrix, joins scenario metadata
  from the complete experiment manifest, validates every raw run before source
  reads, and holds only one run in memory.
- Preserved the existing action-clean T2 temporal feature logic and appended
  only stream FPS, interframe bytes, GOP length, keyframe multiplier, and
  deadline.  Scenario ID, family ID, parameter sample, seeds, run IDs, and
  frame IDs are explicit non-features.
- Thirty-five related dataset, scenario, and generator tests pass.  Two fresh
  builds from the real six-family preflight are byte-identical and contain
  5,110 rows across all families.  The CSV, metadata, and artifact-manifest
  SHA-256 values are respectively
  `026078174c0772f8ebf906ab67592ca933ca01c48038be09ca38f46614302938`,
  `af728c8aa29906b67a82bff4f32e2ce2b87faf9bfb80d02aa15be47538446562`,
  and `469b647149048d40f56e8ed815d149dcb6edccb2914505779091aac539bed4dc`.

### 2026-08-05 - Freeze environment-generalization scenarios

- Separated optional plotting imports from deterministic matrix expansion in
  `aeaafb3`, allowing config generation and identity checks in a minimal
  Python environment without changing run-time plotting behavior.
- Froze six scenario families, observable-only model context, LOFO splitting,
  OOD fallback, logged exploration, regret targets, resource gates, and the
  actual STR/V2/distributional-shadow comparison before reading held-out
  results.
- Expanded the randomized collection to 384 runs and the three-arm held-out
  comparison to 576 runs, filling six and nine complete 64-worker waves.  The
  six-run preflight remains intentionally small.
- Added deterministic SHA-256 Latin-hypercube generation, exact categorical
  balancing, derived frame-period deadlines, seed and parameter-range
  isolation, keyframe/config/CLI validation, atomic generation, and
  byte-for-byte `--check` in `10863e8`.
- Generated and checksum-closed both executable matrices, the unopened
  all-phase catalog, and the artifact manifest.  Thirteen focused tests,
  Python compilation, diff checks, exact regeneration, and the
  `streaming-experiment` target build passed.  Next run the six scenarios on
  the VM and repair only real feasibility or validation defects before the
  full randomized collection.

### 2026-08-05 - Qualify distributional shadow T2 closed loop

- Completed the compiled controller, runtime wiring, independent model and
  ledger replay, and strict validation through `34e9296`; pushed each clean
  implementation/validation boundary before the campaign analysis.
- Recovered all 48 distributional runs and generated a complete same-build
  48-run STR arm on the 64-vCPU VM without reading reserved seeds.
- Strictly validated all 96 runs, reconstructed exact HF7 P99, miss, sender
  airtime, and background metrics, and passed every frozen STR gate.
- Diagnosed the promotion failure: 96.04% conditional rescue remains strong,
  but the candidate spends 3,392 extra actions versus V2 for only 38 extra
  captured primary misses.  The miss gain over V2 is not statistically
  resolved; V2 stays champion.
- Added and pushed the strict analyzer, exact V2 action comparison, paired
  heterogeneity, qualification figures, and historical CDF/PDF/burst plots
  through `544008c`.
- Archived the compact evidence and raw-archive identities under
  `key_experiment_results/15_distributional_shadow_t2_str_engineering_v1`.
  The next boundary is a frozen environment-generalization pipeline with
  held-out-family value/regret tests, OOD fallback, and logged exploration.

### 2026-08-05 - Isolate permanent borrow-and-repay accounting

- Added a policy-independent ledger that causally refills positive balance,
  permits only stop-repayable debt, permanently debits a canonical reservation
  after launch, and tracks minimum balance, peak debt, generated/discarded
  refill, total debit, and final closure.
- Reproduced all four generated deployment credit transitions through public
  state rather than duplicating their formulas in the test: initial action,
  debt action, positive-cap saturation, and horizon-credit rejection.
- Verified invalid mutation and timestamp transitions fail closed while bad
  admission queries remain nonmutating.  The API intentionally has no refund
  or settlement method; the measured meter will remain independent evidence.
- Passed `wifi-streaming-permanent-airtime-credit-ledger` and the unchanged
  `wifi-streaming-secondary-airtime-primitives` suite, then committed this
  accounting-only boundary as `49371b0`.

### 2026-08-05 - Compile the paired distribution feature adapter

- Added `TemporalT2DistributionPredictor` as a separate paired-history owner;
  the proven 246-feature primary adapter is reused without reinterpretation
  and 62 current/lagged passive-secondary queue/PHY features are appended in
  the compiled model's exact order.
- Closed two implementation-time contract gaps before controller wiring:
  secondary polling must remain on the frozen 1 ms cadence, and primary and
  secondary endpoints must carry the same delayed-report capture and
  availability times used by the training augmenter.
- Added monotonic secondary live/polling watermark checks, exact support-mask
  enforcement, untreated-current-frame checks, exact lag-1/3/8 ownership,
  float32 word-order tests, caller-mutation tests, and direct evaluator parity.
- Built the module and passed the new paired-predictor suite plus the existing
  primary-predictor and compiled-distribution-model suites.  Committed this
  policy-neutral boundary as `c33ed15`; permanent-debit admission remains the
  next implementation boundary.

### 2026-08-05 - Validate shadow-priced future credit

- Froze the single borrow/repay mechanism at `ba4dfe9` before reading its
  result.  Predictor, reward, P-frame gate, shadow curves, congestion state,
  reservation, and total resource remain identical to the no-borrow replay.
- Added exact strict-baseline reproduction, repayable-credit accounting,
  decision-route outcomes, time-bin diagnostics, per-run debt closure, shared
  bootstrap deltas, and a four-panel plot through `4a5bf74`.
- Passed all five predeclared gates.  Congestion-aware capture rises from
  51.36% to 73.78%, with DR miss risk falling from 1.389% to 0.748% and every
  run staying below 372 ms of reservation and repaying by measurement stop.
- Proved the mechanism reallocates rather than merely spends more: 3,648
  borrow-only actions carry 17.21% primary-miss risk, versus 4.84% for 3,449
  displaced strict-only actions.
- Archived the checksum-closed machine evidence, generated report, and
  reviewed plot under
  `key_experiment_results/14_temporal_t2_shadow_borrow_repay_v1`.
- Advanced to compiled closed-loop integration on opened engineering seeds.
  Transient debt reaches 144.82 ms, so runtime contention and resource gates
  remain unproven.

### 2026-08-05 - Complete the distributional and online ceiling stage

- Completed the four-variant 96-group cross-fit on the 64-vCPU VM, verified
  every upstream and generated hash, and restored the 25 MB prediction stream
  locally without reading reserved confirmation seeds.
- Generated the exact two-cost static frontier, selected
  `primary_secondary_hgb64` under the frozen priorities, and established a
  79.47% direct primary-miss capture ceiling at 372 ms/run.
- Refit 16 fold/arm shadow-reference models, reproduced selected-model OOF
  predictions exactly, and generated the frozen global and congestion-aware
  nonclairvoyant replays.
- Established that congestion state helps modestly but both causal replays
  lose hundreds of primary-miss captures and underuse their budget because
  high-risk arrivals lack current credit.
- Archived the compact metrics, generated reports, reviewed plots, manifests,
  and omitted-stream identities under
  `key_experiment_results/13_temporal_t2_distributional_online_ceiling_v1`.
- Chose shadow-priced, repayment-enforced future credit as the next isolated
  mechanism.  This differs from failed V4 because future actions receive an
  explicit opportunity value before any debt is taken.

### 2026-08-05 - Launch the distributional and online ceiling stage

- Froze the six-bin completion-distribution screen, implemented four-variant
  eight-fold randomized T-learners, and added an exact canonical two-cost
  static frontier through `62bcf82`.
- Froze the nonclairvoyant allocator before reading the cross-fitted results,
  then implemented exact causal credit replay, global/congestion-regime shadow
  prices, direct miss capture, DR outcomes, and compact plotting through
  `1aeddc0`.
- Removed a subtle fold leak from shadow-price calibration.  Each selected
  outer model is now reproduced exactly and scores only its own 84 training
  groups; evaluation rows remain absent and reproduced OOF predictions must
  match the canonical artifact within `1e-12`.
- Corrected selection and tie semantics before results were available:
  completed-late18 is a binary nonregression gate before airtime cost, and
  equal score densities resolve by smaller frame ID before stable identity.
- Corrected the downstream DR estimand before results were available.  Run
  groups contain 202 to 1,170 action-clean rows, so point estimates now pool
  frames; each uncertainty replicate resamples whole runs and then divides
  pooled pseudo-outcome sums by pooled row counts.  The earlier equal-run
  average would have overweighted sparse runs.  This change does not affect
  model fitting or require a restart.
- Launched the canonical 96-group four-variant cross-fit on the 64-vCPU VM as
  PID `50959`, logging to
  `/home/jingweili/temporal-t2-distributional-crossfit.log`.  The remote
  checkout must remain detached at `1e47792` until this fit records its final
  provenance.  Next checksum-fetch its output and execute the already-frozen
  static, fold-honest reference, and online analyses.

### 2026-08-05 - Establish the closed-loop ceiling split

- Added and pushed a source-hash-bound analysis of factual V2/V5 behavior,
  per-run canonical score frontiers, perfect-primary-information frontiers,
  and V2/V4/V5 action-outcome support through `aa9a20d`.
- Corrected the pooled 309.83-miss sensitivity: average canonical cost is
  below 360 ms/run, but 26/48 individual runs exceed both the refill-only and
  finite-run proxies, so the calculation is not implementable.
- Established that V5's score cannot meet the 345-miss target inside either
  canonical proxy even under perfect rescue, while perfect primary
  information covers every eligible miss far inside the budget.
- Quantified the remaining identification gap: 232 eligible primary misses
  have no observed action outcome, and 18 observed outcomes differ across
  interfering policy arms.
- Archived the machine report, generated report, and reviewed figure under
  `key_experiment_results/12_temporal_t2_ceiling_decomposition_v1`.  The next
  boundary is cross-fitted completion distributions plus an implementable
  online allocator, not another scalar threshold.

### 2026-08-05 - Qualify and archive cost-free score-aware V5

- Completed the 96-run V5/STR campaign on the 64-vCPU VM using only opened
  seeds `1251` through `1298`; reserved seeds remain untouched.
- Fetched and checksum-verified the raw archive, restored it locally, and
  strictly validated all runs through both analysis and plotting.
- Generated the paired qualification figures and the historical
  CDF/PDF/deadline/burst/resource suite, then archived the compact evidence
  under `key_experiment_results/11_cost_free_t2_str_engineering_v5`.
- Added schema-aware V2/V5 action comparison and shared-bootstrap direct
  miss/P99 intervals through `a16bcb3`; established that V5 is null versus V2
  despite passing every STR gate.
- Kept V2 as the engineering champion while completing the exact ceiling
  decomposition and repayable-credit mechanism screen.  The selected
  distributional HGB64 model and reachable congestion reference are now
  compiled through `f233402`; the next boundary is paired-feature parity,
  followed by permanent-debit allocator integration.

### 2026-08-05 - Compile the distributional shadow runtime

- Froze the deployment refit and runtime accounting contract before fitting
  in `b6566c5`; reserved seeds `1301` through `1348` remain unread.
- Fit CONTROL and FULL_COPY_T2 six-class HGB64 heads on all 73,400 opened
  action-clean temporal rows.  The selected 308-feature family has 62 passive
  secondary queue/PHY inputs in addition to the exact primary temporal family.
- Reconstructed both heads independently with zero absolute difference from
  sklearn on the parity sample.  The full-data construction replay records
  15,792 actions, 1,550 observed primary-miss captures, 326.329 ms mean
  canonical reservation per run, 139.611 ms maximum debt, and nonnegative
  final balance in every run.  This is in-sample deployment construction, not
  independent performance evidence.
- Generated and compiled 384 class trees per arm, exact Dirichlet-smoothed
  completion CDFs, separate deadline-rescue and 18 ms tail outputs, all
  reachable congestion shadow-price prefixes, and borrow/repay goldens.
- Passed the exporter `--check`, 10 focused Python tests, module build, and
  `wifi-streaming-temporal-t2-distribution-model` C++ suite; committed and
  pushed the boundary as `f233402`.
- Retained the 31 MB full reference JSON under ignored local results for audit;
  the compiled policy embeds only congestion curves and only the credit prefix
  reachable at or below 372,000 us.  The next implementation is the paired
  feature adapter, not another predictor screen.

### 2026-08-05 - Export and preflight cost-free score-aware V5

- Added and pushed the raw legacy-bad12 runtime score and calibrated primary
  and emergency cutoffs as an isolated profile in `179a283`; V1 through V4
  behavior remains preserved.
- Added schema-V4 telemetry and independent replay of both the historical
  learned-cost-divided diagnostic score and the active raw float32 score, then
  pushed the validation boundary as `c61fa98`.
- Passed the focused and full Python suites, the focused and broad C++ suites,
  and the clean same-commit seed-43 preflight.
- Kept the learned cost head diagnostic-only and retained V2's conservative
  canonical airtime reservation.  The next action is the 48-pair campaign on
  opened seeds followed immediately by strict local validation and plotting.
- Reserved confirmation seeds `1301` through `1348` remain unopened.

### 2026-08-05 - Remove the harmful score cost divisor

- Added and pushed a strict frozen-bundle ablation as `a92bbeb`; it verifies
  every source artifact and reproduces the frozen source policy before
  considering cost-free scores.
- Added whole-run paired policy-delta uncertainty and pushed it separately as
  `8d8f246` after recognizing that separate policy intervals were inadequate
  for the modest estimated gain.
- Regenerated the output from a clean worktree, verified its manifest hashes,
  and plotted the paired calibration curves, objective plane, opened-test
  outcomes and intervals, and resource proxies.
- Established at a matched 15% action fraction that raw value improves both
  objectives while using less estimated airtime; selected raw bad12 value at
  16.5% as the next ranking baseline.
- Archived the compact evidence under
  `key_experiment_results/10_temporal_t2_cost_denominator_ablation_v1` and
  kept reserved confirmation seeds unopened.  The next boundary is causal
  secondary-path state in the treated-outcome model, not another guard change.

### 2026-08-05 - Falsify chronological remaining-refill borrowing

- Added and pushed the remaining-refill V4 guard primitive, runtime profile,
  frozen contract, and schema-V3 strict replay through `28c2b1b`.
- Ran the local seed-43 preflight, then completed all 96 opened-seed runs on
  the 64-vCPU VM.  Fetched and checksum-verified the 96.98 MB raw archive and
  independently reran strict qualification after local restoration.
- Generated the four paired qualification figures, all eight requested
  historical figures, and an exact V3/V4 admission-shift figure.
- Established that V4 still defeats STR but regresses from V2/V3 because
  earlier lower-score actions displace later higher-risk actions.  Rescue
  efficacy remains unchanged; predictor scores are exactly identical.
- Added and pushed the reusable action-transition diagnostic as `6fed1ca`,
  archived the V4 negative result under
  `key_experiment_results/09_remaining_refill_t2_str_engineering_v4`, and
  returned the next work boundary to predictor/ranker quality.  Reserved
  confirmation seeds remain unopened.

### 2026-08-05 - Falsify full-horizon stored-credit carry-over

- Added and pushed the isolated full-horizon V3 runtime and strict evidence
  profile through `ad16a84`, keeping startup credit at two seconds and all
  predictor/action/environment settings frozen.
- Strictly validated the local seed-43 preflight, then ran all 96 opened-seed
  simulations as a detached service on the 64-vCPU VM.  The campaign completed
  in about 13 minutes and consumed 8 h 59 min of CPU time.
- Fetched and checksum-verified the 95.5 MB raw archive, rebuilt the aggregate,
  and freshly strict-validated every run through both analysis and plotting.
- Generated the four qualification figures and all eight requested historical
  CDF/PDF/deadline/burst/resource figures.
- Proved the larger bucket changed guard state but not one admission or frame
  outcome.  Archived the negative result under
  `key_experiment_results/08_full_horizon_t2_str_engineering_v3`.
- Chose bounded borrowing against causally remaining refill as the next single
  admission intervention; reserved confirmation seeds remain unopened.

### 2026-08-04 - Establish and archive the first STR engineering victory

- Completed all 96 score-aware V2 simulations on the 64-vCPU VM.  Eleven
  validator-retained attempts were recovered without simulation reruns after
  the final exact feasibility validator accepted them.
- Reconstructed and checksum-bound the complete manifest, freshly strictly
  validated every raw run twice through analysis and plotting, and produced
  both the paired qualification figures and the standard CDF/PDF/burst suite.
- Established decisive paired improvements in all-generated miss rate and
  completed-frame P99 while passing the sender-airtime and background gates.
- Confirmed that score-aware emergency admission reverses V1's risk ordering:
  admitted candidates are materially riskier than rejected ones, with 737
  factual primary-miss rescues.
- Created the compact archive under
  `key_experiment_results/07_score_aware_t2_str_engineering_v2` and retained a
  checksum-bound 96-run raw archive outside Git history; committed the compact
  evidence as `d114f65`.
- Chose full-horizon causal carry-over as the next single-variable guard
  experiment on already-open engineering seeds.  Reserved confirmation seeds
  remain untouched.

### 2026-08-04 - Implement and preflight score-aware admission V2

- Added a fail-closed debt-limited query to the measured-airtime guard and a
  distinct score-aware runtime profile; preserved V1 output byte-for-byte.
- Froze the emergency score and one-bucket debt bound in a checksum-bound
  contract, with schema-V2 telemetry distinguishing strict and emergency
  admissions.
- Added strict evidence replay for the new admission tiers and corrected its
  expected JSON-decimal representation without weakening float32 identity.
- Pushed four clean implementation/validation commits through `24a5774`.
- Completed the same-commit seed-43 preflight.  The encouraging miss/P99
  points justify the fresh engineering campaign, while the one-seed airtime
  ratio prohibits promotion without the full resource analysis.

### 2026-08-04 - Analyze and archive the temporal-T2 qualification

- Recovered the nine validator-only false rejections, reconstructed a
  checksum-bound 96-run manifest, and strictly revalidated every run.
- Fixed portable acceptance of valid rounded MILP witnesses and pushed it as
  `6b822a4`.
- Established that the candidate passes resource targets but fails both STR
  performance gates on 48 matched pairs.
- Reconstructed the admission funnel and primary-copy counterfactual.  The
  launched secondary copy rescues 97.64% of acted primary misses, while the
  chronological guard rejects a higher-risk population containing 349
  primary misses.
- Added the standard CDF/PDF/burst suite and paired qualification figures,
  then hardened plot generation to rebind the expanded matrix and freshly
  strict-validate all 96 raw runs through `d78c288`.
- Chose score-aware measured-airtime admission as the next intervention;
  retained seeds `1301` through `1348` for final confirmation.

### 2026-08-04 - Complete VM simulations and repair strict replay

- Built clean `da48d7d` on the 64-vCPU VM and ran the frozen 48-pair matrix
  with 64 workers.  The simulations produced 87 promoted runs and nine
  preserved validator-rejected paired attempts.
- Fetched and checksum-verified all nine attempts.  Eight differed only in
  the ridge cost log by up to 12 binary64 ULPs because NumPy changed the
  reduction order; the compiled scalar order reproduces every row exactly.
- Proved the remaining rejection was an unlogged shared-PPDU split: the
  controller's equal-byte allocation satisfied its checkpoint, while the old
  max-flow chose a different extreme allocation with the same final marginals.
- Replaced the arbitrary max-flow with one joint integer-byte feasibility
  witness.  A release audit found and caused fixes for continuous byte splits
  and loose solver acceptance before commit.
- Passed the focused, compatibility, preflight, nine-real-attempt, compilation,
  diff, and independent audit checks recorded above; committed and pushed as
  `575f171`.

### 2026-08-04 - Complete fresh same-commit local preflight

- The first fresh preflight completed both simulations but rejected the T2
  run because 224 event rows serialized at 12 significant digits accumulated
  `1.397e-9 us` of difference from the max-precision controller total.
- Added a sum of per-row half-unit quantization bounds while preserving tests
  that reject a `0.0001 us` mutation; committed and pushed as `bc76a13`.
- Started a new output root because the project commit changed; did not reuse
  the older STR artifact.
- Both `bc76a13` arms completed and passed an explicit validator invocation
  with exact project/ns-3 commits.  The preflight used seed 43 only, not an
  engineering or reserved confirmation seed.

### 2026-08-04 - Complete strict paired-T2 run validation

- Replayed the canonical sklearn model from independently reconstructed 246
  primary-only temporal features for every evaluated decision.
- Added exact policy/config/source, decision, summary, guard, meter, and
  launch/settlement causality validation.
- Fixed the final max-flow fail-open by imposing a positive lower bound on
  every logged event/frame edge.
- Reused the generic exact CSV path and canonical full-copy descriptor
  arithmetic instead of retaining parallel implementations.
- Passed the focused, compatibility, retained-artifact, compilation, and diff
  checks recorded above; committed as `3b8984f`.

### 2026-08-04 - Detect and stop nested validator work

- Compared the uncommitted validator with history from `a829356` through
  `ba59751`.
- Confirmed there is no earlier committed paired-T2 output validator and no
  duplicated campaign run.
- Found avoidable plumbing overlap with `96b07bd` (paired telemetry),
  `5f141d7` (randomized intervention validation), `ce930a3` (canonical airtime
  estimator validation), `42395ba` (settlement rounding), the runner runtime
  contract validation, and the qualification analyzer source closure.
- Stopped nested audits and reduced the execution plan to the three checklist
  items above.
- A final independent audit found the remaining zero-allocation max-flow issue
  before the validator was committed.

### 2026-08-04 - Complete qualification infrastructure

- Added and pushed the 48-pair qualification matrix, clean-worktree/runtime
  closure, canonical model artifacts, and strict analyzer through `ba59751`.
- Reserved seeds `1301` through `1348` for final confirmation.
- Deferred experiment launch until the run-level validator is fail-closed.

### 2026-08-04 - Complete compiled temporal-T2 policy

- Exported the frozen model and exact feature adapter.
- Added the C++ predictor, paired controller, measured-airtime guard, and
  executable policy integration through `fe9bd9f`.

## Resume protocol after context compression

1. Read `AGENTS.md`, `RESEARCH_MEMORY.md`, and this file.
2. Run `git status --short` and `git log -5 --oneline --decorate`.
3. Reconcile the actual commit/worktree/artifact state with the current work
   boundary and checklist above; correct this file if it is stale.
4. Continue the first unchecked checklist item.  Do not reopen a completed
   milestone or rerun ledger checks unless relevant code changed.
5. Update this file after a clean commit, experiment launch/completion,
   material scientific decision, blocker, or context compression.  Record the
   commit ID or artifact path whenever one exists.
