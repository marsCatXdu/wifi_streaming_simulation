# Wi-Fi streaming core design

The module treats an encoded video frame as the unit of generation, policy
choice, and measurement. `FrameSource` produces finite frame descriptors.
`FramePacketizer` divides each descriptor into UDP payloads and supplies
deterministic burst or uniform emission offsets. Every datagram carries a
versioned, fixed-width `StreamingHeader`; all multibyte fields use network byte
order and malformed or unsupported headers are rejected.

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
schemas. Empty completion timestamps represent incomplete copies or frames;
they are not encoded as zero.

The example supports `--topology=single_link` (the original deterministic
5 GHz setup) and `--topology=dual_interface`. Dual mode installs independent
2.4 GHz and 5 GHz `SpectrumWifiPhy` channels, MAC/PHY contention state,
station/AP device pairs, and IP subnets. Each sender UDP socket is bound both
to its radio's source address and its `NetDevice`; matching host routes lead
through the corresponding AP interface to the shared high-rate CSMA edge
subnet. A constant station manager and `FixedRssLossModel` avoid unrelated
randomness. `--policy` selects `fixed_link_0`, `fixed_link_1`, `static_best`,
or `full_duplication` in dual mode. Application output, rather than PCAP or
FlowMonitor, is the source of truth.

## Current boundaries

- Dynamic telemetry and adaptive redundancy policies are not part of this
  phase. `StaticBestLinkPolicy` uses configured initialization scores and does
  not switch during a run.
- The receiver finalizes as soon as the union completes. Packets from a slower
  duplicate copy arriving afterward are suppressed as packets for an already
  finalized frame, so that copy's eventual completion is intentionally not
  reported.
- `trace_defined` packet emission falls back to burst because the core
  `FrameDescriptor` has no per-packet timing field.
- Frame deadlines and generation times are application timestamps; encoder,
  decoder, and firmware processing delays are outside the model.
- MAC/PHY airtime and retry accounting are deferred to the instrumentation
  phase.
