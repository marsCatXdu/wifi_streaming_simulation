# Environment-generalization randomized replay v1

This directory archives the checksum-closed analysis of 384 randomized T2
runs across 96 scenarios and six environment families.  Each scenario has
four source runs.  Every learned prediction for a family comes from separate
arm models trained without that entire family.

This is an eligible-row randomized policy-value and resource-ceiling result.
It is not an all-generated-frame result, a closed-loop result, or a comparison
with STR MLO.  Reserved confirmation seeds `1301` through `1348` were not read.

## Main result

The cross-fitted predicted-benefit resource ceiling fails both predeclared
go/no-go gates.  It is explicitly not a perfect-information oracle.

| Eligible-row policy | Deadline-miss estimate (95% CI) | Relative reduction from no copy | Actions/run | Reservation/run |
| --- | ---: | ---: | ---: | ---: |
| No secondary copy | 26.0618% [20.6929%, 31.5182%] | 0% | 0.00 | 0.00 ms |
| Uniform random, same budget | 21.7955% [17.2334%, 26.4642%] | 16.37% | 178.38 | 360.42 ms |
| Myopic primary risk, same budget | 19.3400% [14.7175%, 24.0723%] | 25.79% | 188.19 | 360.59 ms |
| Cross-fitted resource ceiling | 18.7993% [14.3204%, 23.4304%] | 27.87% | 187.90 | 360.44 ms |

The resource ceiling's deadline-miss upper interval is 23.4304%, not below
the frozen 0.4% target.  Its relative-improvement lower interval is 23.44%,
not at least 50%.  Both gates fail.

The more important comparison is myopic risk versus the resource ceiling.
Myopic risk is only 0.5407 percentage points worse and realizes 92.55% of the
ceiling's deadline gain, with a 95% interval of [89.47%, 95.71%].  Uniform
random realizes 58.74% [51.75%, 64.33%].  Thus risk ranking is valuable, but
the current distributional treatment-benefit score leaves only a small
increment over a much simpler primary-risk ranker at the same resource limit.

## Family result

| Held-out family | No-copy miss | Resource-ceiling miss | Relative reduction | Conservative OOD fallback |
| --- | ---: | ---: | ---: | ---: |
| Radio propagation | 8.9076% | 6.1038% | 31.48% | 0.52% |
| OBSS intensity | 40.1887% | 31.1203% | 22.56% | 0.79% |
| OBSS geometry/MAC | 2.7519% | 0.8097% | 70.58% | 0.00% |
| Video workload | 2.8637% | 0.6691% | 76.63% | 62.50% |
| Legacy coexistence | 43.7687% | 30.6116% | 30.06% | 0.64% |
| Compound shift | 57.8900% | 43.4812% | 24.89% | 8.00% |

The absolute aggregate is dominated by genuinely severe, equally weighted
scenarios in OBSS intensity, legacy coexistence, and compound shift.  A raw
factual-arm sanity check finds 25 of 96 scenarios with at least 50% control
misses and nine with at least 90%; this is a diagnostic of the predeclared
broad domain, not a change to the frozen estimand.

The full-copy action remains valuable.  The reported equal-family doubly
robust mean deadline-CDF gain for treating every eligible row is 15.7041
percentage points.  Subtracting it from the 26.0618% no-copy estimate implies
about 10.3577% misses, a 60.26% relative reduction, before any resource limit.
This all-action sensitivity has no 372 ms/run feasibility and is not a
closed-loop performance claim.  Under the resource limit, the ceiling can
realize less than half of that absolute treatment benefit because risky frames
are too numerous.

## Prediction and OOD diagnosis

Cross-family ranking remains strong: control deadline-miss AUC is 0.9746 and
treated deadline-rescue AUC is 0.9165.  The failure is therefore not evidence
that the predictor learned nothing.  It says that a modest ranker improvement
cannot make the present coarse action cover the broad severe-case demand at
the frozen airtime budget.

