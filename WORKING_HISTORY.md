# Wi-Fi streaming working history

This file is the authoritative execution handoff for the current research
work.  It records what is complete, what is in progress, and what must happen
next.  Read it after `AGENTS.md`, `RESEARCH_MEMORY.md`, and
`EXPERIMENT_HOSTS.md`, especially after a context compression or a new agent
session.

Keep this file factual.  Git, test output, and experiment artifacts remain the
source of truth; reconcile this document with them before continuing work.

## Stable objective

Improve selective duplication until an engineering candidate decisively beats
STR MLO in the unchanged neutral mixed-4x4 environment and demonstrates value
across held-out environment families, then qualify the frozen candidate on
untouched confirmation seeds.

Unless access-category behavior is itself the declared treatment, compare
against STR with target video mapped to the standard WMM video access category.
The historical neutral campaigns used best-effort target traffic and must be
labeled accordingly.

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
- The scenario-15 WMM ablation also reuses opened seeds `1251` through `1298`.
  It compares all three arms in one current build with target CS0 / AC_BE and
  target CS5 / AC_VI; background traffic remains CS0 / AC_BE.
- Reserved final-confirmation seeds: `1301` through `1348`; do not consume
  them during engineering.
- STR uses `NMaxInflights=1`.  Earlier results show MLO collapses at `2`, so it
  is not a tuning dimension for this campaign.
- Experiment hosts: two independent 64-vCPU VMs are directly reachable at
  `jingweili@10.120.16.105:30022` and
  `jingweili@10.120.17.30:30022`.  See `EXPERIMENT_HOSTS.md` for identities,
  storage, forwarding, and distributed-campaign rules.
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
| Freeze held-out qualification execution | `257b194`, `06806cb` | Exact 576-run three-arm matrix and resolved hash in nine complete 64-worker waves |
| Record randomized/qualification estimand boundary | `5d6565d` | Keep eligible-row oracle evidence separate from all-generated closed-loop gates |
| Handle empty eligible-run support | `40b6be1` | Condition policy value on 383 represented runs while retaining all 384 runs for resource accounting |
| Resume verified environment analysis prefix | `60c03f3` | Rehash and reuse only the completed dataset and LOFO stages after a later-stage failure |
| Archive randomized environment replay | `c0085b0` | Checksum-closed six-family LOFO, OOD, resource-ceiling, and five-figure evidence |
| Freeze held-out qualification analysis | `101f132`, `ab9e008` | Exact hierarchical gates, strict 576-run closure, and eleven checksum-bound statistical/historical plots |
| Restore temporal source closure | `a6ac26e` | Historical V2 builder bytes plus exact archived/current generalization source profiles |
| Admit held-out temporal profiles | `616caff` | Explicit workload profile in runtime output and independent validation |
| Amend held-out qualification runtime | `147b1b2` | Failed-root exclusion, repaired matrix, and 144-configuration fail-fast preflight |
| Repair generalized qualification execution | `a33d2c2`, `d4a55e6`, `648a56a`, `28b77ce` | Stale-ACK handling, event-time debt replay, normalized PHY histories, and generalized frame/MPDU contracts |
| Launch repaired held-out qualification | `de49f8b` | Two strict generalized canaries followed by one clean 576-run, 64-worker campaign |
| Repair solver-version portability | `e7a8b3e` | Presolve retry with unchanged feasibility constraints and independent witness replay |
| Isolate concurrent solver timeouts | `2e2b2c6` | Fresh bounded budget for every component and presolve representation |
| Record exact meter allocations | `09e148f` | V2 per-frame byte/allocation evidence and direct replay without a latent solver |
| Freeze exact meter event schema | `60c78c6` | Ordered V2 CSV contract with historical V1 compatibility |
| Launch exact-meter held-out qualification | `47e1996` | Fresh 576-run V2/distributional/STR campaign with V2 event replay |
| Bound derived replay roundoff | `5ca913a` | Accept only the summed source-probability tolerance while preserving exact decisions |
| Preserve the 568-run evidence prefix | `441e4e2` | Balanced exploratory analysis and plots captured before retry |
| Analyze complete reliability | `565d9a2` | All-generated outcomes retained despite unsupported completed-P99 runs |
| Plot complete reliability evidence | `694ce9a` | Ten visually reviewed statistical and historical figures |
| Archive held-out qualification | `b0c7aad` | Complete result, exact recovery, checksums, partial evidence, and excluded supplement |
| Analyze valid mechanism prefix | `e06796f` | Strict balanced four-arm pre-fix analysis over all 20 paired units |
| Archive valid mechanism prefix | `cb42b50` | Seven-figure partial evidence and persistent failure diagnosis |
| Validate coded frame completion | `280b18b` | Exact source-plus-innovative-symbol completion accounting and mutation tests |
| Recover mechanism attempts | `fb26a4b` | Hash-bound no-rerun promotion and oracle-only continuation gates |
| Archive factual mechanism panel | `1d19fd2` | Protected five-arm result after excluding every flawed V1 oracle replay |
| Diagnose failed repair oracle | `02f5f7b` | Complete 20-pair deadline-semantics diagnosis and checksum-bound failure artifact |
| Freeze deadline-correct oracle V2 | `fcb8474` | Finalization-independent repair plans, unchanged binary, and strict paired replay gates |
| Analyze deadline-correct repair | `0196788` | Mixed-source 120-run closure, paired uncertainty, resource gates, and eleven figures |
| Bound repair subset resources | `65b2dbb` | Explicit post-result optimistic sensitivity without treating it as policy evidence |
| Archive corrected mechanism gate | `fef14a6` | Checksum-bound replay, subset ceiling, durable conclusions, and stop boundary |
| Add configurable target MCS | `ab05eaa` | Backward-compatible fixed mode and isolated adaptive Minstrel streams |
| Archive adaptive-MCS qualification | `823ea7f` | Strict 575-run adaptive result and matched fixed/adaptive comparison |
| Add target WMM video priority | `f08596e` | Configurable CS0/AC_BE and CS5/AC_VI target mappings with background unchanged |
| Freeze and preflight WMM comparison | `bb0bb61` | Six-cell, 288-run opened-seed contract and same-build executable validation |
| Analyze scenario-15 WMM comparison | `b2e5665` | Strict 288-run paired analysis, background accounting, and twelve figure pairs |
| Refine scenario-15 WMM figures | `0be0c64` | Unobscured legends and visible isolated-miss burst evidence |
| Add explicit OBSS WMM profiles | `29417c4` | AF41 target mapping and deterministic BE, one-VI-per-channel, and all-VI competitor profiles |
| Freeze WMM realism matrix | `d9867b1` | Four-profile, three-arm, 120-run opened-seed screen split across both VMs |
| Analyze WMM realism matrix | `bccb287`, `72b21fa` | Strict paired analysis, thirteen figure pairs, and corrected figure-manifest provenance |

