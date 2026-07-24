# Specification: Causal Wi-Fi Frame Latency-Risk Prediction Study

**Status:** Revised implementation specification  
**Revision:** 6, 2026-07-24
**Scope:** Increments 1 through 3  
**Target repository:** `wifi_streaming_simulation`  
**Target ns-3 version:** Existing pinned ns-3.48 revision

## 1. Goal

Determine whether causal sender-side Wi-Fi telemetry can predict video-frame
deadline misses early enough to justify later selective redundancy or dropping
research.

The work has three increments:

1. Add passive telemetry to ns-3.
2. Generate and validate fixed-link datasets offline.
3. Evaluate heuristics and prediction models offline.

No increment changes transmission decisions. Adaptive duplication, delayed
duplication, dropping, path switching, queue control, MLO/OFDMA prediction, and
online inference are excluded.

For frame `f`:

```text
Y_f = 1 if the frame completes after its deadline or never completes
Y_f = 0 otherwise
```

At sample time `t`, the model estimates risk using only state available no
later than `t`.

## 2. Existing contracts

- Existing frame generation, packetization, sender policies, receiver
  reassembly, and metrics semantics remain unchanged.
- Existing output schemas remain unchanged.
- `frames.csv` remains the sole source of final completion and miss labels.
- Prediction telemetry is disabled by default.
- Disabled telemetry creates no prediction files and does not change frame
  results.
- Unsupported telemetry is empty/null, never zero.
- The collector never changes queue, retry, rate, path, or policy behavior.
- PCAP, FlowMonitor, and general packet logging remain disabled.

## 3. Architecture

```text
FrameSource / MultipathSender
        |
        | StreamingFrameTag
        v
UDP / IP / Wi-Fi MAC and PHY
        |
        +--> PredictionTelemetryCollector
        |       - frame progress
        |       - bounded MAC/PHY histories
        |       - queue and radio state
        |       - optional causal oracle state
        |
        +--> prediction_samples.csv
        +--> prediction_events.csv (optional)

FrameReceiver / MetricsCollector --> frames.csv

prediction_samples.csv + frames.csv
        |
        v
offline dataset builder, validator, and prediction evaluation
```

The simulator emits observations only. Labels and models remain offline.

# Increment 1: Telemetry instrumentation

## 4. Acceptance boundary

Increment-1 acceptance uses:

```text
topology                 = dual_interface
policy                   = fixed_link_0 or fixed_link_1
target                   = 802.11be, fixed EHT MCS 5
MLO and UL OFDMA         = disabled
A-MSDU                   = disabled
target packet fragmentation = disabled
adaptive actions         = disabled
```

A-MPDU may remain enabled. The validator shall reject telemetry runs that
enable target A-MSDU or permit target application-packet fragmentation.

For target streaming traffic, every packet produced from the packetization
plan shall map to exactly one MAC MSDU and one stable QoS data MPDU identity.
Retransmissions retain that identity. A-MPDU may group several such MPDUs into
one PPDU but shall not merge their identities. Reject a configuration or trace
path if this one-to-one mapping cannot be established.

The stable logical packet identity is:

```text
(run_id, frame_id, path_id, copy_id, packet_index)
```

Do not use `Packet::GetUid()` as the sole cross-layer identity.

## 5. `StreamingFrameTag`

Add an internal ns-3 `Tag`:

```cpp
uint64_t frameId;
uint8_t pathId;
uint8_t copyId;
uint32_t packetIndex;
uint32_t packetCount;
uint64_t generationTimeNs;
uint64_t deadlineTimeNs;
uint32_t frameSizeBytes;
uint8_t frameType;
```

Requirements:

- Attach immediately before submission to the selected UDP socket.
- Do not alter simulated wire bytes.
- `pathId` is the application path, not an internal Wi-Fi link ID.
- Fixed-link runs use `copyId = 0`.
- Preserve identity through socket submission, MAC queueing, A-MPDU, PHY
  transmission, and MPDU outcome callbacks where technically available.
- Populate `packetIndex` and `packetCount` from the immutable packetization
  plan.
- Test registration, serialization, deserialization, copying, and printing.
- Do not repeatedly parse `StreamingHeader` in MAC callbacks.
- If exact attribution is unavailable, record link-level state or null rather
  than inventing frame attribution.

For dual-interface runs, explicitly bind:

```text
(application path ID, WifiNetDevice, internal PHY link ID)
```

Both single-link devices may internally use link ID 0; this must not collapse
application paths 0 and 1.

## 6. `PredictionTelemetryCollector`

Add a collector independent of `MetricsCollector` and sender policy logic. It
shall:

- register each generated frame and selected path;
- observe packet submission, queue lifecycle, and TX outcomes;
- maintain frame-specific sender progress;
- maintain bounded per-link MAC and PHY histories;
- query current queue, radio, and optional oracle state;
- capture immutable snapshots;
- stream optional raw events;
- write deterministically ordered samples;
- never influence simulation decisions.

Track per frame:

```text
identity, path, generation and deadline
frame size, type, and packet count
packets/bytes submitted
packets enqueued/dequeued
packets acknowledged, failed, and terminally dropped
successful acknowledgement state for every application packet
MAC service bytes for every application packet
```

Track per path:

```text
cumulative MPDU attempts, successes, unsuccessful attempts, retries, drops
PPDU transmissions
bounded recent MAC events
bounded PHY state intervals
tagged queued packets
last attempt and last success
```

All per-path state refers to the selected target sender device. It must not
combine target telemetry with background-node counters. PHY occupancy is
measured as sensed by that target PHY.

Retain only the largest configured history window plus a guard interval.

## 7. Sampling contract

Default offsets:

```text
T0 = 0 us
T1 = 1000 us
T2 = 2000 us
T4 = 4000 us
```

Offsets are configurable, strictly increasing, unique, nonnegative, and less
than the frame deadline.

### 7.1 T0 ordering

Add a pure packetization-planning operation. A plan contains:

```text
packet count
packet indexes
application payload bytes per packet
expected MAC service bytes where determinable
emission offsets
copy and path metadata needed to materialize tags and headers
```

Planning may calculate sizes and offsets and allocate ordinary C++ metadata.
It must not submit a socket packet, enqueue a MAC packet, schedule a
zero-delay send event, or otherwise change simulation state.

Capture T0 synchronously:

```text
generate frame
select fixed path
compute immutable packetization plan
register collector state
capture T0
materialize packets from the plan
submit or schedule the planned packets
```

Do not rely on ordering between separate zero-delay events. T0 may include
background events already processed at the same timestamp, but no event caused
by the new frame. `frame_packet_count` and
`packets_remaining_to_submit` at T0 come from the plan, not from observed
packet submissions. Packet materialization shall not recalculate a different
packet count or emission schedule.

At T0, `frame_mac_service_bytes_not_acknowledged` and
`frame_mac_service_bytes_pending_primary` are populated only if the plan can
calculate exact MAC service bytes from the fixed protocol stack. Both include
planned packets that have not yet been submitted. Validate planned per-packet
MAC service bytes against that packet's first MAC enqueue. If exact agreement
cannot be guaranteed, leave both byte fields null at T0 and do not run the
byte-service queue heuristic for that row.

### 7.2 Non-T0 event ordering

For every non-T0 snapshot, the snapshot callback is the causal boundary. The
snapshot includes same-timestamp feature events whose callbacks ns-3 has
already processed before the snapshot callback and excludes same-timestamp
events processed afterward. Do not reorder or reconstruct same-timestamp
events to create a simultaneous-state closure. Tests shall schedule feature
events immediately before and after a snapshot at the same simulation time and
verify this behavior.

### 7.3 Receiver-independent rows

Snapshot generation must never depend on `FrameReceiver`.

Emit every configured pre-deadline snapshot and include:

```text
sender_mac_complete
actionable
```

Definitions:

```text
sender_mac_complete = true
    only after positive MAC acknowledgement of every
    application packet in the frame.

actionable = false
    when sender_mac_complete is true or sample_time >= deadline_time.
```

Retry-limit, lifetime, or queue drops do not mark a frame complete. Receiver
completion and packet arrivals remain offline label data.

`sender_mac_complete` and `actionable` are eligibility metadata, not model
features. Increment 3 evaluates only actionable rows.

Every sample records `latest_feature_event_time_ns`, which must satisfy:

```text
latest_feature_event_time_ns <= sample_time_ns
```

## 8. Counter semantics

### 8.1 MPDU attempts

Count one attempt for each MPDU included in an actual PHY transmission. An
A-MPDU containing ten MPDUs contributes ten attempts and one PPDU.

### 8.2 MPDU successes

Count one success when an MPDU is positively acknowledged by ACK or Block Ack.

### 8.3 MPDU attempt failures

Count one unsuccessful attempt when a transmitted MPDU is not acknowledged by
the corresponding exchange, including ACK timeout, missing Block Ack, or a
negative/missing Block Ack bitmap entry. This is not a terminal loss.

