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

The original 10% replay includes both frame and byte budgets. The separate 30%
snapshot is frame-only and includes the complete budget grid through 30%
without modifying the original replay directory.

`SHA256SUMS` covers every curated artifact except itself. It can be checked
from the repository root with:

```bash
sha256sum --check key_experiment_results/SHA256SUMS
```
