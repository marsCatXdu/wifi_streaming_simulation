/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "frame-packetizer.h"

#include "ns3/abort.h"

#include <algorithm>

namespace ns3
{

void
FramePacketizer::SetPayloadSize(uint32_t bytes)
{
    NS_ABORT_MSG_IF(bytes == 0, "Packet payload must be positive");
    m_payloadSize = bytes;
}

uint32_t
FramePacketizer::GetPayloadSize() const
{
    return m_payloadSize;
}

void
FramePacketizer::SetEmissionMode(EmissionMode mode)
{
    m_mode = mode;
}

void
FramePacketizer::SetEmissionSpan(Time span)
{
    NS_ABORT_MSG_IF(span.IsNegative(), "Emission span cannot be negative");
    m_emissionSpan = span;
}

std::vector<PacketEmission>
FramePacketizer::Packetize(const FrameDescriptor& frame,
                           uint64_t runIdHash,
                           uint8_t copyId,
                           uint8_t linkId) const
{
    NS_ABORT_MSG_IF(frame.frameSizeBytes == 0, "Cannot packetize an empty frame");
    const uint32_t count = (frame.frameSizeBytes + m_payloadSize - 1) / m_payloadSize;
    std::vector<PacketEmission> emissions;
    emissions.reserve(count);
    uint32_t remaining = frame.frameSizeBytes;
    for (uint32_t index = 0; index < count; ++index)
    {
        StreamingHeader header;
        header.runIdHash = runIdHash;
        header.frameId = frame.frameId;
        header.packetIndex = index;
        header.packetCount = count;
        header.frameSizeBytes = frame.frameSizeBytes;
        header.frameType = frame.frameType;
        header.generationTimeNs = frame.generationTimeNs;
        header.deadlineUs = frame.deadlineUs;
        header.copyId = copyId;
        header.senderLinkId = linkId;

        auto packet = Create<Packet>(std::min(remaining, m_payloadSize));
        packet->AddHeader(header);
        Time offset;
        if (m_mode == EmissionMode::UNIFORM_WITHIN_FRAME && count > 1)
        {
            offset = m_emissionSpan * index / (count - 1);
        }
        // TRACE_DEFINED currently has no per-packet timestamps in FrameDescriptor,
        // so its deterministic fallback is burst emission.
        emissions.push_back({packet, offset});
        remaining -= std::min(remaining, m_payloadSize);
    }
    return emissions;
}

} // namespace ns3
