# Full-horizon temporal-T2 V3 engineering null result

This directory archives the controlled test of increasing score-aware V2's
token-bucket carry-over from 10 seconds to the full 60-second experiment
horizon.  V3 keeps the predictor, float32 score threshold, P-frame gate,
high-score emergency tier, `60000 us` debt limit, 0.6% causal refill,
conservative canonical reservation, duplicate action, and neutral mixed-4x4
environment unchanged.  Its capacity is `360000 us`, but its startup credit
remains `12000 us`; it receives no unearned full-horizon credit at startup.

The campaign deliberately reuses the already-opened V2 engineering seeds
`1251` through `1298`, ns-3 run `1`, with one V3/STR pair per seed.  It is a
within-development mechanism test, not independent confirmation.  Reserved
seeds `1301` through `1348` were not used.

## Qualification result

All 96 raw runs passed the profile-bound strict validator.  V3 still passes
the four frozen engineering gates against STR, but every headline value is
exactly the V2 value:

| Metric | Full-horizon T2 V3 | STR MLO | Paired comparison | Gate |
| --- | ---: | ---: | ---: | --- |
| All-generated deadline-miss rate | 0.5729% (495/86,400) | 0.7998% (691/86,400) | policy-minus-STR 95% interval `[-0.3194, -0.1377]` percentage points | Pass |
| Mean per-run completed-frame HF7 P99 | 17.192 ms | 18.875 ms | policy-minus-STR 95% interval `[-2.643, -0.795]` ms | Pass |
| Sender PHY airtime ratio | - | - | 1.1217, 95% interval `[1.0847, 1.1565]` | Pass (`< 1.20`) |
| Background-throughput loss | - | - | 0.0054%, 95% interval `[0.0027%, 0.0081%]` | Pass (`<= 1%`) |

These numbers do not make V3 an improvement.  They reproduce V2 because V3
does not change a single admission or frame outcome.

## Exact V2/V3 behavioral comparison

The two policy arms were rebound by seed and ns-3 run from their respective
checksum-bound manifests.  Across 86,400 policy decisions:

- zero seeds and zero rows differ in decision status, threshold result,
  strict admission, emergency admission, or admission tier;
- zero of 86,400 `frames.csv` rows differ in any field except `run_id`;
- the aggregate CSV files are byte-identical, with SHA-256
  `55b95c127c149a930cb1f24dbf7912c050e9ea2d7558a67cebd5a2fa22036003`;
- the admission-diagnostic payloads, excluding provenance, are byte-identical.

This is not evidence that the implementation ignored the new profile.  The
guard balance differs on 29,673 rows, V3 reaches `360000 us`, and 29,519 V3
rows exceed V2's `60000 us` capacity.  The extra stored credit simply arrives
at the wrong decisions:

| V3 terminal class | Rows | Rows with more available credit than V2 | Maximum increase |
| --- | ---: | ---: | ---: |
| Strict action | 2,828 | 378 | 296.714 ms |
| Emergency action | 2,116 | 0 | 0 ms |
| Guard rejected | 2,540 | 0 | 0 ms |
| Noncandidate | 78,916 | 29,295 | 300 ms |

Thus every frame for which V2 needed emergency borrowing, and every candidate
V2 rejected, had exactly the same available credit under V3.  Additional
capacity only stored credit during noncandidate periods or on candidates V2
already admitted strictly.  `v2_v3_behavioral_comparison.json` records the
exact machine-readable comparison and source identities.

## Scientific decision

The proposed larger bucket is falsified as an admission intervention for this
traffic chronology.  V3 should remain frozen as a negative result and should
not be promoted or rerun on new seeds.

The next minimal guard mechanism is not more storage.  It is bounded borrowing
against refill that is causally known to remain before the measurement stop,
with conservative reservation and a repayment invariant.  That mechanism can
make future credit available at the low-balance decision itself while keeping
the long-run 0.6% budget.  It should first be tested on the same opened seeds.
Only after this admission question is resolved should model work target the
170 below-threshold misses, startup history, I-frames, and the large share of
actions spent on primaries that already arrive on time.

## Figures

Qualification figures are directly under `figures/`:

- `paired_metric_deltas.png`;
- `paired_performance_tradeoff.png`;
- `resource_gates.png`;
- `policy_admission_diagnostics.png`.

The requested historical suite is under `figures/standard/`:

- `latency_cdf.png` and `latency_pdf.png`;
- `deadline_miss.png`;
- `miss_burst_distribution.png` and
  `miss_burst_distribution_by_group.png`;
- `background_degradation.png`;
- `p99_redundancy_pareto.png`;
- `duplication_benefit_correlation.png`.

Because decisions and outcomes are identical, the V3 figures are numerically
identical to V2's figures apart from profile labels and provenance.

## Evidence and provenance

- Simulation and validator project commit:
  `ad16a842419c313ad41830acbec0823fd1657994`.
- ns-3 upstream commit: `d2add90b452d600cfb4859baed8e9ea633519447`.
- Frozen V3 runtime-contract SHA-256:
  `16ccbbfc19ac5c6b824c65b5f00fd0a8792610ea9239e9277390f51eda83f9d8`.
- Frozen matrix SHA-256:
  `d2671600dc9be5e60910de74aa15c7bd73852cd981eb602967d4cd628b4262ad`.
- Validated 96-run manifest SHA-256:
  `b37f7614d731257f83bcb79af5ab041e88f5619ba987f0fb9475cc9274c17a33`.
- Strict report SHA-256:
  `a2ab437d8b0005f1503db8ec23976c84145a1b5cba19339cf48d1288855852a0`.
- Aggregate JSON SHA-256:
  `3baeb362a82ab0a8a1f1c15a942231506aaa4feb76dbf014d8e2900b5f71bb1e`.

## Raw archive

The complete raw campaign is retained outside Git history at:

```text
results/paired_value_t2_full_horizon_str_engineering_v3/
  paired-value-t2-full-horizon-str-engineering-v3-ad16a84.tar.zst
```

- SHA-256:
  `a355df37bce69b57a9f8cf0f081b5c66f7ea4563508734ad824abd0fd27eb598`.
- Compressed size: 95,546,464 bytes.
- Decompressed tar size: 488,581,120 bytes.
- Canonical raw run directories: 96.

`zstd -t` passes locally and on the experiment VM.  A checksum-identical copy
is at
`/home/jingweili/paired-value-t2-full-horizon-str-engineering-v3-ad16a84.tar.zst`
on the VM reached through `jingweili@10.120.16.105:30022`.  As with V2, this
still needs durable external publication before a release-quality handoff.

## Reproduction

With the ignored raw `runs/` directory restored, run:

```bash
MPLCONFIGDIR=/tmp/wifi-streaming-mpl .venv/bin/python \
  tools/summarize_runs.py \
  results/paired_value_t2_full_horizon_str_engineering_v3/runs \
  --json results/paired_value_t2_full_horizon_str_engineering_v3/runs/aggregate.json \
  --csv results/paired_value_t2_full_horizon_str_engineering_v3/runs/aggregate.csv

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python \
  tools/analyze_paired_value_t2_str_qualification.py \
  results/paired_value_t2_full_horizon_str_engineering_v3/runs/aggregate.json \
  --profile full-horizon-v3 \
  --json-output \
  results/paired_value_t2_full_horizon_str_engineering_v3/runs/\
paired_value_t2_full_horizon_str_engineering.json \
  --markdown-output \
  results/paired_value_t2_full_horizon_str_engineering_v3/runs/\
paired_value_t2_full_horizon_str_engineering.md \
  --require-pass

MPLCONFIGDIR=/tmp/wifi-streaming-mpl .venv/bin/python \
  tools/plot_paired_value_t2_str_qualification.py \
  results/paired_value_t2_full_horizon_str_engineering_v3/runs/aggregate.json \
  --profile full-horizon-v3 \
  --analysis \
  results/paired_value_t2_full_horizon_str_engineering_v3/runs/\
paired_value_t2_full_horizon_str_engineering.json

MPLCONFIGDIR=/tmp/wifi-streaming-mpl .venv/bin/python \
  tools/plot_results.py \
  results/paired_value_t2_full_horizon_str_engineering_v3/runs/aggregate.json \
  --output-dir \
  results/paired_value_t2_full_horizon_str_engineering_v3/runs/plots
```
