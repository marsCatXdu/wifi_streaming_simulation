# Offline latency-risk prediction evaluation

Analysis schema version: 1
Dataset: `/home/lijingwei/streamingPaper/wifi_streaming_simulation/results/prediction_dataset_provisional/labelled_samples.csv`
Rows scanned: 22,348,800
Peak RSS: 2101.2 MiB
Runtime: 4844.1 seconds

## Frozen selections

| Link | Stage | Feature set | Model |
|---:|---|---|---|
| 0 | T0 | F0 | logistic_regression |
| 0 | T0 | F0+F1-ideal | histogram_gradient_boosting |
| 0 | T0 | F0+F1-ideal+F2 | histogram_gradient_boosting |
| 0 | T0 | F0+F1-ideal+F2-exportable | histogram_gradient_boosting |
| 0 | T0 | F0+F1-ideal+F2+F3 | histogram_gradient_boosting |
| 1 | T0 | F0 | logistic_regression |
| 1 | T0 | F0+F1-ideal | histogram_gradient_boosting |
| 1 | T0 | F0+F1-ideal+F2 | histogram_gradient_boosting |
| 1 | T0 | F0+F1-ideal+F2-exportable | histogram_gradient_boosting |
| 1 | T0 | F0+F1-ideal+F2+F3 | histogram_gradient_boosting |
| 0 | T1 | F0 | logistic_regression |
| 0 | T1 | F0+F1-ideal | histogram_gradient_boosting |
| 0 | T1 | F0+F1-ideal+F2 | histogram_gradient_boosting |
| 0 | T1 | F0+F1-ideal+F2-exportable | histogram_gradient_boosting |
| 0 | T1 | F0+F1-ideal+F2+F3 | histogram_gradient_boosting |
| 1 | T1 | F0 | logistic_regression |
| 1 | T1 | F0+F1-ideal | histogram_gradient_boosting |
| 1 | T1 | F0+F1-ideal+F2 | histogram_gradient_boosting |
| 1 | T1 | F0+F1-ideal+F2-exportable | histogram_gradient_boosting |
| 1 | T1 | F0+F1-ideal+F2+F3 | histogram_gradient_boosting |
| 0 | T2 | F0 | logistic_regression |
| 0 | T2 | F0+F1-ideal | histogram_gradient_boosting |
| 0 | T2 | F0+F1-ideal+F2 | histogram_gradient_boosting |
| 0 | T2 | F0+F1-ideal+F2-exportable | histogram_gradient_boosting |
| 0 | T2 | F0+F1-ideal+F2+F3 | histogram_gradient_boosting |
| 1 | T2 | F0 | logistic_regression |
| 1 | T2 | F0+F1-ideal | histogram_gradient_boosting |
| 1 | T2 | F0+F1-ideal+F2 | histogram_gradient_boosting |
| 1 | T2 | F0+F1-ideal+F2-exportable | histogram_gradient_boosting |
| 1 | T2 | F0+F1-ideal+F2+F3 | histogram_gradient_boosting |
| 0 | T4 | F0 | logistic_regression |
| 0 | T4 | F0+F1-ideal | histogram_gradient_boosting |
| 0 | T4 | F0+F1-ideal+F2 | histogram_gradient_boosting |
| 0 | T4 | F0+F1-ideal+F2-exportable | histogram_gradient_boosting |
| 0 | T4 | F0+F1-ideal+F2+F3 | histogram_gradient_boosting |
| 1 | T4 | F0 | logistic_regression |
| 1 | T4 | F0+F1-ideal | histogram_gradient_boosting |
| 1 | T4 | F0+F1-ideal+F2 | histogram_gradient_boosting |
| 1 | T4 | F0+F1-ideal+F2-exportable | histogram_gradient_boosting |
| 1 | T4 | F0+F1-ideal+F2+F3 | histogram_gradient_boosting |

## Qualified recommendation

- Prediction recommendation: `insufficient_data`.
- Modified-driver support: `fail`.
- Per-link outcomes remain separate in `go_no_go.json`.

Link 0 passes the ID ranking and fixed-threshold screens at T1. Link 1 fails
the frozen heuristic-gain and fixed-threshold screens at T1, producing a
per-link `no_go`. The driver result fails independently because F2-exportable
incremental ID gains are only 0.0055 (link 0) and 0.0029 (link 1), below 0.10.

## Key T1 results

| Link | Feature set | ID recall @ 10% | Fixed-threshold recall | ID action rate |
|---:|---|---:|---:|---:|
| 0 | F0 | 0.1223 | 0.0411 | 0.0167 |
| 0 | F0 + F1-ideal | 0.6185 | 0.4204 | 0.0450 |
| 0 | F0 + F1-ideal + F2 | 0.6240 | 0.4215 | 0.0451 |
| 0 | F0 + F1-ideal + F2 + F3 | 0.6218 | 0.4264 | 0.0448 |
| 1 | F0 | 0.1094 | 0.0270 | 0.0167 |
| 1 | F0 + F1-ideal | 0.4814 | 0.2901 | 0.0452 |
| 1 | F0 + F1-ideal + F2 | 0.4843 | 0.2972 | 0.0464 |
| 1 | F0 + F1-ideal + F2 + F3 | 0.4958 | 0.3266 | 0.0498 |

The dominant gain is F1 over F0. Full F2 and F2-exportable have identical
reported performance, and F3 adds little. Selection-safe importance identifies
`mac_queue_packets` as the leading F2 feature on both links.

F1 degradation reduces T1 ID recall by 0.1297/0.1286 on link 0 and
0.0864/0.0921 on link 1 for the 1 ms/5 ms profiles. Adding F2 recovers most of
that loss; residual losses are 0.0525/0.0493 and 0.0084/0.0097.

Cross-link T1 recall is asymmetric but close to native target-link performance:
0.4737 for link 0 -> link 1 and 0.6207 for link 1 -> link 0.

## Evidence limitations

- Required OOD scenario `obss_plus_legacy_mixed8` has 10 matched run groups; the frozen minimum is 20. Its formal evidence and all dependent decisions are `insufficient_data`, never pass or fail.

The ranking-budget cutoff uses the complete evaluated population and is an
upper bound on a fixed online threshold. The packet-count heuristic is an
additive baseline, not an airtime or completion-time estimator under A-MPDU.
No result authorizes an adaptive simulation action.
