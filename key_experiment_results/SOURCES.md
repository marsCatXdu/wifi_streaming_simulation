# Source artifact map

The files in this snapshot were copied without numeric transformation from the
following ignored result roots:

| Snapshot directory | Source result root |
|---|---|
| `01_streaming_five_way/combined_contention` | `results/combined_contention_realistic_gop_five_way` |
| `01_streaming_five_way/obss_only` | `results/obss_contention_realistic_gop_five_way` |
| `02_ul_ofdma/combined_contention` | `results/combined_contention_ul_ofdma_comparison` |
| `02_ul_ofdma/obss_only` | `results/obss_contention_ul_ofdma_comparison` |
| `03_prediction_pipeline/telemetry_acceptance` | `results/prediction_telemetry_increment1_acceptance` |
| `03_prediction_pipeline/load_pilots` | `results/prediction_load_pilot_review_v3` |
| `03_prediction_pipeline/dataset` | `results/prediction_dataset_provisional` |
| `03_prediction_pipeline/offline_evaluation` | `results/prediction_evaluation_provisional` |
| `03_prediction_pipeline/online_replay_10pct` | `results/prediction_online_replay_v1` |
| `03_prediction_pipeline/online_replay_30pct` | `results/prediction_online_replay_frame30_v1` |
| `04_genuine_polling_v1/dataset` | `results/genuine_polling_v1/dataset` |
| `04_genuine_polling_v1/offline_evaluation` | `results/genuine_polling_v1/evaluation` |
| `04_genuine_polling_v1/closed_loop_obss_threshold_020` | `results/genuine_polling_v1/closed_loop_obss/runs` |
| `04_genuine_polling_v1/closed_loop_obss_threshold_015` | `results/genuine_polling_v1/closed_loop_obss_threshold_015/runs` plus repair provenance from its `extras/` directory |
| `04_genuine_polling_v1/closed_loop_combined_threshold_020` | `results/genuine_polling_v1/closed_loop_combined/runs` |
| `04_genuine_polling_v1/predicted_risk_threshold_020` | `results/genuine_polling_v1/predicted_risk_duplication_threshold_020` and `results/genuine_polling_v1/predicted_risk_unconditional_threshold_020` |
| `05_adaptive_airtime_obss_v1` | `results/adaptive_airtime_obss_v1/runs` (invalidated; audit only) |
| `06_paired_value_t2_str_qualification_v1` | `results/paired_value_t2_str_qualification_da48d7d` |
| `07_score_aware_t2_str_engineering_v2` | `results/paired_value_t2_score_aware_str_engineering_v2/runs` |
| `08_full_horizon_t2_str_engineering_v3` | `results/paired_value_t2_full_horizon_str_engineering_v3/runs` |
| `09_remaining_refill_t2_str_engineering_v4` | `results/paired_value_t2_remaining_refill_str_engineering_v4/runs` plus the exact V3/V4 comparison in its result-root directory |

The original 10% replay includes both frame and byte budgets. The separate 30%
snapshot is frame-only and includes the complete budget grid through 30%
without modifying the original replay directory.

The complete V2, V3, and V4 raw archives are intentionally not committed
because they are 94,939,663, 95,546,464, and 96,983,224 compressed bytes,
respectively.  Their local paths and SHA-256 identities are recorded in each
snapshot README; publishing those exact archives as release assets remains a
reproducibility task.

`SHA256SUMS` covers every curated artifact except itself. It can be checked
from the repository root with:

```bash
sha256sum --check key_experiment_results/SHA256SUMS
```
