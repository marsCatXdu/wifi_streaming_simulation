# Wi-Fi streaming core design

The module treats an encoded video frame as the unit of generation, policy
choice, and measurement. `FrameSource` produces finite frame descriptors.
`FramePacketizer` divides each descriptor into UDP payloads and supplies
deterministic burst or uniform emission offsets. Every datagram carries a
versioned, fixed-width `StreamingHeader`; all multibyte fields use network byte
order and malformed or unsupported headers are rejected.

The synthetic source models a periodic GOP. `frameSize` is the interframe
size; every `gopLength` frames it emits an I-frame whose size is multiplied by
`keyframeSizeMultiplier`. The reference profile uses a 60-frame GOP and a
fourfold keyframe multiplier: at 30 frames/s, a 12 KB interframe is followed
every two seconds by a 48 KB keyframe. Trace sources retain their recorded
frame types and sizes unchanged.

`MultipathSender` stores paths in a `PathId` map and invokes one replaceable
`RedundancyPolicy` at each frame boundary. A `PolicyDecision` selects one
primary and, when duplicating, one secondary. The sender independently
packetizes a complete copy for each selected path: copy 0 uses the primary
link and copy 1 uses the secondary link. Packets are never sprayed between
links. Successful wire-byte counts are available in total, by path, and for
the redundant copy. The implemented baselines are `FixedLinkPolicy`,
`StaticBestLinkPolicy`, and `FullDuplicationPolicy`.

`FrameReceiver` uses packet indexes rather than arrival order. It separately
tracks the union, each copy, and each sender link. A repeated union packet is
counted and discarded while still contributing to the independent copy state.
Single-copy frames are finalized at union completion. Duplicated frames retain
the union-completion timestamp while waiting for both independent copy states;
they are finalized when both copies complete, or at deadline, cleanup timeout,
or application stop. Incomplete frames are always emitted.

`MetricsCollector` owns the stable `frames.csv` and `policy_decisions.csv`
schemas. It registers every generated frame before transmission, so a frame
with no received packets is still emitted as incomplete at run finalization.
Empty completion timestamps represent incomplete copies or frames; they are
not encoded as zero.

The example supports `--topology=single_link` (the original deterministic
5 GHz setup), `--topology=dual_interface`, `--topology=mlo_str`, and
`--topology=mlo_emlsr`. Dual mode installs independent 2.4 GHz and 5 GHz
`SpectrumWifiPhy` channels, MAC/PHY contention state, station/AP device pairs,
and IP subnets. Each sender UDP socket is bound both to its radio's source
address and its `NetDevice`; matching host routes lead through the
corresponding AP interface to the shared high-rate CSMA edge subnet. A
constant station manager and `FixedRssLossModel` avoid unrelated randomness.
`--policy` selects `fixed_link_0`, `fixed_link_1`, `static_best`, or
`full_duplication` in dual mode. Application output, rather than PCAP or
FlowMonitor, is the source of truth.

Dual mode requires IEEE 802.11be and installs two independent single-link
`WifiNetDevice` instances; it does not create an MLD or enable native MLO.
Both dual-interface links and both STR links use `EhtMcs5`, 20 MHz channels,
an 800 ns guard interval, and the same explicit MAC controls.

Native STR mode requires `--wifiStandard=eht` and installs one two-link
`WifiNetDevice` on each of the STA and AP MLDs. `SpectrumWifiPhyHelper(2)`
maps link 0 to its own 2.4 GHz `MultiModelSpectrumChannel` and link 1 to its
own 5 GHz channel; both links use explicit 20 MHz `ChannelSettings` and fixed
`EhtMcs5`. `WifiStaticSetupHelper` performs deterministic MLD association and
installs bidirectional Block Ack agreements for the selected streaming TID
when A-MPDU is enabled. Uplink streaming traffic is mapped to the set `{0,1}`,
leaving queueing and link selection to the native ns-3 MLO MAC. The
application creates one UDP socket, one IP interface, and one application
copy. Explicit routes replace global routing, which cannot query a single
channel from a multi-channel MLD.

