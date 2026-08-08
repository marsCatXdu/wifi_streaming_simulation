# Pre-fix valid STR preflight prefix

Four STR cells promoted and freshly pass strict validation. The eight selective-policy simulations are excluded because the legacy validator rejected their changed background-rate projection. This one-seed prefix is preservation evidence, not a performance estimate.

| Profile | Misses 1.0x -> 1.5x | P99 1.0x -> 1.5x | Sender airtime 1.0x -> 1.5x | OBSS goodput 1.0x -> 1.5x |
| --- | ---: | ---: | ---: | ---: |
| Target BE / competitors BE | 23 -> 150 | 23.461 -> 30.655 ms | 4.685 -> 5.759 s | 26.855 -> 40.470 Mbps |
| Target VI / competitors BE | 0 -> 1 | 6.875 -> 7.896 ms | 4.146 -> 4.356 s | 26.855 -> 40.475 Mbps |
| Target VI / one VI per channel | 0 -> 1 | 6.861 -> 7.966 ms | 4.120 -> 4.380 s | 26.856 -> 40.475 Mbps |
| Target VI / all competitors VI | 6 -> 6 | 13.220 -> 11.023 ms | 4.333 -> 4.265 s | 25.630 -> 29.116 Mbps |

Do not infer a load effect from one seed or compare selective policies until the contract-specific validator passes.
