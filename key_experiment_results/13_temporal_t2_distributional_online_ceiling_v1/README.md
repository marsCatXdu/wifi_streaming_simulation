# Temporal-T2 distributional and online ceiling v1

This directory archives the completed second stage of the selective full-copy
duplication ceiling decomposition.  It uses the 96 already-opened randomized
run groups at seeds `1101` through `1196`; reserved confirmation seeds `1301`
through `1348` were not read.

The analysis fits separate no-duplication and duplication completion-time
distributions at 12, 18, 24, 30, and 33.333 ms.  Every reported prediction is
eight-fold group-out-of-fold.  It then compares an exact per-run static
predicted-score frontier with a nonclairvoyant shadow-price replay using exact
canonical reservations and causal credit.

## Main result

The selected `primary_secondary_hgb64` predictor establishes a strong static
causal-score ceiling, but the frozen online allocator does not realize it:

| P-frame deadline-rescue policy | Actions | Primary misses captured | Capture fraction | DR miss risk | DR completed-late18 ratio | Mean reservation/run |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Treat none | 0 | 0 | 0% | 2.805% | 4.336% | 0 ms |
| Static future-score frontier, 372 ms | 17,943 | 1,634 | 79.47% | 0.588% | 1.839% | 370.78 ms |
| Online global shadow price | 15,392 | 989 | 48.10% | 1.479% | 2.447% | 318.06 ms |
| Online congestion-tertile shadow price | 15,520 | 1,056 | 51.36% | 1.389% | 2.395% | 320.71 ms |

The congestion state is useful but insufficient: it recovers 67 additional
primary misses and lowers the doubly robust miss estimate by about 0.090
percentage points versus the global online policy.  The remaining gap from
the same predictor's static frontier is 578 captured primary misses.

The online policy does not exhaust its finite-run resource proxy.  It leaves
about 51 ms/run unused while rejecting 5,442 P-frame candidates because their
canonical reservation is larger than current spendable credit.  This points
to chronological liquidity as the next admission problem.  It does not
justify naive borrowing: the earlier V4 closed-loop experiment already showed
that spending future refill without valuing displaced future actions is
harmful.  The next isolated mechanism must combine repayment-bounded future
credit with the present shadow price.

## Predictor interpretation

The four cross-fit variants differ only in feature family and histogram
gradient-boosting capacity.  `HGB64` and `HGB128` mean 64 and 128 boosting
iterations, not CPU count or bit width.  All four no-action deadline-risk AUCs
are close, from 0.9317 to 0.9335.  The frozen selection priorities choose
`primary_secondary_hgb64` because its exact P-frame frontier captures 1,634
primary misses, the best of the four, while keeping the smaller model.

The all-frame cost-aware static ablation captures 1,664 primary misses and
has a 0.548% doubly robust miss estimate at the same 372 ms/run budget.  Its
444 selected I-frame actions capture 52 I-frame primary misses but displace
1,730 P-frame actions and only 22 P-frame captures, for a net gain of 30
captures.  This supports an I-frame-specific action rule; it is not evidence
for duplicating every large I-frame.

## Evidence boundary

- The static allocator sees all future predicted scores in each run.  It is a
  causal-feature predictor ceiling, not a deployable policy.
- The online allocator sees only current causal features and learned reference
  tables, but it is still a frame replay.  It does not reproduce contention,
  queue feedback, sender airtime, background throughput, or completed-frame
  P99 under the changed action sequence.
- The action-clean lag-8 population excludes startup and action-dirty rows, so
  its absolute risks are not directly comparable with the 86,400-frame
  closed-loop campaigns.
- The selected predictor and allocator were engineered on opened data.  None
  of these estimates is independent confirmation evidence.
- V2 remains the closed-loop engineering champion until a changed runtime
  policy passes the frozen paired STR gates.

## Archived artifacts

- `crossfit/`: compact metrics and checksum manifest.  The omitted 25 MB
  prediction stream has SHA-256
  `32d9cfda32d2dd7d380aaaefc659c3c2ca5d6f38593426135b0bc2b338ffab3b`.
- `static_frontier/`: machine-readable frontier, generated report, reviewed
  figure, and checksum manifest.
- `shadow_reference/`: selected-model refit/reproduction metrics and manifest.
  The omitted 45 MB reference stream has SHA-256
  `82c48787d877e1d9a492e3234798917b614a0171f98dfe5a7eeabad0cb990cd8`.
- `online_allocator/`: machine-readable replay, generated report, reviewed
  figure, and checksum manifest.

Each nested manifest binds its generated files and upstream sources.  The
cross-fit was produced from clean commit `1e47792`; the static, reference, and
online stages record their own clean provenance through `dd9be5f`.

## Reproduction

With the checksum-bound ignored temporal dataset and both omitted prediction
streams restored at the paths recorded in the machine artifacts, run:

```bash
PYTHONDONTWRITEBYTECODE=1 \
MPLCONFIGDIR=/tmp/wifi-streaming-matplotlib \
  .venv/bin/python tools/analyze_temporal_t2_online_allocator.py \
  --distribution-dir results/randomized_full_copy_exploration_collection_v1/temporal_t2_distributional_frontier_v1 \
  --temporal-dir results/randomized_full_copy_exploration_collection_v1/temporal_dataset \
  --static-dir results/randomized_full_copy_exploration_collection_v1/temporal_t2_static_distributional_frontier_v1 \
  --reference-dir results/randomized_full_copy_exploration_collection_v1/temporal_t2_shadow_reference_v1 \
  --output-dir results/randomized_full_copy_exploration_collection_v1/temporal_t2_online_shadow_price_v1
```
