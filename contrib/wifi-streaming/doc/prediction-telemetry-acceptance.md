# Increment 1 Prediction Telemetry Acceptance

Status: **PASS**

Evidence date: 2026-07-24

Specification: `latency-risk-prediction-spec.md`, Revision 8

Evidence implementation commit:
`3120dccbab31db05c0012febe26497588d97522c`

This report accepts the Increment 1 telemetry foundation. It records the
implemented surface, trace provenance, support contract, tests, reconstructed
accounting evidence, passivity evidence, measured cost, and known limits.
Generated evidence remains under `results/` and is intentionally not committed.

## Implementation Scope

The implementation delta starts at baseline commit
`b56ca81424a18d66a1bb4af3610250a4180204ec`. The telemetry implementation and
its frozen specification use the following commits:

| Commit | Purpose |
| --- | --- |
| `e066848` | Add the prediction study specification |
| `0c57139` | Add stable cross-layer streaming identity |
| `acee580` | Add the prediction telemetry foundation |
| `c998544` | Observe Wi-Fi MAC and PHY telemetry |
| `dc2e907` | Expose prediction telemetry in experiments |
| `f3a90e5` | Validate prediction telemetry artifacts |
| `9a74cd6` | Enforce passive prediction telemetry semantics |
| `d48e790` | Clarify telemetry acceptance contracts |
| `f550f5c` | Validate telemetry overhead and causality |
| `3a53357` | Freeze Increment 1 telemetry contracts |
| `ed981dc` | Finalize prediction telemetry accounting |
| `3120dcc` | Add prediction telemetry acceptance audit |

The implementation changes these files:

- `contrib/wifi-streaming/CMakeLists.txt`
- `contrib/wifi-streaming/doc/design.md`
- `contrib/wifi-streaming/doc/latency-risk-prediction-spec.md`
- `contrib/wifi-streaming/doc/prediction-telemetry.md`
- `contrib/wifi-streaming/examples/streaming-experiment.cc`
- `contrib/wifi-streaming/model/experiment-output.cc`
- `contrib/wifi-streaming/model/experiment-output.h`
- `contrib/wifi-streaming/model/frame-packetizer.cc`
- `contrib/wifi-streaming/model/frame-packetizer.h`
- `contrib/wifi-streaming/model/multipath-sender.cc`
- `contrib/wifi-streaming/model/multipath-sender.h`
- `contrib/wifi-streaming/model/prediction-telemetry-collector.cc`
- `contrib/wifi-streaming/model/prediction-telemetry-collector.h`
- `contrib/wifi-streaming/model/streaming-frame-tag.cc`
- `contrib/wifi-streaming/model/streaming-frame-tag.h`
- `contrib/wifi-streaming/test/wifi-streaming-test-suite.cc`
- `experiments/configs/prediction_telemetry_acceptance_retry.yaml`
- `experiments/configs/prediction_telemetry_smoke.yaml`
- `tools/audit_prediction_telemetry.py`
- `tools/benchmark_prediction_telemetry.py`
- `tools/run_experiments.py`
- `tools/tests/test_analysis_tools.py`
- `tools/validate_outputs.py`

## Schema and Identity

The accepted source-owned versions are:

| Contract | Version |
| --- | ---: |
| `PREDICTION_TELEMETRY_SCHEMA_VERSION` | 2 |
| `PREDICTION_EVENT_SCHEMA_VERSION` | 2 |
| `FEATURE_SUPPORT_MASK_VERSION` | 2 |

`StreamingFrameTag` carries `frame_id`, `path_id`, `copy_id`, packet index and
count, frame generation and absolute deadline timestamps, frame size, and frame
type. The tag is an ns-3 packet tag. It adds no wire bytes and survives the
application, IP, and Wi-Fi path used by the experiment.

The stable join keys are:

- frame copy: `(run_id, frame_id, path_id, copy_id)`;
- logical packet: `(run_id, frame_id, path_id, copy_id, packet_index)`;
- event order: collector-global `event_sequence`.

