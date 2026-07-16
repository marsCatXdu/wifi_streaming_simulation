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
MetricsCollector::RecordFrame(const FrameResult& result)
{
    FrameResult stored = result;
    if (auto decision = m_policyDecisions.find(stored.frame.frameId);
        decision != m_policyDecisions.end())
    {
        stored.policy = decision->second.policy;
        stored.primaryLink = decision->second.primaryLink;
        stored.duplicated = decision->second.duplicated;
        stored.decisionTimeUs = decision->second.decisionTimeUs;
        stored.predictedDelayLink0 = decision->second.primaryScore;
        stored.predictedDelayLink1 = decision->second.secondaryScore;
    }
    m_results.push_back(stored);
    if (!m_frames)
    {
        return;
    }
    m_frames << stored.runId << ',' << stored.frame.frameId << ','
             << stored.frame.generationTimeNs / 1000 << ',' << stored.frame.frameSizeBytes << ','
             << stored.frame.packetCount << ',' << FrameTypeToString(stored.frame.frameType) << ','
             << stored.frame.deadlineUs << ',' << stored.policy << ',' << +stored.primaryLink << ','
             << stored.duplicated << ',' << stored.decisionTimeUs << ',' << std::setprecision(10)
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
    m_frames << ',' << stored.uniquePacketsReceived << ',' << stored.duplicatePacketsReceived << ','
             << stored.deadlineMiss << ',' << stored.incomplete << ',' << stored.completionMode << '\n';
    m_frames.flush();
}

void
MetricsCollector::RecordPolicyDecision(const PolicyDecisionRecord& decision)
{
    m_policyDecisions[decision.frameId] = decision;
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

const std::vector<FrameResult>&
MetricsCollector::GetFrameResults() const
{
    return m_results;
}

} // namespace ns3
