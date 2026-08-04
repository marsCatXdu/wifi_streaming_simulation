# Paired-value T2 qualification against STR MLO

Evidence role: engineering qualification only; this is not final confirmation.
The reserved final-confirmation seeds were not used.

Validated matched seed/run units: 48.
Every headline value below was reconstructed from raw per-run artifacts.

| Metric | Paired-value T2 | STR MLO | Policy-minus-STR 95% interval |
| --- | ---: | ---: | ---: |
| All-generated deadline-miss rate | 0.7882% | 0.7049% | [-0.0567%, 0.2269%] |
| Mean per-run completed-frame HF7 P99 | 17415.955 us | 18113.048 us | [-1815.990, 310.083] us |

Late completions remain in the completed-frame P99 population; misses and incomplete frames remain in the all-generated denominator.

Sender-airtime ratio: 1.132353 (95% paired-bootstrap interval [1.096317, 1.166041]; strict target < 1.20).
Background-throughput loss: 0.0014% (95% paired-bootstrap interval [-0.0017%, 0.0048%]; target <= 1.00%).

Performance victory: **fail**.  
Resource target: **pass**.  
Overall: **fail**.