The smoke audit follows frame 1, path 0, copy 0 through all 10 application
packets. Every packet has exactly one `PACKET_SUBMITTED` event, one
`MAC_ENQUEUE` event, and at least one MPDU outcome event. The same audit records
multi-MPDU PHY transmissions without losing the per-packet keys.

## Field Source Audit

Snapshots are generated entirely from sender-side state. No receiver trace,
frame completion result, deadline-miss label, or receiver callback is connected
to `PredictionTelemetryCollector`.

| Feature source | Source or trace | Collector use |
| --- | --- | --- |
| Frame plan | `FramePacketizer::Plan` | Immutable frame and packet counts, byte domains, generation time, deadline |
| Application submission | `MultipathSender` before `Socket::Send` | Submitted and remaining packet counts and socket bytes |
| Queue admission | `WifiMacQueue::Enqueue` | Queue entry time, queue position, packet and service-byte state |
| Queue removal | `WifiMacQueue::Dequeue` | Queue departure and current queue state |
| Positive ACK | `WifiMac::AckedMpdu` | Logical ACK state, ACK times, successful-attempt finalization |
| Negative ACK | `WifiMac::NAckedMpdu` | Failed-attempt finalization |
| Response timeout | `WifiMac::MpduResponseTimeout`, `PsduResponseTimeout` | Failed-attempt finalization |
| Terminal removal | `WifiMac::DroppedMpdu` | Retry-limit, lifetime, and queue drop events and current packet state |
| MPDU attempt | `WifiPhy::PhyTxPsduBegin` | One attempt per tagged data MPDU and PPDU counters |
| PHY intervals | `WifiPhyListener` and `WifiPhyStateHelper::State` | Causal TX, RX, CCA_BUSY, IDLE, SWITCHING, SLEEP, and OFF intervals |
| Current PHY | `WifiPhy`, latest tagged TX vector | MCS, NSS, channel width, guard interval, band, and center frequency |
| Current access state | `Txop`, `ChannelAccessManager` | CW, NAV, access status, and medium-busy state |

The per-field derivation is as follows:

- schema version is the source constant; `run_id` is supplied by the resolved
  experiment; frame, path, copy, packet, frame size, packet count, type,
  generation, and deadline values come from `PacketizationPlan` and
  `StreamingFrameTag`;
- stage, offset, sample time, age, and slack come from the configured offset and
  `Simulator::Now()`; the watermark comes from the latest collector event
  included for that frame copy;
- `sender_mac_complete` and `actionable` derive from the current packet state
  and deadline only;
- submitted packets and application socket bytes come only from
  `MultipathSender::RecordPacketSubmitted`; remaining packets subtract the
  submitted count from the immutable plan;
- total attempts, PPDU count, retries, last attempt time, and current TX-vector
  fields come from tagged data MPDUs in `PhyTxPsduBegin`;
- total positive ACKs and last positive-ACK time come from `AckedMpdu`; failed
  attempts come from `NAckedMpdu` and response-timeout traces; retry-limit,
  lifetime, queue, and total terminal drops come from `DroppedMpdu`;
- every MPDU rolling count and latency derives from timestamped enqueue,
  attempt, failure, retry, and ACK history; acknowledged service bytes use the
  tagged MPDU service size at positive ACK;
- every PHY rolling duration and fraction derives from the listener and
  state-helper interval revisions, clipped to the configured history window;
- frame enqueue, dequeue, ACKNOWLEDGED, failure, terminal, pending, queued, and
  service-byte fields derive from the per-packet state machine updated by those
  same traces;
- queue totals, oldest enqueue, packets ahead, and service bytes ahead derive
  from the collector's ordered mirror of `WifiMacQueue`;
- CW and access status come from `Txop`; NAV and medium-busy state come from
  `ChannelAccessManager`; current PHY state comes from `WifiPhyStateHelper`;
