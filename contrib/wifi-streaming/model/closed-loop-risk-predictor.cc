/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "closed-loop-risk-predictor.h"

#include "prediction-model-evaluator.h"
#include "primary-tail-t4-model-evaluator.h"

#include "ns3/abort.h"

#include <algorithm>
#include <cmath>
#include <limits>

namespace ns3
{

NS_OBJECT_ENSURE_REGISTERED(ClosedLoopRiskPredictor);

namespace
{

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

ClosedLoopRiskScore
ClosedLoopRiskPredictor::Score(const PredictionSample& sample)
{
    ValidateFeatureContracts();
    const auto* report = sample.pollingReport ? &*sample.pollingReport : nullptr;
    const auto features = BuildFeatures(sample, report);
    const auto& identity = GetModelIdentity(sample.sampleOffsetUs);
    switch (sample.sampleOffsetUs)
    {
    case 0:
    {
        const auto result = PredictionModelEvaluator::Evaluate(
            PredictionStage::T0,
            std::span<const double>{features.data(), identity.featureCount});
        return {
            result.calibratedProbability,
            identity.scoreKind,
            identity.scoreName,
            identity.modelId,
            identity.sourceModelSha256,
            identity.targetProvenanceSha256,
            identity.featureContractSha256,
            identity.combinerSha256,
            result.calibratedProbability,
            std::nullopt,
        };
    }
    case 4000:
    {
        const auto result = PrimaryTailT4ModelEvaluator::Evaluate(features);
        return {
            result.admissionScore,
            identity.scoreKind,
            identity.scoreName,
            identity.modelId,
            identity.sourceModelSha256,
            identity.targetProvenanceSha256,
            identity.featureContractSha256,
            identity.combinerSha256,
            result.primaryMissProbability,
            result.completedTailProbability,
        };
    }
    default:
        NS_ABORT_MSG("No frozen predictor for sample offset " << sample.sampleOffsetUs);
    }
}

double
ClosedLoopRiskPredictor::ScorePrimaryMissProbability(const PredictionSample& sample)
{
    const auto score = Score(sample);
    NS_ABORT_MSG_IF(
        score.scoreKind != ClosedLoopRiskScoreKind::CALIBRATED_PRIMARY_MISS_PROBABILITY ||
            !score.primaryMissProbability || score.completedTailProbability,
        "Probability-only controller received a non-probability model score");
    return *score.primaryMissProbability;
}

const ClosedLoopRiskModelIdentity&
ClosedLoopRiskPredictor::GetModelIdentity(uint64_t offsetUs)
{
    static const ClosedLoopRiskModelIdentity t0{
        0,
        ClosedLoopRiskScoreKind::CALIBRATED_PRIMARY_MISS_PROBABILITY,
        "primary_miss_calibrated_probability",
        PredictionModelEvaluator::GetModelId(),
        PredictionModelEvaluator::GetTargetId(),
        "",
        PredictionModelEvaluator::GetSourceModelSha256(),
        PredictionModelEvaluator::GetTargetProvenanceSha256(),
        "",
        "",
        "",
        "",
        static_cast<uint32_t>(PredictionModelEvaluator::GetFeatureNames().size()),
    };
    const auto& t4Provenance = PrimaryTailT4ModelEvaluator::GetProvenance();
    static const ClosedLoopRiskModelIdentity t4{
        4000,
        ClosedLoopRiskScoreKind::WEIGHTED_HEAD_PROBABILITY_ADMISSION_SCORE,
        PrimaryTailT4ModelEvaluator::GetScoreName(),
        t4Provenance.modelId,
        t4Provenance.primaryMissTargetId,
        t4Provenance.completedTailTargetId,
        t4Provenance.sourceModelSha256,
        t4Provenance.targetProvenanceSha256,
        t4Provenance.featureContractSha256,
        t4Provenance.combinerSha256,
        t4Provenance.primaryMissModelSha256,
        t4Provenance.completedTailModelSha256,
        static_cast<uint32_t>(PrimaryTailT4ModelEvaluator::GetFeatureNames().size()),
    };
    switch (offsetUs)
    {
    case 0:
        return t0;
    case 4000:
        return t4;
    default:
        NS_ABORT_MSG("No frozen predictor for sample offset " << offsetUs);
    }
}

bool
ClosedLoopRiskPredictor::HasExactStagedModelContract()
{
    constexpr std::string_view t0SourceModelSha256 =
        "735e69ea4ad0ce615b6f827aaa8e3362135cf3f18e4c727d69920af9898d73bf";
    constexpr std::string_view t0TargetProvenanceSha256 =
        "e3d62e814e13aaeb5e4aab495ba7222b2a910a8268fe6f8645299c3451756f84";
    constexpr std::string_view t4SourceModelSha256 =
        "1a9afc23452952d87c7b5845a22260321ba302f38f1c3fb1eeaafadb0a12856c";
    constexpr std::string_view t4TargetProvenanceSha256 =
        "2b16b96bef68a32ec282e01b18a30506eaab933039c85e9bb1f6302da7b73be5";
    constexpr std::string_view t4FeatureContractSha256 =
        "8ccf33d6af8dffb8da758016acbd809a7cc054be4a1abc070d129c788b9c7cb0";
    constexpr std::string_view t4CombinerSha256 =
        "3d47b994ef5fcf579c73fb74492e0293dfe3ba377911f72f7a6b5fe764e6d9e0";
    constexpr std::string_view t4PrimaryMissModelSha256 =
        "8f8944a536166cb0f7dcc7c1a7bcf781f6a4d8fc25a995e3b8ed983b8886d98d";
    constexpr std::string_view t4CompletedTailModelSha256 =
        "ce787f6aaa9e2607c10bdb9227ae831eb6eb94e1499e9e1240e4d4ddc62a1fec";
    ValidateFeatureContracts();
    const auto& t0 = GetModelIdentity(0);
    const auto& t4 = GetModelIdentity(4000);
    return t0.scoreKind ==
               ClosedLoopRiskScoreKind::CALIBRATED_PRIMARY_MISS_PROBABILITY &&
           t0.scoreName == "primary_miss_calibrated_probability" &&
           t0.modelId == "commodity_polling_1ms_obss_primary_t0_v1" &&
           t0.primaryMissTargetId == "primary_copy_deadline_miss" &&
           t0.completedTailTargetId.empty() && t0.featureCount == 86 &&
           t0.sourceModelSha256 == t0SourceModelSha256 &&
           t0.targetProvenanceSha256 == t0TargetProvenanceSha256 &&
           t0.featureContractSha256.empty() && t0.combinerSha256.empty() &&
           t0.primaryMissModelSha256.empty() && t0.completedTailModelSha256.empty() &&
           t4.scoreKind ==
               ClosedLoopRiskScoreKind::WEIGHTED_HEAD_PROBABILITY_ADMISSION_SCORE &&
           t4.scoreName == "admission_score" &&
           t4.modelId == "primary_tail_t4_obss_v1" &&
           t4.primaryMissTargetId == "primary_miss_t4_v1" &&
           t4.completedTailTargetId == "completed_primary_latency_ge_12500us_t4_v1" &&
           t4.featureCount == 101 && t4.sourceModelSha256 == t4SourceModelSha256 &&
           t4.targetProvenanceSha256 == t4TargetProvenanceSha256 &&
           t4.featureContractSha256 == t4FeatureContractSha256 &&
           t4.combinerSha256 == t4CombinerSha256 &&
           t4.primaryMissModelSha256 == t4PrimaryMissModelSha256 &&
           t4.completedTailModelSha256 == t4CompletedTailModelSha256;
}

std::string_view
ClosedLoopRiskPredictor::GetScoreKindName(ClosedLoopRiskScoreKind kind)
{
    switch (kind)
    {
    case ClosedLoopRiskScoreKind::CALIBRATED_PRIMARY_MISS_PROBABILITY:
        return "calibrated_primary_miss_probability";
    case ClosedLoopRiskScoreKind::WEIGHTED_HEAD_PROBABILITY_ADMISSION_SCORE:
        return "weighted_head_probability_admission_score";
    }
    NS_ABORT_MSG("Unknown closed-loop risk score kind");
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
ClosedLoopRiskPredictor::FindWindow(const PredictionPollingReport& report, uint64_t windowUs)
{
    auto found = std::find_if(report.rolling.begin(),
                              report.rolling.end(),
                              [windowUs](const auto& window) {
                                  return window.windowUs == windowUs;
                              });
    NS_ABORT_MSG_IF(found == report.rolling.end(),
                    "Prediction polling report lacks required rolling window " << windowUs);
    return &*found;
}

ClosedLoopRiskPredictor::FeatureArray
ClosedLoopRiskPredictor::BuildFeatures(const PredictionSample& current,
                                       const PredictionPollingReport* report)
{
    FeatureArray values;
    values.fill(Missing());
    std::size_t featureCount = 0;
    const auto add = [&values, &featureCount](double value) {
        NS_ABORT_MSG_IF(featureCount >= values.size(),
                        "Closed-loop feature adapter exceeded its fixed capacity");
        values[featureCount++] = value;
    };
    const auto appendF2 = [&]() {
        add(OptionalValue(current.framePacketsMacEnqueued));
        add(OptionalValue(current.framePacketsMacDequeued));
        add(OptionalValue(current.framePacketsTxSucceeded));
        add(OptionalValue(current.frameMpduAttemptFailures));
        add(OptionalValue(current.framePacketsTerminallyDropped));
        add(OptionalValue(current.framePacketsCurrentlyQueued));
        add(OptionalValue(current.frameMacServiceBytesCurrentlyQueued));
        add(OptionalValue(current.macQueuePackets));
        add(OptionalValue(current.macQueueServiceBytes));
        add(AgeUs(current.sampleTimeNs, current.macQueueOldestEnqueueTimeNs));
        add(OptionalValue(current.packetsAheadOfFrame));
        add(OptionalValue(current.macServiceBytesAheadOfFrame));
        add(OptionalValue(current.framePacketsPendingPrimary));
        add(OptionalValue(current.frameMacServiceBytesNotAcknowledged));
        add(OptionalValue(current.frameMacServiceBytesPendingPrimary));
    };
    add(static_cast<double>(current.applicationSocketPacketBytesSubmitted));
    add(static_cast<double>(current.deadlineSlackUs));
    add(static_cast<double>(current.frameAgeUs));
    add(static_cast<double>(current.framePacketCount));
    add(static_cast<double>(current.frameSizeBytes));
    add(EncodeFrameType(current.frameType));
    add(static_cast<double>(current.packetsRemainingToSubmit));
    add(static_cast<double>(current.packetsSubmitted));

    if (!report)
    {
        featureCount = 86;
        appendF2();
        NS_ABORT_MSG_IF(featureCount != values.size(),
                        "Closed-loop F2 feature count does not match the T4 model");
        for (auto& value : values)
        {
            if (std::isfinite(value))
            {
                value = static_cast<double>(static_cast<float>(value));
            }
        }
        return values;
    }

    const auto* w1 = FindWindow(*report, 1000);
    const auto* w20 = FindWindow(*report, 20000);
    const auto* w5 = FindWindow(*report, 5000);
    add(static_cast<double>(w1->acknowledgedMacServiceBytes));
    add(static_cast<double>(w20->acknowledgedMacServiceBytes));
    add(static_cast<double>(w5->acknowledgedMacServiceBytes));
    add(OptionalValue(report->centerFrequencyMhz));
    add(OptionalValue(report->currentAckSignalDbm));
    add(OptionalValue(report->currentChannelWidthMhz));
    add(OptionalValue(report->currentGuardIntervalNs));
    add(OptionalValue(report->currentMcs));
    add(OptionalValue(report->currentNss));
    add(EncodeFrequencyBand(report->frequencyBand));
    add(AgeUs(report->captureTimeNs, report->lastTxAttemptTimeNs));
    add(AgeUs(report->captureTimeNs, report->lastPositiveAckTimeNs));
    add(static_cast<double>(w1->mpduAttemptFailures));
    add(static_cast<double>(w20->mpduAttemptFailures));
    add(static_cast<double>(w5->mpduAttemptFailures));
    add(static_cast<double>(w1->mpduAttempts));
    add(static_cast<double>(w20->mpduAttempts));
    add(static_cast<double>(w5->mpduAttempts));
    add(OptionalValue(w1->mpduFirstAttemptToAckMeanUs));
    add(OptionalValue(w20->mpduFirstAttemptToAckMeanUs));
    add(OptionalValue(w5->mpduFirstAttemptToAckMeanUs));
    add(OptionalValue(w1->mpduFirstAttemptToAckP95Us));
    add(OptionalValue(w20->mpduFirstAttemptToAckP95Us));
    add(OptionalValue(w5->mpduFirstAttemptToAckP95Us));
    add(OptionalValue(report->mpduLifetimeDropsTotal));
    add(static_cast<double>(w1->mpduPositiveAcks));
    add(static_cast<double>(w20->mpduPositiveAcks));
    add(static_cast<double>(w5->mpduPositiveAcks));
    add(OptionalValue(report->mpduPositiveAcksTotal));
    add(OptionalValue(report->mpduQueueDropsTotal));
    add(OptionalValue(w1->mpduQueueToAckMeanUs));
    add(OptionalValue(w20->mpduQueueToAckMeanUs));
    add(OptionalValue(w5->mpduQueueToAckMeanUs));
    add(OptionalValue(w1->mpduQueueToAckP95Us));
    add(OptionalValue(w20->mpduQueueToAckP95Us));
    add(OptionalValue(w5->mpduQueueToAckP95Us));
    add(static_cast<double>(w1->mpduRetries));
    add(static_cast<double>(w20->mpduRetries));
    add(static_cast<double>(w5->mpduRetries));
    add(OptionalValue(report->mpduRetriesTotal));
    add(OptionalValue(report->mpduRetryLimitDropsTotal));
    add(OptionalValue(w1->mpduRetryRatio));
    add(OptionalValue(w20->mpduRetryRatio));
    add(OptionalValue(w5->mpduRetryRatio));
    add(OptionalValue(report->mpduTerminalDropsTotal));
    add(OptionalValue(report->mpduTxAttemptFailuresTotal));
    add(OptionalValue(report->mpduTxAttemptsTotal));
    add(OptionalValue(w1->phyBusyFraction));
    add(OptionalValue(w20->phyBusyFraction));
    add(OptionalValue(w5->phyBusyFraction));
    add(w1->phyBusyTimeUs);
    add(w20->phyBusyTimeUs);
    add(w5->phyBusyTimeUs);
    add(OptionalValue(w1->phyIdleFraction));
    add(OptionalValue(w20->phyIdleFraction));
    add(OptionalValue(w5->phyIdleFraction));
    add(w1->phyIdleTimeUs);
    add(w20->phyIdleTimeUs);
    add(w5->phyIdleTimeUs);
    add(OptionalValue(w1->phyOtherFraction));
    add(OptionalValue(w20->phyOtherFraction));
    add(OptionalValue(w5->phyOtherFraction));
    add(w1->phyOtherTimeUs);
    add(w20->phyOtherTimeUs);
    add(w5->phyOtherTimeUs);
    add(OptionalValue(w1->phyRxFraction));
    add(OptionalValue(w20->phyRxFraction));
    add(OptionalValue(w5->phyRxFraction));
    add(w1->phyRxTimeUs);
    add(w20->phyRxTimeUs);
    add(w5->phyRxTimeUs);
    add(OptionalValue(w1->phyTxFraction));
    add(OptionalValue(w20->phyTxFraction));
    add(OptionalValue(w5->phyTxFraction));
    add(w1->phyTxTimeUs);
    add(w20->phyTxTimeUs);
    add(w5->phyTxTimeUs);
    add(OptionalValue(report->ppduTxCountTotal));
    NS_ABORT_MSG_IF(featureCount != PredictionModelEvaluator::GetFeatureNames().size(),
                    "Closed-loop F0/F1 feature count does not match the T0 model");
    appendF2();
    NS_ABORT_MSG_IF(featureCount != values.size(),
                    "Closed-loop F2 feature count does not match the T4 model");

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

void
ClosedLoopRiskPredictor::ValidateFeatureContracts()
{
    static const bool validated = []() {
        const auto t0Names = PredictionModelEvaluator::GetFeatureNames();
        const auto t4Names = PrimaryTailT4ModelEvaluator::GetFeatureNames();
        const auto t4PhysicalNames = PrimaryTailT4ModelEvaluator::GetPhysicalFeatureNames();
        NS_ABORT_MSG_IF(t0Names.size() != 86,
                        "Legacy T0 model does not have 86 features");
        NS_ABORT_MSG_IF(t4Names.size() != MAX_FEATURE_COUNT ||
                            t4PhysicalNames.size() != MAX_FEATURE_COUNT,
                        "Two-head T4 model does not have 101 logical and physical features");
        NS_ABORT_MSG_IF(!std::equal(t0Names.begin(), t0Names.end(), t4Names.begin()),
                        "T0 and T4 models do not share the same F0/F1 prefix");

        constexpr std::array<std::string_view, 15> f2Names{
            "frame_packets_mac_enqueued",
            "frame_packets_mac_dequeued",
            "frame_packets_tx_succeeded",
            "frame_mpdu_attempt_failures",
            "frame_packets_terminally_dropped",
            "frame_packets_currently_queued",
            "frame_mac_service_bytes_currently_queued",
            "mac_queue_packets",
            "mac_queue_service_bytes",
            "queue_oldest_age_us",
            "packets_ahead_of_frame",
            "mac_service_bytes_ahead_of_frame",
            "frame_packets_pending_primary",
            "frame_mac_service_bytes_not_acknowledged",
            "frame_mac_service_bytes_pending_primary",
        };
        for (std::size_t i = 0; i < f2Names.size(); ++i)
        {
            const std::size_t feature = t0Names.size() + i;
            NS_ABORT_MSG_IF(t4Names[feature] != f2Names[i] ||
                                t4PhysicalNames[feature] != f2Names[i],
                            "T4 F2 adapter order differs at feature " << feature);
        }
        return true;
    }();
    (void)validated;
}

void
ClosedLoopRiskPredictor::DoDispose()
{
    Object::DoDispose();
}

} // namespace ns3
