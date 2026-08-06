/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "distributional-shadow-t2-controller.h"

#include "canonical-secondary-airtime-estimator.h"
#include "streaming-header.h"

#include "ns3/abort.h"
#include "ns3/log.h"
#include "ns3/simulator.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <iomanip>
#include <limits>
#include <locale>
#include <numeric>
#include <ostream>
#include <sstream>
#include <stdexcept>

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("DistributionalShadowT2Controller");
NS_OBJECT_ENSURE_REGISTERED(DistributionalShadowT2Controller);

namespace
{

constexpr uint64_t NANOS_PER_MICROSECOND = 1000;
constexpr uint32_t QUEUE_MAX_DELAY_MS = 500;
constexpr uint32_t POST_SOCKET_MAC_OVERHEAD_BYTES = 36;
constexpr uint32_t REQUIRED_SUPPORT_MASK_VERSION = 2;
constexpr uint32_t REQUIRED_POLLING_SCHEMA_VERSION = 1;
constexpr std::string_view REQUIRED_SUPPORT_MASK = "0x3ffffffffdffff";
constexpr std::string_view POLICY_NAME = "distributional_shadow_duplication_t2";
constexpr std::string_view RUNTIME_CONTRACT_ID =
    "temporal-t2-shadow-borrow-runtime-v1";
constexpr std::string_view COST_ESTIMATOR_ID =
    "eht_mcs5_20mhz_gi800_nss1_one_ppdu_safety125_v1";
constexpr std::string_view LAUNCH_REASON =
    "distributional deadline rescue under shadow price at T2";
constexpr std::string_view CREDIT_ACCOUNTING_ID =
    "permanent_canonical_reservation_borrow_repay_v1";
constexpr std::string_view CONGESTION_SIGNAL_ID =
    "causal_running_mean_primary_phy_busy_fraction_20ms";
constexpr std::string_view SHADOW_REFERENCE_ID =
    "full_refit_congestion_tertile_5s_finite_horizon_v1";

static_assert(StreamingHeader::SERIALIZED_SIZE == 50,
              "Frozen descriptor contract requires a 50-byte streaming header");
static_assert(PREDICTION_TELEMETRY_SCHEMA_VERSION == 3,
              "Frozen controller requires telemetry schema version 3");
static_assert(PREDICTION_POLLING_SCHEMA_VERSION == REQUIRED_POLLING_SCHEMA_VERSION,
              "Frozen controller requires polling schema version 1");
static_assert(FEATURE_SUPPORT_MASK_VERSION == REQUIRED_SUPPORT_MASK_VERSION,
              "Frozen controller requires support-mask version 2");

bool
ContainsCsvDelimiter(const std::string& value)
{
    return value.find_first_of(",\r\n") != std::string::npos;
}

void
WriteCsvString(std::ostream& output, std::string_view value)
{
    const bool quote = value.find_first_of(",\"\r\n") != std::string_view::npos;
    if (!quote)
    {
        output << value;
        return;
    }
    output << '\"';
    for (const char character : value)
    {
        if (character == '\"')
        {
            output << "\"\"";
        }
        else
        {
            output << character;
        }
    }
    output << '\"';
}

void
WriteOptionalUint64(std::ostream& output, const std::optional<uint64_t>& value)
{
    if (value)
    {
        output << *value;
    }
}

void
WriteOptionalUint8(std::ostream& output, const std::optional<uint8_t>& value)
{
    if (value)
    {
        output << +*value;
    }
}

void
WritePacketIndices(std::ostream& output, const DelayedCopyDescriptor& descriptor)
{
    for (std::size_t index = 0; index < descriptor.packetIndices.size(); ++index)
    {
        if (index != 0)
        {
            output << ';';
        }
        output << descriptor.packetIndices[index];
    }
}

template <std::size_t N>
void
WriteDoubleArray(std::ostream& output, const std::array<double, N>& values)
{
    for (std::size_t index = 0; index < values.size(); ++index)
    {
        if (index != 0)
        {
            output << ';';
        }
        output << values[index];
    }
}

std::string
JsonEscape(const std::string& value)
{
    std::ostringstream escaped;
    for (const unsigned char character : value)
    {
        switch (character)
        {
        case '\"':
            escaped << "\\\"";
            break;
        case '\\':
            escaped << "\\\\";
            break;
        case '\b':
            escaped << "\\b";
            break;
        case '\f':
            escaped << "\\f";
            break;
        case '\n':
            escaped << "\\n";
            break;
        case '\r':
            escaped << "\\r";
            break;
        case '\t':
            escaped << "\\t";
            break;
        default:
            if (character < 0x20)
            {
                escaped << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                        << static_cast<unsigned>(character) << std::dec << std::setfill(' ');
            }
            else
            {
                escaped << character;
            }
        }
    }
    return escaped.str();
}

bool
NearlyEqual(double left, double right)
{
    return std::isfinite(left) && std::isfinite(right) &&
           std::abs(left - right) <=
               DistributionalShadowT2Controller::ACCOUNTING_TOLERANCE_US;
}

bool
AccumulatedSumsNearlyEqual(double left,
                           double right,
                           std::size_t termCount,
                           std::size_t extraAdditions)
{
    if (!std::isfinite(left) || !std::isfinite(right))
    {
        return false;
    }
    const double epsilon = std::numeric_limits<double>::epsilon();
    const double additionCount =
        2.0 * static_cast<double>(termCount) +
        static_cast<double>(extraAdditions);
    const double relativeError = additionCount * epsilon;
    if (!(relativeError < 1.0))
    {
        return false;
    }
    const double scale = std::max(std::abs(left), std::abs(right));
    const double forwardErrorBound =
        relativeError / (1.0 - relativeError) * scale;
    return std::abs(left - right) <=
           DistributionalShadowT2Controller::ACCOUNTING_TOLERANCE_US +
               forwardErrorBound;
}

uint32_t
ExpectedPacketCount(uint32_t frameSizeBytes, uint32_t packetPayloadBytes)
{
    return 1 + (frameSizeBytes - 1) / packetPayloadBytes;
}

uint64_t
CurrentTimeNs()
{
    const int64_t now = Simulator::Now().GetNanoSeconds();
    NS_ABORT_MSG_IF(now < 0, "Distributional-shadow callback time is negative");
    return static_cast<uint64_t>(now);
}

double
QuantileNearestRank(const std::vector<double>& sorted, double probability)
{
    NS_ABORT_MSG_IF(sorted.empty() || !std::isfinite(probability) || probability < 0 ||
                        probability > 1,
                    "Invalid distribution quantile request");
    const auto rank = static_cast<std::size_t>(
        std::ceil(probability * static_cast<double>(sorted.size())));
    const std::size_t index = rank == 0 ? 0 : rank - 1;
    return sorted.at(std::min(index, sorted.size() - 1));
}

void
WriteDistribution(std::ostream& output,
                  const std::vector<double>& values)
{
    output << "{\"finite_count\": " << values.size();
    if (values.empty())
    {
        output << ", \"minimum\": null, \"p50\": null, \"p90\": null, "
                  "\"p99\": null, \"maximum\": null, \"mean\": null}";
        return;
    }
    NS_ABORT_MSG_IF(std::any_of(values.begin(), values.end(), [](double value) {
                        return !std::isfinite(value);
                    }),
                    "Distribution contains a non-finite value");
    std::vector<double> sorted = values;
    std::sort(sorted.begin(), sorted.end());
    const double sum = std::accumulate(sorted.begin(), sorted.end(), 0.0);
    output << ", \"minimum\": " << sorted.front()
           << ", \"p50\": " << QuantileNearestRank(sorted, 0.50)
           << ", \"p90\": " << QuantileNearestRank(sorted, 0.90)
           << ", \"p99\": " << QuantileNearestRank(sorted, 0.99)
           << ", \"maximum\": " << sorted.back()
           << ", \"mean\": " << sum / static_cast<double>(sorted.size()) << '}';
}

template <typename T, std::size_t N>
void
WriteJsonArray(std::ostream& output, const std::array<T, N>& values)
{
    output << '[';
    for (std::size_t index = 0; index < values.size(); ++index)
    {
        if (index != 0)
        {
            output << ", ";
        }
        output << values[index];
    }
    output << ']';
}

} // namespace

TypeId
DistributionalShadowT2Controller::GetTypeId()
{
    static TypeId tid = TypeId("ns3::DistributionalShadowT2Controller")
                            .SetParent<Object>()
                            .SetGroupName("WifiStreaming")
                            .AddConstructor<DistributionalShadowT2Controller>();
    return tid;
}

DistributionalShadowT2Controller::DistributionalShadowT2Controller()
    : m_ledger({BUDGET_FRACTION,
                POSITIVE_BALANCE_CAPACITY_US,
                INITIAL_CREDIT_US,
                MEASUREMENT_STOP_NS})
{
    NS_ABORT_MSG_IF(!m_ledger.IsConfigured(),
                    "Distributional-shadow ledger configuration failed");
}

DistributionalShadowT2Controller::~DistributionalShadowT2Controller() = default;

void
DistributionalShadowT2Controller::SetSender(MultipathSender* sender)
{
    NS_ABORT_MSG_IF(m_started,
                    "Cannot change distributional-shadow sender after control starts");
    NS_ABORT_MSG_IF(!sender, "Distributional-shadow controller requires a sender");
    NS_ABORT_MSG_IF(m_sender && m_sender != sender,
                    "Distributional-shadow sender was configured twice");
    m_sender = sender;
}

