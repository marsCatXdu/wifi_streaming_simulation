Wi-Fi streaming experiment workflow
===================================

Workflow
--------

Run a batch from the repository root::

  python3 tools/run_experiments.py experiments/configs/smoke.yaml

This command builds, executes, validates, summarizes, and writes the standard
plot set.  The tools may also be invoked independently::

  python3 tools/validate_outputs.py results/smoke/*/
  python3 tools/summarize_runs.py results/smoke \
    --json results/smoke/aggregate.json --csv results/smoke/aggregate.csv
  python3 tools/plot_results.py results/smoke/aggregate.json \
    --output-dir results/smoke/plots

The runner builds once, executes jobs with the configured worker count, captures
combined output in ``stdout.log``, validates each attempt, and only then renames
it to its final run ID.  A failed attempt remains hidden under an
``.attempt-`` name and is replaced safely when the same batch is resumed.
Existing complete run IDs are rejected by default.  ``--resume`` validates and
keeps completed runs while executing only missing runs.  Duplicate entries
within one expanded matrix are always rejected.

Configuration
-------------

YAML is consumed only by Python.  ``base`` is a nested, human-readable mapping.
``sweep`` maps dotted keys into that mapping; keys are sorted before their
deterministic Cartesian product is formed.  ``seeds``, ``runs``,
``topologies``, and ``policies`` are also expanded.  A topology may restrict
``policies`` and a policy may restrict ``topologies``.  Their optional
``config`` mappings are dotted-key overlays.

Every leaf is translated to an explicit C++ command-line option.  Relative
trace paths are resolved against the YAML directory.  The run ID is the first
20 hexadecimal characters of SHA-256 over canonical resolved configuration,
seed, run, the fixed ns-3 upstream commit, and project HEAD commit.

Output contract
---------------

A valid run contains ``resolved_config.json``, ``build_info.json``,
``frames.csv``, ``policy_decisions.csv``, ``link_intervals.csv``,
``mac_summary.csv``, ``summary.json``, and ``stdout.log``.  Validation checks
schemas, run IDs, frame/decision cardinality, completion and deadline
invariants, summary ratios, per-link totals, and build identity.  The
experiment manifest is replaced atomically after each completed run.

Aggregation treats runs, not frames, as independent samples.  Reported 95
percent confidence intervals use Student's t multipliers for up to ten runs and
the normal approximation thereafter.  Temporal miss-burst distributions are
retained.  Metrics not supported by available telemetry are represented as
JSON null, never zero.

Calibration
-----------

The supplied trace is a short pipeline smoke input, not a workload calibration.
Before publication, replace it with a trace whose generation intervals, frame
types, sizes, and deadlines were obtained from the target encoder and playback
contract.  Calibrate RSS, MCS, aggregation, queue limits, and background load
against measured devices.  Use multiple seeds and runs and inspect convergence
of run-level confidence intervals.

Limitations
-----------

``link_intervals.csv`` contains one whole-window sample per link.  Native MLO
does not expose application-byte attribution by link.  Redundant-copy airtime
is not measured, so the Pareto plot labels redundant bytes as an airtime proxy.
Cross-copy delay correlation and joint exceedance are available only for
duplicated frames with the required copy timestamps.  Sparse plots explicitly
state that data is insufficient.  The fixed-RSS smoke setup does not represent
mobility, fading, encoder/decoder delay, or firmware processing.
