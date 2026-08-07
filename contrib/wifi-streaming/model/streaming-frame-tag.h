/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef STREAMING_FRAME_TAG_H
#define STREAMING_FRAME_TAG_H

#include "streaming-header.h"

#include "ns3/tag.h"

#include <cstdint>
#include <ostream>

namespace ns3
{

/**
 * Internal cross-layer identity for one streaming application packet.
 *
 * Packet tags do not contribute simulated wire bytes. The tag is attached
 * immediately before socket submission and follows the packet through the
 * protocol stack where packet-tag propagation is supported.
 */
class StreamingFrameTag : public Tag
{
  public:
    static constexpr uint32_t SERIALIZED_SIZE = 41; ///< Serialized tag size.

    /**
     * Return the runtime type information.
     *
     * @return The object TypeId.
     */
    static TypeId GetTypeId();
    TypeId GetInstanceTypeId() const override;

    uint32_t GetSerializedSize() const override;
    void Serialize(TagBuffer buffer) const override;
    void Deserialize(TagBuffer buffer) override;
    void Print(std::ostream& os) const override;

    /**
     * Check structural tag invariants.
     *
     * @return True when the packet index and frame type are valid.
     */
    bool IsValid() const;

    /**
     * Return whether this tag identifies an ideal coded-repair symbol.
     *
     * @return True for a coded-repair symbol.
     */
    bool IsCodedRepair() const;

    uint64_t frameId{0};          ///< Application frame identifier.
    uint8_t pathId{0};            ///< Application path identifier.
    uint8_t copyId{0};            ///< Application copy identifier.
    uint32_t packetIndex{0};      ///< Packet index within the frame.
    uint32_t packetCount{0};      ///< Number of packets in the frame copy.
    uint64_t generationTimeNs{0}; ///< Frame generation time in nanoseconds.
    uint64_t deadlineTimeNs{0};   ///< Absolute frame deadline in nanoseconds.
    uint32_t frameSizeBytes{0};   ///< Encoded-video frame size in bytes.
    FrameType frameType{FrameType::UNKNOWN}; ///< Application frame type.
    uint16_t flags{0}; ///< StreamingHeader semantic flags.
};

} // namespace ns3

#endif // STREAMING_FRAME_TAG_H