void
DistributionalShadowT2Controller::SetAirtimeMeter(Ptr<SecondaryAirtimeMeter> meter)
{
    NS_ABORT_MSG_IF(m_started,
                    "Cannot change distributional-shadow meter after control starts");
    NS_ABORT_MSG_IF(!meter, "Distributional-shadow controller requires an airtime meter");
    NS_ABORT_MSG_IF(m_meter && m_meter != meter,
                    "Distributional-shadow meter was configured twice");
    if (!m_meter)
    {
        m_meter = meter;
        m_meter->SetMeasurementWindow(MEASUREMENT_START_NS, MEASUREMENT_STOP_NS);
        m_meter->SetQueueMaxDelayMs(QUEUE_MAX_DELAY_MS);
        m_meter->SetBudgetMetadata(BUDGET_FRACTION, INITIAL_CREDIT_US);
        m_meter->SetMeasuredAirtimeCallback(
            MakeCallback(&DistributionalShadowT2Controller::NotifyMeasuredAirtime, this));
        m_meter->SetSettlementCallback(
            MakeCallback(&DistributionalShadowT2Controller::NotifySettlement, this));
    }
}

void
DistributionalShadowT2Controller::SetFrameContract(uint64_t deadlineUs,
                                                    uint32_t pFrameSizeBytes,
                                                    uint32_t iFrameSizeBytes,
                                                    uint32_t packetPayloadBytes)
{
    NS_ABORT_MSG_IF(m_started || m_pendingPrimary || m_pairedFrames != 0,
                    "Distributional-shadow frame contract changed after control began");
    NS_ABORT_MSG_IF(deadlineUs <= T2_OFFSET_US ||
                        deadlineUs >
                            std::numeric_limits<uint64_t>::max() /
                                NANOS_PER_MICROSECOND ||
                        pFrameSizeBytes == 0 || iFrameSizeBytes < pFrameSizeBytes ||
                        packetPayloadBytes == 0,
                    "Distributional-shadow frame contract is invalid");
    m_frameDeadlineUs = deadlineUs;
    m_pFrameSizeBytes = pFrameSizeBytes;
    m_iFrameSizeBytes = iFrameSizeBytes;
    m_packetPayloadBytes = packetPayloadBytes;
}

void
DistributionalShadowT2Controller::SetOutputFiles(const std::string& runId,
                                                  const std::string& decisionsFile,
                                                  const std::string& summaryFile)
{
    NS_ABORT_MSG_IF(m_started || m_decisions.is_open() || !m_summaryFile.empty(),
                    "Distributional-shadow outputs may be configured only once");
    NS_ABORT_MSG_IF(runId.empty() || ContainsCsvDelimiter(runId),
                    "Distributional-shadow run ID must be nonempty and CSV-safe");
    NS_ABORT_MSG_IF(decisionsFile.empty() || summaryFile.empty() ||
                        decisionsFile == summaryFile,
                    "Distributional-shadow output paths must be distinct and nonempty");
    m_runId = runId;
    m_summaryFile = summaryFile;
    m_decisions.open(decisionsFile, std::ios::out | std::ios::trunc);
    NS_ABORT_MSG_IF(!m_decisions,
                    "Cannot open distributional-shadow decisions " << decisionsFile);
    m_decisions.imbue(std::locale::classic());
    m_decisions << std::setprecision(std::numeric_limits<double>::max_digits10);
    WriteDecisionHeader();
}

std::string_view
DistributionalShadowT2Controller::GetPolicyName()
{
    return POLICY_NAME;
}

std::string_view
DistributionalShadowT2Controller::GetRuntimeContractId()
{
    return RUNTIME_CONTRACT_ID;
}

std::string_view
DistributionalShadowT2Controller::GetRuntimeContractSha256()
{
    return TemporalT2DistributionModelEvaluator::GetProvenance().runtimeContractSha256;
}

std::string_view
DistributionalShadowT2Controller::GetCostEstimatorId()
{
    return COST_ESTIMATOR_ID;
}

void
DistributionalShadowT2Controller::StartControl()
{
    if (m_started)
    {
        return;
    }
    NS_ABORT_MSG_IF(!m_sender, "Distributional-shadow sender is not configured");
    NS_ABORT_MSG_IF(!m_meter, "Distributional-shadow meter is not configured");
    NS_ABORT_MSG_IF(m_runId.empty() || !m_decisions || m_summaryFile.empty(),
                    "Distributional-shadow outputs are not configured");
    NS_ABORT_MSG_IF(!TemporalT2DistributionPredictor::HasExactModelContract() ||
                        !TemporalT2DistributionModelEvaluator::HasExactRuntimeContract(),
                    "Distributional-shadow compiled model contract differs");
    const auto& provenance = TemporalT2DistributionModelEvaluator::GetProvenance();
    NS_ABORT_MSG_IF(provenance.runtimeContractId != RUNTIME_CONTRACT_ID,
                    "Distributional-shadow runtime contract ID differs");
    NS_ABORT_MSG_IF(!NearlyEqual(
                        TemporalT2DistributionModelEvaluator::GetCanonicalPFrameReservationUs(),
                        1983.760667318285),
                    "Distributional-shadow canonical reservation differs");
    NS_ABORT_MSG_IF(!NearlyEqual(
                        TemporalT2DistributionModelEvaluator::GetMaximumRepayableCreditUs(),
                        372000.0),
                    "Distributional-shadow maximum credit differs");
    NS_ABORT_MSG_IF(GetReconciledMeterReservedUs() != 0,
                    "Distributional-shadow meter has reservations before control starts");
    NS_ABORT_MSG_IF(!m_ledger.Initialize(MEASUREMENT_START_NS) ||
                        !m_ledger.IsOperational(),
                    "Distributional-shadow ledger initialization failed");
    const auto maximumCredit = m_ledger.GetMaximumGeneratedCreditUs();
    NS_ABORT_MSG_IF(!maximumCredit ||
                        !NearlyEqual(*maximumCredit,
                                     TemporalT2DistributionModelEvaluator::
                                         GetMaximumRepayableCreditUs()),
                    "Distributional-shadow ledger and reference credit differ");
    m_started = true;
}

std::optional<std::string>
DistributionalShadowT2Controller::FindEndpointError(const PredictionSample& sample,
                                                     bool primary) const
{
    if (sample.runId != m_runId)
    {
        return "endpoint run ID differs from controller run ID";
    }
    const uint8_t expectedPath = primary ? PRIMARY_PATH_ID : SECONDARY_PATH_ID;
    const uint8_t expectedCopy = primary ? PRIMARY_COPY_ID : SECONDARY_COPY_ID;
    if (sample.key.pathId != expectedPath || sample.key.copyId != expectedCopy)
    {
        return primary ? "endpoint is not primary path 1/copy 0"
                       : "endpoint is not hypothetical secondary path 0/copy 1";
    }
    if (sample.sampleStage != "T2" || sample.sampleOffsetUs != T2_OFFSET_US)
    {
        return "endpoint is not the frozen T2 stage";
    }
    if (sample.generationTimeNs >
            std::numeric_limits<uint64_t>::max() -
                T2_OFFSET_US * NANOS_PER_MICROSECOND ||
        sample.sampleTimeNs !=
            sample.generationTimeNs + T2_OFFSET_US * NANOS_PER_MICROSECOND)
    {
        return "endpoint timestamp differs from generation plus T2";
    }
    if (sample.sampleTimeNs != CurrentTimeNs())
    {
        return "endpoint callback time differs from its sample time";
    }
    if (sample.sampleTimeNs < MEASUREMENT_START_NS ||
        sample.sampleTimeNs >= MEASUREMENT_STOP_NS)
    {
        return "endpoint is outside the frozen measurement window";
    }
    const uint64_t deadlineDurationNs =
        m_frameDeadlineUs * NANOS_PER_MICROSECOND;
    if (sample.generationTimeNs >
            std::numeric_limits<uint64_t>::max() - deadlineDurationNs ||
        sample.deadlineTimeNs != sample.generationTimeNs + deadlineDurationNs)
    {
        return "endpoint deadline differs from the configured frame contract";
    }
    uint32_t expectedFrameSizeBytes = 0;
    if (sample.frameType == FrameType::P_FRAME)
    {
        expectedFrameSizeBytes = m_pFrameSizeBytes;
    }
    else if (sample.frameType == FrameType::I_FRAME)
    {
        expectedFrameSizeBytes = m_iFrameSizeBytes;
    }
    else
    {
        return "endpoint frame type is unsupported";
    }
    if (sample.frameSizeBytes != expectedFrameSizeBytes ||
        sample.framePacketCount !=
            ExpectedPacketCount(expectedFrameSizeBytes, m_packetPayloadBytes))
    {
        return "endpoint frame metadata differs from the configured frame contract";
    }
    return std::nullopt;
}

std::optional<std::string>
DistributionalShadowT2Controller::FindDescriptorError(
    const PredictionSample& primary,
    const DelayedCopyDescriptor& descriptor)
{
    if (descriptor.frameId != primary.key.frameId ||
        descriptor.framePacketCount != primary.framePacketCount ||
        descriptor.deadlineTimeNs != primary.deadlineTimeNs)
    {
        return "delayed descriptor disagrees with immutable frame metadata";
    }
    if (descriptor.packetCount != primary.framePacketCount ||
        descriptor.packetIndices.size() != descriptor.packetCount)
    {
        return "delayed descriptor is not a canonical full frame copy";
    }
    for (uint32_t index = 0; index < descriptor.packetCount; ++index)
    {
        if (descriptor.packetIndices[index] != index)
        {
            return "delayed descriptor packet order is not canonical ascending order";
        }
    }
    constexpr uint64_t PER_PACKET_OVERHEAD =
        StreamingHeader::SERIALIZED_SIZE + POST_SOCKET_MAC_OVERHEAD_BYTES;
    if (descriptor.packetCount >
        (std::numeric_limits<uint64_t>::max() - primary.frameSizeBytes) /
            PER_PACKET_OVERHEAD)
    {
        return "delayed descriptor service-byte recomputation overflows";
    }
    const uint64_t expectedBytes =
        primary.frameSizeBytes + descriptor.packetCount * PER_PACKET_OVERHEAD;
    if (descriptor.expectedMacServiceBytes == 0 ||
        descriptor.expectedMacServiceBytes != expectedBytes)
    {
        return "delayed descriptor MAC service bytes differ from frozen formula";
    }
    return std::nullopt;
}

