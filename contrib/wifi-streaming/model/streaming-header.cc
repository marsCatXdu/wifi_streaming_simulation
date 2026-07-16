/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "streaming-header.h"

#include "ns3/log.h"

#include <algorithm>
#include <array>
#include <cctype>
#include <stdexcept>

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("StreamingHeader");
NS_OBJECT_ENSURE_REGISTERED(StreamingHeader);

std::string
FrameTypeToString(FrameType type)
{
    static const std::array<std::string, 7> names{
        "UNKNOWN",
        "I_FRAME",
        "P_FRAME",
        "B_FRAME",
        "PRIORITY_HIGH",
        "PRIORITY_NORMAL",
        "PRIORITY_LOW"};
    const auto index = static_cast<std::size_t>(type);
    return index < names.size() ? names[index] : names[0];
}

FrameType
FrameTypeFromString(const std::string& value)
{
    std::string normalized = value;
    normalized.erase(std::remove_if(normalized.begin(),
                                    normalized.end(),
                                    [](unsigned char c) { return std::isspace(c); }),
                     normalized.end());
    std::transform(normalized.begin(),
                   normalized.end(),
                   normalized.begin(),
                   [](unsigned char c) { return std::toupper(c); });
    for (uint8_t i = 0; i <= static_cast<uint8_t>(FrameType::PRIORITY_LOW); ++i)
    {
        const auto type = static_cast<FrameType>(i);
        if (normalized == FrameTypeToString(type))
        {
            return type;
        }
    }
    throw std::invalid_argument("unknown frame type: " + value);
}

TypeId
StreamingHeader::GetTypeId()
{
    static TypeId tid =
        TypeId("ns3::StreamingHeader").SetParent<Header>().AddConstructor<StreamingHeader>();
    return tid;
}

TypeId
StreamingHeader::GetInstanceTypeId() const
{
    return GetTypeId();
}

uint32_t
StreamingHeader::GetSerializedSize() const
{
    return SERIALIZED_SIZE;
}

void
StreamingHeader::Serialize(Buffer::Iterator start) const
{
    start.WriteHtonU32(magic);
    start.WriteU8(version);
    start.WriteHtonU64(runIdHash);
    start.WriteHtonU64(frameId);
    start.WriteHtonU32(packetIndex);
    start.WriteHtonU32(packetCount);
    start.WriteHtonU32(frameSizeBytes);
    start.WriteU8(static_cast<uint8_t>(frameType));
    start.WriteHtonU64(generationTimeNs);
    start.WriteHtonU32(deadlineUs);
    start.WriteU8(copyId);
    start.WriteU8(senderLinkId);
    start.WriteHtonU16(flags);
}

uint32_t
StreamingHeader::Deserialize(Buffer::Iterator start)
{
    if (start.GetRemainingSize() < SERIALIZED_SIZE)
    {
        return 0;
    }
    magic = start.ReadNtohU32();
    version = start.ReadU8();
    runIdHash = start.ReadNtohU64();
    frameId = start.ReadNtohU64();
    packetIndex = start.ReadNtohU32();
    packetCount = start.ReadNtohU32();
    frameSizeBytes = start.ReadNtohU32();
    frameType = static_cast<FrameType>(start.ReadU8());
    generationTimeNs = start.ReadNtohU64();
    deadlineUs = start.ReadNtohU32();
    copyId = start.ReadU8();
    senderLinkId = start.ReadU8();
    flags = start.ReadNtohU16();
    return IsValid() ? SERIALIZED_SIZE : 0;
}

bool
StreamingHeader::IsValid() const
{
    return magic == MAGIC && version == VERSION && packetCount > 0 &&
           packetIndex < packetCount &&
           static_cast<uint8_t>(frameType) <= static_cast<uint8_t>(FrameType::PRIORITY_LOW);
}

void
StreamingHeader::Print(std::ostream& os) const
{
    os << "frame=" << frameId << " packet=" << packetIndex << "/" << packetCount
       << " copy=" << +copyId << " link=" << +senderLinkId;
}

} // namespace ns3