The latest post-outcome validator correction is `5ca913a`.  Event schema V2
still replays exact per-frame tagged bytes and binary64 allocations without a
latent solver.  The correction only bounds subtraction roundoff by the sum of
the independently allowed source-probability errors; the affected run had
identical float32 score, gate, and action.  All 576 final runs pass strict
validation.  The neutral-environment V2 engineering win does not generalize
to this held-out population, so no policy is promoted.

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
- [x] Build environment-level generalization qualification before final
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
  published on the VM.  The first exact resource-policy attempt stopped
  before publishing outcomes because one source run has zero eligible rows;
  the pre-outcome support amendment and verified-prefix resume path are
  complete through `40b6be1` and `60c03f3`.  The resumed analysis closed,
  was independently rehashed, visually reviewed, and archived in `c0085b0`.
  The resource ceiling fails both targets, while myopic risk realizes 92.55%
  of its gain.  The frozen LOFO predictor/OOD
  path, exact policy/oracle/regret analyzer, OOD-aware exploration wrapper, and
  single-entry remote pipeline are complete through `3dd6855`.  Fetch,
  checksum, interpret, plot, and archive the analysis only after its top-level
  manifest closes.  The held-out V2/distributional-shadow/STR execution matrix
  was initially frozen at `257b194` as 576 simulations in nine full 64-worker
  waves.  The first `ff6d8b8` launch was stopped after execution metadata
  exposed a canonical-frame-only runtime guard and validator; 85 canonical
  directories, 226 retained attempts, 40 immediate aborts, and 64 in-flight
  processes form an excluded audit root, not qualification evidence.  No
  performance outcome was inspected.  The explicit held-out frame profile,
  historical model-source restoration, exact source-profile amendments,
  repaired execution contract, and 144-configuration preflight are complete
  through `147b1b2`.  The second `d66313b` audit finished all 576 attempts but
  retained only 335: 134 process failures and 107 strict-validator rejections
  exclude the other 241.  No performance outcome was inspected, and the 335
  retained runs cannot be selected or combined with another build.  Commits
  `a33d2c2`, `d4a55e6`, `648a56a`, and `28b77ce` repair the observed stale-ACK,
  debt-replay, fragmented-PHY, generalized-frame, numeric-reconciliation, and
  variable-final-MPDU failures.  Their focused suites and exact canonical and
  generalized full-run replays pass.  Both excluded audits and these repairs
  are bound into the frozen contracts.  The exact `de49f8b` VM checkout passed
  the full `wifi-streaming` suite, all 144 unique configuration checks, and two
  strict end-to-end canaries on the 60 fps, 8,200-byte held-out workload.  Its
  fresh campaign was stopped after 121 retained runs when SciPy 1.11.4 falsely
  rejected one exact mixed-integer reservation component.  One diagnostic
  error-log read exposed only packet, byte, background-byte, and finalized-frame
  aggregates; no deadline, latency, policy-comparison, threshold, or gate
  outcome was inspected or changed.  Commit `e7a8b3e` retries the identical
  formulation with presolve enabled and independently verifies any returned
  witness.  The exact failed attempt and 39 focused remote tests now pass under
  SciPy 1.11.4.  Commit `3bf5bb8` binds this third excluded root and repair into
  both frozen contracts.  Its fresh VM checkout passes the exact failed-attempt
  replay, 52 focused Python tests, the C++ module suite, all 144 configuration
  checks, and five full strict canaries.  Its campaign was stopped after 121
  retained runs when that same preflight run exhausted a single 30-second
  validator budget shared across all components and solver representations
  under full load.  It passes unchanged in isolation.  Commit `2e2b2c6` gives
  each component/representation a fresh 60-second operational budget without
  changing the formulation, tolerances, or witness replay; 64 concurrent exact
  replays pass in 66.94 seconds.  Commit `9196eef` binds this fourth excluded
  root and repair.  Its exact clean VM checkout passes the preserved attempt,
  53 focused Python tests, all 144 frozen configurations, and five full strict
  canaries.  That fifth audit stopped after 175 retained runs when run
  `d24c6d9645e15542184f` exposed a missing-evidence defect in V1 meter events:
  the logged PPDU total and frame IDs did not contain the actual per-frame byte
  split, and the latent MILP returned a witness outside the independent 1e-9-us
  envelope.  No performance outcome was inspected.  Commits `09e148f` and
  `60c78c6` record and freeze exact V2 allocations.  Commit `47e1996` binds
  the fifth exclusion and launches the sixth campaign.  Before any repair,
  its 568 strictly retained runs were fetched, hashed, analyzed, and plotted.
  Commit `5ca913a` fixes the one false replay rejection; the already complete
  attempt was promoted and only seven interrupted original run IDs were rerun.
  All 576 frozen runs then passed strict validation.  Complete reliability
  analysis and figures are frozen through `565d9a2` and `694ce9a`, and the
  checksum-closed result is archived at `b0c7aad`.  V2 and distributional both
  lose decisively to STR on all-generated misses; completed-frame P99 is
  `not_assessable` because 28 valid runs have fewer than 100 completions.
  This pipeline is complete and paused before a new scientific iteration.
- [x] Complete the frozen packet-repair mechanism gate before training another
  predictor.  Compare identical-seed STR, primary-only, full copy at T0 and
  T2, privileged eventual-missing packet repair at T2, and 12.5% ideal
  systematic repair across legacy p17, compound p19, OBSS-intensity p17/p19,
  and radio p17.  Use all-generated deadline misses and deadline-censored
  latency as the stable outcomes; completed-frame P99 is descriptive only.
  Runtime primitives, strict telemetry validation, the pre-result contract,
  and the two-stage sharded runner are complete through `791bb2d`.  Phase 1
  finished all 100 simulations: all 80 non-FEC runs and one non-exercising FEC
  run were promoted, while 19 complete coded FEC attempts hit a generic
  source-packet completion invariant.  Before changing validation, all 81
  promoted outputs and all 19 attempts were retrieved.  The clean `e06796f`
  prefix analyzer strictly validated and plotted the balanced 80-run,
  four-arm panel; compact evidence is under
  `key_experiment_results/18_t2_repair_mechanism_v1/partial_pre_fix`.
  Coded-completion validation is corrected at `280b18b`; all 19 preserved
  attempts pass.  The hash-bound recovery/oracle-only runner at `fb26a4b` was
  rehearsed on copies, then promoted 10 and 9 attempts in place without a
  simulation rerun.  Each shard now contains all 50 phase-1 runs.  The 20
  oracle simulations also finished; all 120 run trees were retrieved before
  diagnosis.  All 100 factual runs pass strict validation and their protected
  five-arm result is archived under
  `key_experiment_results/18_t2_repair_mechanism_v1/factual_phase1`.  Both
  oracle shard closures fail because primary outcomes first drift in compound
  seeds 21173 and 21174.  The complete strict diagnostic at `961c14d` shows
  that all 20 pairs are affected: lazy receiver-state creation omits one
  deadline-late packet from 12,456 repair plans.  The checksum-bound failed
  replay is archived under
  `key_experiment_results/18_t2_repair_mechanism_v1/oracle_pair_diagnostic`.
  V1 oracle evidence is rejected.  The minimum deadline-correct,
  finalization-independent V2 replay is frozen at `fcb8474`; it preserves the
  exact original executable and all 100 factual arms and reruns only 20
  corrected repair arms.  All 20 finished, passed strict pair closure, were
  retrieved, and entered the clean `0196788` analysis.  Deadline repair cuts
  misses from 31.7556% for STR to 17.6167%, but consumes 1.5721x sender
  airtime and therefore fails both the equal-airtime and 1.20 gates.  Its
  report and eleven figure pairs are copied into the key-result archive.  The
  separately labeled optimistic subset sensitivity through `ea3559a` also
  fails: pooled noncausal selection at 1.20 projects 11,827 misses versus
  STR's 11,432, and the optimistic minimum ratio to beat STR by one miss is
  1.2051 before fixed overhead.  The complete diagnostic is archived beside
  the main result.  Stop before redesigning the action or training a model.
- [x] Repeat the held-out qualification as a controlled target
  MCS ablation.  Preserve all 48 scenarios, 192 paired units, three arms,
  seeds, policies, model artifacts, and resource accounting; change only
  `wifi.mcs_mode` from its legacy fixed default to adaptive Minstrel-HT.  Keep
  adaptive-manager RNG streams isolated from the legacy PHY/MAC/background
  stream layout.  Run two intact 288-run shards, retrieve and strictly
  validate all outputs, plot fixed-versus-adaptive all-generated reliability
  and survivor-conditioned latency, archive a simple comparison, then stop
  for user discussion without beginning another iteration.  Shard 0 promoted
  all 288 runs; shard 1 promoted 287.  The missing compound-p22 seed-21188
  adaptive STR run deterministically aborts twice in native ns-3 association
  scanning at the identical simulated time.  Its complete three-arm unit is
  transparently excluded from both MCS modes; all 575 promoted adaptive runs
  freshly pass strict validation and 191 matched units enter final analysis.
  The final report and ten PNG/PDF figure pairs are archived under
  `key_experiment_results/19_environment_generalization_adaptive_mcs_v1`
  through `823ea7f`.
- [x] Add a controlled WMM video-priority treatment in the earlier neutral
  scenario-15 environment.  Keep fixed MCS and every other scenario, policy,
  seed, and resource setting unchanged; compare STR, V2, and Distributional
  with target CS0 / TID 0 / AC_BE and target CS5 / TID 5 / AC_VI while
  background remains AC_BE.  Both 144-run shards completed with no failures
  or replacement seeds.  All 288 runs pass fresh strict validation and the
  shared 10,000-by-48 paired bootstrap.  WMM reduces STR misses from 691 to 2
  and P99 from 18.875 ms to 6.070 ms.  V2 and Distributional reach zero misses
  but are about 2.22 ms slower than STR and use more airtime.  Archive the
  source manifests, raw identities, reports, tables, and twelve figure pairs
  under `key_experiment_results/20_scenario15_wmm_comparison_v1`, then stop
  for user discussion.
- [x] Run the four-case WMM realism matrix requested after that favorable
  ablation: target BE versus BE; target AF41/VI versus BE; target AF41/VI with
  one deterministic VI competitor per channel; and target AF41/VI with all
  latency-sensitive competitors VI.  Compare STR, V2, and Distributional on
  ten identical opened seeds while preserving fixed MCS and every non-WMM
  setting.  Both 60-run shards completed without failures or replacement
  seeds, and all 120 runs passed fresh strict validation.  In the all-VI case,
  V2 and Distributional each reduce STR's 33 misses to 4 at 1.090x and 1.113x
  sender airtime, but neither has an obviously lower P99.  All eight policy
  rows for the four residual events are identical, unacted startup frames.
  Archive the raw identities, reports, tables, diagnostic, and thirteen figure
  pairs under `key_experiment_results/21_scenario15_wmm_realism_matrix_v1`.
- [ ] Repeat that exact 120-run WMM realism matrix with 1.5 times the OBSS
  offered traffic.  Multiply every ON-period UL rate bound from 0.5--3 to
  0.75--4.5 Mbps and every DL bound from 2--8 to 3--12 Mbps while preserving
  the 32 flows, ON/OFF timing, RNG streams, seeds, fixed MCS, WMM profiles,
  policies, models, and resource accounting.  Run the 12-cell full-workload
  preflight, then one intact 60-run shard on each VM.  Retrieve and strictly
  validate all runs; compare each arm with STR at 1.5x load and compare every
  cell directly with the identical-seed 1.0x result.  Plot, archive, document,
  commit, and push the result without using seeds 1301 through 1348.

Do not replace this checklist with nested planning lists.  Add a new top-level
item only when the research objective genuinely changes.