### 8.4 MPDU retries

Count an attempt as a retry only when the same MPDU had a previous
unsuccessful attempt.

### 8.5 Terminal drops

Count an MPDU abandoned without successful acknowledgement. Split where
observable:

```text
mpdu_retry_limit_drops
mpdu_lifetime_drops
mpdu_queue_drops
```

Do not combine terminal drops with unsuccessful attempts.

### 8.6 Trace de-duplication

A failed exchange may emit both timeout and MPDU outcome callbacks. Maintain
per-attempt state and finalize each attempt once. Unit tests shall cover ACK,
Block Ack, timeout, retry, and terminal-drop paths.

### 8.7 MAC service-byte domain

Use explicit byte domains:

```text
encoded_video_payload_bytes
    Bytes generated for the encoded frame before packetization.

application_socket_packet_bytes
    Packet::GetSize() immediately before Socket::Send(). This includes the
    encoded-video chunk and StreamingHeader but excludes UDP/IP and
    lower-layer headers.

udp_ip_packet_bytes
    Packet bytes after UDP/IP encapsulation but before LLC/SNAP and Wi-Fi
    encapsulation. This domain is not emitted unless a field explicitly names
    it.

mac_service_bytes
    WifiMpdu::GetPacketSize() at the first MAC enqueue.

complete_mpdu_bytes
    WifiMpdu::GetSize(), including the Wi-Fi MAC header and FCS.
```

`mac_service_bytes` is the packet stored in the MPDU. It excludes the Wi-Fi MAC
header, FCS, A-MPDU delimiters and padding, and PHY preamble, but includes all
encapsulated upper-layer headers present at the MAC boundary. It is a
consistent service-work proxy, not literal airtime or complete on-air bytes.

Use the MAC service-byte domain for:

```text
frame_mac_service_bytes_currently_queued
mac_queue_service_bytes
mac_service_bytes_ahead_of_frame
frame_mac_service_bytes_not_acknowledged
frame_mac_service_bytes_pending_primary
acknowledged_mac_service_bytes
```

The target MAC queue is the selected sender device's QoS container queue
identified by the target traffic's access category, TID, receiver, and selected
application path. Every queue field shall use this exact scope.

In ns-3.48, `WifiMacQueue::GetNBytes()` is maintained from
`WifiMpdu::GetSize()`. It shall not directly populate
`mac_queue_service_bytes`. Calculate `mac_queue_service_bytes` by summing
`WifiMpdu::GetPacketSize()` over the exact target queue. If the native queue
quantity is also retained, name it `mac_queue_complete_mpdu_bytes` and never
substitute it for a MAC service-byte field.

The feature dictionary shall identify every byte field as encoded-video
payload, application socket packet, UDP/IP packet, MAC service, complete MPDU,
PSDU, or on-air quantity. Do not combine domains in one estimator. A field
named only `bytes` is prohibited unless an unchanged external schema requires
it and the dictionary states its domain.

### 8.8 MPDU latency observations

Record two distinct successful-MPDU observations:

```text
mpdu_queue_to_ack_time_us =
    positive_ack_time - mac_enqueue_time

mpdu_first_attempt_to_ack_time_us =
    positive_ack_time - first_phy_attempt_time
```

Queue-to-ACK includes queue residence, aggregation waiting, contention, and
retries. First-attempt-to-ACK excludes initial queue residence but includes
the retry process after the first attempt.

For A-MPDU, each acknowledged constituent MPDU produces its own observation.
MPDUs acknowledged by the same Block Ack can share an ACK timestamp. Assign a
completed observation to a rolling window by its positive ACK timestamp, not
its enqueue or first-attempt timestamp.

## 9. Rolling histories

Calculate exact 1 ms, 5 ms, and 20 ms windows in ns-3. For sample time `t` and
window `w`, include events in:

```text
(t - w, t]
```

At the upper endpoint `t`, include only events processed before the snapshot
callback under Section 7.2.

For each window label `{window}` in `1ms`, `5ms`, and `20ms`, emit:

```text
mpdu_attempts_{window}
mpdu_successes_{window}
mpdu_attempt_failures_{window}
mpdu_retries_{window}
mpdu_retry_ratio_{window}
acknowledged_mac_service_bytes_{window}
mpdu_queue_to_ack_mean_{window}_us
mpdu_queue_to_ack_p95_{window}_us
mpdu_first_attempt_to_ack_mean_{window}_us
mpdu_first_attempt_to_ack_p95_{window}_us
phy_tx_time_{window}_us
phy_rx_time_{window}_us
phy_busy_time_{window}_us
phy_idle_time_{window}_us
phy_other_time_{window}_us
phy_tx_fraction_{window}
phy_rx_fraction_{window}
phy_busy_fraction_{window}
phy_idle_fraction_{window}
phy_other_fraction_{window}
history_coverage_{window}_us
```

Start telemetry history during the existing warm-up. Clip PHY intervals
exactly at window boundaries. Durations sum to available coverage within
timestamp tolerance. A short coverage interval is not equivalent to zero
activity. When coverage exists but no matching events occur, event counters
and acknowledged service bytes are zero.

Define:

```text
mpdu_retry_ratio_{window} = null
    when mpdu_attempts_{window} = 0
```

Latency means and percentiles are null when the window contains no applicable
successful-MPDU observations. PHY fractions are null when
`history_coverage_{window}_us` is zero.

Use the linear-interpolation percentile convention also known as Hyndman-Fan
type 7 and NumPy `method="linear"`. For sorted observations
`x[0] ... x[n-1]`, percentile probability `q`, and
`h = (n - 1) * q`, interpolate linearly between `x[floor(h)]` and
`x[ceil(h)]`. Use `q = 0.95` for every rolling P95.

`phy_busy` means `WifiPhyState::CCA_BUSY` only. `phy_other` is the sum of
`SWITCHING`, `SLEEP`, and `OFF`. Validate:

```text
TX + RX + CCA_BUSY + IDLE + OTHER = history coverage
```

`acknowledged_mac_service_bytes_{window}` counts each successfully
acknowledged MPDU once, at its ACK timestamp. Retransmitted bytes are not added
again; their cost is reflected in the lower achieved byte service rate.

## 10. Feature tiers

### F0: application-only

- frame size, type, packet count, age, and deadline slack;
- submitted application packets and socket bytes, and remaining application
  packets.

### F1-ideal: idealized commodity-statistic-equivalent telemetry

- aggregate and rolling MPDU outcomes;
- rolling PHY occupancy;
- current/recent rate, MCS, NSS, width, and guard interval;
- ACK signal where available;
- last-attempt and last-success age.

F1-ideal uses signal types related to commodity statistics but observes them
with exact ns-3 timestamps and short windows. It is an optimistic observability
upper bound, not a claim that ordinary Linux interfaces provide the same
precision, latency, or update frequency.

Exact internal Wi-Fi MAC queue occupancy is not F1-ideal.

### F2: modified-driver-equivalent telemetry

- target MAC queue packets, MAC service bytes, and oldest age;
- frame-specific queue and TX progress;
- frame packets/MAC service bytes currently queued;
- packets/MAC service bytes ahead of the frame;
- unacknowledged and primary-pending frame work;
- per-MPDU service and retry state;
- aggregation or internal driver queue state where supported.

### F3: causal ns-3 oracle

- current contention window and exact remaining backoff where a passive,
  validated source exists;
- NAV remaining time and exact PHY state;
- current channel-access status;
- whether the channel-access manager currently considers the medium busy;
- categorical current-state access-within-slack reason only if its complete
  call path is behaviorally passive.

F3 excludes future random draws, traffic, channel events, ACKs, and outcomes.
Do not add a vague interference-state field. Any later interference feature
requires a precise physical quantity and verified source.

## 11. Feature dictionary and support mask

Provide a normative dictionary for every field:

```text
field name
exact ns-3 source
tier
unit
byte domain where applicable
categorical vocabulary where applicable
instantaneous/cumulative/windowed semantics
update timing
whether zero is meaningful
unsupported conditions
Linux analogue
commodity or modified-driver availability
```

Candidate trace sources include target BE queue enqueue/dequeue/drop/expiry,
`PhyTxPsduBegin`, `AckedMpdu`, `NAckedMpdu`, `DroppedMpdu`, response timeouts,
PHY state transitions, TX vectors, ACK/BA receive outcomes, `QosTxop` CW and
backoff, and channel-access NAV state. Verify exact ns-3.48 callback semantics
before assigning a field.

Represent `feature_support_mask` as a canonical variable-length hexadecimal
string:

```text
0x0
0x1f
0x10000000000000001
```

Bit 0 is the least-significant bit. Use lowercase hexadecimal, a `0x` prefix,
and no unnecessary leading zeroes. The bit-to-feature-family mapping is
documented and versioned. Changing a bit meaning requires a support-mask
mapping version change and also a telemetry schema version change when the
sample field contract changes. Unsupported fields remain null even when the
mask is present.

