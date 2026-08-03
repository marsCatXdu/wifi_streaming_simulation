Wi-Fi streaming research TODO
============================

Immediate objective
-------------------

Demonstrate that deadline-aware, budgeted multipath control produces both a
lower deadline-miss ratio and lower P99 frame-completion latency than native
STR MLO and EMLSR MLO, while paying a bounded and explicitly reported airtime
cost.  Miss-burst measurements remain supporting evidence, but a reduction in
burst length alone is not sufficient.

The ideal Pareto operating point uses less than 20 percent additional summed
target PHY TX airtime relative to the matched MLO treatment while reducing
both deadline-miss ratio and P99 latency by more than 50 percent.  Treat these
as separate per-metric ambitions rather than combining them into one score;
progress toward one target must not conceal regression in the other.

The final comparison must use paired runs.  A result counts as a defeat of an
MLO treatment only when the paired confidence intervals for both the
deadline-miss-ratio difference and the P99-latency difference lie below zero
and the improvements are practically meaningful.  Before the final campaign,
freeze a numerical limit for reasonable extra airtime and a maximum acceptable
loss of background throughput.  Report summed target PHY TX airtime, the
per-band airtime change, and background throughput alongside the two primary
outcomes.

Headline treatments and controls
--------------------------------

Keep the publication-scale comparison focused on the proposed controller,
STR MLO, and EMLSR MLO.  Retire selective duplication and single-link
transmission from the full matrix.  Retain occasional single-link and full
application-duplication runs only as small internal sanity controls.  Full
duplication is the reliability oracle that verifies useful cross-link
diversity; it is not an airtime-efficient competitor.

Use ``NMaxInflights=1`` for the current STR reference.  Existing ten-seed
results show that ``NMaxInflights=2`` is pathological rather than a stronger
reliability baseline: it misses approximately 86 percent of frames under
OBSS-only contention and 98 percent under combined contention.  Its low P99
is computed from only the small surviving set of completed frames.  Quarantine
this configuration until an instrumented reproducer identifies and repairs or
explains the shared Block Ack/in-flight recovery failure.  Do not include it
as a valid headline treatment merely because it enables opportunistic MPDU
duplication.

Current engineering evidence
----------------------------

The 30-seed ``adaptive_deficit_obss_v1`` discovery matrix established that
the whole secondary copy is the useful action.  It reduced the union miss
ratio to 0.609 percent, but used 1.94 percent secondary airtime and increased
summed target sender airtime by about 33 percent relative to STR MLO.  At the
same measured airtime, primary-deficit duplication was worse.  In particular,
partial deficit copies consumed 37.3 percent of the deficit arm's airtime but
rescued only 4 primary misses.

Interpret predictor outcomes against the pre-intervention primary copy, not
against the receiver union after an action.  In the discovery matrix, the T0
score has primary-miss ROC AUC 0.851 and average precision 0.198.  Whole-copy
actions rescued 378 of 384 selected primary misses, but 97.3 percent of their
measured airtime was spent on frames whose primary copy met the deadline.
Thus early selection, rather than conditional whole-copy rescue, is the main
bottleneck.

The 12-seed ``early_risk_obss_pilot_v1`` matrix is engineering evidence, not a
positive result.  Its T0-only fixed risk-density gate kept mean summed sender
airtime 16.5 percent above STR MLO, but missed 1.005 percent of frames versus
0.718 percent for MLO.  Pooled completed-frame P99 latency was 21.78 ms versus
19.63 ms.  The 1.5 s bucket deferred 1,328 risk-eligible actions, including 85
actual primary misses, and used only 0.61 percent secondary airtime.  Do not
promote or reuse these seeds as confirmation evidence.

The existing treatment-free prediction dataset already contains correct
primary-copy labels.  A group-held-out audit found that its neutral OBSS slice
had never entered deployed-model training.  With the model family and feature
contract held fixed, target-domain T0 training increased held-out ROC AUC from
0.835 to 0.943 and top-10-percent miss recall from 58.6 to 81.9 percent.  The
next controller iteration should therefore combine this frozen T0 model with
whole-copy rescue, a fixed risk-density gate, and a burst-capable bucket whose
startup credit and total finite-run airtime remain explicit.  Evaluate it on a
new seed block before any publication-scale campaign.

Deadline-aware frame abandonment
--------------------------------

Add an abandonment action for a frame whose remaining packets cannot
plausibly complete before its deadline on any usable path.  The controller
should choose among three outcomes:

#. continue the primary transmission when it remains viable;
#. launch a secondary copy when the extra path can plausibly rescue the frame;
#. abandon the remaining packets when neither action can meet the deadline.

An intentionally abandoned frame must remain in the generated-frame
denominator and count as an incomplete deadline miss.  The benefit must come
from avoiding wasted airtime and head-of-line blocking for later viable
frames, not from removing difficult frames from the metric.

The current burst sender submits a complete frame to the UDP and MAC queues
immediately.  Implementing effective abandonment therefore requires either an
application-owned egress queue that checks slack before each send or a safe way
to identify and purge a frame's queued MAC packets.  Record the abandonment
time, decision evidence, unsent bytes, estimated saved airtime, and the effect
on subsequent-frame outcomes.  Use a conservative feasibility bound so that
uncertain frames are rescued rather than prematurely discarded.

Final statistical campaign
--------------------------

After freezing the controller, MLO profiles, and victory thresholds, run at
least 80--100 paired seeds.  Consider increasing the measurement duration from
60 seconds to 180--300 seconds so each run contains enough deadline misses for
stable absolute-rate and tail estimates.  Runs, not frames, remain the
independent sample unit.

Deferred controlled-environment studies
---------------------------------------

First obtain a strong result under the existing random OBSS environment,
without constructing favorable interference or blockage.  Full duplication
already demonstrates that the naturally occurring link diversity is useful.

After the main result is established, test robustness and mechanism boundaries
with controlled paired scenarios:

* independent versus correlated interference across links;
* asymmetric link quality and load;
* bursty blockage and recovery;
* low, medium, and high OBSS load;
* multiple streaming loads, frame deadlines, and GOP profiles; and
* replayed placements and traffic/interference traces shared by every
  treatment.

These studies are deferred robustness work and must not block the next
MLO-focused experiment round.
