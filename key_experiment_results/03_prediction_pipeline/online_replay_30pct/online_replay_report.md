# Causal 5 GHz latency-risk replay

Individual runs replayed: 54

Frames were processed in generation order. Each model used only telemetry
available at its decision stage, a fixed calibrated probability threshold,
and causal frame or byte credits. Recorded outcomes were never changed.

## Representative operating point

| Split | Scenario | Budget type | Recall | Precision | Action rate | Byte overhead |
|---|---|---|---:|---:|---:|---:|
| in distribution test | all selected | frames | 0.3632 | 0.2645 | 0.1726 | 0.1776 |
| out of distribution test | all selected | frames | 0.3528 | 0.4316 | 0.0683 | 0.0736 |
| out of distribution test | obss only | frames | 0.1256 | 0.2305 | 0.0080 | 0.0098 |
| out of distribution test | obss plus legacy mixed8 | frames | 0.3851 | 0.4498 | 0.2129 | 0.2269 |

This table uses the predeclared probability threshold 0.2 and budget 30%.
The heatmaps report every threshold and budget combination; no test-set
operating point is promoted as a newly tuned deployment threshold.

## Offline ranking upper bound

| Split | Stage | Global Top-30% recall |
|---|---|---:|
| in distribution test | T0 | 0.7293 |
| in distribution test | T1 | 0.7287 |
| out of distribution test | T0 | 0.8846 |
| out of distribution test | T1 | 0.8885 |

## Interpretation boundary

Recall is the fraction of recorded deadline misses that received an early
warning and budget permission. It is not the fraction of misses that would
be prevented. Measuring prevention requires a later closed-loop simulation.

Global Top-K values from the offline evaluation remain an optimistic upper
bound because they rank a complete future population. This replay does not.
