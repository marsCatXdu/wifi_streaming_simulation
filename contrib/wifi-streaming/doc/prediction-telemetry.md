# Prediction telemetry

`PredictionTelemetryCollector` is a passive observer for fixed-link
deadline-risk experiments. It does not read receiver state, select a path, or
change a transmission decision. `MultipathSender` registers an immutable
packetization plan and captures T0 synchronously before materializing or
submitting packets. Later samples use the ns-3 event callback as their causal
boundary.

Increment 1 supports only `dual_interface` with `fixed_link_0` or
`fixed_link_1`, fixed EHT MCS 5, IPv4/UDP, no UL OFDMA, no A-MSDU, and no
fragmentation. A-MPDU remains enabled. Each application packet carries a
`StreamingFrameTag`; the tag adds no wire bytes and identifies one stable QoS
data MPDU across queue, aggregate, retry, and outcome callbacks.

## Configuration and output

The example accepts:

```text
--predictionTelemetryEnabled=false
--predictionSampleOffsetsUs=0,1000,2000,4000
--predictionHistoryWindowsUs=1000,5000,20000
--predictionEventLogEnabled=false
--predictionOracleFeaturesEnabled=false
```

Lists are nonempty and strictly increasing. Offsets start at zero and precede
the frame deadline; history windows are positive. Schema versions are
source-owned constants and cannot be set from the command line.

Enabled runs add `predictionTelemetry` to `resolved_config.json` and write
`prediction_samples.csv`. `prediction_events.csv` exists only when requested.
Disabled runs retain the historical resolved configuration and output set.
`tools/validate_outputs.py` applies the prediction contract conditionally.
`experiments/configs/prediction_telemetry_smoke.yaml` exercises both fixed
paths. `tools/benchmark_prediction_telemetry.py` runs interleaved disabled,
samples-only, and samples-plus-events measurements.

## Byte domains

The following domains are not interchangeable:

```text
frame_size_bytes
    Encoded video bytes before packetization.

application_socket_packet_bytes_submitted
    Packet::GetSize() immediately before Socket::Send(). This includes the
    50-byte StreamingHeader and excludes UDP/IP and lower-layer headers.

MAC service bytes
    WifiMpdu::GetPacketSize() at first enqueue. For the supported fixed
    IPv4/UDP stack this is encoded chunk + StreamingHeader + 20-byte IPv4
    header + 8-byte UDP header + 8-byte LLC/SNAP header.
```

The plan calculates the exact MAC service size with a 36-byte post-socket
overhead. The first enqueue must agree. Complete MPDU bytes, PSDU bytes, and
on-air bytes are not substituted into a service-byte field.

## Feature dictionary

Each comma-separated field in a row below inherits that row's definition.
`current` means state queried inside the snapshot callback. `cumulative` means
since telemetry binding at simulation time zero. Commodity availability is an
analogue, not a claim of equal update timing or precision.

