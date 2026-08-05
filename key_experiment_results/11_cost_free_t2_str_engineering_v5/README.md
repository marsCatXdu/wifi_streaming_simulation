# Cost-free temporal-T2 V5 engineering result

This directory archives the closed-loop test of the cost-free raw-value
ranker selected by the frozen-head ablation. V5 keeps score-aware V2's exact
10-second measured-airtime guard, 0.6% refill, startup credit, `60000 us`
emergency debt limit, conservative canonical reservation, P-frame gate,
duplicate action, and neutral mixed-4x4 environment. It changes the active
ranker from value divided by learned cost to raw legacy-bad12 value and uses
the separately calibrated primary and emergency thresholds. Learned cost is
retained only as diagnostic telemetry.

The campaign deliberately reuses the already-opened engineering seeds `1251`
through `1298`, ns-3 run `1`, with one V5/STR pair per seed. It is a
within-development predictor/ranker test, not independent confirmation.
Reserved seeds `1301` through `1348` were not used.

## Qualification against STR MLO

All 96 raw runs passed the profile-bound strict validator on the experiment
VM and again after the checksum-verified archive was restored locally. V5
passes all four frozen engineering gates against STR:

| Metric | Cost-free T2 V5 | STR MLO | Paired comparison | Gate |
| --- | ---: | ---: | ---: | --- |
| All-generated deadline-miss rate | 0.5741% (496/86,400) | 0.7998% (691/86,400) | policy-minus-STR 95% interval `[-0.3113, -0.1435]` percentage points | Pass |
| Mean per-run completed-frame HF7 P99 | 17.081 ms | 18.875 ms | policy-minus-STR 95% interval `[-2.756, -0.886]` ms | Pass |
| Sender PHY airtime ratio | - | - | 1.1236, 95% interval `[1.0865, 1.1587]` | Pass (`< 1.20`) |
| Background-throughput loss | - | - | 0.0047%, 95% interval `[0.0023%, 0.0073%]` | Pass (`<= 1%`) |

This is a strong victory over STR, but it is not an improvement over V2.
V2 has 495 misses and 17.192 ms P99 on the same opened seeds. The direct
V5-minus-V2 paired intervals are `[-0.0255, +0.0312]` percentage points for
misses and `[-0.296, +0.057]` ms for P99. V5 therefore has one additional
miss, an uncertain 0.111 ms P99 point improvement, and 203 additional actions.
V2 remains the current best implementation because it is simpler and uses
less redundant traffic for statistically indistinguishable performance.

## Exact V2/V5 mechanism comparison

The raw action comparison rebinds both policy arms by seed and ns-3 run and
compares all 86,400 frame and decision rows. The underlying learned heads and
the retained value-per-cost diagnostic score are identical on every row. The
active runtime score differs on 42,731 rows, and score-threshold membership
changes on 790 rows.

| Diagnostic | Score-aware T2 V2 | Cost-free T2 V5 | Change |
| --- | ---: | ---: | ---: |
| Threshold passers | 7,484 | 8,218 | +734 |
| Secondary-copy actions | 4,944 | 5,147 | +203 |
| Acted primary-copy misses | 771 | 763 | -8 |
| Acted primary misses rescued | 737 | 736 | -1 |
| Final union misses | 495 | 496 | +1 |

V5 improves the first threshold boundary: below-threshold primary misses fall
from 170 to 147. That gain is erased after admission, where guard-rejected
primary misses rise from 162 to 193. Residual misses after an action fall from
34 to 27, so the exact component change is `-23 + 31 - 7 = +1` final miss.

The action sets contain 4,426 common actions, 518 V2-only actions, and 721
V5-only actions. V2-only actions contain 57 primary misses (11.00%), whereas
V5-only actions contain 49 (6.80%). V5 fixes 55 V2 misses but creates 56 new
misses. It also raises measured secondary airtime by 394,598 us across the 48
policy runs, or 3.42% relative to V2.

The scientific interpretation is narrow: removing the learned-cost divisor
does improve which frames cross the primary threshold, consistent with the
offline ablation, but the larger candidate set and raw-value ordering feed a
less risk-dense population through the chronological guard. The static
offline projection did not model that resource-allocation interaction. Do not
promote V5 or tune it on new seeds. A future candidate must optimize the
score and admission allocation together rather than treating threshold
ranking and guard chronology as independent layers.

## Figures

Qualification figures are directly under `figures/`:

- `paired_metric_deltas.png`;
- `paired_performance_tradeoff.png`;
- `resource_gates.png`;
- `policy_admission_diagnostics.png`;
- `v2_v5_admission_shift.png`, which shows the exact changed action sets and
  their primary-copy risk.

