# Scenario-15 WMM comparison

This archive is the complete opened-seed comparison requested for the earlier
neutral mixed-4x4 environment used by
`15_distributional_shadow_t2_str_engineering_v1`.  It compares STR MLO,
score-aware T2 V2, and distributional-shadow T2 on identical seeds with target
video WMM prioritization off and on.  All 288 simulations completed and passed
fresh strict validation; no replacement seeds or post-outcome reruns were
used.

Here, `WMM off` is the historical target stream mapping CS0 / TID 0 to AC_BE.
`WMM on` maps only the target stream's CS5 / TID 5 to AC_VI and uses ns-3's
standard EDCA defaults.  Background and OBSS traffic remain CS0 / AC_BE.
This is not a literal removal of the QoS/WMM machinery, which EHT requires; it
is a controlled video access-category treatment.

## Result

Each cell contains 48 runs and 86,400 generated frames.

| WMM | Approach | Misses | Miss rate | Mean per-run P99 | Sender airtime | Background | Actions |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| off | STR MLO | 691 | 0.7998% | 18.875 ms | 4.224 s/run | 26.669 Mbps | 0 |
| off | Score-aware T2 V2 | 496 | 0.5741% | 17.191 ms | 4.739 s/run | 26.667 Mbps | 4,944 |
| off | Distributional shadow T2 | 457 | 0.5289% | 16.836 ms | 4.927 s/run | 26.667 Mbps | 8,336 |
| on | STR MLO | 2 | 0.0023% | 6.070 ms | 3.942 s/run | 26.668 Mbps | 0 |
| on | Score-aware T2 V2 | 0 | 0.0000% | 8.290 ms | 4.069 s/run | 26.668 Mbps | 5,377 |
| on | Distributional shadow T2 | 0 | 0.0000% | 8.284 ms | 4.209 s/run | 26.668 Mbps | 8,209 |

Paired WMM-on minus WMM-off effects are decisive for every arm:

- STR: miss-rate delta -0.7975 percentage points, 95% CI
  [-0.9861, -0.6204]; P99 delta -12.805 ms, CI [-14.157, -11.438].
- V2: miss-rate delta -0.5741 points, CI [-0.7384, -0.4213]; P99
  delta -8.900 ms, CI [-10.368, -7.450].
- Distributional: miss-rate delta -0.5289 points, CI
  [-0.6794, -0.3866]; P99 delta -8.551 ms, CI [-10.146, -6.953].

Within WMM-on runs, V2 and Distributional each prevent the two STR misses,
but the paired miss intervals end at zero.  They are respectively 2.221 ms
and 2.214 ms slower than STR at P99, with intervals wholly above zero, and
use 3.24% and 6.79% more sender airtime.  Distributional and V2 have identical
reliability and indistinguishable P99 under WMM on; Distributional costs 3.44%
more airtime.

## Interpretation

WMM video prioritization is the dominant mechanism in this environment.  It
reduces STR misses from 691 to 2 while also reducing sender airtime.  The
tracked background throughput remains effectively unchanged, so the gain is
not explained by measurable starvation of that traffic.

The selective policies still launch thousands of T2 full-copy actions after
WMM has removed almost all reliability headroom.  Native STR can stripe from
T0, so it has the faster WMM-on completion tail.  In this scenario the overall
engineering choice is therefore STR MLO with WMM on, not either selective
policy.  The result also means that future claims against STR must include a
properly prioritized STR baseline; otherwise the selective policy is being
compared against best-effort video traffic.

This does not establish that selective duplication is useless in harder
conditions.  It establishes that the old neutral scenario saturates near the
reliability floor once standard video access-category priority is enabled.
The next scientific decision should be discussed before starting another
campaign, as requested.

## Historical reproduction note

The current WMM-off build differs slightly from the older scenario-15 archive:
STR reproduces exactly, while V2 has one additional miss and Distributional
has two additional misses.  All three count differences occur at seed 1291.
For Distributional, frames 1763 and 1782 complete in the older build but are
incomplete in the current build; neither frame is duplicated in either build.
This is a small cross-build/runtime drift, not a WMM treatment effect.  Every
off/on and policy comparison in this archive uses the same current executable
and remains exactly seed-paired.

## Evidence and figures

- `scenario15_wmm_comparison.json` is the authoritative statistical report.
- `scenario15_wmm_comparison.md` is its compact generated summary.
- `run_metrics.csv` and `paired_comparisons.csv` retain run-level metrics and
  paired intervals.
- `plot_data.json` is the compact, all-run input for the 12 figure pairs.
- `figures/latency_cdf.png`, `figures/latency_pdf.png`, and
  `figures/latency_tail_cdf.png` show completed-frame distributions.
- `figures/all_generated_censored_latency_cdf.png` and its PDF companion keep
  every generated frame in a deadline-censored latency view.
- `figures/deadline_miss.png`, `figures/completed_p99.png`,
  `figures/sender_airtime.png`, and `figures/background_throughput.png` show
  the headline cell estimates.
- `figures/paired_wmm_effects.png` and `figures/policy_vs_str.png` show the
  formal paired contrasts.
- `figures/deadline_miss_burst_cdf.png` shows that the two WMM-on STR misses
  are isolated; V2 and Distributional have no WMM-on miss bursts.
- `runtime_contract.json`, both source manifests, and
  `evidence_identity.json` bind the exact matrix, executable, raw trees, and
  uncommitted raw archives.

The approximately 394 MB compressed raw run archives remain under ignored
`results/scenario15_wmm_comparison_v1/archives`; their hashes and sizes are in
`evidence_identity.json` rather than adding them to Git history.
