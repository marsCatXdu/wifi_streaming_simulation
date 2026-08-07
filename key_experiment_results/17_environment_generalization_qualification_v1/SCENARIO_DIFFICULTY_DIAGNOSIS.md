# Why the generalized scenarios are much harder than neutral

**Evidence role:** post-campaign mechanism and operating-regime diagnosis.
This note does not change the frozen 576-run estimands, confidence intervals,
or qualification decision.  It explains why absolute performance in the
generalization campaign, and especially in a later five-scenario mechanism
panel drawn from it, is much worse than in the earlier neutral mixed-4x4
environment.

## Main conclusion

The degradation is primarily an environment-selection and queueing-capacity
effect, not a regression in the later mechanism implementation.

The earlier engineering result used one moderate operating point.  The
generalization campaign deliberately randomized propagation, competing
traffic, legacy coexistence, and video workload over ranges that cross the
deadline-feasibility boundary.  Several resulting scenarios operate beyond
the queueing knee: target MPDU service times approach or exceed the 33.333 ms
frame deadline, work from one burst remains when the next burst arrives, and
deadline misses grow nonlinearly into long collapse episodes.

The later T2-repair mechanism panel is even less representative of a typical
operating point.  Its pre-result contract selected five already-opened,
nontrivial cases specifically to separate mechanism behavior.  It must be
treated as a stress/mechanism panel, not as a new estimate of average
generalization performance.

## Neutral, full population, and selected-panel difficulty

The neutral mixed-4x4 experiments used a 10 m target distance, path-loss
exponent 3, 25% OBSS on-duty fraction, and no additional legacy-background
profile.  STR MLO recorded approximately 0.76% to 0.80% deadline misses in
the earlier neutral campaigns.

Across the complete held-out generalization population:

| Population | STR all-generated deadline-miss rate |
| --- | ---: |
| Earlier neutral mixed-4x4 | approximately 0.80% |
| All 48 held-out scenarios, equal scenario weight | 16.0443% mean |
| All 48 held-out scenarios | 2.8403% median |
| Later selected five-scenario stress panel | 31.7556% mean |

The large mean-to-median difference in the 48-scenario campaign shows that
the population is strongly heavy-tailed: many scenarios remain easy, while a
small number cross into severe collapse.  The five cases later selected for
the mechanism panel rank 6th, 8th, 9th, 11th, and 15th from worst among the
48 STR scenario miss rates.  Four are among the worst eleven.  Their mean is
nearly twice the complete population mean and roughly forty times the neutral
STR result.

The suffixes `p17` and `p19` are parameter-sample identifiers in the frozen
scenario catalog.  They are not percentiles and do not denote 17% or 19%
loads.

## Baseline-reproduction check

The later mechanism campaign reused the exact five scenario configurations
and four frozen seeds per scenario.  For all 20 STR scenario/seed units, its
deadline-miss count, deadline-miss rate, and target-sender airtime exactly
match the corresponding archived rows from this 576-run campaign.

This exact deterministic agreement rules out the new mechanism controller or
its analyzer as the cause of the poor STR baseline.  The difficult outcomes
were already present in the held-out qualification data.

## Direct operating-regime telemetry

The later mechanism campaign added the link telemetry needed to diagnose the
same 20 scenario/seed units.  The following values are means over STR runs;
link order is 2.4 GHz / 5 GHz.  The final column shows the later privileged
deadline-repair result only as a mechanism diagnostic.  It was not an arm of
the 576-run campaign and is not implementable.

| Operating point | Mean MPDU service | PHY non-idle | STR misses | Privileged repair misses |
| --- | ---: | ---: | ---: | ---: |
| Neutral mixed-4x4 | 2.8 / 2.7 ms | 33.9% / 31.8% | 0.80% | not run |
| Compound p19 | 4.9 / 7.2 ms | 62.3% / 69.7% | 8.94% | 1.92% |
| Radio p17 | 6.5 / 12.4 ms | 32.2% / 30.1% | 18.85% | 2.75% |
| OBSS-intensity p19 | 15.1 / 15.7 ms | 70.9% / 72.2% | 28.65% | 16.90% |
| Legacy-coexistence p17 | 13.5 / 17.7 ms | 74.9% / 80.2% | 30.24% | 16.47% |
| OBSS-intensity p17 | 68.2 / 85.3 ms | 87.9% / 90.3% | 72.10% | 50.04% |

