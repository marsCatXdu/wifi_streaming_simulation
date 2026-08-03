/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "adaptive-airtime-duplication-controller.h"

#include "multipath-sender.h"
#include "streaming-header.h"

#include "ns3/abort.h"
#include "ns3/eht-phy.h"
#include "ns3/log.h"
#include "ns3/simulator.h"
#include "ns3/wifi-phy.h"
#include "ns3/wifi-tx-vector.h"

#include <algorithm>
#include <cmath>
#include <iomanip>
#include <limits>
#include <string_view>

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("AdaptiveAirtimeDuplicationController");
NS_OBJECT_ENSURE_REGISTERED(AdaptiveAirtimeDuplicationController);

namespace
{

WifiTxVector
MakeEstimatorTxVector()
{
    return WifiTxVector(EhtPhy::GetEhtMcs5(),
                        0,
                        WIFI_PREAMBLE_EHT_MU,
                        NanoSeconds(800),
                        1,
                        1,
                        0,
                        MHz_u{20},
                        false);
}

std::string_view
PacketSelectionName(AdaptiveSecondaryPacketSelection selection)
{
    return selection == AdaptiveSecondaryPacketSelection::FULL_COPY
               ? "full_forward"
               : "primary_unacknowledged_reverse";
}

std::string_view
ConfiguredAdmissionPacketCostName(AdaptiveAdmissionPacketCost cost)
{
    return cost == AdaptiveAdmissionPacketCost::WHOLE_COPY ? "whole_copy"
                                                           : "launched_packet_set";
}

std::string_view
EffectiveAdmissionPacketCostName(AdaptiveSecondaryPacketSelection selection,
                                 AdaptiveAdmissionPacketCost cost)
{
    if (selection == AdaptiveSecondaryPacketSelection::FULL_COPY ||
        cost == AdaptiveAdmissionPacketCost::WHOLE_COPY)
    {
        return "whole_copy";
    }
    return "primary_unacknowledged_packet_set";
}

} // namespace

TypeId
AdaptiveAirtimeDuplicationController::GetTypeId()
{
    static TypeId tid = TypeId("ns3::AdaptiveAirtimeDuplicationController")
                            .SetParent<Object>()
                            .SetGroupName("WifiStreaming")
                            .AddConstructor<AdaptiveAirtimeDuplicationController>();
    return tid;
}

AdaptiveAirtimeDuplicationController::AdaptiveAirtimeDuplicationController()
    : m_referencePacketCount(10),
      m_referenceExpectedMacServiceBytes(
          10ULL * (1200 + StreamingHeader::SERIALIZED_SIZE + 36))
{
}

AdaptiveAirtimeDuplicationController::~AdaptiveAirtimeDuplicationController() = default;

void
AdaptiveAirtimeDuplicationController::SetSender(MultipathSender* sender)
{
    NS_ABORT_MSG_IF(!sender, "Adaptive airtime duplication requires a sender");
    m_sender = sender;
}

void
AdaptiveAirtimeDuplicationController::SetRiskScorer(
    Callback<ClosedLoopRiskScore, const PredictionSample&> scorer)
{
    NS_ABORT_MSG_IF(scorer.IsNull(), "Adaptive airtime risk scorer cannot be null");
    m_scorer = std::move(scorer);
}

void
AdaptiveAirtimeDuplicationController::SetAirtimeMeter(Ptr<SecondaryAirtimeMeter> meter)
{
    NS_ABORT_MSG_IF(!meter, "Adaptive airtime controller requires a meter");
    m_meter = meter;
    m_meter->SetMeasuredAirtimeCallback(
        MakeCallback(&AdaptiveAirtimeDuplicationController::NotifyMeasuredAirtime, this));
    m_meter->SetSettlementCallback(
        MakeCallback(&AdaptiveAirtimeDuplicationController::NotifySettlement, this));
}

void
AdaptiveAirtimeDuplicationController::SetPrimaryPath(uint8_t pathId)
{
    m_primaryPath = pathId;
}

void
AdaptiveAirtimeDuplicationController::SetSecondaryPacketSelection(
    AdaptiveSecondaryPacketSelection selection)
{
    NS_ABORT_MSG_IF(m_bucketInitialized || m_output.is_open(),
                    "Cannot change secondary packet selection after control starts");
    m_secondaryPacketSelection = selection;
}

void
AdaptiveAirtimeDuplicationController::SetAdmissionPacketCost(
    AdaptiveAdmissionPacketCost cost)
{
    NS_ABORT_MSG_IF(m_bucketInitialized || m_output.is_open(),
                    "Cannot change adaptive admission packet cost after control starts");
    m_admissionPacketCost = cost;
}