## 12. Output schemas

### 12.1 `prediction_samples.csv`

Create only when telemetry is enabled. Sort by sample time, frame ID, path,
and offset.

Required identity and timing:

```text
telemetry_schema_version, run_id, frame_id, path_id, copy_id
sample_stage, sample_offset_us, sample_time_ns
latest_feature_event_time_ns, generation_time_ns, deadline_time_ns
frame_age_us, deadline_slack_us, sender_mac_complete, actionable
```

Required F0:

```text
frame_size_bytes, frame_packet_count, frame_type
packets_submitted, application_socket_packet_bytes_submitted
packets_remaining_to_submit
```

`frame_size_bytes` is encoded-video payload before packetization.
`application_socket_packet_bytes_submitted` is the sum of
`Packet::GetSize()` immediately before socket submission. It includes each
`StreamingHeader` and excludes UDP/IP and lower-layer headers.
F0 does not contain a remaining-byte field; the explicit remaining-work field
is `packets_remaining_to_submit`.

Required F1-ideal cumulative state:

```text
mpdu_tx_attempts_total, mpdu_tx_successes_total
mpdu_tx_attempt_failures_total, mpdu_retries_total
mpdu_terminal_drops_total, mpdu_retry_limit_drops_total
mpdu_lifetime_drops_total, mpdu_queue_drops_total, ppdu_tx_count_total
last_tx_attempt_time_ns, last_tx_success_time_ns
current_mcs, current_nss, current_channel_width_mhz
current_guard_interval_ns, frequency_band, center_frequency_mhz
current_ack_signal_dbm
```

Also include all rolling fields with the exact names defined in Section 9,
including `mpdu_retries_5ms`, `mpdu_queue_to_ack_mean_20ms_us`,
`acknowledged_mac_service_bytes_20ms`, `phy_busy_fraction_20ms`, and
`phy_other_fraction_20ms`.

Required F2:

```text
frame_packets_mac_enqueued, frame_packets_mac_dequeued
frame_packets_tx_succeeded, frame_mpdu_attempt_failures
frame_packets_terminally_dropped
frame_packets_currently_queued
frame_mac_service_bytes_currently_queued
mac_queue_packets, mac_queue_service_bytes
mac_queue_oldest_enqueue_time_ns
packets_ahead_of_frame, mac_service_bytes_ahead_of_frame
frame_packets_pending_primary
frame_mac_service_bytes_not_acknowledged
frame_mac_service_bytes_pending_primary
```

`frame_packets_tx_succeeded` counts distinct application packets with positive
MAC acknowledgement and cannot exceed `frame_packet_count`.
`frame_mpdu_attempt_failures` counts unsuccessful MPDU attempts attributed to
the frame and may exceed its packet count.

Definitions:

```text
frame_mac_service_bytes_not_acknowledged
    All planned frame MAC service bytes without positive MAC
    acknowledgement. This includes terminally dropped packets.

frame_mac_service_bytes_pending_primary
    Unacknowledged MAC service bytes still expected to consume primary-path
    service. This includes planned but not yet submitted packets, queued
    packets, in-flight packets, and packets eligible for retry. It excludes
    packets terminally dropped or otherwise permanently removed from the
    primary path.

frame_packets_pending_primary
    Packet-count analogue of frame_mac_service_bytes_pending_primary.
```

Both byte fields start from the plan's total MAC service bytes.
`frame_mac_service_bytes_not_acknowledged` decreases only on positive MAC
acknowledgement. `frame_mac_service_bytes_pending_primary` decreases on
positive acknowledgement or terminal removal from the primary path; it does
not decrease on dequeue, transmission, or a nonterminal failed attempt.
Until the MAC service size of every packet contributing to a field is known
and validated, that field is null rather than a partial byte sum.

`frame_packets_terminally_dropped > 0` is a separate strong causal risk signal.
It is not a final miss label: an MPDU may have reached the receiver even when
its positive acknowledgement was lost.

Ahead-of-frame fields are null unless current queue ordering makes them exact.
At T0, before the planned frame is enqueued, all packets currently ahead in a
provably FIFO target queue may be counted as ahead of the planned frame.

Required F3 schema columns:

```text
current_cw, remaining_backoff_slots, nav_remaining_us
current_phy_state, channel_access_status, medium_busy_now
expected_access_reason_within_slack
```

`channel_access_status` is the selected target traffic `QosTxop` status on the
selected link and uses the ns-3 `Txop::ChannelAccessStatus` values
`NOT_REQUESTED`, `REQUESTED`, and `GRANTED`. `medium_busy_now` comes from
`ChannelAccessManager::IsBusy()`. Do not add `medium_busy_until_ns` without a
verified API for one exact consolidated end time.

In ns-3.48, `QosTxop::GetBackoffSlots()` returns a lazily updated stored
counter, not an exact remaining-slot snapshot. Therefore
`remaining_backoff_slots` remains null in Increment 1. Supporting it later
requires a passive reconstruction of `ChannelAccessManager::UpdateBackoff()`
and dedicated slot-boundary tests.

`expected_access_reason_within_slack` is the categorical
`WifiExpectedAccessReason` returned by
`ChannelAccessManager::GetExpectedAccessWithin(deadline_slack)` when that query
is supported. Serialize one of these canonical tokens:

```text
ACCESS_EXPECTED
NOT_REQUESTED
NOTHING_TO_TX
RX_END
BUSY_END
TX_END
NAV_END
ACK_TIMER_END
CTS_TIMER_END
SWITCHING_END
NO_PHY_END
SLEEP_END
OFF_END
BACKOFF_END
```

Do not serialize the enum's underlying integer or rely on its stream insertion
spelling. The query considers current link-wide channel-access state and all
TXOPs registered with that `ChannelAccessManager`; it is not a promise that
the sampled frame itself will gain access.

In ns-3.48 this nominally `const` query calls
`QosTxop::HasFramesToTransmit()`, which can wipe expired queue entries and fire
traces. It is therefore not behaviorally passive.
`expected_access_reason_within_slack` remains null in Increment 1 and its
unsupported state is documented rather than invoking the query.

F3 fields are present but null when oracle collection is disabled. A field
without a verified passive source remains null when oracle collection is
enabled; a family support bit does not imply that every member is available.

Required provenance:

```text
feature_support_mask
```

The file must not contain receiver progress, final completion, final latency,
miss labels, future events, or final run summaries.

### 12.2 `prediction_events.csv`

Create only when explicitly enabled. Every row includes
`event_schema_version`. Stream or bounded-buffer rows. Include time, path,
frame, packet, attempt, queue, rate, and current MAC/PHY fields for events such
as:

```text
FRAME_REGISTERED, PACKET_SUBMITTED, MAC_ENQUEUE, MAC_DEQUEUE, MAC_DROP
MPDU_TX_ATTEMPT, MPDU_TX_SUCCESS, MPDU_TX_ATTEMPT_FAILURE
MPDU_RETRY, MPDU_TERMINAL_DROP, PPDU_TX, PHY_STATE_CHANGE
```

## 13. Configuration

Add:

```text
--predictionTelemetryEnabled=false
--predictionSampleOffsetsUs=0,1000,2000,4000
--predictionHistoryWindowsUs=1000,5000,20000
--predictionEventLogEnabled=false
--predictionOracleFeaturesEnabled=false
```

Schema versions are implementation-owned constants, not command-line or YAML
configuration:

```cpp
constexpr uint32_t PREDICTION_TELEMETRY_SCHEMA_VERSION = 1;
constexpr uint32_t PREDICTION_EVENT_SCHEMA_VERSION = 1;
constexpr uint32_t FEATURE_SUPPORT_MASK_VERSION = 1;
```

A user must not be able to relabel unchanged output with another semantic
version. Lists are strict, unique, ordered integers. Windows are positive.
Record all resolved values and all three source-owned versions under
`predictionTelemetry` in `resolved_config.json`.

Include enabled telemetry settings and source-owned versions in run identity.
A telemetry-disabled configuration must preserve historical run identity and
must not add default prediction arguments or prediction schema versions to the
identity hash.

## 14. Validation and tests

Conditionally extend the existing validator. Disabled telemetry preserves the
historical contract. Enabled telemetry requires samples and requires events
only when requested.

Validate schema, nulls, unique sample keys, fixed-path isolation, configured
times, pre-deadline rows, receiver-independent cardinality, causal timestamps,
nonnegative values, monotonic counters, actionable transitions, history
coverage, complete PHY duration sums including OTHER, MAC byte conservation,
primary-pending transitions, canonical support-mask encoding, oracle nulls,
categorical values, and ordering. Validate every byte field against its
declared byte domain. In particular,
`mac_queue_service_bytes` must not silently equal the native
complete-MPDU-byte queue counter.

Do not reject a sample because offline receiver completion occurred earlier.

Unit and integration tests shall cover:

- tag serialization and A-MPDU propagation;
- pure packetization planning and plan/materialization agreement;
- one logical streaming packet to one stable MPDU identity;
- path binding;
- strict configuration parsing;
- synchronous T0 and exact later stages;
- same-timestamp callbacks before and after non-T0 snapshots;
- receiver-independent sample presence;
- sender completion/actionability;
- MPDU accounting without duplicate failure counts;
- unacknowledged versus primary-pending packet and byte transitions;
- queue-to-ACK and first-attempt-to-ACK timing;
- ACK-timestamp rolling-window assignment;
- acknowledged MAC service-byte accounting;
- zero-attempt null ratios and type-7 rolling P95;
- rolling-window boundaries and PHY clipping;
- complete TX/RX/CCA_BUSY/IDLE/OTHER accounting;
- bounded history;
- queue-byte-domain invariants and canonical support-mask encoding;
- exact channel-access status, medium-busy state, and expected-access
  categorical serialization;
- source-owned, non-configurable schema versions;
- oracle-disabled nulls;
- deterministic output;
- no-background and controlled-background fixed-link runs;
- telemetry-on/off matched frame results;
- optional event output.

Benchmark telemetry disabled, samples only, and samples plus events using
interleaved single-worker repetitions. Report median wall time, output size,
row count, and peak RSS where available. Samples-only overhead should target
less than approximately 25 percent.

## 15. Increment-1 acceptance

Stop and review evidence before Increment 2. Increment 1 passes only if:

- existing tests pass;
- disabled behavior remains compatible;
- fixed-link smoke runs validate;
- samples are causal and independent of receiver state;
- MPDU accounting and bounded histories pass tests;
- unsupported data remains null;
- identical runs produce identical sample content;
- no model or adaptive action exists in C++.

# Increment 2: Dataset generation and validation

## 16. Objective

Create reproducible labelled datasets with sufficient deadline misses, causal
features, explicit provenance, and run-grouped splits. Increment 2 should not
change ns-3 unless Increment-1 validation reveals a telemetry defect.

## 17. Experiment matrices

### 17.1 Pilot and load tuning

Before production batches:

- run short fixed-link pilots;
- tune existing background rate and duty-cycle parameters with telemetry
  disabled;
- choose low, medium, and high load parameters from measured miss rates;
- freeze those values in dedicated prediction configurations.

Do not add a simulator scenario label to force a miss regime. The regime is
analysis metadata derived from resolved parameters and pilot results.

### 17.2 Stage A: same-BSS contention

Use fixed EHT MCS 5, `dual_interface`, and both fixed-link policies without MLO
or UL OFDMA.

Conditions:

1. no background;
2. `legacy_mixed8` with independent bursts;
3. `legacy_mixed8` with common bursts;
4. `legacy_mixed8` with mixed common and independent bursts.

Tune loaded cases toward:

```text
low:    1-3 percent misses
medium: 5-10 percent misses
high:   15-25 percent misses
```

Use multiple explicit seeds and run substreams.

### 17.3 Stage B: out-of-distribution data

Reserve initially for OOD evaluation:

1. `obss_only`: `mixed4x4` OBSS without same-BSS background;
2. `obss_plus_legacy_mixed8`: `mixed4x4` OBSS plus `legacy_mixed8`.

Use matched fixed links and the existing log-distance plus Nakagami model.
These stable scenario IDs are the default required OOD scenarios. Exact run
filters, loads, and required status are frozen in the analysis YAML before any
test evaluation.

### 17.4 Size targets

Target at least:

- 2,000 training misses;
- 500 misses in each major test subset;
- enough complete run groups and both classes in each validation subset;
- multiple independent runs per evaluated condition.

Frames are not independent experimental replicates. Runs are the statistical
unit.

## 18. Batch files

Add without changing existing matrices:

```text
experiments/configs/prediction_telemetry_smoke.yaml
experiments/configs/prediction_stage_a.yaml
experiments/configs/prediction_obss.yaml
experiments/configs/prediction_analysis.yaml
```

Production batches enable samples, disable event logging, and use explicit
seeds/runs. Oracle features may use a matched batch or all runs if smoke tests
show acceptable overhead and identical frame outcomes.

## 19. Dataset builder

Add:

```text
tools/build_prediction_dataset.py
```

Inputs:

- one or more batch roots or run directories;
- `prediction_samples.csv`;
- `frames.csv`;
- `resolved_config.json`;
- `build_info.json`;
- optional scenario filters;
- explicit output path and format.

Join on `run_id + frame_id` and verify the selected fixed path. Attach:

```text
dataset_schema_version
frame_complete
frame_completion_time_ns
frame_latency_us
deadline_miss
run_seed
run_number
scenario_name
background_profile
correlation_mode
selected_policy
run_group_id
```

Final labels are constant across snapshots. Incomplete frames miss; completed
frames use the existing `frames.csv` deadline and completion semantics.

`run_group_id`, identifiers, and scenario context are metadata and prohibited
model inputs.

Write:

```text
labelled_samples.parquet
dataset_manifest.json
dataset_validation.json
```

An explicit CSV fallback is allowed when Parquet dependencies are unavailable.
Record the fallback; never change format silently.

### 19.1 Offline derived features

Derive only from values present at the same sample or earlier:

```text
last_success_age_us =
    (sample_time_ns - last_tx_success_time_ns) / 1000

last_attempt_age_us =
    (sample_time_ns - last_tx_attempt_time_ns) / 1000

queue_oldest_age_us =
    (sample_time_ns - mac_queue_oldest_enqueue_time_ns) / 1000

frame_packets_not_acknowledged =
    frame_packet_count - frame_packets_tx_succeeded
```

`frame_packets_not_acknowledged` includes terminally dropped packets and
answers how many packets still lack sender confirmation. It is not a
primary-service-work estimate; use `frame_packets_pending_primary` for that
purpose.

If a source timestamp is null, the corresponding age is null. Reject negative
ages. Rolling-window fields are taken directly from Increment-1 samples; do
not approximate them from sparse snapshots.

## 20. Dataset validation

Add:

```text
tools/validate_prediction_dataset.py
```

Validate:

- every sample joins to exactly one unique frame;
- sample keys are unique;
- offsets and timestamps match resolved configuration;
- rows exist independently of receiver completion;
- every source event for a derived feature is no later than sample time;
- counters are nondecreasing and ages/durations are nonnegative;
- support masks agree with null/non-null fields;
- raw samples contain no prohibited outcomes;
- labels come only from `frames.csv`;
- model allowlists contain no IDs, context, final summaries, or outcomes;
- missingness, support rates, class balance, run counts, quantiles, constant
  features, and suspicious label correlations are reported.

`sender_mac_complete` and `actionable` remain eligibility metadata and are not
model features.

## 21. Manifest and provenance

Record:

- source roots, included run IDs, and rejected runs with reasons;
- project/ns-3 commits and build profile;
- telemetry, dataset, support-mask, and analysis schema versions;
- feature dictionary and tier assignment;
- exact label and stage definitions;
- run, frame, sample, and miss counts;
- per-split-role and per-OOD-scenario run-group, frame, and miss counts;
- commands, timestamps, dependencies, and source checksums.

Never silently skip an invalid run.

## 22. Run-grouped splits

Persist:

```text
splits.json
```

Partitions:

- training;
- `validation_selection`;
- `validation_calibration`;
- in-distribution test;
- out-of-distribution test.

Rules:

- keep every frame and snapshot from a run together;
- prohibit row-level and adjacent-frame splits;
- assign matched link-0/link-1 runs sharing scenario parameters, seed,
  substream, and background process one `run_group_id`;
- keep each matched group in one partition;
- keep `validation_selection` and `validation_calibration` disjoint by complete
  `run_group_id`;
- place Stage-B OBSS runs initially in OOD and preserve their stable scenario
  IDs;
- construct deterministically from an explicit seed;
- list exact run and group IDs;
- require both classes and the configured minimum run-group count in each
  validation and test subset or mark that subset insufficient.

Before constructing a split, freeze these stable keys in the versioned analysis
YAML:

```yaml
minimum_run_groups_validation_selection: 10
minimum_run_groups_validation_calibration: 10
minimum_run_groups_id_test: 20
minimum_run_groups_per_required_ood_scenario: 20
```

These values count complete `run_group_id` units after run validation and
partition eligibility filters. The OOD minimum applies separately to every
required scenario, not to pooled OOD data. They are lower bounds, not target
split ratios. Both outcome classes remain mandatory independently of satisfying
the count. The split builder shall not supply hidden defaults or move groups
after examining model scores; an unmet minimum produces `insufficient_data`.

Never collapse the two validation subsets after examining outcomes. If the
available complete run groups cannot support both subsets, generate additional
runs or report `insufficient_data`. Grouped cross-fitting requires a separate
fully specified analysis protocol and is not an implicit fallback.

## 23. Increment-2 acceptance

