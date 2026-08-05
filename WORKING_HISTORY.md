# Wi-Fi streaming working history

This file is the authoritative execution handoff for the current research
work.  It records what is complete, what is in progress, and what must happen
next.  Read it after `AGENTS.md` and `RESEARCH_MEMORY.md`, especially after a
context compression or a new agent session.

Keep this file factual.  Git, test output, and experiment artifacts remain the
source of truth; reconcile this document with them before continuing work.

## Stable objective

Improve selective duplication until an engineering candidate decisively beats
STR MLO in the unchanged neutral mixed-4x4 environment, then qualify the
frozen candidate on untouched confirmation seeds.

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

The latest T2 validator milestone is `c61fa98`.  It retains `cff64f7`'s exact
compiled-ridge and integer airtime-feasibility replay plus the V2-V4 guard
profiles, and adds schema-V4 reconstruction of the diagnostic divided score
and active raw float32 score used by V5.  The completed V5 result is archived
at `0528610`; V2 remains the engineering champion.

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
- [ ] Combine the fold-honest shadow price with exact future-credit borrowing
  that must be repaid by measurement stop.  First replay the single frozen
  mechanism on the already-opened randomized groups and record direct rejection
  outcomes.  If it materially closes the static gap, integrate the selected
  policy into the runtime and qualify it against STR on already-opened
  engineering seeds before touching confirmation seeds.  Preserve conservative
  reservations, the less-than-1.20 sender-airtime gate, and the background
  throughput gate.

Do not replace this checklist with nested planning lists.  Add a new top-level
item only when the research objective genuinely changes.

## Current work boundary

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

This does not revive naive V4 borrowing.  V4 already proved that making future
refill available without valuing displaced later actions spends chronologically
and regresses.  The next isolated screen must debit future credit only when the
current distributional reward exceeds the fold-honest opportunity price, then
enforce full repayment at measurement stop.  The compact completed ceiling is
archived under
`key_experiment_results/13_temporal_t2_distributional_online_ceiling_v1`.

Reserved seeds `1301` through `1348` remain unopened.  V2 is an engineering
pass, not final confirmation, and its 28.36% relative miss reduction is still
short of the longer-term greater-than-50% aspiration.  Freeze the final
candidate and final analyzer before consuming those seeds.

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

## Work log

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
- Kept V2 as the engineering champion.  The next work is an exact ceiling
  decomposition, not another score/threshold-only runtime variant.

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