void
DistributionalShadowT2Controller::NotifySnapshot(const PredictionSample& sample)
{
    if (sample.sampleOffsetUs == 0)
    {
        return;
    }
    StartControl();
    NS_ABORT_MSG_IF(sample.sampleOffsetUs != T2_OFFSET_US,
                    "Distributional-shadow controller received unsupported sample offset");

    if (sample.key.pathId == PRIMARY_PATH_ID && sample.key.copyId == PRIMARY_COPY_ID)
    {
        NS_ABORT_MSG_IF(m_pendingPrimary,
                        "Distributional-shadow primary endpoints were interleaved");
        const auto error = FindEndpointError(sample, true);
        NS_ABORT_MSG_IF(error,
                        "Invalid distributional-shadow primary endpoint: " << *error);
        m_pendingPrimary = sample;
        return;
    }

    NS_ABORT_MSG_IF(sample.key.pathId != SECONDARY_PATH_ID ||
                        sample.key.copyId != SECONDARY_COPY_ID,
                    "Distributional-shadow endpoint has unsupported path/copy identity");
    NS_ABORT_MSG_IF(!m_pendingPrimary,
                    "Distributional-shadow secondary arrived before its primary");
    const auto error = FindEndpointError(sample, false);
    NS_ABORT_MSG_IF(error,
                    "Invalid distributional-shadow secondary endpoint: " << *error);
    PredictionSample primary = std::move(*m_pendingPrimary);
    m_pendingPrimary.reset();
    ProcessPair(primary, sample);
}

void
DistributionalShadowT2Controller::CaptureAccountingBefore(
    uint64_t sampleTimeNs,
    DecisionEvidence& evidence)
{
    NS_ABORT_MSG_IF(!m_ledger.Advance(sampleTimeNs) || !m_ledger.IsOperational(),
                    "Distributional-shadow causal ledger advance failed");
    const auto remaining = m_ledger.GetRemainingRefillUs();
    const auto repayable = m_ledger.GetRepayableCreditUs();
    NS_ABORT_MSG_IF(!remaining || !repayable || !std::isfinite(*remaining) ||
                        *remaining < 0 || !std::isfinite(*repayable) || *repayable < 0,
                    "Distributional-shadow ledger snapshot is invalid");
    evidence.ledgerBalanceBeforeUs = m_ledger.GetBalanceUs();
    evidence.ledgerDebtBeforeUs = m_ledger.GetDebtUs();
    evidence.ledgerRemainingRefillBeforeUs = *remaining;
    evidence.ledgerRepayableBeforeUs = *repayable;
    evidence.ledgerDebitedBeforeUs = m_ledger.GetPermanentDebitedUs();
    evidence.meterReservedBeforeUs = GetReconciledMeterReservedUs();
}

void
DistributionalShadowT2Controller::CaptureAccountingAfter(DecisionEvidence& evidence)
{
    evidence.ledgerBalanceAfterUs = m_ledger.GetBalanceUs();
    evidence.ledgerDebtAfterUs = m_ledger.GetDebtUs();
    evidence.ledgerDebitedAfterUs = m_ledger.GetPermanentDebitedUs();
    evidence.meterReservedAfterUs = GetReconciledMeterReservedUs();
    NS_ABORT_MSG_IF(!m_ledger.IsOperational() ||
                        !std::isfinite(evidence.ledgerBalanceAfterUs) ||
                        !std::isfinite(evidence.ledgerDebtAfterUs) ||
                        !std::isfinite(evidence.ledgerDebitedAfterUs),
                    "Distributional-shadow ledger failed after decision");
    if (evidence.secondaryLaunched)
    {
        NS_ABORT_MSG_IF(!NearlyEqual(evidence.ledgerDebitedAfterUs -
                                         evidence.ledgerDebitedBeforeUs,
                                     evidence.canonicalReservedAirtimeUs) ||
                            !NearlyEqual(evidence.meterReservedAfterUs -
                                             evidence.meterReservedBeforeUs,
                                         evidence.canonicalReservedAirtimeUs),
                        "Distributional-shadow action debit does not reconcile");
    }
    else
    {
        NS_ABORT_MSG_IF(!NearlyEqual(evidence.ledgerDebitedBeforeUs,
                                     evidence.ledgerDebitedAfterUs) ||
                            !NearlyEqual(evidence.meterReservedBeforeUs,
                                         evidence.meterReservedAfterUs),
                        "Distributional-shadow rejection changed accounting");
    }
}

void
DistributionalShadowT2Controller::UpdateCongestion(
    const PredictionSample& primary,
    DecisionEvidence& evidence)
{
    NS_ABORT_MSG_IF(!primary.pollingReport || primary.pollingReport->rolling.size() != 3 ||
                        primary.pollingReport->rolling[2].windowUs != 20000 ||
                        !primary.pollingReport->rolling[2].phyBusyFraction,
                    "Distributional-shadow primary busy signal is unavailable");
    const double busy = *primary.pollingReport->rolling[2].phyBusyFraction;
    NS_ABORT_MSG_IF(!std::isfinite(busy) || busy < 0 || busy > 1,
                    "Distributional-shadow primary busy signal is invalid");
    m_primaryBusySum += busy;
    ++m_congestionObservationCount;
    NS_ABORT_MSG_IF(!std::isfinite(m_primaryBusySum) || m_congestionObservationCount == 0,
                    "Distributional-shadow running busy state overflowed");
    evidence.congestionUpdated = true;
    evidence.currentPrimaryBusy20ms = busy;
    evidence.congestionObservationCount = m_congestionObservationCount;
    evidence.runningPrimaryBusy20ms =
        m_primaryBusySum / static_cast<double>(m_congestionObservationCount);
    const double relativeDecisionTimeUs =
        static_cast<double>(primary.sampleTimeNs - MEASUREMENT_START_NS) /
        NANOS_PER_MICROSECOND;
    evidence.timeBin =
        TemporalT2DistributionModelEvaluator::GetTimeBin(relativeDecisionTimeUs);
    evidence.congestionRegime =
        TemporalT2DistributionModelEvaluator::GetCongestionRegime(
            *evidence.timeBin,
            evidence.runningPrimaryBusy20ms);
}

