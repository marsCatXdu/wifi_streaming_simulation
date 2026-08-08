# Scenario-15 WMM realism matrix at 1.5x background offered load

All cells use the same opened seeds and configuration as the 1.0x matrix, except that every OBSS ON-period rate is exactly 1.5 times larger.

| Profile | Approach | Misses | Miss rate | Mean per-run P99 | Sender airtime | OBSS goodput | Actions |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Target BE; competitors BE | STR MLO | 902 | 5.0111% | 27.171 ms | 4.765 s/run | 40.438 Mbps | 0 |
| Target BE; competitors BE | Score-aware T2 V2 | 1,359 | 7.5500% | 27.111 ms | 5.943 s/run | 40.438 Mbps | 1,659 |
| Target BE; competitors BE | Distributional shadow T2 | 1,333 | 7.4056% | 27.254 ms | 6.054 s/run | 40.439 Mbps | 1,870 |
| Target AF41/VI; competitors BE | STR MLO | 2 | 0.0111% | 7.663 ms | 4.132 s/run | 40.441 Mbps | 0 |
| Target AF41/VI; competitors BE | Score-aware T2 V2 | 2 | 0.0111% | 9.926 ms | 4.551 s/run | 40.440 Mbps | 1,982 |
| Target AF41/VI; competitors BE | Distributional shadow T2 | 3 | 0.0167% | 10.004 ms | 4.527 s/run | 40.439 Mbps | 1,870 |
| Target AF41/VI; one VI competitor/channel | STR MLO | 2 | 0.0111% | 7.810 ms | 4.128 s/run | 40.441 Mbps | 0 |
| Target AF41/VI; one VI competitor/channel | Score-aware T2 V2 | 2 | 0.0111% | 9.752 ms | 4.562 s/run | 40.437 Mbps | 1,939 |
| Target AF41/VI; one VI competitor/channel | Distributional shadow T2 | 2 | 0.0111% | 9.780 ms | 4.548 s/run | 40.437 Mbps | 1,870 |
| Target AF41/VI; all competitors VI | STR MLO | 212 | 1.1778% | 20.251 ms | 4.380 s/run | 38.468 Mbps | 0 |
| Target AF41/VI; all competitors VI | Score-aware T2 V2 | 53 | 0.2944% | 18.063 ms | 4.774 s/run | 35.645 Mbps | 1,617 |
| Target AF41/VI; all competitors VI | Distributional shadow T2 | 46 | 0.2556% | 16.961 ms | 4.850 s/run | 35.180 Mbps | 1,855 |

## Selective primary-copy and rescue decomposition

The primary-copy reconstruction is factual under each selective topology, including its concurrent repair traffic. It is not a counterfactual no-redundancy baseline.

| Profile | Approach | Primary-copy misses | Acted primary misses | Rescued | Rescue efficiency | Final misses | STR misses |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Target BE; competitors BE | Score-aware T2 V2 | 1,860 | 526 | 501 | 95.25% | 1,359 | 902 |
| Target BE; competitors BE | Distributional shadow T2 | 1,859 | 562 | 526 | 93.59% | 1,333 | 902 |
| Target AF41/VI; competitors BE | Score-aware T2 V2 | 5 | 3 | 3 | 100.00% | 2 | 2 |
| Target AF41/VI; competitors BE | Distributional shadow T2 | 5 | 2 | 2 | 100.00% | 3 | 2 |
| Target AF41/VI; one VI competitor/channel | Score-aware T2 V2 | 2 | 0 | 0 | n/a | 2 | 2 |
| Target AF41/VI; one VI competitor/channel | Distributional shadow T2 | 2 | 0 | 0 | n/a | 2 | 2 |
| Target AF41/VI; all competitors VI | Score-aware T2 V2 | 69 | 17 | 16 | 94.12% | 53 | 212 |
| Target AF41/VI; all competitors VI | Distributional shadow T2 | 69 | 24 | 23 | 95.83% | 46 | 212 |

## 1.5x minus 1.0x paired effects

### Target BE; competitors BE

- STR MLO: miss delta +4.3667 pp [+2.9278, +5.8222]; P99 delta +8.219 ms [+7.077, +9.390]; airtime ratio 1.1431 [1.1108, 1.1759].
- Score-aware T2 V2: miss delta +7.0111 pp [+3.9111, +10.0111]; P99 delta +9.945 ms [+9.214, +10.744]; airtime ratio 1.2459 [1.2154, 1.2711].
- Distributional shadow T2: miss delta +6.8778 pp [+3.7944, +9.8611]; P99 delta +10.533 ms [+8.860, +12.226]; airtime ratio 1.2305 [1.1884, 1.2658].

### Target AF41/VI; competitors BE

- STR MLO: miss delta +0.0111 pp [+0.0000, +0.0278]; P99 delta +1.636 ms [+1.397, +1.876]; airtime ratio 1.0529 [1.0472, 1.0589].
- Score-aware T2 V2: miss delta +0.0111 pp [+0.0000, +0.0278]; P99 delta +1.372 ms [+0.813, +1.999]; airtime ratio 1.1129 [1.1001, 1.1278].
- Distributional shadow T2: miss delta +0.0167 pp [+0.0000, +0.0444]; P99 delta +1.459 ms [+0.903, +2.031]; airtime ratio 1.0725 [1.0645, 1.0813].

### Target AF41/VI; one VI competitor/channel

- STR MLO: miss delta +0.0111 pp [+0.0000, +0.0278]; P99 delta +1.688 ms [+1.295, +2.104]; airtime ratio 1.0487 [1.0421, 1.0552].
- Score-aware T2 V2: miss delta +0.0111 pp [+0.0000, +0.0278]; P99 delta +1.325 ms [+0.829, +1.768]; airtime ratio 1.1091 [1.0982, 1.1225].
- Distributional shadow T2: miss delta +0.0111 pp [+0.0000, +0.0278]; P99 delta +1.421 ms [+0.904, +1.868]; airtime ratio 1.0749 [1.0686, 1.0830].

### Target AF41/VI; all competitors VI

- STR MLO: miss delta +0.9944 pp [+0.6333, +1.3889]; P99 delta +8.770 ms [+5.896, +10.960]; airtime ratio 1.0717 [1.0479, 1.0892].
- Score-aware T2 V2: miss delta +0.2722 pp [+0.1722, +0.3944]; P99 delta +6.738 ms [+4.472, +8.626]; airtime ratio 1.0719 [1.0286, 1.1063].
- Distributional shadow T2: miss delta +0.2333 pp [+0.1278, +0.3556]; P99 delta +5.418 ms [+3.183, +7.287]; airtime ratio 1.0661 [1.0374, 1.0886].

## Evidence boundary

All 120 new runs and all 120 retained baseline runs passed fresh strict validation. Every generated background rate period matched the baseline timing and was exactly 1.5 times its baseline rate within serialization tolerance. Seeds 1301 through 1348 were not used.
