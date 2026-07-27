# Causal 5 GHz latency-risk replay

Individual runs replayed: 54

Frames were processed in generation order. Each model used only telemetry
available at its decision stage, a fixed calibrated probability threshold,
and causal frame or byte credits. Recorded outcomes were never changed.

## Representative operating point

| Split | Budget type | Recall | Precision | Action rate | Byte overhead |
|---|---|---:|---:|---:|---:|
| in distribution test | bytes | 0.1270 | 0.2313 | 0.0691 | 0.0662 |
| in distribution test | frames | 0.1246 | 0.2393 | 0.0655 | 0.0712 |
| out of distribution test | bytes | 0.1369 | 0.3698 | 0.0309 | 0.0313 |
| out of distribution test | frames | 0.1336 | 0.3799 | 0.0294 | 0.0320 |

## Offline ranking upper bound

| Split | Stage | Global Top-10% recall |
|---|---|---:|
| in distribution test | T0 | 0.3962 |
| in distribution test | T1 | 0.3951 |
| out of distribution test | T0 | 0.6216 |
| out of distribution test | T1 | 0.6097 |

## Interpretation boundary

Recall is the fraction of recorded deadline misses that received an early
warning and budget permission. It is not the fraction of misses that would
be prevented. Measuring prevention requires a later closed-loop simulation.

Global Top-K values from the offline evaluation remain an optimistic upper
bound because they rank a complete future population. This replay does not.
