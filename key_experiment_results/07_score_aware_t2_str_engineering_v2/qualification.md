# Score-aware T2 V2 engineering against STR MLO

Evidence role: engineering qualification only; this is not final confirmation.
The reserved final-confirmation seeds were not used.

Validated matched seed/run units: 48.
Every headline value below was reconstructed from raw per-run artifacts.

| Metric | Score-aware T2 V2 | STR MLO | Policy-minus-STR 95% interval |
| --- | ---: | ---: | ---: |
| All-generated deadline-miss rate | 0.5729% | 0.7998% | [-0.3194%, -0.1377%] |
| Mean per-run completed-frame HF7 P99 | 17192.270 us | 18874.604 us | [-2642.556, -795.291] us |

Late completions remain in the completed-frame P99 population; misses and incomplete frames remain in the all-generated denominator.

Sender-airtime ratio: 1.121707 (95% paired-bootstrap interval [1.084731, 1.156450]; strict target < 1.20).
Background-throughput loss: 0.0054% (95% paired-bootstrap interval [0.0027%, 0.0081%]; target <= 1.00%).

Performance victory: **pass**.  
Resource target: **pass**.  
Overall: **pass**.
