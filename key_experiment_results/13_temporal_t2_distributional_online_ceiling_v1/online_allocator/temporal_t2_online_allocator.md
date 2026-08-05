# Temporal-T2 online shadow-price allocator

This retrospective screen admits each row using only current causal state and shadow-price tables learned without its run-group fold. Only the frozen static-frontier winner is evaluated.

Selected predictor: `primary_secondary_hgb64`. The outer model scores its own 84 training groups for the shadow reference; held-out rows and outcomes remain absent.

## P-frame deadline-rescue policy

| Predictor | Regime | Actions | Captured primary misses | Static capture gap | DR miss risk | DR late18 ratio |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| primary_secondary_hgb64 | global | 15,392 | 989 | -645 | 1.479% | 2.447% |
| primary_secondary_hgb64 | congestion_tertile | 15,520 | 1,056 | -578 | 1.389% | 2.395% |

## Interpretation boundary

- The shadow price is a nonparametric fractional-knapsack approximation to the finite-horizon value difference, not a perfect dynamic program.
- Cross-fitted frame replay does not reproduce policy-induced contention or queue feedback.
- The action-clean lag-8 population excludes startup and other generated frames, so a closed-loop campaign remains necessary.
- The full-horizon carry setting isolates allocation quality; it is not a claim that unused credit alone helped V3.
- No reserved confirmation seed may be read during this screen.
- The selected predictor was chosen using all 96 opened engineering groups; this is not independent confirmation.
- Each outer model's empirical shadow reference uses in-sample scores on its 84 training groups. This excludes held-out outcomes but may retain training-score optimism.