- `feature_support_mask` is generated only from the path-bound and
  oracle-enabled configuration plus the version-2 compile-time mapping.

The collector binds paths at simulation time zero while the PHY is idle. It
observes the existing MAC and PHY; it does not change queue order, contention,
rate control, retry policy, socket traffic, or receiver processing.

`GetExpectedAccessWithin()` is deliberately not called because that query is
not behaviorally passive in ns-3.48. `GetBackoffSlots()` is also not exported as
an exact remaining-slot observation because its value is updated lazily.

## Feature Support Mask

Version 2 assigns one bit per optional field or configured-window field
pattern. A set bit means that the field is implemented from an accepted source,
is populated whenever mathematically defined, and has passed its boundary and
reconstruction checks. A clear bit requires a null CSV value.

The exact mapping is:

| Bits | Fields |
| --- | --- |
| 0-8 | `mpdu_tx_attempts_total`, `mpdu_positive_acks_total`, `mpdu_tx_attempt_failures_total`, `mpdu_retries_total`, `mpdu_terminal_drops_total`, `mpdu_retry_limit_drops_total`, `mpdu_lifetime_drops_total`, `mpdu_queue_drops_total`, `ppdu_tx_count_total` |
| 9-17 | `last_tx_attempt_time_ns`, `last_positive_ack_time_ns`, `current_mcs`, `current_nss`, `current_channel_width_mhz`, `current_guard_interval_ns`, `frequency_band`, `center_frequency_mhz`, `current_ack_signal_dbm` |
| 18-27 | Windowed MPDU attempts, positive ACKs, attempt failures, retries, retry ratio, acknowledged MAC service bytes, queue-to-ACK mean and P95, and first-attempt-to-ACK mean and P95 |
| 28-38 | Windowed PHY TX, RX, busy, idle, and other time; their five fractions; and history coverage |
| 39-45 | Frame MAC-enqueued, MAC-dequeued, ACKNOWLEDGED, attempt-failure, TERMINALLY_REMOVED_PRIMARY, currently queued packet counts, and currently queued MAC service bytes |
| 46-53 | MAC queue packets and service bytes, oldest enqueue time, packets and service bytes ahead, frame pending-primary packets, not-acknowledged service bytes, and pending-primary service bytes |
| 54-60 | Current CW, remaining backoff slots, NAV remaining, current PHY state, channel-access status, medium-busy state, and expected-access reason |

For the accepted Wi-Fi-bound oracle-enabled configuration, all bits are set
except:

| Bit | Field | Reason |
| ---: | --- | --- |
| 17 | `current_ack_signal_dbm` | No accepted passive ACK-signal source is implemented |
| 55 | `remaining_backoff_slots` | The available query is not an exact remaining-slot snapshot |
| 60 | `expected_access_reason_within_slack` | The available expected-access query is behaviorally active |

The audited mask is `0xf7ffffffffdffff`. The audit confirms that bits 17, 55,
and 60 are clear, every other bit from 0 through 60 is set, and all three
unsupported columns are null.

## Causality and Snapshot Cardinality

The collector registers each frame synchronously at generation time after pure
packetization planning and before packet materialization or socket submission.
It writes exactly one T0 row and one row for each configured later offset.

For the accepted configuration, offsets are 0, 1000, 2000, and 4000 us. Every
frame copy therefore has exactly four rows with unique
`(frame_id, path_id, copy_id, sample_offset_us)` keys.
Snapshot events are scheduled by `RegisterFrame`; the foundation test produces
the required cardinality without a receiver, and output validation compares
sample keys only with sender frame-copy plans. Receiver success, loss, and
completion therefore cannot add, remove, or reschedule a sample.

The audited first T0 row has:

- `sample_time_ns == generation_time_ns == 1000000000`;
- `frame_age_us == 0`;
- `packets_submitted == 0`;
- `application_socket_packet_bytes_submitted == 0`;
- `packets_remaining_to_submit == frame_packet_count == 40`;
- a valid prior path-event watermark:
  `latest_feature_event_time_ns == 907211033` and
  `latest_feature_event_sequence == 86`.

