# Adaptive-MCS qualification observations

## Result

The adaptive treatment is a clear regression relative to the archived fixed
EhtMcs5 baseline:

| Arm | Fixed misses | Adaptive misses | Adaptive - fixed | 95% interval | Adaptive/fixed airtime |
| --- | ---: | ---: | ---: | ---: | ---: |
| STR MLO | 16.0437% | 17.7337% | +1.6901 pp | [+0.4716, +3.0163] pp | 1.0822 |
| V2 | 19.1306% | 23.1784% | +4.0478 pp | [+1.9235, +6.5725] pp | 1.0553 |
| Distributional | 18.7584% | 22.4778% | +3.7194 pp | [+1.6803, +6.1924] pp | 1.0663 |

Under adaptive MCS, V2 is 5.4447 percentage points worse than STR (95%
interval +3.5573 to +7.6245), and Distributional is 4.7440 points worse (95%
interval +3.0390 to +6.7117).  Their sender-airtime ratios against adaptive
STR are 1.0934 and 1.1345.  The target is therefore not met.

## Generality and latency interpretation

Adaptive MCS is worse than fixed MCS in 41 of 48 scenario-level STR points, 44
of 48 V2 points, and 43 of 48 Distributional points.  The largest family
penalties occur under radio propagation: +5.9653 points for STR, +11.6892 for
V2, and +10.9410 for Distributional.  V2 and Distributional also regress by
about four points in OBSS intensity and compound failures.

All deadline misses in the analyzed raw runs are incomplete frames; there are
no completed-but-late frames.  Adaptive MCS increases the pooled incomplete
rate from 15.2400% to 16.8441% for STR, 18.2346% to 22.1738% for V2, and
17.8754% to 21.4690% for Distributional.  Any slightly faster-looking portion
of the completed-frame CDF is survivor conditioning, not a reliability win.
The all-generated deadline-censored CDF shows the adverse tail directly.

Background throughput changes are statistically indistinguishable from zero.
The miss regression therefore does not come from a changed generated
environment.  Adaptive MCS instead increases sender airtime and changes the
closed-loop target-link behavior.

## Interpretation

The most plausible explanation is rate/queue feedback: Minstrel can reside at
lower rates in difficult propagation or contention states, increasing airtime
and queue residence.  This effect is amplified for selective duplication,
whose predictor was trained under fixed MCS and whose conservative admission
reservation remains derived from EhtMcs5.  Selected MCS was not logged, so
this is a supported hypothesis rather than a demonstrated mechanism.

The excluded compound-shift p22 seed-21188 unit changes the fixed baseline
only in the fourth decimal place relative to the complete archived result.
Both exact adaptive STR attempts abort at the identical simulated time on the
same native ns-3 association-manager assertion.  The analysis validates all
575 promoted adaptive runs, drops that unit from both MCS modes, and uses 191
matched units.  No seed substitution or patched executable is included.

This experiment closes only the unretuned Minstrel ablation.  It does not show
that all possible adaptive-rate designs are harmful.  Per the requested stop
boundary, no retuning, new predictor, or further experiment follows here.