EHT MACs always use 802.11e/WMM-style QoS in ns-3. The `--wmmMode=off`
default preserves the historical target-stream marking (CS0, TID 0, and
`AC_BE`). `--wmmMode=on` marks only target streaming sockets as CS5, selecting
TID 5 and `AC_VI` with the standard EDCA parameters. `--wmmMode=af41` instead
uses the standards-oriented AF41 marking, TID 4, and the same `AC_VI` EDCA
category. The selected TID is used consistently for native
MLO TID-to-link mapping, static Block Ack, prediction telemetry queue binding,
and resolved configuration output.

OBSS traffic remains historical CS0/`AC_BE` under the default
`--obssWmmProfile=legacy`. Explicit realism treatments can select `be`,
`one_vi_per_channel`, or `all_vi`. The latter two mark, respectively, the
first uplink flow on each channel or every OBSS flow as AF41/TID 4/`AC_VI`.
The exact selected flow ordinals are recorded in `resolved_config.json`.

Native EMLSR mode reuses that two-link MLD and one-copy application path, with
`NMaxInflights=1`. Static setup is deliberately ordered as association, EMLSR
activation, then bidirectional Block Ack. The predeclared
`advanced_sta_ap_fixed_aux_v4` practical profile uses
`AdvancedEmlsrManager` at the STA and `AdvancedApEmlsrManager` at the AP.
It enables EMLSR links `{0,1}`, places PHY 1 initially on the 5 GHz link as
the main PHY, advertises 128 us padding and transition delays, and uses a
100 us channel-switch delay. The fixed 20 MHz OFDM auxiliary PHY is not TX
capable, does not switch bands, is not put to sleep, and models no in-device
interference. The profile explicitly pins the inherited and advanced STA/AP
manager controls, the EHT transition and medium-sync controls, the PHY
MAC-header notification, and the channel-access-manager controls that affect
switch decisions. A TXOP released without a transmission retains its zero
backoff, which is the more aggressive standard-permitted behavior. The
advanced manager defers a same-boundary retry by one simulator timestep so a
switch refusal cannot immediately reacquire and release the same TXOP
indefinitely at one simulation timestamp. The main PHY has spectrum interfaces
for both bands, which is required for an actual cross-band switch.

An earlier literal `DefaultEmlsrManager` reference profile (32 us padding,
128 us transition, TX-capable switching auxiliary PHY) activated EMLSR in both
MLDs but used only the 5 GHz link: a neutral mixed4x4 smoke observed zero 2.4
GHz successful MPDUs and PHY TX time. That observation motivated the stronger
versioned profile, but zero activity on one link is not by itself an activation
failure and is not grounds for discarding a neutral experiment outcome. Every
practical-profile run writes `mlo_runtime.json`; the validator checks the exact
configured and observed profile and cross-checks the per-link activity arrays
against `link_intervals.csv` in link-ID order. A separate controlled high-load
integration smoke requires nonzero activity on both links to prove the profile
can exercise both paths.

`mloStaMaxInflights` controls the target STA's BE `QosTxop::NMaxInflights`.
The value one permits traffic splitting but prevents an MPDU from being
simultaneously active on both links. The value two permits opportunistic
uplink MPDU duplication after Block Ack setup. It does not affect the AP,
background BSSs, or application-copy accounting.

UL OFDMA uses `RrMultiUserScheduler` on selected HE/EHT APs. A nonzero
`AccessReqInterval` is mandatory because the target workload is uplink-only;
without periodic AP access requests, the scheduler has no downlink frame from
which to initiate a trigger exchange. The experiment exposes BSRP enablement,
maximum scheduled stations, fallback UL PSDU size, and either target-AP or
all-capable-AP scope. HT/VHT stations remain EDCA contenders and cannot be
allocated RUs.

The 2.4 GHz link uses `ErpOfdmRate24Mbps` for control traffic; the 5 GHz link
uses `OfdmRate24Mbps`. This distinction is required for trigger and QoS Null
exchanges. Data remains fixed at MCS 5 for the target on both links.

## Output and measurement contract

