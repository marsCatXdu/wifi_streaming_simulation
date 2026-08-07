/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "frame-packetizer.h"

#include "ns3/abort.h"

#include <algorithm>
#include <limits>
#include <set>

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

void
FramePacketizer::SetExpectedMacServiceOverhead(uint32_t bytes)
{
    m_expectedMacServiceOverhead = bytes;
}

std::vector<PacketEmission>
FramePacketizer::Materialize(const PacketizationPlan& plan) const
{
    std::vector<PacketEmission> emissions;
    emissions.reserve(plan.packets.size());
    for (const auto& planned : plan.packets)
    {
        StreamingHeader header;
        header.runIdHash = plan.runIdHash;
        header.frameId = plan.frame.frameId;
        header.packetIndex = planned.packetIndex;
        header.packetCount = plan.frame.packetCount;
        header.frameSizeBytes = plan.frame.frameSizeBytes;
        header.frameType = plan.frame.frameType;
        header.generationTimeNs = plan.frame.generationTimeNs;
        header.deadlineUs = plan.frame.deadlineUs;
        header.copyId = plan.copyId;
        header.senderLinkId = plan.pathId;
        header.flags = plan.flags;

        auto packet = Create<Packet>(planned.applicationPayloadBytes);
        packet->AddHeader(header);

        StreamingFrameTag tag;
        tag.frameId = plan.frame.frameId;
        tag.pathId = plan.pathId;
        tag.copyId = plan.copyId;
        tag.packetIndex = planned.packetIndex;
        tag.packetCount = plan.frame.packetCount;
        if ((plan.flags & StreamingHeader::FLAG_CODED_REPAIR) != 0)
        {
            tag.packetCount += static_cast<uint32_t>(plan.packets.size());
        }
        tag.generationTimeNs = plan.frame.generationTimeNs;
        tag.deadlineTimeNs =
            plan.frame.generationTimeNs + static_cast<uint64_t>(plan.frame.deadlineUs) * 1000;
        tag.frameSizeBytes = plan.frame.frameSizeBytes;
        tag.frameType = plan.frame.frameType;
        emissions.push_back({packet, tag, planned.offset});
    }
    return emissions;
}

PacketizationPlan
FramePacketizer::SelectReverseTail(const PacketizationPlan& plan, uint32_t packetCount)
{
    NS_ABORT_MSG_IF(packetCount == 0 || packetCount > plan.packets.size(),
                    "Reverse-tail packet count must be within the full-copy plan");
    std::vector<uint32_t> packetIndices;
    packetIndices.reserve(packetCount);
    for (uint32_t launchIndex = 0; launchIndex < packetCount; ++launchIndex)
    {
        packetIndices.push_back(
            static_cast<uint32_t>(plan.packets.size() - 1 - launchIndex));
    }
    return SelectPackets(plan, packetIndices);
}

PacketizationPlan
FramePacketizer::SelectPackets(const PacketizationPlan& plan,
                               const std::vector<uint32_t>& packetIndices)
{
    NS_ABORT_MSG_IF(plan.frame.packetCount == 0 ||
                        plan.frame.packetCount != plan.packets.size(),
                    "Packet selection requires a canonical full-copy plan");
    NS_ABORT_MSG_IF(packetIndices.empty() || packetIndices.size() > plan.packets.size(),
                    "Selected packet indexes must be a nonempty plan subset");
    for (std::size_t index = 0; index < plan.packets.size(); ++index)
    {
        NS_ABORT_MSG_IF(plan.packets[index].packetIndex != index,
                        "Packet selection requires contiguous canonical indexes");
    }

    PacketizationPlan selected = plan;
    selected.packets.clear();
    selected.packets.reserve(packetIndices.size());
    selected.flags = StreamingHeader::WithActionPacketCount(
        static_cast<uint16_t>(selected.flags | StreamingHeader::FLAG_PARTIAL_COPY),
        packetIndices.size());
    std::set<uint32_t> uniqueIndices;
    for (std::size_t launchIndex = 0; launchIndex < packetIndices.size(); ++launchIndex)
    {
        const uint32_t packetIndex = packetIndices[launchIndex];
        NS_ABORT_MSG_IF(packetIndex >= plan.packets.size(),
                        "Selected packet index exceeds the canonical plan");
        NS_ABORT_MSG_IF(!uniqueIndices.insert(packetIndex).second,
                        "Selected packet indexes must be distinct");
        auto packet = plan.packets[packetIndex];
        packet.offset = plan.packets[launchIndex].offset;
        selected.packets.push_back(packet);
    }
    return selected;
}

