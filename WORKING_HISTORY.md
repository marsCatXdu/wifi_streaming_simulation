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
- Any carry-over V3 engineering iteration must reuse already-open development
  units rather than opening confirmation units.  Record the reuse explicitly;
  it is candidate-development evidence, not an independent confirmation.
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

The latest validator milestone is `cff64f7`.  It exactly replays the compiled
ridge reduction order and proves one integer per-frame byte allocation can
jointly satisfy PPDU totals, settlements, and every outstanding reservation
checkpoint.  It normalizes sub-nanosecond rows to exact integer lattices,
splits independent MILP components, reconstructs dependent variables, and
accepts only integer witnesses that pass an independent exact constraint
recheck.

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
- [ ] Isolate full-horizon causal carry-over as admission V3 on already-open
  engineering seeds while freezing the V2 predictor, threshold, emergency
  tier, refill, reservation, action, and environment.  Do not replace the
  predictor or open final-confirmation seeds until this guard question is
  resolved.

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

The current boundary is one controlled V3 guard test, not an open-ended model
search.  Increase the causal token-bucket capacity/carry-over from 10 seconds
toward the full 60-second experiment horizon while keeping the V2 predictor,
threshold, P-frame gate, emergency tier/debt, 0.6% refill, conservative cost
reservation, duplicate action, and environment fixed.  Use already-open
engineering seeds only.  All threshold passers have 355.25 ms/run of learned
predicted cost versus 360 ms/run of generated credit, but the 120.84 ms/run
assigned to rejected candidates is only a projection until closed-loop
contention is measured.

After this isolation, shift to the predictor/outcome problem unless the guard
still clearly dominates.  The specific targets are 170 below-threshold
misses and the substantial airtime spent accelerating already-on-time frames
or giving no benefit.  Startup and I-frames are separate semantic changes:
choose a causal non-temporal startup fallback or consistent pre-roll, and
test an I-specific policy instead of indiscriminate I-frame duplication.

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

## Work log

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
