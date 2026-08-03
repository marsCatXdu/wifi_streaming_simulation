# Key experiment results

This directory is a compact, version-controlled snapshot of the principal
experimental evidence generated through 2026-08-02. It intentionally excludes
raw per-run directories, packet traces, frame-score streams, and the 2 GB
labelled dataset. Those artifacts remain under the ignored `results/` tree.

The prediction artifacts under `03_prediction_pipeline/` are legacy,
one-frame-delayed evidence and are no longer formal claims. See
`03_prediction_pipeline/LEGACY_FRAME_DELAYED_NOTICE.md`. Corrected
frame-independent 1 ms polling evidence is preserved under
`04_genuine_polling_v1/`. The adaptive-airtime v1 snapshot under
`05_adaptive_airtime_obss_v1/` is retained only as an invalidated audit artifact.

## Contents

| Directory | Experiment | Included evidence |
|---|---|---|
| `01_streaming_five_way/combined_contention` | Five sender approaches with OBSS and legacy contention | Aggregate CSV/JSON and eight latency, deadline, burst, redundancy, and background figures |
| `01_streaming_five_way/obss_only` | The same five approaches with OBSS-only contention | Aggregate CSV/JSON and the same eight figures |
| `02_ul_ofdma/combined_contention` | UL OFDMA disabled/enabled under combined contention | Configuration description, aggregate tables, paired comparisons, and per-approach latency distributions |
| `02_ul_ofdma/obss_only` | UL OFDMA disabled/enabled under OBSS-only contention | Configuration description, aggregate tables, paired comparisons, and per-approach latency distributions |
| `03_prediction_pipeline/telemetry_acceptance` | Legacy frame-aligned telemetry acceptance | Retained, non-formal reconciliation audit |
| `03_prediction_pipeline/load_pilots` | Legacy load selection | Retained, non-formal pilot table |
| `03_prediction_pipeline/dataset` | Legacy prediction dataset | Retained manifest and validation metadata |
| `03_prediction_pipeline/offline_evaluation` | Legacy frame-delayed predictability evaluation | Retained, non-formal report and metrics |
| `03_prediction_pipeline/online_replay_10pct` | Legacy frame-delayed online replay | Retained, non-formal 10% operating point |
| `03_prediction_pipeline/online_replay_30pct` | Legacy frame-delayed online replay | Retained, non-formal 30% operating point |
| `04_genuine_polling_v1/dataset` | Corrected frame-independent 1 ms polling dataset | Manifest, splits, and validation evidence |
| `04_genuine_polling_v1/offline_evaluation` | Corrected prediction evaluation | Metrics, go/no-go record, report, calibration, recall, and importance figures |
| `04_genuine_polling_v1/closed_loop_obss_threshold_020` | Closed-loop duplication under OBSS at threshold 0.20 | Ten paired groups, aggregate evidence, control summaries, and figures |
| `04_genuine_polling_v1/closed_loop_obss_threshold_015` | Repaired closed-loop duplication under OBSS at threshold 0.15 | Ten paired groups, repair provenance, aggregate evidence, and figures |
| `04_genuine_polling_v1/closed_loop_combined_threshold_020` | Closed-loop duplication under combined contention at threshold 0.20 | Seven paired groups, aggregate evidence, control summaries, and figures |
| `04_genuine_polling_v1/predicted_risk_threshold_020` | Runtime calibrated-risk distributions | Conditional-action and unconditional PDF/CDF plots |
| `05_adaptive_airtime_obss_v1` | Invalidated adaptive-airtime OBSS v1 | Historical aggregates and figures retained for audit; not valid evidence |

## Principal streaming results

All five-way values below are means over ten runs. The deadline is 33.333 ms.

### Combined contention

| Approach | Deadline-miss ratio | P99 completion latency | Redundant-byte ratio |
|---|---:|---:|---:|
| Single 2.4 GHz interface | 25.63% | 31.64 ms | 0% |
| Single 5 GHz interface | 27.31% | 31.53 ms | 0% |
| Application full duplication | 7.21% | 29.33 ms | 50% |
| MLO, `NMaxInflights=1` | 15.18% | 30.41 ms | 0% |
| MLO, `NMaxInflights=2` | 98.17% | 8.21 ms among completed frames | 0% |

