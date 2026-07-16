Wi-Fi streaming experiment workflow
===================================

Workflow
--------

Run a batch from the repository root::

  python3 tools/run_experiments.py experiments/configs/smoke.yaml

The matched legacy-contention comparison is available as::

  python3 tools/run_experiments.py experiments/configs/legacy_contention.yaml

The distance-aware overlapping-BSS batches are available as::

  python3 tools/run_experiments.py experiments/configs/obss_contention.yaml
  python3 tools/run_experiments.py experiments/configs/combined_contention.yaml

``obss_contention.yaml`` adds four independent APs, one each for HT, VHT, HE,
and EHT, with four STAs per AP.  Every STA generates independent UL and DL
UDP ON/OFF traffic.  Uplink draws 0.5--3 Mbps and downlink draws 2--8 Mbps for
each ON period.  The 100/300 ms ON/OFF means give 25 percent expected duty and
about 27 Mbps aggregate OBSS offered load.  Background BSSs use Minstrel-HT;
the target remains fixed at EHT MCS 5.  ``combined_contention.yaml`` adds the
same overlapping BSSs on top of the sixteen stations in ``legacy_mixed8``,
for about 43 Mbps expected aggregate background load.

It compares dual-interface full duplication with native STR MLO against the
same eight seeded, independent mixed-standard UDP ON/OFF uplinks on each 2.4
and 5 GHz channel.
The 2.4 GHz contenders use HT/HE/EHT and the 5 GHz contenders use
HT/VHT/HE/EHT.  Per-station standards, random streams, ON/OFF means, and
per-link/per-station traffic totals are retained in run metadata.
Each topology runs for 60 simulated seconds at 30 frames/s, yielding exactly
1,800 frames.  Synthetic video uses 12 KB interframes and a 48 KB keyframe
every 60 frames (two seconds), approximating a fourfold I-frame size while
raising mean offered video load by only five percent.  Ten explicit seeds
provide ten paired rounds.  The streaming STA is placed 10 m from the AP
through ``station_distance_m``.

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

The latency CDF and PDF use one distribution per run.  Their center curves are
the pointwise median across runs and their shaded bands span the pointwise
10th--90th percentiles.  The PDF is a common-bin histogram density, not a
kernel-density estimate.

OBSS runs also contain ``background_flows.csv`` and
``background_rate_periods.csv``.  The former identifies all 32 directional
flows and their explicit streams.  The latter records every ON-period rate
draw.  Log-distance path loss and Nakagami fading make seeded AP/STA placement
physically relevant; both target topologies receive identical resolved
coordinates and traffic schedules for a matched seed.

Calibration
-----------

The supplied trace is a short pipeline smoke input, not a workload calibration.
Before publication, replace it with a trace whose generation intervals, frame
types, sizes, and deadlines were obtained from the target encoder and playback
contract.  Calibrate RSS, MCS, aggregation, queue limits, and background load
against measured devices.  Use multiple seeds and runs and inspect convergence
of run-level confidence intervals.

For the legacy profile, changing ``run`` selects different ns-3 substreams
while preserving explicit stream numbers; repeating the same seed and run is
reproducible.  Fair dual/MLO comparisons must keep the profile parameters,
seed, and run matched.

Limitations
-----------

``link_intervals.csv`` contains one whole-window sample per link.  Native MLO
does not expose application-byte attribution by link.  Redundant-copy airtime
is not measured, so the Pareto plot labels redundant bytes as an airtime proxy.
Cross-copy delay correlation and joint exceedance are available only for
duplicated frames with the required copy timestamps.  Sparse plots explicitly
state that data is insufficient.  The fixed-RSS smoke setup does not represent
mobility, fading, encoder/decoder delay, or firmware processing.
Changing ``station_distance_m`` changes only propagation delay while
``FixedRssLossModel`` is selected; it changes received power in the OBSS
log-distance configurations.
Non-MLD legacy stations associate dynamically to matching AP MLD links because
the ns-3.48 static-association helper is not safe for this mixed device shape;
the streaming MLD remains statically associated.
