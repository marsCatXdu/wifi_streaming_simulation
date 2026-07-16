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
MultipathSender::SetPolicy(Ptr<RedundancyPolicy> policy)
{
    NS_ABORT_MSG_IF(!policy, "MultipathSender policy cannot be null");
    m_policy = policy;
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
    if (!m_policy)
    {
        auto fixed = CreateObject<FixedLinkPolicy>();
        fixed->SetPath(m_primaryPath);
        m_policy = fixed;
    }
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
    // This is the sole policy invocation for this frame.
    const PolicyDecision policyDecision = m_policy->Decide(frame, m_telemetry);
    NS_ABORT_MSG_IF(!m_paths.contains(policyDecision.primaryPath),
                    "Policy selected unconfigured primary path "
                        << +policyDecision.primaryPath);
    NS_ABORT_MSG_IF(policyDecision.duplicate && !policyDecision.secondaryPath,
                    "Duplicating policy did not select a secondary path");
    NS_ABORT_MSG_IF(!policyDecision.duplicate && policyDecision.secondaryPath,
                    "Non-duplicating policy selected a secondary path");
    if (policyDecision.secondaryPath)
    {
        NS_ABORT_MSG_IF(*policyDecision.secondaryPath == policyDecision.primaryPath,
                        "Primary and secondary paths must differ");
        NS_ABORT_MSG_IF(!m_paths.contains(*policyDecision.secondaryPath),
                        "Policy selected unconfigured secondary path "
                            << +*policyDecision.secondaryPath);
    }

    frame.packetCount =
        (frame.frameSizeBytes + m_packetizer.GetPayloadSize() - 1) /
        m_packetizer.GetPayloadSize();

    if (m_collector)
    {
        m_collector->RegisterExpectedFrame(frame);
        PolicyDecisionRecord decision;
        decision.runId = m_collector->GetRunId();
        decision.frameId = frame.frameId;
        decision.decisionTimeUs = Simulator::Now().GetMicroSeconds();
        decision.policy = m_policy->GetName();
        decision.primaryLink = policyDecision.primaryPath;
        decision.duplicated = policyDecision.duplicate;
        if (policyDecision.secondaryPath)
        {
            decision.secondaryLink = std::to_string(*policyDecision.secondaryPath);
        }
        decision.reason = policyDecision.reason;
        decision.primaryScore = policyDecision.primaryScore;
        decision.secondaryScore = policyDecision.secondaryScore;
        m_collector->RecordPolicyDecision(decision);
    }

    ScheduleCopy(frame, policyDecision.primaryPath, 0, false, policyDecision.duplicate);
    if (policyDecision.secondaryPath)
    {
        ScheduleCopy(frame, *policyDecision.secondaryPath, 1, true, true);
    }
}

void
MultipathSender::ScheduleCopy(const FrameDescriptor& frame,
                              PathId pathId,
                              uint8_t copyId,
                              bool redundant,
                              bool duplicatedFrame)
{
    const uint16_t flags =
        duplicatedFrame ? StreamingHeader::FLAG_DUPLICATED_FRAME : 0;
    const auto emissions = m_packetizer.Packetize(frame, m_runIdHash, copyId, pathId, flags);
    for (const auto& emission : emissions)
    {
        m_events.push_back(Simulator::Schedule(emission.offset,
                                               &MultipathSender::SendPacket,
                                               this,
                                               pathId,
                                               emission.packet,
                                               redundant));
    }
}

void
MultipathSender::SendPacket(PathId pathId, Ptr<Packet> packet, bool redundant)
{
    const auto iterator = m_paths.find(pathId);
    if (iterator != m_paths.end() && iterator->second.socket->Send(packet) >= 0)
    {
        ++m_packetsSent;
        const uint64_t bytes = packet->GetSize();
        m_bytesSent += bytes;
        m_pathBytesSent[pathId] += bytes;
        if (redundant)
        {
            m_redundantBytesSent += bytes;
            m_pathRedundantBytesSent[pathId] += bytes;
        }
    }
}

uint64_t
MultipathSender::GetPacketsSent() const
{
    return m_packetsSent;
}

uint64_t
MultipathSender::GetBytesSent() const
{
    return m_bytesSent;
}

uint64_t
MultipathSender::GetRedundantBytesSent() const
{
    return m_redundantBytesSent;
}

uint64_t
MultipathSender::GetPathBytesSent(PathId pathId) const
{
    if (auto bytes = m_pathBytesSent.find(pathId); bytes != m_pathBytesSent.end())
    {
        return bytes->second;
    }
    return 0;
}

uint64_t
MultipathSender::GetPathRedundantBytesSent(PathId pathId) const
{
    if (auto bytes = m_pathRedundantBytesSent.find(pathId);
        bytes != m_pathRedundantBytesSent.end())
    {
        return bytes->second;
    }
    return 0;
}

} // namespace ns3