| Fields | Source | Tier | Unit/domain | Semantics and update | Null and zero | Linux availability |
|---|---|---|---|---|---|---|
| `telemetry_schema_version`, `run_id`, `frame_id`, `path_id`, `copy_id` | Source constants, run configuration, `PacketizationPlan` | metadata | identifier | Immutable per sample | Never null; zero is valid for numeric IDs | Application metadata |
| `sample_stage`, `sample_offset_us`, `sample_time_ns` | Configured offsets and `Simulator::Now()` | metadata | categorical, us, ns | Immutable sample identity | Never null; offset zero is T0 | Application timer analogue |
| `latest_feature_event_time_ns` | Maximum processed frame/path feature event time | metadata | ns | Causal watermark | Never null; must not exceed sample time | No direct commodity equivalent |
| `generation_time_ns`, `deadline_time_ns`, `frame_age_us`, `deadline_slack_us` | Frame plan and current sample offset | F0 | ns or us | Immutable except age/slack by stage | Never null; zero is meaningful | Application state |
| `sender_mac_complete`, `actionable` | Per-packet positive-ACK state and deadline comparison | eligibility metadata | Boolean | Monotone completion/actionability state | Never null; zero is meaningful; prohibited as model features | Modified-driver completion analogue |
| `frame_size_bytes`, `frame_packet_count`, `frame_type` | `FrameDescriptor` and packetization plan | F0 | encoded-video bytes, packets, categorical | Immutable | Never null; size/count are positive | Application state |
| `packets_submitted`, `packets_remaining_to_submit` | `MultipathSender::SendPacket` and plan | F0 | packets | Recorded immediately before `Socket::Send()` so synchronous lower-layer traces are causally later; enabled runs abort on submission failure | Never null; zero is meaningful | Application state |
| `application_socket_packet_bytes_submitted` | `Packet::GetSize()` immediately before `Socket::Send()` | F0 | application socket packet bytes | Cumulative | Never null; zero is meaningful | Application state |
| `mpdu_tx_attempts_total`, `mpdu_retries_total`, `last_tx_attempt_time_ns` | Tagged MPDUs in `PhyTxPsduBegin`; stable MPDU attempt state | F1-ideal | count or ns | Cumulative; one attempt per transmitted constituent MPDU | Null only without Wi-Fi binding; zero count is meaningful; timestamp null before first attempt | Driver TX/retry counters, coarser timing |
| `mpdu_tx_successes_total`, `last_tx_success_time_ns` | `AckedMpdu` after a prior tagged attempt | F1-ideal | count or ns | Cumulative; one success per stable MPDU; a late Block ACK after explicit BAR does not create another attempt or erase the earlier timeout | Null only without Wi-Fi binding; timestamp null before first success | Driver TX success counters |
| `mpdu_tx_attempt_failures_total` | De-duplicated `NAckedMpdu` and response-timeout callbacks | F1-ideal | count | Cumulative unsuccessful attempts, not terminal drops | Null only without Wi-Fi binding; zero is meaningful | Driver retry/failure counters |
| `mpdu_terminal_drops_total`, `mpdu_retry_limit_drops_total`, `mpdu_lifetime_drops_total`, `mpdu_queue_drops_total` | `DroppedMpdu` reason | F1-ideal | count | Cumulative terminal outcomes by cause | Null only without Wi-Fi binding; zero is meaningful | Partial driver statistics |
| `ppdu_tx_count_total` | Target sender PHY `PhyTxPsduBegin` | F1-ideal | PPDUs | Cumulative transmitting-device count | Null only without Wi-Fi binding; zero is meaningful | Driver/radio statistics, device dependent |
| `current_mcs`, `current_nss`, `current_channel_width_mhz`, `current_guard_interval_ns` | Most recent tagged target `WifiTxVector` | F1-ideal | index, streams, MHz, ns | Last observed target transmission | Null before the first target PPDU or without TX-vector support; zero is not substituted | Rate-control/driver statistics |
| `frequency_band`, `center_frequency_mhz` | Bound `WifiPhy` operating channel | F1-ideal | categorical, MHz | Static for the fixed path | Null without Wi-Fi binding | Interface/channel configuration |
| `current_ack_signal_dbm` | No verified ns-3.48 ACK/BA signal callback is bound in Increment 1 | F1-ideal | dBm | Unsupported | Always null; zero must not be substituted | Some modified drivers expose ACK signal |
| `frame_packets_mac_enqueued`, `frame_packets_mac_dequeued` | Tagged target BE queue enqueue/dequeue traces | F2 | packets | Cumulative per frame copy | Null without queue binding; zero is meaningful | Modified driver |
| `frame_packets_tx_succeeded` | Distinct tagged packets with positive MAC ACK | F2 | packets | Cumulative, at most frame packet count | Null without outcome support; zero is meaningful | Modified driver |
| `frame_mpdu_attempt_failures` | De-duplicated unsuccessful attempts attributed by tag | F2 | attempts | Cumulative per frame copy | Null without outcome support; zero is meaningful | Modified driver |
| `frame_packets_terminally_dropped` | Tagged terminal drop callbacks | F2 | packets | Cumulative per frame copy | Null without outcome support; zero is meaningful | Modified driver |
| `frame_packets_currently_queued`, `frame_mac_service_bytes_currently_queued` | Tagged entries in the selected target receiver/TID queue | F2 | packets, MAC service bytes | Instantaneous exact frame state; terminal removal remains queued until the authoritative `Dequeue` trace | Null without queue support; zero is meaningful | Modified driver |
| `mac_queue_packets`, `mac_queue_service_bytes`, `mac_queue_oldest_enqueue_time_ns` | All entries mirrored from the selected receiver/TID queue | F2 | packets, MAC service bytes, ns | Instantaneous exact logical-queue scope; service bytes use `WifiMpdu::GetPacketSize()` | Counts/bytes zero when empty; oldest timestamp null when empty | Modified driver |
| `packets_ahead_of_frame`, `mac_service_bytes_ahead_of_frame` | FIFO order of all mirrored target logical-queue entries | F2 | packets, MAC service bytes | Instantaneous; older entries before the first queued packet of this frame, or all entries before that frame is queued | Both null when exact ordering is unavailable; zero is meaningful | Modified driver |
| `frame_packets_pending_primary` | Planned packets minus ACKed and terminally removed packets | F2 | packets | Instantaneous primary-path work | Null without frame state; zero is meaningful | Modified driver |
| `frame_mac_service_bytes_not_acknowledged` | Planned/validated per-packet MAC service sizes without positive ACK | F2 | MAC service bytes | Includes terminally dropped work needing delivery or rescue | Null until every contributing size is exact; zero is meaningful | Modified driver |
| `frame_mac_service_bytes_pending_primary` | Planned/validated sizes still queued, in flight, or retry eligible | F2 | MAC service bytes | Excludes ACKed and terminally removed work | Null until every contributing size is exact; zero is meaningful | Modified driver |
| `current_cw` | Selected `QosTxop::GetCw()` | F3 | slots | Instantaneous and side-effect free | Null when oracle collection is disabled | Internal/modified driver |
| `remaining_backoff_slots` | No exact passive ns-3.48 getter is used: `GetBackoffSlots()` is lazily updated | F3 | slots | Unsupported until passive reconstruction and boundary tests are available | Always null in Increment 1 | Internal/modified driver |
| `nav_remaining_us` | `ChannelAccessManager::GetNavEnd()` minus current time, clipped at zero | F3 | us | Instantaneous | Null when oracle collection is disabled; zero is meaningful | Internal/modified driver |
| `current_phy_state` | `WifiPhyStateHelper::GetState()` | F3 | `IDLE`, `CCA_BUSY`, `TX`, `RX`, `SWITCHING`, `SLEEP`, `OFF` | Instantaneous | Null when oracle collection is disabled | Radio state, device dependent |
| `channel_access_status` | Selected `QosTxop::GetAccessStatus()` | F3 | `NOT_REQUESTED`, `REQUESTED`, `GRANTED` | Instantaneous | Null when oracle collection is disabled | Internal/modified driver |
| `medium_busy_now` | `ChannelAccessManager::IsBusy()` | F3 | Boolean | Instantaneous | Null when oracle collection is disabled; zero is meaningful | CCA analogue |
| `expected_access_reason_within_slack` | No passive ns-3.48 source: `GetExpectedAccessWithin()` can expire queued MPDUs through `HasFramesToTransmit()` | F3 | documented `WifiExpectedAccessReason` token | Unsupported because collector queries must not mutate simulation state | Always null in Increment 1 | No commodity equivalent |
| `feature_support_mask` | Collector support-family mapping below | provenance | canonical hexadecimal | Immutable support declaration per row | Never null | Not applicable |

