# Environment-generalization closed-loop qualification v1

This directory archives the complete 576-run held-out campaign, its strict
post-outcome reliability analysis, ten reviewed figures, the evidence-first
568-run snapshot, and the exact recovery record.

The principal result is negative: distributional-shadow improves V2, but both
selective full-copy policies have decisively more all-generated-frame deadline
misses than STR MLO across the frozen held-out population.  The preregistered
completed-frame P99 estimand is not assessable because 28 valid runs have
fewer than 100 completions.  No policy qualifies or is promoted from this
campaign.

## Source closure

- Simulation project commit:
  `47e19962420bb7623784bc91b0c0d40fbf462b35`.
- ns-3 upstream commit:
  `d2add90b452d600cfb4859baed8e9ea633519447`.
- Corrected strict validator commit:
  `5ca913ab40f8d6fa06188d80a86b7489f221eb05`.
- Complete-reliability analyzer commit:
  `565d9a284399bfc6bd26dc6507bddf1e1e12e3e9`.
- Final visually reviewed plotter commit:
  `694ce9aece9da23933247386097c5ae43406e239`.
- Final campaign-manifest SHA-256:
  `4c847ccc7d2e7d0ac9c154b0c4efc778ddcb403d0d12dabfd4a971c976269ef4`.
- Final manifest-selected raw tree: 9,025 files, SHA-256
  `2a1c5d5767647fb37aaabca507ea46bf3cb8307f71c8b5c7dc22bf538bc913f2`.
  The local and VM trees independently produced the same identity.
- Analysis-artifact-manifest SHA-256:
  `dba7188133c161a8b822580b7f806801e02c9078f3867815d742347ea111a0c3`.
- Plot-artifact-manifest SHA-256:
  `4ed770129317379b02326dde63ceb7ea14e74f6eeba36b4333e3d5f6d5a6006d`.

All 576 canonical directories passed the corrected strict validator.  The
campaign contains 48 scenarios in six families, four paired replicates per
scenario, and three arms, with 192 runs per arm.  Supplementary seed `21193`
is excluded from all numbers below.

## Complete-campaign result

| Arm | Misses / generated | Miss rate | Mean sender airtime | P99-supported runs |
| --- | ---: | ---: | ---: | ---: |
| STR MLO | 57,181 / 367,200 | 16.0443% | 5157.587 ms/run | 184 / 192 |
| Score-aware T2 V2 | 68,133 / 367,200 | 19.1308% | 5792.251 ms/run | 181 / 192 |
| Distributional-shadow T2 | 66,819 / 367,200 | 18.7582% | 5946.699 ms/run | 183 / 192 |

The frozen equal-family, equal-scenario, equal-replicate hierarchical
bootstrap gives:

| Comparison | Miss-rate delta | Relative miss reduction | Sender-airtime ratio | Background loss |
| --- | ---: | ---: | ---: | ---: |
| V2 minus STR | +3.0865 pp `[+1.5367, +4.6140]` | -19.24% | 1.1231 `[1.0584, 1.1859]` | 0.206% `[-0.402%, 0.751%]` |
| Distributional minus STR | +2.7139 pp `[+1.1790, +4.2135]` | -16.91% | 1.1530 `[1.0887, 1.2146]` | 0.175% `[-0.428%, 0.739%]` |
| Distributional minus V2 | -0.3726 pp `[-0.5596, -0.1960]` | +1.95% | 1.0267 `[1.0201, 1.0336]` | -0.031% `[-0.102%, 0.025%]` |

Intervals are 95% paired hierarchical-bootstrap intervals.  V2 is decisively
worse than STR on reliability while its airtime upper endpoint remains below
1.20.  Distributional is decisively better than V2, but still decisively
worse than STR and its airtime upper endpoint exceeds 1.20.  Both background
effects remain inside the 1% limit.

V2 beats STR on miss rate in only 15 of 48 scenarios, ties one, and loses 32.
Distributional beats STR in 17, ties one, and loses 30.  Distributional beats
V2 in 36 of 48 scenarios, confirming that the new predictor/allocator has
real value without making it a viable STR competitor.

## Environment-family diagnosis

| Family | STR miss rate | V2 minus STR | Distributional minus STR | V2 airtime / STR | Distributional airtime / STR |
| --- | ---: | ---: | ---: | ---: | ---: |
| Compound shift | 40.9133% | +8.1065 pp | +7.3734 pp | 0.9457 | 0.9695 |
| Legacy coexistence | 19.8628% | +8.2639 pp | +7.7795 pp | 1.1594 | 1.1834 |
| OBSS geometry/MAC | 0.9236% | -0.0295 pp | +0.0972 pp | 1.1387 | 1.1810 |
| OBSS intensity | 26.7118% | -0.0104 pp | -0.2830 pp | 1.1514 | 1.1777 |
| Radio propagation | 6.6771% | +1.9062 pp | +1.0521 pp | 1.2206 | 1.2593 |
| Video workload | 1.1769% | +0.2823 pp | +0.2640 pp | 1.1360 | 1.1624 |