## Current work boundary

The user authorized one direct load-sensitivity repeat of the completed WMM
realism matrix.  The new frozen contract is
`experiments/model-selection/scenario15-wmm-realism-bg150-matrix-v1.json`,
SHA-256 `a74cd5678e68d4152ced46c1b0b664c5d8005b5854cee8fb7d73d0fef656d80e`.
It reuses the same four WMM profiles, three arms, seeds 1251--1260, fixed MCS,
models, topology, flow count, duty cycle, geometry, and random streams.

The sole simulation treatment is 1.5 times the offered ON-period rate for
every OBSS flow: UL Uniform(0.5, 3) becomes Uniform(0.75, 4.5) Mbps and DL
Uniform(2, 8) becomes Uniform(3, 12) Mbps.  This means 50% more offered
traffic, not a promise of 50% more achieved goodput after contention.  A
closure test proves that all 120 expanded configurations differ from the
completed baseline only in those four rate bounds.  The 12-run preflight and
two 60-run shard configurations preserve complete paired units and do not use
reserved seeds 1301--1348.

Next, commit this frozen boundary, deploy the exact commit to both VMs, and
run the full-workload preflight.  In addition to strict output validation,
verify that its per-period generated rates are exactly 1.5 times the retained
same-seed baseline before launching the formal shards.  No outcome has been
observed for the new treatment yet.

### Superseded packet-repair boundary

The user reviewed the failed held-out qualification and authorized one small
action-mechanism gate before any further predictor work.  The current boundary
is the frozen six-arm `t2_repair_mechanism_v1` campaign at `791bb2d`, not a
larger model or dataset.  It uses 20 opened qualification scenario/seed units,
keeps all six arms for a unit on one host, and does not touch seeds `1301`
through `1348`.

Shard 0 service `wifi-t2-repair-mechanism-shard0-791bb2d.service` and shard 1
service `wifi-t2-repair-mechanism-shard1-791bb2d-retry1.service` have stopped
at the phase-1 boundary.  The clone's first service had previously been
stopped by logind ten seconds after SSH logout because `Linger=no`; it
produced zero promoted runs.  Enabling linger as the same unprivileged user
fixed that launch-lifetime defect.  Both VMs used clean commit `791bb2d` and
identical executable SHA-256
`ad595359e594f12e238ab74aca1889c15b241fc3adf49e6ead95beb8485b507d`.

The original stable phase-1 evidence consisted of 80/80 promoted non-FEC
runs, one promoted FEC run that did not need coded completion, and 19 complete
FEC attempts rejected by the old generic invariant `complete frame lacks
unique packets`.  Those exact trees remain locally retained under
`results/t2_repair_mechanism_v1/remote_prefix` and
`results/t2_repair_mechanism_v1/failed_attempts`; the balanced pre-fix analysis
at clean commit `e06796f` is archived under
`key_experiment_results/18_t2_repair_mechanism_v1/partial_pre_fix`.

Commit `280b18b` now requires `unique source receipts + innovative ideal-MDS
symbols >= source packet count` for FEC completion, reconciles exact source
counts with the packet-outcome sidecar, and retains the all-source-packets
rule for every non-FEC arm.  All 19 untouched attempts, the promoted FEC run,
51 relevant Python tests, and the C++ mechanism-controller suite pass.
Commit `fb26a4b` adds transactional tree-hashed promotion, a phase-1-complete
oracle-only gate, and an exact binary hash gate.  A byte-for-byte local
rehearsal closed at 50 runs per shard.  The same tool then promoted 10 and 9
attempts on the VMs; both recovery reports mark every row `promoted` and both
manifests now contain all 50 phase-1 runs.

The oracle services finished all ten simulations per shard.  All 120 run trees
are retained locally under
`results/t2_repair_mechanism_v1/complete_remote`; each run passes individual
strict validation.  The frozen post-run pair audit nevertheless stops on
primary-outcome differences in compound seed 21173 frame 2 on shard 0 and
compound seed 21174 frame 33 on shard 1.  The full diagnostic subsequently
found closure failures in all 20 pairs.  No V1 oracle result is admissible.
The factual analyzer at `9538afe` excludes all 20 oracle runs,
strictly validates the remaining 100, and emits a checksum-bound eight-figure
result archived under
`key_experiment_results/18_t2_repair_mechanism_v1/factual_phase1`.

The protected factual result confirms diversity but not resource viability.
STR has 31.7556% all-generated misses.  Full T0 and T2 reduce this to 18.7972%
and 18.9778%, but consume 2.0905x and 2.0971x STR sender airtime.  Ideal 12.5%
FEC T2 has 40.5833% misses and 1.4232x airtime; versus STR its paired miss
delta is +8.8278 percentage points with 95% CI [+3.0667, +14.0224].  It is
only 0.4639 points better than 5 GHz alone despite 1,698 ms/run of added
secondary airtime.  Its secondary queue is generally small, while primary ACK
deficit is saturated at ten packets.  OBSS p17 is the only family where FEC
beats STR, and both are already in a collapse regime, so it is not evidence of
a generally useful action.

The clean diagnostic at `961c14d` strict-validates and tree-hashes the 60
relevant STR/baseline/oracle runs.  It finds 12,456 baseline frames whose first
recorded packet arrived only after the deadline; lazy receiver-state creation
nonetheless includes that packet, so the V1 sidecar repairs the other packets
and systematically omits this one.  This explains 9,172 flawed-replay misses.
The flawed arm has 36.7917% misses at 1.5679x STR airtime and is diagnostic
only.  All pairs have identical aggregate primary application bytes, PHY TX
airtime, MPDU successes, and retransmissions, but 23 additional deadline-edge
packet differences and 8,520 snapshot differences prevent exact per-frame
closure.  The complete artifact is archived under
`key_experiment_results/18_t2_repair_mechanism_v1/oracle_pair_diagnostic`.

The minimum V2 correction is frozen at `fcb8474`.  It reuses the original
binary and replaces only the 20 oracle sidecars with deadline-correct plans;
the 100 factual runs remain immutable.  Both ten-run shards finished with
status 0 and strict pair closure.  Their complete 180 MB roots were retrieved
before local analysis.  A local resume attempt revalidated the runs but then
stopped because the current checkout's unrelated executable hash differs from
the frozen VM binary; it did not change remote evidence.  The two locally
rewritten manifests were immediately restored byte-for-byte from the VMs and
their SHA-256 identities match the authoritative remote copies.

The clean `0196788` analyzer combines exactly 100 factual and 20 corrected
runs while excluding every rejected V1 oracle output.  Deadline repair has
6,342/36,000 misses (17.6167%) versus STR's 11,432 (31.7556%), a paired delta
of -14.1389 percentage points with 95% CI [-19.4890, -10.0361].  Its
sender-airtime ratio is 1.5721 with CI [1.5064, 1.6316], so both the original
equal-airtime and 1.20 engineering gates fail with zero joint successes in
10,000 bootstrap draws.  Receiver primary sets drift in 8,957 frames; retain
the paired-potential wording.  All eleven PNG/PDF pairs were visually
reviewed, and the result is copied under
`key_experiment_results/18_t2_repair_mechanism_v1/deadline_oracle_v2`.

The explicitly post-result optimistic subset sensitivity is complete through
`ea3559a`.  It noncausally pools budget across all runs, chooses only factual
rescues, and ignores fixed overhead and changed feedback.  At 1.20 it selects
2,950 rescues and projects 11,827 misses, still 395 worse than STR.  Beating
STR by one miss requires an optimistic minimum ratio of 1.2051 before omitted
overhead.  Its checksum-bound artifact is archived below the main result.
This iteration is complete.  Stop and do not start a subsequent action or
predictor iteration without user review.

The decisive question is answered negatively.  Privileged deadline repair
has fewer all-generated misses than STR but consumes 1.5721x airtime; even its
optimistic noncausal subset cannot beat STR at 1.20.  Prediction cannot rescue
this action architecture under the current resource target.  Any future work
must first discuss a different redundancy action, then train a causal selector
only after the action itself shows an adequate resource frontier.

The authoritative result is
`key_experiment_results/17_environment_generalization_qualification_v1`.
STR records 16.0443% all-generated misses, versus 19.1308% for V2 and 18.7582%
for distributional-shadow.  The candidate-minus-STR 95% intervals are
strictly positive: `[+1.5367, +4.6140]` percentage points for V2 and
`[+1.1790, +4.2135]` for distributional.  Distributional improves V2 by
0.3726 points with interval `[-0.5596, -0.1960]`, but does not qualify.  The
formal completed-frame P99 estimand is `not_assessable` because 28 valid
collapse runs have fewer than 100 completions.  All misses in all arms are
incomplete frames, so completion CDF/PDF figures are survivor-conditioned.

The separately launched compound-p23 seed `21193` trio is archived as an
excluded sensitivity check and never entered the frozen manifest or any
headline estimate.  It also favors STR.  All four exact original p23 seed
pairs were recovered, so no substitute is needed.

### Historical pre-closure boundary

The following material is retained as an execution audit.  Its present-tense
instructions describe the state before the completed `47e1996` campaign and
must not override the pause above.

