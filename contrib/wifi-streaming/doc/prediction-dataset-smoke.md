# Increment 2 dataset smoke acceptance

## Scope

This report records the first telemetry-enabled Increment 2 dataset smoke
batch. It validates the join, label, support-mask, provenance, and grouped
split workflow before Stage-A or OOD production is launched.

The simulations and dataset tools used project commit:

```text
cc61de85631af6ba77245dfaf56dde314ac0a31f
```

The two source batches were:

```text
results/prediction_dataset_smoke_stage_a
results/prediction_dataset_smoke_obss
```

All 32 Stage-A runs and all 4 OBSS runs completed. No failed or replacement
run was admitted.

## Dataset

The accepted smoke artifact is:

```text
results/prediction_dataset_smoke_v1
```

Its principal counts are:

```text
runs:                 36
matched run groups:   18
frames:             6000
samples:           24000
deadline misses:     677
overall miss rate: 11.2833%
```

The source telemetry and support-mask schema versions are both 2. The
labelled dataset SHA-256 is:

```text
2d4bac0bd0b93baa79d60a65d64bb032b194036b1fcd5dd8cd55359453377793
```

Parquet was requested. PyArrow was unavailable, so the builder used its
explicitly recorded CSV fallback. No data was omitted.

## Load and scenario coverage

The frozen Stage-A regimes all contain both classes except `unloaded`, which
correctly has no misses in this short smoke:

```text
regime       frames  misses  miss rate
unloaded        300       0     0.0000%
low             900      16     1.7778%
medium          900      48     5.3333%
high            900     171    19.0000%
off_target     1800     246    13.6667%
```

The low-regime smoke rate is above its pilot target because this check uses
one short seed. It does not supersede the frozen three-seed pilot estimate.
Medium and high retain the intended separation.

Both required OOD scenarios passed their exact resolved-config filters:

```text
scenario                         frames  misses  miss rate
obss_only                           600       6     1.0000%
obss_plus_legacy_mixed8             600     190    31.6667%
```

Each fixed-link pair maps to one run group. Fixed link 0 has 253 misses in
3000 frames; fixed link 1 has 424 misses in 3000 frames.

## Validation result

`dataset_validation.json` reports `PASS`. The following checks passed:

```text
causal derived ages
constant labels across frame snapshots
counter monotonicity
exact frame join
fixed path match
label provenance
model allowlist exclusions
OOD isolation
raw sample preservation
run-group reconstruction
source checksums
split-group isolation
support-mask source validation
unique sample keys
```

All unsupported fields remain null. The validator found no suspicious
near-perfect feature-to-label correlation.

The smoke split is intentionally marked `insufficient_data`: it has four
groups in each ID validation/test role and one group per required OOD
scenario, below the frozen production minimums. This is expected for a
structural smoke and is not a validation failure.

An independent rebuild to
`results/prediction_dataset_smoke_replay_tmp` produced byte-identical
`labelled_samples.csv` and `splits.json`.

## Decision

The Increment 2 dataset smoke gate passes. The evidence does not expose an
Increment 1 telemetry defect. The production Stage-A and OOD batches may
start from the frozen configurations.