void
DistributionalShadowT2Controller::ProcessPair(const PredictionSample& primary,
                                               const PredictionSample& secondary)
{
    const auto history = m_predictor.ObservePair(primary, secondary);
    const bool inserted = m_decidedFrameIds.insert(primary.key.frameId).second;
    NS_ABORT_MSG_IF(!inserted,
                    "Distributional-shadow frame was decided more than once");
    ++m_pairedFrames;

    DecisionEvidence evidence;
    evidence.primary = &primary;
    evidence.secondary = &secondary;
    evidence.history = history;
    evidence.insideDecisionWindow = primary.sampleTimeNs >= DECISION_START_NS &&
                                    primary.sampleTimeNs < DECISION_STOP_NS;
    CaptureAccountingBefore(primary.sampleTimeNs, evidence);

    if (!evidence.insideDecisionWindow)
    {
        evidence.status = DecisionStatus::OUTSIDE_DECISION_WINDOW;
    }
    else if (!history.ready)
    {
        evidence.status = DecisionStatus::HISTORY_WARMUP;
    }
    else if (!primary.actionable)
    {
        evidence.status = DecisionStatus::NOT_ACTIONABLE;
    }
    else
    {
        UpdateCongestion(primary, evidence);
        if (!TemporalT2DistributionPredictor::PassesFrameGate(primary.frameType))
        {
            evidence.status = DecisionStatus::FRAME_TYPE_RESTRICTED;
        }
        else
        {
            evidence.descriptorChecked = true;
            evidence.descriptor =
                m_sender->GetDelayedSecondaryCopyDescriptor(primary.key.frameId);
            if (!evidence.descriptor)
            {
                evidence.status = DecisionStatus::DESCRIPTOR_UNAVAILABLE;
            }
            else
            {
                const auto descriptorError =
                    FindDescriptorError(primary, *evidence.descriptor);
                NS_ABORT_MSG_IF(descriptorError,
                                "Invalid distributional-shadow descriptor: "
                                    << *descriptorError);
                evidence.canonicalNominalAirtimeUs =
                    CanonicalSecondaryAirtimeEstimator::EstimateNominalUs(
                        evidence.descriptor->packetCount,
                        evidence.descriptor->expectedMacServiceBytes);
                evidence.canonicalReservedAirtimeUs =
                    CanonicalSecondaryAirtimeEstimator::EstimateUs(
                        evidence.descriptor->packetCount,
                        evidence.descriptor->expectedMacServiceBytes,
                        COST_SAFETY_FACTOR,
                        1.0);
                NS_ABORT_MSG_IF(
                    !std::isfinite(evidence.canonicalNominalAirtimeUs) ||
                        !(evidence.canonicalNominalAirtimeUs > 0) ||
                        !std::isfinite(evidence.canonicalReservedAirtimeUs) ||
                        !(evidence.canonicalReservedAirtimeUs > 0) ||
                        !NearlyEqual(evidence.canonicalReservedAirtimeUs,
                                     COST_SAFETY_FACTOR *
                                         evidence.canonicalNominalAirtimeUs),
                    "Distributional-shadow canonical secondary cost is invalid");

                evidence.earlierUnsettledLaunches = CountUnsettledLaunches();
                evidence.secondaryStateActionDirty =
                    evidence.earlierUnsettledLaunches != 0;
                if (evidence.secondaryStateActionDirty)
                {
                    ++m_actionDirtyScored;
                }
                evidence.model = m_predictor.Evaluate(primary.key.frameId);
                evidence.featureEvaluated = true;
                ++m_featureEvaluated;
                const double reward = evidence.model->deadlineRescueReward;
                NS_ABORT_MSG_IF(!std::isfinite(reward) || reward < 0 ||
                                    !std::isfinite(evidence.model->tail18CdfGain),
                                "Distributional-shadow model result is invalid");
                if (!(reward > 0))
                {
                    evidence.status = DecisionStatus::NONPOSITIVE_REWARD;
                }
                else
                {
                    ++m_positiveRewardCount;
                    m_positiveRewards.push_back(reward);
                    evidence.rewardDensityPerUs =
                        reward / evidence.canonicalReservedAirtimeUs;
                    evidence.opportunityCostPerUs =
                        TemporalT2DistributionModelEvaluator::GetOpportunityCost(
                            *evidence.timeBin,
                            *evidence.congestionRegime,
                            evidence.ledgerRepayableBeforeUs);
                    NS_ABORT_MSG_IF(std::isnan(evidence.opportunityCostPerUs) ||
                                        evidence.opportunityCostPerUs < 0,
                                    "Distributional-shadow opportunity cost is invalid");
                    if (std::isfinite(evidence.opportunityCostPerUs))
                    {
                        m_finiteOpportunityCosts.push_back(
                            evidence.opportunityCostPerUs);
                    }
                    else
                    {
                        ++m_infiniteOpportunityCosts;
                    }
                    evidence.passesOpportunityPrice =
                        evidence.rewardDensityPerUs >=
                        evidence.opportunityCostPerUs;
                    if (!evidence.passesOpportunityPrice)
                    {
                        evidence.status = DecisionStatus::OPPORTUNITY_PRICE_REJECTED;
                    }
                    else
                    {
                        ++m_opportunityPassed;
                        evidence.horizonAdmissionConsidered = true;
                        ++m_horizonAdmissionConsidered;
                        evidence.horizonAdmitted =
                            m_ledger.CanDebit(evidence.canonicalReservedAirtimeUs);
                        if (!evidence.horizonAdmitted)
                        {
                            evidence.status = DecisionStatus::HORIZON_CREDIT_REJECTED;
                        }
                        else
                        {
                            ++m_horizonAdmitted;
                            evidence.launchAttempted = true;
                            ++m_launchAttempted;
                            evidence.secondaryLaunched =
                                m_sender->RequestSecondaryCopy(
                                    primary.key.frameId,
                                    std::string(LAUNCH_REASON));
                            if (!evidence.secondaryLaunched)
                            {
                                evidence.status = DecisionStatus::LAUNCH_REJECTED;
                            }
                            else
                            {
                                NS_ABORT_MSG_IF(
                                    !m_ledger.Debit(
                                        evidence.canonicalReservedAirtimeUs),
                                    "Distributional-shadow permanent debit failed");
                                m_meter->ObserveBudgetDebt(m_ledger.GetDebtUs());
                                m_meter->RegisterLaunchedCopy(
                                    BuildReservation(
                                        *evidence.descriptor,
                                        evidence.canonicalNominalAirtimeUs,
                                        evidence.canonicalReservedAirtimeUs));
                                LaunchedFrameState launched;
                                launched.nominalAirtimeUs =
                                    evidence.canonicalNominalAirtimeUs;
                                launched.reservedAirtimeUs =
                                    evidence.canonicalReservedAirtimeUs;
                                launched.remainingReservedUs =
                                    evidence.canonicalReservedAirtimeUs;
                                const bool stored =
                                    m_launchedFrames
                                        .emplace(primary.key.frameId, launched)
                                        .second;
                                const bool indexed =
                                    m_launchedFrameIds.insert(primary.key.frameId)
                                        .second;
                                NS_ABORT_MSG_IF(!stored || !indexed,
                                                "Distributional-shadow frame launched twice");
                                m_expectedMeterReservedUs +=
                                    evidence.canonicalReservedAirtimeUs;
                                m_canonicalNominalLaunchedSumUs +=
                                    evidence.canonicalNominalAirtimeUs;
                                m_canonicalReservedLaunchedSumUs +=
                                    evidence.canonicalReservedAirtimeUs;
                                m_predictedRewardLaunchedSum += reward;
                                m_tail18GainLaunchedSum +=
                                    evidence.model->tail18CdfGain;
                                ++m_actionsByTimeBin.at(*evidence.timeBin);
                                m_reservationUsByTimeBin.at(*evidence.timeBin) +=
                                    evidence.canonicalReservedAirtimeUs;
                                ++m_actionsByRegime.at(*evidence.congestionRegime);
                                m_reservationUsByRegime.at(
                                    *evidence.congestionRegime) +=
                                    evidence.canonicalReservedAirtimeUs;
                                NS_ABORT_MSG_IF(
                                    !std::isfinite(m_expectedMeterReservedUs) ||
                                        !std::isfinite(
                                            m_canonicalNominalLaunchedSumUs) ||
                                        !std::isfinite(
                                            m_canonicalReservedLaunchedSumUs) ||
                                        !std::isfinite(
                                            m_predictedRewardLaunchedSum) ||
                                        !std::isfinite(m_tail18GainLaunchedSum),
                                    "Distributional-shadow launch totals overflowed");
                                GetReconciledMeterReservedUs();
                                evidence.status = DecisionStatus::ACTION;
                            }
                        }
                    }
                }
            }
        }
    }

    ++m_statusCounts[StatusIndex(evidence.status)];
    CaptureAccountingAfter(evidence);
    WriteDecision(evidence);
}

SecondaryAirtimeReservation
DistributionalShadowT2Controller::BuildReservation(
    const DelayedCopyDescriptor& descriptor,
    double nominalAirtimeUs,
    double reservedAirtimeUs)
{
    SecondaryAirtimeReservation reservation;
    reservation.frameId = descriptor.frameId;
    reservation.packetCount = descriptor.packetCount;
    reservation.reservedAirtimeUs = reservedAirtimeUs;
    reservation.estimatedAirtimeUs = reservedAirtimeUs;
    reservation.nominalAirtimeUs = nominalAirtimeUs;
    reservation.deadlineTimeNs = descriptor.deadlineTimeNs;
    reservation.expectedPacketIndices.insert(descriptor.packetIndices.begin(),
                                             descriptor.packetIndices.end());
    return reservation;
}

uint64_t
DistributionalShadowT2Controller::CountUnsettledLaunches() const
{
    return static_cast<uint64_t>(std::count_if(
        m_launchedFrames.begin(),
        m_launchedFrames.end(),
        [](const auto& entry) { return !entry.second.settled; }));
}

double
DistributionalShadowT2Controller::GetReconciledMeterReservedUs() const
{
    NS_ABORT_MSG_IF(!m_meter,
                    "Distributional-shadow meter reservation queried before setup");
    const double actual = m_meter->GetReservedAirtimeUs();
    NS_ABORT_MSG_IF(!std::isfinite(actual) || actual < 0 ||
                        !std::isfinite(m_expectedMeterReservedUs) ||
                        m_expectedMeterReservedUs < 0 ||
                        !NearlyEqual(actual, m_expectedMeterReservedUs),
                    "Distributional-shadow tracked and meter reservations disagree: tracked="
                        << m_expectedMeterReservedUs << " actual=" << actual);
    return actual;
}

void
DistributionalShadowT2Controller::NotifyMeasuredAirtime(uint64_t frameId,
                                                        double allocatedUs,
                                                        double ppduDurationUs)
{
    auto launched = m_launchedFrames.find(frameId);
    NS_ABORT_MSG_IF(launched == m_launchedFrames.end() || launched->second.settled,
                    "Measured airtime references unlaunched or settled shadow frame");
    NS_ABORT_MSG_IF(!std::isfinite(allocatedUs) || !(allocatedUs > 0) ||
                        !std::isfinite(ppduDurationUs) || !(ppduDurationUs > 0) ||
                        allocatedUs > ppduDurationUs + ACCOUNTING_TOLERANCE_US,
                    "Distributional-shadow measured-airtime callback is invalid");
    auto& state = launched->second;
    const double reduction = std::min(state.remainingReservedUs, allocatedUs);
    state.remainingReservedUs -= reduction;
    state.measuredAirtimeUs += allocatedUs;
    m_expectedMeterReservedUs -= reduction;
    if (std::abs(m_expectedMeterReservedUs) <= ACCOUNTING_TOLERANCE_US)
    {
        m_expectedMeterReservedUs = 0;
    }
    NS_ABORT_MSG_IF(!std::isfinite(state.remainingReservedUs) ||
                        state.remainingReservedUs < 0 ||
                        !std::isfinite(state.measuredAirtimeUs) ||
                        !std::isfinite(m_expectedMeterReservedUs) ||
                        m_expectedMeterReservedUs < 0,
                    "Distributional-shadow measured-airtime state is invalid");
    GetReconciledMeterReservedUs();

    const uint64_t nowNs = CurrentTimeNs();
    NS_ABORT_MSG_IF(nowNs < MEASUREMENT_START_NS || nowNs >= MEASUREMENT_STOP_NS,
                    "Distributional-shadow measured callback is outside measurement");
}

