# Wi-Fi streaming core design

The module treats an encoded video frame as the unit of generation, policy
choice, and measurement. `FrameSource` produces finite frame descriptors.
`FramePacketizer` divides each descriptor into UDP payloads and supplies
deterministic burst or uniform emission offsets. Every datagram carries a
versioned, fixed-width `StreamingHeader`; all multibyte fields use network byte
order and malformed or unsupported headers are rejected.

`MultipathSender` stores paths in a `PathId` map and makes one path choice at
the frame boundary. The initial implementation deliberately configures one
connected UDP path. This keeps the data-plane boundary needed by later
dual-interface policy work without implementing that phase early.

`FrameReceiver` uses packet indexes rather than arrival order. It separately
tracks the union, each copy, and each sender link. A repeated union packet is
counted and discarded while still contributing to the independent copy state.
Complete frames are finalized immediately. Incomplete frames are always
finalized at their deadline, cleanup timeout, or application stop.

`MetricsCollector` owns the stable `frames.csv` and `policy_decisions.csv`
schemas. Empty completion timestamps represent incomplete copies or frames;
they are not encoded as zero.

The example is a deterministic 802.11ax `SpectrumWifiPhy` station/AP link with
`FixedRssLossModel`. The AP routes the wireless subnet onto a high-rate CSMA
edge-receiver subnet. A constant station manager avoids rate-control
randomness. Application output, rather than PCAP or FlowMonitor, is the source
of truth.

## Current boundaries

- Only one sender path is configured by the example; there is no duplication
  policy or second radio yet.
- `trace_defined` packet emission falls back to burst because the core
  `FrameDescriptor` has no per-packet timing field.
- Frame deadlines and generation times are application timestamps; encoder,
  decoder, and firmware processing delays are outside the model.
- MAC/PHY airtime and retry accounting are deferred to the instrumentation
  phase.
