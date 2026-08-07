# Pre-recovery observations

This checkpoint contains 479 freshly strict-validated adaptive runs.  The
descriptive comparison uses the 159 scenario/seed units for which all three
adaptive arms were already promoted, together with the matching fixed-MCS
units.  It excludes compound-shift p22 seed 21188 because its adaptive STR arm
is the single missing run.  Coverage is incomplete and uneven, so this is not
the formal 576-run estimate.

Adaptive MCS is unfavorable in this snapshot:

- STR MLO misses rise from 12.2646% to 14.2309%, a 1.9663 percentage-point or
  16.0% relative increase, while mean sender airtime rises by 7.70%.
- V2 misses rise from 14.2531% to 18.4566%, a 4.2035 percentage-point or 29.5%
  relative increase, while mean sender airtime rises by 5.40%.
- Distributional misses rise from 13.9411% to 17.7986%, a 3.8576
  percentage-point or 27.7% relative increase, while mean sender airtime rises
  by 6.55%.
- Under adaptive MCS, V2 and Distributional remain worse than adaptive STR by
  4.2257 and 3.5677 percentage points, respectively.

The all-generated deadline-censored CDF agrees with the miss counts: adaptive
MCS puts more probability in the tail for the selective arms.  The
completed-frame CDF sometimes shifts slightly left, but this is
survivor-conditioned while adaptive MCS loses more frames.  It therefore
cannot be interpreted as a latency improvement.

The current evidence suggests that an unretuned Minstrel treatment does not
repair the generalized collapse.  It consumes more sender airtime and
interacts more adversely with the frozen selective policies than with STR.
Possible causes such as low-rate residence, queue amplification, or the
fixed-EhtMcs5 admission reservation remain hypotheses until the full campaign
and telemetry are examined; no mechanism claim is made from this partial
snapshot.
