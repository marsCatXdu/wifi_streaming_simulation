# Specification: ns-3 Environment for Low-Latency Multi-Link Wi-Fi Streaming

## 1. Project Objective

Build a reproducible ns-3 experiment framework for studying frame-level tail latency in real-time uplink streaming over:

1. A single Wi-Fi link.
2. Two independent Wi-Fi interfaces using application-controlled link selection and selective duplication.
3. Native IEEE 802.11be STR MLO.
4. Native IEEE 802.11be EMLSR.
5. IEEE 802.11ax AP-triggered uplink OFDMA where applicable.

The framework must support mixed Wi-Fi generations, correlated and independent congestion across bands, trace-driven video traffic, per-frame latency measurement, and batch experiment execution.

The framework is not intended to reproduce the behavior of a particular commercial Wi-Fi 7 chipset or firmware. It is a mechanism-level simulator for controlled comparisons and counterfactual experiments.

------

## 2. Research Questions

The implementation must support answering the following questions:

### RQ1: Selective redundancy

Under what conditions does selective frame duplication reduce frame-completion tail latency relative to:

- Single-link transmission.
- Static link selection.
- Full duplication.
- Native STR MLO.
- Native EMLSR.

### RQ2: Redundancy cost

What latency reduction is obtained per unit of additional:

- Application bytes.
- MAC bytes.
- Channel airtime.
- Background-flow degradation.

### RQ3: Cross-link correlation

How does the benefit of duplication change when delay events on the two links are:

- Independent.
- Weakly correlated.
- Strongly correlated.
- Caused by a common traffic burst.

### RQ4: Heterogeneous deployments

How robust are the results when the network includes:

- VHT/Wi-Fi 5 stations.
- HE/Wi-Fi 6 stations.
- EHT/Wi-Fi 7 stations.
- Near/far asymmetry.
- Different traffic directions and offered loads.

### RQ5: Wi-Fi 7 comparison

Does application-controlled dual-interface redundancy still provide useful latency–airtime tradeoffs when compared with native STR MLO or EMLSR?

------

## 3. Technical Baseline

Pin the initial implementation to **ns-3.48**. Record the exact git commit in every experiment.

Current ns-3 Wi-Fi documentation reports support for:

- IEEE 802.11be PHY.
- Multi-link discovery and setup.
- STR MLO.
- EMLSR.
- IEEE 802.11ax uplink and downlink OFDMA.
- Multiple `WifiNetDevice` instances on one node.
- Multiple spectrum channels through `SpectrumWifiPhy`.

Important limitations:

- EMLSR is explicitly marked experimental and may fail or crash under some configurations. It must not block completion of the core dual-interface framework.
- MU-MIMO has only an idealized PHY-level implementation and lacks a complete MAC-layer scheduler. Do not present simulated UL MU-MIMO as a realistic hardware baseline.
- Processing delays, beamforming, firmware queues, and commercial NIC scheduling are not modelled.
- MLO may cause `WifiPhy::MonitorSniffRx` or generated PCAP traces to omit some received packets. Application-level instrumentation must be the source of truth.
- MinstrelHT has documented open issues. Initial mechanism experiments must use controlled PHY rates; rate adaptation should be added only as a sensitivity experiment.

The framework should use existing ns-3 facilities including:

- `WifiTxStatsHelper` for MPDU enqueue, transmission, acknowledgement, failure, retransmission, and per-link success information.
- `WifiCoTraceHelper` for PHY-state and channel-occupancy accounting.
- `WifiStaticSetupHelper` for deterministic association, multi-link setup, Block Ack setup, and EMLSR setup.
- `NeighborCacheHelper` to avoid ARP/NDISC transients during the measurement interval.

------

## 4. Non-Goals

The initial implementation must not attempt to provide:

