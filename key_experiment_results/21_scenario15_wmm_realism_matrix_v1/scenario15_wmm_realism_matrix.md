# Scenario-15 WMM realism matrix

This is a ten-seed opened-data screen. The primary outcome is deadline misses over all generated frames; completed-frame P99 remains secondary.

| Profile | Approach | Misses | Miss rate | Mean per-run P99 | Sender airtime | OBSS goodput | Actions |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Target BE; competitors BE | STR MLO | 116 | 0.6444% | 18.952 ms | 4.168 s/run | 26.839 Mbps | 0 |
| Target BE; competitors BE | Score-aware T2 V2 | 97 | 0.5389% | 17.167 ms | 4.770 s/run | 26.836 Mbps | 1,172 |
| Target BE; competitors BE | Distributional shadow T2 | 95 | 0.5278% | 16.721 ms | 4.920 s/run | 26.837 Mbps | 1,791 |
| Target AF41/VI; competitors BE | STR MLO | 0 | 0.0000% | 6.028 ms | 3.925 s/run | 26.839 Mbps | 0 |
| Target AF41/VI; competitors BE | Score-aware T2 V2 | 0 | 0.0000% | 8.554 ms | 4.089 s/run | 26.838 Mbps | 1,189 |
| Target AF41/VI; competitors BE | Distributional shadow T2 | 0 | 0.0000% | 8.545 ms | 4.221 s/run | 26.839 Mbps | 1,741 |
| Target AF41/VI; one VI competitor/channel | STR MLO | 0 | 0.0000% | 6.122 ms | 3.936 s/run | 26.840 Mbps | 0 |
| Target AF41/VI; one VI competitor/channel | Score-aware T2 V2 | 0 | 0.0000% | 8.426 ms | 4.113 s/run | 26.839 Mbps | 1,257 |
| Target AF41/VI; one VI competitor/channel | Distributional shadow T2 | 0 | 0.0000% | 8.358 ms | 4.231 s/run | 26.839 Mbps | 1,751 |
| Target AF41/VI; all competitors VI | STR MLO | 33 | 0.1833% | 11.481 ms | 4.087 s/run | 26.059 Mbps | 0 |
| Target AF41/VI; all competitors VI | Score-aware T2 V2 | 4 | 0.0222% | 11.325 ms | 4.454 s/run | 26.260 Mbps | 1,554 |
| Target AF41/VI; all competitors VI | Distributional shadow T2 | 4 | 0.0222% | 11.543 ms | 4.549 s/run | 26.364 Mbps | 1,867 |

## Selective approaches compared with STR

### Target BE; competitors BE

- `score_aware_t2_v2_minus_str_mlo`: miss delta -0.1056 pp [-0.2500, +0.0500]; P99 delta -1.785 ms [-4.123, +0.174]; airtime ratio 1.1443 [1.0525, 1.2209].
- `distributional_shadow_t2_minus_str_mlo`: miss delta -0.1167 pp [-0.3111, +0.1000]; P99 delta -2.231 ms [-4.616, -0.117]; airtime ratio 1.1804 [1.1172, 1.2329].

### Target AF41/VI; competitors BE

- `score_aware_t2_v2_minus_str_mlo`: miss delta +0.0000 pp [+0.0000, +0.0000]; P99 delta +2.526 ms [+1.873, +3.224]; airtime ratio 1.0419 [0.9930, 1.0850].
- `distributional_shadow_t2_minus_str_mlo`: miss delta +0.0000 pp [+0.0000, +0.0000]; P99 delta +2.517 ms [+1.866, +3.211]; airtime ratio 1.0754 [1.0402, 1.1056].

### Target AF41/VI; one VI competitor/channel

- `score_aware_t2_v2_minus_str_mlo`: miss delta +0.0000 pp [+0.0000, +0.0000]; P99 delta +2.304 ms [+1.779, +2.814]; airtime ratio 1.0448 [0.9957, 1.0882].
- `distributional_shadow_t2_minus_str_mlo`: miss delta +0.0000 pp [+0.0000, +0.0000]; P99 delta +2.237 ms [+1.724, +2.749]; airtime ratio 1.0750 [1.0390, 1.1054].

### Target AF41/VI; all competitors VI

- `score_aware_t2_v2_minus_str_mlo`: miss delta -0.1611 pp [-0.2556, -0.0778]; P99 delta -0.156 ms [-1.259, +1.012]; airtime ratio 1.0896 [1.0554, 1.1191].
- `distributional_shadow_t2_minus_str_mlo`: miss delta -0.1611 pp [-0.2556, -0.0778]; P99 delta +0.061 ms [-1.229, +1.384]; airtime ratio 1.1130 [1.0955, 1.1291].

## Evidence boundary

All 120 runs passed strict validation. Seeds 1301 through 1348 were not used. Ten paired seeds make this directional evidence, not final qualification.
