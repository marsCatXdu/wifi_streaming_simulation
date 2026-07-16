/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "frame-receiver.h"

#include "streaming-header.h"

#include "ns3/abort.h"
#include "ns3/inet-socket-address.h"
#include "ns3/log.h"
#include "ns3/simulator.h"
#include "ns3/udp-socket-factory.h"

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("FrameReceiver");
NS_OBJECT_ENSURE_REGISTERED(FrameReceiver);

TypeId
FrameReceiver::GetTypeId()
{
    static TypeId tid = TypeId("ns3::FrameReceiver")
                            .SetParent<Application>()
                            .SetGroupName("WifiStreaming")
                            .AddConstructor<FrameReceiver>();
    return tid;
}

FrameReceiver::FrameReceiver() = default;

FrameReceiver::~FrameReceiver() = default;

void
FrameReceiver::SetLocal(const Address& local)
{
    m_local = local;
}

void
FrameReceiver::SetMetricsCollector(Ptr<MetricsCollector> collector)
{
    m_collector = collector;
}

void
FrameReceiver::SetCleanupTimeout(Time timeout)
{
    NS_ABORT_MSG_IF(timeout.IsNegative() || timeout.IsZero(), "Cleanup timeout must be positive");
    m_cleanupTimeout = timeout;
}

void
FrameReceiver::StartApplication()
{
    if (!m_socket)
    {
        m_socket = Socket::CreateSocket(GetNode(), UdpSocketFactory::GetTypeId());
        NS_ABORT_MSG_IF(m_socket->Bind(m_local) < 0, "FrameReceiver bind failed");
        m_socket->SetRecvCallback(MakeCallback(&FrameReceiver::HandleRead, this));
    }
}

void
FrameReceiver::StopApplication()
{
    FinalizeAll();
    if (m_socket)
    {
        m_socket->SetRecvCallback(MakeNullCallback<void, Ptr<Socket>>());
        m_socket->Close();
        m_socket = nullptr;
    }
}

void
FrameReceiver::DoDispose()
{
    FinalizeAll();
    m_socket = nullptr;
    m_collector = nullptr;
    Application::DoDispose();
}

void
FrameReceiver::HandleRead(Ptr<Socket> socket)
{
    while (auto packet = socket->Recv())
    {
        ProcessPacket(packet);
    }
}

void
FrameReceiver::ProcessPacket(Ptr<Packet> packet)
{
    StreamingHeader header;
    if (packet->RemoveHeader(header) != StreamingHeader::SERIALIZED_SIZE || !header.IsValid())
    {
        NS_LOG_WARN("Discarding malformed streaming packet");
        return;
    }
    if (m_finalizedFrameIds.contains(header.frameId))
    {
        return;
    }

    auto [iterator, inserted] = m_frames.try_emplace(header.frameId);
    auto& state = iterator->second;
    const uint64_t nowUs = Simulator::Now().GetMicroSeconds();
    if (inserted)
    {
        state.frame = {header.frameId,
                       header.generationTimeNs,
                       header.frameSizeBytes,
                       header.packetCount,
                       header.deadlineUs,
                       header.frameType};
        state.duplicatedFrame =
            (header.flags & StreamingHeader::FLAG_DUPLICATED_FRAME) != 0;
        state.firstArrivalUs = nowUs;
        const Time generation = NanoSeconds(header.generationTimeNs);
        const Time deadline = generation + MicroSeconds(header.deadlineUs);
        if (header.deadlineUs > 0)
        {
            state.deadlineEvent =
                Simulator::Schedule(std::max(Time(), deadline - Simulator::Now()),
                                    &FrameReceiver::Finalize,
                                    this,
                                    header.frameId,
                                    true);
        }
        const Time cleanup = generation + m_cleanupTimeout;
        state.cleanupEvent =
            Simulator::Schedule(std::max(Time(), cleanup - Simulator::Now()),
                                &FrameReceiver::Finalize,
                                this,
                                header.frameId,
                                true);
    }
    else if (state.frame.packetCount != header.packetCount ||
             state.frame.frameSizeBytes != header.frameSizeBytes ||
             state.frame.generationTimeNs != header.generationTimeNs)
    {
        NS_LOG_WARN("Discarding inconsistent metadata for frame " << header.frameId);
        return;
    }

    auto& copyPackets = state.copyPackets[header.copyId];
    const bool newForCopy = copyPackets.insert(header.packetIndex).second;
    if (newForCopy && copyPackets.size() == state.frame.packetCount)
    {
        state.copyCompletionUs.emplace(header.copyId, nowUs);
    }
    state.linkPackets[header.senderLinkId].insert(header.packetIndex);

    const bool newUnionPacket = state.unionPackets.insert(header.packetIndex).second;
    if (!newUnionPacket)
    {
        ++state.duplicates;
    }
    else
    {
        state.firstLinkForPacket.emplace(header.packetIndex, header.senderLinkId);
    }
    if (!state.completionUs && state.unionPackets.size() == state.frame.packetCount)
    {
        state.completionUs = nowUs;
    }
    const bool allCopiesComplete =
        state.copyPackets[0].size() == state.frame.packetCount &&
        state.copyPackets[1].size() == state.frame.packetCount;
    if (state.completionUs && (!state.duplicatedFrame || allCopiesComplete))
    {
        Finalize(header.frameId, false);
    }
}

void
FrameReceiver::Finalize(uint64_t frameId, bool expired)
{
    (void)expired;
    auto iterator = m_frames.find(frameId);
    if (iterator == m_frames.end())
    {
        return;
    }
    auto& state = iterator->second;
    state.deadlineEvent.Cancel();
    state.cleanupEvent.Cancel();

    FrameResult result;
    result.runId = m_collector ? m_collector->GetRunId() : "run";
    result.frame = state.frame;
    result.unionFirstPacketUs = state.firstArrivalUs;
    result.unionCompletionUs = state.completionUs;
    if (auto completion = state.copyCompletionUs.find(0);
        completion != state.copyCompletionUs.end())
    {
        result.copy0CompletionUs = completion->second;
    }
    if (auto completion = state.copyCompletionUs.find(1);
        completion != state.copyCompletionUs.end())
    {
        result.copy1CompletionUs = completion->second;
    }
    result.uniquePacketsReceived = state.unionPackets.size();
    result.duplicatePacketsReceived = state.duplicates;
    result.incomplete = !state.completionUs.has_value();
    const uint64_t absoluteDeadlineUs =
        state.frame.generationTimeNs / 1000 + state.frame.deadlineUs;
    result.deadlineMiss =
        state.frame.deadlineUs > 0 &&
        (!state.completionUs || *state.completionUs > absoluteDeadlineUs);

    std::set<uint8_t> contributingLinks;
    for (const auto& entry : state.firstLinkForPacket)
    {
        contributingLinks.insert(entry.second);
    }
    if (contributingLinks.size() > 1)
    {
        result.completionMode = "mixed";
    }
    else if (!contributingLinks.empty())
    {
        result.completionMode = "link_" + std::to_string(*contributingLinks.begin()) + "_only";
    }

    if (m_collector)
    {
        m_collector->RecordFrame(result);
    }
    m_finalizedFrameIds.insert(frameId);
    ++m_finalized;
    m_frames.erase(iterator);
}

void
FrameReceiver::FinalizeAll()
{
    while (!m_frames.empty())
    {
        Finalize(m_frames.begin()->first, true);
    }
}

uint32_t
FrameReceiver::GetPendingFrameCount() const
{
    return m_frames.size();
}

uint32_t
FrameReceiver::GetFinalizedFrameCount() const
{
    return m_finalized;
}

} // namespace ns3