- A complete WebRTC, RTSP, RTP congestion-control, or codec implementation.
- Realistic commercial NIC firmware emulation.
- Full UL MU-MIMO MAC scheduling.
- Beamforming or CSI-based scheduling.
- Site-specific ray tracing.
- Detailed encoder or decoder processing.
- Mobility or roaming.
- Cross-layer cancellation of packets already queued in the Wi-Fi MAC.
- Modifications to ns-3 core unless no extension point exists.
- Simulation-only claims about real hardware performance.

These can be considered after the basic environment is validated.

------

## 5. Repository Structure

Implement the project as an ns-3 `contrib` module rather than one large file under `scratch`.

```text
ns-3/
├── contrib/
│   └── wifi-streaming/
│       ├── CMakeLists.txt
│       ├── doc/
│       │   └── design.md
│       ├── model/
│       │   ├── streaming-header.{cc,h}
│       │   ├── frame-source.{cc,h}
│       │   ├── frame-packetizer.{cc,h}
│       │   ├── multipath-sender.{cc,h}
│       │   ├── frame-receiver.{cc,h}
│       │   ├── redundancy-policy.{cc,h}
│       │   ├── link-telemetry.{cc,h}
│       │   ├── probe-application.{cc,h}
│       │   ├── metrics-collector.{cc,h}
│       │   └── correlated-load-controller.{cc,h}
│       ├── helper/
│       │   ├── streaming-helper.{cc,h}
│       │   ├── topology-helper.{cc,h}
│       │   └── output-helper.{cc,h}
│       ├── examples/
│       │   └── streaming-experiment.cc
│       └── test/
│           ├── packetization-test.cc
│           ├── reassembly-test.cc
│           ├── policy-test.cc
│           └── integration-test.cc
├── experiments/
│   ├── configs/
│   ├── traces/
│   └── results/
└── tools/
    ├── run_experiments.py
    ├── validate_outputs.py
    ├── summarize_runs.py
    └── plot_results.py
```

Do not implement the simulation exclusively in Python bindings. Use C++ for simulation components and Python for experiment orchestration and analysis.

------

## 6. Topology Modes

The executable must support three distinct topology modes.

### 6.1 Single-link topology

```text
Streaming STA
     |
  Wi-Fi link
     |
     AP
     |
 Ethernet/CSMA
     |
 Edge receiver
```

Use this for:

- SLO 2.4 GHz.
- SLO 5 GHz.
- SLO 6 GHz sensitivity experiments.
- Calibration against hardware.

### 6.2 Application-controlled dual-interface topology

```text
                     2.4 GHz WifiNetDevice
                    /
Streaming STA -----+
                    \
                     5 GHz WifiNetDevice
                            |
                      dual-radio AP
                            |
                      Ethernet/CSMA
                            |
                       edge receiver
```

Requirements:

- The sender node has two independent `WifiNetDevice` instances.
- The AP node has one AP interface for each band.
- Each band has its own channel, MAC queue, PHY, contention state, and IP address.
- The application owns two UDP sockets.
- Each socket must be bound to both:
  - The corresponding source address.
  - The corresponding `NetDevice`, using `BindToNetDevice` or an equivalent mechanism.
- The AP routes traffic between both wireless subnets and the wired receiver subnet.
- The sender policy explicitly chooses the interface for every frame.
- This topology must not use native MLO.

This mode represents the proposed application-controlled or bridge-controlled multipath system.

### 6.3 Native MLO topology

```text
STA MLD
  ├── link 0
  └── link 1
       |
     AP MLD
       |
   Ethernet
       |
   receiver
```

Requirements:

- Use `WIFI_STANDARD_80211be`.
- Use one multi-link `WifiNetDevice` per MLD.
- Use `SpectrumWifiPhyHelper` with two PHY links and two spectrum channels.
- Configure independent band, channel width, propagation model, and PHY parameters per link.
- Use multi-link setup rather than two separate IP interfaces.
- Keep the application unaware of native link selection.
- Support:
  - STR mode.
  - EMLSR mode as an optional experimental mode.
- Record all MLO and EMLSR configuration parameters in the output metadata.

