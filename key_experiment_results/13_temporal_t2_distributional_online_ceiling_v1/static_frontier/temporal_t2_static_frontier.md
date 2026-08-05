# Temporal-T2 cross-fitted static frontier

This is a retrospective engineering screen on the action-clean randomized temporal population. The static allocator sees future predicted scores, so it is a predictor ceiling, not an online policy.

## Deadline-rescue frontier at 372 ms/run

| Predictor | Primary AUC | Actions | Captured primary misses | Perfect-rescue residual | DR miss risk | DR late18 ratio |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| primary_hgb64 | 0.9319 | 17,943 | 1,632 | 424 | 0.593% | 1.846% |
| primary_hgb128 | 0.9334 | 17,942 | 1,630 | 426 | 0.595% | 1.821% |
| primary_secondary_hgb64 | 0.9317 | 17,943 | 1,634 | 422 | 0.588% | 1.839% |
| primary_secondary_hgb128 | 0.9335 | 17,943 | 1,630 | 426 | 0.598% | 1.826% |

## Interpretation boundary

- Static policies inspect all predicted scores in a run and are not deployable online.
- The randomized temporal population excludes startup and action-dirty rows, so absolute risks cannot be compared directly with 86,400-frame closed-loop campaigns.
- Doubly robust frame replay does not reproduce policy-induced queue or interference feedback.
- Primary-copy capture is directly observed; counterfactual final misses and completed-frame P99 still require closed-loop measurement.
