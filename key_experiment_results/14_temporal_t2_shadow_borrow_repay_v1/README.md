# Temporal-T2 shadow-priced borrow/repay v1

This directory archives the frozen opened-data test of one allocation change:
after a frame clears the fold-honest future-opportunity price, its canonical
reservation may debit deterministic future refill and must be fully repaid by
the 60-second measurement stop.  Predictor, reward, P-frame gate, time bins,
congestion state, shadow curves, conservative reservation, and total 372
ms/run resource remain unchanged.

The screen uses the same 96 already-opened randomized groups at seeds `1101`
through `1196` as the distributional ceiling.  Reserved confirmation seeds
`1301` through `1348` were not read.

## Result

The predeclared congestion-tertile borrow/repay policy passes all five frozen
screen checks:

| Policy | Actions | Captured primary misses | Capture | DR miss risk | DR completed-late18 | Mean reservation/run | Maximum debt |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Static predictor frontier, 372 ms | 17,943 | 1,634 | 79.47% | 0.588% | 1.839% | 370.78 ms | n/a |
| Strict online, global | 15,392 | 989 | 48.10% | 1.479% | 2.447% | 318.06 ms | 0 ms |
| Borrow/repay, global | 15,586 | 1,505 | 73.20% | 0.754% | 1.983% | 322.07 ms | 132.06 ms |
| Strict online, congestion | 15,520 | 1,056 | 51.36% | 1.389% | 2.395% | 320.71 ms | 0 ms |
| Borrow/repay, congestion | 15,719 | 1,517 | 73.78% | 0.748% | 2.002% | 324.82 ms | 144.82 ms |

Relative to the exact strict congestion baseline, borrow/repay adds only 199
net actions but captures 461 more primary misses.  The shared whole-run
bootstrap estimate for its miss-risk change is -0.6409 percentage points with
a 95% interval of [-0.7945, -0.4946] points.  Its completed-late18 change is
-0.3933 points with interval [-0.5339, -0.2734].

Every run stays below 370.964 ms of canonical reservation and finishes with a
positive balance; the minimum final balance is 1.037 ms.  The primary policy
therefore passes the frozen 70% capture, 0.8% miss-risk, no-late18-regression,
372 ms resource, and full-repayment gates.

## Why this differs from failed V4 borrowing

The net action-count change hides a large and favorable substitution:

| Congestion policy transition | Frames | Primary misses | Primary-miss risk | Median frame ID | Median predicted reward |
| --- | ---: | ---: | ---: | ---: | ---: |
| Common strict and borrow actions | 12,071 | 889 | 7.36% | 936 | 0.03348 |
| Borrow-only actions | 3,648 | 628 | 17.21% | 551 | 0.11080 |
| Strict-only displaced actions | 3,449 | 167 | 4.84% | 1,268 | 0.02749 |

The new policy spends future credit on earlier, much higher-risk actions and
then raises its opportunity threshold as debt consumes future capacity.  It
displaces later actions with roughly one quarter of the primary-miss risk.
V4 did the reverse: unpriced early actions displaced later, higher-score
actions and increased closed-loop misses.  Opportunity pricing is therefore
the mechanism that makes future credit useful in this replay.

## Important risk before promotion

This is not yet a closed-loop performance result.  Fifty-five of 96 runs enter
debt, with a worst transient debt of 144.82 ms and as many as 180 borrowed
admissions in one run.  Those temporal bursts can change contention, queueing,
sender airtime, secondary-copy outcomes, and background throughput.  The
doubly robust frame replay cannot model those feedbacks or mean per-run
completed-frame P99.

The frozen screen therefore authorizes compiled integration and an
already-opened-seed closed-loop campaign; it does not replace score-aware V2
as the engineering champion.  Promotion still requires the standard paired
STR gates: lower all-generated miss rate and completed-frame P99, sender
airtime ratio with upper interval below 1.20, and background-throughput loss
no greater than 1%.

## Artifacts

- `temporal_t2_shadow_borrow.json`: exact decisions, observed primary misses
  by route and time bin, action transitions, doubly robust outcomes and shared
  bootstrap intervals, per-run debt/resource summaries, frozen screen, and
  provenance.
- `temporal_t2_shadow_borrow.md`: generated compact report.
- `temporal_t2_shadow_borrow.png`: reviewed static/strict/borrow comparison,
  decision routing, outcome plane, and resource/debt figure.
- `artifact_manifest.json`: SHA-256 closure over the generated artifacts,
  source predictions/results, and frozen design contract.

The canonical artifact records clean commit `4a5bf74` and tool SHA-256
`811f0b3049ed3de98ba063dc4225dad7a1f45764910c79df52c67d584b0f70f3`.
Its machine-result SHA-256 is
`134e2632a02fc3979284700e3bd9531353c18d0fa6e4d27f6e1adea4b2d6f4bb`.

## Reproduction

Restore the checksum-bound ignored sources named in `artifact_manifest.json`,
then run:

```bash
PYTHONDONTWRITEBYTECODE=1 \
MPLCONFIGDIR=/tmp/wifi-streaming-matplotlib \
  .venv/bin/python tools/analyze_temporal_t2_shadow_borrow.py \
  --distribution-dir results/randomized_full_copy_exploration_collection_v1/temporal_t2_distributional_frontier_v1 \
  --temporal-dir results/randomized_full_copy_exploration_collection_v1/temporal_dataset \
  --static-dir results/randomized_full_copy_exploration_collection_v1/temporal_t2_static_distributional_frontier_v1 \
  --reference-dir results/randomized_full_copy_exploration_collection_v1/temporal_t2_shadow_reference_v1 \
  --online-v1-dir results/randomized_full_copy_exploration_collection_v1/temporal_t2_online_shadow_price_v1 \
  --output-dir results/randomized_full_copy_exploration_collection_v1/temporal_t2_shadow_borrow_repay_v1
```