void
AdaptiveAirtimeDuplicationController::SetBudgetFraction(double fraction)
{
    NS_ABORT_MSG_IF(!std::isfinite(fraction) || fraction <= 0 || fraction > 1,
                    "Adaptive airtime budget fraction must be in (0, 1]");
    NS_ABORT_MSG_IF(m_bucketInitialized, "Cannot change budget after control starts");
    m_budgetFraction = fraction;
}

void
AdaptiveAirtimeDuplicationController::SetBucketHorizonUs(uint64_t horizonUs)
{
    NS_ABORT_MSG_IF(horizonUs == 0, "Adaptive airtime bucket horizon must be positive");
    NS_ABORT_MSG_IF(m_bucketInitialized, "Cannot change horizon after control starts");
    NS_ABORT_MSG_IF(m_initialBucketHorizonUs &&
                        !AreBucketHorizonsValid(horizonUs, *m_initialBucketHorizonUs),
                    "Adaptive airtime bucket horizon cannot be shorter than the initial "
                    "credit horizon");
    m_bucketHorizonUs = horizonUs;
}

void
AdaptiveAirtimeDuplicationController::SetInitialBucketHorizonUs(uint64_t horizonUs)
{
    NS_ABORT_MSG_IF(m_bucketInitialized,
                    "Cannot change initial bucket horizon after control starts");
    NS_ABORT_MSG_IF(!AreBucketHorizonsValid(m_bucketHorizonUs, horizonUs),
                    "Adaptive airtime initial bucket horizon must be positive and no larger "
                    "than the bucket horizon");
    m_initialBucketHorizonUs = horizonUs;
}

void
AdaptiveAirtimeDuplicationController::SetInitialShadowPrice(double price)
{
    NS_ABORT_MSG_IF(!std::isfinite(price) || price < 0 || price > 1,
                    "Adaptive airtime shadow price must be in [0, 1]");
    NS_ABORT_MSG_IF(m_bucketInitialized, "Cannot change initial price after control starts");
    m_initialShadowPrice = price;
    m_shadowPrice = price;
}

void
AdaptiveAirtimeDuplicationController::SetDualStep(double step)
{
    NS_ABORT_MSG_IF(!std::isfinite(step) || step < 0,
                    "Adaptive airtime dual step must be nonnegative");
    m_dualStep = step;
}

void
AdaptiveAirtimeDuplicationController::SetAdmissionUsesRetryInflation(bool enabled)
{
    m_admissionUsesRetryInflation = enabled;
}

void
AdaptiveAirtimeDuplicationController::SetCostSafetyFactor(double factor)
{
    NS_ABORT_MSG_IF(!std::isfinite(factor) || factor < 1,
                    "Adaptive airtime cost safety factor must be >= 1");
    m_costSafetyFactor = factor;
}

void
AdaptiveAirtimeDuplicationController::SetCostEwmaAlpha(double alpha)
{
    NS_ABORT_MSG_IF(!std::isfinite(alpha) || alpha <= 0 || alpha > 1,
                    "Adaptive airtime EWMA alpha must be in (0, 1]");
    m_costEwmaAlpha = alpha;
}

void
AdaptiveAirtimeDuplicationController::SetDecisionOffsetsUs(
    const std::vector<uint64_t>& offsetsUs)
{
    NS_ABORT_MSG_IF(m_bucketInitialized,
                    "Cannot change adaptive decision offsets after control starts");
    NS_ABORT_MSG_IF(offsetsUs.empty(), "Adaptive airtime requires a decision offset");
    std::set<uint64_t> resolved(offsetsUs.begin(), offsetsUs.end());
    NS_ABORT_MSG_IF(resolved.size() != offsetsUs.size(),
                    "Adaptive airtime decision offsets must be unique");
    NS_ABORT_MSG_IF(!resolved.contains(0),
                    "Adaptive airtime decision offsets must include T0");
    for (const auto& [offsetUs, price] : m_decisionOffsetShadowPrices)
    {
        (void)price;
        NS_ABORT_MSG_IF(!resolved.contains(offsetUs),
                        "Adaptive shadow-price override is not a decision offset");
    }
    for (const auto offsetUs : m_iFrameOnlyDecisionOffsetsUs)
    {
        NS_ABORT_MSG_IF(!resolved.contains(offsetUs),
                        "Adaptive I-frame restriction is not a decision offset");
    }
    m_decisionOffsetsUs = std::move(resolved);
}

