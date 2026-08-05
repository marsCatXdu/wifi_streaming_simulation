# Temporal-T2 ceiling decomposition v1

This directory archives the first exact decomposition of the closed-loop V2
and V5 evidence. It asks whether selective full-copy duplication is limited by
the action itself, the current scalar score, or chronological allocation. The
analysis uses only the already-opened engineering seeds `1251` through `1298`;
reserved confirmation seeds `1301` through `1348` were not read.

The resource frontiers assign a separate canonical reservation budget to each
simulation run. They never transfer unused credit between independent seeds.
They are static reservation-cost proxies, not replays of hypothetical measured
airtime settlements or changed contention.

## Result

The current action space has substantial headroom, but the current scalar
scores do not reach it inside the resource proxy:

| Frontier | Captured primary misses | Perfect-rescue final misses | Final misses projected at V5's 96.46% factual rescue rate |
| --- | ---: | ---: | ---: |
| V2 score, 360 ms/run refill budget | 850 | 382 | 412.08 |
| V2 score, 372 ms/run finite budget | 858 | 374 | 404.36 |
| V5 score, 360 ms/run refill budget | 858 | 374 | 404.36 |
| V5 score, 372 ms/run finite budget | 867 | 365 | 395.68 |
| V5 all-threshold pooled sensitivity | 956 | 276 | 309.83 |
| Perfect primary information, 360 ms/run | 1,103 | 129 | 168.03 |

The target of a miss rate below 0.40% and more than 50% fewer misses than
STR permits at most 345 misses. At V5's factual rescue efficiency, it requires
capturing at least 920 primary misses.

Every evaluated P-frame action has the same exact canonical reservation,
`1983.760667318285 us`. The 360 ms refill-only budget therefore permits 181
actions per run; startup credit raises the finite-run proxy to 372 ms and 187
actions. V5's scalar score first captures 920 primary misses at a uniform cap
of 236 actions per run, requiring up to 468.168 ms of canonical reservation.
The present score cannot meet the target inside either budget proxy even if
every selected primary miss is rescued.

In contrast, a perfect primary-miss oracle can select all 1,103 eligible
primary misses. This costs only 45.585 ms/run on average and 130.928 ms in the
worst run. The perfect-rescue residual is the 129 misses outside the current
candidate population; applying V5's factual rescue rate gives 168.03 expected
misses. This is strong evidence that full-copy duplication has not reached its
action or reservation ceiling.

## Why the 310-miss sensitivity is not implementable

V5's 8,218 threshold passers average 339.636 ms of canonical reservation per
run, below the 360 ms refill budget when all runs are pooled. However, 26 of
48 individual runs exceed both the 360 ms refill-only budget and the 372 ms
finite-run proxy. Quiet-run credit cannot be transferred to congested runs.
The 309.83-miss result is therefore a useful aggregate sensitivity, not a
feasible per-run policy frontier.

## Identification boundary

This analysis exactly identifies primary-miss capture because every run
observes its primary copy. It does not identify a perfect secondary-outcome or
P99 oracle:

- only 871 of 1,103 eligible primary misses have an action outcome in at least
  one of V2, V4, or V5;
- 232 candidate actions have no observed secondary-copy outcome; and
- 18 observed frames change rescue outcome across policies, demonstrating
  closed-loop interference.

The projected columns apply an explicitly stated constant rescue rate. They
are not performance estimates. Factual V2 and V5 P99 remains recorded beside
their miss rates, but counterfactual P99 requires completion distributions.

## Scientific decision

Freeze V2 as the engineering champion and stop score/threshold-only variants.
The next stage should fit cross-fitted no-duplication and duplication
completion distributions from randomized T2 data, keeping deadline rescue,
18 ms tail acceleration, and conservative cost separate. It should then
compare a clairvoyant allocation bound with a nonclairvoyant time-, credit-,
and congestion-aware allocator. Change the action space only if those causal
and online frontiers fail.

## Artifacts

- `closed_loop_ceiling.json`: machine-readable factual, score, primary-oracle,
  resource, support, provenance, and identification results.
- `closed_loop_ceiling.md`: generated compact report.
- `closed_loop_ceiling.png`: factual miss sensitivities and the scalar-score
  information gap against the canonical budget band.
- `artifact_manifest.json`: checksum closure for the generated artifacts and
  analysis tool.

## Reproduction

Restore the checksum-bound V2, V4, and V5 raw campaign archives, then run:

```bash
PYTHONDONTWRITEBYTECODE=1 \
MPLCONFIGDIR=/tmp/wifi-streaming-matplotlib \
  .venv/bin/python tools/analyze_paired_value_t2_ceiling.py \
  --campaign 'Score-aware T2 V2=results/paired_value_t2_score_aware_str_engineering_v2/runs/aggregate.json' \
  --campaign 'Cost-free T2 V5=results/paired_value_t2_cost_free_str_engineering_v5/runs/aggregate.json' \
  --support-campaign 'Remaining-refill T2 V4=results/paired_value_t2_remaining_refill_str_engineering_v4/runs/aggregate.json' \
  --reference-label 'Cost-free T2 V5' \
  --str-misses 691 \
  --json-output results/paired_value_t2_ceiling_decomposition_v1/closed_loop_ceiling.json \
  --markdown-output results/paired_value_t2_ceiling_decomposition_v1/closed_loop_ceiling.md \
  --plot-output results/paired_value_t2_ceiling_decomposition_v1/closed_loop_ceiling.png
```
