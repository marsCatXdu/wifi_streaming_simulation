# Adaptive airtime OBSS v1 — invalidated

This snapshot is retained only as an audit artifact. Do not use its aggregates,
figures, or headline values as experimental evidence.

The v1 runs fail the corrected output contract for several independent reasons:

- The runner overrode `project_git_commit` with `a829356` even though the
  executed binary contained later, uncommitted implementation changes. The
  recorded run IDs and build metadata therefore do not identify the source that
  produced the data.
- Secondary reservations could settle after duplicate terminal callbacks rather
  than after all distinct packets reached terminal MAC state.
- Controller logs mixed pre- and post-decision reservation state, used the wrong
  reference header size, and did not preserve a per-frame settlement ledger.
- `policy_decisions.csv` recorded the initial primary-only state instead of the
  final delayed-duplication decision.
- The old validator checked row counts and aggregate sums but did not enforce the
  decision arithmetic, half-open measurement window, finite-run budget, or
  event/settlement reconciliation.

Those defects were corrected in commits `74da235`, `475beef`, `dd5bff9`, and
`fdeead8`. The corrected experiment is versioned separately as
`closed-loop-adaptive-airtime-obss-v2` and writes to
`results/adaptive_airtime_obss_v2/runs`.

The files in this directory are unchanged historical outputs except for this
notice. `experiment_manifest.json` demonstrates the invalid identity override;
the remaining tables and figures are useful only for tracing how the obsolete
claims were produced.
