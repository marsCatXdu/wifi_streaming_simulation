# Exact campaign recovery

The original 576-run campaign stopped after one completed run failed an
overly narrow numerical replay check.  The other seven jobs active at that
moment were terminated by the fail-fast watchdog.  The full pre-repair state
was archived before any retry or stale-attempt cleanup.

`failure_diagnosis.json` records the exact one-field discrepancy and the
validator correction.  The corrected validator is commit `5ca913a`; it passes
all 28 paired-value validation tests and validates the retained 1,800-frame
attempt end to end.

`resume_exact_qualification_runs.py` is the checksum-bound recovery driver.
It keeps the simulator and run identity fixed at commit `47e1996`, loads the
corrected validator from a separate clean checkout, preserves the existing
568 manifest command records, promotes the complete attempt, and executes
only the seven interrupted planned run IDs.  It does not substitute seeds or
insert supplementary runs into the preregistered population.

`wifi-qualification-repair-5ca913a.service` is the persistent user service
used to run that driver on the experiment VM.  It completed successfully:
the retained attempt was promoted, all seven interrupted original run IDs
were rerun, and the exact frozen matrix closed at 576/576.  The final manifest
SHA-256 is
`4c847ccc7d2e7d0ac9c154b0c4efc778ddcb403d0d12dabfd4a971c976269ef4`.
`attempt_recovery_5ca913a.json` records both manifest identities, every
recovered run ID, the promoted-attempt tree identity, and the absence of
recovery failures.

One three-arm seed-21193 trio was launched separately while the exact repair
was still running.  It is archived under `../supplementary_p23_seed21193/`,
explicitly excluded from the 576-run manifest and every qualification
estimand.  It was not needed to close the frozen population.

The compressed failure-evidence archive is intentionally retained outside
Git because it contains about 29.7 MB of partial raw outputs.  Its SHA-256 is
`bb4c067ada47afc3ee2067f1a2fc70179ebfb7cd1231d8720e3aed5300ba9c00`.
The local ignored copy is under the campaign's `failure_evidence/` result
directory; the VM copy is
`/home/jingweili/wifi-qualification-47e1996-failure-evidence-attempt-182816.tar.zst`.