The foundation unit test separately captures an unbound T0 snapshot with no
prior feature history. It verifies the empty-watermark representation:
`latest_feature_event_time_ns == null` and
`latest_feature_event_sequence == 0`.

Every nonempty sample watermark identifies the greatest included
`event_sequence`, its timestamp equals `latest_feature_event_time_ns`, and that
timestamp is not later than `sample_time_ns`. Event sequences are contiguous
and strictly increasing. This makes same-timestamp inclusion auditable without
depending on CSV row order.

Same-time ordering is explicit: for frame 1 packet 0,
`PACKET_SUBMITTED` is sequence 340 and `MAC_ENQUEUE` is sequence 341, both at
1033333333 ns. The sender records submission before invoking `Socket::Send`,
and the unit and Python validator tests reject enqueue-before-submission order.

## MPDU and Packet Accounting

The attempt ledger uses:

`attempts = successful attempt finalizations + failed attempt finalizations + unresolved attempts`.

`MPDU_POSITIVE_ACK.finalizes_attempt_success` distinguishes an ACK that closes
an unresolved PHY attempt from a late or duplicate logical ACK that changes
packet state but does not invent another successful attempt.

The complete successful multi-packet lifecycle excerpt is frame 1, path 0,
copy 0. It contains 10 packets, each with 1286 MAC service bytes:

| Time (ns) | Event sequences | Packet indexes | Event |
| ---: | --- | --- | --- |
| 1033333333 | 339 | frame level | `FRAME_REGISTERED` |
| 1033333333 | 340, 342, ..., 358 | 0-9 | `PACKET_SUBMITTED` |
| 1033333333 | 341, 343, ..., 359 | 0-9 | `MAC_ENQUEUE` |
| 1033337431 | 360-369 | 0-9 | `MPDU_TX_ATTEMPT`, attempt 1 |
| 1035016297 | 381-390 | 0-9 | `MPDU_POSITIVE_ACK`, finalizes attempt success |
| 1035016297 | 391-400 | 0-9 | `MAC_DEQUEUE` |

Thus all 10 immutable packet indexes are submitted, enqueued, transmitted in a
multi-MPDU PSDU, positively acknowledged, and dequeued exactly once. The audit
artifact contains the unabridged 50 packet-specific rows.

The weak-link acceptance run contains this reconstructed packet history:

| Event sequence | Time (ns) | Event | Attempt |
| ---: | ---: | --- | ---: |
| 205 | 1000369099 | `MPDU_TX_ATTEMPT` | 1 |
| 311 | 1005877165 | `MPDU_TX_ATTEMPT_FAILURE` | 1 |
| 312 | 1006001165 | `MPDU_TX_ATTEMPT` | 2 |
| 313 | 1006001165 | `MPDU_RETRY` | 2 |
| 330 | 1007062031 | `MPDU_POSITIVE_ACK` | 2 |

For that packet:

- key: path 1, frame 0, copy 0, packet 8;
- attempts: 2;
- failed attempt finalizations: 1;
- successful attempt finalizations: 1;
- unresolved attempts: 0;
- retries: 1;
- positive ACK events: 1;
- terminal drops: 0.

The test suite separately checks a late ACK after terminal removal. Immediately
before the late ACK, cumulative terminal-drop events are 2 and current
TERMINALLY_REMOVED_PRIMARY packets are 2. Immediately after it, cumulative
terminal-drop events remain 2, positive-ACK packets increase by 1, and current
TERMINALLY_REMOVED_PRIMARY packets decrease to 1. A late ACK therefore repairs
current packet state without erasing historical drop events or creating a PHY
attempt.

At every captured snapshot the implementation checks:

- the MPDU attempt ledger above;
- `submitted = queued + acknowledged + terminally removed + other submitted`;
- `frame packets = submitted + remaining to submit`;
- the corresponding application and MAC service-byte conservation equations.