void
AdaptiveAirtimeDuplicationController::SetDecisionOffsetShadowPrices(
    const std::map<uint64_t, double>& prices)
{
    NS_ABORT_MSG_IF(m_bucketInitialized,
                    "Cannot change adaptive shadow-price overrides after control starts");
    for (const auto& [offsetUs, price] : prices)
    {
        NS_ABORT_MSG_IF(!m_decisionOffsetsUs.contains(offsetUs),
                        "Adaptive shadow-price override is not a decision offset");
        NS_ABORT_MSG_IF(!std::isfinite(price) || price < 0 || price > 1,
                        "Adaptive shadow-price overrides must be in [0, 1]");
    }
    m_decisionOffsetShadowPrices = prices;
}

void
AdaptiveAirtimeDuplicationController::SetIFrameOnlyDecisionOffsetsUs(
    const std::vector<uint64_t>& offsetsUs)
{
    NS_ABORT_MSG_IF(m_bucketInitialized,
                    "Cannot change adaptive frame-type restrictions after control starts");
    std::set<uint64_t> resolved(offsetsUs.begin(), offsetsUs.end());
    NS_ABORT_MSG_IF(resolved.size() != offsetsUs.size(),
                    "Adaptive I-frame-only decision offsets must be unique");
    for (const auto offsetUs : resolved)
    {
        NS_ABORT_MSG_IF(!m_decisionOffsetsUs.contains(offsetUs),
                        "Adaptive I-frame restriction is not a decision offset");
    }
    m_iFrameOnlyDecisionOffsetsUs = std::move(resolved);
}

void
AdaptiveAirtimeDuplicationController::SetReferenceCopyDescriptor(
    uint32_t packetCount,
    uint64_t expectedMacServiceBytes)
{
    NS_ABORT_MSG_IF(packetCount == 0 || expectedMacServiceBytes == 0,
                    "Adaptive airtime reference copy must be nonempty");
    NS_ABORT_MSG_IF(m_bucketInitialized,
                    "Cannot change adaptive airtime reference after control starts");
    m_referencePacketCount = packetCount;
    m_referenceExpectedMacServiceBytes = expectedMacServiceBytes;
}

void
AdaptiveAirtimeDuplicationController::SetOutputFile(const std::string& runId,
                                                    const std::string& fileName)
{
    NS_ABORT_MSG_IF(runId.empty(), "Adaptive airtime run ID cannot be empty");
    NS_ABORT_MSG_IF(fileName.empty(), "Adaptive airtime output path cannot be empty");
    NS_ABORT_MSG_IF(m_output.is_open(), "Adaptive airtime output configured twice");
    m_runId = runId;
    m_output.open(fileName, std::ios::out | std::ios::trunc);
    NS_ABORT_MSG_IF(!m_output, "Cannot open adaptive airtime output " << fileName);
    m_output << std::setprecision(12);
    WriteHeader();
}

void
AdaptiveAirtimeDuplicationController::InitializeBucket(uint64_t nowNs)
{
    const uint64_t initialHorizonUs =
        m_initialBucketHorizonUs.value_or(m_bucketHorizonUs);
    NS_ABORT_MSG_IF(!AreBucketHorizonsValid(m_bucketHorizonUs, initialHorizonUs),
                    "Adaptive airtime bucket horizons are inconsistent");
    m_bucketCapacityUs = m_budgetFraction * static_cast<double>(m_bucketHorizonUs);
    m_initialCapacityUs = m_budgetFraction * static_cast<double>(initialHorizonUs);
    m_bucketBalanceUs = m_initialCapacityUs;
    if (m_meter)
    {
        m_meter->SetBudgetMetadata(m_budgetFraction, m_initialCapacityUs);
    }
    m_shadowPrice = m_initialShadowPrice;
    m_lastRefillTimeNs = nowNs;
    m_lastPriceUpdateNs = nowNs;
    m_bucketInitialized = true;
}

bool
AdaptiveAirtimeDuplicationController::AreBucketHorizonsValid(uint64_t bucketHorizonUs,
                                                             uint64_t initialHorizonUs)
{
    return bucketHorizonUs > 0 && initialHorizonUs > 0 &&
           initialHorizonUs <= bucketHorizonUs;
}

void
AdaptiveAirtimeDuplicationController::RefillBucket(uint64_t nowNs)
{
    if (!m_bucketInitialized)
    {
        InitializeBucket(nowNs);
        return;
    }
    if (nowNs <= m_lastRefillTimeNs)
    {
        return;
    }
    const double elapsedUs =
        static_cast<double>(nowNs - m_lastRefillTimeNs) / 1000.0;
    m_bucketBalanceUs =
        std::min(m_bucketCapacityUs,
                 m_bucketBalanceUs + m_budgetFraction * elapsedUs);
    m_lastRefillTimeNs = nowNs;
}

