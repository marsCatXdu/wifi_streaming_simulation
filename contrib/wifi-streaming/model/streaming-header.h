/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef STREAMING_HEADER_H
#define STREAMING_HEADER_H

#include "ns3/header.h"

#include <cstdint>
#include <ostream>
#include <string>

namespace ns3
{

/**
 * Application-level frame classification.
 */
enum class FrameType : uint8_t
{
    UNKNOWN = 0,
    I_FRAME,
    P_FRAME,
    B_FRAME,
    PRIORITY_HIGH,
    PRIORITY_NORMAL,
    PRIORITY_LOW
};

std::string FrameTypeToString(FrameType type);
FrameType FrameTypeFromString(const std::string& value);

/**
 * Metadata for one generated application frame.
 */
struct FrameDescriptor
{
    uint64_t frameId{0};
    uint64_t generationTimeNs{0};
    uint32_t frameSizeBytes{0};
    uint32_t packetCount{0};
    uint32_t deadlineUs{0};
    FrameType frameType{FrameType::UNKNOWN};
};

/**
 * Wire header carried by every packet belonging to a streaming frame.
 */
class StreamingHeader : public Header
{
  public:
    static constexpr uint32_t MAGIC = 0x5354524d; // "STRM"
    static constexpr uint8_t VERSION = 1;
    static constexpr uint32_t SERIALIZED_SIZE = 50;
    static constexpr uint16_t FLAG_DUPLICATED_FRAME = 1U << 0;
    static constexpr uint16_t FLAG_PARTIAL_COPY = 1U << 1;
    static constexpr uint16_t FLAG_CODED_REPAIR = 1U << 2;
    static constexpr uint16_t ACTION_PACKET_COUNT_MASK = 0xff00;
    static constexpr uint8_t ACTION_PACKET_COUNT_SHIFT = 8;

    /**
     * Encode a partial-copy or coded-repair packet count in the flags word.
     *
     * @param baseFlags Existing low-order semantic flags.
     * @param packetCount Number of packets in the delayed action.
     * @return Flags containing the exact delayed-action packet count.
     */
    static uint16_t WithActionPacketCount(uint16_t baseFlags, uint32_t packetCount);

    /**
     * Return the delayed-action packet count encoded in the flags word.
     *
     * @return Encoded packet count, or zero when none is present.
     */
    uint32_t GetActionPacketCount() const;

    /**
     * Return whether this datagram carries an ideal coded-repair symbol.
     *
     * @return True for a coded-repair symbol.
     */
    bool IsCodedRepair() const;

    static TypeId GetTypeId();
    TypeId GetInstanceTypeId() const override;

    uint32_t GetSerializedSize() const override;
    void Serialize(Buffer::Iterator start) const override;
    uint32_t Deserialize(Buffer::Iterator start) override;
    void Print(std::ostream& os) const override;

    bool IsValid() const;

    uint32_t magic{MAGIC};
    uint8_t version{VERSION};
    uint64_t runIdHash{0};
    uint64_t frameId{0};
    uint32_t packetIndex{0};
    uint32_t packetCount{0};
    uint32_t frameSizeBytes{0};
    FrameType frameType{FrameType::UNKNOWN};
    uint64_t generationTimeNs{0};
    uint32_t deadlineUs{0};
    uint8_t copyId{0};
    uint8_t senderLinkId{0};
    uint16_t flags{0};
};

} // namespace ns3

#endif // STREAMING_HEADER_H
