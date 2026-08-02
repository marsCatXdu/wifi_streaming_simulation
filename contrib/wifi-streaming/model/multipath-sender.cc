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
MultipathSender::SetPredictionTelemetryCollector(Ptr<PredictionTelemetryCollector> collector)
{
    m_predictionCollector = collector;
}

void
MultipathSender::SetPacketPayloadSize(uint32_t bytes)
{
    m_packetizer.SetPayloadSize(bytes);
}

void
MultipathSender::SetExpectedMacServiceOverhead(uint32_t bytes)
{
    m_packetizer.SetExpectedMacServiceOverhead(bytes);
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
MultipathSender::SetDelayedSecondaryPath(PathId pathId)
{
    m_delayedSecondaryPath = pathId;
}

bool
MultipathSender::RequestSecondaryCopy(uint64_t frameId)
{
    return RequestSecondaryCopy(frameId, "calibrated risk threshold crossed");
}

std::optional<DelayedCopyDescriptor>
MultipathSender::GetDelayedSecondaryCopyDescriptor(uint64_t frameId) const
{
    auto frame = m_delayedFrames.find(frameId);
    if (frame == m_delayedFrames.end() || frame->second.launched)
    {
        return std::nullopt;
    }
    const auto& plan = frame->second.secondaryPlan;
    DelayedCopyDescriptor descriptor;
    descriptor.frameId = frameId;
    descriptor.packetCount = static_cast<uint32_t>(plan.packets.size());
    uint64_t expectedBytes = 0;
    for (const auto& packet : plan.packets)
    {
        NS_ABORT_MSG_IF(!packet.expectedMacServiceBytes,
                        "Delayed secondary packet lacks expected MAC service bytes");
        expectedBytes += *packet.expectedMacServiceBytes;
    }
    descriptor.expectedMacServiceBytes = expectedBytes;
    descriptor.deadlineTimeNs =
        plan.frame.generationTimeNs + static_cast<uint64_t>(plan.frame.deadlineUs) * 1000;
    return descriptor;
}

bool
MultipathSender::RequestSecondaryCopy(uint64_t frameId, const std::string& reason)
{
    auto frame = m_delayedFrames.find(frameId);
    if (frame == m_delayedFrames.end() || frame->second.launched)
    {
        return false;
    }
    const auto& plan = frame->second.secondaryPlan;
    const uint64_t deadlineNs =
        plan.frame.generationTimeNs + static_cast<uint64_t>(plan.frame.deadlineUs) * 1000;
    const int64_t nowNs = Simulator::Now().GetNanoSeconds();
    if (nowNs < 0 || static_cast<uint64_t>(nowNs) >= deadlineNs)
    {
        return false;
    }
    frame->second.launched = true;
    ScheduleCopy(plan, true, false);
    if (m_collector)
    {
        m_collector->MarkPolicyDecisionDuplicated(frameId,
                                                   Simulator::Now().GetMicroSeconds(),
                                                   plan.pathId,
                                                   reason);
    }
    return true;
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
    m_delayedFrames.clear();
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
    NS_ABORT_MSG_IF(m_delayedSecondaryPath && policyDecision.duplicate,
                    "Delayed duplication requires a non-duplicating initial policy");
    if (m_delayedSecondaryPath)
    {
        NS_ABORT_MSG_IF(*m_delayedSecondaryPath == policyDecision.primaryPath,
                        "Delayed secondary path must differ from the primary path");
        NS_ABORT_MSG_IF(!m_paths.contains(*m_delayedSecondaryPath),
                        "Delayed secondary path is not configured");
    }

    const uint16_t flags =
        policyDecision.duplicate ? StreamingHeader::FLAG_DUPLICATED_FRAME : 0;
    const auto primaryPlan =
        m_packetizer.Plan(frame, m_runIdHash, 0, policyDecision.primaryPath, flags);
    frame = primaryPlan.frame;
    if (m_delayedSecondaryPath)
    {
        auto delayedPlan = m_packetizer.Plan(frame,
                                             m_runIdHash,
                                             1,
                                             *m_delayedSecondaryPath,
                                             StreamingHeader::FLAG_DUPLICATED_FRAME);
        const bool inserted =
            m_delayedFrames.emplace(frame.frameId,
                                    DelayedFrameState{std::move(delayedPlan), false})
                .second;
        NS_ABORT_MSG_IF(!inserted, "Duplicate delayed frame identifier " << frame.frameId);
    }
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
    if (m_predictionCollector)
    {
        m_predictionCollector->RegisterFrame(primaryPlan);
    }

    std::optional<PacketizationPlan> secondaryPlan;
    if (policyDecision.secondaryPath)
    {
        secondaryPlan =
            m_packetizer.Plan(frame, m_runIdHash, 1, *policyDecision.secondaryPath, flags);
        if (m_predictionCollector)
        {
            m_predictionCollector->RegisterFrame(*secondaryPlan);
        }
    }
    ScheduleCopy(primaryPlan, false);
    if (secondaryPlan)
    {
        ScheduleCopy(*secondaryPlan, true);
    }
}

void
MultipathSender::ScheduleCopy(const PacketizationPlan& plan,
                              bool redundant,
                              bool predictionTracked)
{
    const auto emissions = m_packetizer.Materialize(plan);
    for (const auto& emission : emissions)
    {
        m_events.push_back(Simulator::Schedule(emission.offset,
                                               &MultipathSender::SendPacket,
                                               this,
                                               plan.pathId,
                                               emission.packet,
                                               emission.frameTag,
                                               redundant,
                                               predictionTracked));
    }
}

void
MultipathSender::SendPacket(PathId pathId,
                            Ptr<Packet> packet,
                            StreamingFrameTag frameTag,
                            bool redundant,
                            bool predictionTracked)
{
    const auto iterator = m_paths.find(pathId);
    if (iterator == m_paths.end())
    {
        return;
    }

    packet->AddPacketTag(frameTag);
    const uint32_t bytes = packet->GetSize();
    if (m_predictionCollector && predictionTracked)
    {
        // Socket::Send can synchronously enqueue the packet at the MAC. Record
        // application submission first so every lower-layer trace has a
        // causally prior packet record.
        m_predictionCollector->RecordPacketSubmitted(frameTag, bytes);
    }
    const int sent = iterator->second.socket->Send(packet);
    NS_ABORT_MSG_IF(sent < 0 && m_predictionCollector && predictionTracked,
                    "Prediction telemetry requires successful UDP socket submission");
    if (sent >= 0)
    {
        ++m_packetsSent;
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