The active boundary was the repaired held-out closed-loop environment
qualification.
The 384-run randomized analysis is complete, checksum-closed, fetched, and
archived under `key_experiment_results/16_environment_generalization_randomized_v1`
through `c0085b0`.  The qualification analyzer and plot contracts are now
source-closed through `ab9e008` and bound to the pre-outcome runtime amendment
through `147b1b2`.  They report direct all-generated paired STR
miss, completed HF7 P99, sender airtime, and background gates at aggregate,
family, and scenario levels, and mark the predeclared randomized-oracle
fraction `not_assessable` because the two populations differ.

The `ff6d8b8`, `d66313b`, `de49f8b`, `3bf5bb8`, and `9196eef` checkouts are
stopped and excluded in both execution and analysis contracts.  Their
directory and failure counts are audit metadata only; do not resume any root,
promote the first root's 85 complete entries, promote the second root's 335
retained entries, promote either middle root's 121 retained entries, promote
the fifth root's 175 retained entries, or combine any population with a new
build.
The second audit's 576 attempts split exactly into 335
retained entries, 134 process failures, and 107 validator rejections.  Commits
`a6ac26e`, `616caff`, and `147b1b2` restore source closure, add the named held-out
workload envelope, and check all 144 unique configurations.  Commits `a33d2c2`,
`d4a55e6`, `648a56a`, and `28b77ce` close the failures exposed by the complete
second audit.  The third audit stopped after 121 retained runs, one failed
completed attempt, and 64 interrupted attempt directories because SciPy 1.11.4
reported a false infeasibility with presolve disabled.  Commit `e7a8b3e` adds a
presolve retry under the unchanged formulation and still requires independent
witness replay.  The preserved failed attempt now validates on the VM under
SciPy 1.11.4, as do 39 focused and compatibility tests.  Commit `3bf5bb8`
closes the third audit in both contracts.  The exact fresh checkout embeds that
commit and passes the preserved failed attempt, 52 focused Python tests, the
`wifi-streaming` C++ suite, all 144 frozen configurations, and five full strict
canaries spanning STR, both selective policies, the former failing OBSS
scenario, and the variable-frame workload.  The final service
`wifi-qualification-3bf5bb8.service` started from an empty root at 18:25 SGT
with 64 workers, then stopped after 121 retained runs, one failed completed
attempt, and 64 interrupted or unretained attempts.  The failure was the same
run that passed preflight and isolated replay: one 30-second wall-clock budget
was shared across all independent MILP components and both presolve choices.
No performance aggregate was inspected.  Commit `2e2b2c6` replaces that shared
guard with a fresh 60-second budget per component/representation; the exact
attempt passes 64/64 concurrent stress replays in 66.94 seconds.  Commit
`9196eef` binds this fourth excluded audit in both contracts.  The exact clean
`9196eef` VM checkout embeds the full commit, passes the preserved failed
attempt, 53 focused Python tests, all 144 configurations, and five strict
canaries.  `wifi-qualification-9196eef.service` started at 19:50 SGT from a
fresh empty result root with 64 workers.  The watchdog stopped it and both
downstream jobs at 20:56 SGT after 175 retained runs, one failed completed
attempt, and 64 interrupted attempts.  The failing variable-final-MPDU run
produced the same invalid latent-solver witness in isolated and tightened-
tolerance replay.  No performance aggregate was inspected.  `09e148f` records
the meter's exact byte split and binary64 allocation bits; `60c78c6` freezes
their ordered V2 schema and direct replay.  Launch nothing until these repairs,
the fifth exclusion, and the regenerated matrix are committed and all new
canaries pass.

One compound-shift scenario contributes only 16 included rows across four
runs after the frozen warmup and action-contamination exclusions.  Preserve
this as a coverage/uncertainty warning when interpreting family aggregates;
do not silently drop it or change the frozen population after seeing results.

The randomized resource oracle estimates only the action-clean eligible-T2
population and explicitly forbids an all-generated-frame claim.  Therefore
its miss probability is not commensurate with the closed-loop all-generated
miss rate, and the predeclared `fraction_of_oracle_deadline_gain_realized`
gate cannot honestly be calculated by subtracting those two rates.  The
qualification analyzer must report that gate as `not_assessable`, while still
reporting direct all-generated STR gates and eligible-population oracle/regret
evidence separately.  A future all-generated oracle needs a sequentially
identified policy-value design that covers startup, ineligible, and
action-history-contaminated frames; do not silently bridge the estimands.

The randomized result estimates 26.0618% no-copy eligible-row misses and
18.7993% [14.3204%, 23.4304%] under the cross-fitted predicted-benefit
resource ceiling, only a 27.87% relative reduction.  Both the 0.4% absolute
and 50% relative gates fail.  Myopic primary risk reaches 19.3400% and
realizes 92.55% [89.47%, 95.71%] of the ceiling's gain, so a modest scalar
ranker improvement has little headroom at the same 372 ms/run budget.  Ranking
itself remains strong: control-miss AUC is 0.9746 and treated-rescue AUC is
0.9165.  The broad domain contains severe OBSS-intensity, legacy, and compound
scenarios; video workload also has 62.5% hard OOD fallback.  An all-action DR
sensitivity still implies about a 60.26% relative reduction, so diversity is
valuable but the full-copy action is too coarse under the frozen resource
limit when risky frames are numerous.

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

The V1 event schema does not record its per-frame byte split and is now limited
to historical excluded-audit replay.  V2 records the ordered frame IDs, exact
per-frame tagged bytes, PPDU-duration binary64 bits, and exact allocation bits;
new qualification evidence must replay these values directly without a latent
solver.  The portable compiled cost path still does not freeze FMA contraction,
so retain that separate portability caveat for a future device qualification.

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
- Exact meter evidence repair at `09e148f`: `streaming-experiment` builds and
  the full C++ `wifi-streaming` suite passes locally.  The solver-free V2 test
  passes without NumPy/SciPy, and 34 paired/distributional validator tests pass
  on the VM with NumPy 1.26.4 and SciPy 1.11.4.
- Ordered V2 schema at `60c78c6`: Python compile and `git diff --check` pass;
  35 paired/distributional validator tests pass on the VM.  The schema contract
  keeps V1 available only for historical audit replay.
- Fifth-audit contract amendment: generated artifacts reproduce byte-for-byte,
  and 56 validator, generator, configuration-check, qualification-analyzer, and
  plotter tests pass on the VM production scientific stack.
- Exact `47e1996` campaign closure: all 576 manifest runs pass corrected strict
  validation.  The campaign manifest SHA-256 is
  `4c847ccc7d2e7d0ac9c154b0c4efc778ddcb403d0d12dabfd4a971c976269ef4`;
  the independently matched local and VM 9,025-file raw-tree identity is
  `2a1c5d5767647fb37aaabca507ea46bf3cb8307f71c8b5c7dc22bf538bc913f2`.
- Roundoff correction at `5ca913a`: 28 paired-validator tests pass on the VM,
  including the preserved false rejection.  Exact float32 score, gate, action,
  and controller output remain unchanged.
- Complete reliability analysis and plots through `694ce9a`: four focused
  analyzer/plot test groups pass; all internal manifests and the 451-file key
  result checksum set verify.  All ten PNG/PDF figures were visually reviewed.

## Work log

### 2026-08-08 - Freeze 1.5x-background WMM repeat

- Interpreted 50% more background traffic as 1.5 times every OBSS flow's
  offered ON-period rate, preserving flow count and duty cycle so the treatment
  remains a clean paired load scaling.
- Froze the four-profile, three-arm, ten-seed contract and separate preflight
  and two-shard matrices.  The full matrix has 120 runs and the preflight has
  one complete 12-cell seed.
- Added a closure test proving the original and new expanded matrices are
  identical after removing only the four UL/DL rate bounds.  All existing WMM
  matrix and mapping tests remain green.  No new outcome has been observed.

### 2026-08-08 - Complete WMM realism matrix

- Added explicit AF41 target and OBSS BE, one-VI-per-channel, and all-VI
  profiles while preserving legacy output and target-WMM semantics.  Froze a
  120-run, ten-seed contract across STR, V2, and Distributional through
  `d9867b1`.
- Ran a full 12-cell workload preflight, followed by one 60-run persistent
  shard on each documented VM.  All simulations completed and passed fresh
  strict validation; retrieved trees exactly match the recorded remote
  identities.  No replacement or reserved seed was used.
- Ran the predeclared 10,000-replication paired bootstrap and generated
  thirteen PNG/PDF figure pairs through `bccb287`; fixed the figure-manifest
  root in the separate tested commit `72b21fa`.
- Found that one VI competitor per channel leaves every arm at zero misses.
  With all competitors VI, V2 and Distributional each cut STR misses from 33
  to 4 while staying below 1.20 sender airtime, but P99 is indistinguishable.
  V2 uses less airtime and remains the engineering choice.
- Traced every residual selective miss to the first eight frames with no
  duplication action.  Archived this diagnostic, reports, raw identities,
  and all figures under
  `key_experiment_results/21_scenario15_wmm_realism_matrix_v1`.  Stop before
  testing a startup fallback.

### 2026-08-08 - Complete neutral scenario WMM comparison

- Added target-stream `wmmMode=off|on` while keeping EHT QoS valid in both
  modes.  Off is the historical CS0/TID0/AC_BE mapping; on is CS5/TID5/AC_VI.
  Background and OBSS traffic remain CS0/AC_BE.  Implementation, frozen
  contract, preflight, analyzer, and background accounting were committed and
  pushed through `b2e5665`.