void
AdaptiveAirtimeDuplicationController::UpdateShadowPrice(uint64_t nowNs)
{
    if (nowNs < m_lastPriceUpdateNs)
    {
        return;
    }
    const double elapsedUs =
        static_cast<double>(nowNs - m_lastPriceUpdateNs) / 1000.0;
    const double allowance = m_budgetFraction * elapsedUs;
    const double reference = GetReferenceAirtimeUs();
    NS_ABORT_MSG_IF(!(reference > 0), "Adaptive airtime reference cost must be positive");
    m_shadowPrice = std::clamp(
        m_shadowPrice +
            m_dualStep * (m_measuredSinceLastT0Us - allowance) / reference,
        0.0,
        1.0);
    m_measuredSinceLastT0Us = 0;
    m_lastPriceUpdateNs = nowNs;
}

double
AdaptiveAirtimeDuplicationController::ResolveDecisionShadowPrice(uint64_t offsetUs) const
{
    const auto override = m_decisionOffsetShadowPrices.find(offsetUs);
    return override == m_decisionOffsetShadowPrices.end() ? m_shadowPrice
                                                          : override->second;
}

bool
AdaptiveAirtimeDuplicationController::IsFrameTypeEligible(
    const PredictionSample& sample) const
{
    return !m_iFrameOnlyDecisionOffsetsUs.contains(sample.sampleOffsetUs) ||
           sample.frameType == FrameType::I_FRAME;
}

double
AdaptiveAirtimeDuplicationController::EstimateSecondaryAirtimeUs(
    uint32_t packetCount,
    uint64_t expectedMacServiceBytes,
    double inflation) const
{
    NS_ABORT_MSG_IF(packetCount == 0, "Secondary airtime estimate requires packets");
    NS_ABORT_MSG_IF(expectedMacServiceBytes == 0,
                    "Secondary airtime estimate requires MAC service bytes");
    NS_ABORT_MSG_IF(!std::isfinite(inflation) || inflation < 1,
                    "Secondary airtime inflation must be finite and >= 1");
    const auto txVector = MakeEstimatorTxVector();
    const uint64_t rateBps =
        EhtPhy::GetEhtMcs5().GetDataRate(MHz_u{20}, NanoSeconds(800), 1);
    NS_ABORT_MSG_IF(rateBps == 0, "EHT MCS5 data rate resolved to zero");
    const Time preamble = WifiPhy::CalculatePhyPreambleAndHeaderDuration(txVector);
    const double preambleUs = preamble.GetSeconds() * 1e6;
    const double macBytes =
        static_cast<double>(expectedMacServiceBytes) + 38.0 * static_cast<double>(packetCount);
    const double payloadUs = (8.0 * macBytes / static_cast<double>(rateBps)) * 1e6;
    return m_costSafetyFactor * inflation * (preambleUs + payloadUs);
}

double
AdaptiveAirtimeDuplicationController::GetReferenceAirtimeUs() const
{
    NS_ABORT_MSG_IF(m_referencePacketCount == 0 || m_referenceExpectedMacServiceBytes == 0,
                    "Adaptive airtime reference copy is unset");
    return EstimateSecondaryAirtimeUs(m_referencePacketCount,
                                      m_referenceExpectedMacServiceBytes,
                                      1.0);
}

std::optional<std::vector<uint32_t>>
AdaptiveAirtimeDuplicationController::ResolveSecondaryPacketIndices(
    const PredictionSample& sample) const
{
    if (m_secondaryPacketSelection == AdaptiveSecondaryPacketSelection::FULL_COPY)
    {
        return std::nullopt;
    }
    NS_ABORT_MSG_IF(!m_sender, "Primary-deficit packet selection requires a sender");
    auto packetIndices = m_sender->GetUnacknowledgedPacketIndices(sample.key);
    NS_ABORT_MSG_IF(!packetIndices,
                    "Primary-deficit packet selection requires registered F2 packet state");
    NS_ABORT_MSG_IF(packetIndices->size() > sample.framePacketCount,
                    "Primary packet deficit exceeds the frame plan");
    for (const auto index : *packetIndices)
    {
        NS_ABORT_MSG_IF(index >= sample.framePacketCount,
                        "Primary packet deficit contains an invalid index");
    }
    std::reverse(packetIndices->begin(), packetIndices->end());
    return packetIndices;
}

