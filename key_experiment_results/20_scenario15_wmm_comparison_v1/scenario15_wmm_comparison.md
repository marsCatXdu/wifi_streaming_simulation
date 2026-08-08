# Scenario-15 WMM comparison

This is opened-seed engineering evidence on seeds 1251 through 1298. WMM off means historical CS0/TID 0/AC_BE streaming; WMM on means CS5/TID 5/AC_VI streaming with standard EDCA defaults.

| WMM | Approach | Misses | Miss rate | Mean per-run P99 | Sender airtime | Background | Actions |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| off | STR MLO | 691 | 0.7998% | 18.875 ms | 4.224 s/run | 26.669 Mbps | 0 |
| off | Score-aware T2 V2 | 496 | 0.5741% | 17.191 ms | 4.739 s/run | 26.667 Mbps | 4,944 |
| off | Distributional shadow T2 | 457 | 0.5289% | 16.836 ms | 4.927 s/run | 26.667 Mbps | 8,336 |
| on | STR MLO | 2 | 0.0023% | 6.070 ms | 3.942 s/run | 26.668 Mbps | 0 |
| on | Score-aware T2 V2 | 0 | 0.0000% | 8.290 ms | 4.069 s/run | 26.668 Mbps | 5,377 |
| on | Distributional shadow T2 | 0 | 0.0000% | 8.284 ms | 4.209 s/run | 26.668 Mbps | 8,209 |

## Paired WMM effect (on minus off)

- STR MLO: miss delta -0.7975 pp [-0.9861, -0.6204], P99 delta -12.805 ms [-14.157, -11.438], airtime ratio 0.9331 [0.9126, 0.9552].
- Score-aware T2 V2: miss delta -0.5741 pp [-0.7384, -0.4213], P99 delta -8.900 ms [-10.368, -7.450], airtime ratio 0.8586 [0.8323, 0.8888].
- Distributional shadow T2: miss delta -0.5289 pp [-0.6794, -0.3866], P99 delta -8.551 ms [-10.146, -6.953], airtime ratio 0.8543 [0.8298, 0.8821].

## Within-mode comparisons

### WMM off

- `score_aware_t2_v2_minus_str_mlo`: miss delta -0.2257 pp [-0.3171, -0.1377]; P99 delta -1.684 ms [-2.627, -0.794].
- `distributional_shadow_t2_minus_str_mlo`: miss delta -0.2708 pp [-0.3912, -0.1562]; P99 delta -2.039 ms [-3.029, -1.090].
- `distributional_shadow_t2_minus_score_aware_t2_v2`: miss delta -0.0451 pp [-0.1134, +0.0255]; P99 delta -0.355 ms [-0.675, -0.030].

### WMM on

- `score_aware_t2_v2_minus_str_mlo`: miss delta -0.0023 pp [-0.0058, +0.0000]; P99 delta +2.221 ms [+1.940, +2.518].
- `distributional_shadow_t2_minus_str_mlo`: miss delta -0.0023 pp [-0.0058, +0.0000]; P99 delta +2.214 ms [+1.937, +2.512].
- `distributional_shadow_t2_minus_score_aware_t2_v2`: miss delta +0.0000 pp [+0.0000, +0.0000]; P99 delta -0.006 ms [-0.020, +0.006].

## Evidence boundary

All 288 runs passed strict validation. Reserved seeds 1301 through 1348 were not used.
