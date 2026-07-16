/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "multipath-sender.h"

#include "ns3/log.h"
#include "ns3/simulator.h"

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("MultipathSender");
NS_OBJECT_ENSURE_REGISTERED(MultipathSender);

TypeId
MultipathSender::GetTypeId()
{
    static TypeId tid = TypeId("ns3::MultipathSender")
                            .SetParent<Application>()
                            .SetGroupName("WifiStreaming")
                            .AddConstructor<MultipathSender>();
    return tid;
}

MultipathSender::MultipathSender() = default;

MultipathSender::~MultipathSender() = default;

void
MultipathSender::SetFrameSource(Ptr<FrameSource> source)
{
    m_source = source;
}

void
MultipathSender::SetMetricsCollector(Ptr<MetricsCollector> collector)
{
    m_collector = collector;
}

void
MultipathSender::SetPacketPayloadSize(uint32_t bytes)
{
    m_packetizer.SetPayloadSize(bytes);
}

void
MultipathSender::SetEmissionMode(EmissionMode mode)
{
    m_packetizer.SetEmissionMode(mode);
}

void
MultipathSender::SetEmissionSpan(Time span)
{
    m_packetizer.SetEmissionSpan(span);
}

void
MultipathSender::SetRunIdHash(uint64_t hash)
{
    m_runIdHash = hash;
}

void
MultipathSender::SetPrimaryPath(PathId pathId)
{
    m_primaryPath = pathId;
}

void
MultipathSender::AddPath(PathId pathId, Ptr<Socket> socket, Ptr<NetDevice> device)
{
    NS_ABORT_MSG_IF(!socket, "Path requires a socket");
    NS_ABORT_MSG_IF(m_paths.contains(pathId), "Duplicate path ID " << +pathId);
    if (device)
    {
        socket->BindToNetDevice(device);
    }
    m_paths.emplace(pathId, Path{socket, device});
}

void
MultipathSender::StartApplication()
{
    NS_ABORT_MSG_IF(!m_source, "MultipathSender requires a FrameSource");
    NS_ABORT_MSG_IF(!m_paths.contains(m_primaryPath), "Primary path is not configured");
    for (auto frame : m_source->GetFrames())
    {
        m_events.push_back(
            Simulator::Schedule(NanoSeconds(frame.generationTimeNs),
                                &MultipathSender::GenerateFrame,
                                this,
                                frame));
    }
}

void
MultipathSender::StopApplication()
{
    for (auto& event : m_events)
    {
        event.Cancel();
    }
    m_events.clear();
    for (auto& entry : m_paths)
    {
        entry.second.socket->Close();
    }
}

void
MultipathSender::GenerateFrame(FrameDescriptor frame)
{
    frame.generationTimeNs = Simulator::Now().GetNanoSeconds();
    const auto emissions =
        m_packetizer.Packetize(frame, m_runIdHash, 0, static_cast<uint8_t>(m_primaryPath));
    frame.packetCount = emissions.size();

    if (m_collector)
    {
        PolicyDecisionRecord decision;
        decision.runId = m_collector->GetRunId();
        decision.frameId = frame.frameId;
        decision.decisionTimeUs = Simulator::Now().GetMicroSeconds();
        decision.primaryLink = m_primaryPath;
        m_collector->RecordPolicyDecision(decision);
    }
    for (const auto& emission : emissions)
    {
        m_events.push_back(Simulator::Schedule(emission.offset,
                                               &MultipathSender::SendPacket,
                                               this,
                                               m_primaryPath,
                                               emission.packet));
    }
}

void
MultipathSender::SendPacket(PathId pathId, Ptr<Packet> packet)
{
    const auto iterator = m_paths.find(pathId);
    if (iterator != m_paths.end() && iterator->second.socket->Send(packet) >= 0)
    {
        ++m_packetsSent;
    }
}

uint64_t
MultipathSender::GetPacketsSent() const
{
    return m_packetsSent;
}

} // namespace ns3