Stop and review evidence before Increment 3. Increment 2 passes only if:

- Stage-A batches complete and validate;
- miss-count targets are met;
- datasets reproduce from frozen inputs;
- causality and support-mask checks pass;
- splits are grouped by matched run groups;
- model selection and calibration validation subsets are disjoint;
- Stage-B remains separated into explicit OOD scenarios;
- manifests preserve exact code, build, configuration, and dependencies;
- no model or adaptive behavior is added to ns-3.

# Increment 3: Offline predictability evaluation

## 24. Objective and package

Determine whether risk is predictable, how early it is useful, and which
telemetry tier provides the gain. Increment 3 is Python-only.

Add:

```text
tools/evaluate_prediction.py
tools/prediction/features.py
tools/prediction/heuristics.py
tools/prediction/models.py
tools/prediction/metrics.py
tools/prediction/calibration.py
tools/prediction/reporting.py
```

Inputs are the labelled dataset, dataset manifest, fixed split manifest,
versioned analysis YAML, and explicit random seed. The analysis YAML shall
exist and be frozen before Increment 2 constructs the split manifest because
split-sufficiency keys affect dataset acceptance. Analysis tools shall reject a
missing required key rather than provide an implementation default.

Outputs:

```text
prediction_report.md
model_metrics.csv
budget_metrics.csv
calibration.csv
feature_ablation.csv
feature_importance.csv
f1_degradation.csv
stage_rescue_eligibility.csv
predictions.parquet or predictions.csv
go_no_go.json
plots/
```

## 25. Eligibility and evaluation unit

Train and evaluate separate models for T0, T1, T2, and T4.

For each stage:

1. retain only rows where `actionable = true`;
2. remove `actionable` and `sender_mac_complete`;
3. remove all IDs, context metadata, completion fields, and labels;
4. assign one score to each eligible frame.

Budget selection operates at frame level. Report results by stage, link, miss
regime, correlation mode, frame type/size, and ID/OOD partition.

The primary analysis trains separate models for link 0 and link 1. `path_id`
is excluded. Cross-link transfer trains on one link and applies the fitted
pipeline unchanged to the other.

An optional pooled analysis may combine both links:

- a path-agnostic pooled model excludes all link identity;
- a radio-context pooled model may include deployable physical context such
  as `frequency_band` or `center_frequency_mhz`;
- it must not include arbitrary `path_id`, policy name, or experiment labels.

## 26. Feature sets

Use explicit nested upper-bound allowlists:

```text
F0
F0 + F1-ideal
F0 + F1-ideal + F2
F0 + F1-ideal + F2 + F3
```

Never use "all numeric columns." Also ablate queue, retry, PHY occupancy,
frame-progress, and oracle CW/backoff/NAV families.

Prohibited predictors include run/group IDs, seed, run number, path/policy,
scenario/profile/regime labels, completion/outcome fields, and the
`feature_support_mask`. The mask is provenance metadata, not a predictor.

Maintain a separately versioned `F2-exportable` allowlist containing only F2
fields whose feature-dictionary entries identify a concrete modified-driver
export mechanism. Freeze this allowlist before test evaluation. It is the
required feature set for the modified-driver decision in Section 33; a
post-test feature-importance selection is not sufficient.

### 26.1 F1 observation degradation

F1-ideal is an optimistic upper bound because ns-3 exposes exact, immediate
short-window counters. Also evaluate `F1-degraded`, generated causally from
F1-ideal using a versioned analysis configuration:

```text
report_interval_us
observation_delay_us
counter_quantization
signal_quantization_db
rate_quantization
disabled_feature_families
```

At sample time `t`, a degraded value may use only a synthetic report whose
source cutoff is no later than `t - observation_delay`. Hold values between
report updates. Quantization and disabled families are applied without using
labels or test statistics. When only frame-aligned source snapshots are
available, use the latest source snapshot no later than the synthetic report
cutoff and record its actual source timestamp and staleness. Never interpolate
from a future snapshot.

Evaluate two tracks:

```text
upper-bound track:
    F0
    F0 + F1-ideal
    F0 + F1-ideal + F2
    F0 + F1-ideal + F2 + F3

deployment-sensitivity track:
    F0
    F0 + F1-degraded
    F0 + F1-degraded + F2
```

Report multiple plausible degradation profiles rather than describing one as
an exact model of every commodity device.

## 27. Mandatory heuristics

### 27.1 Random ranking

Use the analytical expectation for selecting `K` of `N` eligible frames
uniformly:

```text
random_expected_recall = K / N
random_expected_precision = eligible_miss_count / N
```

This is exact conditional on the evaluated population. Optional seeded random
permutations may verify the implementation but are not the formal baseline
and must not affect model selection or go/no-go decisions.

### 27.2 Retry pressure

```text
score = mpdu_retries_5ms / max(mpdu_attempts_5ms, 1)
```

### 27.3 Delivery drought

```text
score = last_success_age_us / max(deadline_slack_us, epsilon)
```

### 27.4 Byte-service queue slack

Use the same MAC service-byte domain defined in Section 8.7:

```text
remaining_primary_service_bytes =
    mac_service_bytes_ahead_of_frame
    + frame_mac_service_bytes_pending_primary

recent_service_rate_bytes_per_us =
    acknowledged_mac_service_bytes_20ms
    / history_coverage_20ms_us

estimated_service_us =
    remaining_primary_service_bytes
    / recent_service_rate_bytes_per_us

score = estimated_service_us / max(deadline_slack_us, epsilon)
```

Use this estimate only when history coverage is positive and acknowledged
service bytes are nonzero. Otherwise use a fallback fixed from training data,
a longer causal history, or configured PHY assumptions. Never use test
outcomes or zero as the fallback. Terminally dropped bytes remain
unacknowledged but do not inflate remaining primary-path service; use
`frame_packets_terminally_dropped` as a separate risk signal.

### 27.5 Deliberately simple packet-count baseline

Retain the packet-count formula only as a simple baseline:

```text
estimated_service_us =
    (packets_ahead_of_frame + frame_packets_pending_primary)
    * mpdu_queue_to_ack_mean_20ms_us
```

This baseline assumes additive packet service and is not an airtime or
completion-time estimator under A-MPDU. Label it accordingly in every report.

Do not add a recent receiver miss-rate baseline. The sender has no modelled
application-level outcome feedback. Such a baseline requires a future
specification that defines a feedback channel and delay.

## 28. Models and calibration

### 28.1 Logistic regression

- fit-data-only imputation and missing indicators;
- fit-data-only scaling and categorical encoding;
- L2 regularization by default;
- class/sample weighting;
- deterministic seed.

### 28.2 Histogram gradient boosting

Use scikit-learn histogram gradient boosting or an equivalently lightweight
implementation:

- shallow/moderate depth;
- fit-data-only preprocessing;
- `validation_selection`-only hyperparameter selection;
- class/sample weighting;
- deterministic seed;
- no ID or context predictors.

### 28.3 Selection and final refit

Candidate pipelines are fitted on training runs. Use only
`validation_selection` to choose model family, hyperparameters, stage,
heuristic, selectable feature/degradation profile, and reactive-candidate
stage. Primary feature sets fixed by the specification are not silently
replaced by the best ablation.

After every choice is frozen, refit the selected uncalibrated pipeline,
including preprocessing, on training plus `validation_selection`. Do not use
`validation_calibration` in this refit. Record both the selection-time and
final-refit training run groups. Metrics on `validation_selection` justify
selection but are not reported as independent performance evidence.

### 28.4 Calibration

Fit the frozen calibrator using only complete run groups in
`validation_calibration`. The calibration method is declared in the analysis
YAML before evaluation or selected by a grouped internal procedure confined
to `validation_selection`; do not choose between Platt and isotonic by their
fit on `validation_calibration`.

Select fixed action thresholds from calibrated scores on
`validation_calibration` as specified in Section 29. Never fit calibration or
select thresholds on ID or OOD test data. Calibration-set metrics describe
calibrator fitting and threshold construction, not independent evidence.
Report ranking before calibration and test probability metrics after
calibration.

## 29. Metrics

### 29.1 Ranking-budget metrics

At budgets 5, 10, and 20 percent, report:

```text
MissRecall@TopKBudget
Precision@TopKBudget
```

For `N` eligible frames, select:

```text
K = ceil(budget * N)
```

If `N = 0`, the metric is insufficient. Otherwise, let `c` be the Kth-highest
score and define:

```text
A = frames with score > c
T = frames with score = c
m = K - |A|
Y_A = deadline misses in A
Y_T = deadline misses in T
M = deadline misses among all N eligible frames

expected_topk_true_positives =
    Y_A + m * Y_T / |T|

MissRecall@TopKBudget =
    expected_topk_true_positives / M

Precision@TopKBudget =
    expected_topk_true_positives / K
```