Across the selected five cases, mean MPDU service rises to 21.6 / 27.7 ms
from 2.8 / 2.7 ms in neutral.  Mean per-run MPDU-service P95 rises from
7.2 / 6.9 ms to 91.2 / 110.3 ms, far beyond the 33.333 ms frame deadline.
OBSS p17 is the clearest capacity collapse: service P95 reaches
287.4 / 326.1 ms and the privileged secondary repair action completes for
only 11.6% of acted frames.

Target-sender airtime alone must not be read as available capacity in these
runs.  It counts target PHY transmission time, not time spent waiting under
CCA or time consumed by competing transmitters.  A starved target can
therefore record modest sender airtime while its queues grow on a medium that
is non-idle approximately 90% of the time.

## Scenario-specific causes

### OBSS-intensity p17

The configured OBSS on-duty fraction rises from 25% in neutral to 54.1%, or
2.17 times the neutral fraction.  Uplink rates rise from 0.5--3.0 Mbit/s to
1.14--5.7591 Mbit/s, and downlink rates also increase.  Measured background
throughput rises from approximately 26.7 to 72.6 Mbit/s.  Both target links
are close to continuously occupied, and extra redundancy competes for scarce
service rather than exploiting idle diversity.

This also explains the unusual ordering in which 5 GHz alone beats STR in
this case.  Packets sent by native STR on the especially congested 2.4 GHz
link can become whole-frame stragglers.  Complete duplication still reduces
misses, but only from 72.10% to approximately 52.6%, because the secondary
copy is itself usually unable to finish.

### OBSS-intensity p19

The on-duty fraction is 36.5%, or 1.46 times neutral, with higher peak rates
and smaller packets.  The medium is non-idle for approximately 71% to 72% of
the measurement interval and MPDU-service P95 is approximately 71--76 ms.
This is a less extreme version of the same service-capacity failure.

### Legacy-coexistence p17

The scenario retains the mixed-4x4 OBSS environment and adds the
`legacy_mixed8` background profile: eight mixed-generation stations per link
with a 47.9% on-duty fraction and 2.9549 Mbit/s configured rate.  The mixture
of 802.11n/ac/ax/be stations adds contention and protocol-efficiency costs.
Measured background throughput reaches 49.4 Mbit/s, and target MPDU-service
P95 reaches approximately 59 / 78 ms.

### Compound-shift p19

This case combines `legacy_mixed8`, OBSS traffic, changed propagation, and a
video workload whose GOP-averaged frame bytes are approximately 5.2% above
neutral.  It is close to, but not as far beyond, the queueing knee.  This is
why privileged repair and full copy can still reduce misses to approximately
2% even though STR records 8.94%.

### Radio-propagation p17

The target distance changes from 10 m to 16.05 m and the path-loss exponent
from 3 to 3.7489.  Under the configured log-distance model, the distance term
adds approximately 15.2 dB of path loss relative to neutral.  Target traffic
continues to use fixed `EhtMcs5`, so the link responds through retransmissions
rather than rate adaptation.  The 5 GHz link records approximately 2.78
retransmissions per successful MPDU, versus approximately 0.40 in neutral.

PHY occupancy remains moderate, distinguishing this as a link-quality and
diversity problem rather than a shared-medium capacity collapse.  Full copy
or privileged repair reduces misses from 18.85% to approximately 2.8%, which
confirms that useful path diversity remains present.

## Why the deadline behavior becomes nonlinear

The synthetic stream emits an entire frame as a burst every 33.333 ms.  A
normal P-frame contains ten source packets and an I-frame contains forty.
Frame completion requires every source packet to arrive by the deadline, so
one packet delayed behind contention makes the complete frame miss.