- Ran 48 opened seeds across two modes and three arms as two persistent
  144-run VM shards.  All 288 simulations completed and passed strict
  promotion.  Retrieved both raw trees and compressed archives, verified exact
  local/remote tree identities, and did not use replacement or reserved seeds.
- Ran the predeclared shared 10,000-by-48 paired bootstrap and generated twelve
  PNG/PDF figure pairs.  A visual review found and fixed clipped isolated-miss
  evidence and overlapping legends in `0be0c64`; focused rendering tests pass.
- Established that WMM nearly saturates the neutral reliability ceiling: STR
  has 2 misses, V2 and Distributional have 0, but STR has 2.22 ms lower P99
  and less sender airtime.  Background throughput is unchanged.  Prioritized
  STR is the overall choice in this environment.
- Archived the compact analysis, all figures, source manifests, frozen
  contract, raw-tree identities, archive hashes, and the cross-build drift note
  under `key_experiment_results/20_scenario15_wmm_comparison_v1`.  Stop before
  any further experiment and wait for user discussion.

### 2026-08-08 - Complete adaptive target-MCS qualification

- Finished the two 288-run services using clean simulation commit `3ec0319`.
  Shard 0 promoted 288 runs and shard 1 promoted 287.  Before diagnosis or
  retry, retrieved 479 valid runs and all then-present attempt data, freshly
  strict-validated them, plotted 159 complete matched units, and archived the
  pre-recovery result at `ee70333`.
- Diagnosed the missing adaptive STR run as an ns-3
  `WifiAssocManager::MatchScanParams` assertion.  One exact unchanged retry
  reproduced the same assertion at 29.947258708 simulated seconds with
  byte-identical partial frame, decision, and resolved-config files.  Archived
  the checksums and stopped retries without substituting a seed or patching the
  campaign binary.
- Froze the post-execution exception contract and 191-unit paired analysis at
  `6630288`.  All 575 promoted adaptive runs pass fresh strict validation;
  both MCS modes exclude the incomplete unit, leaving 573 analyzed runs each.
- Generated and visually reviewed all ten fixed/adaptive PNG/PDF figure pairs.
  Adaptive MCS significantly worsens all three arms, uses more sender airtime,
  and leaves background throughput unchanged.  It is worse in 41/48 STR,
  44/48 V2, and 43/48 Distributional scenario points.  All misses are
  incomplete frames, not completed-but-late frames.
- Checksum-matched both final local raw trees to their remote VM copies and
  archived the identities, final tables, observations, source manifests, and
  partial recovery evidence under
  `key_experiment_results/19_environment_generalization_adaptive_mcs_v1` at
  `823ea7f`.
  Stop before any retuning or new experiment.

### 2026-08-08 - Freeze adaptive target-MCS qualification

- Added a fixed/adaptive target-MCS attribute with fixed behavior as the
  backward-compatible default and Minstrel-HT as the adaptive treatment.
- Preserved the legacy PHY, MAC, queue, and background RNG assignment by
  moving only Minstrel's eight target-manager streams to a reserved range.
- Reproduced pre-change fixed dual-interface and STR-MLO smoke outputs exactly
  after removing run identity and new provenance fields; completed adaptive
  smoke runs in both topologies.
- Froze a parent-bound 576-run contract and a dedicated sharded runner so the
  generic historical runner remains byte-identical.  Static expansion proves
  that the only simulation setting change is `wifi.mcs_mode=adaptive` and
  that two shards cover 288 runs each without splitting paired units.
- Passed the wifi-streaming C++ suite, 144/144 unique configuration checks,
  and focused/compatibility Python tests.  A broad 568-test run had one new
  legacy-fixture incompatibility, now fixed; its remaining six setup errors
  and one source-hash assertion are pre-existing frozen-source/local-artifact
  closure failures.  Do not weaken those frozen contracts or rewrite
  historical evidence for this campaign.
- Committed the implementation as `ab05eaa` and the frozen campaign boundary
  as `0f4169e`.  A real three-arm canary from the latter commit completed and
  passed strict replay for all 5,400 generated frames; this opens the formal
  two-VM launch gate.
- Deployed clean `3ec0319` to both documented VMs, verified identical
  executables and 144/144 configurations remotely, then launched the two
  persistent 288-run services.  Initial service, manifest, and 64-process
  checks passed on both hosts.
- Froze the outcome-blind comparison and plotting pipeline at `3fd6b05` while
  the formal campaign was still in its first wave.  Eighteen focused analyzer,
  plotter, campaign, and MCS tests pass, as does a synthetic render of all 20
  planned PNG/PDF artifacts.  The archive will also retain exact copies of
  both adaptive source manifests.

### 2026-08-07 - Analyze the deadline-correct repair replay

- Both persistent services finished all ten runs with status 0.  Each shard's
  final closure has 18,000 frames, exact sidecar/action agreement, and exact
  aggregate primary sender counters.  Retrieved both complete roots before
  any local analysis.
- Shard closure records 2,930 and 3,412 misses.  Receiver primary packet sets
  drift in 4,897 and 4,060 frames, respectively, so the treatment remains a
  paired no-repair deadline-potential replay rather than an exact within-run
  oracle.
- Froze the mixed-source analyzer at `0196788` before executing it.  It
  strictly validates 100 immutable factual runs plus 20 corrected runs,
  excludes all 20 rejected V1 outputs, hashes 2,540 raw files, and produces
  eleven PNG/PDF figure pairs.  All figures were visually reviewed.
- Deadline repair records 6,342/36,000 misses (17.6167%), rescuing 8,436 of
  14,777 primary misses and introducing one miss under the paired replay.
  STR records 11,432 misses (31.7556%).  The paired miss delta is -14.1389
  points with 95% CI [-19.4890, -10.0361], and reliability improves in every
  scenario.
- The repair arm uses 10,468.22 ms/run sender airtime, or 1.5721x STR with
  95% CI [1.5064, 1.6316].  It fails equal airtime and the 1.20 sensitivity in
  every bootstrap draw.  Primary-only already uses 1.1682x STR and leaves
  only 211.92 ms/run of 1.20 headroom before repair.
- Added the explicitly post-result optimistic subset sensitivity at `65b2dbb`
  and corrected its negative-headroom reporting at `ea3559a`.  The pooled
  noncausal 1.20 upper sensitivity selects 2,950 factual rescues and projects
  11,827 misses, 395 worse than STR.  The optimistic tagged-airtime ratio
  needed to beat STR by one miss is 1.2051 before fixed overhead.  Archived
  the checksum-bound diagnostic beside the main result and stopped.

### 2026-08-07 - Launch the deadline-correct repair replay

- Froze the narrowly corrected V2 contract and runner at `fcb8474`.  The
  correction defines the timely primary set independently of lazy receiver
  state: when the first primary arrival is absent or strictly after the
  integer-microsecond frame deadline, the timely set is empty; otherwise it
  is the baseline link-1 receipt set.  The repair plan is the packet universe
  minus that timely set.
- Reuse the exact `791bb2d` simulation implementation and executable SHA-256
  `ad595359e594f12e238ab74aca1889c15b241fc3adf49e6ead95beb8485b507d`.
  No C++ source, factual run, opened scenario/seed unit, or reserved
  confirmation seed changes.  The V2 contract SHA-256 is
  `2df18a10f7b584af516d56c77350175f30e7b10c190fbd7f61da598b7b592cea`.
- Thirteen focused tests and both prepare-only shards pass.  Shard 0 contains
  79,157 planned repair packets including 6,600 deadline corrections; shard 1
  contains 71,822 including 5,856 corrections.
- Launched the ten new runs per VM under
  `wifi-t2-deadline-oracle-v2-shard0-fcb8474.service` on
  `10.120.16.105:30022` and
  `wifi-t2-deadline-oracle-v2-shard1-fcb8474.service` on
  `10.120.17.30:30022`.  Invocation IDs are
  `e41d9809f75e412d97b61a52b503fb98` and
  `891599d20e5c446d94a3729c987a3442`.  Both services are active with all ten
  attempts started at the last poll; zero runs have yet crossed atomic
  validation and manifest publication.
- Treat this arm as a paired no-repair potential-outcome replay, not an exact
  within-run oracle if the repair changes later receiver state.  Strictly
  require exact sidecar actions and aggregate primary sender counters, report
  receiver-set drift, and evaluate both the equal-airtime question and the
  `1.20` engineering sensitivity.  Stop after the result is archived.

### 2026-08-07 - Reject the flawed packet-repair oracle replay

- Enumerated all 20 failed pairs instead of stopping at the first error per
  shard.  All 60 relevant runs remain individually strict-valid and all V1
  actions exactly match their source sidecars.
- Found a receiver deadline-semantics defect in the oracle source: when the
  first primary packet arrives after the deadline, lazy state creation records
  it before immediate finalization.  The V1 plan therefore omits one late
  packet in 12,456 frames and under-repairs 138,523 versus 150,979 packets.
- Added the standard-library diagnostic and focused tests at `c34b019`, then
  added a sandbox-compatible serial validation path at `961c14d`.  Eleven
  runner/diagnostic tests pass.
