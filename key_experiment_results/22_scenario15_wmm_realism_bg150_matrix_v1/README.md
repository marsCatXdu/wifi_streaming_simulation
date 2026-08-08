# Scenario-15 WMM realism matrix with 1.5x background load

This archive repeats the complete scenario-15 WMM realism matrix after
multiplying every OBSS flow's ON-period offered rate by 1.5.  It preserves the
32 flow identities, ON/OFF timing, topology, fixed MCS, WMM mappings, policies,
models, seeds, and random streams.  UL rates change from Uniform(0.5, 3) to
Uniform(0.75, 4.5) Mbps; DL rates change from Uniform(2, 8) to Uniform(3, 12)
Mbps.  This is 50% more offered traffic, not a guaranteed 50% increase in
achieved goodput.

The matrix contains four WMM profiles, three arms, and ten identical opened
seeds per cell: 120 new simulations and 216,000 generated frames.  All new runs
and all 120 retained 1.0x runs passed fresh strict validation.  No replacement
seed and no seed from the reserved 1301 through 1348 range was used.

## Result

Each cell contains ten runs and 18,000 generated frames.

| Target / competitors | Approach | Misses | Miss rate | Mean per-run P99 | Sender airtime | OBSS goodput | Actions |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| BE / BE | STR MLO | 902 | 5.0111% | 27.171 ms | 4.765 s/run | 40.438 Mbps | 0 |
| BE / BE | Score-aware T2 V2 | 1,359 | 7.5500% | 27.111 ms | 5.943 s/run | 40.438 Mbps | 1,659 |
| BE / BE | Distributional shadow T2 | 1,333 | 7.4056% | 27.254 ms | 6.054 s/run | 40.439 Mbps | 1,870 |
| AF41/VI / BE | STR MLO | 2 | 0.0111% | 7.663 ms | 4.132 s/run | 40.441 Mbps | 0 |
| AF41/VI / BE | Score-aware T2 V2 | 2 | 0.0111% | 9.926 ms | 4.551 s/run | 40.440 Mbps | 1,982 |
| AF41/VI / BE | Distributional shadow T2 | 3 | 0.0167% | 10.004 ms | 4.527 s/run | 40.439 Mbps | 1,870 |
| AF41/VI / one VI per channel | STR MLO | 2 | 0.0111% | 7.810 ms | 4.128 s/run | 40.441 Mbps | 0 |
| AF41/VI / one VI per channel | Score-aware T2 V2 | 2 | 0.0111% | 9.752 ms | 4.562 s/run | 40.437 Mbps | 1,939 |
| AF41/VI / one VI per channel | Distributional shadow T2 | 2 | 0.0111% | 9.780 ms | 4.548 s/run | 40.437 Mbps | 1,870 |
| AF41/VI / all VI | STR MLO | 212 | 1.1778% | 20.251 ms | 4.380 s/run | 38.468 Mbps | 0 |
| AF41/VI / all VI | Score-aware T2 V2 | 53 | 0.2944% | 18.063 ms | 4.774 s/run | 35.645 Mbps | 1,617 |
| AF41/VI / all VI | Distributional shadow T2 | 46 | 0.2556% | 16.961 ms | 4.850 s/run | 35.180 Mbps | 1,855 |

## Main interpretation

The all-VI equal-priority stress case is a strong target-level win for both
selective policies.  Distributional reduces misses by 78.30% relative to STR;
its paired miss-rate delta is -0.9222 percentage points with a 95% interval of
[-1.2222, -0.6222], and it is better on all ten seeds.  Its P99 delta is
-3.290 ms, interval [-5.485, -1.246], while its sender-airtime ratio is 1.1072,
interval [1.0740, 1.1309].  The all-generated deadline-censored mean also
improves by 0.347 ms, interval [0.066, 0.696] ms.

Distributional is better than V2 in this stress case: seven fewer misses,
1.102 ms lower P99 with interval [-1.626, -0.667] ms, and 1.0159 times V2
sender airtime with interval [1.0017, 1.0363].  This reverses the practical
tie at 1.0x load and shows that the distributional allocator becomes useful
when the risk regime is sufficiently dense.

The win is not a complete engineering defeat of STR under the repository's
current fairness gate.  V2 and Distributional reduce competing OBSS goodput by
7.34% and 8.55% relative to STR in the all-VI case.  Distributional's paired
loss interval is [3.49%, 14.15%], well above the 1% target.  The extra repair
traffic is using capacity that same-access-category competitors would
otherwise receive.