Recall is null for `M = 0`. Score equality is exact equality of the score
written by the frozen pipeline; do not introduce an unrecorded tolerance.
This definition is the expected metric under uniform selection of `m` frames
from the cutoff tie group.

Do not use `frame_id`, timestamp, run order, row order, or any other frame
identity to resolve a Top-K tie. For tie-only uncertainty:

```text
X ~ Hypergeometric(population = |T|,
                   positives = Y_T,
                   draws = m)
```

Report the configured-confidence exact interval induced by `Y_A + X`
separately from run-group uncertainty. In every run-group bootstrap replicate,
recompute the analytical tie expectation; do not draw an arbitrary tied-frame
subset. Model, stage, feature-set, and heuristic selection use this expected
metric and therefore cannot depend on a tie-breaking seed.

For confidence level `1 - alpha`, use equal-tailed hypergeometric quantiles:
the lower endpoint is the smallest integer whose cumulative probability is at
least `alpha / 2`, and the upper endpoint is the smallest integer whose
cumulative probability is at least `1 - alpha / 2`. Transform these
true-positive endpoints to recall and precision with the same denominators.

Record `topk_cutoff_score`, `topk_strict_count`, `topk_tie_count`,
`topk_tie_slots`, and `expected_topk_true_positives`. PR and ROC calculations
shall group equal scores rather than impose an identity-based ordering.

These metrics measure ranking quality under a globally allocated
test-population budget. They are an upper bound on what a fixed online
threshold can achieve at the same nominal action rate because their cutoff
depends on the complete partition's score distribution.

### 29.2 Calibration-selected fixed thresholds

For every frozen reporting pipeline or heuristic, stage, link, feature set,
and target budget, select a score threshold using
`validation_calibration` only. Threshold selection is performed after model
selection, final refit, and the calibration pipeline are frozen.

Consider the action rules `score >= threshold` for every distinct finite
calibration score and one explicit `no_action` candidate. Every frame tied at
a numeric threshold is acted upon; fixed-threshold evaluation does not break
ties. Choose the candidate that minimizes:

```text
abs(observed_calibration_action_rate - target_budget)
```

Break equal deviations by preferring no budget overshoot and then the higher
threshold. Reject nonfinite scores. Record the achieved calibration action
rate because tied scores may prevent an exact budget. Represent the no-action
candidate with `calibration_threshold_mode = no_action` and a null numeric
threshold; do not serialize an infinity.

Apply this threshold unchanged to every test frame. Do not use test quantiles,
test prevalence, test score ranges, or test action counts. Report:

```text
calibration_score_threshold
calibration_threshold_mode
observed_calibration_action_rate
calibration_miss_recall_at_threshold
calibration_precision_at_threshold
observed_test_action_rate
test_miss_recall_at_calibration_threshold
test_precision_at_calibration_threshold
```

Use `MissRecall@TopKBudget` for predictability screening and fixed-threshold
metrics for deployment realism and budget drift.

### 29.3 Denominators and uncertainty

For a stage and partition, the recall denominator is the number of deadline
misses among frames actionable at that stage. Report both this eligible-miss
count and the total partition miss count so conditional late-stage evaluation
is explicit.

Also report:

- PR-AUC;
- ROC-AUC as secondary and null for a one-class partition;
- Brier score and equal-frequency calibration error;
- false-positive and false-negative rates;
- prevalence;
- run, frame, sample, and miss counts;
- per-run metrics and run-aware confidence intervals.

Freeze these metric settings in the analysis YAML:

```yaml
pr_auc_metric: average_precision
calibration_bin_count: 10
```

`pr_auc_metric` has no implicit alternative. Average precision uses the
positive class `deadline_miss = 1`. Group exact score ties, order distinct
finite score groups from highest to lowest, and let `precision_k` and
`recall_k` be the cumulative values after group `k`:

```text
average_precision =
    sum over k of (recall_k - recall_(k - 1)) * precision_k

recall_0 = 0
```

This is the step-wise average-precision definition, not trapezoidal
integration of the precision-recall curve. The primary ranking report uses the
frozen uncalibrated ranking score. Any separately reported calibrated average
precision must be named as such. Average precision is `insufficient_data` when
there are no eligible rows or no positive examples; an all-positive partition
has average precision 1.

Equal-frequency calibration error uses calibrated probabilities and:

```text
calibration_error =
    sum over bins b of
        (n_b / N)
        * abs(mean_predicted_probability_b - observed_miss_rate_b)
```

Reject nonfinite probabilities and probabilities outside `[0, 1]`. Group
exactly equal probabilities before binning and never split one tie group.
For `N` rows and `D` distinct probabilities, use:

```text
effective_calibration_bin_count =
    min(calibration_bin_count, N, D)
```

If the effective count is zero, calibration error is `insufficient_data`.
Otherwise, sort the `D` tie groups by increasing probability and create that
many contiguous nonempty bins. For boundary `b` from 1 through
`effective_calibration_bin_count - 1`, target cumulative row count
`b * N / effective_calibration_bin_count`. Among boundaries after the previous
one that leave at least one tie group for every remaining bin, choose the
boundary with cumulative row count closest to the target; break an exact
distance tie toward the smaller cumulative count. Report the requested and
effective bin counts and each bin's row count, probability range, mean
probability, and observed miss rate. Recompute bins within each evaluated
partition and each bootstrap replicate. Score equality is exact equality of
the frozen emitted probability; do not introduce a tolerance or row-identity
tie break.

Rows from one run are correlated. Confidence intervals, bootstrap units, and
dispersion treat runs or matched run groups, not frames, as independent. The
analysis configuration shall record `confidence_level`,
`bootstrap_replicates`, and a bootstrap seed; defaults are 0.95 and 2,000
replicates. Use percentile bootstrap intervals and resample matched run groups
together for paired differences. Use the type-7 linear quantile convention
from Section 9 for interval endpoints.

A fixed calibration threshold remains fixed while bootstrapping test run
groups. Do not reselect it from a bootstrap test sample. If full
training-pipeline uncertainty is evaluated separately, use an explicitly
nested run-group procedure and label it separately.

## 30. Lead time

For fixed relative deadlines and fixed T0/T1/T2/T4 offsets:

```text
nominal_rescue_slack_us(stage) =
    configured_deadline_us - sample_offset_us(stage)
```

This value is constant at one stage and is not a meaningful distribution.
Write `stage_rescue_eligibility.csv` containing each stage, nominal rescue
slack, configured minimum rescue time, and:

```yaml
minimum_rescue_time_us: 5000
```

This stable key and its value shall be present in the analysis YAML. The
implementation shall not supply a fallback. Evaluate:

```text
rescue_eligible =
    nominal_rescue_slack_us >= minimum_rescue_time_us
```

A stage can satisfy go/no-go criteria only when `rescue_eligible = true`.

Lead-time median, P10, and threshold distributions are required only when
deadlines vary by frame, sample times are nonfixed, or a later sequential
policy records the first threshold-crossing time. Do not present repeated
constant stage slack as an empirical lead-time distribution.

## 31. Generalization

Required comparisons:

1. unseen seeds in the same scenario family;
2. independent bursts to common bursts;
3. common bursts to independent bursts;
4. same-BSS contention to OBSS;
5. link 0 to link 1 where feature semantics permit;
6. lower load to higher load;
7. keyframes versus interframes.

Reuse fixed split manifests for every feature tier and model.

Stage-specific absolute metrics use each stage's actionable population. Any
claimed improvement between T0 and a later stage shall instead use a common
cohort: frames actionable at the later stage and having both snapshots. Apply
the frozen T0 and later-stage pipelines to this same cohort, use the same
budget and paired run-group bootstrap samples, and then subtract metrics. Do
not subtract recalls computed on different actionable populations.

### 31.1 Required OOD evidence

Before test evaluation, the analysis YAML shall declare stable scenario IDs,
exact run filters, and whether each OOD scenario is required or secondary. The
default formal configuration is:

```text
required_ood_scenarios:
    - obss_only
    - obss_plus_legacy_mixed8

ood_formal_aggregation: per_scenario_worst_case
pooled_ood_decision_use: false
```

Do not redefine, remove, or pool a required scenario after examining its
scores or labels. Calculate every formal OOD metric separately for each
required scenario. For a metric where larger is better, the formal aggregate
is the minimum scenario value. For action-rate overshoot, the formal aggregate
is the maximum scenario value.

Pooled OOD metrics may be reported descriptively but never enter go/no-go,
model selection, calibration, or threshold selection. A required scenario
with no eligible rows, one outcome class, too few configured run groups, or a
missing frozen pipeline yields `insufficient_data`.

For run-aware confidence intervals on a worst-case metric, each bootstrap
replicate independently resamples complete run groups within ID and within
every required OOD scenario, computes all scenario metrics, and then takes the
scenario minimum or maximum for that replicate. Use the resulting distribution
for the formal worst-case interval. Do not select one apparently easiest or
hardest scenario once and reuse it for unrelated metrics.
Within one replicate, use the same resampled ID groups as the denominator for
every scenario-retention value.