For EMLSR, expose at least:

- Main PHY ID.
- EMLSR link set.
- Channel-switch delay.
- Transition delay.
- Padding delay.
- Auxiliary PHY channel width.
- Whether auxiliary PHY transmission is enabled.
- Selected EMLSR manager type.

------

## 7. Application Traffic Model

### 7.1 Frame abstraction

Traffic must be generated as application frames rather than a continuous byte stream.

Each frame contains:

```cpp
struct FrameDescriptor
{
    uint64_t frameId;
    uint64_t generationTimeNs;
    uint32_t frameSizeBytes;
    uint32_t packetCount;
    uint32_t deadlineUs;
    FrameType frameType;
};
```

Supported frame types:

```text
UNKNOWN
I_FRAME
P_FRAME
B_FRAME
PRIORITY_HIGH
PRIORITY_NORMAL
PRIORITY_LOW
```

The application must not require actual video decoding.

### 7.2 Trace-driven source

Support a CSV input trace:

```text
frame_id,generation_time_us,size_bytes,frame_type,deadline_us
0,0,126442,I_FRAME,33333
1,33333,59122,P_FRAME,33333
2,66666,47381,P_FRAME,33333
```

The trace source must preserve:

- Original frame intervals.
- Frame-size variation.
- GOP or frame-type information.
- Explicit deadlines where supplied.

### 7.3 Synthetic source

Also support synthetic generation with:

- Configurable FPS.
- Configurable duration.
- Constant, lognormal, or empirical frame-size distribution.
- Configurable GOP structure.
- Configurable frame deadline.
- Configurable random stream.

Synthetic traffic is for unit testing and parameter sweeps. Main evaluation should use measured frame-size traces.

### 7.4 Packetization

Implement a custom `StreamingHeader`.

Minimum fields:

```text
magic
version
run_id_hash
frame_id
packet_index
packet_count
frame_size_bytes
frame_type
generation_time_ns
deadline_us
copy_id
sender_link_id
flags
```

Requirements:

- Header serialization and deserialization must have unit tests.
- Packet payload size must be configurable.
- Default application payload should be selected to remain below the configured IP MTU after all headers.
- The receiver must tolerate out-of-order packets.
- Application-layer retransmission is disabled.
- Wi-Fi MAC retransmission remains enabled.

### 7.5 Packet emission modes

Support:

```text
burst
uniform_within_frame
trace_defined
```

`burst` sends all packets of a frame immediately after frame generation.

`uniform_within_frame` distributes packets over a configurable emission span.

Do not assume one of these modes is universally correct. Record the selected mode in every result.

------

## 8. Sender Architecture

Implement a `MultipathSender` application.

```cpp
class MultipathSender : public Application
{
public:
    void SetFrameSource(...);
    void SetPolicy(...);
    void AddPath(PathId id, Ptr<Socket> socket, Ptr<NetDevice> device);
    void SetTelemetry(Ptr<LinkTelemetry> telemetry);
};
```

For each generated frame:

1. Obtain the current causal telemetry snapshot.
2. Invoke the configured policy.
3. Select the primary path.
4. Decide whether the frame is duplicated.
5. Packetize the frame.
6. Send packets on the chosen path or paths.
7. Record the policy decision.

The policy must make one decision per frame in the initial implementation. Do not implement arbitrary per-packet spraying in the MVP.

------

## 9. Receiver Architecture

Implement a `FrameReceiver` application.

The receiver must maintain:

```text
frame_id
expected_packet_count
unique packets received
duplicates received
first packet arrival
last unique packet arrival
per-copy packet state
per-link packet state
completion status
deadline status
```

A frame is complete when all unique packet indexes have arrived, regardless of which link supplied each packet.

For duplicated frames, compute:

- Union completion time using the first arrival of every unique packet.
- Completion time of copy 0 independently.
- Completion time of copy 1 independently.
- Number of duplicate packets discarded.
- Whether completion used:
  - Link 0 only.
  - Link 1 only.
  - A mixture of both links.