BE/BE exposes the opposite regime.  V2 and Distributional increase miss rate
over STR by 2.5389 and 2.3944 percentage points; both paired 95% intervals are
strictly positive.  Their sender-airtime ratios are 1.2471 and 1.2706, above
the 1.20 target, while P99 is statistically tied with STR.  The selective
architecture therefore does not generalize across access-category mappings at
this load.

In the two lightly stressed VI-target profiles every arm has only two or three
misses.  Selective duplication has no measurable reliability value there; it
makes P99 about 2 ms worse and adds about 10% sender airtime.  A useful runtime
policy ultimately needs a conservative no-action region for such states.

## Primary-copy mechanism diagnosis

The repair action itself remains effective.  In BE/BE, V2 reconstructs 1,860
primary-copy misses and rescues 501 of 526 acted primary misses (95.25%);
Distributional reconstructs 1,859 and rescues 526 of 562 (93.59%).  Those
rescues reduce final misses substantially, but not enough to beat STR's 902.
The dominant BE/BE deficit therefore exists before union repair.

In all-VI, V2 and Distributional have exactly the same 69 factual primary-miss
frames.  V2 rescues 16 and finishes with 53 misses; Distributional rescues 23
and finishes with 46.  Thus Distributional's seven-miss advantage over V2 is
an admission/prediction benefit.  However, 69 primary-copy misses already beat
STR's 212, so most of the selective-versus-STR improvement precedes secondary
repair selection.

The reconstructed primary outcome is factual under the selective topology and
its concurrent repair traffic.  It is not a counterfactual no-redundancy run.
The next mechanism gate should therefore add a single-primary/no-redundancy arm
for representative BE/BE and all-VI cells before training another predictor.
That experiment can distinguish topology/scheduling value from prediction and
repair value.  No such follow-up was started here.

## Load sensitivity

The paired load audit compared 579,996 generated ON periods.  Every period
identity and timing matches the retained baseline, and every rate is 1.5 times
the baseline within `5.5001336818349955e-11` Mbps.

The 50% offered-rate increase raises achieved OBSS goodput by approximately
50% in BE/BE and the two low-contention VI profiles.  All-VI begins to saturate:
STR still gains about 47.6%, while V2 and Distributional gain only about 35.7%
and 33.4%.  Their repair traffic competes directly with the all-VI background
load, explaining the fairness loss.

## Evidence and figures

- `scenario15_wmm_realism_bg150_matrix.json` is the authoritative schema-v2
  report.  It includes the paired statistics and exact primary-copy rescue
  decomposition; its Markdown companion is the generated compact summary.
- `run_metrics.csv` contains all 240 new and baseline run rows, including the
  primary-copy counts.  `paired_comparisons.csv` contains all paired intervals.
- `plot_data.json` is the compact input for the 16 PNG/PDF figure pairs under
  `figures/`.
- `figures/deadline_miss.png`, `figures/completed_p99.png`,
  `figures/sender_airtime.png`, and `figures/background_throughput.png` show
  the headline outcomes.
- `figures/bg150_vs_bg100_effects.png` shows the exact same-seed load effect;
  the two load-overlay tail CDF figures show how the completion distribution
  changes.
- Completion CDF/PDF/tail, all-generated deadline-censored CDF/PDF, and
  miss-burst CDF figures remain separate so survivor conditioning is explicit.
- `runtime_contract.json`, the treatment and baseline source manifests, and
  `evidence_identity.json` bind the matrix, build, raw trees, and archives.
- `preflight_pre_fix/` preserves the valid four-STR prefix and its descriptive
  plot from before the validator-contract repair.  The partial preflight
  manifest is explicitly named `experiment_manifest_preflight_pre_fix.json`.

The approximately 181 MB compressed formal raw archives remain under ignored
`results/scenario15_wmm_realism_bg150_matrix_v1/archives`; their hashes and
sizes are recorded in `evidence_identity.json` rather than adding them to Git.

## Evidence boundary

This is an opened-data load-sensitivity screen with ten paired seeds, not a
reserved-seed final qualification.  The primary-copy reconstruction cannot
replace a factual no-redundancy treatment.  Completed-frame P99 is secondary
and survivor-conditioned; all-generated deadline-censored latency is included
alongside it.  The predictors were not retrained for the WMM or load changes.
