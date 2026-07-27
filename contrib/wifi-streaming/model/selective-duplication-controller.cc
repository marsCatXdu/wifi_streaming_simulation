/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "selective-duplication-controller.h"

#include "multipath-sender.h"

#include "ns3/abort.h"
#include "ns3/log.h"

#include <algorithm>
#include <cmath>
#include <iomanip>

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("SelectiveDuplicationController");
NS_OBJECT_ENSURE_REGISTERED(SelectiveDuplicationController);

TypeId
SelectiveDuplicationController::GetTypeId()
{
    static TypeId tid = TypeId("ns3::SelectiveDuplicationController")
                            .SetParent<Object>()
                            .SetGroupName("WifiStreaming")
                            .AddConstructor<SelectiveDuplicationController>();
    return tid;
}

SelectiveDuplicationController::SelectiveDuplicationController() = default;

SelectiveDuplicationController::~SelectiveDuplicationController() = default;

void
SelectiveDuplicationController::SetSender(MultipathSender* sender)
{
    NS_ABORT_MSG_IF(!sender, "Selective duplication requires a sender");
    m_sender = sender;
}

void
SelectiveDuplicationController::SetRiskScorer(
    Callback<double, const PredictionSample&> scorer)
{
    NS_ABORT_MSG_IF(scorer.IsNull(), "Selective duplication risk scorer cannot be null");
    m_scorer = std::move(scorer);
}

void
SelectiveDuplicationController::SetPrimaryPath(uint8_t pathId)
{
    m_primaryPath = pathId;
}

void
SelectiveDuplicationController::SetProbabilityThreshold(double threshold)
{
    NS_ABORT_MSG_IF(!std::isfinite(threshold) || threshold < 0 || threshold > 1,
                    "Selective duplication probability threshold must be in [0, 1]");
    m_probabilityThreshold = threshold;
}

void
SelectiveDuplicationController::SetFrameBudget(double budget)
{
    NS_ABORT_MSG_IF(!std::isfinite(budget) || budget <= 0 || budget > 1,
                    "Selective duplication frame budget must be in (0, 1]");
    NS_ABORT_MSG_IF(m_bucketInitialized, "Cannot change budget after replay starts");
    m_frameBudget = budget;
}

void
SelectiveDuplicationController::SetBurstHorizonFrames(uint32_t frames)
{
    NS_ABORT_MSG_IF(frames == 0, "Selective duplication burst horizon must be positive");
    NS_ABORT_MSG_IF(m_bucketInitialized, "Cannot change burst horizon after replay starts");
    m_burstHorizonFrames = frames;
}

void
SelectiveDuplicationController::SetDecisionOffsetsUs(
    const std::vector<uint64_t>& offsetsUs)
{
    NS_ABORT_MSG_IF(offsetsUs.empty(), "Selective duplication requires a decision offset");
    std::set<uint64_t> resolved(offsetsUs.begin(), offsetsUs.end());
    NS_ABORT_MSG_IF(resolved.size() != offsetsUs.size(),
                    "Selective duplication decision offsets must be unique");
    NS_ABORT_MSG_IF(!resolved.contains(0),
                    "Selective duplication decision offsets must include T0");
    m_decisionOffsetsUs = std::move(resolved);
}

void
SelectiveDuplicationController::SetOutputFile(const std::string& runId,
                                              const std::string& fileName)
{
    NS_ABORT_MSG_IF(runId.empty(), "Selective duplication run ID cannot be empty");
    NS_ABORT_MSG_IF(fileName.empty(), "Selective duplication output path cannot be empty");
    NS_ABORT_MSG_IF(m_output.is_open(), "Selective duplication output configured twice");
    m_runId = runId;
    m_output.open(fileName, std::ios::out | std::ios::trunc);
    NS_ABORT_MSG_IF(!m_output, "Cannot open selective duplication output " << fileName);
    m_output << std::setprecision(12);
    WriteHeader();
}

void
SelectiveDuplicationController::InitializeBucket()
{
    m_tokenCapacity =
        std::max(1.0, m_frameBudget * static_cast<double>(m_burstHorizonFrames));
    m_tokenBalance = m_tokenCapacity;
    m_bucketInitialized = true;
}