Incomplete frames must be finalized when:

- Their deadline expires.
- A configurable cleanup timeout expires.
- The simulation terminates.

Do not silently discard incomplete frames from the output.

------

## 10. Transmission Policies

Define a common interface:

```cpp
struct PolicyDecision
{
    PathId primaryPath;
    bool duplicate;
    std::optional<PathId> secondaryPath;
    std::string reason;
    double primaryScore;
    double secondaryScore;
};

class RedundancyPolicy : public Object
{
public:
    virtual PolicyDecision Decide(
        const FrameDescriptor& frame,
        const LinkTelemetrySnapshot& telemetry) = 0;
};
```

Implement the following policies.

### 10.1 Mandatory baselines

#### `FixedLinkPolicy`

Always use a configured link.

Variants:

```text
fixed_link_0
fixed_link_1
```

#### `FullDuplicationPolicy`

Send every complete frame on both links.

#### `StaticBestLinkPolicy`

Select one link at initialization based on a configured static ranking. Do not change the link during the run.

#### `MinRecentDelayPolicy`

Select the path with the lowest causal recent delay estimate. Do not duplicate.

### 10.2 Initial selective-redundancy policy

Implement `EwmaThresholdPolicy`.

For each link, estimate:

```text
predicted_delay =
    EWMA(probe_forward_delay)
  + queue_bytes / EWMA(delivered_rate)
  + jitter_weight × EWMA_absolute_deviation
```

Decision:

```text
primary = link with minimum predicted_delay

duplicate =
    predicted_delay(primary) > duplication_threshold
    OR telemetry_staleness(primary) > staleness_threshold
```

Duplication must also satisfy a token-bucket redundancy budget:

```text
budget_rate: maximum redundant bytes per second
budget_burst: maximum short-term redundant bytes
```

Expose all weights and thresholds as command-line parameters.

The first adaptive policy is a reference implementation, not the final research contribution. Keep it replaceable.

### 10.3 Oracle policies

Oracle policies must be clearly labelled non-causal.

Possible modes:

- Full-duplication lower envelope.
- Replay-based clairvoyant best-link selection.
- Perfect internal queue-state policy.

Oracle results must never be mixed with deployable policies without explicit labelling.

------

## 11. Link Telemetry

Provide two telemetry classes.

### 11.1 Observable telemetry

May use only information plausibly available to a real sender:

- Probe delay returned through an actual feedback packet.
- Probe loss.
- Recent application delivery reports.
- Recent MAC success/failure events.
- Retry ratio.
- Local application queue depth.
- Time since last valid observation.

Do not expose future simulation events.

### 11.2 Oracle telemetry

May inspect internal simulator state, including exact queue occupancy or future replay information.

Oracle telemetry is for upper bounds and debugging only.

### 11.3 Probe application

Implement a lightweight probe protocol:

```text
Probe request:
    sequence
    path_id
    sender_timestamp

Probe reply:
    sequence
    path_id
    sender_timestamp
    receiver_arrival_timestamp
```

Requirements:

- Send probes independently on each path.
- Receiver replies to the source address of each probe.
- Feedback must travel through the simulated network.
- The policy cannot use a measurement until its feedback packet arrives.
- Probe interval and packet size are configurable.
- Probe traffic must be included in overhead accounting.

------

## 12. Background Traffic

Support the following background applications:

- UDP constant-rate uplink.
- UDP bursty uplink.
- TCP bulk uplink.
- TCP bulk downlink.
- Mixed uplink and downlink.
- Synchronized bursts across bands.
- Independent bursts on each band.

Background STAs must support heterogeneous standards:

```text
VHT STA
HE STA
EHT STA
```

Each background STA configuration must include:

- Standard.
- Band.
- Distance.
- Traffic direction.
- Offered load.
- Start and stop time.
- Transport type.
- Channel width.
- PHY-rate configuration.