PacketizationPlan
FramePacketizer::MakeSystematicRepair(const PacketizationPlan& plan,
                                      uint32_t repairPacketCount)
{
    NS_ABORT_MSG_IF(plan.frame.packetCount == 0 ||
                        plan.frame.packetCount != plan.packets.size(),
                    "Systematic repair requires a canonical full-copy plan");
    NS_ABORT_MSG_IF(repairPacketCount == 0 || repairPacketCount > 255 ||
                        repairPacketCount > plan.packets.size(),
                    "Systematic repair count must be in [1,min(255,source packet count)]");
    for (std::size_t index = 0; index < plan.packets.size(); ++index)
    {
        NS_ABORT_MSG_IF(plan.packets[index].packetIndex != index,
                        "Systematic repair requires contiguous canonical indexes");
    }

    const auto largestPayload =
        std::max_element(plan.packets.begin(),
                         plan.packets.end(),
                         [](const auto& left, const auto& right) {
                             return left.applicationPayloadBytes < right.applicationPayloadBytes;
                         })
            ->applicationPayloadBytes;
    PacketizationPlan repair = plan;
    repair.flags = StreamingHeader::WithActionPacketCount(
        static_cast<uint16_t>((repair.flags & ~StreamingHeader::FLAG_PARTIAL_COPY) |
                              StreamingHeader::FLAG_CODED_REPAIR),
        repairPacketCount);
    repair.packets.clear();
    repair.packets.reserve(repairPacketCount);
    for (uint32_t repairIndex = 0; repairIndex < repairPacketCount; ++repairIndex)
    {
        PlannedPacket packet;
        packet.packetIndex = plan.frame.packetCount + repairIndex;
        packet.applicationPayloadBytes = largestPayload;
        if (plan.packets.front().expectedMacServiceBytes)
        {
            const uint32_t sourcePayload = plan.packets.front().applicationPayloadBytes;
            NS_ABORT_MSG_IF(*plan.packets.front().expectedMacServiceBytes < sourcePayload,
                            "Canonical MAC service bytes are smaller than source payload");
            packet.expectedMacServiceBytes =
                largestPayload +
                (*plan.packets.front().expectedMacServiceBytes - sourcePayload);
        }
        packet.offset = plan.packets[repairIndex].offset;
        repair.packets.push_back(packet);
    }
    return repair;
}

PacketizationPlan
FramePacketizer::Plan(const FrameDescriptor& frame,
                      uint64_t runIdHash,
                      uint8_t copyId,
                      uint8_t pathId,
                      uint16_t flags) const
{
    NS_ABORT_MSG_IF(frame.frameSizeBytes == 0, "Cannot packetize an empty frame");
    const uint64_t deadlineOffsetNs = static_cast<uint64_t>(frame.deadlineUs) * 1000;
    NS_ABORT_MSG_IF(frame.generationTimeNs >
                        std::numeric_limits<uint64_t>::max() - deadlineOffsetNs,
                    "Frame deadline overflows nanosecond timestamp");

    PacketizationPlan plan;
    plan.frame = frame;
    plan.runIdHash = runIdHash;
    plan.copyId = copyId;
    plan.pathId = pathId;
    plan.flags = flags;
    plan.frame.packetCount = 1 + (frame.frameSizeBytes - 1) / m_payloadSize;
    plan.packets.reserve(plan.frame.packetCount);

    uint32_t remaining = frame.frameSizeBytes;
    for (uint32_t index = 0; index < plan.frame.packetCount; ++index)
    {
        PlannedPacket packet;
        packet.packetIndex = index;
        packet.applicationPayloadBytes = std::min(remaining, m_payloadSize);
        if (m_expectedMacServiceOverhead)
        {
            const uint64_t expectedBytes =
                static_cast<uint64_t>(packet.applicationPayloadBytes) +
                StreamingHeader::SERIALIZED_SIZE + *m_expectedMacServiceOverhead;
            NS_ABORT_MSG_IF(expectedBytes > std::numeric_limits<uint32_t>::max(),
                            "Expected MAC service size exceeds uint32_t");
            packet.expectedMacServiceBytes = static_cast<uint32_t>(expectedBytes);
        }
        if (m_mode == EmissionMode::UNIFORM_WITHIN_FRAME && plan.frame.packetCount > 1)
        {
            packet.offset = m_emissionSpan * index / (plan.frame.packetCount - 1);
        }
        // TRACE_DEFINED has no per-packet timestamps in FrameDescriptor, so its
        // deterministic fallback remains burst emission.
        plan.packets.push_back(packet);
        remaining -= packet.applicationPayloadBytes;
    }
    return plan;
}

std::vector<PacketEmission>
FramePacketizer::Packetize(const FrameDescriptor& frame,
                           uint64_t runIdHash,
                           uint8_t copyId,
                           uint8_t linkId,
                           uint16_t flags) const
{
    return Materialize(Plan(frame, runIdHash, copyId, linkId, flags));
}

} // namespace ns3