`streaming-experiment` requires `--outputDir`. The path must be new or empty,
and one run atomically owns its contents. The frame-generation interval is the
measurement window; the preceding one second is association/neighbor-cache
warmup. `WifiTxStatsHelper` and `WifiCoTraceHelper` are enabled only for the
station radios and clip accounting to that window. Delayed outcomes for MPDUs
enqueued inside the window are retained according to `WifiTxStatsHelper`
semantics. Packet-event, MAC-event, PCAP, and FlowMonitor logging are disabled.

Every successful run writes:

```text
resolved_config.json
build_info.json
frames.csv
policy_decisions.csv
link_intervals.csv
mac_summary.csv
ofdma_summary.csv
summary.json
```

Fixed-link prediction runs additionally write `prediction_samples.csv` and,
when requested, `prediction_events.csv`. Their causal contract, field
dictionary, and validation workflow are defined in `prediction-telemetry.md`.
Increment 1 verification is recorded in
`prediction-telemetry-acceptance.md`. The offline join, label, provenance, and
run-grouped split workflow is defined in `prediction-dataset.md`; its
pre-production smoke evidence is recorded in `prediction-dataset-smoke.md`.
The causal fixed-threshold evaluation over individual 5 GHz runs is defined
in `prediction-online-replay.md`.

`resolved_config.json` records the effective stream, propagation, channel,
rate, guard interval, queue, aggregation, Block Ack, retry, RTS/fragmentation,
access-category, and TXOP settings. `build_info.json` records the ns-3 version,
the immutable upstream ns-3.48 commit, the project commit, compiler, build
profile, UTC execution time, and host. `--projectGitCommit` takes precedence;
otherwise ns-3 build-version metadata is used. The legacy `--framesFile` and
`--decisionsFile` options copy their corresponding required artifacts after
the run.

`link_intervals.csv` currently has one row per radio link for the full
measurement window. Application counters come from sender/receiver state,
MPDU counters and service times come from `WifiTxStatsHelper`, and PHY state
durations come from `WifiCoTraceHelper`. Queue occupancy and estimated rate
are left empty because this fixed-rate baseline does not install sampled queue
or rate traces; zero would incorrectly claim an observation. Native MLO has no
application-level link attribution, so its one-path byte totals appear on row
0 and row 1 has zero application bytes; MAC and PHY fields on both rows are
genuinely per-link.

`ofdma_summary.csv` separates target devices, same-BSS background STAs, and
independent OBSS devices. It counts Basic and BSRP triggers, RU grants,
transmitted TB PPDUs and bytes, and successfully monitored TB MPDUs and bytes.
The file is present for enabled and disabled runs so paired analyses can
verify that the disabled treatment generated no trigger-based traffic.

`summary.json` uses generated frames as its denominator. It reports complete,
incomplete, and deadline counts/ratios; linear-interpolated P50/P90/P95/P99
over completed frames; goodput; redundant-byte ratio; duplication recovery
and no-benefit fields; and MAC/PHY totals. P99.9 is `null` unless at least
100,000 completed observations leave roughly 100 samples above that quantile.
A duplicated frame is a recovery when the union completes before the primary
copy (or while it remains incomplete). It provides no benefit when primary
copy completion equals union completion.

## Background and correlated-load model

`CorrelatedLoadController` owns separate common and per-link alternating ON/OFF
processes. `independent` uses only per-link processes, `common_bursts` applies
each common transition explicitly to every link, and
`mixed_common_and_independent` gates a link ON when either its local process or
the common process is ON. `trace_replay` consumes `timestamp_s,link,on` CSV
rows; `timestamp_s` is relative to the controller start, not simulation time
zero. Common and local exponential durations have separate assigned ns-3
streams; a positive deterministic duration replaces the corresponding
exponential draw. Transition records retain the event source even when a
mixed-mode event does not change effective state.