Initial experiments should use controlled fixed PHY rates. Rate adaptation is a separate sensitivity mode.

------

## 13. Correlated Congestion Model

Implement a `CorrelatedLoadController`.

Use a latent common state plus link-specific states:

```text
load_0(t) = base_0 + common_state(t) + local_state_0(t)
load_1(t) = base_1 + common_state(t) + local_state_1(t)
```

At minimum, support:

```text
independent
common_bursts
mixed_common_and_independent
trace_replay
```

For synthetic correlated load:

- Use configurable ON/OFF state durations.
- Use deterministic ns-3 random streams.
- Allow common bursts to activate background senders on both bands.
- Allow link-specific bursts to affect only one band.

Do not model correlation merely by assigning the same random seed to two independent random variables. The common event process must be explicit.

Later extensions may add non-Wi-Fi waveform interference, but this is not required for the MVP.

------

## 14. Propagation and PHY Configuration

Use `SpectrumWifiPhy` for all multi-link experiments.

Expose per-band configuration:

```text
frequency band
channel number
channel width
transmit power
noise figure
CCA threshold
propagation-loss model
propagation-delay model
MCS or station manager
guard interval
aggregation limits
retry limits
queue limits
```

Initial propagation modes:

```text
fixed_rss
log_distance
log_distance_with_shadowing
trace_driven_state
```

`fixed_rss` is required for deterministic tests.

`log_distance` is required for near/far experiments.

Do not use a complex fading model until deterministic and static-propagation tests pass.

------

## 15. Queue and Aggregation Controls

Expose at least:

- MAC queue maximum delay.
- MAC queue maximum packet count or byte count.
- A-MPDU maximum size.
- A-MSDU maximum size.
- Block Ack setup.
- Retry limits.
- EDCA access category.
- TXOP limit.

Real-time traffic should use a configurable QoS access category.

The default experiment configuration must not rely on ns-3 defaults for queue limits or TXOP limits. Record all explicit values.

------

## 16. Native MLO Baselines

### 16.1 STR MLO

The STR baseline must:

- Use two EHT links.
- Permit simultaneous operation where supported.
- Use native ns-3 MLO queueing and link selection.
- Use one application socket.
- Record per-link successful MPDUs and PHY occupancy.
- Record the selected TID-to-link configuration.
- Avoid custom application-level duplication.

### 16.2 EMLSR

The EMLSR baseline must:

- Be independently enabled or disabled.
- Record every main-PHY switch where available.
- Record switching delay and reason.
- Verify that only the permitted radio operation occurs.
- Be excluded from mandatory CI if ns-3 instability prevents deterministic execution.

### 16.3 Uplink OFDMA

Implement uplink OFDMA only after SLO and MLO baselines work independently.

Expose:

- `RrMultiUserScheduler`.
- `EnableUlOfdma`.
- `EnableBsrp`.
- `AccessReqInterval`.
- Number of scheduled stations.
- RU-allocation-related parameters exposed by ns-3.

Do not combine native MLO and uplink OFDMA in the first implementation phase. Validate each independently before evaluating their interaction.

Do not implement or claim realistic UL MU-MIMO.

------

## 17. Instrumentation

### 17.1 Source-of-truth hierarchy

Use the following precedence:

1. Custom application frame and packet records.
2. `WifiTxStatsHelper`.
3. `WifiCoTraceHelper`.
4. Selected MAC/PHY trace callbacks.
5. PCAP only for debugging.

FlowMonitor and PCAP must not be used as the primary source of frame-completion latency.

### 17.2 Required application metrics

Per frame:

```text
run_id
frame_id
generation_time_us
frame_size_bytes
packet_count
frame_type
deadline_us
policy
primary_link
duplicated
decision_time_us
predicted_delay_link_0
predicted_delay_link_1
union_first_packet_us
union_completion_us
union_latency_us
copy_0_completion_us
copy_1_completion_us
unique_packets_received
duplicate_packets_received
deadline_miss
incomplete
completion_mode
```