For each configured window label, the collector expands these exact fields:

| Field pattern | Source | Tier | Unit/domain | Semantics and null handling |
|---|---|---|---|---|
| `mpdu_attempts_{window}`, `mpdu_successes_{window}`, `mpdu_attempt_failures_{window}`, `mpdu_retries_{window}` | Timestamped tagged MPDU events | F1-ideal | count | Events in `(sample_time - window, sample_time]`; zero is meaningful |
| `mpdu_retry_ratio_{window}` | Retry count divided by attempt count | F1-ideal | ratio | Null when attempt count is zero |
| `acknowledged_mac_service_bytes_{window}` | Positive ACK events | F1-ideal | MAC service bytes | Counted once at ACK time; zero is meaningful |
| `mpdu_queue_to_ack_mean_{window}_us`, `mpdu_queue_to_ack_p95_{window}_us` | ACK time minus first enqueue time | F1-ideal | us | Completed observations assigned by ACK time; null with no observation |
| `mpdu_first_attempt_to_ack_mean_{window}_us`, `mpdu_first_attempt_to_ack_p95_{window}_us` | ACK time minus first PHY attempt | F1-ideal | us | Completed observations assigned by ACK time; null with no observation |
| `phy_tx_time_{window}_us`, `phy_rx_time_{window}_us`, `phy_busy_time_{window}_us`, `phy_idle_time_{window}_us`, `phy_other_time_{window}_us` | Reconciled `WifiPhyStateHelper::State` intervals and causal `WifiPhyListener` notifications | F1-ideal | us | Exact clipped duration without future leakage; listener starts cover RX/CCA and predicted-duration states, authoritative traces truncate interrupted intervals; OTHER is switching, sleep, and off |
| `phy_tx_fraction_{window}`, `phy_rx_fraction_{window}`, `phy_busy_fraction_{window}`, `phy_idle_fraction_{window}`, `phy_other_fraction_{window}` | State duration divided by history coverage | F1-ideal | ratio | Null only when coverage is zero |
| `history_coverage_{window}_us` | Intersection of telemetry lifetime and requested window | provenance | us | Zero before any coverage; short coverage is not zero activity |

P95 uses linear interpolation (Hyndman-Fan type 7). PHY durations must sum
to history coverage within timestamp tolerance.

## Support mask

`feature_support_mask` uses lowercase hexadecimal, bit 0 as the least
significant bit, and no leading zeroes:

```text
bit 0  frame plan
bit 1  socket submission progress
bit 2  MPDU outcomes
bit 3  exact target MAC queue
bit 4  PHY occupancy
bit 5  TX vector
bit 6  causal oracle
```

The mapping version is 1. A set family bit does not convert an explicitly
unsupported member, such as ACK signal, exact remaining backoff, or expected
access reason in Increment 1, into a fabricated zero.

## Validation

The validator checks sample cardinality independently of receiver completion,
unique fixed-path keys, configured times, causal watermarks, monotone
counters, sender completion, queue and primary-work conservation, exact
IPv4/UDP MAC service bytes, rolling boundaries, full PHY coverage, supported
oracle values, required unsupported nulls, submission-before-enqueue event
ordering, and canonical support masks. It
rejects receiver outcomes or final labels in the feature schema.
