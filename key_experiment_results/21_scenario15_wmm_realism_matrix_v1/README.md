# Scenario-15 WMM realism matrix

This archive answers whether the favorable scenario-15 WMM result survives
when competing traffic uses the same access category as the target stream. It
keeps the fixed-MCS scenario, workload, policies, models, seeds, and resource
settings unchanged and varies only the target and OBSS WMM mappings.

The screen contains four profiles, three arms, and ten identical opened seeds
per cell: 120 simulations and 216,000 generated frames in total. All runs
completed and passed fresh strict validation. No replacement seed and no seed
from the reserved 1301 through 1348 range was used.

## Result

Each cell contains ten runs and 18,000 generated frames.

| Target / competitors | Approach | Misses | Miss rate | Mean per-run P99 | Sender airtime | OBSS goodput | Actions |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| BE / BE | STR MLO | 116 | 0.6444% | 18.952 ms | 4.168 s/run | 26.839 Mbps | 0 |
| BE / BE | Score-aware T2 V2 | 97 | 0.5389% | 17.167 ms | 4.770 s/run | 26.836 Mbps | 1,172 |
| BE / BE | Distributional shadow T2 | 95 | 0.5278% | 16.721 ms | 4.920 s/run | 26.837 Mbps | 1,791 |
| AF41/VI / BE | STR MLO | 0 | 0.0000% | 6.028 ms | 3.925 s/run | 26.839 Mbps | 0 |
| AF41/VI / BE | Score-aware T2 V2 | 0 | 0.0000% | 8.554 ms | 4.089 s/run | 26.838 Mbps | 1,189 |
| AF41/VI / BE | Distributional shadow T2 | 0 | 0.0000% | 8.545 ms | 4.221 s/run | 26.839 Mbps | 1,741 |
| AF41/VI / one VI per channel | STR MLO | 0 | 0.0000% | 6.122 ms | 3.936 s/run | 26.840 Mbps | 0 |
| AF41/VI / one VI per channel | Score-aware T2 V2 | 0 | 0.0000% | 8.426 ms | 4.113 s/run | 26.839 Mbps | 1,257 |
| AF41/VI / one VI per channel | Distributional shadow T2 | 0 | 0.0000% | 8.358 ms | 4.231 s/run | 26.839 Mbps | 1,751 |
| AF41/VI / all VI | STR MLO | 33 | 0.1833% | 11.481 ms | 4.087 s/run | 26.059 Mbps | 0 |
| AF41/VI / all VI | Score-aware T2 V2 | 4 | 0.0222% | 11.325 ms | 4.454 s/run | 26.260 Mbps | 1,554 |
| AF41/VI / all VI | Distributional shadow T2 | 4 | 0.0222% | 11.543 ms | 4.549 s/run | 26.364 Mbps | 1,867 |

## Main interpretation

One VI competitor per channel is not enough to challenge the target's WMM
advantage. It produces zero misses for every arm, just like the favorable
AF41/VI-target versus BE-competitor case. The paired changes in sender airtime
are at most 0.58%, and no tracked OBSS-throughput harm appears.

Making all latency-sensitive competitors VI removes that access-category
isolation. In this case V2 and Distributional each reduce misses from 33 to 4,
an 87.88% relative reduction. Their paired miss-rate delta against STR is
-0.1611 percentage points with a 95% interval of [-0.2556, -0.0778]. V2 is
better on eight seeds and tied on two; it is worse on none.

V2 pays 8.96% extra sender airtime, with a paired 95% ratio interval of
[1.0554, 1.1191]. Distributional pays 11.30%, interval [1.0955, 1.1291].
Both therefore satisfy the desired greater-than-50% miss reduction and
less-than-20% extra-airtime targets in this preliminary stress case.

This is not a complete defeat of STR under the project's definition. V2's
P99 delta is only -0.156 ms, with interval [-1.259, +1.012] ms;
Distributional's is +0.061 ms, with interval [-1.229, +1.384] ms. The result
supports equivalent, not obviously lower, P99. The all-generated
deadline-censored mean is also indistinguishable.

Distributional provides no reliability gain over V2 in the all-VI case and
uses 2.15% more sender airtime, with interval [0.84%, 3.88%]. V2 is therefore
the better engineering candidate here.

## Startup diagnostic

The four residual misses are exactly the same frames under V2 and
Distributional: seed 1251 frame 5, seed 1257 frame 3, and seed 1258 frames 2
and 4. Every one is among the first eight frames and every one has
`duplicated=0`. In contrast, only three of STR's 33 misses are startup frames.
Thus the selective policies eliminate every observed steady-state miss in
this ten-seed all-VI screen; the observed residual is the already-known
no-history startup gate. `residual_selective_misses.csv` preserves the exact
frame evidence.

A non-temporal startup fallback or an explicitly symmetric pre-roll is the
smallest plausible next experiment. That is a hypothesis, not a result, and
should be discussed before another campaign.

## Evidence and figures

- `scenario15_wmm_realism_matrix.json` is the authoritative statistical
  report; its Markdown companion is the compact generated summary.
- `run_metrics.csv` and `paired_comparisons.csv` preserve run-level outcomes
  and all paired intervals.
- `plot_data.json` is the compact all-run input for the 13 PNG/PDF figure
  pairs under `figures/`.
- `figures/deadline_miss.png`, `figures/completed_p99.png`,
  `figures/sender_airtime.png`, and `figures/background_throughput.png` show
  the headline estimates.
- `figures/vi_competitor_effect.png` isolates the consequence of adding one or
  all VI competitors while the target remains VI.
- The completion CDF/PDF/tail and all-generated deadline-censored CDF/PDF are
  archived separately so survivor conditioning remains explicit.
- `runtime_contract.json`, the preflight and formal source manifests, and
  `evidence_identity.json` bind the matrix, exact build, raw trees, and raw
  archives.

The approximately 138 MiB compressed raw archives remain under ignored
`results/scenario15_wmm_realism_matrix_v1/archives`; their hashes and sizes are
recorded in `evidence_identity.json` rather than adding them to Git history.

## Evidence boundary

This is an opened-data mechanism screen with only ten paired seeds. Its paired
direction is strong in the all-VI case, but it is not a reserved-seed final
qualification. Completed-frame P99 is secondary and survivor-conditioned.
The frozen predictors were trained under the historical BE target and BE
competitor setting and were not retrained for these treatments.
