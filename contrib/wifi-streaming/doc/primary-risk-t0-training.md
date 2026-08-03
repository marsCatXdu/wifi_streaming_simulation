# Primary-copy T0 risk training

The controller-specific T0 model predicts whether the primary application
copy on path 1 will miss its frame deadline. It is deliberately separate from
the frozen general-purpose prediction evaluation. The latter treats OBSS as
out-of-distribution evidence; the controller model uses independent neutral
OBSS runs as its target training distribution.

## Label and source isolation

`primary_risk_t0_obss_v1.yaml` freezes a treatment-free population containing
only `obss_only`, `fixed_link_1`, path 1, copy 0, and T0 rows. The trainer
reopens every source `frames.csv`, verifies its checksum, and requires all of
the following before accepting its `deadline_miss` column as the primary
label:

- no frame was duplicated and copy 1 never completed;
- copy-0 completion equals union completion;
- the primary path is path 1; and
- a miss means copy 0 is incomplete or completes strictly after the inclusive
  deadline.

Adaptive actions, union outcomes from duplicated runs, secondary airtime, and
rescue outcomes never enter training or operating-point selection.

## Group split and fitting

Whole run groups are ordered by a frozen SHA-256 rule that does not inspect
labels. Of the 24 path-1 groups, 12 train the fixed histogram-gradient-boosting
model, six fit Platt calibration, and six remain test-only. The model keeps the
deployed 86-feature F0 plus recorded 1 ms polling contract. In particular, the
trainer substitutes the recorded `polling_1ms_` columns for ideal F1 fields;
it does not consume the ideal-observability upper bound.

The trainer first reports that honest 12/6/6 evaluation and a complete
four-fold group cross-validation. It then creates the deployment artifact by
refitting the ranker on all 18 non-calibration groups. The same six calibration
groups remain unseen by ranker fitting and alone fit Platt scaling and the
airtime gates. The earlier held-out metrics remain identified as evaluation
evidence; they are not relabelled as deployment-model test results. Fresh-seed
simulation is the independent test of the deployed controller.

The output bundle replaces only `(commodity_polling_1ms, T0)`. The base
bundle's T1, T2, and T4 predictors, along with its other sensitivity pipelines,
are retained without refitting or mutation. Canonical per-predictor SHA-256
fingerprints verify their fitted state after bundle serialization. The generated
split, metrics, and bundle manifests retain the dataset manifest, dataset,
source-frame, configuration, base-bundle, predictor, and output checksums.
Because the preserved later-stage models do not share this target-domain fit,
the new model identity is valid for controller decision offset 0 only. Both
the experiment executable and output validator reject later decision offsets
under this identity.

Run the training step with the repository virtual environment:

```bash
.venv/bin/python tools/train_primary_risk_t0.py \
  results/genuine_polling_v1/dataset \
  --base-bundle-dir results/genuine_polling_v1/models \
  --config experiments/configs/primary_risk_t0_obss_v1.yaml \
  --output-dir results/primary_risk_t0_obss_v1/models
```

## Calibration-only airtime gates

The trainer freezes risk-density gates for estimated secondary sender-airtime
targets of 0.50, 0.70, and 0.95 percent. Risk density is calibrated primary
miss probability divided by normalized whole-copy airtime. Selection uses the
same strict predicate as the controller:

```text
probability / normalized_cost > threshold
```

Each threshold is selected from calibration features, probabilities, and
estimated costs only. For the honest evaluation model, the selected threshold
is applied unchanged to the held-out six-group test partition; that report is
the controller-free operating-point evidence. Separately, deployment gates are
selected with probabilities from the ranker refit on all non-calibration
groups. Calibration labels are consulted afterward to report action rate,
primary-miss recall and precision, and separate I/P-frame strata. Held-out
outcomes never select a threshold or budget. Fresh-seed closed-loop simulation
is the independent test of the refit deployment artifact.

The estimator reproduces the controller's nominal EHT MCS5, 20 MHz, 800 ns
guard-interval whole-copy cost with the 1.25 safety factor. Retry inflation at
runtime can increase the estimate, while the measured-airtime token bucket
remains the hard authority. The 0.95 percent point is therefore a formal
maximum, not an instruction to spend the full allowance.

After training, export the copied bundle and verify sklearn/C++ parity:

```bash
.venv/bin/python tools/export_prediction_models_v1.py \
  --bundle results/primary_risk_t0_obss_v1/models/model_bundle.pkl \
  --manifest results/primary_risk_t0_obss_v1/models/model_bundle_manifest.json
./ns3 build streaming-experiment
./test.py -s wifi-streaming
```