void
SelectiveDuplicationController::NotifySnapshot(const PredictionSample& sample)
{
    if (sample.key.pathId != m_primaryPath || sample.key.copyId != 0 ||
        !m_decisionOffsetsUs.contains(sample.sampleOffsetUs))
    {
        return;
    }
    NS_ABORT_MSG_IF(!m_sender, "Selective duplication snapshot arrived without a sender");
    NS_ABORT_MSG_IF(m_scorer.IsNull(),
                    "Selective duplication snapshot arrived without a risk scorer");
    if (!m_bucketInitialized)
    {
        InitializeBucket();
    }
    if (sample.sampleOffsetUs == 0)
    {
        m_tokenBalance = std::min(m_tokenCapacity, m_tokenBalance + m_frameBudget);
        const bool inserted = m_frames.emplace(sample.key.frameId, FrameState{}).second;
        NS_ABORT_MSG_IF(!inserted,
                        "Selective duplication received duplicate T0 for frame "
                            << sample.key.frameId);
    }
    auto frame = m_frames.find(sample.key.frameId);
    NS_ABORT_MSG_IF(frame == m_frames.end(),
                    "Selective duplication received a non-T0 snapshot before T0");

    const double probability = m_scorer(sample);
    NS_ABORT_MSG_IF(!std::isfinite(probability) || probability < 0 || probability > 1,
                    "Selective duplication scorer returned an invalid probability");
    const double tokensBefore = m_tokenBalance;
    if (frame->second.resolved)
    {
        WriteDecision(sample, probability, tokensBefore, "already_resolved", false);
        return;
    }
    if (!sample.actionable)
    {
        WriteDecision(sample, probability, tokensBefore, "not_actionable", false);
        return;
    }
    if (probability < m_probabilityThreshold)
    {
        WriteDecision(sample, probability, tokensBefore, "below_threshold", false);
        return;
    }

    frame->second.resolved = true;
    if (m_tokenBalance + 1e-12 < 1.0)
    {
        ++m_budgetSuppressions;
        WriteDecision(sample, probability, tokensBefore, "budget_suppressed", false);
        return;
    }

    m_tokenBalance = std::max(0.0, m_tokenBalance - 1.0);
    const bool launched = m_sender->RequestSecondaryCopy(sample.key.frameId);
    if (!launched)
    {
        m_tokenBalance = std::min(m_tokenCapacity, m_tokenBalance + 1.0);
        WriteDecision(sample, probability, tokensBefore, "launch_rejected", false);
        return;
    }
    ++m_actions;
    WriteDecision(sample, probability, tokensBefore, "action", true);
}

uint64_t
SelectiveDuplicationController::GetActionCount() const
{
    return m_actions;
}

uint64_t
SelectiveDuplicationController::GetBudgetSuppressionCount() const
{
    return m_budgetSuppressions;
}

double
SelectiveDuplicationController::GetTokenBalance() const
{
    return m_tokenBalance;
}

void
SelectiveDuplicationController::WriteHeader()
{
    m_output << "run_id,frame_id,path_id,copy_id,sample_stage,sample_offset_us,"
                "sample_time_ns,deadline_time_ns,actionable,calibrated_probability,"
                "probability_threshold,frame_budget,token_capacity,tokens_before,"
                "tokens_after,decision,secondary_launched\n";
}

void
SelectiveDuplicationController::WriteDecision(const PredictionSample& sample,
                                               double probability,
                                               double tokensBefore,
                                               const std::string& decision,
                                               bool launched)
{
    if (!m_output)
    {
        return;
    }
    m_output << m_runId << ',' << sample.key.frameId << ',' << +sample.key.pathId << ','
             << +sample.key.copyId << ',' << sample.sampleStage << ',' << sample.sampleOffsetUs
             << ',' << sample.sampleTimeNs << ',' << sample.deadlineTimeNs << ','
             << sample.actionable << ',' << probability << ',' << m_probabilityThreshold << ','
             << m_frameBudget << ',' << m_tokenCapacity << ',' << tokensBefore << ','
             << m_tokenBalance << ',' << decision << ',' << launched << '\n';
    m_output.flush();
}

void
SelectiveDuplicationController::DoDispose()
{
    m_sender = nullptr;
    m_scorer = Callback<double, const PredictionSample&>();
    if (m_output.is_open())
    {
        m_output.close();
    }
    Object::DoDispose();
}

} // namespace ns3