The video-workload holdout exposes a separate support problem.  Its hard OOD
fallback covers 62.5% of eligible rows, and 10,463 of the resource ceiling's
72,153 selected rows are marked for fallback across all families.  The
resource ceiling reports this overlap but does not apply the deployment
fallback.  A safe deployable policy should therefore be expected to do worse
unless training data spans the discrete workload values and their interactions
more densely.  Observable environment variables are present; coverage, not
their absence, is the immediate issue.

## Scientific decision

Do not treat this result as a reason to cancel the already-frozen held-out
closed-loop campaign.  Randomized replay omits startup, action-dirty rows,
queue feedback, sender airtime, background throughput, completed-frame P99,
and STR MLO.  The 576-run STR/V2/distributional-shadow campaign is still
needed to establish actual value on unseen scenarios.

If that campaign confirms the same severe-family ceiling, stop expecting a
larger scalar ranker alone to supply the missing gain.  The next action-space
candidate should provide a finer reliability/cost control, such as systematic
erasure-coded striping or adaptive repair.  In parallel, the next randomized
dataset should add dense compound coverage of workload categories and
environment interactions so that OOD fallback is exceptional rather than the
majority behavior of an entire family.

## Evidence boundary and sparse support

The dataset contains 307,689 action-clean eligible T2 rows.  One source run,
`79c0388a0d75a32f2909` (seed `20379`, compound scenario `p14`), has no eligible
row; the other three runs in that scenario contribute 16 rows.  Policy values
condition that scenario on its three represented runs, while resource means
retain all 384 source runs and assign the empty run zero actions and cost.  The
support amendment was committed before any policy outcome was published or
read.

The randomized oracle estimates only the eligible action-clean population.
It must not be subtracted from an all-generated closed-loop miss rate to claim
a fraction of oracle gain.  That future qualification field is not assessable
without a sequential causal estimand spanning startup, ineligible, and
action-history-contaminated frames.

## Provenance and complete archive

- Collection experiment-manifest SHA-256:
  `b306ea8384f99413834760978a2ef76fb9969f35df2cd8ef0930aed3565652c9`.
- Analysis top-manifest SHA-256:
  `3c5bdcae98e75e91332ec3821d7555076cdead926d6a1746a09a1359d83a6a3e`.
- Corrected support and resume checkout: `3edf1ad`.
- Complete analysis archive: 171,531,136 bytes, SHA-256
  `ca0bede79bfd9ee0cc067a8f080a22cab71dc60b05f9475dc83e35c2aa39b7f0`.
- Remote archive path:
  `/home/jingweili/environment-generalization-analysis-3edf1ad.tar.zst`.
- Ignored local extraction:
  `results/environment_generalization_analysis_8c38753_916bb9a`.

The archive passes `zstd -t`.  Every file declared by all four nested stage
manifests was independently rehashed after remote publication and again after
local extraction.  The complete archive contains the omitted 806.7 MB dataset,
54.5 MB LOFO prediction stream, and 10.9 MB row-action stream.

## Compact artifacts

- `analysis_pipeline_manifest.json`: top-level source, software, input, and
  nested-manifest closure, including the verified-prefix resume disclosure.
- `dataset/`: compact metadata and the full dataset's artifact manifest.
- `lofo/`: predictor/calibration/OOD metrics and artifact manifest; the large
  row prediction stream is retained only in the complete archive.
- `policy/`: policy values, uncertainty, family values, report, and artifact
  manifest; the large row-action stream is retained only in the complete
  archive.
- `plots/`: all five reviewed figures and their artifact manifest.

## Reproduction

Restore the checksum-bound randomized run root, then run from a clean checkout:

```bash
MPLCONFIGDIR=/tmp/wifi-streaming-environment-matplotlib \
  python3 tools/run_environment_generalization_analysis.py \
  --run-root RANDOMIZED_RUN_ROOT \
  --output-root OUTPUT_ROOT \
  --config experiments/configs/environment_generalization_randomized_collection_v1.yaml
```

The `--resume-completed-prefix` option is only for a root containing exactly a
fully hash-validated `dataset/` and `lofo/` prefix after a later-stage failure.
