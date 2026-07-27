/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "closed-loop-risk-predictor.h"

#include "ns3/abort.h"

#include <algorithm>
#include <cmath>
#include <limits>
#include <string>

namespace ns3
{

NS_OBJECT_ENSURE_REGISTERED(ClosedLoopRiskPredictor);

namespace
{

constexpr uint64_t REPORT_INTERVAL_US = 1000;
constexpr uint64_t OBSERVATION_DELAY_US = 1000;

double
Missing()
{
    return std::numeric_limits<double>::quiet_NaN();
}

} // namespace

TypeId
ClosedLoopRiskPredictor::GetTypeId()
{
    static TypeId tid = TypeId("ns3::ClosedLoopRiskPredictor")
                            .SetParent<Object>()
                            .SetGroupName("WifiStreaming")
                            .AddConstructor<ClosedLoopRiskPredictor>();
    return tid;
}

ClosedLoopRiskPredictor::ClosedLoopRiskPredictor() = default;

ClosedLoopRiskPredictor::~ClosedLoopRiskPredictor() = default;

double
ClosedLoopRiskPredictor::Score(const PredictionSample& sample)
{
    const PredictionSample* delayed = FindDelayedF1(sample);
    auto features = BuildFeatures(sample, delayed);
    const auto result = PredictionModelEvaluator::Evaluate(ResolveStage(sample.sampleOffsetUs),
                                                            features);
    Remember(sample);
    return result.calibratedProbability;
}

PredictionStage
ClosedLoopRiskPredictor::ResolveStage(uint64_t offsetUs)
{
    switch (offsetUs)
    {
    case 0:
        return PredictionStage::T0;
    case 1000:
        return PredictionStage::T1;
    case 2000:
        return PredictionStage::T2;
    case 4000:
        return PredictionStage::T4;
    default:
        NS_ABORT_MSG("No frozen predictor for sample offset " << offsetUs);
    }
}

double
ClosedLoopRiskPredictor::OptionalValue(const std::optional<uint64_t>& value)
{
    return value ? static_cast<double>(*value) : Missing();
}

double
ClosedLoopRiskPredictor::OptionalValue(const std::optional<uint32_t>& value)
{
    return value ? static_cast<double>(*value) : Missing();
}

double
ClosedLoopRiskPredictor::OptionalValue(const std::optional<uint16_t>& value)
{
    return value ? static_cast<double>(*value) : Missing();
}

double
ClosedLoopRiskPredictor::OptionalValue(const std::optional<uint8_t>& value)
{
    return value ? static_cast<double>(*value) : Missing();
}

double
ClosedLoopRiskPredictor::OptionalValue(const std::optional<double>& value)
{
    return value.value_or(Missing());
}

double
ClosedLoopRiskPredictor::AgeUs(uint64_t sampleTimeNs,
                               const std::optional<uint64_t>& eventTimeNs)
{
    if (!eventTimeNs)
    {
        return Missing();
    }
    NS_ABORT_MSG_IF(*eventTimeNs > sampleTimeNs, "Prediction age references a future event");
    return static_cast<double>(sampleTimeNs - *eventTimeNs) / 1000.0;
}

double
ClosedLoopRiskPredictor::EncodeFrameType(FrameType type)
{
    switch (type)
    {
    case FrameType::I_FRAME:
        return 0;
    case FrameType::P_FRAME:
        return 1;
    case FrameType::B_FRAME:
        return 2;
    default:
        return -1;
    }
}

double
ClosedLoopRiskPredictor::EncodeFrequencyBand(const std::optional<std::string>& band)
{
    if (!band)
    {
        return Missing();
    }
    if (*band == "2.4GHz")
    {
        return 0;
    }
    if (*band == "5GHz")
    {
        return 1;
    }
    if (*band == "6GHz")
    {
        return 2;
    }
    return -1;
}

const PredictionRollingSample*
ClosedLoopRiskPredictor::FindWindow(const PredictionSample& sample, uint64_t windowUs)
{
    auto found = std::find_if(sample.rolling.begin(),
                              sample.rolling.end(),
                              [windowUs](const auto& window) {
                                  return window.windowUs == windowUs;
                              });
    NS_ABORT_MSG_IF(found == sample.rolling.end(),
                    "Prediction snapshot lacks required rolling window " << windowUs);
    return &*found;
}

std::vector<double>
ClosedLoopRiskPredictor::BuildFeatures(const PredictionSample& current,
                                       const PredictionSample* delayedF1)
{
    std::vector<double> values;
    values.reserve(86);
    values.push_back(static_cast<double>(current.applicationSocketPacketBytesSubmitted));
    values.push_back(static_cast<double>(current.deadlineSlackUs));
    values.push_back(static_cast<double>(current.frameAgeUs));
    values.push_back(static_cast<double>(current.framePacketCount));
    values.push_back(static_cast<double>(current.frameSizeBytes));
    values.push_back(EncodeFrameType(current.frameType));
    values.push_back(static_cast<double>(current.packetsRemainingToSubmit));
    values.push_back(static_cast<double>(current.packetsSubmitted));

    if (!delayedF1)
    {
        values.resize(86, Missing());
        for (auto& value : values)
        {
            if (std::isfinite(value))
            {
                value = static_cast<double>(static_cast<float>(value));
            }
        }
        return values;
    }

    const auto* w1 = FindWindow(*delayedF1, 1000);
    const auto* w20 = FindWindow(*delayedF1, 20000);
    const auto* w5 = FindWindow(*delayedF1, 5000);
    values.push_back(static_cast<double>(w1->acknowledgedMacServiceBytes));
    values.push_back(static_cast<double>(w20->acknowledgedMacServiceBytes));
    values.push_back(static_cast<double>(w5->acknowledgedMacServiceBytes));
    values.push_back(OptionalValue(delayedF1->centerFrequencyMhz));
    values.push_back(OptionalValue(delayedF1->currentAckSignalDbm));
    values.push_back(OptionalValue(delayedF1->currentChannelWidthMhz));
    values.push_back(OptionalValue(delayedF1->currentGuardIntervalNs));
    values.push_back(OptionalValue(delayedF1->currentMcs));
    values.push_back(OptionalValue(delayedF1->currentNss));
    values.push_back(EncodeFrequencyBand(delayedF1->frequencyBand));
    values.push_back(AgeUs(delayedF1->sampleTimeNs, delayedF1->lastTxAttemptTimeNs));
    values.push_back(AgeUs(delayedF1->sampleTimeNs, delayedF1->lastPositiveAckTimeNs));
    values.push_back(static_cast<double>(w1->mpduAttemptFailures));
    values.push_back(static_cast<double>(w20->mpduAttemptFailures));
    values.push_back(static_cast<double>(w5->mpduAttemptFailures));
    values.push_back(static_cast<double>(w1->mpduAttempts));
    values.push_back(static_cast<double>(w20->mpduAttempts));
    values.push_back(static_cast<double>(w5->mpduAttempts));
    values.push_back(OptionalValue(w1->mpduFirstAttemptToAckMeanUs));
    values.push_back(OptionalValue(w20->mpduFirstAttemptToAckMeanUs));
    values.push_back(OptionalValue(w5->mpduFirstAttemptToAckMeanUs));
    values.push_back(OptionalValue(w1->mpduFirstAttemptToAckP95Us));
    values.push_back(OptionalValue(w20->mpduFirstAttemptToAckP95Us));
    values.push_back(OptionalValue(w5->mpduFirstAttemptToAckP95Us));
    values.push_back(OptionalValue(delayedF1->mpduLifetimeDropsTotal));
    values.push_back(static_cast<double>(w1->mpduPositiveAcks));
    values.push_back(static_cast<double>(w20->mpduPositiveAcks));
    values.push_back(static_cast<double>(w5->mpduPositiveAcks));
    values.push_back(OptionalValue(delayedF1->mpduPositiveAcksTotal));
    values.push_back(OptionalValue(delayedF1->mpduQueueDropsTotal));
    values.push_back(OptionalValue(w1->mpduQueueToAckMeanUs));
    values.push_back(OptionalValue(w20->mpduQueueToAckMeanUs));
    values.push_back(OptionalValue(w5->mpduQueueToAckMeanUs));
    values.push_back(OptionalValue(w1->mpduQueueToAckP95Us));
    values.push_back(OptionalValue(w20->mpduQueueToAckP95Us));
    values.push_back(OptionalValue(w5->mpduQueueToAckP95Us));
    values.push_back(static_cast<double>(w1->mpduRetries));
    values.push_back(static_cast<double>(w20->mpduRetries));
    values.push_back(static_cast<double>(w5->mpduRetries));
    values.push_back(OptionalValue(delayedF1->mpduRetriesTotal));
    values.push_back(OptionalValue(delayedF1->mpduRetryLimitDropsTotal));
    values.push_back(OptionalValue(w1->mpduRetryRatio));
    values.push_back(OptionalValue(w20->mpduRetryRatio));
    values.push_back(OptionalValue(w5->mpduRetryRatio));
    values.push_back(OptionalValue(delayedF1->mpduTerminalDropsTotal));
    values.push_back(OptionalValue(delayedF1->mpduTxAttemptFailuresTotal));
    values.push_back(OptionalValue(delayedF1->mpduTxAttemptsTotal));
    values.push_back(OptionalValue(w1->phyBusyFraction));
    values.push_back(OptionalValue(w20->phyBusyFraction));
    values.push_back(OptionalValue(w5->phyBusyFraction));
    values.push_back(w1->phyBusyTimeUs);
    values.push_back(w20->phyBusyTimeUs);
    values.push_back(w5->phyBusyTimeUs);
    values.push_back(OptionalValue(w1->phyIdleFraction));
    values.push_back(OptionalValue(w20->phyIdleFraction));
    values.push_back(OptionalValue(w5->phyIdleFraction));
    values.push_back(w1->phyIdleTimeUs);
    values.push_back(w20->phyIdleTimeUs);
    values.push_back(w5->phyIdleTimeUs);
    values.push_back(OptionalValue(w1->phyOtherFraction));
    values.push_back(OptionalValue(w20->phyOtherFraction));
    values.push_back(OptionalValue(w5->phyOtherFraction));
    values.push_back(w1->phyOtherTimeUs);
    values.push_back(w20->phyOtherTimeUs);
    values.push_back(w5->phyOtherTimeUs);
    values.push_back(OptionalValue(w1->phyRxFraction));
    values.push_back(OptionalValue(w20->phyRxFraction));
    values.push_back(OptionalValue(w5->phyRxFraction));
    values.push_back(w1->phyRxTimeUs);
    values.push_back(w20->phyRxTimeUs);
    values.push_back(w5->phyRxTimeUs);
    values.push_back(OptionalValue(w1->phyTxFraction));
    values.push_back(OptionalValue(w20->phyTxFraction));
    values.push_back(OptionalValue(w5->phyTxFraction));
    values.push_back(w1->phyTxTimeUs);
    values.push_back(w20->phyTxTimeUs);
    values.push_back(w5->phyTxTimeUs);
    values.push_back(OptionalValue(delayedF1->ppduTxCountTotal));
    NS_ABORT_MSG_IF(values.size() != PredictionModelEvaluator::GetFeatureNames().size(),
                    "Closed-loop feature count does not match frozen model");

    const auto names = PredictionModelEvaluator::GetFeatureNames();
    for (std::size_t index = 8; index < values.size(); ++index)
    {
        if (!std::isfinite(values[index]))
        {
            continue;
        }
        const std::string name(names[index]);
        if (name.find("signal") != std::string::npos ||
            name.find("mcs") != std::string::npos ||
            name.find("nss") != std::string::npos ||
            name.find("width") != std::string::npos ||
            name.find("guard_interval") != std::string::npos ||
            name.find("count") != std::string::npos ||
            name.find("attempt") != std::string::npos ||
            name.find("ack") != std::string::npos ||
            name.find("retr") != std::string::npos ||
            name.find("drop") != std::string::npos ||
            name.find("bytes") != std::string::npos)
        {
            values[index] = std::nearbyint(values[index]);
        }
    }
    // The frozen Python runtime constructs its raw matrix as float32 before
    // preprocessing. Preserve that contract at tree thresholds.
    for (auto& value : values)
    {
        if (std::isfinite(value))
        {
            value = static_cast<double>(static_cast<float>(value));
        }
    }
    return values;
}

const PredictionSample*
ClosedLoopRiskPredictor::FindDelayedF1(const PredictionSample& sample) const
{
    const uint64_t sampleTimeUs = sample.sampleTimeNs / 1000;
    if (sampleTimeUs < OBSERVATION_DELAY_US)
    {
        return nullptr;
    }
    const uint64_t reportTimeUs =
        ((sampleTimeUs - OBSERVATION_DELAY_US) / REPORT_INTERVAL_US) * REPORT_INTERVAL_US;
    auto stage = m_history.find(sample.sampleOffsetUs);
    if (stage == m_history.end())
    {
        return nullptr;
    }
    for (auto candidate = stage->second.rbegin(); candidate != stage->second.rend(); ++candidate)
    {
        if (candidate->sampleTimeNs / 1000 <= reportTimeUs)
        {
            return &*candidate;
        }
    }
    return nullptr;
}

void
ClosedLoopRiskPredictor::Remember(const PredictionSample& sample)
{
    auto& history = m_history[sample.sampleOffsetUs];
    history.push_back(sample);
    while (history.size() > 4)
    {
        history.pop_front();
    }
}

void
ClosedLoopRiskPredictor::DoDispose()
{
    m_history.clear();
    Object::DoDispose();
}

} // namespace ns3
