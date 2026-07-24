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
5 GHz setup), `--topology=dual_interface`, and `--topology=mlo_str`. Dual mode installs independent
2.4 GHz and 5 GHz `SpectrumWifiPhy` channels, MAC/PHY contention state,
station/AP device pairs, and IP subnets. Each sender UDP socket is bound both
to its radio's source address and its `NetDevice`; matching host routes lead
through the corresponding AP interface to the shared high-rate CSMA edge
subnet. A constant station manager and `FixedRssLossModel` avoid unrelated
randomness. `--policy` selects `fixed_link_0`, `fixed_link_1`, `static_best`,
or `full_duplication` in dual mode. Application output, rather than PCAP or
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
installs bidirectional TID-0 Block Ack agreements when A-MPDU is enabled.
Uplink TID 0 is mapped to the set `{0,1}`, leaving queueing and link selection
to the native ns-3 MLO MAC. The application creates one UDP socket, one IP
interface, and one application copy. Explicit routes replace global routing,
which cannot query a single channel from a multi-channel MLD.

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
fixed MCS 5. Each station runs one uplink-only `OnOffApplication` with
independent exponential ON and OFF random variables. The application consumes
two explicit streams starting at `--backgroundStreamBase`; the ON/OFF means
are set by `--randomOnMeanMs` and `--randomOffMeanMs`.

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

Controller behavior is covered by deterministic, common-versus-local, trace,
and independent-versus-common tests in the module suite. The controlled
end-to-end check
`contrib/wifi-streaming/test/background-load-integration.py` compares matched
dual-interface runs and requires offered load to worsen streaming latency or
delivery and link-0 MPDU service time.

`contrib/wifi-streaming/test/mlo-str-integration.py` requires complete native
MLO delivery, one socket/no duplication metadata, two rows sharing one device
ID, and successful MPDUs plus PHY TX occupancy on both links.

## Current boundaries

- Stations associated with the target MLO AP are supported only by the
  `legacy_mixed8` uplink profile. Independent OBSS networks support both
  directions. STR is implemented; EMLSR is not configured or implied.
- ns-3.48 reports successful MPDUs and their service times per MLD link, but
  failed MPDUs and retry-limit drops only per device. Those device-level
  failure totals are placed on MLO link row 0 to avoid double-counting;
  successful retransmissions are summed from per-link success records.
- Prediction telemetry is passive and restricted to fixed-link dual-interface
  runs. No model or adaptive action is implemented. `StaticBestLinkPolicy`
  uses configured initialization scores and does not switch during a run.
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