## Rolling-Window Reconstruction

`prediction_events.csv` records exact MAC events and revision-based PHY
intervals. `INITIAL`, `PREDICTED_START`, `EXPLICIT_END`, and `AUTHORITATIVE`
PHY revisions provide enough information to replace provisional interval ends
without adding future knowledge to earlier samples.

The acceptance audit independently rebuilds the 5 ms values for frame 1,
path 0, copy 0 at T2:

| Value | Emitted | Reconstructed |
| --- | ---: | ---: |
| MPDU attempts | 10 | 10 |
| Positive ACKs | 10 | 10 |
| Attempt failures | 0 | 0 |
| Retries | 0 | 0 |
| Acknowledged service bytes | 12860 | 12860 |
| Queue-to-ACK mean (us) | 1682.964 | 1682.964 |
| Queue-to-ACK P95 (us) | 1682.964 | 1682.964 |
| First-attempt-to-ACK mean (us) | 1678.866 | 1678.866 |
| First-attempt-to-ACK P95 (us) | 1678.866 | 1678.866 |
| PHY TX time (us) | 1590.8 | 1590.8 |
| PHY RX time (us) | 58.0 | 58.0 |
| PHY busy time (us) | 16.0 | 16.0 |
| PHY idle time (us) | 3335.2 | 3335.2 |
| PHY other time (us) | 0.0 | 0.0 |
| History coverage (us) | 5000.0 | 5000.0 |

The MAC source arithmetic is:

- enqueue rows 341, 343, ..., 359 at 1033333333 ns;
- 10 attempt rows 360-369 at 1033337431 ns;
- 10 positive-ACK rows 381-390 at 1035016297 ns;
- acknowledged bytes: `10 * 1286 = 12860`;
- queue-to-ACK time:
  `(1035016297 - 1033333333) / 1000 = 1682.964 us`;
- first-attempt-to-ACK time:
  `(1035016297 - 1033337431) / 1000 = 1678.866 us`.

The clipped PHY intervals are:

| Event sequence | State | Start (ns) | End (ns) | Duration (us) |
| ---: | --- | ---: | ---: | ---: |
| 371 | IDLE | 1030333333 | 1033337431 | 3004.098 |
| 373 | TX | 1033337431 | 1034928231 | 1590.800 |
| 375 | IDLE | 1034928231 | 1034942297 | 14.066 |
| 377 | CCA_BUSY | 1034942297 | 1034954297 | 12.000 |
| 378 | CCA_BUSY | 1034954297 | 1034958297 | 4.000 |
| 380 | RX | 1034958297 | 1035016297 | 58.000 |
| 1 | IDLE | 1035016297 | 1035333333 | 317.036 |

The PHY arithmetic is
`3004.098 + 1590.800 + 14.066 + 12.000 + 4.000 + 58.000 + 317.036 = 5000.000 us`.
Fractions are
computed over that available coverage, not over unobserved pre-history.
Validator reconstruction is applied to every emitted sample, not only to the
excerpt above.

## Validation and Regression Results

The following checks pass:

| Check | Result |
| --- | --- |
| `./ns3 build` | PASS |
| `./test.py -s wifi-streaming --no-build` | PASS, 1 suite |
| Direct `wifi-streaming` suite | PASS, 17 C++ test cases |
| `python3 -m unittest tools.tests.test_analysis_tools` | PASS, 18 tests |
| `./test.py --no-build --kinds=TestSuite` | PASS, 1290 of 1290 |
| `./test.py --no-build --constrain core` | PASS, 356 of 356 |
| Four-run telemetry smoke matrix | PASS, 4 of 4 validated |
| Weak-link retry acceptance run | PASS, 1 of 1 validated |
| Machine-readable acceptance audit | PASS |
| Seven-repetition overhead benchmark | PASS |