### 17.3 Required link metrics

Per sampling interval and link:

```text
timestamp
link_id
application_bytes_sent
application_bytes_received
redundant_bytes
probe_bytes
successful_mpdus
failed_mpdus
retransmissions
mean_mpdu_service_time
p95_mpdu_service_time
queue_bytes
estimated_rate
phy_idle_time
phy_cca_busy_time
phy_tx_time
phy_rx_time
```

### 17.4 Required run summary

Compute:

- Frame latency P50.
- Frame latency P90.
- Frame latency P95.
- Frame latency P99.
- Frame latency P99.9 when statistically defensible.
- Deadline-miss ratio.
- Incomplete-frame ratio.
- Mean and maximum tail-event burst length.
- Application goodput.
- Redundant-byte ratio.
- MAC-byte overhead.
- Airtime overhead.
- Probe overhead.
- Background-flow throughput.
- Background-flow tail latency where applicable.
- Joint link-delay exceedance probability.
- Cross-link delay correlation.
- Duplicate recovery rate.
- Fraction of duplicated frames that provided no latency benefit.

Do not report P99.9 when the sample contains fewer than approximately 100 expected observations beyond that quantile.

------

## 18. Output Files

Each run must create:

```text
results/<experiment>/<run_id>/
├── resolved_config.json
├── build_info.json
├── frames.csv
├── policy_decisions.csv
├── link_intervals.csv
├── mac_summary.csv
├── summary.json
├── stdout.log
└── optional/
    ├── packet_events.csv
    ├── mac_events.csv
    └── pcap/
```

`build_info.json` must contain:

```text
ns-3 version
ns-3 git commit
project git commit
compiler
build profile
execution timestamp
host information
```

High-volume packet and MAC event logs must be disabled by default.

------

## 19. Configuration and Experiment Runner

Use YAML for human-authored experiment configuration.

Example:

```yaml
experiment: dual_link_basic
topology: dual_interface
duration_s: 60
warmup_s: 5

stream:
  source: trace
  trace_file: experiments/traces/4k30.csv
  packet_payload_bytes: 1200
  emission_mode: burst
  default_deadline_us: 33333

policy:
  type: ewma_threshold
  duplication_threshold_us: 12000
  probe_interval_us: 10000
  redundancy_budget_mbps: 20

links:
  - id: 0
    band: 2.4GHz
    width_mhz: 20
    mcs: HeMcs5
  - id: 1
    band: 5GHz
    width_mhz: 80
    mcs: HeMcs8

background:
  common_burst_ratio: 0.5
  stations:
    - standard: VHT
      band: 5GHz
      direction: uplink
      transport: tcp
      distance_m: 5

runs:
  seeds: [1, 2, 3, 4, 5]
```

`run_experiments.py` must:

1. Load YAML.
2. Expand parameter sweeps.
3. Generate an immutable resolved configuration.
4. Construct the ns-3 command line.
5. Run configurations in parallel with a configurable worker count.
6. Capture stdout and stderr.
7. Reject duplicate completed run IDs.
8. Resume incomplete experiment batches.
9. Validate output files.
10. Produce an experiment-level manifest.

Do not require a JSON or YAML parser inside the C++ simulation executable. Python may translate resolved configuration into explicit command-line arguments.

------

## 20. Reproducibility

Requirements:

- Assign explicit ns-3 random streams to every random component.
- Record the global seed and run number.
- Use paired seeds across policies.
- Avoid changing stream assignment when adding unrelated components.
- Derive `run_id` from:
  - Resolved configuration.
  - Seed.
  - ns-3 commit.
  - project commit.
- Exclude warm-up traffic from reported metrics.
- Keep calibration runs separate from evaluation runs.
- Use run-level or block bootstrap confidence intervals, not an IID frame-level bootstrap.
- Preserve temporal order when analysing tail-event burstiness.

------

## 21. Calibration Workflow

Calibration must be performed hierarchically.

