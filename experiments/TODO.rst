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
