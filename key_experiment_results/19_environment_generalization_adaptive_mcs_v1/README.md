# Environment-generalization adaptive-MCS qualification

`complete_575` is the final result.  It freshly validates all 575 promoted
adaptive-MCS runs and compares 191 complete scenario/seed three-arm units with
the matching fixed-MCS evidence.  One adaptive STR run deterministically
aborted twice inside ns-3; its entire three-arm unit is excluded from both MCS
modes rather than replacing its seed or mixing a patched binary.

`partial_pre_recovery` preserves the required pre-diagnosis checkpoint: 479
strictly validated promoted runs, early plots, observations, and the failure
record captured before the exact retry.

The final result is negative: unretuned adaptive Minstrel increases deadline
misses and sender airtime for STR, V2, and Distributional.  Neither selective
policy beats adaptive STR.