- Generated a clean, checksum-bound diagnostic over all 36,000 paired frames.
  The flawed replay records 13,245 misses (36.7917%) versus STR's 11,432
  (31.7556%) at 1.5679x airtime.  Exactly 9,172 misses contain the omitted
  packet.  These are failure-diagnostic values, not an oracle ceiling.
- Archived the report, source-tree identities, exact frame/pair tables, and
  artifact hashes under
  `key_experiment_results/18_t2_repair_mechanism_v1/oracle_pair_diagnostic`.
  Preserve the valid factual panel and reject all V1 oracle outcomes before a
  narrowly corrected replay.

### 2026-08-07 - Protect the complete factual mechanism panel

- Both oracle services finished, bringing the campaign to 120/120 simulated
  and individually strict-valid runs.  Retrieved both complete 60-run shards
  before any diagnosis, repair, or rerun.
- Both frozen oracle pair-closure audits failed on primary packet outcomes:
  compound seed 21173 frame 2 on shard 0 and seed 21174 frame 33 on shard 1.
  Excluded all 20 oracle runs rather than weakening the counterfactual
  contract or selecting only apparently matching pairs.
- Added the clean factual analyzer at `9538afe`.  It strictly validates the
  balanced 100-run, five-arm panel and generated eight PNG/PDF figures plus
  aggregate, per-run, and per-scenario tables.  Every artifact hash verifies.
- Archived the protected result under
  `key_experiment_results/18_t2_repair_mechanism_v1/factual_phase1`.  FEC T2
  loses to STR on misses (+8.8278 percentage points, paired 95% CI +3.0667 to
  +14.0224) while consuming 1.4232x airtime.  Small secondary queues and a
  saturated ten-packet ACK-deficit signal argue against simple queue pressure
  as the main explanation.
- The next and final boundary for this iteration is to enumerate the oracle
  drift and decide whether a valid equal-airtime oracle conclusion can be
  identified.  Do not begin predictor or action redesign afterward without
  review.

### 2026-08-07 - Preserve and analyze the mechanism phase-1 prefix

- Both services completed all 100 phase-1 simulations.  The runner promoted
  all 80 non-FEC outputs and one benign FEC output, then stopped before the
  oracle phase because 19 coded FEC completions violated an old generic
  validator assumption that a completed frame must receive every source
  packet.
- Retrieved the 81 promoted outputs and, separately, all 19 complete attempt
  directories before changing validation or launching a rerun.  The balanced
  four-arm panel contains all 20 identical-seed units and 36,000 generated
  frames per arm.
- Added the clean prefix analyzer at `e06796f`.  It strictly validates only a
  complete STR/single/T0/T2 grid, uses all-generated deadline misses and
  deadline-censored latency, applies a 10,000-sample scenario-stratified paired
  bootstrap, and labels completed P99 as survivor-conditioned.
- Generated and visually inspected the scenario miss, censored CDF/PDF,
  per-link airtime, burst, paired-effect, and T2 queue/ACK figures.  Archived
  the report, tables, manifests, and PNG/PDF figures under
  `key_experiment_results/18_t2_repair_mechanism_v1/partial_pre_fix`.
- Full copy beats STR on aggregate reliability but costs about 2.09x airtime;
  T2 does not save airtime versus T0.  OBSS p17 remains a severe collapse
  regime even under full copy.  This prefix cannot answer the oracle
  equal-airtime question, so no action or predictor decision is made from it.
- Corrected only the coded-completion interpretation in `280b18b`.  All 19
  preserved attempts pass exact validation; non-FEC completion remains
  unchanged and focused adversarial mutations are rejected.
- Added and rehearsed no-rerun recovery at `fb26a4b`.  The in-place VM
  recovery produced 50 phase-1 runs in each manifest and exact recovery counts
  of 10 and 9.  Launched only the 20 pending oracle runs under persistent
  services after both the complete-phase-1 and original-binary gates passed.

### 2026-08-07 - Launch the packet-repair mechanism gate

- Froze five representative opened scenarios and four seeds per scenario in
  `t2-repair-mechanism-v1.json`.  The six identical-seed arms are STR,
  primary-only 5 GHz, full copy at T0, full copy at T2, privileged T2 repair
  of only the primary packets absent at the deadline, and `ceil(k/8)` ideal
  systematic repair symbols at T2.
- Added source-packet and coded-symbol actions, exact per-frame packet outcomes,
  paired T2 queue/ACK telemetry, measured-airtime settlement, and strict
  validation through `504fe44`.  All five executable action/observation paths
  pass fresh end-to-end short runs; both C++ module suites pass locally.
- Added the hash-closed two-stage sharded runner in `791bb2d`.  Phase 2 cannot
  start until each paired primary-only sidecar validates.  Its closure check
  requires the oracle run's primary packet outcome to be byte-identical to the
  baseline and its repair plan to equal the baseline eventual-missing set.
- Pushed through `791bb2d`, transferred and verified a 2.5 MB Git bundle to
  both VMs without changing their archival local remotes, built identical
  executables, and passed the focused controller test on both.
- Launched 10 complete paired units per VM.  The clone's first service stopped
  after logout because user lingering was disabled; it had zero promoted runs.
  Enabled linger without sudo and resumed the unchanged shard root.  The
  original shard and cloned retry service are both active.  Preserve and fetch
  validated evidence before diagnosing any subsequent failure.

### 2026-08-07 - Clone and validate a second experiment VM

- Installed and enabled KVM/libvirt on physical host `10.120.17.30` without
  restarting it.  The host has 96 logical CPUs, 503 GiB RAM, and about 1.3 TiB
  free; `virt-host-validate qemu` passes all requirements relevant to this
  software-only VM.
- Made a crash-consistent live copy of `wifi-exp-f2e354c`: flushed the idle
  guest, atomically pivoted to an external overlay, transferred the immutable
  50.28 GB base directly between physical hosts, matched independent SHA-256
  results, and obtained a clean `qemu-img check`.  Block-committed the 6.88 MiB
  overlay, restored the original single-disk chain, and deleted only that
  unreferenced temporary volume.
- Defined autostart domain `wifi-exp-17-30` with 64 vCPUs and 48 GiB RAM.
  Separated its UUID, machine ID, hostname, MAC, and SSH host keys while
  retaining the authorized user key.  The original and clone now have distinct
  ED25519 fingerprints and both accept batch-mode SSH through their own host's
  port `30022`.
- Verified both VMs concurrently at project commit `47e1996`; each reports 64
  processors and runs `streaming-experiment --help` without build work.  No
  physical host was restarted.  Exact operations and future two-host campaign
  rules are recorded in `EXPERIMENT_HOSTS.md`.

### 2026-08-07 - Close and archive held-out qualification

- Preserved the first 568 strictly retained runs before diagnosing or retrying
  anything.  Fetched and hashed that raw prefix, built a balanced 360-run
  exploratory panel, and archived its analysis and eleven plots.  This keeps
  early evidence independent of the subsequent repair.
- Isolated the sole completed-attempt rejection to
  `2.220446049250313e-16` of derived probability-subtraction roundoff.  The
  float32 score, threshold gate, action, and runtime result were identical.
  Commit `5ca913a` bounds that derived comparison by the sum of its two
  independently permitted source-probability errors.
- Promoted the already complete exact attempt and reran only the seven
  interrupted original run IDs.  No seed or scenario substitution entered the
  frozen campaign.  All 576 runs now pass strict validation.
- Ran one separate compound-p23 seed `21193` three-arm sensitivity requested
  for rapid evidence.  It also favors STR, but remains outside the canonical
  manifest and every headline estimate.  The four exact frozen p23 seed pairs
  were recovered, so the supplement is not needed for completion.
- The formal analyzer failed closed as designed: 28 valid runs have fewer than
  100 completed frames, so completed-frame P99 and promotion are
  `not_assessable`.  The complete post-outcome analyzer retains all runs for
  reliability and resources instead of deleting collapse outcomes or lowering
  the support rule.
- Across 367,200 generated frames per arm, STR records 57,181 misses (16.0443%),
  V2 records 68,133 (19.1308%), and distributional-shadow records 66,819
  (18.7582%).  V2 minus STR is +3.0865 percentage points with 95% interval
  `[+1.5367, +4.6140]`; distributional minus STR is +2.7139 points with
  `[+1.1790, +4.2135]`.  Distributional improves V2 by 0.3726 points but still
  fails qualification, and its airtime upper interval exceeds 1.20.
- All observed misses are incomplete frames.  Completion CDF/PDF figures are
  therefore survivor-conditioned; they do not offset the reliability loss.
  Legacy coexistence and compound shift dominate the reversal, while radio
  propagation also loses reliability and exceeds the desired airtime ratio.
- Archived the complete report, ten reviewed figures, partial evidence,
  recovery tooling, exact manifests, checksums, and excluded supplement under
  `key_experiment_results/17_environment_generalization_qualification_v1` in
  commit `b0c7aad`.  The requested six-step pipeline is complete.  Stop here
  for user review before any new model, action, dataset, or simulation campaign.

### 2026-08-06 - Launch the load-stable final qualification

- Built a new detached, clean VM checkout at the exact full commit
  `9196eef15be3ea88736c11389cd0cc6c4f9b8c22`; the 7.4 MB experiment
  executable embeds that same identity.  The preserved load-sensitive attempt
  validates unchanged under the production SciPy 1.11.4 stack, all 53 focused
  qualification and validator tests pass, and all 144 unique scenario/arm
  configuration checks pass at 64 workers.
