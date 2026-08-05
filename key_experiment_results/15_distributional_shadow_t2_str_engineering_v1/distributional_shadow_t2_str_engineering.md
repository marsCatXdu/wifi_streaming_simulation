# Distributional shadow T2 engineering result

This is opened-seed engineering evidence, not final confirmation. Seeds 1301 through 1348 remain unopened.

| Metric | Distributional shadow T2 | STR MLO | Paired 95% interval |
| --- | ---: | ---: | ---: |
| All-generated deadline-miss rate | 0.5266% (455/86400) | 0.7998% (691/86400) | [-0.3935, -0.1597] pp |
| Mean per-run completed-frame HF7 P99 | 16.832 ms | 18.875 ms | [-3.057, -1.109] ms |

Sender-airtime ratio: 1.166183, 95% interval [1.140479, 1.190621].
Background-throughput loss: 0.0050%, 95% interval [0.0026%, 0.0074%].
Paired directions (win/tie/loss): misses 32/8/8; completed P99 35/0/13. 26 of 48 pairs improve both.
21 of 48 individual runs exceed a 1.20 sender-airtime ratio even though the frozen campaign-level upper confidence gate passes.

STR qualification: **pass**.
Promotion readiness: **fail**.

## Mechanism

The policy launched 8336 copies. It captured 809 of 1232 primary misses (65.67%) and rescued 777 of those captured misses (96.04%).
Final union misses are 455; the relative reduction versus STR is 34.15%.

The policy beats STR on both primary performance metrics and satisfies the resource bounds, but it is not ready for confirmation: it misses the 0.50% engineering miss target and the longer-term 50% relative-reduction target. The completed-P99 target is met.