An additional unconstrained `./test.py --no-build` invocation, which includes
examples as well as test suites, was attempted. It was terminated after four
pre-existing Wi-Fi example jobs remained CPU-bound for more than five minutes:
three `wifi-eht-network` parameterizations and one EMLSR parameterization. No
test-suite failure had been reported, but that examples-inclusive invocation is
incomplete and is not represented as a pass. The complete 1290-test TestSuite
run above is the regression result used for acceptance.

## CSV Schema Examples

`prediction_samples.csv` starts with
`telemetry_schema_version` and uses version 2. Its exact header is emitted by
`PredictionTelemetryCollector::WriteSampleHeader` and checked for exact column
equality by `validate_outputs.py`. Configured windows expand the version-2
window patterns in ascending configured order. The accepted example uses 1 ms,
5 ms, and 20 ms windows.

A representative T0 sample excerpt is:

| Field | Value |
| --- | --- |
| `telemetry_schema_version` | 2 |
| `run_id` | `db680eaf5ccd222a027e` |
| `frame_id`, `path_id`, `copy_id` | 0, 0, 0 |
| `sample_stage`, `sample_offset_us` | `T0`, 0 |
| `sample_time_ns` | 1000000000 |
| `latest_feature_event_time_ns` | 907211033 |
| `latest_feature_event_sequence` | 86 |
| `generation_time_ns`, `deadline_time_ns` | 1000000000, 1033333000 |
| `frame_size_bytes`, `frame_packet_count`, `frame_type` | 48000, 40, `I_FRAME` |
| `packets_submitted`, `packets_remaining_to_submit` | 0, 40 |
| `frame_packets_mac_enqueued`, `frame_packets_tx_succeeded` | 0, 0 |
| `feature_support_mask` | `0xf7ffffffffdffff` |

`prediction_events.csv` starts with `event_schema_version` and uses version 2.
Its fixed schema is:

```text
event_schema_version,run_id,event_time_ns,event_sequence,event_type,path_id,
copy_id,frame_id,packet_index,attempt_number,finalizes_attempt_success,
mac_service_bytes,mac_queue_packets,mac_queue_service_bytes,current_mcs,
current_nss,current_channel_width_mhz,current_guard_interval_ns,
current_phy_state,phy_interval_revision_kind,phy_interval_state,
phy_interval_start_ns,phy_interval_end_ns
```

Representative version-2 event excerpts are:

| Version | Time (ns) | Sequence | Event | Key or interval | Outcome |
| ---: | ---: | ---: | --- | --- | --- |
| 2 | 0 | 1 | `PHY_INTERVAL_REVISION` | path 0, IDLE `[0, INT64_MAX)` | `INITIAL` |
| 2 | 1033337431 | 360 | `MPDU_TX_ATTEMPT` | path 0, frame 1, copy 0, packet 0 | attempt 1 |
| 2 | 1035016297 | 381 | `MPDU_POSITIVE_ACK` | path 0, frame 1, copy 0, packet 0 | attempt 1 finalized successfully |

## Passive-Behavior and Determinism Evidence

The benchmark runs disabled, samples-only, and samples-plus-events modes in a
deterministically shuffled order for seven repetitions. It compares every
pre-existing meaningful output after removing only the expected mode-dependent
metadata: `run_id`, the resolved `predictionTelemetry` block, and the build
timestamp.

All normalized outputs match:

| Output | Normalized SHA-256 |
| --- | --- |
| `frames.csv` | `bc9c5075ab0c13ca6a0c5136265264074d510dbb8d34c61cb42045412a88a361` |
| `policy_decisions.csv` | `f09072d8393606057bc25e33e0262de3f3113dbb968b35fceb70942d9638ed47` |
| `link_intervals.csv` | `bd99248b29a7cf1530e63e64496187ba5e764bdd17da71379bb8d2e414d40dd1` |
| `mac_summary.csv` | `5f3c3fc4590a14e9cfa76d9bbcff2ad3ad0cd9373626faddf90abbbc9b3faf30` |
| `summary.json` | `93bee69b04814c1362733ccb4d122237bc5c24ca7ff006eb7eead2bb42e07828` |
| `ofdma_summary.csv` | `21703edc357c51240cb497906c82dd0210f65676e60f6e193f0efbb9872f16ff` |
| `background_flows.csv` | `17c95aa36fd2460a8d5f80ad7ffd15c13348e692db75684847ffe44dff1f19c2` |
| `background_rate_periods.csv` | `e12dc3eec57c6773e13d832c5058baf8ccfccb65cdfc979af17ff699936f674d` |
| `resolved_config.json` | `34c2106a4b386ea9b686c6666658373d462a1959d3b8e5e7df1a4d2642a8780c` |
| `build_info.json` | `78c63fc076c5d46ed210e5b82a17807932a7bbe9b7f19ebaa7bbb60e36776b54` |
| `stdout.log` | `abf53e54125ceb94c49da7fecdcbb9a529bbdc96d90d529af1662b5d9b4027ea` |

Prediction outputs are deterministic across all seven repetitions after
normalizing `run_id`:

- `prediction_samples.csv`:
  `136f98a503a57d092ae78aa472213f047d8e6264729b0338a27e0ed64c9ac843`;
- `prediction_events.csv`:
  `31f14eb065ec96cfdb63833e4a76ffce3163be3e13b5c2cadbc547fdd44a02ea`.

These checks demonstrate that enabling telemetry does not change simulated
frame outcomes, policy decisions, MAC accounting, background traffic, or
ordinary experiment output.

## Runtime and Output Cost

The final benchmark medians over seven repetitions are:

| Mode | Wall time (s) | Peak RSS (KiB) | Output bytes | Samples | Events |
| --- | ---: | ---: | ---: | ---: | ---: |
| Disabled | 0.507757 | 77252 | 20837 | 0 | 0 |
| Samples only | 0.537664 | 77820 | 146156 | 240 | 0 |
| Samples plus events | 0.550174 | 77840 | 670658 | 240 | 4269 |

Samples-only wall-time overhead is 5.8900 percent. Revision 8 classifies an
overhead at or below 25 percent as `PASS`; above 25 through 35 percent is
`PASS_WITH_PERFORMANCE_WARNING`; above 35 percent is `REVIEW_REQUIRED`.
Increment 1 therefore passes the overhead gate without a performance warning.

## Evidence Artifacts

| Artifact | SHA-256 |
| --- | --- |
| `results/prediction_telemetry_increment1_smoke/experiment_manifest.json` | `e29771b9bef636bfa21c4dbcb82158292d8245afc5610b353df5f29bafd61ce4` |
| `results/prediction_telemetry_increment1_retry/experiment_manifest.json` | `ada53ae605e2f6bb5848cc51491563d53388745c6bda74f5211e2e92f6b1495e` |
| `results/prediction_telemetry_increment1_benchmark_final/prediction_telemetry_benchmark.json` | `65bbdee27c28faabb1cbb44a358bf5e37e98e763277e432fdb7195432db1876f` |
| `results/prediction_telemetry_increment1_acceptance/prediction_telemetry_audit.json` | `dfcd46a481355adfb70f84ac82d4cd36b38021b369c2e5a77207791e216de528` |

The audited smoke files have these checksums:

| File | SHA-256 |
| --- | --- |
| `prediction_samples.csv` | `15560d6e024fd9446f70c46374267e6dff6c2dbad563d01fa28f819ce13b5cfe` |
| `prediction_events.csv` | `392dbc08e3427e77da6402e6b75144dea707fbcbfe089e1c2f7d883cc1eb1b94` |
| `frames.csv` | `038b42de61d675e96e63f55a6556e89df1d21d5fa0d0bc4d7d0f80f51b29dc1f` |
| `resolved_config.json` | `ef07d0b43eddc2be36f4fc8a9f429a93dddf4203f72df58cce0a4c9064052003` |

