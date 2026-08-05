# Cost-free temporal-T2 V5 contract erratum

This erratum applies to the frozen runtime contract
`paired-value-duplication-t2-cost-free-score-aware-v5.json`, whose SHA-256 is
`b7fb00982ae090fe1142b39adf0ad6d26d253741dd5059ed95637dd86047ba96`.
The contract remains byte-for-byte unchanged because that identity is bound
into the V5 run configuration, telemetry, manifest, and strict validation.

The `selection_evidence` field
`secondary_feature_and_larger_model_prototypes_were_null` is an unsupported
historical note.  No checksum-bound prototype definitions, outputs, metrics,
or model-selection record were archived with the contract.  Therefore:

- do not cite the field as evidence that secondary-path features or larger
  models are unhelpful;
- do not use it to exclude either family from a future registered ablation;
- limit reproducible V5 selection claims to the three checksum-bound
  cost-denominator-ablation artifacts named in `selection_evidence`; and
- treat the 48-pair V5 campaign as evidence about the exported raw-value
  ranker combined with the inherited V2 chronological admission controller,
  not about unarchived prototypes.

This correction is interpretive only.  It does not change V5 runtime
behavior, thresholds, validation, or the archived result.