void
AdaptiveAirtimeDuplicationController::NotifySnapshot(const PredictionSample& sample)
{
    if (sample.key.pathId != m_primaryPath || sample.key.copyId != 0 ||
        !m_decisionOffsetsUs.contains(sample.sampleOffsetUs))
    {
        return;
    }
    NS_ABORT_MSG_IF(!m_sender, "Adaptive airtime snapshot arrived without a sender");
    NS_ABORT_MSG_IF(m_scorer.IsNull(),
                    "Adaptive airtime snapshot arrived without a risk scorer");
    NS_ABORT_MSG_IF(!m_meter, "Adaptive airtime snapshot arrived without a meter");

    RefillBucket(sample.sampleTimeNs);
    if (sample.sampleOffsetUs == 0)
    {
        UpdateShadowPrice(sample.sampleTimeNs);
        const bool inserted = m_frames.emplace(sample.key.frameId, FrameState{}).second;
        NS_ABORT_MSG_IF(!inserted,
                        "Adaptive airtime received duplicate T0 for frame "
                            << sample.key.frameId);
    }
    auto frame = m_frames.find(sample.key.frameId);
    NS_ABORT_MSG_IF(frame == m_frames.end(),
                    "Adaptive airtime received a non-T0 snapshot before T0");

    const ClosedLoopRiskScore score = m_scorer(sample);
    NS_ABORT_MSG_IF(!std::isfinite(score.admissionScore) || score.admissionScore < 0 ||
                        score.admissionScore > 1,
                    "Adaptive airtime scorer returned an invalid admission score");
    NS_ABORT_MSG_IF(score.scoreName.empty() || score.modelId.empty() ||
                        score.sourceModelSha256.empty() ||
                        score.targetProvenanceSha256.empty(),
                    "Adaptive airtime scorer returned incomplete score provenance");
    const auto validProbability = [](const std::optional<double>& probability) {
        return !probability || (std::isfinite(*probability) && *probability >= 0 &&
                                *probability <= 1);
    };
    NS_ABORT_MSG_IF(!validProbability(score.primaryMissProbability) ||
                        !validProbability(score.completedTailProbability),
                    "Adaptive airtime scorer returned an invalid head probability");
    if (score.scoreKind ==
        ClosedLoopRiskScoreKind::CALIBRATED_PRIMARY_MISS_PROBABILITY)
    {
        NS_ABORT_MSG_IF(!score.primaryMissProbability ||
                            score.completedTailProbability ||
                            std::abs(score.admissionScore -
                                     *score.primaryMissProbability) > 1e-12,
                        "Adaptive calibrated-probability score has inconsistent heads");
    }
    else
    {
        NS_ABORT_MSG_IF(!score.primaryMissProbability ||
                            !score.completedTailProbability ||
                            score.featureContractSha256.empty() ||
                            score.combinerSha256.empty(),
                        "Adaptive weighted-head score has incomplete semantics");
    }

    const auto selectedPacketIndices = ResolveSecondaryPacketIndices(sample);
    std::optional<std::vector<uint32_t>> primaryAcknowledgedPacketIndices;
    if (selectedPacketIndices)
    {
        std::vector<bool> unacknowledged(sample.framePacketCount, false);
        for (const auto index : *selectedPacketIndices)
        {
            unacknowledged[index] = true;
        }
        primaryAcknowledgedPacketIndices.emplace();
        primaryAcknowledgedPacketIndices->reserve(sample.framePacketCount -
                                                   selectedPacketIndices->size());
        for (uint32_t index = 0; index < sample.framePacketCount; ++index)
        {
            if (!unacknowledged[index])
            {
                primaryAcknowledgedPacketIndices->push_back(index);
            }
        }
        NS_ABORT_MSG_IF(!sample.framePacketsTxSucceeded ||
                            *sample.framePacketsTxSucceeded !=
                                primaryAcknowledgedPacketIndices->size(),
                        "Primary-deficit packet state disagrees with its synchronous snapshot");
    }
    const auto* acknowledgedPacketIndices =
        primaryAcknowledgedPacketIndices ? &*primaryAcknowledgedPacketIndices : nullptr;
    std::optional<DelayedCopyDescriptor> descriptor;
    if (!selectedPacketIndices)
    {
        descriptor = m_sender->GetDelayedSecondaryCopyDescriptor(sample.key.frameId);
    }
    else if (!selectedPacketIndices->empty())
    {
        descriptor = m_sender->GetDelayedSecondaryPacketDescriptor(sample.key.frameId,
                                                                    *selectedPacketIndices);
    }
    std::optional<DelayedCopyDescriptor> wholeCopyDescriptor;
    const DelayedCopyDescriptor* admissionDescriptor = descriptor ? &*descriptor : nullptr;
    if (m_admissionPacketCost == AdaptiveAdmissionPacketCost::WHOLE_COPY)
    {
        wholeCopyDescriptor = m_sender->GetDelayedSecondaryCopyDescriptor(sample.key.frameId);
        admissionDescriptor = wholeCopyDescriptor ? &*wholeCopyDescriptor : nullptr;
    }
    const double referenceUs = GetReferenceAirtimeUs();
    double nominalUs = 0;
    double admissionUs = 0;
    double estimatedUs = 0;
    double normalizedCost = 0;
    double utility = std::numeric_limits<double>::quiet_NaN();
    const double shadowPrice = ResolveDecisionShadowPrice(sample.sampleOffsetUs);
    if (descriptor)
    {
        nominalUs = EstimateSecondaryAirtimeUs(descriptor->packetCount,
                                               descriptor->expectedMacServiceBytes,
                                               1.0);
        estimatedUs = EstimateSecondaryAirtimeUs(descriptor->packetCount,
                                                 descriptor->expectedMacServiceBytes,
                                                 m_retryInflation);
    }
    if (admissionDescriptor)
    {
        const double admissionNominalUs =
            EstimateSecondaryAirtimeUs(admissionDescriptor->packetCount,
                                       admissionDescriptor->expectedMacServiceBytes,
                                       1.0);
        const double admissionInflatedUs =
            EstimateSecondaryAirtimeUs(admissionDescriptor->packetCount,
                                       admissionDescriptor->expectedMacServiceBytes,
                                       m_retryInflation);
        admissionUs = m_admissionUsesRetryInflation ? admissionInflatedUs
                                                    : admissionNominalUs;
        normalizedCost = admissionUs / referenceUs;
        utility = score.admissionScore - shadowPrice * normalizedCost;
    }
    const uint32_t admissionPacketCount =
        admissionDescriptor ? admissionDescriptor->packetCount : 0;

    const double reservedUs = m_meter->GetReservedAirtimeUs();
    const double availableUs = m_bucketBalanceUs - reservedUs;
    const double balanceUs = m_bucketBalanceUs;

    if (frame->second.launched)
    {
        WriteDecision(sample,
                      score,
                      admissionUs,
                      estimatedUs,
                      admissionPacketCount,
                      referenceUs,
                      shadowPrice,
                      normalizedCost,
                      utility,
                      balanceUs,
                      reservedUs,
                      availableUs,
                      "already_resolved",
                      false,
                      descriptor ? &*descriptor : nullptr,
                      acknowledgedPacketIndices);
        return;
    }
    if (!IsFrameTypeEligible(sample))
    {
        WriteDecision(sample,
                      score,
                      admissionUs,
                      estimatedUs,
                      admissionPacketCount,
                      referenceUs,
                      shadowPrice,
                      normalizedCost,
                      utility,
                      balanceUs,
                      reservedUs,
                      availableUs,
                      "frame_type_restricted",
                      false,
                      descriptor ? &*descriptor : nullptr,
                      acknowledgedPacketIndices);
        return;
    }
    if (selectedPacketIndices && selectedPacketIndices->empty())
    {
        WriteDecision(sample,
                      score,
                      admissionUs,
                      estimatedUs,
                      admissionPacketCount,
                      referenceUs,
                      shadowPrice,
                      normalizedCost,
                      utility,
                      balanceUs,
                      reservedUs,
                      availableUs,
                      "no_primary_deficit",
                      false,
                      nullptr,
                      acknowledgedPacketIndices);
        return;
    }
    if (!sample.actionable)
    {
        WriteDecision(sample,
                      score,
                      admissionUs,
                      estimatedUs,
                      admissionPacketCount,
                      referenceUs,
                      shadowPrice,
                      normalizedCost,
                      utility,
                      balanceUs,
                      reservedUs,
                      availableUs,
                      "not_actionable",
                      false,
                      descriptor ? &*descriptor : nullptr,
                      acknowledgedPacketIndices);
        return;
    }
    NS_ABORT_MSG_IF(!descriptor,
                    "Adaptive airtime actionable frame lacks a delayed secondary descriptor");
    if (!(utility > 0))
    {
        WriteDecision(sample,
                      score,
                      admissionUs,
                      estimatedUs,
                      admissionPacketCount,
                      referenceUs,
                      shadowPrice,
                      normalizedCost,
                      utility,
                      balanceUs,
                      reservedUs,
                      availableUs,
                      "price_rejected",
                      false,
                      descriptor ? &*descriptor : nullptr,
                      acknowledgedPacketIndices);
        return;
    }
    if (!(availableUs + 1e-9 >= estimatedUs))
    {
        if (availableUs < 0)
        {
            m_meter->ObserveBudgetDebt(-availableUs);
        }
        WriteDecision(sample,
                      score,
                      admissionUs,
                      estimatedUs,
                      admissionPacketCount,
                      referenceUs,
                      shadowPrice,
                      normalizedCost,
                      utility,
                      balanceUs,
                      reservedUs,
                      availableUs,
                      "airtime_deferred",
                      false,
                      descriptor ? &*descriptor : nullptr,
                      acknowledgedPacketIndices);
        return;
    }

    SecondaryAirtimeReservation reservation;
    reservation.frameId = sample.key.frameId;
    reservation.packetCount = descriptor->packetCount;
    reservation.reservedAirtimeUs = estimatedUs;
    reservation.estimatedAirtimeUs = estimatedUs;
    reservation.nominalAirtimeUs = nominalUs;
    reservation.deadlineTimeNs = descriptor->deadlineTimeNs;
    reservation.expectedPacketIndices.insert(descriptor->packetIndices.begin(),
                                             descriptor->packetIndices.end());

    const bool launched = selectedPacketIndices
                              ? m_sender->RequestSecondaryPackets(
                                    sample.key.frameId,
                                    *selectedPacketIndices,
                                    "adaptive primary-deficit utility positive")
                              : m_sender->RequestSecondaryCopy(
                                    sample.key.frameId,
                                    "adaptive airtime utility positive");
    if (!launched)
    {
        WriteDecision(sample,
                      score,
                      admissionUs,
                      estimatedUs,
                      admissionPacketCount,
                      referenceUs,
                      shadowPrice,
                      normalizedCost,
                      utility,
                      balanceUs,
                      reservedUs,
                      availableUs,
                      "launch_rejected",
                      false,
                      descriptor ? &*descriptor : nullptr,
                      acknowledgedPacketIndices);
        return;
    }
    // Zero-offset packet sends are scheduled events, so registering here still
    // precedes every PHY trace while avoiding reservations for rejected launches.
    m_meter->RegisterLaunchedCopy(std::move(reservation));
    frame->second.launched = true;
    ++m_actions;
    WriteDecision(sample,
                  score,
                  admissionUs,
                  estimatedUs,
                  admissionPacketCount,
                  referenceUs,
                  shadowPrice,
                  normalizedCost,
                  utility,
                  balanceUs,
                  reservedUs,
                  availableUs,
                  "action",
                  true,
                  descriptor ? &*descriptor : nullptr,
                  acknowledgedPacketIndices);
}

