# Remaining-refill temporal-T2 V4 engineering result

This directory archives the controlled test of a final admission tier that may
reserve against refill causally remaining before the measurement stop. V4
keeps V3's predictor, float32 score threshold, P-frame gate, strict tier,
high-score emergency tier, `60000 us` emergency debt limit, 0.6% refill,
conservative canonical reservation, duplicate action, and neutral mixed-4x4
environment unchanged. The new tier is considered only after strict and
emergency admission both fail.

The campaign deliberately reuses the already-opened engineering seeds `1251`
through `1298`, ns-3 run `1`, with one V4/STR pair per seed. It is a
within-development mechanism test, not independent confirmation. Reserved
seeds `1301` through `1348` were not used.

## Qualification result

All 96 raw runs passed the profile-bound strict validator on the experiment
VM and again after the checksum-verified archive was restored locally. V4
passes all four frozen engineering gates against STR:

| Metric | Remaining-refill T2 V4 | STR MLO | Paired comparison | Gate |
| --- | ---: | ---: | ---: | --- |
| All-generated deadline-miss rate | 0.6771% (585/86,400) | 0.7998% (691/86,400) | policy-minus-STR 95% interval `[-0.2164, -0.0335]` percentage points | Pass |
| Mean per-run completed-frame HF7 P99 | 17.395 ms | 18.875 ms | policy-minus-STR 95% interval `[-2.484, -0.538]` ms | Pass |
| Sender PHY airtime ratio | - | - | 1.1255, 95% interval `[1.0878, 1.1611]` | Pass (`< 1.20`) |
| Background-throughput loss | - | - | 0.0050%, 95% interval `[0.0025%, 0.0076%]` | Pass (`<= 1%`) |

This is still a negative policy iteration. The unchanged V2/V3 policy has 495
misses and 17.192 ms P99 on these same development seeds, so V4 adds 90 misses
and 0.202 ms P99 while using slightly more airtime. V2 remains the current
best implementation.

## Exact V3/V4 admission comparison

`v3_v4_admission_comparison.json` rebinds the policy arms by seed and ns-3
run, then compares all 86,400 decision and frame rows. The predictor score and
score-threshold membership are identical on every row. Primary-copy outcome
also changes on only one frame. This isolates admission chronology as the
cause of the regression.

| Diagnostic | Full-horizon T2 V3 | Remaining-refill T2 V4 | Change |
| --- | ---: | ---: | ---: |
| Secondary-copy actions | 4,944 | 5,272 | +328 |
| Acted primary-copy misses | 771 | 675 | -96 |
| Acted primary misses rescued | 737 | 646 | -91 |
| Final union misses | 495 | 585 | +90 |

The action sets are not nested over the closed-loop run. They contain 3,910
common actions, 1,034 V3-only actions, and 1,362 V4-only actions. The V3-only
set contains 172 primary misses (16.63%), while the V4-only set contains only
76 (5.58%). The V3-only actions have median score `1.7865e-4` and median frame
time 46.45 s. The V4-only actions have median score `1.1684e-4` and median
frame time 15.27 s.

Thus the new tier locally adds an action when queried, but that early
reservation changes later guard state. It spends future refill on earlier,
lower-score frames and later displaces higher-score actions. Of the 495 V3
misses, V4 fixes 82 but retains 413; it creates 172 new misses, for the net
increase of 90. Conditional duplication still works: rescue efficiency is
95.70% in V4 versus 95.59% in V3. Admission allocation, not rescue efficacy,
is the failure.

## Scientific decision

The proposed chronological use of all causally remaining refill is falsified
as an improvement over score-aware V2/V3. Do not promote V4, rerun it on new
seeds, or interpret its pass against STR as progress over the current best
policy. A pointwise statement that the final tier "can only add actions" does
not imply a closed-loop action superset when every added reservation changes
future credit.

The guard experiments have now answered the immediate admission questions:
V2's bounded high-score emergency tier is useful; larger stored capacity is a
null intervention; unrestricted chronological remaining-refill borrowing is
harmful. Return to V2 and move the next development boundary to the predictor
and ranker. In particular, target the 170 below-threshold V2 misses and wasted
actions on primary copies that arrive on time, while preserving the current
runtime action and resource accounting. Startup fallback and an I-frame rule
remain separate later interventions.

## Figures

Qualification figures are directly under `figures/`:

- `paired_metric_deltas.png`;
- `paired_performance_tradeoff.png`;
- `resource_gates.png`;
- `policy_admission_diagnostics.png`;
- `v3_v4_admission_shift.png`, which shows the exact action-set, risk, score,
  and chronology shift relative to V3.

The requested historical suite is under `figures/standard/`:

- `latency_cdf.png` and `latency_pdf.png`;
- `deadline_miss.png`;
- `miss_burst_distribution.png` and
  `miss_burst_distribution_by_group.png`;
- `background_degradation.png`;
- `p99_redundancy_pareto.png`;
- `duplication_benefit_correlation.png`.

## Evidence and provenance

- Simulation and validator project commit:
  `28c2b1ba80804e54281f7728a5b2ccc198d64ce0`.
- Admission-comparison tool commit: `6fed1ca`.
- ns-3 upstream commit: `d2add90b452d600cfb4859baed8e9ea633519447`.
- Frozen V4 runtime-contract SHA-256:
  `0b5d31861c862e1b4fb31231936ecd144958939308b21566e97405a29de0d9dd`.
- Frozen matrix SHA-256:
  `1b49aeebf241dd0111a958311b62c859064c84d9e4597c11f85a8753aed658d6`.
- Validated 96-run manifest SHA-256:
  `1bf8ffe0b3550ad39ff1df43ade8d71ae691aa2f78ac70d7a7d48361eff8d4e6`.
- Locally regenerated strict report SHA-256:
  `26bafbb66c54f060ce6556f7d4ca329f3b6e7e43f436edeb3dcf060c438234d2`.
- Aggregate JSON SHA-256:
  `a6ac67796cfd5c7fcf3d80b92422634fe27c5f47be703377b148cce3c5c12d51`.
- Exact V3/V4 comparison SHA-256:
  `c2fc9106e238d8c11f0741b3e9aa67cabc9756f261b91fa4cad1b9dcdaf3a2d1`.

## Raw archive

The complete raw campaign is retained outside Git history at:

```text
results/paired_value_t2_remaining_refill_str_engineering_v4/
  paired-value-t2-remaining-refill-str-engineering-v4-28c2b1b.tar.zst
```

- SHA-256:
  `94a440bcf2ca2cb255fe8393ea2b18600196d692411bc97d0d8999a0ace51301`.
- Compressed size: 96,983,224 bytes.
- Decompressed tar size: 493,783,040 bytes.
- Canonical raw run directories: 96.

`zstd -t` passes locally and on the experiment VM. A checksum-identical copy
is at
`/home/jingweili/paired-value-t2-remaining-refill-str-engineering-v4-28c2b1b.tar.zst`
on the VM reached through `jingweili@10.120.16.105:30022`. This still needs
durable external publication before a release-quality handoff.

## Reproduction

With both ignored raw campaign directories restored, run:

```bash
PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/wifi-streaming-mpl \
  .venv/bin/python tools/analyze_paired_value_t2_str_qualification.py \
  results/paired_value_t2_remaining_refill_str_engineering_v4/runs/aggregate.json \
  --profile remaining-refill-v4 \
  --json-output \
  results/paired_value_t2_remaining_refill_str_engineering_v4/runs/\
paired_value_t2_remaining_refill_str_engineering.json \
  --markdown-output \
  results/paired_value_t2_remaining_refill_str_engineering_v4/runs/\
paired_value_t2_remaining_refill_str_engineering.md \
  --require-pass

MPLCONFIGDIR=/tmp/wifi-streaming-mpl .venv/bin/python \
  tools/plot_paired_value_t2_str_qualification.py \
  results/paired_value_t2_remaining_refill_str_engineering_v4/runs/aggregate.json \
  --profile remaining-refill-v4 \
  --analysis \
  results/paired_value_t2_remaining_refill_str_engineering_v4/runs/\
paired_value_t2_remaining_refill_str_engineering.json

MPLCONFIGDIR=/tmp/wifi-streaming-mpl .venv/bin/python tools/plot_results.py \
  results/paired_value_t2_remaining_refill_str_engineering_v4/runs/aggregate.json \
  --output-dir \
  results/paired_value_t2_remaining_refill_str_engineering_v4/runs/plots

MPLCONFIGDIR=/tmp/wifi-streaming-mpl .venv/bin/python \
  tools/compare_paired_value_t2_admission.py \
  results/paired_value_t2_full_horizon_str_engineering_v3/runs/aggregate.json \
  results/paired_value_t2_remaining_refill_str_engineering_v4/runs/aggregate.json \
  --baseline-label 'Full-horizon T2 V3' \
  --candidate-label 'Remaining-refill T2 V4' \
  --json-output \
  results/paired_value_t2_remaining_refill_str_engineering_v4/\
v3_v4_admission_comparison.json \
  --markdown-output \
  results/paired_value_t2_remaining_refill_str_engineering_v4/\
v3_v4_admission_comparison.md \
  --plot-output \
  results/paired_value_t2_remaining_refill_str_engineering_v4/\
v3_v4_admission_shift.png
```
