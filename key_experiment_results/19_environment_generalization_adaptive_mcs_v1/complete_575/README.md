# Fixed versus adaptive target-MCS qualification

All 575 promoted adaptive runs passed fresh strict validation.  The comparison uses 191 complete matched three-arm units per MCS mode. Deadline misses and deadline-censored latency use every generated frame; completion CDF/PDF and run-level P99 remain survivor-conditioned.

## Aggregate results

| MCS | Arm | Miss rate | Censored mean | Sender airtime | Background throughput | P99 support |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Fixed | STR MLO | 16.0437% | 9.149 ms | 5164.322 ms | 32.319 Mbit/s | 184/191 |
| Fixed | V2 | 19.1306% | 10.469 ms | 5790.966 ms | 32.260 Mbit/s | 181/191 |
| Fixed | Distributional | 18.7584% | 10.371 ms | 5946.342 ms | 32.270 Mbit/s | 183/191 |
| Adaptive | STR MLO | 17.7337% | 9.243 ms | 5588.842 ms | 32.252 Mbit/s | 181/191 |
| Adaptive | V2 | 23.1784% | 11.068 ms | 6111.111 ms | 32.296 Mbit/s | 174/191 |
| Adaptive | Distributional | 22.4778% | 10.896 ms | 6340.811 ms | 32.289 Mbit/s | 176/191 |

## Adaptive minus fixed MCS

### STR MLO

- Miss-rate delta: 1.6901 percentage points (95% interval [0.4716, 3.0163]).
- Sender-airtime ratio: 1.0822 (95% interval [0.9646, 1.2042]).
- Deadline-censored mean delta: 0.094 ms.

### V2

- Miss-rate delta: 4.0478 percentage points (95% interval [1.9235, 6.5725]).
- Sender-airtime ratio: 1.0553 (95% interval [0.9510, 1.1623]).
- Deadline-censored mean delta: 0.599 ms.

### Distributional

- Miss-rate delta: 3.7194 percentage points (95% interval [1.6803, 6.1924]).
- Sender-airtime ratio: 1.0663 (95% interval [0.9582, 1.1768]).
- Deadline-censored mean delta: 0.525 ms.

## Adaptive-MCS policies versus adaptive-MCS STR

- V2: miss delta 5.4447 percentage points (95% interval [3.5573, 7.6245]); sender-airtime ratio 1.0934.
- Distributional: miss delta 4.7440 percentage points (95% interval [3.0390, 6.7117]); sender-airtime ratio 1.1345.

## Interpretation

This is a controlled MCS ablation. The selective predictors, admission rules, and conservative EhtMcs5-derived reservations were intentionally not retrained or retuned. Differences therefore include closed-loop interaction between rate adaptation and the frozen policies.
The aggregate estimates and intervals weight families, scenarios, and replicates equally. CDF/PDF figures pool frames and are descriptive.
