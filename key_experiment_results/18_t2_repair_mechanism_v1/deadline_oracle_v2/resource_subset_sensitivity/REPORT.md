# Post-result repair subset resource sensitivity

This is an optimistic static projection, not a simulated policy result.
It selects only factual rescues and charges their measured tagged PPDU
airtime from the full-action replay. It ignores fixed secondary-link
overhead, policy feedback, changed contention, and causal prediction.

| Budget | Selected rescues | Projected misses | Beats STR? |
| --- | ---: | ---: | --- |
| Equal airtime, per run | 608 | 14,169 | no |
| 1.20 airtime, per run | 2,440 | 12,337 | no |
| 1.20 airtime, pooled upper sensitivity | 2,950 | 11,827 | no |

STR records 11,432 misses; the primary-only
arm records 14,777. The equal-airtime
headroom is negative before any repair because primary-only already
uses more airtime than STR.

Even under pooled noncausal selection, at least 3,346 factual rescues are needed to beat STR. Their optimistic measured tagged-cost floor implies a sender-airtime ratio of 1.2051 before fixed secondary overhead or closed-loop effects.

A failure even in the pooled 1.20 sensitivity is strong negative
evidence. A pass would show only an optimistic selection ceiling and
would still require a causal, closed-loop experiment.