void
AdaptiveAirtimeDuplicationController::NotifyMeasuredAirtime(uint64_t /*frameId*/,
                                                            double allocatedUs,
                                                            double /*ppduDurationUs*/)
{
    const uint64_t nowNs = static_cast<uint64_t>(
        std::max<int64_t>(0, Simulator::Now().GetNanoSeconds()));
    RefillBucket(nowNs);
    m_bucketBalanceUs -= allocatedUs;
    m_measuredSinceLastT0Us += allocatedUs;
    const double availableUs =
        m_bucketBalanceUs - (m_meter ? m_meter->GetReservedAirtimeUs() : 0.0);
    if (availableUs < 0)
    {
        m_meter->ObserveBudgetDebt(-availableUs);
    }
}

void
AdaptiveAirtimeDuplicationController::NotifySettlement(uint64_t /*frameId*/,
                                                       double /*releasedUs*/,
                                                       double measuredUs,
                                                       double nominalUs,
                                                       bool /*fallback*/)
{
    if (!(nominalUs > 0) || !(measuredUs > 0))
    {
        return;
    }
    const double ratio = std::max(1.0, measuredUs / nominalUs);
    m_retryInflation =
        (1.0 - m_costEwmaAlpha) * m_retryInflation + m_costEwmaAlpha * ratio;
}

