# Prediction dataset workflow

Increment 2 converts passive sender snapshots into an offline labelled
dataset. It does not add a model or adaptive action to the simulator.

## Frozen inputs

The workflow uses:

```text
experiments/configs/prediction_analysis.yaml
experiments/configs/prediction_loads.yaml
experiments/configs/prediction_stage_a.yaml
experiments/configs/prediction_obss.yaml
```

`prediction_loads.yaml` records the telemetry-disabled pilot evidence,
selected low, medium, and high miss-rate loads per fixed link, and the union
of rates run as matched link pairs. A regime is analysis metadata. It is not a
simulator input.

The stable scenario names are:

```text
stage_a_none
stage_a_independent
stage_a_common_bursts
stage_a_mixed_common_and_independent
obss_only
obss_plus_legacy_mixed8
```

The two OBSS names are required OOD scenarios. Their exact resolved filters
are checked against `prediction_analysis.yaml`.

## Build

Build a dataset from one or more completed batch roots:

```bash
python3 tools/build_prediction_dataset.py \
    results/prediction_stage_a \
    results/prediction_obss \
    --output-dir results/prediction_dataset \
    --analysis-config experiments/configs/prediction_analysis.yaml \
    --load-config experiments/configs/prediction_loads.yaml \
    --format parquet
```

The builder requires an explicit output format. If Parquet is requested but
PyArrow is unavailable, it writes CSV and records the fallback in
`dataset_manifest.json`. It never silently skips a failed or invalid run.

Samples join to frames on `(run_id, frame_id)`. The selected fixed path must
match `path_id`, `primary_link`, and policy. Final completion and deadline
labels come only from `frames.csv` and remain constant across all snapshots
of a frame. Derived ages use only timestamps no later than the sample time.

## Run groups and splits

The group identity hashes nominal scenario parameters, seed, run substream,
project commit, and ns-3 commit after removing only the selected fixed-link
policy. Matched link-0 and link-1 runs therefore share one `run_group_id`.

`splits.json` assigns whole groups to:

```text
training
validation_selection
validation_calibration
in_distribution_test
out_of_distribution_test
```

Assignment is deterministic from the frozen split seed and does not inspect
labels. Every OBSS group remains OOD. A smoke dataset may validate
structurally while reporting `insufficient_data` for production split
minimums or class coverage.

## Outputs and validation

The output directory contains:

```text
labelled_samples.parquet or labelled_samples.csv
dataset_manifest.json
dataset_validation.json
splits.json
```

`tools/validate_prediction_dataset.py` revalidates every source run and
checksum, reproduces run groups and labels, verifies raw sample preservation,
checks causal derived fields and monotone counters, audits support masks
through the source validator, and proves that no run group crosses a split.
It also reports missingness, support rates, class balance, quantiles, constant
features, and suspicious label correlations.

Large Stage-A and OOD batches must not start until a small telemetry-enabled
dataset passes these structural checks.
