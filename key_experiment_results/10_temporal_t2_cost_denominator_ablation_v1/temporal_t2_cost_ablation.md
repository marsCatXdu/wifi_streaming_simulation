# Temporal T2 cost-denominator ablation

This is retrospective engineering evidence on already-opened runs, not confirmation.
No model was refit and fresh seeds 1301+ were not read.

## Selected policies

| Evidence role | Ranker | Family | Gate | Requested action fraction |
| --- | --- | --- | --- | ---: |
| Frozen source | legacy_bad12_value_per_cost | primary_compact_physics_temporal | p_frames_only | 15.0% |
| Cost-free winner | legacy_bad12_value | primary_compact_physics_temporal | p_frames_only | 16.5% |

## Engineering-test estimates

| Metric | Frozen source | Cost-free winner | Paired delta (winner - source), 95% interval |
| --- | ---: | ---: | ---: |
| Deadline-miss probability | 0.522864% | 0.472305% | -0.050559 pp [-0.101566, -0.000643] pp |
| Completed-late18 ratio | 1.591785% | 1.488929% | -0.102856 pp [-0.186144, -0.031313] pp |
| DR airtime (us/eligible frame) | 365.494 | 395.962 | +30.467 [+20.651890, +39.515430] |
| Action fraction | 16.186841% | 17.680246% | +1.493404 pp [+1.108157, +1.819022] pp |

The calibration winner is chosen without consulting engineering-test outcomes.
The test estimates remain descriptive because that split was opened previously.
