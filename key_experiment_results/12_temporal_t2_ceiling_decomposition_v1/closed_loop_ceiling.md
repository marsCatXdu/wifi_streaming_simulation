# Temporal-T2 closed-loop ceiling decomposition

Engineering diagnostic only; this is not a qualification result.

Reference campaign: Cost-free T2 V5. Matched units: 48; generated frames: 86,400.
Primary misses: 1,232; current action-candidate misses: 1,103; fixed outside-candidate misses: 129.

## Factual campaigns

| Campaign | Actions | Final misses | Miss rate | Mean P99 | Mean secondary airtime |
| --- | ---: | ---: | ---: | ---: | ---: |
| Score-aware T2 V2 | 4,944 | 495 | 0.5729% | 17.192 ms | 240.429 ms/run |
| Cost-free T2 V5 | 5,147 | 496 | 0.5741% | 17.081 ms | 248.649 ms/run |

## Canonical reservation frontier

Every evaluated candidate costs 1983.760667 us. The 0.6% refill-only budget is 360.000 ms/run (181 actions); startup credit raises the finite-run proxy to 372.000 ms/run (187 actions).

| Score | Frontier | Captured primary misses | Perfect-rescue misses | Reference-rate projected misses |
| --- | --- | ---: | ---: | ---: |
| Score-aware T2 V2 | same factual action count | 817 | 415 | 443.91 |
| Score-aware T2 V2 | per-run refill budget | 850 | 382 | 412.08 |
| Score-aware T2 V2 | per-run finite budget | 858 | 374 | 404.36 |
| Score-aware T2 V2 | pooled threshold sensitivity | 933 | 299 | 332.02 |
| Cost-free T2 V5 | same factual action count | 828 | 404 | 433.30 |
| Cost-free T2 V5 | per-run refill budget | 858 | 374 | 404.36 |
| Cost-free T2 V5 | per-run finite budget | 867 | 365 | 395.68 |
| Cost-free T2 V5 | pooled threshold sensitivity | 956 | 276 | 309.83 |
| Perfect primary information | per-run refill budget | 1,103 | 129 | 168.03 |

The target permits at most 345 misses. At 96.46% rescue efficiency it requires capturing 920 primary misses.

The Cost-free T2 V5 threshold set averages 339.636 ms/run, but 26 runs exceed the refill-only budget and 26 exceed the finite-run proxy. Its pooled projection therefore transfers unused credit across independent runs and is not implementable.

The current Cost-free T2 V5 score first captures the required 920 primary misses at a uniform cap of 236 actions/run, requiring up to 468.168 ms/run of canonical reservation.

## Identification boundary

Action outcomes are observed for 871/1103 eligible primary misses; 232 remain unobserved. Among observed frames, 18 change rescue outcome across policies.

Consequently, the primary-information oracle is an exact miss-capture frontier but not an exact secondary-outcome or P99 oracle. The next stage needs cross-fitted randomized potential-outcome distributions and an implementable online allocator.
