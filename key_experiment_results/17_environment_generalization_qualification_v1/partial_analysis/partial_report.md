# Exploratory partial held-out environment result

**EXPLORATORY PARTIAL RESULT: the preregistered 576-run campaign did not close. This balanced complete-scenario panel cannot establish formal qualification or promotion.**

The stopped campaign contains 568 valid runs. This analysis uses 360 runs in 120 fully paired units.

## Aggregate balanced-panel results

| Arm | Miss rate | Completed P99 | Sender airtime | Background throughput |
| --- | ---: | ---: | ---: | ---: |
| STR MLO | 9.1649% | 23.457 ms | 5048.560 ms | 30.706 Mbit/s |
| Score-aware T2 V2 | 12.6157% | 23.542 ms | 6017.942 ms | 30.592 Mbit/s |
| Distributional-shadow T2 | 12.1560% | 23.275 ms | 6168.023 ms | 30.593 Mbit/s |

## Exploratory comparisons with STR MLO

### Score-aware T2 V2

- Miss delta: 3.4508 percentage points (95% interval [1.2244, 5.4511]).
- Relative miss reduction: -37.65%.
- Completed-P99 delta: 0.085 ms (95% interval [-0.728, 0.848]).
- Sender-airtime ratio: 1.1920 (95% interval [1.1504, 1.2335]).
- Background-throughput loss: 0.371% (95% interval [-0.206%, 1.131%]).

### Distributional-shadow T2

- Miss delta: 2.9911 percentage points (95% interval [0.8253, 4.9376]).
- Relative miss reduction: -32.64%.
- Completed-P99 delta: -0.182 ms (95% interval [-1.054, 0.645]).
- Sender-airtime ratio: 1.2217 (95% interval [1.1838, 1.2597]).
- Background-throughput loss: 0.368% (95% interval [-0.221%, 1.151%]).

## Missingness and interpretation

- Incomplete scenarios: 4.
- Missing planned runs: 8.
- Every selected raw run passed the unchanged strict validator.
- Every selected scenario contains all four replicates and all three arms.
- Completed-frame P99 is survivor-conditioned and must be read with miss rate.
- No formal qualification or promotion decision may use this partial report.
