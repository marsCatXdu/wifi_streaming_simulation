# Adaptive airtime duplication under OBSS

This directory is the compact evidence snapshot for the OBSS-only
`adaptive_airtime_duplication` closed-loop matrix. Raw per-run directories remain
under `results/adaptive_airtime_obss_v1/runs/`.

## Experiment

Thirty paired seeds compare five approaches under the same OBSS topology,
traffic, and propagation used by the genuine-polling closed-loop OBSS matrix:

1. Single 5 GHz (`fixed_link_1`)
2. Selective duplication at threshold `0.20` with a 30% frame-token budget
3. Adaptive airtime duplication (`rho=0.02`, initial shadow price `0.20`)
4. Full application duplication (primary path 1)
5. MLO STR with `NMaxInflights=1`

The adaptive controller budgets secondary-sender PHY TX airtime on path 0
rather than frame tokens, and adapts a shadow price online. The receiver hold
for delayed secondary copies (`0fcee68`) is required so late-stage actions are
not discarded after primary-only finalize.

Matrix run IDs preserve the original launch identity commit `a829356`; the
executed binary includes the adaptive policy and delayed-secondary hold fix.

## Headline results

All values are means across the 30 paired seeds. The deadline is 33.333 ms.

| Approach | Miss ratio | P99 latency | Redundant bytes | Tagged secondary airtime |
|---|---:|---:|---:|---:|
| Single 5 GHz | 1.307% | 19.51 ms | 0% | 0% |
| Selective duplication (0.20) | 0.976% | 18.82 ms | 1.263% | 0.105% |
| Adaptive airtime duplication | 0.611% | 16.33 ms | 20.836% | 1.944% |
| Full duplication | 0.043% | 9.92 ms | 50% | 7.718% |
| MLO `NMaxInflights=1` | 0.715% | 19.02 ms | 0% | 0% |

Selective duplication launches 23.4 of 1,800 frames per run on average
(1.30% action rate) with no budget suppressions. Adaptive airtime
duplication launches 498.4 frames per run on average while staying near the
configured 2% long-run secondary PHY TX budget (measured tagged airtime
fraction 1.944%).

Lower miss ratio for the adaptive arm is therefore confounded by much higher
measured secondary airtime than selective duplication. Treat the first matrix
as a budget-utilization study; a later cost-matched or budget-sweep comparison
is required before attributing gains to the decision rule alone.

## Directory guide

- `aggregate.csv` / `aggregate.json`: run-level and grouped summaries
- `adaptive_airtime_summary.csv`: adaptive-specific control and airtime metrics
- `selective_duplication_summary.csv`: selective control rates for the paired arm
- `experiment_manifest.json`: full matrix provenance (150 runs)
- `DESCRIPTION.rst`: generated matrix description
- `figures/`: general latency, miss, redundancy, and background plots
- `figures/adaptive_airtime/`: adaptive-specific miss, airtime, price, and stage plots
- `figures/selective_control/`: selective action-rate and stage plots
