/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "streaming-frame-tag.h"

#include "ns3/tag-buffer.h"

namespace ns3
{

NS_OBJECT_ENSURE_REGISTERED(StreamingFrameTag);

TypeId
StreamingFrameTag::GetTypeId()
{
    static TypeId tid = TypeId("ns3::StreamingFrameTag")
                            .SetParent<Tag>()
                            .SetGroupName("WifiStreaming")
                            .AddConstructor<StreamingFrameTag>();
    return tid;
}

TypeId
StreamingFrameTag::GetInstanceTypeId() const
{
    return GetTypeId();
}

uint32_t
StreamingFrameTag::GetSerializedSize() const
{
    return SERIALIZED_SIZE;
}

void
StreamingFrameTag::Serialize(TagBuffer buffer) const
{
    buffer.WriteU64(frameId);
    buffer.WriteU8(pathId);
    buffer.WriteU8(copyId);
    buffer.WriteU32(packetIndex);
    buffer.WriteU32(packetCount);
    buffer.WriteU64(generationTimeNs);
    buffer.WriteU64(deadlineTimeNs);
    buffer.WriteU32(frameSizeBytes);
    buffer.WriteU8(static_cast<uint8_t>(frameType));
}

void
StreamingFrameTag::Deserialize(TagBuffer buffer)
{
    frameId = buffer.ReadU64();
    pathId = buffer.ReadU8();
    copyId = buffer.ReadU8();
    packetIndex = buffer.ReadU32();
    packetCount = buffer.ReadU32();
    generationTimeNs = buffer.ReadU64();
    deadlineTimeNs = buffer.ReadU64();
    frameSizeBytes = buffer.ReadU32();
    frameType = static_cast<FrameType>(buffer.ReadU8());
}

void
StreamingFrameTag::Print(std::ostream& os) const
{
    os << "frame=" << frameId << " packet=" << packetIndex << "/" << packetCount
       << " copy=" << +copyId << " path=" << +pathId << " generated=" << generationTimeNs
       << "ns deadline=" << deadlineTimeNs << "ns";
}

bool
StreamingFrameTag::IsValid() const
{
    return packetCount > 0 && packetIndex < packetCount &&
           static_cast<uint8_t>(frameType) <= static_cast<uint8_t>(FrameType::PRIORITY_LOW);
}

} // namespace ns3
