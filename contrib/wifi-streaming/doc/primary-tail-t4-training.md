# Primary-copy T4 miss and tail-risk model

`train_primary_tail_t4.py` builds an action-neutral two-head predictor. The
artifact contains the feature contract, fitted heads, calibrators, target
provenance, and score combiner. It deliberately contains no duplication mode,
airtime estimator, risk-density threshold, T0 guard, or token-bucket policy.
Those choices belong to separately named operating profiles and must be tested
in fresh-seed closed-loop simulation.

## Treatment-free targets

The source population contains only `obss_only`, `fixed_link_1`, path 1,
copy 0 frames. The trainer reopens every source `frames.csv`, verifies its
manifest checksums and resolved run configuration, and requires oracle
features to be disabled. It joins source outcomes to T4 samples by
`(seed, run, frame_id)` rather than depending on build-specific run IDs.

Both heads use the 101-feature
`exportable_driver_polling_1ms` T4 contract. Ideal F1 values are replaced by
recorded 1 ms polling observations, and the only F2 fields are those exposed
by the driver-exportable allowlist. The heads are:

- primary-copy deadline miss, fitted on all actionable T4 rows; and
- completed primary-copy latency at least 12,500 us, fitted and calibrated
  only on actionable rows where the primary copy completed.

Misses are censored out of the completed-latency head rather than assigned an
invented latency. The frozen score is:

```text
(primary_miss_probability + 0.2 * completed_tail_probability) / 1.2
```

The output is named `admission_score` and has score kind
`weighted_head_probability_admission_score`. Only the two head outputs are
calibrated probabilities. Their weighted arithmetic mean is a decision score,
not a calibrated probability for a single event. Both heads remain separate
outputs for auditing.

## Frozen split and evidence status

Split membership is frozen by simulation seed. Seeds 401, 404, 409, 418, 419,
and 422 are calibration-only. The other 18 seeds fit both final rankers. Four
explicit six-seed folds support model-only out-of-fold diagnostics.

These 24 seeds have already supported stage, feature, target, and combiner
exploration. The diagnostics are therefore post-selection engineering
evidence, not an independent OOD result. The model manifest says so explicitly;
fresh-seed closed-loop simulation is the only independent test.

## Model and operating-profile boundary

The serialized bundle and plain JSON export freeze only:

- dataset, validation, training-config, target, and feature provenance;
- the two histogram-gradient-boosting heads and Platt calibrators; and
- the admission-score name, kind, weights, normalization, and combiner.

An operating profile may consume that score and define an action cost, gate,
earlier-stage dependency, and airtime authority. Profiles must carry their own
identity and diagnostics. In particular, the whole secondary copy is the
primary T4 action to evaluate because MAC BlockAck progress can overstate what
the application receiver can release from its reorder buffer. A
primary-deficit action is retained only as an explicit ablation, never as the
model's implied deployment policy.

Offline capture metrics cannot reproduce retry inflation, asynchronous
reservation settlement, secondary delivery latency, or action feedback into
later snapshots. They are useful for choosing closed-loop experiments, not for
claiming a win over MLO.

## Reproducible artifact

Build the corrected fixed-link dataset:

```bash
.venv/bin/python tools/build_prediction_dataset.py \
  results/primary_tail_t4_source_v1/runs \
  --output-dir results/primary_tail_t4_source_v1/dataset \
  --analysis-config experiments/configs/prediction_analysis.yaml \
  --load-config experiments/configs/prediction_loads.yaml \
  --format parquet \
  --scenario obss_only
```

Inspect deterministic model pins without publishing:

```bash
.venv/bin/python tools/train_primary_tail_t4.py \
  results/primary_tail_t4_source_v1/dataset \
  --base-bundle-dir results/genuine_polling_v1/models \
  --config experiments/configs/primary_tail_t4_obss_v1.yaml \
  --output-dir /tmp/primary-tail-unpublished \
  --observed-pins-only
```

Publish the trusted-local bundle and deterministic plain export:

```bash
.venv/bin/python tools/train_primary_tail_t4.py \
  results/primary_tail_t4_source_v1/dataset \
  --base-bundle-dir results/genuine_polling_v1/models \
  --config experiments/configs/primary_tail_t4_obss_v1.yaml \
  --output-dir results/primary_tail_t4_corrected_v1/models

.venv/bin/python tools/export_primary_tail_t4_v1.py \
  --bundle results/primary_tail_t4_corrected_v1/models/primary_tail_t4_bundle.pkl \
  --manifest results/primary_tail_t4_corrected_v1/models/primary_tail_t4_manifest.json \
  --output results/primary_tail_t4_corrected_v1/models/primary_tail_t4_export.json
```

The trainer omits wall-clock time, timestamps, and invocation paths. Repeated
runs with the same environment and inputs produce byte-identical artifacts.