Secondary OOD scenarios produce `secondary_generalization_warning` records
only and cannot block or establish a full-domain go decision.

## 32. Feature value and importance

Report:

```text
F1-ideal gain =
    MissRecall@TopK10%(F0+F1-ideal)
    - MissRecall@TopK10%(F0)

F1 degradation loss =
    MissRecall@TopK10%(F0+F1-ideal)
    - MissRecall@TopK10%(F0+F1-degraded)

F2 conservative gain =
    MissRecall@TopK10%(F0+F1-ideal+F2)
    - MissRecall@TopK10%(F0+F1-ideal)

F2 deployment-sensitivity gain =
    MissRecall@TopK10%(F0+F1-degraded+F2)
    - MissRecall@TopK10%(F0+F1-degraded)

F3 gain =
    MissRecall@TopK10%(F0+F1-ideal+F2+F3)
    - MissRecall@TopK10%(F0+F1-ideal+F2)
```

Report analogous gains at each calibration-selected fixed threshold together
with calibration and test action rates.

Use selection-safe permutation importance for boosted trees. Any importance
computed on ID or OOD test data is descriptive and must not alter a pipeline,
allowlist, or decision threshold. Identify important F2 features and map each
back to the feature dictionary and a plausible Linux export mechanism. Model
importance alone does not prove driver exportability.

## 33. Go/no-go report

`go_no_go.json` shall report every criterion, threshold, observed value, and
evidence partition. A missing required partition yields
`insufficient_data`, not an implicit no-go.

Its top-level decision fields are:

```json
{
  "prediction_recommendation": "go",
  "modified_driver_supported": "pass"
}
```

`prediction_recommendation` is one of `go`, `go_limited_domain`,
`go_ranking_only`, `redirect_reactive`, `no_go`, or `insufficient_data`. It
describes whether the evidence supports continued latency-risk prediction
research. `modified_driver_supported` is independently one of `pass`, `fail`,
or `insufficient_data` and describes whether the predeclared
`F2-exportable` telemetry is sufficient. A prediction recommendation never
implicitly authorizes modified-driver implementation.

In this section, F2 performance means `F0 + F1-ideal + F2` unless the
deployment-sensitivity track is named explicitly.

### 33.1 Frozen decision candidates

Evaluate decisions separately per link. Any project-level rule combining
links must be declared in the analysis YAML before test evaluation.

For each link, use `validation_selection` to freeze:

1. the primary model family and hyperparameters for each stage and feature
   set;
2. one screening stage among rescue-eligible T0, T1, and T2;
3. the best simple heuristic for each evaluated stage;
4. one reactive-candidate stage among rescue-eligible T1, T2, and T4; and
5. every selectable feature or F1-degradation profile.

Select by the analytical tie-expected `MissRecall@TopKBudget` at the resolved
`screening_budget`. Break exact stage ties in favor of the earlier stage and
model ties using a declared model-name order; no frame identity participates.
Select the reactive-candidate stage by the `validation_selection`
common-cohort later-stage gain defined in Section 33.6, again breaking an
exact tie in favor of the earlier stage.

After final refit, use only `validation_calibration` to fit the predeclared
calibrator and freeze one fixed action threshold per budget. Do not choose a
model, stage, heuristic, feature profile, calibrator method, or link from
`validation_calibration`, ID test, or OOD test results. Report unselected
models and stages as secondary analyses.

For compact formulas, let:

```text
R0(P, s) = MissRecall@TopKBudget for F0
R1(P, s) = MissRecall@TopKBudget for F0 + F1-ideal
R2(P, s) = MissRecall@TopKBudget for F0 + F1-ideal + F2
R2E(P, s) = MissRecall@TopKBudget for F0 + F1-ideal + F2-exportable
R3(P, s) = MissRecall@TopKBudget for F0 + F1-ideal + F2 + F3
RH(P, s) = MissRecall@TopKBudget for the selection-frozen heuristic
RR(P, s) = analytical expected recall for uniform random ranking
Q2(P, s) = F2 miss recall at the calibration-selected threshold
A2(P, s) = F2 action rate at the calibration-selected threshold
```

All quantities use the resolved `screening_budget`; `P` is an evidence
partition (`ID` or one exact `OOD_j`) and `s` is a stage. Every `R` uses the
analytical cutoff-tie expectation from Section 29. Confidence bounds on
differences come from paired run-group bootstrap differences, not subtraction
of independently calculated interval endpoints.

### 33.2 Thresholds

Use these stable analysis-configuration names and defaults:

```text
screening_budget = 0.10
minimum_f2_recall = 0.40
minimum_random_multiple = 3.0
minimum_heuristic_gain = 0.10
minimum_f2_incremental_gain = 0.10
minimum_ood_retention = 0.70
minimum_later_stage_gain = 0.10
maximum_fixed_threshold_action_rate_overshoot = 0.02
```

The analysis YAML may change values but not criterion names or formulas. These
thresholds and the split, metric, calibration-bin, and rescue-time settings
defined in Sections 22, 29, and 30 are required explicit keys. Every resolved
value is written to `go_no_go.json`.

### 33.3 ID predictability and threshold stability

For the `validation_selection`-frozen screening stage `s*`, define:

```text
id_f2_absolute_recall:
    R2(ID, s*) >= minimum_f2_recall

id_f2_random_multiple:
    R2(ID, s*) >= minimum_random_multiple * RR(ID, s*)

id_f2_heuristic_gain:
    R2(ID, s*) - RH(ID, s*) >= minimum_heuristic_gain

unseen_seed_persistence:
    lower confidence bound of
    R2(ID, s*) - RR(ID, s*) > 0

id_predictability_pass:
    s* is rescue-eligible
    and id_f2_absolute_recall
    and id_f2_random_multiple
    and id_f2_heuristic_gain
    and unseen_seed_persistence

fixed_threshold_stable_id:
    Q2(ID, s*) >= minimum_f2_recall
    and
    A2(ID, s*)
        <= screening_budget
           + maximum_fixed_threshold_action_rate_overshoot
```

`id_predictability_pass` is a ranking screen. The independent
`fixed_threshold_stable_id` condition tests deployment realism using the
threshold frozen on `validation_calibration`.

### 33.4 Required OOD generalization

Let `J` be the required OOD scenario IDs frozen under Section 31.1 and define:

```text
id_excess =
    R2(ID, s*) - RR(ID, s*)

ood_excess(j) =
    R2(OOD_j, s*) - RR(OOD_j, s*)

ood_retention(j) =
    ood_excess(j) / id_excess

required_ood_min_excess =
    min over j in J of ood_excess(j)

required_ood_min_retention =
    min over j in J of ood_retention(j)

required_ood_min_threshold_recall =
    min over j in J of Q2(OOD_j, s*)

required_ood_max_action_rate =
    max over j in J of A2(OOD_j, s*)
```

Formal conditions are:

```text
required_ood_evidence_complete:
    every required scenario has sufficient eligible rows, both classes,
    the configured minimum run-group count, and all R2, RR, Q2, and A2 values

required_ood_excess_positive:
    lower confidence bound of required_ood_min_excess > 0

required_ood_retention_pass:
    required_ood_min_retention >= minimum_ood_retention

required_ood_generalization_pass:
    required_ood_evidence_complete
    and required_ood_excess_positive
    and required_ood_retention_pass

fixed_threshold_stable_required_ood:
    required_ood_evidence_complete
    and required_ood_min_threshold_recall >= minimum_f2_recall
    and
    required_ood_max_action_rate
        <= screening_budget
           + maximum_fixed_threshold_action_rate_overshoot
```

`required_ood_min_retention` is undefined and has status
`insufficient_data` when `id_excess` is nonpositive or any required scenario
metric is unavailable. Report scenario-level values and the worst-case
bootstrap intervals. Pooled OOD results remain descriptive.
`required_ood_evidence_complete` is `pass` when all listed evidence exists and
otherwise is `insufficient_data`; absence of required evidence is not a
performance failure.

Define `required_ood_generalization_failure` as the three-valued negation of
`required_ood_generalization_pass`. A required scenario failure blocks full
`go`; it is not merely a warning.

### 33.5 Modified-driver value

Define:

```text
f2_incremental_gain_id =
    R2(ID, s*) - R1(ID, s*)

f2_exportable_incremental_gain_id =
    R2E(ID, s*) - R1(ID, s*)

f2_exportable_ood_excess(j) =
    R2E(OOD_j, s*) - RR(OOD_j, s*)

f2_exportable_ood_incremental_gain(j) =
    R2E(OOD_j, s*) - R1(OOD_j, s*)

required_ood_min_exportable_excess =
    min over j in J of f2_exportable_ood_excess(j)

required_ood_min_exportable_incremental_gain =
    min over j in J of f2_exportable_ood_incremental_gain(j)

required_ood_exportable_excess_positive =
    lower confidence bound of
    required_ood_min_exportable_excess > 0

required_ood_exportable_incremental_gain_positive =
    lower confidence bound of
    required_ood_min_exportable_incremental_gain > 0
```