- Ran five fresh preflight-only canaries spanning STR, V2, and distributional
  shadow on `obss-intensity-qualification-p17`, plus both selective policies
  on `video-workload-qualification-p16`.  All five passed independent strict
  validation at run IDs `133e678fef5e8df8ed34`,
  `a38f9e39d8dfe8cbbc29`, `924a5e7ce7755f9767cc`,
  `efa3a0bdc8f96fdea83e`, and `bb547cc6336ef226806f`; none is reused as
  qualification evidence.
- Stopped stale analysis and plot retry loops left by the excluded `d66313b`
  audit.  Installed a verified three-unit fail-closed service chain for
  `9196eef`; the campaign unit refuses a pre-existing canonical result root,
  and analysis and plotting require their upstream unit to succeed.
- Started `wifi-qualification-9196eef.service` at 19:50 SGT from an empty root.
  The initial state had exactly 64 matching simulator processes, zero failure
  lines, and the analysis and plot jobs waiting behind the campaign.  Until
  exact 576-run closure, inspect only service, process, manifest-count, failure,
  and storage metadata.
- The manifest reached 129 strictly retained runs at 20:39 SGT with zero
  failures, closing two complete 64-run waves and beginning the third.  This
  crosses the 121-run boundary where both preceding audits encountered their
  first validator failure.  All 64 worker slots refilled after that boundary.
- Removed only the reproducible `build/` directories from the stopped
  `ff6d8b8`, `d66313b`, and `de49f8b` checkouts, reclaiming about 1.9 GB while
  retaining every raw audit result.  A metadata-only 15-second watchdog now
  stops the campaign and downstream jobs immediately if any `FAILED` line
  appears; it does not read performance outcomes.

### 2026-08-06 - Exclude the fifth audit and remove latent V2 solving

- The metadata-only watchdog stopped `wifi-qualification-9196eef.service`, its
  analysis job, and its plot job immediately after the first failure line.
  The excluded root contains 175 retained manifest runs, one failed completed
  attempt, and 64 interrupted attempts.  Zero simulator processes remained
  after the stop.  None of these runs may enter a qualification estimand.
- Inspected only service state, directory counts, the failed run ID, and the
  exact validator error.  No miss, latency, P99, airtime, throughput, action,
  policy-comparison, threshold, or gate outcome was inspected.
- Run `d24c6d9645e15542184f` exposed that V1 events log PPDU total bytes and
  frame IDs but omit the per-frame byte split actually used by the meter.  For
  a variable-final-MPDU event, SciPy/HiGHS returned a latent integer witness
  whose independent replay violated lower and upper rows by about `1.02e-4`,
  far outside the `1e-9 us` evidence envelope.  Isolated replay and explicitly
  tighter HiGHS tolerances returned the same invalid witness.
- Commit `09e148f` adds event schema V2: exact per-frame tagged bytes and the
  unsigned decimal bit patterns of the PPDU duration and each applied binary64
  allocation.  V2 settlements and reservation checkpoints replay those exact
  allocations directly in near-linear time and never import the legacy solver.
  Historical V1 validation retains the old bounded MILP.
- Commit `60c78c6` freezes the ordered V2 CSV and fail-closes malformed,
  reordered, mixed-version, noncanonical, or numerically inconsistent rows.
  The C++ module suite passes, as do 35 focused paired/distributional validator
  tests on the production VM stack.  Bind this fifth exclusion and both repair
  commits into the execution and analysis contracts before new canaries.

### 2026-08-06 - Exclude the fourth audit and isolate solver timeouts

- Stopped `wifi-qualification-3bf5bb8.service` and both downstream jobs as soon
  as the first failure line appeared.  The manifest reached 121 retained runs
  during shutdown; one completed attempt failed validation and 64 attempt
  directories were interrupted or not retained.  This entire root is excluded
  and none of its 121 retained runs may be combined with a later campaign.
- Inspected only service state, manifest and attempt counts, the failed run ID,
  and its validator error text.  No deadline, latency, P99, throughput, airtime,
  action, policy-comparison, threshold, or gate outcome was inspected.  The
  failing run `7ea792adf5b6eaa071a2` is one of the five successful preflight
  canaries and validates unchanged after campaign load is removed.
- The validator incorrectly shared one 30-second wall-clock allowance across
  every independent feasibility component and both presolve representations.
  Thus a slow earlier component could exhaust the budget of a later feasible
  representation.  This is an operational-load dependency, not an evidence
  constraint or solver-formulation difference.
- Commit `2e2b2c6` gives every component and representation a fresh 60-second
  solver allowance.  It leaves the matrices, integer lattices, bounds,
  tolerances, rounding, and independent exact replay unchanged.  Fifty-one
  focused local validator, distributional, generator, configuration, and
  analyzer tests pass.
- A separate clean VM checkout at `2e2b2c6` ran 64 concurrent strict replays
  of the exact failed attempt under SciPy 1.11.4.  All 64 passed in 66.936
  seconds.  Amend both frozen contracts to exclude `3bf5bb8`, bind `2e2b2c6`,
  regenerate the matrix provenance, and start all 576 runs again from a new
  clean checkout and empty root.

### 2026-08-06 - Exclude the third audit and repair solver portability

- Stopped `wifi-qualification-de49f8b.service` immediately after its first
  strict validation failure.  The excluded root contains 121 retained manifest
  runs, one failed completed attempt, and 64 interrupted attempt directories.
  Neither this root nor either earlier audit may contribute to a qualification
  estimand; all 576 runs must be generated again from one clean commit.
- The failure was validator portability, not a policy or simulation failure.
  SciPy 1.11.4 reported a 483-row, 186-column mixed-integer reservation
  component infeasible with presolve disabled.  The identical formulation
  solved with presolve enabled, and its rounded witness passed every original
  bound, constraint, and independent event-replay check.  The unchanged failed
  attempt also validates with SciPy 1.17.1.
- Diagnosing the failed attempt exposed one stdout line containing only the
  fields `sent_packets`, `sent_bytes`, `redundant_bytes`, `link_0_bytes`,
  `link_1_bytes`, `background_tx_bytes`, `background_rx_bytes`, and
  `finalized_frames`.  No deadline-miss, latency, P99, policy-comparison,
  threshold, or gate result was inspected.  These fields were not used to
  select or modify the policy, model, threshold, or analysis gates.
- Commit `e7a8b3e` retains the original presolve-disabled solve first and
  retries an unsuccessful component with presolve enabled under the same
  30-second component timeout.  Any returned witness is still rounded and
  independently checked against the original formulation and the 1e-9-us
  replay envelope.  Twenty-five focused local solver tests, 17 compatibility
  tests, and 32 generator/qualification tests pass.
- A clean validator checkout on the VM revalidated the exact failed attempt
  under SciPy 1.11.4 in 31.06 seconds, and all 39 focused remote tests pass.
  The execution and analysis contracts now record the third excluded root,
  limited inspection disclosure, and solver repair before another launch.
- Committed and pushed that source-closed boundary as `3bf5bb8`, then created
  `/home/jingweili/wifi_streaming_qualification_3bf5bb8` from the exact commit.
  Its independently configured GCC 13.3.0 executable embeds the full commit
  identity.  The preserved failed attempt, 52 focused Python tests including
  plot generation, the C++ `wifi-streaming` suite, and all 144 unique frozen
  scenario-arm configuration checks pass on the production VM stack.
- Ran five full canaries in a separate preflight root: STR, V2, and
  distributional-shadow on `obss-intensity-qualification-p17` at seed 21038,
  plus V2 and distributional-shadow on `video-workload-qualification-p16` at
  seed 21097.  All five completed and passed independent strict validation;
  their run IDs are `fa39df0475ec59f1374b`, `7ea792adf5b6eaa071a2`,
  `c2d07c3e8cfd082ed506`, `b692321be254629a58e3`, and
  `658256b70061462b0553`.  They are preflight evidence only and are not reused.
- Started `wifi-qualification-3bf5bb8.service` from a new empty root at
  18:25 SGT with 64 workers.  At launch, 64 simulation workers were active,
  the manifest contained 0 of 576 runs, and the failure count was zero.
  `wifi-qualification-analysis-3bf5bb8.service` and
  `wifi-qualification-plots-3bf5bb8.service` are ordered required dependencies;
  they can run only after the campaign and analysis respectively succeed.

### 2026-08-06 - Exclude the complete second qualification audit

- Allowed `wifi-qualification-d66313b.service` to finish every scheduled
  attempt, then inspected only completion and error metadata.  The manifest
  retained 335 of 576 attempts; 134 process failures and 107 validator
  rejections exclude the remaining 241.  No latency, miss, airtime,
  throughput, action, or policy-comparison outcome was inspected.
- The validator rejections divide exactly into 91 maximum-debt event-replay
  differences, 13 ordered-versus-sklearn cost-head differences, and 3
  infeasible old reservation witnesses.  The process failures contain 44 V2
  and 44 distributional deadline-contract aborts, 20 V2 and 20 distributional
  I-frame-contract aborts, 4 fragmented-PHY fraction aborts, 1 distributional
  final-reconciliation abort, and 1 exit without an abort marker.
