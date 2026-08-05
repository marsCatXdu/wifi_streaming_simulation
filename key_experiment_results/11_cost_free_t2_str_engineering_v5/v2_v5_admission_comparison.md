# Score-aware T2 V2 versus Cost-free T2 V5 admission comparison

Diagnostic only; qualification gates remain in the frozen campaign reports.

Matched units: 48; frame rows: 86400.
Diagnostic value-per-cost scores changed on 0 rows; active policy scores changed on 42731 rows; score-threshold membership changed on 790 rows.

| Paired metric | Baseline | Candidate | Candidate - baseline (95% CI) |
| --- | ---: | ---: | ---: |
| All-generated deadline-miss rate | 0.5729% | 0.5741% | +0.0012% [-0.0255%, +0.0312%] |
| Mean per-run completed-frame P99 | 17.192 ms | 17.081 ms | -0.111 ms [-0.296, +0.057] ms |

| Metric | Baseline | Candidate | Candidate - baseline |
| --- | ---: | ---: | ---: |
| Actions | 4944 | 5147 | +203 |
| Acted primary misses | 771 | 763 | -8 |
| Rescued acted primary misses | 737 | 736 | -1 |
| Final misses | 495 | 496 | +1 |

Common actions: 4426; displaced baseline actions: 518; candidate-only actions: 721.
Displaced actions contain 57 baseline primary misses; candidate-only actions contain 49 candidate primary misses.

Final-miss transitions (baseline -> candidate):

- miss -> miss: 440
- miss -> on_time: 55
- on_time -> miss: 56
- on_time -> on_time: 85849
