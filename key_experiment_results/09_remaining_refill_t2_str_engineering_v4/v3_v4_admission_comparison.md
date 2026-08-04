# Full-horizon T2 V3 versus Remaining-refill T2 V4 admission comparison

Diagnostic only; qualification gates remain in the frozen campaign reports.

Matched units: 48; frame rows: 86400.
Scores changed on 0 rows and score-threshold membership changed on 0 rows.

| Metric | Baseline | Candidate | Candidate - baseline |
| --- | ---: | ---: | ---: |
| Actions | 4944 | 5272 | +328 |
| Acted primary misses | 771 | 675 | -96 |
| Rescued acted primary misses | 737 | 646 | -91 |
| Final misses | 495 | 585 | +90 |

Common actions: 3910; displaced baseline actions: 1034; candidate-only actions: 1362.
Displaced actions contain 172 baseline primary misses; candidate-only actions contain 76 candidate primary misses.

Final-miss transitions (baseline -> candidate):

- miss -> miss: 413
- miss -> on_time: 82
- on_time -> miss: 172
- on_time -> on_time: 85733