The experiment's no-background default is unchanged. Background stations are
configured per link with alternating near/far coordinates and may generate
constant-rate UDP, controller-gated bursty UDP, or TCP bulk traffic. Direction
is uplink, downlink, or alternating (`mixed`). Streaming/AP devices use
`--wifiStandard`; background stations independently use
`--backgroundStandard0` and `--backgroundStandard1` (`inherit` by default).
Each helper has its own `ConstantRateWifiManager` fixed at HT, VHT, HE, or EHT
MCS 5. The fixed dual-interface channel plan is 2.4 GHz plus 5 GHz, so VHT is
rejected on link 0 and allowed on link 1. A background standard cannot be newer
than its AP standard. Heterogeneous standards are uplink-only: downlink and
mixed directions are rejected because one fixed AP data mode cannot safely
serve legacy stations. Invalid combinations abort before simulation.

`--backgroundProfile=legacy_mixed8` installs eight independent non-MLD
contenders on each active link. The 2.4 GHz set contains HT, HE, and EHT
stations but no VHT station; the 5 GHz set contains HT, VHT, HE, and EHT
stations. Every station is installed by a separate `WifiHelper` on the same
`MultiModelSpectrumChannel` as the streaming radio and uses its standard's
fixed MCS 5. With `udp_random_onoff`, each station runs one uplink-only
`OnOffApplication` with independent exponential ON and OFF random variables.
The application consumes two explicit streams starting at
`--backgroundStreamBase`; the ON/OFF means are set by `--randomOnMeanMs` and
`--randomOffMeanMs`. With `udp_bursty`, all stations on a link use
`ControlledUdpApplication` and the existing `CorrelatedLoadController`.
`--correlationMode` then selects independent per-link, common cross-link, or
mixed common-and-independent bursts.

For native MLO, AP MLD beacon generation is enabled and the non-MLD stations
passively associate to the AP link whose channel matches their PHY. The
streaming MLD association remains static. This is the ns-3.48-supported path:
using `WifiStaticSetupHelper` for a non-MLD station and AP MLD reaches an
invalid remote-station-manager link during data transmission. Dual-interface
and MLO runs therefore use the same station standards, channels, traffic
parameters, application streams, and physical contenders, while association
mechanics necessarily differ.

Resolved background profile, counts, per-link and per-station standards,
association mode, direction, traffic kind, rates, placement, controller
means/durations, trace, stream base, per-application streams, and random
ON/OFF means are written under `background` in `resolved_config.json`.
`summary.json` adds offered and delivered bytes in total, per link, and per
station, plus delivered throughput. Existing CSV columns are unchanged.

### Overlapping BSS profile

`--obssProfile=mixed4x4` is independent of `--backgroundProfile` and may be
used alone or with `legacy_mixed8`. It creates four infrastructure BSSs with
unique SSIDs and four statically associated STAs each. HT and HE BSSs share
the experiment's 2.4 GHz 20 MHz channel; VHT and single-link EHT BSSs share
the 5 GHz 20 MHz channel. Background APs and STAs use Minstrel-HT rate
adaptation with 50 ms statistics updates. The target devices remain fixed at
EHT MCS 5 for a controlled dual-versus-MLO comparison.

Each STA has simultaneous UL and DL UDP ON/OFF sources. ON and OFF durations
are independent exponential samples. At the start of every ON period, uplink
draws 0.5--3 Mbps and downlink draws 2--8 Mbps. The 100 ms ON and 300 ms OFF
means provide 25 percent expected duty. Each selected rate is held for one ON
period. Rate, ON-time, and OFF-time draws use three explicit streams per flow.
Placement, Wi-Fi, application, and propagation streams occupy separately
configured ranges so matched dual and MLO runs use the same inputs.

AP coordinates are sampled inside the configured rectangle. STA angle and
radius are sampled independently around the associated AP. OBSS batches use
`log_distance_nakagami`: a band-specific log-distance model feeds a Nakagami
model on each shared spectrum channel. Scalar loss is therefore
distance-sensitive and fading consumes explicit channel streams.

`background_flows.csv` records one row per BSS, STA, and direction, including
stream assignments and delivered totals. `background_rate_periods.csv`
records every ON interval and its sampled rate. Resolved propagation,
positions, and stream bases are retained in `resolved_config.json`.

### Adaptive secondary-airtime duplication