void
DistributionalShadowT2Controller::NotifySettlement(uint64_t frameId,
                                                   double releasedUs,
                                                   double measuredUs,
                                                   double nominalUs,
                                                   bool fallback)
{
    (void)fallback;
    auto launched = m_launchedFrames.find(frameId);
    NS_ABORT_MSG_IF(launched == m_launchedFrames.end() || launched->second.settled,
                    "Settlement references unlaunched or settled shadow frame");
    auto& state = launched->second;
    NS_ABORT_MSG_IF(!std::isfinite(releasedUs) || releasedUs < 0 ||
                        !std::isfinite(measuredUs) || measuredUs < 0 ||
                        !std::isfinite(nominalUs) || !(nominalUs > 0) ||
                        !NearlyEqual(releasedUs, state.remainingReservedUs) ||
                        !NearlyEqual(measuredUs, state.measuredAirtimeUs) ||
                        !NearlyEqual(nominalUs, state.nominalAirtimeUs),
                    "Distributional-shadow settlement evidence does not reconcile");
    m_expectedMeterReservedUs -= state.remainingReservedUs;
    state.remainingReservedUs = 0;
    state.settled = true;
    if (std::abs(m_expectedMeterReservedUs) <= ACCOUNTING_TOLERANCE_US)
    {
        m_expectedMeterReservedUs = 0;
    }
    NS_ABORT_MSG_IF(m_expectedMeterReservedUs < 0 ||
                        !std::isfinite(m_expectedMeterReservedUs),
                    "Distributional-shadow settlement made reservations invalid");
    const bool inserted = m_settledFrameIds.insert(frameId).second;
    NS_ABORT_MSG_IF(!inserted,
                    "Distributional-shadow frame settled more than once");
    ++m_secondarySettled;
    GetReconciledMeterReservedUs();
}

std::string_view
DistributionalShadowT2Controller::StatusName(DecisionStatus status)
{
    constexpr std::array<std::string_view, 10> names{
        "outside_decision_window",
        "history_warmup",
        "not_actionable",
        "frame_type_restricted",
        "descriptor_unavailable",
        "nonpositive_reward",
        "opportunity_price_rejected",
        "horizon_credit_rejected",
        "launch_rejected",
        "action",
    };
    return names.at(StatusIndex(status));
}

std::size_t
DistributionalShadowT2Controller::StatusIndex(DecisionStatus status)
{
    const auto index = static_cast<std::size_t>(status);
    NS_ABORT_MSG_IF(index >= 10,
                    "Unknown distributional-shadow decision status");
    return index;
}

void
DistributionalShadowT2Controller::WriteDecisionHeader()
{
    m_decisions
        << "schema_version,run_id,frame_id,policy,decision_status,primary_path_id,"
           "primary_copy_id,secondary_path_id,secondary_copy_id,sample_stage,"
           "sample_offset_us,generation_time_ns,deadline_time_ns,primary_sample_time_ns,"
           "secondary_sample_time_ns,primary_feature_watermark_time_ns,"
           "primary_feature_watermark_sequence,secondary_feature_watermark_time_ns,"
           "secondary_feature_watermark_sequence,frame_type,frame_size_bytes,"
           "frame_packet_count,primary_actionable,decision_window_start_ns,"
           "decision_window_stop_ns,inside_decision_window,history_ready,"
           "primary_current_poll_capture_time_ns,primary_current_poll_available_time_ns,"
           "secondary_current_poll_capture_time_ns,secondary_current_poll_available_time_ns,"
           "primary_lag1_frame_id,primary_lag1_poll_capture_time_ns,"
           "primary_lag3_frame_id,primary_lag3_poll_capture_time_ns,"
           "primary_lag8_frame_id,primary_lag8_poll_capture_time_ns,"
           "secondary_lag1_frame_id,secondary_lag1_poll_capture_time_ns,"
           "secondary_lag3_frame_id,secondary_lag3_poll_capture_time_ns,"
           "secondary_lag8_frame_id,secondary_lag8_poll_capture_time_ns,"
           "congestion_updated,current_primary_busy20ms,running_primary_busy20ms,"
           "congestion_observation_count,time_bin,congestion_regime,"
           "feature_evaluated,model_spec_id,selected_variant,feature_family,"
           "feature_count,feature_adapter_id,runtime_contract_id,"
           "runtime_contract_sha256,control_logits,control_probabilities,control_cdf,"
           "full_copy_logits,full_copy_probabilities,full_copy_cdf,"
           "deadline_rescue_reward,tail18_cdf_gain,reward_density_per_us,"
           "opportunity_cost_per_us,passes_opportunity_price,"
           "earlier_unsettled_launches,secondary_state_action_dirty,descriptor_checked,"
           "descriptor_available,descriptor_frame_packet_count,descriptor_packet_count,"
           "descriptor_packet_indices,descriptor_expected_mac_service_bytes,"
           "descriptor_deadline_time_ns,canonical_cost_estimator_id,cost_safety_factor,"
           "canonical_nominal_airtime_us,canonical_reserved_airtime_us,"
           "credit_accounting_id,budget_fraction,positive_balance_capacity_us,"
           "initial_credit_us,repayment_stop_ns,ledger_balance_before_us,"
           "ledger_debt_before_us,ledger_remaining_refill_before_us,"
           "ledger_repayable_before_us,ledger_debited_before_us,"
           "horizon_admission_considered,horizon_admitted,launch_attempted,"
           "secondary_launched,ledger_balance_after_us,ledger_debt_after_us,"
           "ledger_debited_after_us,meter_reserved_before_us,meter_reserved_after_us,"
           "measured_settlement_refunds_ledger\n";
    m_decisions.flush();
}

void
DistributionalShadowT2Controller::WriteDecision(const DecisionEvidence& evidence)
{
    NS_ABORT_MSG_IF(!evidence.primary || !evidence.secondary,
                    "Distributional-shadow decision lacks endpoint pair");
    const auto& primary = *evidence.primary;
    const auto& secondary = *evidence.secondary;
    const auto& primaryLags = evidence.history.primary.lags;
    const auto& secondaryLags = evidence.history.secondaryLags;
    NS_ABORT_MSG_IF(primaryLags.size() != 3 || secondaryLags.size() != 3 ||
                        primaryLags[0].lagFrames != 1 ||
                        primaryLags[1].lagFrames != 3 ||
                        primaryLags[2].lagFrames != 8 ||
                        secondaryLags[0].lagFrames != 1 ||
                        secondaryLags[1].lagFrames != 3 ||
                        secondaryLags[2].lagFrames != 8,
                    "Distributional-shadow exact-lag evidence differs");
    NS_ABORT_MSG_IF(evidence.featureEvaluated != evidence.model.has_value() ||
                        (evidence.featureEvaluated &&
                         (!evidence.timeBin || !evidence.congestionRegime)) ||
                        (evidence.descriptor && !evidence.descriptorChecked),
                    "Distributional-shadow decision evidence is inconsistent");
    const auto& provenance = TemporalT2DistributionModelEvaluator::GetProvenance();

    m_decisions << CSV_SCHEMA_VERSION << ',' << m_runId << ','
                << primary.key.frameId << ',' << POLICY_NAME << ','
                << StatusName(evidence.status) << ',' << +PRIMARY_PATH_ID << ','
                << +PRIMARY_COPY_ID << ',' << +SECONDARY_PATH_ID << ','
                << +SECONDARY_COPY_ID << ",T2," << T2_OFFSET_US << ','
                << primary.generationTimeNs << ',' << primary.deadlineTimeNs << ','
                << primary.sampleTimeNs << ',' << secondary.sampleTimeNs << ',';
    WriteOptionalUint64(m_decisions, primary.latestFeatureEventTimeNs);
    m_decisions << ',' << primary.latestFeatureEventSequence << ',';
    WriteOptionalUint64(m_decisions, secondary.latestFeatureEventTimeNs);
    m_decisions << ',' << secondary.latestFeatureEventSequence << ','
                << FrameTypeToString(primary.frameType) << ','
                << primary.frameSizeBytes << ',' << primary.framePacketCount << ','
                << primary.actionable << ',' << DECISION_START_NS << ','
                << DECISION_STOP_NS << ',' << evidence.insideDecisionWindow << ','
                << evidence.history.ready << ','
                << evidence.history.primary.currentPollCaptureTimeNs << ','
                << evidence.history.primary.currentPollAvailableTimeNs << ','
                << evidence.history.currentSecondaryPollCaptureTimeNs << ','
                << evidence.history.currentSecondaryPollAvailableTimeNs << ',';
    for (const auto& lag : primaryLags)
    {
        WriteOptionalUint64(m_decisions, lag.frameId);
        m_decisions << ',';
        WriteOptionalUint64(m_decisions, lag.pollCaptureTimeNs);
        m_decisions << ',';
    }
    for (const auto& lag : secondaryLags)
    {
        WriteOptionalUint64(m_decisions, lag.frameId);
        m_decisions << ',';
        WriteOptionalUint64(m_decisions, lag.pollCaptureTimeNs);
        m_decisions << ',';
    }
    m_decisions << evidence.congestionUpdated << ',';
    if (evidence.congestionUpdated)
    {
        m_decisions << evidence.currentPrimaryBusy20ms << ','
                    << evidence.runningPrimaryBusy20ms << ','
                    << evidence.congestionObservationCount << ',';
        WriteOptionalUint8(m_decisions, evidence.timeBin);
        m_decisions << ',';
        WriteOptionalUint8(m_decisions, evidence.congestionRegime);
    }
    else
    {
        m_decisions << ",,,,";
    }
    m_decisions << ',' << evidence.featureEvaluated << ','
                << TemporalT2DistributionModelEvaluator::GetModelSpecId() << ','
                << provenance.selectedVariant << ','
                << TemporalT2DistributionModelEvaluator::GetFeatureFamily() << ','
                << TemporalT2DistributionPredictor::FEATURE_COUNT << ',';
    WriteCsvString(m_decisions,
                   TemporalT2DistributionModelEvaluator::GetFeatureAdapter());
    m_decisions << ',' << RUNTIME_CONTRACT_ID << ','
                << provenance.runtimeContractSha256 << ',';
    if (evidence.model)
    {
        WriteDoubleArray(m_decisions, evidence.model->controlLogits);
        m_decisions << ',';
        WriteDoubleArray(m_decisions, evidence.model->controlProbabilities);
        m_decisions << ',';
        WriteDoubleArray(m_decisions, evidence.model->controlCdf);
        m_decisions << ',';
        WriteDoubleArray(m_decisions, evidence.model->fullCopyLogits);
        m_decisions << ',';
        WriteDoubleArray(m_decisions, evidence.model->fullCopyProbabilities);
        m_decisions << ',';
        WriteDoubleArray(m_decisions, evidence.model->fullCopyCdf);
        m_decisions << ',' << evidence.model->deadlineRescueReward << ','
                    << evidence.model->tail18CdfGain << ','
                    << evidence.rewardDensityPerUs << ',';
        if (evidence.model->deadlineRescueReward > 0)
        {
            m_decisions << evidence.opportunityCostPerUs;
        }
        m_decisions << ',' << evidence.passesOpportunityPrice;
    }
    else
    {
        m_decisions << ",,,,,,,,,,";
    }
    m_decisions << ',' << evidence.earlierUnsettledLaunches << ','
                << evidence.secondaryStateActionDirty << ','
                << evidence.descriptorChecked << ','
                << evidence.descriptor.has_value() << ',';
    if (evidence.descriptor)
    {
        m_decisions << evidence.descriptor->framePacketCount << ','
                    << evidence.descriptor->packetCount << ',';
        WritePacketIndices(m_decisions, *evidence.descriptor);
        m_decisions << ',' << evidence.descriptor->expectedMacServiceBytes << ','
                    << evidence.descriptor->deadlineTimeNs;
    }
    else
    {
        m_decisions << ",,,,";
    }
    m_decisions << ',' << COST_ESTIMATOR_ID << ',' << COST_SAFETY_FACTOR << ',';
    if (evidence.descriptor)
    {
        m_decisions << evidence.canonicalNominalAirtimeUs << ','
                    << evidence.canonicalReservedAirtimeUs;
    }
    else
    {
        m_decisions << ',';
    }
    m_decisions << ',' << CREDIT_ACCOUNTING_ID << ',' << BUDGET_FRACTION << ','
                << POSITIVE_BALANCE_CAPACITY_US << ',' << INITIAL_CREDIT_US << ','
                << MEASUREMENT_STOP_NS << ',' << evidence.ledgerBalanceBeforeUs << ','
                << evidence.ledgerDebtBeforeUs << ','
                << evidence.ledgerRemainingRefillBeforeUs << ','
                << evidence.ledgerRepayableBeforeUs << ','
                << evidence.ledgerDebitedBeforeUs << ','
                << evidence.horizonAdmissionConsidered << ','
                << evidence.horizonAdmitted << ',' << evidence.launchAttempted << ','
                << evidence.secondaryLaunched << ',' << evidence.ledgerBalanceAfterUs << ','
                << evidence.ledgerDebtAfterUs << ',' << evidence.ledgerDebitedAfterUs << ','
                << evidence.meterReservedBeforeUs << ','
                << evidence.meterReservedAfterUs << ",0\n";
    m_decisions.flush();
    NS_ABORT_MSG_IF(!m_decisions,
                    "Distributional-shadow decision row write failed");
}

