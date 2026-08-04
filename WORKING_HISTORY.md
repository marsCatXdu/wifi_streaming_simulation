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

- Engineering qualification units: seeds `1201` through `1248`, ns-3 run `1`.
- Matrix: one paired temporal-T2 run and one STR MLO run per seed, for 48
  matched pairs and 96 runs.
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

The latest validator milestone is `6b822a4`.  It exactly replays the
compiled ridge reduction order and proves one integer per-frame byte
allocation can jointly satisfy PPDU totals, settlements, and every outstanding
reservation checkpoint.  It also accepts only solver-rounded integer
witnesses that pass an independent exact constraint recheck.

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
- [ ] Implement and evaluate a score-aware measured-airtime admission policy
  on development seeds, preserving seeds `1301` through `1348` for final
  confirmation.  Replace the predictor only after admission is no longer the
  dominant failure.

Do not replace this checklist with nested planning lists.  Add a new top-level
item only when the research objective genuinely changes.

## Current work boundary

All 96 qualification simulations finished on the VM from clean commit
`da48d7d`; every run now passes the strict local validator.  Nine preserved
attempts were recovered without rerunning their valid simulations.  The
canonical manifest SHA-256 is
`50e90d04e68b0d13cba9eb80873098a21871f0b80cc9d535fadf51d4470c3420`.

The temporal-T2 candidate fails engineering qualification.  It records 681
misses (0.7882%) versus STR's 609 (0.7049%); the paired miss interval is
[-0.0567, +0.2269] percentage points.  Its P99 point estimate is 0.697 ms
better, but the paired interval [-1.816, +0.310] ms is inconclusive.  It passes
the sender-airtime ratio target at 1.1324 and background target at 0.0014%
loss.

Admission, not duplication efficacy, is the next work boundary.  The policy
rescues 538/551 acted primary misses, while the chronological guard rejects
2,276 higher-scoring candidates containing 349 primary misses.  Implement a
score-aware guard or bounded high-score emergency credit on development seeds
before considering a new predictor.  Keep the current primary-risk features;
the learned cost head requires later replacement because it has no useful
per-action rank correlation.  Do not open reserved final-confirmation seeds.

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

## Work log

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