The low P99 for `NMaxInflights=2` is not a good outcome: almost all frames are
incomplete or miss the deadline, so the latency statistic describes only the
small surviving subset. Application duplication gives the strongest usable
tail reduction, at the cost of sending half of transmitted application bytes
as redundant copies.

### OBSS-only contention

| Approach | Deadline-miss ratio | P99 completion latency | Redundant-byte ratio |
|---|---:|---:|---:|
| Single 2.4 GHz interface | 3.29% | 25.94 ms | 0% |
| Single 5 GHz interface | 1.15% | 19.20 ms | 0% |
| Application full duplication | 0.07% | 10.71 ms | 50% |
| MLO, `NMaxInflights=1` | 0.88% | 20.98 ms | 0% |
| MLO, `NMaxInflights=2` | 85.98% | 8.07 ms among completed frames | 0% |

The OBSS-only environment is materially easier than combined contention.
Full application duplication nearly eliminates misses, while
`NMaxInflights=2` again behaves pathologically rather than as deterministic
"send both copies and keep the first" duplication.

The OFDMA directories preserve the paired disabled/enabled evidence separately.
The most useful entry figures are `ofdma_group_comparison.png`,
`ofdma_paired_differences.png`, and each file under `figures/` named for an
individual sender approach.

## Prediction pipeline results

### Telemetry and dataset

- Increment 1 records schema-version-2 causal samples and MPDU lifecycle events;
  the included audit is the acceptance evidence.
- The final load review contains 195 pilot runs spanning 65 candidates.
- The provisional dataset validation passes all causal, identity, provenance,
  support-mask, split-isolation, and checksum checks.
- Dataset size: 388 runs, 194 matched run groups, 698,400 frames, 2,793,600
  stage samples, and 71,431 deadline misses.
- OOD coverage is asymmetric: 24 matched `obss_only` groups but only 10
  `obss_plus_legacy_mixed8` groups.

### Offline prediction

At T1 and a global 10% ranking budget:

| Link | Feature set | ID miss recall | Fixed-threshold recall | Action rate |
|---:|---|---:|---:|---:|
| 2.4 GHz | Application only (F0) | 12.23% | 4.11% | 1.67% |
| 2.4 GHz | F0 + ideal commodity telemetry (F1) | 61.85% | 42.04% | 4.50% |
| 2.4 GHz | F0 + F1 + driver telemetry (F2) | 62.40% | 42.15% | 4.51% |
| 5 GHz | Application only (F0) | 10.94% | 2.70% | 1.67% |
| 5 GHz | F0 + ideal commodity telemetry (F1) | 48.14% | 29.01% | 4.52% |
| 5 GHz | F0 + F1 + driver telemetry (F2) | 48.43% | 29.72% | 4.64% |

The dominant gain comes from F1. Exportable F2 adds only 0.55 percentage
points on 2.4 GHz and 0.29 percentage points on 5 GHz, below the frozen
10-point modified-driver criterion. The formal recommendation remains
`insufficient_data` overall: 5 GHz fails its ID screen, and mixed8 OOD has only
10 of the required 20 matched groups.

### Causal 5 GHz online replay

The replay processes frames chronologically, uses frozen calibrated models,
and never reads outcomes before making a decision. Warnings are hypothetical;
the recorded deadline outcomes are unchanged.

At risk threshold 0.2:

| Test population | 10% frame budget recall | 30% frame budget recall | 30% precision | Realized 30% action rate |
|---|---:|---:|---:|---:|
| ID, familiar conditions | 12.46% | 36.32% | 26.45% | 17.26% |
| All OOD | 13.36% | 35.28% | 43.16% | 6.83% |
| OOD: OBSS only | 10.52% | 12.56% | 23.05% | 0.80% |
| OOD: OBSS + legacy mixed8 | 13.76% | 38.51% | 44.98% | 21.29% |