void
DistributionalShadowT2Controller::WriteSummary(
    uint64_t generatedFrames,
    const std::set<uint64_t>& duplicatedFrameIds)
{
    NS_ABORT_MSG_IF(!m_started,
                    "Distributional-shadow summary requires completed control");
    NS_ABORT_MSG_IF(m_summaryWritten,
                    "Distributional-shadow summary was written twice");
    NS_ABORT_MSG_IF(m_pendingPrimary,
                    "Distributional-shadow summary has unmatched primary endpoint");
    NS_ABORT_MSG_IF(m_decidedFrameIds.size() != m_pairedFrames ||
                        generatedFrames != m_pairedFrames,
                    "Distributional-shadow generated and paired frames differ");

    const uint64_t statusSum =
        std::accumulate(m_statusCounts.begin(), m_statusCounts.end(), uint64_t{0});
    const uint64_t featureStatusSum =
        m_statusCounts[StatusIndex(DecisionStatus::NONPOSITIVE_REWARD)] +
        m_statusCounts[StatusIndex(DecisionStatus::OPPORTUNITY_PRICE_REJECTED)] +
        m_statusCounts[StatusIndex(DecisionStatus::HORIZON_CREDIT_REJECTED)] +
        m_statusCounts[StatusIndex(DecisionStatus::LAUNCH_REJECTED)] +
        m_statusCounts[StatusIndex(DecisionStatus::ACTION)];
    const uint64_t positiveStatusSum =
        m_statusCounts[StatusIndex(DecisionStatus::OPPORTUNITY_PRICE_REJECTED)] +
        m_statusCounts[StatusIndex(DecisionStatus::HORIZON_CREDIT_REJECTED)] +
        m_statusCounts[StatusIndex(DecisionStatus::LAUNCH_REJECTED)] +
        m_statusCounts[StatusIndex(DecisionStatus::ACTION)];
    const uint64_t opportunityPassStatusSum =
        m_statusCounts[StatusIndex(DecisionStatus::HORIZON_CREDIT_REJECTED)] +
        m_statusCounts[StatusIndex(DecisionStatus::LAUNCH_REJECTED)] +
        m_statusCounts[StatusIndex(DecisionStatus::ACTION)];
    const uint64_t admittedStatusSum =
        m_statusCounts[StatusIndex(DecisionStatus::LAUNCH_REJECTED)] +
        m_statusCounts[StatusIndex(DecisionStatus::ACTION)];
    const uint64_t congestionStatusSum =
        m_statusCounts[StatusIndex(DecisionStatus::FRAME_TYPE_RESTRICTED)] +
        m_statusCounts[StatusIndex(DecisionStatus::DESCRIPTOR_UNAVAILABLE)] +
        featureStatusSum;
    const uint64_t actionBinSum =
        std::accumulate(m_actionsByTimeBin.begin(),
                        m_actionsByTimeBin.end(),
                        uint64_t{0});
    const uint64_t actionRegimeSum =
        std::accumulate(m_actionsByRegime.begin(),
                        m_actionsByRegime.end(),
                        uint64_t{0});
    const double reservationBinSum =
        std::accumulate(m_reservationUsByTimeBin.begin(),
                        m_reservationUsByTimeBin.end(),
                        0.0);
    const double reservationRegimeSum =
        std::accumulate(m_reservationUsByRegime.begin(),
                        m_reservationUsByRegime.end(),
                        0.0);
    NS_ABORT_MSG_IF(statusSum != m_pairedFrames,
                    "Distributional-shadow status count differs from paired frames: status="
                        << statusSum << " paired=" << m_pairedFrames);
    NS_ABORT_MSG_IF(featureStatusSum != m_featureEvaluated,
                    "Distributional-shadow feature status count differs: status="
                        << featureStatusSum << " evaluated=" << m_featureEvaluated);
    NS_ABORT_MSG_IF(positiveStatusSum != m_positiveRewardCount,
                    "Distributional-shadow positive status count differs: status="
                        << positiveStatusSum << " positive=" << m_positiveRewardCount);
    NS_ABORT_MSG_IF(opportunityPassStatusSum != m_opportunityPassed,
                    "Distributional-shadow opportunity-pass status count differs: status="
                        << opportunityPassStatusSum << " passed=" << m_opportunityPassed);
    NS_ABORT_MSG_IF(opportunityPassStatusSum != m_horizonAdmissionConsidered,
                    "Distributional-shadow horizon-considered status count differs: status="
                        << opportunityPassStatusSum
                        << " considered=" << m_horizonAdmissionConsidered);
    NS_ABORT_MSG_IF(admittedStatusSum != m_horizonAdmitted,
                    "Distributional-shadow admitted status count differs: status="
                        << admittedStatusSum << " admitted=" << m_horizonAdmitted);
    NS_ABORT_MSG_IF(admittedStatusSum != m_launchAttempted,
                    "Distributional-shadow launch-attempt status count differs: status="
                        << admittedStatusSum << " attempted=" << m_launchAttempted);
    NS_ABORT_MSG_IF(
        m_launchedFrameIds.size() !=
            m_statusCounts[StatusIndex(DecisionStatus::ACTION)],
        "Distributional-shadow action status count differs from launched IDs: status="
            << m_statusCounts[StatusIndex(DecisionStatus::ACTION)]
            << " launched=" << m_launchedFrameIds.size());
    NS_ABORT_MSG_IF(m_launchedFrames.size() != m_launchedFrameIds.size(),
                    "Distributional-shadow launch state count differs from launched IDs: states="
                        << m_launchedFrames.size()
                        << " launched=" << m_launchedFrameIds.size());
    NS_ABORT_MSG_IF(m_settledFrameIds.size() != m_secondarySettled,
                    "Distributional-shadow settled ID count differs: ids="
                        << m_settledFrameIds.size()
                        << " settled=" << m_secondarySettled);
    NS_ABORT_MSG_IF(m_launchedFrameIds != m_settledFrameIds,
                    "Distributional-shadow launched and settled frame IDs differ");
    NS_ABORT_MSG_IF(m_launchedFrameIds != duplicatedFrameIds,
                    "Distributional-shadow launched and output duplicate frame IDs differ");
    NS_ABORT_MSG_IF(congestionStatusSum != m_congestionObservationCount,
                    "Distributional-shadow congestion status count differs: status="
                        << congestionStatusSum
                        << " observations=" << m_congestionObservationCount);
    NS_ABORT_MSG_IF(m_actionDirtyScored > m_featureEvaluated,
                    "Distributional-shadow action-dirty count exceeds evaluations: dirty="
                        << m_actionDirtyScored << " evaluated=" << m_featureEvaluated);
    NS_ABORT_MSG_IF(m_positiveRewards.size() != m_positiveRewardCount,
                    "Distributional-shadow stored positive rewards differ: stored="
                        << m_positiveRewards.size()
                        << " positive=" << m_positiveRewardCount);
    NS_ABORT_MSG_IF(
        m_finiteOpportunityCosts.size() + m_infiniteOpportunityCosts !=
            m_positiveRewardCount,
        "Distributional-shadow opportunity-cost count differs: finite="
            << m_finiteOpportunityCosts.size()
            << " infinite=" << m_infiniteOpportunityCosts
            << " positive=" << m_positiveRewardCount);
    NS_ABORT_MSG_IF(actionBinSum != m_launchedFrameIds.size(),
                    "Distributional-shadow time-bin action count differs: bins="
                        << actionBinSum << " launched=" << m_launchedFrameIds.size());
    NS_ABORT_MSG_IF(actionRegimeSum != m_launchedFrameIds.size(),
                    "Distributional-shadow regime action count differs: regimes="
                        << actionRegimeSum << " launched=" << m_launchedFrameIds.size());
    NS_ABORT_MSG_IF(!AccumulatedSumsNearlyEqual(
                        reservationBinSum,
                        m_canonicalReservedLaunchedSumUs,
                        m_launchedFrameIds.size(),
                        m_reservationUsByTimeBin.size()),
                    "Distributional-shadow time-bin reservation differs: bins="
                        << reservationBinSum
                        << " launched=" << m_canonicalReservedLaunchedSumUs);
    NS_ABORT_MSG_IF(!AccumulatedSumsNearlyEqual(
                        reservationRegimeSum,
                        m_canonicalReservedLaunchedSumUs,
                        m_launchedFrameIds.size(),
                        m_reservationUsByRegime.size()),
                    "Distributional-shadow regime reservation differs: regimes="
                        << reservationRegimeSum
                        << " launched=" << m_canonicalReservedLaunchedSumUs);

    for (const auto& [frameId, launched] : m_launchedFrames)
    {
        NS_ABORT_MSG_IF(!launched.settled || launched.remainingReservedUs != 0 ||
                            !m_settledFrameIds.contains(frameId),
                        "Distributional-shadow final launch state is unsettled");
    }
    const double meterReservedRawUs = GetReconciledMeterReservedUs();
    NS_ABORT_MSG_IF(std::abs(meterReservedRawUs) > ACCOUNTING_TOLERANCE_US,
                    "Distributional-shadow final meter reservation is nonzero");
    NS_ABORT_MSG_IF(
        !AccumulatedSumsNearlyEqual(m_canonicalReservedLaunchedSumUs,
                                    m_meter->GetEstimatedActionAirtimeUs(),
                                    m_launchedFrameIds.size(),
                                    0) ||
            !AccumulatedSumsNearlyEqual(m_canonicalReservedLaunchedSumUs,
                                        m_ledger.GetPermanentDebitedUs(),
                                        m_launchedFrameIds.size(),
                                        0) ||
            m_ledger.GetDebitCount() != m_launchedFrameIds.size(),
        "Distributional-shadow ledger and meter launch costs differ");

    const double balanceBeforeFinalizeUs = m_ledger.GetBalanceUs();
    const double debtBeforeFinalizeUs = m_ledger.GetDebtUs();
    const double minimumBalanceUs = m_ledger.GetMinimumBalanceUs();
    const double peakDebtUs = m_ledger.GetPeakDebtUs();
    const double generatedRefillBeforeFinalizeUs =
        m_ledger.GetGeneratedRefillUs();
    const double discardedRefillBeforeFinalizeUs =
        m_ledger.GetDiscardedRefillUs();
    const auto remainingBeforeFinalizeUs = m_ledger.GetRemainingRefillUs();
    const auto repayableBeforeFinalizeUs = m_ledger.GetRepayableCreditUs();
    NS_ABORT_MSG_IF(!remainingBeforeFinalizeUs || !repayableBeforeFinalizeUs,
                    "Distributional-shadow final operational snapshot is absent");
    NS_ABORT_MSG_IF(!m_ledger.Finalize() || !m_ledger.IsFinalized(),
                    "Distributional-shadow repayment did not close");
    const double finalBalanceUs = m_ledger.GetBalanceUs();
    const double finalGeneratedRefillUs = m_ledger.GetGeneratedRefillUs();
    const double finalDiscardedRefillUs = m_ledger.GetDiscardedRefillUs();
    NS_ABORT_MSG_IF(finalBalanceUs < 0 || m_ledger.GetDebtUs() != 0 ||
                        !std::isfinite(finalBalanceUs),
                    "Distributional-shadow final ledger balance is invalid");

    const double measuredSecondaryAirtimeUs =
        m_meter->GetMeasuredAirtimeTotalUs();
    const std::array<double, 8> finalValues{
        m_canonicalNominalLaunchedSumUs,
        m_canonicalReservedLaunchedSumUs,
        m_predictedRewardLaunchedSum,
        measuredSecondaryAirtimeUs,
        balanceBeforeFinalizeUs,
        finalBalanceUs,
        finalGeneratedRefillUs,
        finalDiscardedRefillUs,
    };
    NS_ABORT_MSG_IF(std::any_of(finalValues.begin(), finalValues.end(), [](double value) {
                        return !std::isfinite(value);
                    }),
                    "Distributional-shadow final numeric evidence is invalid");

    const auto& provenance = TemporalT2DistributionModelEvaluator::GetProvenance();
    std::ofstream summary(m_summaryFile, std::ios::out | std::ios::trunc);
    NS_ABORT_MSG_IF(!summary,
                    "Cannot open distributional-shadow summary " << m_summaryFile);
    summary.imbue(std::locale::classic());
    summary << std::setprecision(std::numeric_limits<double>::max_digits10);
    summary
        << "{\n"
        << "  \"schema_version\": " << SUMMARY_SCHEMA_VERSION << ",\n"
        << "  \"run_id\": \"" << JsonEscape(m_runId) << "\",\n"
        << "  \"policy\": \"" << POLICY_NAME << "\",\n"
        << "  \"runtime_contract_id\": \"" << RUNTIME_CONTRACT_ID << "\",\n"
        << "  \"runtime_contract_sha256\": \""
        << provenance.runtimeContractSha256 << "\",\n"
        << "  \"evidence_status\": \"" << provenance.evidenceStatus << "\",\n"
        << "  \"source_artifacts\": {\n"
        << "    \"training_git_commit\": \"" << provenance.trainingGitCommit
        << "\",\n"
        << "    \"source_model_pickle_sha256\": \""
        << provenance.sourceModelPickleSha256 << "\",\n"
        << "    \"source_model_json_sha256\": \""
        << provenance.sourceModelJsonSha256 << "\",\n"
        << "    \"source_reference_json_sha256\": \""
        << provenance.sourceReferenceJsonSha256 << "\",\n"
        << "    \"source_metrics_sha256\": \""
        << provenance.sourceMetricsSha256 << "\",\n"
        << "    \"source_manifest_sha256\": \""
        << provenance.sourceManifestSha256 << "\",\n"
        << "    \"exporter_sha256\": \"" << provenance.exporterSha256
        << "\",\n"
        << "    \"portable_model_sha256\": \""
        << provenance.portableModelSha256 << "\",\n"
        << "    \"deployment_reference_sha256\": \""
        << provenance.deploymentReferenceSha256 << "\",\n"
        << "    \"feature_contract_sha256\": \""
        << provenance.featureContractSha256 << "\"\n"
        << "  },\n"
        << "  \"model\": {\n"
        << "    \"selected_variant\": \"" << provenance.selectedVariant << "\",\n"
        << "    \"model_spec_id\": \""
        << TemporalT2DistributionModelEvaluator::GetModelSpecId() << "\",\n"
        << "    \"feature_family\": \""
        << TemporalT2DistributionModelEvaluator::GetFeatureFamily() << "\",\n"
        << "    \"feature_count\": "
        << TemporalT2DistributionPredictor::FEATURE_COUNT << ",\n"
        << "    \"feature_adapter_id\": \""
        << TemporalT2DistributionModelEvaluator::GetFeatureAdapter() << "\",\n"
        << "    \"objective\": \""
        << TemporalT2DistributionModelEvaluator::GetObjective() << "\",\n"
        << "    \"frame_gate\": \""
        << TemporalT2DistributionModelEvaluator::GetFrameGate() << "\"\n"
        << "  },\n"
        << "  \"telemetry\": {\n"
        << "    \"telemetry_schema_version\": "
        << PREDICTION_TELEMETRY_SCHEMA_VERSION << ",\n"
        << "    \"polling_schema_version\": "
        << PREDICTION_POLLING_SCHEMA_VERSION << ",\n"
        << "    \"feature_support_mask_version\": "
        << FEATURE_SUPPORT_MASK_VERSION << ",\n"
        << "    \"required_support_mask_hex\": \"" << REQUIRED_SUPPORT_MASK
        << "\",\n"
        << "    \"sample_offsets_us\": [0, 2000],\n"
        << "    \"history_windows_us\": [1000, 5000, 20000],\n"
        << "    \"polling_interval_us\": 1000,\n"
        << "    \"polling_report_delay_us\": 1000,\n"
        << "    \"raw_prediction_event_log_enabled\": false,\n"
        << "    \"oracle_features_enabled\": false\n"
        << "  },\n"
        << "  \"decision_window\": {\n"
        << "    \"measurement_start_ns\": " << MEASUREMENT_START_NS << ",\n"
        << "    \"measurement_stop_ns\": " << MEASUREMENT_STOP_NS << ",\n"
        << "    \"decision_start_ns\": " << DECISION_START_NS << ",\n"
        << "    \"decision_stop_ns\": " << DECISION_STOP_NS << ",\n"
        << "    \"interval_semantics\": \"half_open\"\n"
        << "  },\n"
        << "  \"allocator\": {\n"
        << "    \"shadow_reference_id\": \"" << SHADOW_REFERENCE_ID << "\",\n"
        << "    \"congestion_signal_id\": \"" << CONGESTION_SIGNAL_ID
        << "\",\n"
        << "    \"time_bin_width_us\": "
        << TemporalT2DistributionModelEvaluator::GetTimeBinWidthUs() << ",\n"
        << "    \"time_bin_count\": "
        << +TemporalT2DistributionModelEvaluator::TIME_BIN_COUNT << ",\n"
        << "    \"congestion_regime_count\": "
        << +TemporalT2DistributionModelEvaluator::REGIME_COUNT << ",\n"
        << "    \"canonical_estimator_id\": \"" << COST_ESTIMATOR_ID << "\",\n"
        << "    \"canonical_p_frame_reservation_us\": "
        << TemporalT2DistributionModelEvaluator::GetCanonicalPFrameReservationUs()
        << ",\n"
        << "    \"cost_safety_factor\": " << COST_SAFETY_FACTOR << ",\n"
        << "    \"credit_accounting_id\": \"" << CREDIT_ACCOUNTING_ID << "\",\n"
        << "    \"refill_fraction\": " << BUDGET_FRACTION << ",\n"
        << "    \"positive_balance_capacity_us\": "
        << POSITIVE_BALANCE_CAPACITY_US << ",\n"
        << "    \"initial_credit_us\": " << INITIAL_CREDIT_US << ",\n"
        << "    \"maximum_generated_credit_us\": "
        << TemporalT2DistributionModelEvaluator::GetMaximumRepayableCreditUs()
        << ",\n"
        << "    \"negative_balance_allowed_when_repayable\": true,\n"
        << "    \"accepted_reservation_is_permanent\": true,\n"
        << "    \"measured_settlement_refunds_ledger\": false\n"
        << "  },\n"
        << "  \"counts\": {\n"
        << "    \"generated_frames\": " << generatedFrames << ",\n"
        << "    \"paired_t2_frames\": " << m_pairedFrames << ",\n"
        << "    \"outside_decision_window\": "
        << m_statusCounts[StatusIndex(DecisionStatus::OUTSIDE_DECISION_WINDOW)]
        << ",\n"
        << "    \"history_warmup\": "
        << m_statusCounts[StatusIndex(DecisionStatus::HISTORY_WARMUP)] << ",\n"
        << "    \"not_actionable\": "
        << m_statusCounts[StatusIndex(DecisionStatus::NOT_ACTIONABLE)] << ",\n"
        << "    \"frame_type_restricted\": "
        << m_statusCounts[StatusIndex(DecisionStatus::FRAME_TYPE_RESTRICTED)]
        << ",\n"
        << "    \"descriptor_unavailable\": "
        << m_statusCounts[StatusIndex(DecisionStatus::DESCRIPTOR_UNAVAILABLE)]
        << ",\n"
        << "    \"feature_evaluated\": " << m_featureEvaluated << ",\n"
        << "    \"nonpositive_reward\": "
        << m_statusCounts[StatusIndex(DecisionStatus::NONPOSITIVE_REWARD)] << ",\n"
        << "    \"positive_reward\": " << m_positiveRewardCount << ",\n"
        << "    \"opportunity_price_rejected\": "
        << m_statusCounts[StatusIndex(DecisionStatus::OPPORTUNITY_PRICE_REJECTED)]
        << ",\n"
        << "    \"opportunity_price_passed\": " << m_opportunityPassed << ",\n"
        << "    \"horizon_credit_rejected\": "
        << m_statusCounts[StatusIndex(DecisionStatus::HORIZON_CREDIT_REJECTED)]
        << ",\n"
        << "    \"horizon_admission_considered\": "
        << m_horizonAdmissionConsidered << ",\n"
        << "    \"horizon_admitted\": " << m_horizonAdmitted << ",\n"
        << "    \"launch_attempted\": " << m_launchAttempted << ",\n"
        << "    \"launch_rejected\": "
        << m_statusCounts[StatusIndex(DecisionStatus::LAUNCH_REJECTED)] << ",\n"
        << "    \"secondary_launched\": " << m_launchedFrameIds.size() << ",\n"
        << "    \"secondary_settled\": " << m_secondarySettled << ",\n"
        << "    \"congestion_observations\": "
        << m_congestionObservationCount << ",\n"
        << "    \"action_dirty_scored_decisions\": " << m_actionDirtyScored
        << "\n"
        << "  },\n"
        << "  \"allocation_by_time_bin\": {\n"
        << "    \"actions\": ";
    WriteJsonArray(summary, m_actionsByTimeBin);
    summary << ",\n    \"canonical_reservation_us\": ";
    WriteJsonArray(summary, m_reservationUsByTimeBin);
    summary
        << "\n  },\n"
        << "  \"allocation_by_congestion_regime\": {\n"
        << "    \"actions\": ";
    WriteJsonArray(summary, m_actionsByRegime);
    summary << ",\n    \"canonical_reservation_us\": ";
    WriteJsonArray(summary, m_reservationUsByRegime);
    summary
        << "\n  },\n"
        << "  \"prediction_diagnostics\": {\n"
        << "    \"positive_deadline_rescue_reward\": ";
    WriteDistribution(summary, m_positiveRewards);
    summary << ",\n    \"finite_opportunity_cost_per_us\": ";
    WriteDistribution(summary, m_finiteOpportunityCosts);
    summary
        << ",\n"
        << "    \"infinite_opportunity_cost_count\": "
        << m_infiniteOpportunityCosts << ",\n"
        << "    \"predicted_deadline_rescue_sum_launched\": "
        << m_predictedRewardLaunchedSum << ",\n"
        << "    \"predicted_tail18_cdf_gain_sum_launched\": "
        << m_tail18GainLaunchedSum << "\n"
        << "  },\n"
        << "  \"ledger\": {\n"
        << "    \"balance_after_last_decision_us\": "
        << balanceBeforeFinalizeUs << ",\n"
        << "    \"debt_after_last_decision_us\": " << debtBeforeFinalizeUs
        << ",\n"
        << "    \"remaining_refill_after_last_decision_us\": "
        << *remainingBeforeFinalizeUs << ",\n"
        << "    \"repayable_after_last_decision_us\": "
        << *repayableBeforeFinalizeUs << ",\n"
        << "    \"minimum_balance_us\": " << minimumBalanceUs << ",\n"
        << "    \"maximum_debt_us\": " << peakDebtUs << ",\n"
        << "    \"permanent_debited_us\": "
        << m_ledger.GetPermanentDebitedUs() << ",\n"
        << "    \"permanent_debit_count\": " << m_ledger.GetDebitCount()
        << ",\n"
        << "    \"generated_refill_before_finalize_us\": "
        << generatedRefillBeforeFinalizeUs << ",\n"
        << "    \"discarded_refill_before_finalize_us\": "
        << discardedRefillBeforeFinalizeUs << ",\n"
        << "    \"generated_refill_at_stop_us\": " << finalGeneratedRefillUs
        << ",\n"
        << "    \"discarded_refill_at_stop_us\": " << finalDiscardedRefillUs
        << ",\n"
        << "    \"final_balance_us\": " << finalBalanceUs << ",\n"
        << "    \"repayment_closed\": true\n"
        << "  },\n"
        << "  \"airtime\": {\n"
        << "    \"canonical_nominal_launched_sum_us\": "
        << m_canonicalNominalLaunchedSumUs << ",\n"
        << "    \"canonical_reserved_launched_sum_us\": "
        << m_canonicalReservedLaunchedSumUs << ",\n"
        << "    \"measured_secondary_airtime_us\": "
        << measuredSecondaryAirtimeUs << ",\n"
        << "    \"meter_estimated_action_airtime_us\": "
        << m_meter->GetEstimatedActionAirtimeUs() << "\n"
        << "  },\n"
        << "  \"integrity\": {\n"
        << "    \"pending_pair_empty\": true,\n"
        << "    \"generated_equals_paired\": true,\n"
        << "    \"status_counts_reconcile\": true,\n"
        << "    \"launches_equal_settlements\": true,\n"
        << "    \"launched_frame_ids_equal_duplicated_frame_ids\": true,\n"
        << "    \"ledger_debits_equal_actions\": true,\n"
        << "    \"ledger_debits_equal_canonical_reservation_sum\": true,\n"
        << "    \"ledger_finalized_at_repayment_stop\": true,\n"
        << "    \"meter_reserved_final_within_tolerance\": true,\n"
        << "    \"meter_reserved_final_raw_us\": " << meterReservedRawUs
        << ",\n"
        << "    \"meter_reserved_final_normalized_us\": 0,\n"
        << "    \"measured_settlement_refunds_ledger\": false\n"
        << "  }\n"
        << "}\n";
    summary.flush();
    NS_ABORT_MSG_IF(!summary,
                    "Distributional-shadow summary write failed");
    m_summaryWritten = true;
}

uint64_t
DistributionalShadowT2Controller::GetPairedFrameCount() const
{
    return m_pairedFrames;
}

uint64_t
DistributionalShadowT2Controller::GetFeatureEvaluationCount() const
{
    return m_featureEvaluated;
}

uint64_t
DistributionalShadowT2Controller::GetLaunchCount() const
{
    return m_launchedFrameIds.size();
}

uint64_t
DistributionalShadowT2Controller::GetSettlementCount() const
{
    return m_secondarySettled;
}

void
DistributionalShadowT2Controller::DoDispose()
{
    if (m_started && !m_summaryWritten)
    {
        NS_LOG_WARN("Distributional-shadow controller disposed before summary");
    }
    if (m_decisions.is_open())
    {
        m_decisions.close();
    }
    m_pendingPrimary.reset();
    m_sender = nullptr;
    m_meter = nullptr;
    Object::DoDispose();
}

} // namespace ns3
