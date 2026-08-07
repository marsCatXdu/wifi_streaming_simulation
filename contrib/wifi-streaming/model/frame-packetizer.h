/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef FRAME_PACKETIZER_H
#define FRAME_PACKETIZER_H

#include "streaming-frame-tag.h"
#include "streaming-header.h"

#include "ns3/nstime.h"
#include "ns3/packet.h"

#include <optional>
#include <vector>

namespace ns3
{

enum class EmissionMode
{
    BURST,
    UNIFORM_WITHIN_FRAME,
    TRACE_DEFINED
};

/**
 * Immutable description of one packet in a packetization plan.
 */
struct PlannedPacket
{
    uint32_t packetIndex{0};             ///< Packet index within the frame.
    uint32_t applicationPayloadBytes{0}; ///< Encoded-video bytes in the packet.
    std::optional<uint32_t> expectedMacServiceBytes; ///< Exact MAC service bytes, if known.
    Time offset;                         ///< Emission offset from frame generation.
};

/**
 * Pure packetization result used for synchronous T0 telemetry.
 */
struct PacketizationPlan
{
    FrameDescriptor frame;              ///< Frame metadata with resolved packet count.
    uint64_t runIdHash{0};               ///< Stable run identifier hash for the wire header.
    uint8_t copyId{0};                   ///< Application copy identifier.
    uint8_t pathId{0};                   ///< Application path identifier.
    uint16_t flags{0};                   ///< Streaming header flags.
    /**
     * Ordered packet descriptions scheduled by this copy.
     *
     * A canonical plan contains every frame packet. A launch-time projection
     * may retain only a subset while preserving the original packet indexes
     * and total frame packet count.
     */
    std::vector<PlannedPacket> packets;
};

/**
 * Materialized packet and its submission-time metadata.
 */
struct PacketEmission
{
    Ptr<Packet> packet;          ///< Packet carrying the streaming wire header.
    StreamingFrameTag frameTag; ///< Internal tag to attach immediately before submission.
    Time offset;                 ///< Emission offset from frame generation.
};

/**
 * Splits a frame into MTU-safe application packets and emission offsets.
 */
class FramePacketizer
{
  public:
    void SetPayloadSize(uint32_t bytes);
    uint32_t GetPayloadSize() const;
    void SetEmissionMode(EmissionMode mode);
    void SetEmissionSpan(Time span);

    /**
     * Set exact lower-layer bytes added after socket submission.
     *
     * @param bytes UDP, IP, and LLC/SNAP bytes added before MAC service.
     */
    void SetExpectedMacServiceOverhead(uint32_t bytes);

    /**
     * Build an immutable packetization plan without changing simulation state.
     *
     * @param frame Frame to packetize.
     * @param runIdHash Stable run identifier hash.
     * @param copyId Application copy identifier.
     * @param pathId Application path identifier.
     * @param flags Streaming header flags.
     * @return The resolved packetization plan.
     */
    PacketizationPlan Plan(const FrameDescriptor& frame,
                           uint64_t runIdHash,
                           uint8_t copyId,
                           uint8_t pathId,
                           uint16_t flags = 0) const;

    /**
     * Materialize packets exactly as described by a plan.
     *
     * @param plan Immutable packetization plan.
     * @return Ordered packet emissions.
     */
    std::vector<PacketEmission> Materialize(const PacketizationPlan& plan) const;

    /**
     * Project a canonical full-copy plan to a reverse-ordered packet tail.
     *
     * Packet indexes and frame metadata remain unchanged. Emission offsets
     * are reassigned from the beginning of the canonical schedule so the
     * selected tail starts immediately and retains the configured pacing.
     *
     * @param plan Canonical full-copy packetization plan.
     * @param packetCount Number of tail packets to retain.
     * @return Reverse-ordered partial-copy plan.
     */
    static PacketizationPlan SelectReverseTail(const PacketizationPlan& plan,
                                               uint32_t packetCount);

    /**
     * Project a canonical full-copy plan to explicitly ordered packets.
     *
     * Packet indexes and frame metadata remain unchanged. Emission offsets
     * are reassigned from the beginning of the canonical schedule.
     *
     * @param plan Canonical full-copy packetization plan.
     * @param packetIndices Distinct original packet indexes in launch order.
     * @return Ordered partial-copy plan.
     */
    static PacketizationPlan SelectPackets(const PacketizationPlan& plan,
                                           const std::vector<uint32_t>& packetIndices);

    /**
     * Build ideal systematic MDS-style repair symbols for a canonical plan.
     *
     * The source packets are treated as equally sized, zero-padded symbols.
     * Repair symbols therefore carry the largest source payload size. Their
     * packet indexes follow the source namespace and are marked as coded
     * repair in the streaming header flags.
     *
     * @param plan Canonical full-copy packetization plan.
     * @param repairPacketCount Number of innovative repair symbols to send.
     * @return Ordered ideal coded-repair plan.
     */
    static PacketizationPlan MakeSystematicRepair(const PacketizationPlan& plan,
                                                  uint32_t repairPacketCount);

    /**
     * Plan and materialize a frame in one compatibility operation.
     *
     * @param frame Frame to packetize.
     * @param runIdHash Stable run identifier hash.
     * @param copyId Application copy identifier.
     * @param linkId Application path identifier.
     * @param flags Streaming header flags.
     * @return Ordered packet emissions.
     */
    std::vector<PacketEmission> Packetize(const FrameDescriptor& frame,
                                          uint64_t runIdHash,
                                          uint8_t copyId,
                                          uint8_t linkId,
                                          uint16_t flags = 0) const;

  private:
    uint32_t m_payloadSize{1200};
    EmissionMode m_mode{EmissionMode::BURST};
    Time m_emissionSpan{MilliSeconds(1)};
    std::optional<uint32_t> m_expectedMacServiceOverhead; ///< Post-socket service overhead.
};

} // namespace ns3

#endif // FRAME_PACKETIZER_H
