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

The frozen T0 design has two predeclared engineering operating points.  During
honest procedure evaluation, an evaluation ranker fit on the training groups
used calibration-selected gates on then-untouched test groups.  The
0.50-percent procedure recalled 77.0 percent of primary misses at 0.407 percent
estimated nominal secondary airtime; the 0.70-percent procedure recalled 86.7
percent at 0.594 percent.  After that evaluation was frozen, the deployment
ranker was refit on the former training and test groups; the calibration groups
alone fit its probability calibration and deployment gates.  Thus the fresh
engineering seeds are the first independent assessment of the exact deployed
ranker and thresholds.  Runtime admission must use nominal airtime, as the
calibration did, while token reservation and measurement retain the
retry-inflated estimate.

Retrospective replay on the failed pilot seeds is mechanism evidence only.  It
showed that a strict equal wall-clock budget clips actions most aggressively in
the naturally difficult OBSS realizations: a 0.60-percent token rate retained
only 46.5 percent of primary misses selected by the 0.50-percent gate, whereas
the unthrottled fixed gate selected 77.5 percent.  Consequently, the fresh
``primary_risk_mlo_005_v1`` and ``primary_risk_mlo_007_v1`` engineering runs use
a 2-percent token rate with ten seconds of capacity and two seconds of startup
credit as a loose catastrophe guard.  The resulting 60-second nominal
finite-run secondary-airtime allowance is 2.067 percent; a retry-cost
underestimate can still create explicitly reported settlement excess.  This is
not a claim that the controller guarantees the less-than-20-percent
total-airtime ambition.  Judge that ambition from the paired measured mean, and
report per-run scatter, high-airtime tails, bucket deferrals, debt, and any
budget excess.  Seeds 43--54 are reserved for this engineering comparison; do
not reuse them as final confirmation evidence.

The first execution completed all adaptive and STR runs before exposing a
same-timestamp EMLSR channel-access loop.  These partial results are development
evidence only because the three treatments do not share the eventual corrected
build identity.  Against matched STR, the 0.50-percent gate reduced the mean
deadline-miss ratio by 42.1 percent and completed-frame P99 by 12.8 percent at a
13.9-percent ratio-of-means summed-airtime increase.  The 0.70-percent gate
reduced them by 59.1 percent and 16.6 percent, respectively, at 17.4 percent
extra airtime.  Both paired miss and P99 differences had two-sided 95-percent
Student-t intervals below zero; the loose gate exceeded 20 percent extra
airtime in six of twelve individual runs.

A retrospective nested-action sweep of the loose run selected a fixed shadow
price of 0.034 as the next engineering point.  It estimated a 56.7-percent miss
reduction, 15.3-percent P99 reduction, and 16.4-percent ratio-of-means airtime
increase.  This sweep is useful for choosing a candidate, but it is not an
independent result: secondary actions can perturb later random outcomes, and
the same seeds selected the point.  Run
``closed_loop_primary_risk_mlo_frontier.yaml`` to execute adaptive, STR, and the
strong no-extra-backoff EMLSR comparator from one corrected commit.  Freeze or
replace the controller only after analyzing that complete engineering matrix;
use a new seed block for confirmation.

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
