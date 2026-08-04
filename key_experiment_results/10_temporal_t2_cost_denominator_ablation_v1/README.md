# Frozen temporal-T2 cost-denominator ablation

This directory archives a retrospective test of one narrow ranking question:
does dividing the frozen temporal-T2 value heads by a learned per-frame
secondary-airtime estimate improve or harm admission order? No model was
refit. The outcome heads, evaluation nuisances, feature families, grouped
train/calibration/test split, frame gates, action fractions, and feasibility
gates are inherited from the canonical primary-only temporal-T2 artifact.

The analysis uses only the randomized intervention seeds `1101` through
`1196`. The 16-run engineering-test role had already been opened by the V1
training analysis, so all findings here are development evidence rather than
confirmation. Reserved seeds `1301` through `1348` were not read.

## Result

The learned cost divisor harms the ranking. At the same 15% requested action
fraction, on the same P-frame gate and 246-feature temporal family, removing
only the divisor improves both calibrated objectives while reducing estimated
airtime:

| Calibration policy | Deadline improvement | Completed-late18 improvement | Worst objective | DR airtime (us/eligible frame) |
| --- | ---: | ---: | ---: | ---: |
| Frozen bad12 value / learned cost | 79.99% | 61.79% | 61.79% | 372.63 |
| Frozen bad12 value, no divisor | 82.40% | 65.40% | 65.40% | 360.05 |

Thus the gain is not merely caused by allowing more actions. The cost-free
ranker chooses a better 15% subset and its estimated cost is lower.

Selection over the predeclared cost-free grid chooses the same temporal
family, P-frame gate, and bad12 value signal at a 16.5% requested action
fraction. Its calibrated worst-objective improvement is 68.43%, versus 61.79%
for the frozen source. Calibrated DR airtime rises to 398.05 us per eligible
frame, just below the frozen 400 us ceiling.

On the already-opened engineering-test role:

| Metric | Frozen source | Cost-free winner | Paired winner-minus-source estimate, 95% interval |
| --- | ---: | ---: | ---: |
| Deadline-miss probability | 0.5229% | 0.4723% | -0.0506 pp `[-0.1016, -0.0006]` pp |
| Completed-late18 ratio | 1.5918% | 1.4889% | -0.1029 pp `[-0.1861, -0.0313]` pp |
| DR airtime (us/eligible frame) | 365.49 | 395.96 | +30.47 `[+20.65, +39.52]` |
| Action fraction | 16.19% | 17.68% | +1.49 pp `[+1.11, +1.82]` pp |

The whole-run paired intervals favor the cost-free policy on both outcomes,
but the miss interval only narrowly excludes zero. These estimates describe
the action-clean common-T2-eligible population. They do not prove an
all-generated-frame or closed-loop improvement under the V2 airtime guard.

## Scientific decision

Do not use the learned cost estimate as the score denominator. Retain it only
where conservative runtime reservation needs a cost estimate. The same-score
comparison at 15% provides the cleanest mechanism evidence: per-frame cost
prediction adds enough ranking noise to select a worse and more expensive
subset.

This is a useful correction, not the final predictor. The selected signal is
still legacy bad12 value rather than the explicit deadline or completed-tail
head, and the winning point nearly saturates the offline airtime ceiling. The
next offline boundary is therefore to add causally available, action-clean
secondary-path state to the treated-outcome model while retaining cost-free
ranking as the baseline. That directly represents whether an identical copy
on 2.4 GHz can rescue the union outcome. Compare that finite candidate set on
the same opened split before defining another closed-loop VM campaign.

## Artifacts

- `temporal_t2_cost_ablation_candidates.csv`: all 384 paired calibration
  candidates, including the 192 cost-free candidates.
- `temporal_t2_cost_ablation_metrics.json`: selected policies, frozen-source
  replay, individual and paired whole-run uncertainty, provenance, and
  interpretation guards.
- `temporal_t2_cost_ablation.md`: compact generated report.
- `temporal_t2_cost_ablation.png`: calibration curves, objective plane,
  engineering-test outcomes, paired intervals, and resource proxies.
- `artifact_manifest.json`: SHA-256 closure over the generated artifacts.

## Evidence and provenance

- Evaluator and paired-bootstrap commit:
  `8d8f2460781c65c8a735a49e974c5358a6813097`.
- Clean-worktree output provenance: `true`.
- Output artifact-manifest SHA-256:
  `1b082c5d908e00067256512b037871233350ca07df2d6fba9b7fe3f9ddb0b549`.
- Metrics SHA-256:
  `e7a4452738e940e2a28ad475fc6213fc4a4c5ea9c195e5b5a8d60b2f4cca2574`.
- Candidate-table SHA-256:
  `dac3ee0e34f39f0bb89a05d1c15b280ff92fda38f2ad2070c63e6bc20b8d66b7`.
- Figure SHA-256:
  `9168deef99a4fed57cf62362bf981a81f0ad8e6db09a226df7e00818b1389106`.
- Source model artifact-manifest SHA-256:
  `b3af02b647c7671a631f3d43ebece75781989889358c845335d4003610a8208f`.
- Action-clean temporal dataset artifact-manifest SHA-256:
  `87d630a66f460b46a31245f56da2a8110091c42dc5fd499416e2d82d697d0314`.

## Reproduction

From the repository root with the ignored temporal dataset and source model
artifact restored:

```bash
MPLCONFIGDIR=/tmp/wifi-streaming-matplotlib .venv/bin/python \
  tools/evaluate_temporal_t2_cost_ablation.py \
  results/randomized_full_copy_exploration_collection_v1/temporal_dataset \
  results/randomized_full_copy_exploration_collection_v1/\
temporal_t2_primary_only_two_objective_v1 \
  --output-dir results/randomized_full_copy_exploration_collection_v1/\
temporal_t2_cost_denominator_ablation_v1
```