`adaptive_airtime_duplication` sends every frame first on path 1 (5 GHz) and
may launch one delayed whole-frame copy on path 0 (2.4 GHz). The frozen causal
predictor is evaluated only at configured telemetry stages. Admission uses the
calibrated miss probability minus a shadow-price-weighted, normalized estimate
of secondary sender PHY TX airtime. A token bucket limits measured airtime;
outstanding estimates are reservations and do not reduce the earned balance
until a tagged PPDU actually transmits.

The bucket horizon sets the maximum balance. The independently configurable
initial horizon sets only startup credit and defaults to the maximum horizon,
preserving historical full-bucket startup behavior. The initial horizon must
be positive and no larger than the maximum. A zero dual-update step freezes the
initial shadow price, making it an explicit fixed risk-density gate while the
measured-airtime token bucket remains the hard admission authority.

Selected stages may instead use fixed admission prices through
`adaptive_airtime_decision_offset_shadow_prices` (CLI
`adaptiveAirtimeDecisionOffsetShadowPrices`), encoded as an ordered
`offset_us:price` list. Stages without an override continue to use the global
dual variable. `adaptive_airtime_i_frame_only_decision_offsets_us` (CLI
`adaptiveAirtimeIFrameOnlyDecisionOffsetsUs`) restricts only the listed stages
to I-frames. These controls do not alter packet selection, reservations,
measured-airtime charging, or the one-launch-per-frame state. Decision output
records both the effective admission price and the underlying dual price, and
resolved configuration records every offset override and frame-type
restriction. Multiple adaptive decision stages remain disabled unless the
executable recognizes the exact audited staged-model and policy identity.

By default, both utility and reservation accounting use the retry-inflated
airtime estimate. `adaptive_airtime_admission_uses_retry_inflation = false`
instead prices utility with nominal candidate airtime. Token availability,
reservation, settlement, and measured-airtime debt remain retry-inflated or
measured, so this option does not weaken the airtime safety boundary.
`adaptive_airtime_decisions.csv` records `admission_airtime_us` separately from
the retry-inflated `estimated_airtime_us`; resolved configuration records both
cost definitions and the selected admission mode.

`SecondaryAirtimeMeter` observes path-0 sender `PhyTxPsduBegin` events in the
half-open measurement interval. It counts a tagged data PPDU once, allocates a
multi-frame PPDU in proportion to tagged MPDU bytes, and counts retransmissions
again. A-MSDU and fragmentation are disabled so one terminal packet index is
unambiguous. Reservations settle only after every distinct packet is ACKed or
terminally dropped, with a logged deadline-plus-queue-delay fallback. The
run-level contract consists of `adaptive_airtime_decisions.csv`,
`secondary_airtime_events.csv`, `secondary_airtime_settlements.csv`, and
`secondary_airtime_summary.json`. Validation reconciles the controller
arithmetic, action frame IDs, event total, per-frame settlements, link-0 PHY TX
occupancy, and finite-run budget.

The controller-specific target-domain T0 training and its treatment-free
primary-copy label audit are documented in `primary-risk-t0-training.md`.

### Adaptive primary-deficit duplication

`adaptive_deficit_duplication` preserves the adaptive-airtime admission and
budget controller, but changes the admitted action from a forward whole-frame
copy to the current primary packet deficit. At each synchronous prediction
snapshot, the controller reads the primary copy's per-packet MAC ACK state.
Every original packet index without a positive ACK is selected, including an
index that has already reached a terminal-drop state on the primary. The
selected indexes are sent on path 0 in descending order. Missing or
inconsistent per-packet state aborts the run because silently falling back to
a whole copy would change the experiment treatment.

The secondary projection retains the original frame packet count, packet
indexes, payload sizes, and streaming metadata. The receiver therefore needs
no policy-specific behavior: completion still occurs when the union of unique
packet indexes received on both links reaches the original packet count. A
partial secondary is not itself a complete copy, so duplicated-frame
accounting remains open until the frame deadline while preserving the earlier
union-completion timestamp.