Reproduction commands:

```sh
python3 tools/run_experiments.py \
  --config experiments/configs/prediction_telemetry_smoke.yaml \
  --output-root results/prediction_telemetry_increment1_smoke \
  --validate

python3 tools/run_experiments.py \
  --config experiments/configs/prediction_telemetry_acceptance_retry.yaml \
  --output-root results/prediction_telemetry_increment1_retry \
  --validate

python3 tools/audit_prediction_telemetry.py \
  results/prediction_telemetry_increment1_smoke/db680eaf5ccd222a027e \
  --retry-run results/prediction_telemetry_increment1_retry/97047008e50e091c13a2 \
  --output results/prediction_telemetry_increment1_acceptance/prediction_telemetry_audit.json

python3 tools/benchmark_prediction_telemetry.py \
  --output-root results/prediction_telemetry_increment1_benchmark_final \
  --repetitions 7 --duration 2 --seed 29
```

The output roots must not already exist when these commands are repeated.

## Known Limitations

- Increment 1 is sender-side telemetry only. It emits no labels or model-ready
  split manifest; those belong to later increments.
- `current_ack_signal_dbm`, exact remaining backoff slots, and expected-access
  reason are unsupported and null by contract.
- F1 values are idealized simulator observations. Commodity degradation,
  quantization, and observation delay are later analysis work.
- Event logging is an audit mode. Samples-only mode is the normal collection
  mode and has the formal overhead gate.
- The collector currently requires path binding at simulation time zero while
  the PHY is idle.
- The examples-inclusive ns-3 run has the four pre-existing long-running Wi-Fi
  examples described above. This does not weaken the complete TestSuite pass,
  but it remains an upstream simulation-suite limitation.

There are no known deviations from the frozen Revision 8 Increment 1 contract.
The three null fields are explicitly unsupported by that contract and are not
deviations.

## Acceptance Decision

```text
PASS  existing regression tests
PASS  telemetry-on/off meaningful-output equivalence
PASS  stable frame/packet identity through A-MPDU and retries
PASS  distinct successful-attempt and logical-positive-ACK accounting
PASS  no duplicate MPDU attempt-outcome counting
PASS  exclusive packet-state, attempt, and byte conservation
PASS  manual rolling-window reconstruction
PASS  PHY-state duration conservation
PASS  receiver-independent snapshot cardinality
PASS  deterministic sample and event output
PASS  per-field unsupported/null/support-bit semantics
PASS  expected-access query never invoked
PASS  samples-only overhead policy
```

Increment 1 is accepted because the implementation evidence demonstrates all
required properties:

- telemetry is behaviorally passive;
- frame, copy, packet, and event identities remain joinable;
- attempts, failures, ACKs, retries, and terminal drops reconcile;
- rolling MAC and PHY values reconstruct from the event ledger;
- unsupported fields are null and have clear per-field support bits;
- receiver information does not enter snapshot generation;
- schemas, run identifiers, and keys are sufficient for later joins and
  run-grouped splits;
- unit, integration, validator, regression, determinism, and overhead checks
  pass.

No concrete failure condition requiring another specification revision was
observed. Increment 2 may proceed against the frozen Revision 8 contracts.

## Increment 2 terminal-drop ordering correction

The first 60-second production attempt exposed a reproducible implementation
defect that the short Increment 1 tests did not exercise. Depending on the
queue-removal path, ns-3 may emit `Dequeue` either before or after
`DroppedMpdu`. The collector incorrectly required every terminally dropped
packet that had ever been enqueued to remain in its queue mirror. It therefore
aborted when the authoritative `Dequeue` trace had already removed the packet.

The correction does not change the frozen telemetry contract. It checks the
packet's current `queued` state against current queue-mirror membership and
supports both valid trace orders. The MPDU accounting test now exercises both
orders. The module test suite passes, and the original OBSS-plus-legacy seed
423 failure completes in the targeted six-second regression.