### Stage 1: PHY and throughput

Match:

- Approximate RSSI.
- Sustainable throughput.
- PHY rate or MCS.
- Retry ratio.
- Airtime utilization.

### Stage 2: packet service behavior

Match:

- MAC service-time distribution.
- Retransmission distribution.
- Queueing behavior.
- Burst duration of congestion events.

### Stage 3: frame behavior

Match:

- Frame-completion latency CDF.
- P95 and P99.
- Deadline-miss ratio.
- Consecutive delayed-frame burst lengths.

### Stage 4: cross-link behavior

Match:

- Delay correlation.
- Joint tail-event probability.
- Frequency and duration of common congestion periods.

Matching average throughput alone is not sufficient.

Store calibration parameters separately from experiment-policy parameters.

------

## 22. Testing Requirements

### 22.1 Unit tests

Implement tests for:

- Header serialization.
- Trace parsing.
- Frame packetization.
- Out-of-order reassembly.
- Duplicate suppression.
- Incomplete-frame timeout.
- Deadline classification.
- Policy threshold decisions.
- Redundancy token bucket.
- Deterministic random streams.
- Correlated-load state transitions.

### 22.2 Integration tests

#### Test A: uncongested single link

Expected:

- Every frame completes.
- No duplicates.
- Zero deadline misses under a generous deadline.

#### Test B: full duplication with one failed link

Expected:

- Frames complete through the surviving link.
- Duplicate/recovery statistics are correct.

#### Test C: duplicate suppression

Expected:

- Receiving two copies of every packet does not double the reconstructed frame size.

#### Test D: overloaded primary link

Expected:

- Frame latency and queueing increase with offered load.
- Fixed secondary link performs better when configured with adequate capacity.

#### Test E: correlated congestion

Expected:

- Common bursts create measurable joint tail events.
- Independent mode produces lower joint exceedance than common-burst mode.

#### Test F: STR MLO packet delivery

Expected:

- Multi-link setup succeeds.
- Application traffic is delivered.
- Per-link MAC activity is visible.

#### Test G: EMLSR smoke test

Expected:

- Setup succeeds for a known supported configuration.
- Main-PHY switching events are observed.
- Failure should be reported clearly rather than silently ignored.

------

## 23. Implementation Phases

### Phase 0: Build and repository scaffold

Deliver:

- ns-3.48 pinned.
- `contrib/wifi-streaming` module.
- Build scripts.
- One empty example executable.
- Unit-test registration.
- Commit/version capture.

Acceptance criterion:

```text
./ns3 configure --enable-examples --enable-tests
./ns3 build
./test.py -s wifi-streaming
```

must succeed.

### Phase 1: Single-link frame experiment

Implement:

- Frame trace source.
- Synthetic frame source.
- Streaming header.
- Packetizer.
- UDP sender.
- Frame receiver.
- `frames.csv`.
- Single-link topology.

Acceptance criterion:

A deterministic fixed-RSS test produces the expected frame count, packet count, and latency.

### Phase 2: Dual-interface data plane

Implement:

- Two sender sockets.
- Per-device binding.
- Dual-radio AP topology.
- `FixedLinkPolicy`.
- `FullDuplicationPolicy`.
- Duplicate reassembly.

Acceptance criterion:

Traffic can be forced independently through either interface, and full duplication produces correct union completion results.

### Phase 3: Instrumentation

Implement:

- `WifiTxStatsHelper`.
- `WifiCoTraceHelper`.
- Link-interval aggregation.
- Summary generation.
- Run metadata.
- Output validator.

Acceptance criterion:

Application, MAC, and occupancy accounting are internally consistent within documented tolerances.

### Phase 4: Background and correlated load

Implement:

- Heterogeneous background STAs.
- UDP/TCP traffic.
- Independent and common burst controllers.
- Near/far placement.

Acceptance criterion:

Increasing background load produces monotonic degradation in at least the controlled fixed-rate sanity scenarios.

