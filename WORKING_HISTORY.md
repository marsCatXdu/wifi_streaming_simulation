# Wi-Fi streaming working history

This file is the authoritative execution handoff for the current research
work.  It records what is complete, what is in progress, and what must happen
next.  Read it after `AGENTS.md` and `RESEARCH_MEMORY.md`, especially after a
context compression or a new agent session.

Keep this file factual.  Git, test output, and experiment artifacts remain the
source of truth; reconcile this document with them before continuing work.

## Stable objective

Qualify the frozen primary-only temporal-T2 selective-duplication policy
against STR MLO in the unchanged neutral mixed-4x4 environment.

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

`ba59751` is the current committed `main` and `origin/main` boundary at the
time of this update.

## One authoritative TODO checklist

- [ ] Finish one reviewable paired-T2 output-validator boundary: remove
  avoidable overlap with earlier generic validation, fix the remaining
  positive event-to-frame allocation invariant, run the focused and
  compatibility checks once, then commit and push.
- [ ] Run and strictly validate the two-run local preflight.  Fix only defects
  that block a trustworthy campaign, and commit/push any such fix at a clean
  boundary.
- [ ] Run the 96-run campaign on the 64-vCPU VM, fetch and checksum the raw
  artifacts, validate every run locally, analyze against STR MLO, and record
  the result and next scientific decision here.

Do not replace this checklist with nested planning lists.  Add a new top-level
item only when the research objective genuinely changes.

## Current work boundary

The validator boundary is confined to these code/test files:

- `tools/validate_outputs.py`
- `tools/tests/test_paired_value_t2_validation.py`

The validator currently adds genuinely new checks for exact 246-feature
canonical-model replay, paired decision/status gates, controller summary,
measured-airtime guard replay, and causal event/settlement allocation.  It
also contains avoidable overlap with previously committed generic telemetry,
descriptor, meter, and runtime-contract validation.  Reuse the existing
validated parsing and estimator helpers where doing so does not weaken the
independent model/runtime check.  Do not turn this cleanup into a separate
framework project.

One medium issue remains before commit: the max-flow reconstruction permits a
zero allocation on a frame ID explicitly listed in a secondary-airtime event.
The C++ meter lists a frame only after observing positive tagged MPDU bytes, so
every listed event/frame edge must receive positive airtime.  Enforce a
defensible positive lower bound (the event duration divided by tagged bytes is
available) and add the reproduced two-active-frame regression.

No engineering qualification seed has been run yet.

## Verification ledger

Do not repeat an entry unless relevant code changed after it ran.

- Paired validator focused suite before the remaining positive-edge fix:
  `10/10` passed.
- Validator compatibility suites at the same boundary: `144/144` passed.
- Two retained real artifacts at that boundary: both validated with 1,800
  frames; the action-bearing artifact had 1,016 evaluated model rows and 133
  actions.
- `py_compile` and `git diff --check` passed at that boundary.
- Independent audit result: model replay, strict CSV width, absolute summary
  accounting, settlement ordering, and positive-marginal allocation checks
  passed; the zero-allocation edge issue above remains.

These checks must be rerun once after the code changes, not once per agent.

## Work log

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
