# Causal online prediction replay

The online replay evaluates whether a fixed-link 5 GHz sender could warn about
deadline risk as frames arrive. It reuses recorded simulation telemetry but
does not rerun ns-3 and does not modify a frame outcome. Consequently, an
action means that a warning and its hypothetical resource permission were
issued; it does not mean that a deadline miss was prevented.

## Inputs and separation

`prediction_online_replay.yaml` freezes the replay choices. Path 1 is required
because the dual-interface topology maps path 1 to 5 GHz. Model selection uses
`training` and `validation_selection`; Platt calibration uses
`validation_calibration`. Only `in_distribution_test` and
`out_of_distribution_test` individual run directories are replayed.

Training uses the combined labelled dataset because it supplies stable grouped
partitions and labels. Replay does not read future rows from that dataset.
Instead, it applies the frozen bundle to each run's `prediction_samples.csv`
in chronological order. `frames.csv` is read separately and only after scores
have been produced, to evaluate the immutable deadline-miss labels.

The primary predictor is `F0+F1-degraded` with the frozen 1 ms commodity
polling profile. Exportable-driver telemetry is a sensitivity result, and
ideal telemetry is an upper bound.

## Decisions

The fixed-T0 and fixed-T1 policies make one decision per frame. The sequential
policy checks T0, T1, T2, and T4 in order and uses the first stage whose
calibrated miss probability reaches the fixed threshold. A frame is never
acted on twice.

A token is virtual action credit, not a Wi-Fi packet or protocol field.

- A frame bucket with budget `b` earns `b` credits for each arriving frame.
  One action costs one credit.
- A byte bucket earns `b * frame_size_bytes` credits. Hypothetically
  duplicating a frame costs its full size.

The bucket starts full and has a training-derived one-second burst capacity.
This permits short risk bursts while enforcing the configured long-run
resource rate. Frame and byte budgets are separate experiments.

## Commands

Fit and freeze the path-1 models:

```bash
python3 tools/replay_online_prediction.py train \
    results/prediction_dataset_provisional \
    --analysis-config experiments/configs/prediction_analysis.yaml \
    --replay-config experiments/configs/prediction_online_replay.yaml \
    --output-dir results/prediction_online_models_v1
```

Replay all eligible individual runs:

```bash
python3 tools/replay_online_prediction.py replay \
    results/prediction_dataset_provisional \
    --bundle-dir results/prediction_online_models_v1 \
    --replay-config experiments/configs/prediction_online_replay.yaml \
    --output-dir results/prediction_online_replay_v1
```

`--run-id` may be repeated to evaluate an explicit subset. `--max-runs` is
provided for bounded smoke tests. The aggregate is always built from exactly
the selected per-run results, so later analysis may combine part or all of the
runs without changing the replay rule.

## Outputs

Each run receives:

- `online_frame_scores.csv`, containing stage-local frozen model scores;
- `online_replay_metrics.csv`, containing all threshold and budget results;
- `online_replay_events.csv`, containing representative frame decisions;
- `online_replay_run.json`, containing source checksums and provenance.

The result root contains per-run and aggregate metrics, group-bootstrap
confidence intervals, plots, a manifest, and `online_replay_report.md`.

The key metrics are miss recall, warning precision, realized action rate,
realized byte overhead, useful-lead-time recall, threshold-negative misses,
and misses suppressed by exhausted budget. Global Top-K ranking remains only
an optimistic offline upper bound because it can inspect the complete test
population before choosing frames.