uint64_t
AdaptiveAirtimeDuplicationController::GetActionCount() const
{
    return m_actions;
}

double
AdaptiveAirtimeDuplicationController::GetShadowPrice() const
{
    return m_shadowPrice;
}

double
AdaptiveAirtimeDuplicationController::GetBucketBalanceUs() const
{
    return m_bucketBalanceUs;
}

double
AdaptiveAirtimeDuplicationController::GetInitialCapacityUs() const
{
    return m_initialCapacityUs;
}

void
AdaptiveAirtimeDuplicationController::WriteHeader()
{
    m_output << "run_id,frame_id,sample_stage,sample_offset_us,sample_time_ns,"
                "actionable,admission_score,score_name,score_kind,score_model_id,"
                "score_source_model_sha256,score_target_provenance_sha256,"
                "score_feature_contract_sha256,score_combiner_sha256,"
                "primary_miss_probability,completed_tail_probability,"
                "admission_airtime_us,estimated_airtime_us,admission_packet_count,"
                "configured_admission_packet_cost,effective_admission_packet_cost,"
                "reference_airtime_us,shadow_price,dual_shadow_price,"
                "shadow_price_source,normalized_cost,net_utility,"
                "airtime_budget_fraction,bucket_capacity_us,bucket_balance_us,"
                "initial_bucket_capacity_us,"
                "reserved_airtime_us,available_airtime_us,measured_airtime_total_us,"
                "decision,secondary_launched,frame_packet_count,primary_acked_packets,"
                "primary_acked_packet_indices,secondary_packet_count,"
                "secondary_packet_indices,secondary_packet_order\n";
}