### Phase 5: Native STR MLO

Implement:

- Two-link AP MLD and STA MLD.
- Static multi-link setup.
- Native application traffic.
- Per-link statistics.
- STR configuration output.

Acceptance criterion:

The same trace can run under SLO, dual-interface, and STR MLO using a common output schema.

### Phase 6: EMLSR and uplink OFDMA

Implement independently:

- EMLSR baseline.
- UL OFDMA baseline.

Do not initially combine them.

Acceptance criterion:

Each mode passes a documented smoke-test scenario or is explicitly marked unsupported because of a reproducible ns-3 limitation.

### Phase 7: Telemetry and adaptive policy

Implement:

- Probe request/reply.
- Causal telemetry snapshots.
- `MinRecentDelayPolicy`.
- `EwmaThresholdPolicy`.
- Redundancy budget.
- Policy-decision logging.

Acceptance criterion:

The adaptive policy responds to an induced link degradation without accessing future state.

### Phase 8: Batch experiments and analysis

Implement:

- YAML sweep expansion.
- Parallel runs.
- Resume support.
- Statistical aggregation.
- Latency CDF.
- Deadline-miss plots.
- Latency versus airtime-overhead Pareto plot.
- Cross-link correlation analysis.

Acceptance criterion:

A complete experiment batch can be reproduced from one configuration file and one command.

------

## 24. Initial Experiment Matrix

Run the following restricted matrix before broad parameter sweeps.

### Topologies

```text
SLO 2.4 GHz
SLO 5 GHz
dual-interface fixed 2.4 GHz
dual-interface fixed 5 GHz
dual-interface full duplication
dual-interface adaptive duplication
native STR MLO
native EMLSR
```

### Traffic

```text
4K30 trace-driven source
burst packet emission
UDP transport
configurable 16 ms and 33.3 ms latency thresholds
```

### Background conditions

```text
no background traffic
5 GHz-only background traffic
2.4 GHz-only background traffic
independent dual-band traffic
synchronized dual-band bursts
mixed VHT and HE uplink STAs
```

### Primary result plots

1. Frame-completion latency CDF.
2. Deadline-miss ratio versus offered load.
3. P99 latency versus redundant airtime.
4. Deadline-miss ratio versus redundant airtime.
5. Background throughput degradation versus streaming latency.
6. Duplication benefit versus cross-link tail correlation.
7. Consecutive deadline-miss burst-length distribution.

------

## 25. Definition of MVP Completion

The MVP is complete when:

- A measured video frame trace can be replayed.
- Per-frame completion latency is correctly recorded.
- Two independent interfaces can be explicitly selected.
- Complete frames can be duplicated and deduplicated.
- Single-link and full-duplication baselines work.
- Background traffic can be added independently on each band.
- Correlated traffic bursts can be generated.
- STR MLO runs through the same experiment interface.
- All resolved parameters and versions are archived.
- Repeated runs are deterministic for a fixed seed.
- Unit and integration tests pass.
- One command generates validated per-run outputs and aggregate plots.

EMLSR, UL OFDMA, and adaptive prediction are not required to declare the core harness functional.

------

## 26. Constraints for the Coding Agent

1. Keep simulation mechanism, policy logic, and analysis code separate.
2. Do not modify ns-3 core unless an extension point is demonstrably insufficient.
3. Do not hard-code topology or PHY parameters.
4. Do not treat PCAP as authoritative for MLO.
5. Do not silently remove incomplete frames.
6. Do not use future simulator state in deployable policies.
7. Do not implement WebRTC or codec processing in the first version.
8. Do not enable rate adaptation until fixed-rate tests are validated.
9. Do not combine MLO, EMLSR, OFDMA, adaptive scheduling, mobility, and complex fading in the first implementation.
10. Every implementation phase must add tests before the next mechanism is introduced.
11. Every result must be traceable to a resolved configuration, seed, and code revision.
12. Prefer a correct restricted model over a large environment whose behavior cannot be validated.