The MAC queue maximum delay is 500 ms, about fifteen frame periods, and the
current sender has no frame-deadline-aware purge.  Once service falls behind,
packets belonging to frames that can no longer meet their deadline may remain
queued and consume later transmission opportunities:

```text
contention or weak link
    -> service exceeds one frame period
    -> the next burst arrives before prior work clears
    -> expired or doomed work delays later viable frames
    -> clustered deadline misses and eventual collapse
```

This amplification explains why modest parameter changes can move the result
from below 1% misses to tens of percent.  Every miss in all three 576-run arms
is an incomplete-at-deadline outcome; survivor-only latency CDF/PDF and P99
cannot describe this loss of service feasibility.

## Why the proposed policies reverse against STR

The proposed architecture uses 5 GHz as its primary application path and
adds selective 2.4 GHz copies.  In neutral, the reconstructed primary-copy
miss rate in the successful V2 engineering campaign was approximately 1.43%
versus 0.80% for STR.  Selective actions could bridge that small starting gap
and then improve on STR within the 1.20 airtime target.

In the selected stress panel, 5 GHz alone records 41.05% misses versus 31.76%
for STR, a 9.29 percentage-point deficit before the selective policy can
produce any net advantage.  Native STR continuously uses both links, while a
fixed-primary selective architecture must spend substantial extra airtime to
recover frames lost by its primary-only starting point.

The action comparisons confirm the distinction between prediction failure
and physical/action limitations:

- full T0/T2 copies reduce aggregate misses to approximately 18.8%--19.0%,
  proving that diversity remains useful, but consume about 2.09 times STR
  sender airtime;
- privileged T2 deadline repair reduces misses to 17.62% but consumes 1.572
  times STR sender airtime;
- an optimistic post-result subset cannot beat STR at the 1.20 limit;
- 12.5% ideal FEC remains near the primary-only result because these failures
  frequently involve large queue deficits or whole-frame starvation, not one
  or two independent packet erasures.

A larger predictor cannot repair a secondary path that lacks enough timely
service capacity.  Prediction and admission errors still matter in moderate
regimes, but they are not the dominant cause of the collapse cases.

## Interpretation and future evaluation boundary

The evidence supports two operating regimes:

1. **Diversity-limited regimes**, such as radio p17 and compound p19.  A
   multipath action can recover most misses, but its airtime efficiency must
   improve.
2. **Capacity-limited regimes**, especially OBSS p17/p19 and legacy p17.
   Extra redundancy can deepen congestion; deadline-aware work conservation
   and a no-harm fallback are more relevant than a larger risk model.

The five-scenario mechanism aggregate should therefore always be labeled as
an opened-data stress panel.  Generalization claims should continue to use
the complete frozen 48-scenario population and should report its median,
heavy tail, family results, and scenario-level differences against STR.
Future figures would benefit from a neutral reference and a regime indicator
such as MPDU-service-P95/deadline or PHY non-idle fraction.

Potential later architectural directions, requiring a separate reviewed
iteration, are:

- use native STR or packet striping as the no-harm base rather than a fixed
  5 GHz primary;
- choose the primary/link allocation from current environment state;
- detect unfamiliar or saturated telemetry and revert conservatively;
- abandon remaining work for frames that cannot meet the deadline while
  retaining those frames as misses in the all-generated denominator;
- test equal-airtime coded striping or repair before training another causal
  selector.

No new policy is promoted by this diagnosis.

## Evidence locations

- Frozen complete-campaign scenario rows:
  `analysis/scenario_metrics.csv`.
- Frozen complete-campaign reliability report:
  `analysis/complete_reliability_report.md`.
- Frozen scenario catalog:
  `../../experiments/model-selection/environment-generalization-scenarios-v1.json`.
- Later five-scenario selection contract:
  `../../experiments/model-selection/t2-repair-mechanism-v1.json`.
- Later mechanism telemetry and resource result:
  `../18_t2_repair_mechanism_v1/deadline_oracle_v2/`.
- Earlier neutral V2 engineering reference:
  `../07_score_aware_t2_str_engineering_v2/`.