- Fixed stale terminal acknowledgments in `a33d2c2`, event-time debt replay in
  `d4a55e6`, fragmented PHY-history normalization in `648a56a`, and generalized
  controller contracts plus exact variable-final-MPDU replay in `28b77ce`.
  The focused C++/Python suites, canonical full-run replay, and generalized V2
  and distributional full-run replays pass.
- Amended the execution and analysis boundaries to exclude both audit
  checkouts, prohibit partial-run reuse and mixed builds, bind all four repair
  commits, and require all 576 runs from one new clean repaired commit.  The
  deterministic generator and focused analyzer tests pass.  Committed and
  pushed this source-closed boundary as `de49f8b`.
- Created a separate clean `de49f8b` VM checkout without altering either audit
  tree.  Its default-profile executable and tests build with GCC 13.3.0; the
  complete `wifi-streaming` suite passes, and all 144 unique scenario/arm
  configuration checks pass with 64 workers.
- Ran V2 and distributional-shadow canaries in parallel on
  `video-workload-qualification-p16`, seed 21097: 60 fps, 8,200-byte P-frames,
  16.667 ms deadline, and a variable final MPDU.  Run IDs
  `cb0077268aed320afc4c` and `7331dbb2f0c03378173b` both completed and passed
  a second strict validation.  They remain in a separate preflight root and
  are not reused in the final campaign.
- Launched all 576 runs from a new empty root at 16:36 SGT as
  `wifi-qualification-de49f8b.service`, with 64 workers and no build or partial
  analysis.  Armed `wifi-qualification-analysis-de49f8b.service` and
  `wifi-qualification-plots-de49f8b.service`; both reject incomplete inputs,
  retry after 60 seconds, and have published no partial artifact.  Monitor only
  completeness/error metadata until exact closure, then strict-validate,
  analyze, plot, visually inspect, and archive the complete result.

### 2026-08-06 - Repair held-out qualification runtime before outcomes

- Stopped the first `ff6d8b8` qualification service after execution metadata
  showed two deterministic contract failures: the C++ temporal-T2 guard
  accepted only the original 30 fps synthetic profile, and the independent
  validator required the original neutral-environment hash.  The stopped root
  has 85 canonical directories, 226 retained policy attempts, 40 immediate
  abort logs, and 64 interrupted in-flight logs.  No latency, miss, airtime, or
  throughput outcome was inspected.
- Preserved the failed root only as an audit trail and amended both execution
  and analysis contracts before outcomes.  They require all 576 simulations to
  come from one new clean commit and explicitly prohibit using any `ff6d8b8`
  directory in an estimand.
- Restored `tools/build_randomized_temporal_dataset.py` byte-for-byte to the
  `7ca3bfc1...` source used by the deployed V2 model.  Moved the later
  missing-FIFO-ahead behavior into the environment-specific builder and added
  exact archived/current five-source profiles plus a loader compatibility
  contract, so historical artifacts retain their actual provenance and mixed
  source histories fail closed (`a6ac26e`).
- Added `environment_generalization_v1` as an explicit runtime/output/validator
  profile while retaining the exact controller, telemetry, airtime, target
  Wi-Fi, duration, and bounded workload contracts (`616caff`).
- Added a no-output executable configuration-check mode and exercised all 144
  unique scenario/arm configurations behind the 576-run matrix.  The amended
  matrix and failed-root exclusion are committed in `147b1b2`.
- Passed 89 focused Python tests, the deterministic generator check, a clean
  executable build, all 144 configuration checks, `git diff --check`, and the
  `wifi-streaming`, paired-value T2, and distributional-shadow T2 suites.
- Pushed the four clean repair/history commits through `d66313b`, created a
  fresh exact VM checkout, configured and built it with 64 jobs, and repeated
  all 144 unique configuration checks successfully on the VM.  Launched the
  new 576-run matrix at 12:56 SGT as
  `wifi-qualification-d66313b.service`; its output root is separate from the
  excluded `ff6d8b8` tree.
- Armed `wifi-qualification-analysis-d66313b.service` and
  `wifi-qualification-plots-d66313b.service` as fail-closed retries.  The first
  currently rejects the incomplete manifest before reading observations, and
  the second rejects the absent atomic analysis directory.  They can publish
  only after exact 576-run closure and successful strict validation.

### 2026-08-06 - Freeze held-out closed-loop evaluation before launch

- Froze the qualification analysis contract and implementation in `101f132`.
  It requires the exact 576-run manifest, derived run IDs, scenario identities,
  one clean build commit, and fresh strict validation of every run.  Point
  estimates weight families, scenarios, and replicates equally; one shared
  10,000-replicate bootstrap keeps families fixed and resamples scenario
  clusters then whole paired replicates.
- Kept direct all-generated STR victory separate from parent promotion.  The
  eligible-row randomized ceiling and all-generated closed-loop outcomes are
  noncommensurate, so the oracle-gain fraction and parent promotion readiness
  are explicitly `not_assessable`; all assessable aggregate and per-family
  gates remain enforced.
- Added the checksum-bound plot suite in `ab9e008`: six aggregate/family/
  scenario qualification figures plus the historical completion CDF, PDF,
  deadline/completion, miss-burst CDF, and resource views.  Historical plots
  freshly revalidate all raw runs and label pooled distributions descriptive
  and completed latency survivor-conditioned.
- Thirteen focused generator, analyzer, hierarchy/gate, and plot tests pass.
  A full 10,000-resample synthetic replay is deterministic, and all eleven
  PNG/PDF renderings were generated and visually reviewed.  Next record this
  boundary, push it, then deploy the exact clean commit and run all nine waves
  without inspecting partial outcomes.

### 2026-08-06 - Close and archive randomized environment replay

- Resumed from a byte-rehashed dataset/LOFO prefix at clean commit `3edf1ad`.
  The corrected policy replay completed in about 18 minutes, all five plots
  published, and top-manifest SHA-256 closed as
  `3c5bdcae98e75e91332ec3821d7555076cdead926d6a1746a09a1359d83a6a3e`.
- Independently rehashed every declared artifact remotely and after local
  extraction.  The complete 171,531,136-byte analysis archive passes
  `zstd -t` with SHA-256
  `ca0bede79bfd9ee0cc067a8f080a22cab71dc60b05f9475dc83e35c2aa39b7f0`.
- The cross-fitted resource ceiling fails both go/no-go gates at 18.7993%
  eligible-row misses and 27.87% relative reduction.  Myopic primary-risk
  ranking is only 0.5407 percentage points worse and realizes 92.55% of the
  ceiling gain.  Control-risk and treated-rescue AUC remain 0.9746 and 0.9165.
- Diagnosed resource/action-space pressure rather than a useless predictor:
  the reported treat-all DR sensitivity implies 10.3577% misses and 60.26%
  relative reduction, but it has no 372 ms/run feasibility.  Video workload's
  62.5% hard OOD rate separately requires denser workload/interactions support.
- Visually inspected all five figures and archived the compact evidence under
  `key_experiment_results/16_environment_generalization_randomized_v1` in
  `c0085b0`.  Next freeze the held-out closed-loop analyzer, preserving the
  randomized-versus-qualification estimand boundary, then run the nine frozen
  64-worker waves.

### 2026-08-06 - Repair sparse eligible-run replay before outcomes

- The first exact resource-policy replay ran for about 18 minutes, then failed
  before atomically publishing its policy directory.  The 307,689-row dataset
  and all 12 LOFO fits remain checksum-closed; no policy outcome was available
  or inspected.
- Diagnosed one legitimate zero-eligible-row source run in
  `compound-shift-collection-p14` (run ID `79c0388a0d75a32f2909`, seed `20379`,
  run `1`).  Its scenario retains 16 eligible rows across the other three runs.
- Amended the contract before reading outcomes in `40b6be1`: policy-value and
  bootstrap estimates condition on the 383 represented nonempty runs, with
  three draws for that sparse scenario and four elsewhere; resource summaries
  retain all 384 source runs and assign the empty run zero actions and
  reservation.  The contract records the exact exception and source identity.
- Added a fail-closed resume path in `60c03f3`.  It permits only an output root
  containing exactly the completed dataset and LOFO directories, rehashes
  every declared artifact, revalidates campaign/config/source provenance, and
  reruns only policy and plotting before publishing a new top-level manifest.
- All 68 environment-generalization and qualification regression tests pass.
  Next deploy the clean commits, resume from the verified remote prefix, and
  interpret nothing until the top-level manifest closes.

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
  reserved `1301` through `1348` confirmation seeds.  Follow-up `06806cb`
  records the runner's fully inherited resolved-matrix SHA-256 as
  `e2f89a25432b76cbe69f4a81691370c3809bde41addf4354a1ef74cddb362922`.
- The generator validates source hashes, inherited V2 closure, arm identity,
  exact pairing, worker-wave arithmetic, and seed isolation; 45 focused and
  compatibility tests pass.  The campaign is frozen but not launched.  Build
  and freeze the outcome analyzer after the randomized analysis closes and
  before any held-out qualification result can be read.
- A pre-launch analyzer audit found that the randomized oracle's eligible,
  action-clean T2 estimand cannot normalize an all-generated closed-loop miss
  rate.  Preserve its replay as ceiling evidence, but mark the parent's oracle
  fraction gate unassessable until an all-generated sequential causal bridge
  exists.  Direct paired STR performance and resource gates remain valid.

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
