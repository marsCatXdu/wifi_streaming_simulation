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
    std::vector<PlannedPacket> packets;  ///< Ordered immutable packet descriptions.
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
};

} // namespace ns3

#endif // FRAME_PACKETIZER_H