Increasing the frame budget helps ID and mixed8 substantially. It does almost
nothing for OBSS-only because most misses score below threshold; that is a
generalization/calibration failure rather than token scarcity. All warnings in
the representative replay retain about 29.3-33.3 ms before the deadline.

The global offline Top-K values in the reports are optimistic references, not
deployable online results: they rank a complete future population and can move
budget between scenarios. At a 30% global budget they report 72.9% ID and
88.9% aggregate OOD recall, versus approximately 36% and 35% in causal replay.

## Corrected genuine-polling results

The corrected dataset uses a frame-independent 1 ms polling clock with a 1 ms
report delay. It contains 416 runs, 208 matched groups, 748,800 frames, and
80,650 deadline misses. Both required OOD scenarios now have 24 matched groups,
so the old ten-group insufficiency does not apply.

At T1 on the deployed 5 GHz commodity-polling feature set, ID average precision
is 0.5785 and ROC-AUC is 0.8919. The frozen per-link recommendations differ,
leaving the aggregate recommendation `insufficient_data`; modified-driver
support remains `fail`.

Under OBSS-only contention, threshold 0.15 selective duplication reduces the
mean miss ratio from 1.233% for single 5 GHz to 0.822%, with 1.779% redundant
bytes. It launches 316 of 18,000 frames, incurs no budget suppressions, and uses
5.85% of the configured 30% long-run frame budget. Full duplication reaches
0.061% misses at 50% redundant bytes.

Under combined contention at threshold 0.20, seven complete paired groups show
11.429% misses for single 5 GHz, 6.270% for selective duplication, and 2.325%
for full duplication. Selective duplication uses 13.619% redundant bytes and
encounters 220 budget suppressions.

See `04_genuine_polling_v1/README.md` for the complete compact table and
interpretation limits.

## Adaptive airtime duplication under OBSS

The original thirty-seed v1 snapshot is invalidated. Its runner deliberately
recorded commit `a829356` while executing later source, and the controller,
meter, decision ledger, and validator contained accounting defects. The old
numbers are therefore not reproduced here as findings. They remain under
`05_adaptive_airtime_obss_v1/` only to make the provenance failure auditable.

The corrected matrix is `closed-loop-adaptive-airtime-obss-v2`, writing to
`results/adaptive_airtime_obss_v2/runs`. No corrected headline result is claimed
until that matrix has completed and passed the strict event/settlement validator.

## Figure guide

For the streaming experiments:

- `latency_cdf.png` and `latency_pdf.png`: completion-latency distributions.
- `deadline_miss.png`: mean deadline-miss ratio with uncertainty.
- `p99_redundancy_pareto.png`: tail latency versus redundant traffic.
- `miss_burst_distribution*.png`: whether misses are isolated or clustered.
- `background_degradation.png`: impact on competing traffic.
- `duplication_benefit_correlation.png`: copy correlation and duplication gain.

For online prediction:

- `*_recall_heatmap.png`: fraction of actual misses warned about for each fixed
  threshold and online budget.
- `*_precision_heatmap.png`: fraction of warnings that correspond to misses.
- `*_recall_tradeoff.png`: realized action rate versus recall.
- `*_miss_outcomes.png`: warned, budget-suppressed, and below-threshold misses.
- `warning_lead_time_cdf.png`: time remaining before the deadline at action.

## Interpretation limits

1. A warning is not a prevented miss. Prevention requires closed-loop
   simulation with a defined rescue action.
2. OOD aggregate results hide the severe split between OBSS-only and mixed8.
3. `NMaxInflights=2` is opportunistic ns-3 MLO in-flight behavior, not
   guaranteed application-equivalent duplication.
4. The offline evaluation remains provisional because mixed8 OOD evidence is
   below its frozen minimum.
5. The copied aggregate tables remain the authoritative numeric evidence;
   rounded values in this README are navigation summaries.
6. Adaptive-airtime OBSS v1 is explicitly invalidated and is not among the
   authoritative numeric evidence.
