# Distributional shadow T2 versus V2

The same 48 opened seeds are compared. The candidate's 48 runs pass the current strict validator; V2 is read from its exact checksum-bound archive and its embedded 96-run schema-compatible strict report. Primary-copy deadline outcomes match on all 86,400 frames.

| Metric | V2 | Distributional shadow T2 | Candidate-minus-V2 95% interval |
| --- | ---: | ---: | ---: |
| Final misses | 495 (0.5729%) | 455 (0.5266%) | [-0.1181, 0.0255] pp |
| Actions | 4944 | 8336 | - |
| Captured primary misses | 771 | 809 | - |
| Mean per-run completed P99 | 17.192 ms | 16.832 ms | [-0.687, -0.041] ms |

V2-only actions contain 108/1142 primary misses (9.46%).
Candidate-only actions contain 146/4534 primary misses (3.22%).

The candidate spends substantially more actions on a lower-risk marginal population. Its 40-miss improvement over V2 comes from only 38 additional captured primary misses, while the many added on-time-frame actions primarily improve latency. Selection efficiency, not full-copy rescue, is the limiting mechanism.

The direct miss-rate interval includes zero, while the completed-P99 interval is strictly negative. V2 therefore remains the engineering champion; this candidate is retained as evidence about prediction and allocation, not promoted.
