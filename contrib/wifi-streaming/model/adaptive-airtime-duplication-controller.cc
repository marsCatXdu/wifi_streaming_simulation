/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "adaptive-airtime-duplication-controller.h"

#include "multipath-sender.h"

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

AdaptiveAirtimeDuplicationController::AdaptiveAirtimeDuplicationController() = default;

AdaptiveAirtimeDuplicationController::~AdaptiveAirtimeDuplicationController() = default;

void
AdaptiveAirtimeDuplicationController::SetSender(MultipathSender* sender)
{
    NS_ABORT_MSG_IF(!sender, "Adaptive airtime duplication requires a sender");
    m_sender = sender;
}

void
AdaptiveAirtimeDuplicationController::SetRiskScorer(
    Callback<double, const PredictionSample&> scorer)
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
    m_bucketHorizonUs = horizonUs;
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
    NS_ABORT_MSG_IF(!std::isfinite(step) || step <= 0,
                    "Adaptive airtime dual step must be positive");
    m_dualStep = step;
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
    NS_ABORT_MSG_IF(offsetsUs.empty(), "Adaptive airtime requires a decision offset");
    std::set<uint64_t> resolved(offsetsUs.begin(), offsetsUs.end());
    NS_ABORT_MSG_IF(resolved.size() != offsetsUs.size(),
                    "Adaptive airtime decision offsets must be unique");
    NS_ABORT_MSG_IF(!resolved.contains(0),
                    "Adaptive airtime decision offsets must include T0");
    m_decisionOffsetsUs = std::move(resolved);
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
    m_bucketCapacityUs = m_budgetFraction * static_cast<double>(m_bucketHorizonUs);
    m_bucketBalanceUs = m_bucketCapacityUs;
    m_initialCapacityUs = m_bucketCapacityUs;
    m_shadowPrice = m_initialShadowPrice;
    m_lastRefillTimeNs = nowNs;
    m_lastPriceUpdateNs = nowNs;
    m_bucketInitialized = true;
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
    const double reference = ReferenceAirtimeUs();
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
AdaptiveAirtimeDuplicationController::EstimateSecondaryAirtimeUs(
    uint32_t packetCount,
    uint64_t expectedMacServiceBytes,
    double inflation) const
{
    NS_ABORT_MSG_IF(packetCount == 0, "Secondary airtime estimate requires packets");
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
AdaptiveAirtimeDuplicationController::ReferenceAirtimeUs() const
{
    // Normal 12 KB P-frame packetized into 1200-byte payloads => 10 packets.
    // Expected MAC service bytes for prediction runs use 36 B overhead/packet
    // plus the 28 B streaming header already counted in expectedMacServiceBytes.
    constexpr uint32_t packets = 10;
    constexpr uint64_t expectedMacServiceBytes = 10ULL * (1200 + 28 + 36);
    return EstimateSecondaryAirtimeUs(packets, expectedMacServiceBytes, 1.0);
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

    const double probability = m_scorer(sample);
    NS_ABORT_MSG_IF(!std::isfinite(probability) || probability < 0 || probability > 1,
                    "Adaptive airtime scorer returned an invalid probability");

    const auto descriptor = m_sender->GetDelayedSecondaryCopyDescriptor(sample.key.frameId);
    const double referenceUs = ReferenceAirtimeUs();
    double estimatedUs = 0;
    double normalizedCost = 0;
    double utility = std::numeric_limits<double>::quiet_NaN();
    if (descriptor)
    {
        estimatedUs = EstimateSecondaryAirtimeUs(descriptor->packetCount,
                                                 descriptor->expectedMacServiceBytes,
                                                 m_retryInflation);
        normalizedCost = estimatedUs / referenceUs;
        utility = probability - m_shadowPrice * normalizedCost;
    }

    const double reservedUs = m_meter->GetReservedAirtimeUs();
    const double availableUs = m_bucketBalanceUs - reservedUs;
    const double balanceUs = m_bucketBalanceUs;

    if (frame->second.launched)
    {
        WriteDecision(sample,
                      probability,
                      estimatedUs,
                      referenceUs,
                      normalizedCost,
                      utility,
                      balanceUs,
                      reservedUs,
                      availableUs,
                      "already_resolved",
                      false);
        return;
    }
    if (!sample.actionable)
    {
        WriteDecision(sample,
                      probability,
                      estimatedUs,
                      referenceUs,
                      normalizedCost,
                      utility,
                      balanceUs,
                      reservedUs,
                      availableUs,
                      "not_actionable",
                      false);
        return;
    }
    NS_ABORT_MSG_IF(!descriptor,
                    "Adaptive airtime actionable frame lacks a delayed secondary descriptor");
    if (!(utility > 0))
    {
        WriteDecision(sample,
                      probability,
                      estimatedUs,
                      referenceUs,
                      normalizedCost,
                      utility,
                      balanceUs,
                      reservedUs,
                      availableUs,
                      "price_rejected",
                      false);
        return;
    }
    if (!(availableUs + 1e-9 >= estimatedUs))
    {
        if (availableUs < 0)
        {
            m_meter->ObserveBudgetDebt(-availableUs);
        }
        WriteDecision(sample,
                      probability,
                      estimatedUs,
                      referenceUs,
                      normalizedCost,
                      utility,
                      balanceUs,
                      reservedUs,
                      availableUs,
                      "airtime_deferred",
                      false);
        return;
    }

    SecondaryAirtimeReservation reservation;
    reservation.frameId = sample.key.frameId;
    reservation.packetCount = descriptor->packetCount;
    reservation.reservedAirtimeUs = estimatedUs;
    reservation.estimatedAirtimeUs = estimatedUs;
    reservation.nominalAirtimeUs =
        EstimateSecondaryAirtimeUs(descriptor->packetCount,
                                    descriptor->expectedMacServiceBytes,
                                    1.0);
    reservation.deadlineTimeNs = descriptor->deadlineTimeNs;
    m_meter->RegisterLaunchedCopy(std::move(reservation));

    const bool launched =
        m_sender->RequestSecondaryCopy(sample.key.frameId,
                                       "adaptive airtime utility positive");
    if (!launched)
    {
        m_meter->ReleaseReservation(sample.key.frameId);
        WriteDecision(sample,
                      probability,
                      estimatedUs,
                      referenceUs,
                      normalizedCost,
                      utility,
                      balanceUs,
                      m_meter->GetReservedAirtimeUs(),
                      m_bucketBalanceUs - m_meter->GetReservedAirtimeUs(),
                      "launch_rejected",
                      false);
        return;
    }
    frame->second.launched = true;
    ++m_actions;
    WriteDecision(sample,
                  probability,
                  estimatedUs,
                  referenceUs,
                  normalizedCost,
                  utility,
                  balanceUs,
                  m_meter->GetReservedAirtimeUs(),
                  m_bucketBalanceUs - m_meter->GetReservedAirtimeUs(),
                  "action",
                  true);
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
    if (m_bucketBalanceUs < 0)
    {
        m_meter->ObserveBudgetDebt(-m_bucketBalanceUs);
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

void
AdaptiveAirtimeDuplicationController::WriteHeader()
{
    m_output << "run_id,frame_id,sample_stage,sample_offset_us,sample_time_ns,"
                "actionable,calibrated_probability,estimated_airtime_us,"
                "reference_airtime_us,shadow_price,normalized_cost,net_utility,"
                "airtime_budget_fraction,bucket_capacity_us,bucket_balance_us,"
                "reserved_airtime_us,available_airtime_us,measured_airtime_total_us,"
                "decision,secondary_launched\n";
}

void
AdaptiveAirtimeDuplicationController::WriteDecision(const PredictionSample& sample,
                                                    double probability,
                                                    double estimatedUs,
                                                    double referenceUs,
                                                    double normalizedCost,
                                                    double utility,
                                                    double balanceUs,
                                                    double reservedUs,
                                                    double availableUs,
                                                    const std::string& decision,
                                                    bool launched)
{
    if (!m_output)
    {
        return;
    }
    const double measuredTotal =
        m_meter ? m_meter->GetMeasuredAirtimeTotalUs() : 0.0;
    m_output << m_runId << ',' << sample.key.frameId << ',' << sample.sampleStage << ','
             << sample.sampleOffsetUs << ',' << sample.sampleTimeNs << ','
             << sample.actionable << ',' << probability << ',' << estimatedUs << ','
             << referenceUs << ',' << m_shadowPrice << ',' << normalizedCost << ','
             << utility << ',' << m_budgetFraction << ',' << m_bucketCapacityUs << ','
             << balanceUs << ',' << reservedUs << ',' << availableUs << ','
             << measuredTotal << ',' << decision << ',' << launched << '\n';
    m_output.flush();
}

void
AdaptiveAirtimeDuplicationController::DoDispose()
{
    m_sender = nullptr;
    m_scorer = Callback<double, const PredictionSample&>();
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