The `modified_driver_supported` conjunction requires:

1. the selected stage is rescue-eligible;
2. `R2E(ID, s*) >= minimum_f2_recall`;
3. `R2E(ID, s*) >= minimum_random_multiple * RR(ID, s*)`;
4. `f2_exportable_incremental_gain_id >= minimum_f2_incremental_gain`; and
5. both required-OOD exportable conditions pass.

Emit `modified_driver_supported = pass` when every requirement passes, `fail`
when at least one requirement fails, and `insufficient_data` when none fails
but at least one cannot be evaluated. This status does not inherit from
`prediction_recommendation`.

The OOD incremental-gain condition is distinct from excess over random; both
must hold in every required scenario under the worst-case bootstrap.
The `R2E` pipeline uses the explicitly versioned exportable-F2 allowlist fixed
before test evaluation from the feature dictionary.
Also report the corresponding deployment-sensitivity gain against
F1-degraded.

### 33.6 Reactive redirect and diagnostic conditions

Define T0 weakness on the ID test:

```text
t0_weak =
    R2(ID, T0) < minimum_f2_recall
    or
    R2(ID, T0) - RH(ID, T0) < minimum_heuristic_gain
```

For each later stage `s` in T1, T2, and T4, let `C_s` be the common cohort of
frames actionable at `s` and having both T0 and `s` snapshots. Let `s_r` be
the reactive-candidate stage frozen from `validation_selection`. Define:

```text
later_stage_gain(s) =
    R2(ID, s | C_s) - R2(ID, T0 | C_s)

later_stage_material_improvement =
    s_r is rescue-eligible
    and later_stage_gain(s_r) >= minimum_later_stage_gain
    and its lower paired confidence bound is greater than zero

reactive_redirect_supported =
    t0_weak and later_stage_material_improvement
```

Also emit these exact diagnostics:

```text
oracle_ceiling_weak =
    no rescue-eligible stage has R3(ID, stage) >= minimum_f2_recall

f2_adds_little =
    f2_incremental_gain_id < minimum_f2_incremental_gain
    and
    R2(ID, s*) - RH(ID, s*) < minimum_heuristic_gain

gains_collapse_ood =
    required_ood_generalization_failure

useful_detection_too_late =
    no rescue-eligible evaluated stage passes the ID absolute-recall,
    random-multiple, and heuristic-gain criteria

oracle_only_signal =
    id_predictability_pass is false
    and at least one rescue-eligible F3 stage passes the ID
    absolute-recall and random-multiple criteria

secondary_generalization_warning =
    at least one predeclared secondary held-out scenario has a
    nonpositive lower confidence bound for F2 excess over random
```

Weak F3 alone must not overturn a passing deployable F2 result.
`secondary_generalization_warning` is descriptive and does not override a
decision based on all required scenarios.

### 33.7 Recommendation hierarchy

The mutually exclusive machine-readable `prediction_recommendation` values
are:

```text
go:
    id_predictability_pass
    and fixed_threshold_stable_id
    and required_ood_evidence_complete
    and required_ood_generalization_pass
    and fixed_threshold_stable_required_ood

go_limited_domain:
    id_predictability_pass
    and fixed_threshold_stable_id
    and required_ood_evidence_complete
    and (
        required_ood_generalization_failure
        or not fixed_threshold_stable_required_ood
    )

go_ranking_only:
    id_predictability_pass
    and not fixed_threshold_stable_id

redirect_reactive:
    not id_predictability_pass
    and reactive_redirect_supported

no_go:
    not id_predictability_pass
    and not reactive_redirect_supported
```

`go` supports the declared ID domain and every required OOD scenario.
`go_limited_domain` supports only the ID scenario family and must identify
every failed required OOD ranking or threshold criterion.
`go_ranking_only` reports predictability without a stable online action
threshold. Secondary OOD warnings accompany but do not change these statuses.
All three `go` variants concern the prediction-research question using full
F2. Proceeding to modified-driver implementation additionally requires
`modified_driver_supported = pass`; a `go` value with driver status `fail` or
`insufficient_data` is valid and must not be rewritten.

Each criterion record contains its stable name, status
(`pass`, `fail`, or `insufficient_data`), estimate, confidence bounds,
operator, threshold, partition, scenario ID, link, stage, model, feature set,
eligible frame/miss counts, run-group count, cutoff-tie metadata, split role,
and analysis configuration identity.
Logical formulas use three-valued logic. Negation turns `pass` into `fail` and
`fail` into `pass`, while `insufficient_data` propagates. An existential
condition is `pass` if any member passes, `fail` if every member fails, and
`insufficient_data` otherwise. If any required input prevents a recommendation
from resolving under these rules, emit the recommendation as
`insufficient_data` rather than treating missing evidence as false.

## 34. Increment-3 acceptance

Increment 3 passes only if:

- all modelling remains outside ns-3;
- fixed run-group splits are used;
- mandatory heuristics, logistic regression, and boosting are evaluated;
- tier and targeted ablations are complete;
- analytical tie-expected ranking-budget metrics, calibration-selected
  fixed-threshold metrics, calibration, F1 degradation, and stage rescue
  eligibility are reported;
- model selection and calibration use disjoint complete run groups;
- required OOD scenarios are evaluated separately with worst-case formal
  aggregation;
- synthetic tied-score tests verify analytical expectations and invariance to
  frame, timestamp, run, and row ordering;
- split audits verify that no `run_group_id` crosses training, selection,
  calibration, ID-test, or OOD-test roles;
- per-link, cross-link, and any optional pooled analyses are explicitly
  separated;
- F1 degradation is deterministic and uses no future source snapshot;
- ID and OOD results are separated;
- exact features supporting or rejecting driver work are identified;
- the versioned analysis YAML contains every required split, metric,
  calibration, rescue, and decision key;
- a reproducible written and machine-readable decision is produced with
  separate `prediction_recommendation` and `modified_driver_supported` fields;
- no adaptive simulation policy exists.

# Cross-increment requirements

## 35. Dependencies

Add an explicit, versioned analysis dependency file covering at least:

```text
numpy
pandas
pyarrow
scikit-learn
matplotlib
PyYAML
```

Use the package manager to resolve compatible versions; do not invent version
numbers. Tools shall fail clearly when required dependencies are absent.

## 36. Versioning and reproducibility

Version independently:

- telemetry schema;
- event schema;
- support-mask mapping;
- labelled dataset schema;
- analysis output schema;
- analysis configuration;
- split-manifest schema and split definition.

All semantic schema and support-mapping versions are constants owned by the
source that writes the artifact. They are never selected by a command-line
argument or experiment/analysis configuration. Python tools shall use
source-code constants equivalent to:

```text
PREDICTION_DATASET_SCHEMA_VERSION = 1
PREDICTION_ANALYSIS_SCHEMA_VERSION = 1
PREDICTION_SPLIT_SCHEMA_VERSION = 1
```

An input file may declare the version it expects for compatibility checking;
the tool must verify that value and fail on a mismatch rather than use it to
relabel output. Any semantic column, type, meaning, or support-bit change
requires the owning source constant to be reviewed and, when incompatible,
incremented.

Every derived artifact retains ns-3 commit, project commit, build profile,
source run IDs, schema versions, analysis code identity, resolved command, and
dependency versions.

All tools accept explicit seeds, sort deterministic outputs, reject duplicate
inputs, fail on schema mismatches, and avoid hidden defaults.

## 37. Implementation sequence

### Increment 1

1. Add and test the frame tag.
2. Add collector registration and synchronous T0.
3. Bind fixed paths and trace sources.
4. Implement exact MPDU accounting.
5. Implement rolling MAC/PHY histories.
6. Add samples and optional event output.
7. Add configuration, resolved metadata, and conditional validation.
8. Add unit/integration tests and overhead benchmark.
9. Produce acceptance evidence and stop for review.

### Increment 2

1. Add smoke and tuning configurations.
2. Freeze Stage-A loads after pilots.
3. Run and validate Stage A.
4. Build, validate, and manifest the labelled dataset.
5. Create matched run-group splits with separate selection and calibration
   validation subsets.
6. Run and reserve Stage-B OOD data.
7. Produce acceptance evidence and stop for review.

### Increment 3

1. Implement feature allowlists and heuristics.
2. Implement analytical tie-aware and grouped evaluation metrics.
3. Add logistic regression and boosting.
4. Add selection-only model choice, final refit, calibration, thresholds,
   ablations, and importance.
5. Run ID/OOD and stage rescue-eligibility analyses.
6. Generate the report and go/no-go decision.

## 38. Final non-goals

This specification does not prove that acting on prediction improves the
network. Duplication changes airtime, contention, and queue evolution.
Closed-loop action requires a separate specification and new simulations.