void
AdaptiveAirtimeDuplicationController::WriteDecision(const PredictionSample& sample,
                                                    const ClosedLoopRiskScore& score,
                                                    double admissionUs,
                                                    double estimatedUs,
                                                    uint32_t admissionPacketCount,
                                                    double referenceUs,
                                                    double shadowPrice,
                                                    double normalizedCost,
                                                    double utility,
                                                    double balanceUs,
                                                    double reservedUs,
                                                    double availableUs,
                                                    const std::string& decision,
                                                    bool launched,
                                                    const DelayedCopyDescriptor* descriptor,
                                                    const std::vector<uint32_t>*
                                                        primaryAcknowledgedPacketIndices)
{
    if (!m_output)
    {
        return;
    }
    const double measuredTotal =
        m_meter ? m_meter->GetMeasuredAirtimeTotalUs() : 0.0;
    m_output << m_runId << ',' << sample.key.frameId << ',' << sample.sampleStage << ','
             << sample.sampleOffsetUs << ',' << sample.sampleTimeNs << ','
             << sample.actionable << ',' << score.admissionScore << ',' << score.scoreName << ','
             << ClosedLoopRiskPredictor::GetScoreKindName(score.scoreKind) << ','
             << score.modelId << ',' << score.sourceModelSha256 << ','
             << score.targetProvenanceSha256 << ',' << score.featureContractSha256 << ','
             << score.combinerSha256 << ',';
    if (score.primaryMissProbability)
    {
        m_output << *score.primaryMissProbability;
    }
    m_output << ',';
    if (score.completedTailProbability)
    {
        m_output << *score.completedTailProbability;
    }
    m_output << ',' << admissionUs << ',' << estimatedUs << ',' << admissionPacketCount << ','
             << ConfiguredAdmissionPacketCostName(m_admissionPacketCost) << ','
             << EffectiveAdmissionPacketCostName(m_secondaryPacketSelection,
                                                 m_admissionPacketCost)
             << ',' << referenceUs << ',' << shadowPrice << ','
             << m_shadowPrice << ','
             << (m_decisionOffsetShadowPrices.contains(sample.sampleOffsetUs)
                     ? "offset_override"
                     : "global_dual")
             << ',' << normalizedCost << ',' << utility << ',' << m_budgetFraction << ','
             << m_bucketCapacityUs << ',' << balanceUs << ',' << m_initialCapacityUs << ','
             << reservedUs << ',' << availableUs << ',' << measuredTotal << ',' << decision
             << ',' << launched << ',' << sample.framePacketCount << ',';
    if (sample.framePacketsTxSucceeded)
    {
        m_output << *sample.framePacketsTxSucceeded;
    }
    m_output << ',';
    if (primaryAcknowledgedPacketIndices)
    {
        for (std::size_t index = 0;
             index < primaryAcknowledgedPacketIndices->size();
             ++index)
        {
            m_output << (index == 0 ? "" : ";")
                     << (*primaryAcknowledgedPacketIndices)[index];
        }
    }
    m_output << ',' << (descriptor ? descriptor->packetCount : 0) << ',';
    if (descriptor)
    {
        for (std::size_t index = 0; index < descriptor->packetIndices.size(); ++index)
        {
            m_output << (index == 0 ? "" : ";") << descriptor->packetIndices[index];
        }
    }
    m_output << ',' << (descriptor ? PacketSelectionName(m_secondaryPacketSelection) : "none")
             << '\n';
    m_output.flush();
}

void
AdaptiveAirtimeDuplicationController::DoDispose()
{
    m_sender = nullptr;
    m_scorer = Callback<ClosedLoopRiskScore, const PredictionSample&>();
    if (m_meter)
    {
        m_meter->SetMeasuredAirtimeCallback(SecondaryAirtimeMeter::MeasuredAirtimeCallback());
        m_meter->SetSettlementCallback(SecondaryAirtimeMeter::SettlementCallback());
    }
    m_meter = nullptr;
    if (m_output.is_open())
    {
        m_output.close();
    }
    Object::DoDispose();
}

} // namespace ns3
