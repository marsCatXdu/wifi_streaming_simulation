# Genuine 1 ms polling and closed-loop duplication

This directory is the compact evidence snapshot for the corrected
frame-independent 1 ms polling pipeline and its closed-loop selective
duplication experiments. Raw per-frame samples and the Parquet dataset remain
under `results/genuine_polling_v1/`.

## Dataset and offline prediction

The schema-version-2 dataset contains 416 runs, 208 matched run groups,
748,800 frames, 2,995,200 decision-stage samples, and 80,650 deadline misses.
Both required OOD scenarios contain 24 matched groups, and every dataset
validation check passes.

For the deployed commodity 1 ms polling feature set on the 5 GHz link at T1,
the ID evaluation reports average precision 0.5785, ROC-AUC 0.8919, Brier
score 0.0689, and calibration error 0.0112. The frozen per-link conclusions
differ: link 0 is `go_limited_domain`, link 1 is `go_ranking_only`, and the
combined recommendation therefore remains `insufficient_data`. This label is
not caused by missing OOD run groups. Modified-driver support fails because
exportable F2 does not meet its frozen incremental-gain requirement.

## Closed-loop results

All values are means across paired runs within the indicated experiment.
The selective controller uses a 30% frame-token budget and a 30-frame burst
horizon.

| Scenario | Threshold | Paired groups | Approach | Miss ratio | P99 latency | Redundant bytes |
|---|---:|---:|---|---:|---:|---:|
| OBSS only | 0.20 | 10 | Single 5 GHz | 1.150% | 19.20 ms | 0% |
| OBSS only | 0.20 | 10 | Selective duplication | 0.828% | 18.79 ms | 1.137% |
| OBSS only | 0.20 | 10 | Full duplication | 0.067% | 10.71 ms | 50% |
| OBSS only | 0.20 | 10 | MLO `NMaxInflights=1` | 0.878% | 20.98 ms | 0% |
| OBSS only | 0.15 | 10 | Single 5 GHz | 1.233% | 19.29 ms | 0% |
| OBSS only | 0.15 | 10 | Selective duplication | 0.822% | 18.33 ms | 1.779% |
| OBSS only | 0.15 | 10 | Full duplication | 0.061% | 10.20 ms | 50% |
| OBSS only | 0.15 | 10 | MLO `NMaxInflights=1` | 0.828% | 19.74 ms | 0% |
| OBSS + legacy contention | 0.20 | 7 | Single 5 GHz | 11.429% | 30.43 ms | 0% |
| OBSS + legacy contention | 0.20 | 7 | Selective duplication | 6.270% | 29.27 ms | 13.619% |
| OBSS + legacy contention | 0.20 | 7 | Full duplication | 2.325% | 25.42 ms | 50% |
| OBSS + legacy contention | 0.20 | 7 | MLO `NMaxInflights=1` | 8.024% | 29.13 ms | 0% |

At threshold 0.20, selective duplication launches 213 of 18,000 OBSS-only
frames with no budget suppressions. Under combined contention it launches
1,827 of 12,600 frames and records 220 budget suppressions. At threshold 0.15,
the repaired OBSS experiment launches 316 of 18,000 frames with no budget
suppressions, using 1.76% of frames or 5.85% of the configured long-run budget.

The two OBSS threshold experiments use different complete paired seed sets,
so their aggregate means are not a fully paired threshold comparison.

## Directory guide

- `dataset/`: corrected dataset manifest, split assignment, and validation.
- `offline_evaluation/`: formal metrics, report, decision record, and plots.
- `closed_loop_obss_threshold_020/`: ten paired OBSS-only groups.
- `closed_loop_obss_threshold_015/`: ten repaired paired OBSS-only groups.
- `closed_loop_combined_threshold_020/`: seven paired combined groups.
- `predicted_risk_threshold_020/`: conditional-action and unconditional
  calibrated-risk PDF/CDF plots.

The unconditional risk plots include every T0/T1/T2/T4 score. Scores after an
earlier duplication are observed under that action and are not no-action
counterfactual predictions.
