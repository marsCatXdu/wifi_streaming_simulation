# Offline latency-risk prediction evaluation

Analysis schema version: 1
Dataset: `/home/lijingwei/Desktop/work/wifi_streaming_simulation/results/genuine_polling_v1/dataset/labelled_samples.parquet`
Rows scanned: 23,961,600
Peak RSS: 4378.8 MiB
Runtime: 5381.5 seconds

## Frozen selections

| Link | Stage | Feature set | Model |
|---:|---|---|---|
| 0 | T0 | F0 | logistic_regression |
| 0 | T0 | F0+F1-ideal | histogram_gradient_boosting |
| 0 | T0 | F0+F1-ideal+F2 | histogram_gradient_boosting |
| 0 | T0 | F0+F1-ideal+F2-exportable | histogram_gradient_boosting |
| 0 | T0 | F0+F1-ideal+F2+F3 | histogram_gradient_boosting |
| 0 | T0 | F0+F1-polling-1ms | histogram_gradient_boosting |
| 0 | T0 | F0+F1-polling-1ms+F2 | histogram_gradient_boosting |
| 0 | T0 | F0+F1-polling-1ms+F2-exportable | histogram_gradient_boosting |
| 1 | T0 | F0 | logistic_regression |
| 1 | T0 | F0+F1-ideal | histogram_gradient_boosting |
| 1 | T0 | F0+F1-ideal+F2 | histogram_gradient_boosting |
| 1 | T0 | F0+F1-ideal+F2-exportable | histogram_gradient_boosting |
| 1 | T0 | F0+F1-ideal+F2+F3 | histogram_gradient_boosting |
| 1 | T0 | F0+F1-polling-1ms | histogram_gradient_boosting |
| 1 | T0 | F0+F1-polling-1ms+F2 | histogram_gradient_boosting |
| 1 | T0 | F0+F1-polling-1ms+F2-exportable | histogram_gradient_boosting |
| 0 | T1 | F0 | logistic_regression |
| 0 | T1 | F0+F1-ideal | histogram_gradient_boosting |
| 0 | T1 | F0+F1-ideal+F2 | histogram_gradient_boosting |
| 0 | T1 | F0+F1-ideal+F2-exportable | histogram_gradient_boosting |
| 0 | T1 | F0+F1-ideal+F2+F3 | histogram_gradient_boosting |
| 0 | T1 | F0+F1-polling-1ms | histogram_gradient_boosting |
| 0 | T1 | F0+F1-polling-1ms+F2 | histogram_gradient_boosting |
| 0 | T1 | F0+F1-polling-1ms+F2-exportable | histogram_gradient_boosting |
| 1 | T1 | F0 | logistic_regression |
| 1 | T1 | F0+F1-ideal | histogram_gradient_boosting |
| 1 | T1 | F0+F1-ideal+F2 | histogram_gradient_boosting |
| 1 | T1 | F0+F1-ideal+F2-exportable | histogram_gradient_boosting |
| 1 | T1 | F0+F1-ideal+F2+F3 | histogram_gradient_boosting |
| 1 | T1 | F0+F1-polling-1ms | histogram_gradient_boosting |
| 1 | T1 | F0+F1-polling-1ms+F2 | histogram_gradient_boosting |
| 1 | T1 | F0+F1-polling-1ms+F2-exportable | histogram_gradient_boosting |
| 0 | T2 | F0 | logistic_regression |
| 0 | T2 | F0+F1-ideal | histogram_gradient_boosting |
| 0 | T2 | F0+F1-ideal+F2 | histogram_gradient_boosting |
| 0 | T2 | F0+F1-ideal+F2-exportable | histogram_gradient_boosting |
| 0 | T2 | F0+F1-ideal+F2+F3 | histogram_gradient_boosting |
| 0 | T2 | F0+F1-polling-1ms | histogram_gradient_boosting |
| 0 | T2 | F0+F1-polling-1ms+F2 | histogram_gradient_boosting |
| 0 | T2 | F0+F1-polling-1ms+F2-exportable | histogram_gradient_boosting |
| 1 | T2 | F0 | logistic_regression |
| 1 | T2 | F0+F1-ideal | histogram_gradient_boosting |
| 1 | T2 | F0+F1-ideal+F2 | histogram_gradient_boosting |
| 1 | T2 | F0+F1-ideal+F2-exportable | histogram_gradient_boosting |
| 1 | T2 | F0+F1-ideal+F2+F3 | histogram_gradient_boosting |
| 1 | T2 | F0+F1-polling-1ms | histogram_gradient_boosting |
| 1 | T2 | F0+F1-polling-1ms+F2 | histogram_gradient_boosting |
| 1 | T2 | F0+F1-polling-1ms+F2-exportable | histogram_gradient_boosting |
| 0 | T4 | F0 | logistic_regression |
| 0 | T4 | F0+F1-ideal | histogram_gradient_boosting |
| 0 | T4 | F0+F1-ideal+F2 | histogram_gradient_boosting |
| 0 | T4 | F0+F1-ideal+F2-exportable | histogram_gradient_boosting |
| 0 | T4 | F0+F1-ideal+F2+F3 | histogram_gradient_boosting |
| 0 | T4 | F0+F1-polling-1ms | histogram_gradient_boosting |
| 0 | T4 | F0+F1-polling-1ms+F2 | histogram_gradient_boosting |
| 0 | T4 | F0+F1-polling-1ms+F2-exportable | histogram_gradient_boosting |
| 1 | T4 | F0 | logistic_regression |
| 1 | T4 | F0+F1-ideal | histogram_gradient_boosting |
| 1 | T4 | F0+F1-ideal+F2 | histogram_gradient_boosting |
| 1 | T4 | F0+F1-ideal+F2-exportable | histogram_gradient_boosting |
| 1 | T4 | F0+F1-ideal+F2+F3 | histogram_gradient_boosting |
| 1 | T4 | F0+F1-polling-1ms | histogram_gradient_boosting |
| 1 | T4 | F0+F1-polling-1ms+F2 | histogram_gradient_boosting |
| 1 | T4 | F0+F1-polling-1ms+F2-exportable | histogram_gradient_boosting |

## Qualified recommendation

- Prediction recommendation: `insufficient_data`.
- Modified-driver support: `fail`.
- Per-link outcomes remain separate in `go_no_go.json`.

## Evidence status and limitations

- No required OOD scenario is below its frozen matched-run-group minimum.

The ranking-budget cutoff uses the complete evaluated population and is an
upper bound on a fixed online threshold. The packet-count heuristic is an
additive baseline, not an airtime or completion-time estimator under A-MPDU.
No result authorizes an adaptive simulation action.