The historical suite is under `figures/standard/`:

- `latency_cdf.png` and `latency_pdf.png`;
- `deadline_miss.png`;
- `miss_burst_distribution.png` and
  `miss_burst_distribution_by_group.png`;
- `background_degradation.png`;
- `p99_redundancy_pareto.png`;
- `duplication_benefit_correlation.png`.

## Evidence and provenance

- Simulation and validator project commit:
  `ed21d0aafa28ca33e5a1628101d7478532434693`.
- V2/V5 comparison-tool commit: `a16bcb3`.
- ns-3 upstream commit: `d2add90b452d600cfb4859baed8e9ea633519447`.
- Frozen V5 runtime-contract SHA-256:
  `b7fb00982ae090fe1142b39adf0ad6d26d253741dd5059ed95637dd86047ba96`.
- Frozen matrix SHA-256:
  `fcac4ba30d3c8cd2394880e9550d9f40dc7b7c2ed56b66cb5293b3d91b8be363`.
- Validated 96-run manifest SHA-256:
  `e341438bb43dcef81a62862632bb95b643336dd8e08a4961cf3c58ba2587161a`.
- Locally regenerated strict report SHA-256:
  `d86cee0418069ee5337d9a9a84d8930b96959bf6f22d6529a077d493320fc373`.
- Aggregate JSON SHA-256:
  `4126beba63873cc24b9c33ab017a3131277d751e5dcd9bd5f6835929e2dfe62e`.
- Exact V2/V5 comparison SHA-256:
  `c9c29dad25409965c5528c64fc5f11d97fc8bfdb2dd75e2faec7c842d3d8339c`.

## Raw archive

The complete raw campaign is retained outside Git history at:

```text
results/paired_value_t2_cost_free_str_engineering_v5/
  paired-value-t2-cost-free-str-engineering-v5-ed21d0a.tar.zst
```

- SHA-256:
  `fce039fa28e3ecc8ba8c9bee6759eb6ba71f9af0a16277a7e7f834b01fbd5694`.
- Compressed size: 97,159,582 bytes.
- Decompressed tar size: 489,820,160 bytes.
- Canonical raw run directories: 96.

`zstd -t` passes locally and on the experiment VM. A checksum-identical copy
is at
`/home/jingweili/paired-value-t2-cost-free-str-engineering-v5-ed21d0a.tar.zst`
on the VM reached through `jingweili@10.120.16.105:30022`. This still needs
durable external publication before a release-quality handoff.

## Reproduction

With the V2 and V5 raw campaign archives restored, run:

```bash
PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/wifi-streaming-mpl \
  .venv/bin/python tools/analyze_paired_value_t2_str_qualification.py \
  --profile cost-free-v5 \
  --json-output results/paired_value_t2_cost_free_str_engineering_v5/runs/\
paired_value_t2_cost_free_str_engineering.json \
  --markdown-output results/paired_value_t2_cost_free_str_engineering_v5/runs/\
paired_value_t2_cost_free_str_engineering.md \
  --require-pass \
  results/paired_value_t2_cost_free_str_engineering_v5/runs/aggregate.json

MPLCONFIGDIR=/tmp/wifi-streaming-mpl .venv/bin/python \
  tools/plot_paired_value_t2_str_qualification.py \
  results/paired_value_t2_cost_free_str_engineering_v5/runs/aggregate.json \
  --profile cost-free-v5 \
  --analysis results/paired_value_t2_cost_free_str_engineering_v5/runs/\
paired_value_t2_cost_free_str_engineering.json

MPLCONFIGDIR=/tmp/wifi-streaming-mpl .venv/bin/python tools/plot_results.py \
  results/paired_value_t2_cost_free_str_engineering_v5/runs/aggregate.json \
  --output-dir \
  results/paired_value_t2_cost_free_str_engineering_v5/runs/plots

MPLCONFIGDIR=/tmp/wifi-streaming-mpl .venv/bin/python \
  tools/compare_paired_value_t2_admission.py \
  results/paired_value_t2_score_aware_str_engineering_v2/runs/aggregate.json \
  results/paired_value_t2_cost_free_str_engineering_v5/runs/aggregate.json \
  --baseline-label 'Score-aware T2 V2' \
  --candidate-label 'Cost-free T2 V5' \
  --json-output results/paired_value_t2_cost_free_str_engineering_v5/\
v2_v5_admission_comparison.json \
  --markdown-output results/paired_value_t2_cost_free_str_engineering_v5/\
v2_v5_admission_comparison.md \
  --plot-output results/paired_value_t2_cost_free_str_engineering_v5/\
v2_v5_admission_shift.png
```