Legacy coexistence and compound shift dominate the reliability reversal.
Radio propagation also loses reliability while exceeding the desired airtime
ratio.  The sub-1.0 compound airtime ratios are not an efficiency success:
the selective runs collapse, complete fewer frames, and consequently perform
less transmission work.

## Exact repaired p23 scenario

All four original frozen seed pairs were recovered; no substitution was
needed.

| Arm | Misses / generated | Miss rate | Mean completed-survivor P99 |
| --- | ---: | ---: | ---: |
| STR MLO | 5,357 / 7,200 | 74.4028% | 32.459 ms, descriptive only |
| Score-aware T2 V2 | 6,111 / 7,200 | 84.8750% | 32.418 ms, descriptive only |
| Distributional-shadow T2 | 6,138 / 7,200 | 85.2500% | 32.524 ms, descriptive only |

Several p23 runs fail the 100-completion support requirement, so those P99
means are not qualification estimands.  The separately launched seed `21193`
also favors STR (78.67% misses versus 90.67% V2 and 98.11%
distributional).  It is retained under `supplementary_p23_seed21193/` only as
an excluded sensitivity check.

## Latency and burst interpretation

Every one of the 192-run arm totals has zero late completed frames.  All
57,181 STR misses, 68,133 V2 misses, and 66,819 distributional misses are
incomplete frames.  Consequently:

- the completion CDF and PDF describe survivors only;
- they cannot compensate for a higher incomplete-frame rate;
- completed-frame P99 becomes undefined under the frozen support rule in 28
  valid runs;
- the proposed policies show no visible survivor-tail advantage that could
  overturn the reliability result.

STR also has more isolated miss bursts: 57.75% of its bursts have length one,
versus 49.71% for V2 and 49.37% for distributional.  V2 has 6,852 multi-frame
bursts containing 61,361 misses; distributional has 6,382 containing 60,595;
STR has 4,975 containing 50,381.  The policies therefore worsen absolute
misses and clustering in the broad population.

## Formal P99 boundary

The unchanged frozen analyzer failed closed at run
`9893eb6f2fe9e4ed4f1a`, which has one completed frame rather than the required
100.  In total, 28 runs are unsupported: nine in OBSS-intensity `p20`, three
in compound `p20`, all twelve in compound `p22`, and four in compound `p23`.
These are valid reliability outcomes, not corrupt or missing data.

The post-outcome analyzer therefore preserves every run for deadline-miss,
airtime, and background estimands while marking P99 and formal promotion
`not_assessable`.  It does not lower the support threshold, discard collapse
runs, or substitute a survivor-conditioned P99 gate.

## Recovery and evidence-first record

The campaign originally stopped with 568 validated runs after one complete
attempt differed from replay by `2.220446049250313e-16` in a derived
probability subtraction.  The controller's float32 score, gate, and action
were identical.  Validator commit `5ca913a` correctly bounds the subtraction
by the sum of its two independently allowed probability roundoff bounds.

Before diagnosis or retry, the 568 valid runs, partial outputs, and failure
archive were fetched, hashed, analyzed, plotted, and preserved.  The balanced
partial panel lives under `partial_analysis/` and `partial_plots/`; it remains
explicitly exploratory.  The recovery then promoted the already complete
attempt and reran only the seven interrupted original run IDs.  The final
record is `recovery/attempt_recovery_5ca913a.json`.

The compressed pre-repair failure archive remains outside Git with SHA-256
`bb4c067ada47afc3ee2067f1a2fc70179ebfb7cd1231d8720e3aed5300ba9c00`.

## Archived artifacts

- `experiment_manifest.json`: exact final 576-run expansion and command
  records.
- `formal_analysis_status.json`: frozen analyzer failure and explicit
  `not_assessable` boundary.
- `analysis/`: strict 576-run report, run/family/scenario tables, and artifact
  manifest.
- `plots/`: ten reviewed figures in PNG and PDF plus their artifact manifest.
- `partial_analysis/` and `partial_plots/`: the evidence-first 568-run
  snapshot and visibly watermarked 360-run balanced panel.
- `recovery/`: failure diagnosis, exact retry driver, service unit, and final
  recovery record.
- `supplementary_p23_seed21193/`: excluded three-arm sensitivity run and exact
  execution provenance.

The ignored local raw root is
`results/environment_generalization_closed_loop_qualification_v1_partial_47e1996/runs`;
despite the historical directory name, it now contains the complete 576-run
tree.  The VM source remains under
`/home/jingweili/wifi_streaming_qualification_47e1996/results/environment_generalization_closed_loop_qualification_v1/runs`.
