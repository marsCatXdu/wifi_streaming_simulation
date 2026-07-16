/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef METRICS_COLLECTOR_H
#define METRICS_COLLECTOR_H

#include "streaming-header.h"

#include "ns3/object.h"

#include <fstream>
#include <map>
#include <optional>
#include <string>
#include <vector>

namespace ns3
{

struct FrameResult
{
    std::string runId;
    FrameDescriptor frame;
    std::string policy{"fixed_link"};
    uint8_t primaryLink{0};
    bool duplicated{false};
    uint64_t decisionTimeUs{0};
    double predictedDelayLink0{0};
    double predictedDelayLink1{0};
    std::optional<uint64_t> unionFirstPacketUs;
    std::optional<uint64_t> unionCompletionUs;
    std::optional<uint64_t> copy0CompletionUs;
    std::optional<uint64_t> copy1CompletionUs;
    uint32_t uniquePacketsReceived{0};
    uint32_t duplicatePacketsReceived{0};
    bool deadlineMiss{false};
    bool incomplete{false};
    std::string completionMode{"none"};
};

struct PolicyDecisionRecord
{
    std::string runId;
    uint64_t frameId{0};
    uint64_t decisionTimeUs{0};
    std::string policy{"fixed_link"};
    uint8_t primaryLink{0};
    bool duplicated{false};
    std::string secondaryLink;
    std::string reason{"configured single path"};
    double primaryScore{0};
    double secondaryScore{0};
};

/**
 * Owns the stable application-level CSV output schemas.
 */
class MetricsCollector : public Object
{
  public:
    static TypeId GetTypeId();
    MetricsCollector();
    ~MetricsCollector() override;

    void SetRunId(const std::string& runId);
    const std::string& GetRunId() const;
    void SetOutputFiles(const std::string& framesFile, const std::string& decisionsFile);
    void RegisterExpectedFrame(const FrameDescriptor& frame);
    void RecordFrame(const FrameResult& result);
    void RecordPolicyDecision(const PolicyDecisionRecord& decision);
    void FinalizeMissingFrames();

    const std::vector<FrameResult>& GetFrameResults() const;

  private:
    void WriteFrameHeader();
    void WriteDecisionHeader();

    std::string m_runId{"run"};
    std::ofstream m_frames;
    std::ofstream m_decisions;
    std::vector<FrameResult> m_results;
    std::map<uint64_t, PolicyDecisionRecord> m_policyDecisions;
    std::map<uint64_t, FrameDescriptor> m_expectedFrames;
};

} // namespace ns3

#endif // METRICS_COLLECTOR_H
