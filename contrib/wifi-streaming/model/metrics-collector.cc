/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "metrics-collector.h"

#include "ns3/fatal-error.h"

#include <iomanip>

namespace ns3
{

NS_OBJECT_ENSURE_REGISTERED(MetricsCollector);

namespace
{

template <typename T>
void
WriteOptional(std::ostream& output, const std::optional<T>& value)
{
    if (value)
    {
        output << *value;
    }
}

void
WriteIndices(std::ostream& output, const std::vector<uint32_t>& indices)
{
    for (std::size_t index = 0; index < indices.size(); ++index)
    {
        output << (index == 0 ? "" : ";") << indices[index];
    }
}

void
WriteMappedIndices(std::ostream& output,
                   const std::map<uint8_t, std::vector<uint32_t>>& indices,
                   uint8_t key)
{
    if (const auto entry = indices.find(key); entry != indices.end())
    {
        WriteIndices(output, entry->second);
    }
}

} // namespace

TypeId
MetricsCollector::GetTypeId()
{
    static TypeId tid = TypeId("ns3::MetricsCollector")
                            .SetParent<Object>()
                            .SetGroupName("WifiStreaming")
                            .AddConstructor<MetricsCollector>();
    return tid;
}

MetricsCollector::MetricsCollector() = default;

MetricsCollector::~MetricsCollector() = default;

void
MetricsCollector::SetRunId(const std::string& runId)
{
    m_runId = runId;
}

const std::string&
MetricsCollector::GetRunId() const
{
    return m_runId;
}

void
MetricsCollector::SetOutputFiles(const std::string& framesFile, const std::string& decisionsFile)
{
    m_frames.open(framesFile, std::ios::out | std::ios::trunc);
    m_decisions.open(decisionsFile, std::ios::out | std::ios::trunc);
    NS_ABORT_MSG_IF(!m_frames, "Cannot open frames output " << framesFile);
    NS_ABORT_MSG_IF(!m_decisions, "Cannot open policy output " << decisionsFile);
    WriteFrameHeader();
    WriteDecisionHeader();
}

void
MetricsCollector::SetPacketOutcomesFile(const std::string& packetOutcomesFile)
{
    NS_ABORT_MSG_IF(m_packetOutcomes.is_open(),
                    "Packet-outcome output may be configured only once");
    if (packetOutcomesFile.empty())
    {
        return;
    }
    m_packetOutcomes.open(packetOutcomesFile, std::ios::out | std::ios::trunc);
    NS_ABORT_MSG_IF(!m_packetOutcomes,
                    "Cannot open packet-outcome output " << packetOutcomesFile);
    WritePacketOutcomeHeader();
}

void
MetricsCollector::RegisterExpectedFrame(const FrameDescriptor& frame)
{
    NS_ABORT_MSG_IF(m_expectedFrames.contains(frame.frameId),
                    "Frame " << frame.frameId << " was registered more than once");
    m_expectedFrames.emplace(frame.frameId, frame);
}

void
MetricsCollector::WriteFrameHeader()
{
    m_frames << "run_id,frame_id,generation_time_us,frame_size_bytes,packet_count,frame_type,"
                "deadline_us,policy,primary_link,duplicated,decision_time_us,"
                "predicted_delay_link_0,predicted_delay_link_1,union_first_packet_us,"
                "union_completion_us,union_latency_us,copy_0_completion_us,"
                "copy_1_completion_us,unique_packets_received,duplicate_packets_received,"
                "deadline_miss,incomplete,completion_mode\n";
}

void
MetricsCollector::WriteDecisionHeader()
{
    m_decisions << "run_id,frame_id,decision_time_us,policy,primary_link,duplicated,"
                   "secondary_link,reason,primary_score,secondary_score\n";
}

void
MetricsCollector::WritePacketOutcomeHeader()
{
    m_packetOutcomes
        << "run_id,frame_id,source_packet_count,received_source_packet_indices,"
           "missing_source_packet_indices,copy_0_source_packet_indices,"
           "copy_1_source_packet_indices,link_0_source_packet_indices,"
           "link_1_source_packet_indices,received_coded_repair_indices\n";
}

void
MetricsCollector::RecordFrame(const FrameResult& result)
{
    FrameResult stored = result;
    const auto decision = m_policyDecisions.find(stored.frame.frameId);
    if (decision != m_policyDecisions.end())
    {
        stored.policy = decision->second.policy;
        stored.primaryLink = decision->second.primaryLink;
        stored.duplicated = decision->second.duplicated;
        stored.decisionTimeUs = decision->second.decisionTimeUs;
        stored.predictedDelayLink0 = decision->second.primaryScore;
        stored.predictedDelayLink1 = decision->second.secondaryScore;
    }
    m_expectedFrames.erase(stored.frame.frameId);
    m_results.push_back(stored);
    if (m_frames)
    {
        m_frames << stored.runId << ',' << stored.frame.frameId << ','
                 << stored.frame.generationTimeNs / 1000 << ',' << stored.frame.frameSizeBytes
                 << ',' << stored.frame.packetCount << ','
                 << FrameTypeToString(stored.frame.frameType) << ',' << stored.frame.deadlineUs
                 << ',' << stored.policy << ',' << +stored.primaryLink << ',' << stored.duplicated
                 << ',' << stored.decisionTimeUs << ',' << std::setprecision(10)
                 << stored.predictedDelayLink0 << ',' << stored.predictedDelayLink1 << ',';
        WriteOptional(m_frames, stored.unionFirstPacketUs);
        m_frames << ',';
        WriteOptional(m_frames, stored.unionCompletionUs);
        m_frames << ',';
        if (stored.unionCompletionUs)
        {
            m_frames << (*stored.unionCompletionUs - stored.frame.generationTimeNs / 1000);
        }
        m_frames << ',';
        WriteOptional(m_frames, stored.copy0CompletionUs);
        m_frames << ',';
        WriteOptional(m_frames, stored.copy1CompletionUs);
        m_frames << ',' << stored.uniquePacketsReceived << ','
                 << stored.duplicatePacketsReceived << ',' << stored.deadlineMiss << ','
                 << stored.incomplete << ',' << stored.completionMode << '\n';
        m_frames.flush();
    }
    if (m_packetOutcomes)
    {
        m_packetOutcomes << stored.runId << ',' << stored.frame.frameId << ','
                         << stored.frame.packetCount << ',';
        WriteIndices(m_packetOutcomes, stored.receivedSourcePacketIndices);
        m_packetOutcomes << ',';
        WriteIndices(m_packetOutcomes, stored.missingSourcePacketIndices);
        m_packetOutcomes << ',';
        WriteMappedIndices(m_packetOutcomes, stored.sourcePacketIndicesByCopy, 0);
        m_packetOutcomes << ',';
        WriteMappedIndices(m_packetOutcomes, stored.sourcePacketIndicesByCopy, 1);
        m_packetOutcomes << ',';
        WriteMappedIndices(m_packetOutcomes, stored.sourcePacketIndicesByLink, 0);
        m_packetOutcomes << ',';
        WriteMappedIndices(m_packetOutcomes, stored.sourcePacketIndicesByLink, 1);
        m_packetOutcomes << ',';
        WriteIndices(m_packetOutcomes, stored.receivedCodedRepairIndices);
        m_packetOutcomes << '\n';
        m_packetOutcomes.flush();
    }
    if (decision != m_policyDecisions.end())
    {
        WritePolicyDecision(decision->second);
    }
}

void
MetricsCollector::RecordPolicyDecision(const PolicyDecisionRecord& decision)
{
    const bool inserted = m_policyDecisions.emplace(decision.frameId, decision).second;
    NS_ABORT_MSG_IF(!inserted, "Duplicate policy decision for frame " << decision.frameId);
}

void
MetricsCollector::WritePolicyDecision(const PolicyDecisionRecord& decision)
{
    if (!m_decisions)
    {
        return;
    }
    m_decisions << decision.runId << ',' << decision.frameId << ',' << decision.decisionTimeUs << ','
                << decision.policy << ',' << +decision.primaryLink << ',' << decision.duplicated << ','
                << decision.secondaryLink << ',' << decision.reason << ',' << decision.primaryScore
                << ',' << decision.secondaryScore << '\n';
    m_decisions.flush();
}

void
MetricsCollector::MarkPolicyDecisionDuplicated(uint64_t frameId,
                                               uint64_t decisionTimeUs,
                                               uint8_t secondaryLink,
                                               const std::string& reason)
{
    auto decision = m_policyDecisions.find(frameId);
    NS_ABORT_MSG_IF(decision == m_policyDecisions.end(),
                    "Cannot update an unknown frame policy decision " << frameId);
    NS_ABORT_MSG_IF(decision->second.duplicated,
                    "Frame policy decision was already marked duplicated " << frameId);
    decision->second.duplicated = true;
    decision->second.decisionTimeUs = decisionTimeUs;
    decision->second.secondaryLink = std::to_string(secondaryLink);
    decision->second.reason = reason;
}

void
MetricsCollector::FinalizeMissingFrames()
{
    while (!m_expectedFrames.empty())
    {
        FrameResult result;
        result.runId = m_runId;
        result.frame = m_expectedFrames.begin()->second;
        result.incomplete = true;
        result.deadlineMiss = result.frame.deadlineUs > 0;
        result.missingSourcePacketIndices.reserve(result.frame.packetCount);
        for (uint32_t index = 0; index < result.frame.packetCount; ++index)
        {
            result.missingSourcePacketIndices.push_back(index);
        }
        RecordFrame(result);
    }
}

const std::vector<FrameResult>&
MetricsCollector::GetFrameResults() const
{
    return m_results;
}

} // namespace ns3