Admission prices only the selected packets' expected MAC service bytes. The
token reservation records the exact selected index set and settles after each
of those indexes is ACKed or terminally dropped on the secondary. Decision
output appends total frame packets, the exact primary-ACKed index set, selected
secondary packet count and indexes, and packet order. Resolved configuration records
`packet_selection = primary_unacknowledged_reverse` and separates the
`F0+F1-degraded` admission features from the `F2-primary-frame-ack-state`
selection dependency.

Output validation reconstructs nominal airtime from the selected indexes and
their exact application payload sizes, including a partial final packet. For a
calibrated controller identity it also pins payload size, reference-frame size,
cost safety factor, and reference airtime to the model contract.

This first policy is a causal current-deficit envelope, not the final learned
packet-deficit predictor. It uses no secondary-link state and makes no claim
that every packet still unacknowledged at a decision would remain missing at
the deadline. Primary packets may complete after selection, so some secondary
work can still become redundant. There is one secondary launch per frame; the
controller does not top up, cancel, or revise an admitted packet set. The
whole-copy `adaptive_airtime_duplication` policy remains unchanged for paired
comparison and reproduction of earlier results.

Controller behavior is covered by deterministic, common-versus-local, trace,
and independent-versus-common tests in the module suite. The controlled
end-to-end check
`contrib/wifi-streaming/test/background-load-integration.py` compares matched
dual-interface runs and requires offered load to worsen streaming latency or
delivery and link-0 MPDU service time.

`contrib/wifi-streaming/test/mlo-str-integration.py` requires complete native
MLO delivery, one socket/no duplication metadata, two rows sharing one device
ID, and successful MPDUs plus PHY TX occupancy on both links.

`contrib/wifi-streaming/test/mlo-emlsr-integration.py` additionally requires
the exact practical profile in resolved and runtime metadata, initial 5 GHz
main-PHY placement, AP-side EMLSR enablement on both links, and exact equality
between runtime and interval activity counters. It runs the neutral mixed4x4
OBSS workload under a wall-clock timeout so a same-timestamp channel-access
loop fails deterministically instead of occupying a worker indefinitely.

`contrib/wifi-streaming/test/selective-meter-regression.py` runs the frozen
selective controller with identical RNG inputs and the passive airtime meter
disabled and enabled. After removing run IDs, it requires identical field
values in the frame, final policy-decision, and selective-controller CSV rows.

## Current boundaries

- Stations associated with the target MLO AP are supported only by the
  `legacy_mixed8` uplink profile. Independent OBSS networks support both
  directions. STR and the predeclared practical EMLSR profile are implemented;
  no other EMLSR manager or hardware profile is implied.
- ns-3.48 reports successful MPDUs and their service times per MLD link, but
  failed MPDUs and retry-limit drops only per device. Those device-level
  failure totals are placed on MLO link row 0 to avoid double-counting;
  successful retransmissions are summed from per-link success records.
- Prediction telemetry remains receiver-independent and is restricted to
  fixed-link, selective-duplication, adaptive-airtime, and adaptive-deficit
  dual-interface runs. All three causal policies use the frozen
  commodity-polling admission model; adaptive-deficit packet selection also
  depends on primary per-frame F2 ACK state. `StaticBestLinkPolicy` uses
  configured initialization scores and does not switch during a run.
- A duplicated frame retains its union-completion timestamp while the receiver
  waits for both complete copy states or a finalization timeout. This permits
  independent copy-completion and duplicate accounting without changing the
  measured union latency.
- `trace_defined` packet emission falls back to burst because the core
  `FrameDescriptor` has no per-packet timing field.
- Frame deadlines and generation times are application timestamps; encoder,
  decoder, and firmware processing delays are outside the model.
- `link_intervals.csv` is a whole-window interval in this phase; periodic
  queue/rate sampling is not implemented.
- MAC and airtime totals cover transmitting station radios. AP ACK/control
  airtime is observable in the station PHY receive state but does not create
  AP MAC rows.
- `FixedRssLossModel` intentionally makes geometry irrelevant in legacy smoke
  configurations. OBSS configurations use log-distance loss plus Nakagami
  fading, so their coordinates affect received power.
- TCP offered-byte accounting uses application Tx trace bytes; delivered bytes
  come from sinks. TCP burst gating and per-background-station MAC summaries
  are not implemented.
