/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "paired-value-t2-controller.h"

#include "canonical-secondary-airtime-estimator.h"
#include "streaming-header.h"

#include "ns3/abort.h"
#include "ns3/log.h"
#include "ns3/simulator.h"

#include <algorithm>
#include <array>
#include <bit>
#include <cmath>
#include <iomanip>
#include <limits>
#include <locale>
#include <numeric>
#include <sstream>
#include <utility>

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("PairedValueT2Controller");
NS_OBJECT_ENSURE_REGISTERED(PairedValueT2Controller);

namespace
{

constexpr uint64_t NANOS_PER_MICROSECOND = 1000;
constexpr uint64_t FRAME_DEADLINE_US = 33333;
constexpr uint32_t P_FRAME_SIZE_BYTES = 12000;
constexpr uint32_t I_FRAME_SIZE_BYTES = 48000;
constexpr uint32_t PACKET_PAYLOAD_BYTES = 1200;
constexpr uint32_t QUEUE_MAX_DELAY_MS = 500;
constexpr uint32_t POST_SOCKET_MAC_OVERHEAD_BYTES = 36;
constexpr uint64_t DECISION_STOP_GUARD_US = 534000;
constexpr uint32_t REQUIRED_SUPPORT_MASK_VERSION = 2;
constexpr uint32_t REQUIRED_POLLING_SCHEMA_VERSION = 1;
constexpr std::string_view REQUIRED_SUPPORT_MASK = "0x3ffffffffdffff";
constexpr std::string_view POLICY_NAME = "paired_value_duplication_t2";
constexpr std::string_view MODEL_SPEC_ID =
    "hgb64_depth3_7leaf_two_head_ridge_log_cost_v1";
constexpr std::string_view MODEL_ARTIFACT_SHA256 =
    "dff01b0f8319320489709c4039d97011f35439aa92adedbe167fe61b9de7bcb8";
constexpr std::string_view FEATURE_FAMILY = "primary_compact_physics_temporal";
constexpr std::string_view FEATURE_ADAPTER =
    "finite_numeric_float32_then_float64_one_hot_v1";
constexpr std::string_view FEATURE_NAMES_SHA256 =
    "a00ebbb9807f99972f2cd009d1b2a20bf0b001cee123ac60d5121b2b1c07209e";
constexpr std::string_view VALUE_PER_COST_RANKER =
    "legacy_bad12_value_per_cost";
constexpr std::string_view COST_FREE_RANKER = "legacy_bad12_value";
constexpr std::string_view FRAME_GATE = "p_frames_only";
constexpr std::string_view SCORE_ADAPTER = "final_candidate_float32_threshold_ge_v1";
constexpr std::string_view BASELINE_RUNTIME_CONTRACT_ID =
    "paired-value-duplication-t2-runtime-v1";
constexpr std::string_view BASELINE_RUNTIME_CONTRACT_SHA256 =
    "b9b9caf6cf49e73cb0669107576a17790f59bda4875c43f676caa426393dbf41";
constexpr std::string_view SCORE_AWARE_RUNTIME_CONTRACT_ID =
    "paired-value-duplication-t2-score-aware-emergency-v2";
constexpr std::string_view SCORE_AWARE_RUNTIME_CONTRACT_SHA256 =
    "bdc5b2a944475d1cc31749100e333a2eb2059e106eaf86d918855b721ab3fcda";
constexpr std::string_view FULL_HORIZON_RUNTIME_CONTRACT_ID =
    "paired-value-duplication-t2-full-horizon-carryover-v3";
constexpr std::string_view FULL_HORIZON_RUNTIME_CONTRACT_SHA256 =
    "16ccbbfc19ac5c6b824c65b5f00fd0a8792610ea9239e9277390f51eda83f9d8";
constexpr std::string_view REMAINING_REFILL_RUNTIME_CONTRACT_ID =
    "paired-value-duplication-t2-remaining-refill-borrowing-v4";
constexpr std::string_view REMAINING_REFILL_RUNTIME_CONTRACT_SHA256 =
    "0b5d31861c862e1b4fb31231936ecd144958939308b21566e97405a29de0d9dd";
constexpr std::string_view COST_FREE_RUNTIME_CONTRACT_ID =
    "paired-value-duplication-t2-cost-free-score-aware-v5";
constexpr std::string_view COST_FREE_RUNTIME_CONTRACT_SHA256 =
    "b7fb00982ae090fe1142b39adf0ad6d26d253741dd5059ed95637dd86047ba96";
constexpr std::string_view COST_ESTIMATOR_ID =
    "eht_mcs5_20mhz_gi800_nss1_one_ppdu_safety125_v1";
constexpr std::string_view LAUNCH_REASON = "paired temporal value full copy at T2";

static_assert(StreamingHeader::SERIALIZED_SIZE == 50,
              "Frozen descriptor contract requires a 50-byte streaming header");
static_assert(PREDICTION_TELEMETRY_SCHEMA_VERSION == 3,
              "Frozen controller requires telemetry schema version 3");
static_assert(PREDICTION_POLLING_SCHEMA_VERSION == REQUIRED_POLLING_SCHEMA_VERSION,
              "Frozen controller requires polling schema version 1");
static_assert(FEATURE_SUPPORT_MASK_VERSION == REQUIRED_SUPPORT_MASK_VERSION,
              "Frozen controller requires support-mask version 2");
static_assert(std::bit_cast<uint32_t>(PairedValueT2Controller::EMERGENCY_SCORE_THRESHOLD) ==
                  0x391d4952U,
              "Frozen emergency score threshold differs");
static_assert(std::bit_cast<uint32_t>(PairedValueT2Controller::COST_FREE_SCORE_THRESHOLD) ==
                  0x3e3f68cfU,
              "Frozen cost-free score threshold differs");
static_assert(std::bit_cast<uint32_t>(
                  PairedValueT2Controller::COST_FREE_EMERGENCY_SCORE_THRESHOLD) ==
                  0x3e9d2ac5U,
              "Frozen cost-free emergency score threshold differs");

bool
ContainsCsvDelimiter(const std::string& value)
{
    return value.find_first_of(",\r\n\"") != std::string::npos;
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
WritePacketIndices(std::ostream& output, const DelayedCopyDescriptor& descriptor)
{
    for (std::size_t index = 0; index < descriptor.packetIndices.size(); ++index)
    {
        output << (index == 0 ? "" : ";") << descriptor.packetIndices[index];
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
                escaped << "\\u00" << std::hex << std::setw(2) << std::setfill('0')
                        << static_cast<unsigned int>(character) << std::dec << std::setfill(' ');
            }
            else
            {
                escaped << static_cast<char>(character);
            }
        }
    }
    return escaped.str();
}

bool
NearlyEqual(double left, double right)
{
    return std::isfinite(left) && std::isfinite(right) &&
           std::abs(left - right) <= PairedValueT2Controller::ACCOUNTING_TOLERANCE_US;
}

uint64_t
CurrentTimeNs()
{
    const int64_t nowNs = Simulator::Now().GetNanoSeconds();
    NS_ABORT_MSG_IF(nowNs < 0, "Paired-value controller observed negative simulation time");
    return static_cast<uint64_t>(nowNs);
}

} // namespace

TypeId
PairedValueT2Controller::GetTypeId()
{
    static TypeId tid = TypeId("ns3::PairedValueT2Controller")
                            .SetParent<Object>()
                            .SetGroupName("WifiStreaming")
                            .AddConstructor<PairedValueT2Controller>();
    return tid;
}

PairedValueT2Controller::PairedValueT2Controller()
    : m_guard(SecondaryAirtimeBudgetGuard::Configuration{BUDGET_FRACTION,
                                                         BUDGET_MAX_HORIZON_US,
                                                         BUDGET_INITIAL_HORIZON_US})
{
    NS_ABORT_MSG_IF(!m_guard.IsConfigured() ||
                        !m_guard.Initialize(MEASUREMENT_START_NS) ||
                        m_guard.GetCapacityUs() != static_cast<double>(BUDGET_CAPACITY_US) ||
                        m_guard.GetInitialCreditUs() !=
                            static_cast<double>(BUDGET_INITIAL_CREDIT_US),
                    "Frozen paired-value airtime guard failed initialization");
}

PairedValueT2Controller::~PairedValueT2Controller() = default;

void
PairedValueT2Controller::SetAdmissionProfile(AdmissionProfile profile)
{
    NS_ABORT_MSG_IF(m_started || m_decisions.is_open() || !m_summaryFile.empty(),
                    "Cannot change paired-value admission profile after output setup");
    NS_ABORT_MSG_IF(profile != AdmissionProfile::BASELINE_V1 &&
                        profile != AdmissionProfile::SCORE_AWARE_EMERGENCY_V2 &&
                        profile != AdmissionProfile::SCORE_AWARE_FULL_HORIZON_V3 &&
                        profile != AdmissionProfile::SCORE_AWARE_REMAINING_REFILL_V4 &&
                        profile != AdmissionProfile::COST_FREE_SCORE_AWARE_V5,
                    "Unknown paired-value admission profile");
    const uint64_t maximumHorizonUs = GetBudgetMaxHorizonUs(profile);
    const uint64_t capacityUs = GetBudgetCapacityUs(profile);
    NS_ABORT_MSG_IF(
        !m_guard.Configure(SecondaryAirtimeBudgetGuard::Configuration{
            BUDGET_FRACTION,
            maximumHorizonUs,
            BUDGET_INITIAL_HORIZON_US}) ||
            !m_guard.Initialize(MEASUREMENT_START_NS) ||
            m_guard.GetCapacityUs() != static_cast<double>(capacityUs) ||
            m_guard.GetInitialCreditUs() !=
                static_cast<double>(BUDGET_INITIAL_CREDIT_US),
        "Paired-value profile guard failed initialization");
    m_admissionProfile = profile;
}

PairedValueT2Controller::AdmissionProfile
PairedValueT2Controller::GetAdmissionProfile() const
{
    return m_admissionProfile;
}

std::string_view
PairedValueT2Controller::AdmissionProfileName(AdmissionProfile profile)
{
    switch (profile)
    {
    case AdmissionProfile::BASELINE_V1:
        return "baseline_v1";
    case AdmissionProfile::SCORE_AWARE_EMERGENCY_V2:
        return "score_aware_emergency_v2";
    case AdmissionProfile::SCORE_AWARE_FULL_HORIZON_V3:
        return "score_aware_full_horizon_v3";
    case AdmissionProfile::SCORE_AWARE_REMAINING_REFILL_V4:
        return "score_aware_remaining_refill_v4";
    case AdmissionProfile::COST_FREE_SCORE_AWARE_V5:
        return "cost_free_score_aware_v5";
    }
    NS_ABORT_MSG("Unknown paired-value admission profile");
    return {};
}

std::optional<PairedValueT2Controller::AdmissionProfile>
PairedValueT2Controller::ParseAdmissionProfile(std::string_view name)
{
    if (name == AdmissionProfileName(AdmissionProfile::BASELINE_V1))
    {
        return AdmissionProfile::BASELINE_V1;
    }
    if (name == AdmissionProfileName(AdmissionProfile::SCORE_AWARE_EMERGENCY_V2))
    {
        return AdmissionProfile::SCORE_AWARE_EMERGENCY_V2;
    }
    if (name == AdmissionProfileName(AdmissionProfile::SCORE_AWARE_FULL_HORIZON_V3))
    {
        return AdmissionProfile::SCORE_AWARE_FULL_HORIZON_V3;
    }
    if (name == AdmissionProfileName(AdmissionProfile::SCORE_AWARE_REMAINING_REFILL_V4))
    {
        return AdmissionProfile::SCORE_AWARE_REMAINING_REFILL_V4;
    }
    if (name == AdmissionProfileName(AdmissionProfile::COST_FREE_SCORE_AWARE_V5))
    {
        return AdmissionProfile::COST_FREE_SCORE_AWARE_V5;
    }
    return std::nullopt;
}

uint32_t
PairedValueT2Controller::GetCsvSchemaVersion(AdmissionProfile profile)
{
    switch (profile)
    {
    case AdmissionProfile::BASELINE_V1:
        return CSV_SCHEMA_VERSION;
    case AdmissionProfile::SCORE_AWARE_EMERGENCY_V2:
    case AdmissionProfile::SCORE_AWARE_FULL_HORIZON_V3:
        return SCORE_AWARE_CSV_SCHEMA_VERSION;
    case AdmissionProfile::SCORE_AWARE_REMAINING_REFILL_V4:
        return REMAINING_REFILL_CSV_SCHEMA_VERSION;
    case AdmissionProfile::COST_FREE_SCORE_AWARE_V5:
        return COST_FREE_CSV_SCHEMA_VERSION;
    }
    NS_ABORT_MSG("Unknown paired-value admission profile");
    return 0;
}

uint32_t
PairedValueT2Controller::GetSummarySchemaVersion(AdmissionProfile profile)
{
    switch (profile)
    {
    case AdmissionProfile::BASELINE_V1:
        return SUMMARY_SCHEMA_VERSION;
    case AdmissionProfile::SCORE_AWARE_EMERGENCY_V2:
    case AdmissionProfile::SCORE_AWARE_FULL_HORIZON_V3:
        return SCORE_AWARE_SUMMARY_SCHEMA_VERSION;
    case AdmissionProfile::SCORE_AWARE_REMAINING_REFILL_V4:
        return REMAINING_REFILL_SUMMARY_SCHEMA_VERSION;
    case AdmissionProfile::COST_FREE_SCORE_AWARE_V5:
        return COST_FREE_SUMMARY_SCHEMA_VERSION;
    }
    NS_ABORT_MSG("Unknown paired-value admission profile");
    return 0;
}

bool
PairedValueT2Controller::UsesScoreAwareEmergency(AdmissionProfile profile)
{
    switch (profile)
    {
    case AdmissionProfile::BASELINE_V1:
        return false;
    case AdmissionProfile::SCORE_AWARE_EMERGENCY_V2:
    case AdmissionProfile::SCORE_AWARE_FULL_HORIZON_V3:
    case AdmissionProfile::SCORE_AWARE_REMAINING_REFILL_V4:
    case AdmissionProfile::COST_FREE_SCORE_AWARE_V5:
        return true;
    }
    NS_ABORT_MSG("Unknown paired-value admission profile");
    return false;
}

bool
PairedValueT2Controller::UsesCostFreeScore(AdmissionProfile profile)
{
    switch (profile)
    {
    case AdmissionProfile::BASELINE_V1:
    case AdmissionProfile::SCORE_AWARE_EMERGENCY_V2:
    case AdmissionProfile::SCORE_AWARE_FULL_HORIZON_V3:
    case AdmissionProfile::SCORE_AWARE_REMAINING_REFILL_V4:
        return false;
    case AdmissionProfile::COST_FREE_SCORE_AWARE_V5:
        return true;
    }
    NS_ABORT_MSG("Unknown paired-value admission profile");
    return false;
}

bool
PairedValueT2Controller::UsesRemainingRefillBorrowing(AdmissionProfile profile)
{
    switch (profile)
    {
    case AdmissionProfile::BASELINE_V1:
    case AdmissionProfile::SCORE_AWARE_EMERGENCY_V2:
    case AdmissionProfile::SCORE_AWARE_FULL_HORIZON_V3:
        return false;
    case AdmissionProfile::SCORE_AWARE_REMAINING_REFILL_V4:
        return true;
    case AdmissionProfile::COST_FREE_SCORE_AWARE_V5:
        return false;
    }
    NS_ABORT_MSG("Unknown paired-value admission profile");
    return false;
}

uint64_t
PairedValueT2Controller::GetBudgetMaxHorizonUs(AdmissionProfile profile)
{
    switch (profile)
    {
    case AdmissionProfile::BASELINE_V1:
    case AdmissionProfile::SCORE_AWARE_EMERGENCY_V2:
    case AdmissionProfile::COST_FREE_SCORE_AWARE_V5:
        return BUDGET_MAX_HORIZON_US;
    case AdmissionProfile::SCORE_AWARE_FULL_HORIZON_V3:
    case AdmissionProfile::SCORE_AWARE_REMAINING_REFILL_V4:
        return FULL_HORIZON_BUDGET_MAX_HORIZON_US;
    }
    NS_ABORT_MSG("Unknown paired-value admission profile");
    return 0;
}

uint64_t
PairedValueT2Controller::GetBudgetCapacityUs(AdmissionProfile profile)
{
    switch (profile)
    {
    case AdmissionProfile::BASELINE_V1:
    case AdmissionProfile::SCORE_AWARE_EMERGENCY_V2:
    case AdmissionProfile::COST_FREE_SCORE_AWARE_V5:
        return BUDGET_CAPACITY_US;
    case AdmissionProfile::SCORE_AWARE_FULL_HORIZON_V3:
    case AdmissionProfile::SCORE_AWARE_REMAINING_REFILL_V4:
        return FULL_HORIZON_BUDGET_CAPACITY_US;
    }
    NS_ABORT_MSG("Unknown paired-value admission profile");
    return 0;
}

std::string_view
PairedValueT2Controller::GetPolicyRanker(AdmissionProfile profile)
{
    return UsesCostFreeScore(profile) ? COST_FREE_RANKER : VALUE_PER_COST_RANKER;
}

float
PairedValueT2Controller::GetPolicyScoreThreshold(AdmissionProfile profile)
{
    return UsesCostFreeScore(profile)
               ? COST_FREE_SCORE_THRESHOLD
               : TemporalT2ValueModelEvaluator::GetScoreThreshold();
}

float
PairedValueT2Controller::GetEmergencyScoreThreshold(AdmissionProfile profile)
{
    return UsesCostFreeScore(profile)
               ? COST_FREE_EMERGENCY_SCORE_THRESHOLD
               : EMERGENCY_SCORE_THRESHOLD;
}

float
PairedValueT2Controller::GetPolicyScore(
    AdmissionProfile profile,
    const TemporalT2ValueModelResult& model)
{
    return UsesCostFreeScore(profile)
               ? static_cast<float>(model.nonnegativeBad12Value)
               : model.valuePerCostScore;
}

void
PairedValueT2Controller::SetSender(MultipathSender* sender)
{
    NS_ABORT_MSG_IF(m_started, "Cannot change paired-value sender after control starts");
    NS_ABORT_MSG_IF(!sender, "Paired-value controller requires a sender");
    NS_ABORT_MSG_IF(m_sender && m_sender != sender,
                    "Paired-value controller sender was configured twice");
    m_sender = sender;
}

void
PairedValueT2Controller::SetAirtimeMeter(Ptr<SecondaryAirtimeMeter> meter)
{
    NS_ABORT_MSG_IF(m_started, "Cannot change paired-value meter after control starts");
    NS_ABORT_MSG_IF(!meter, "Paired-value controller requires an airtime meter");
    NS_ABORT_MSG_IF(m_meter && m_meter != meter,
                    "Paired-value controller meter was configured twice");
    if (!m_meter)
    {
        m_meter = meter;
        m_meter->SetMeasurementWindow(MEASUREMENT_START_NS, MEASUREMENT_STOP_NS);
        m_meter->SetQueueMaxDelayMs(QUEUE_MAX_DELAY_MS);
        m_meter->SetBudgetMetadata(BUDGET_FRACTION,
                                   static_cast<double>(BUDGET_INITIAL_CREDIT_US));
        m_meter->SetMeasuredAirtimeCallback(
            MakeCallback(&PairedValueT2Controller::NotifyMeasuredAirtime, this));
        m_meter->SetSettlementCallback(
            MakeCallback(&PairedValueT2Controller::NotifySettlement, this));
    }
}

void
PairedValueT2Controller::SetOutputFiles(const std::string& runId,
                                        const std::string& decisionsFile,
                                        const std::string& summaryFile)
{
    NS_ABORT_MSG_IF(m_started || m_decisions.is_open() || !m_summaryFile.empty(),
                    "Paired-value outputs may be configured only once before control");
    NS_ABORT_MSG_IF(runId.empty() || ContainsCsvDelimiter(runId),
                    "Paired-value run ID must be nonempty and CSV-safe");
    NS_ABORT_MSG_IF(decisionsFile.empty() || summaryFile.empty() ||
                        decisionsFile == summaryFile,
                    "Paired-value output paths must be distinct and nonempty");
    m_runId = runId;
    m_summaryFile = summaryFile;
    m_decisions.open(decisionsFile, std::ios::out | std::ios::trunc);
    NS_ABORT_MSG_IF(!m_decisions, "Cannot open paired-value decisions " << decisionsFile);
    m_decisions.imbue(std::locale::classic());
    m_decisions << std::setprecision(std::numeric_limits<double>::max_digits10);
    WriteDecisionHeader();
}

std::string_view
PairedValueT2Controller::GetRuntimeContractId()
{
    return GetRuntimeContractId(AdmissionProfile::BASELINE_V1);
}

std::string_view
PairedValueT2Controller::GetRuntimeContractId(AdmissionProfile profile)
{
    switch (profile)
    {
    case AdmissionProfile::BASELINE_V1:
        return BASELINE_RUNTIME_CONTRACT_ID;
    case AdmissionProfile::SCORE_AWARE_EMERGENCY_V2:
        return SCORE_AWARE_RUNTIME_CONTRACT_ID;
    case AdmissionProfile::SCORE_AWARE_FULL_HORIZON_V3:
        return FULL_HORIZON_RUNTIME_CONTRACT_ID;
    case AdmissionProfile::SCORE_AWARE_REMAINING_REFILL_V4:
        return REMAINING_REFILL_RUNTIME_CONTRACT_ID;
    case AdmissionProfile::COST_FREE_SCORE_AWARE_V5:
        return COST_FREE_RUNTIME_CONTRACT_ID;
    }
    NS_ABORT_MSG("Unknown paired-value admission profile");
    return {};
}

std::string_view
PairedValueT2Controller::GetRuntimeContractSha256()
{
    return GetRuntimeContractSha256(AdmissionProfile::BASELINE_V1);
}

std::string_view
PairedValueT2Controller::GetRuntimeContractSha256(AdmissionProfile profile)
{
    switch (profile)
    {
    case AdmissionProfile::BASELINE_V1:
        return BASELINE_RUNTIME_CONTRACT_SHA256;
    case AdmissionProfile::SCORE_AWARE_EMERGENCY_V2:
        return SCORE_AWARE_RUNTIME_CONTRACT_SHA256;
    case AdmissionProfile::SCORE_AWARE_FULL_HORIZON_V3:
        return FULL_HORIZON_RUNTIME_CONTRACT_SHA256;
    case AdmissionProfile::SCORE_AWARE_REMAINING_REFILL_V4:
        return REMAINING_REFILL_RUNTIME_CONTRACT_SHA256;
    case AdmissionProfile::COST_FREE_SCORE_AWARE_V5:
        return COST_FREE_RUNTIME_CONTRACT_SHA256;
    }
    NS_ABORT_MSG("Unknown paired-value admission profile");
    return {};
}

std::string_view
PairedValueT2Controller::GetCostEstimatorId()
{
    return COST_ESTIMATOR_ID;
}

void
PairedValueT2Controller::StartControl()
{
    if (m_started)
    {
        return;
    }
    NS_ABORT_MSG_IF(!m_sender, "Paired-value sender is not configured");
    NS_ABORT_MSG_IF(!m_meter, "Paired-value airtime meter is not configured");
    NS_ABORT_MSG_IF(m_runId.empty() || !m_decisions || m_summaryFile.empty(),
                    "Paired-value outputs are not configured");
    NS_ABORT_MSG_IF(!m_guard.IsOperational(),
                    "Paired-value measured-airtime guard is not operational");
    NS_ABORT_MSG_IF(!TemporalT2ValuePredictor::HasExactModelContract(),
                    "Paired-value compiled temporal model contract differs");
    NS_ABORT_MSG_IF(std::bit_cast<uint32_t>(
                        TemporalT2ValueModelEvaluator::GetScoreThreshold()) != 0x38bbc0e5U,
                    "Paired-value float32 score threshold differs");
    NS_ABORT_MSG_IF(GetReconciledMeterReservedUs() != 0,
                    "Paired-value meter has reservations before control starts");
    m_started = true;
}

std::optional<std::string>
PairedValueT2Controller::FindPendingPrimaryError(const PredictionSample& sample) const
{
    if (sample.runId != m_runId)
    {
        return "primary run ID differs from controller run ID";
    }
    if (sample.key.pathId != PRIMARY_PATH_ID || sample.key.copyId != PRIMARY_COPY_ID)
    {
        return "pending endpoint is not primary path 1/copy 0";
    }
    if (sample.sampleStage != "T2" || sample.sampleOffsetUs != T2_OFFSET_US)
    {
        return "primary endpoint is not the frozen T2 stage";
    }
    if (sample.generationTimeNs >
        std::numeric_limits<uint64_t>::max() - T2_OFFSET_US * NANOS_PER_MICROSECOND)
    {
        return "primary endpoint timestamp calculation overflows";
    }
    if (sample.sampleTimeNs !=
        sample.generationTimeNs + T2_OFFSET_US * NANOS_PER_MICROSECOND)
    {
        return "primary endpoint time differs from generation plus T2";
    }
    if (sample.sampleTimeNs != CurrentTimeNs())
    {
        return "primary endpoint callback time differs from its sample time";
    }
    if (sample.sampleTimeNs >= MEASUREMENT_STOP_NS)
    {
        return "primary endpoint is at or after the frozen measurement stop";
    }
    if (sample.deadlineTimeNs !=
        sample.generationTimeNs + FRAME_DEADLINE_US * NANOS_PER_MICROSECOND)
    {
        return "primary endpoint deadline differs from the frozen deadline";
    }
    if (sample.frameType == FrameType::P_FRAME)
    {
        if (sample.frameSizeBytes != P_FRAME_SIZE_BYTES ||
            sample.framePacketCount != P_FRAME_SIZE_BYTES / PACKET_PAYLOAD_BYTES)
        {
            return "primary P-frame metadata differs from the frozen synthetic source";
        }
    }
    else if (sample.frameType == FrameType::I_FRAME)
    {
        if (sample.frameSizeBytes != I_FRAME_SIZE_BYTES ||
            sample.framePacketCount != I_FRAME_SIZE_BYTES / PACKET_PAYLOAD_BYTES)
        {
            return "primary I-frame metadata differs from the frozen synthetic source";
        }
    }
    else
    {
        return "primary endpoint frame type is unsupported";
    }
    return std::nullopt;
}

std::optional<std::string>
PairedValueT2Controller::FindSecondaryError(const PredictionSample& sample) const
{
    if (sample.runId != m_runId)
    {
        return "secondary run ID differs from controller run ID";
    }
    if (sample.key.pathId != SECONDARY_PATH_ID || sample.key.copyId != SECONDARY_COPY_ID)
    {
        return "endpoint is not hypothetical secondary path 0/copy 1";
    }
    if (sample.sampleStage != "T2" || sample.sampleOffsetUs != T2_OFFSET_US)
    {
        return "secondary endpoint is not the frozen T2 stage";
    }
    if (sample.generationTimeNs >
        std::numeric_limits<uint64_t>::max() - T2_OFFSET_US * NANOS_PER_MICROSECOND ||
        sample.sampleTimeNs !=
            sample.generationTimeNs + T2_OFFSET_US * NANOS_PER_MICROSECOND)
    {
        return "secondary endpoint timestamp is invalid";
    }
    if (sample.sampleTimeNs != CurrentTimeNs())
    {
        return "secondary endpoint callback time differs from its sample time";
    }
    return std::nullopt;
}

std::optional<std::string>
PairedValueT2Controller::FindPairError(const PredictionSample& primary,
                                       const PredictionSample& secondary)
{
    if (primary.runId != secondary.runId ||
        primary.telemetrySchemaVersion != secondary.telemetrySchemaVersion ||
        primary.key.frameId != secondary.key.frameId ||
        primary.sampleStage != secondary.sampleStage ||
        primary.sampleOffsetUs != secondary.sampleOffsetUs ||
        primary.sampleTimeNs != secondary.sampleTimeNs ||
        primary.generationTimeNs != secondary.generationTimeNs ||
        primary.deadlineTimeNs != secondary.deadlineTimeNs ||
        primary.frameAgeUs != secondary.frameAgeUs ||
        primary.deadlineSlackUs != secondary.deadlineSlackUs ||
        primary.frameSizeBytes != secondary.frameSizeBytes ||
        primary.framePacketCount != secondary.framePacketCount ||
        primary.frameType != secondary.frameType)
    {
        return "paired T2 endpoints disagree on immutable frame metadata";
    }
    return std::nullopt;
}

std::optional<std::string>
PairedValueT2Controller::FindUntreatedSecondaryError(
    const PredictionSample& secondary)
{
    if (secondary.packetsSubmitted != 0 ||
        secondary.applicationSocketPacketBytesSubmitted != 0 ||
        secondary.packetsRemainingToSubmit != secondary.framePacketCount ||
        secondary.senderMacComplete || !secondary.actionable)
    {
        return "hypothetical secondary endpoint contains application progress";
    }
    const auto nonzero = [](const auto& value) { return value && *value != 0; };
    if (nonzero(secondary.framePacketsMacEnqueued) ||
        nonzero(secondary.framePacketsMacDequeued) ||
        nonzero(secondary.framePacketsTxSucceeded) ||
        nonzero(secondary.frameMpduAttemptFailures) ||
        nonzero(secondary.framePacketsTerminallyDropped) ||
        nonzero(secondary.framePacketsCurrentlyQueued) ||
        nonzero(secondary.frameMacServiceBytesCurrentlyQueued))
    {
        return "hypothetical secondary endpoint contains MAC progress";
    }
    return std::nullopt;
}

std::optional<std::string>
PairedValueT2Controller::FindDescriptorError(
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
        return "delayed descriptor MAC service bytes differ from the frozen formula";
    }
    return std::nullopt;
}

void
PairedValueT2Controller::NotifySnapshot(const PredictionSample& sample)
{
    if (sample.sampleOffsetUs == 0)
    {
        return;
    }
    StartControl();
    NS_ABORT_MSG_IF(sample.sampleOffsetUs != T2_OFFSET_US,
                    "Paired-value controller received an unsupported sample offset");

    if (sample.key.pathId == PRIMARY_PATH_ID && sample.key.copyId == PRIMARY_COPY_ID)
    {
        NS_ABORT_MSG_IF(m_pendingPrimary,
                        "Paired-value primary endpoints were interleaved");
        const auto error = FindPendingPrimaryError(sample);
        NS_ABORT_MSG_IF(error, "Invalid paired-value primary endpoint: " << *error);
        m_pendingPrimary = sample;
        return;
    }

    NS_ABORT_MSG_IF(sample.key.pathId != SECONDARY_PATH_ID ||
                        sample.key.copyId != SECONDARY_COPY_ID,
                    "Paired-value endpoint has an unsupported path/copy identity");
    NS_ABORT_MSG_IF(!m_pendingPrimary,
                    "Paired-value secondary endpoint arrived before its primary");
    const auto secondaryError = FindSecondaryError(sample);
    const auto pairError = FindPairError(*m_pendingPrimary, sample);
    const auto untreatedError = FindUntreatedSecondaryError(sample);
    NS_ABORT_MSG_IF(secondaryError, "Invalid paired-value secondary endpoint: " << *secondaryError);
    NS_ABORT_MSG_IF(pairError, "Invalid paired-value endpoint pair: " << *pairError);
    NS_ABORT_MSG_IF(untreatedError,
                    "Invalid paired-value untreated secondary endpoint: " << *untreatedError);

    PredictionSample primary = std::move(*m_pendingPrimary);
    m_pendingPrimary.reset();
    ProcessPair(primary, sample);
}

void
PairedValueT2Controller::CaptureAccountingBefore(DecisionEvidence& evidence)
{
    if (evidence.primary->sampleTimeNs >= MEASUREMENT_START_NS)
    {
        const uint64_t refillTimeNs =
            std::min(evidence.primary->sampleTimeNs, MEASUREMENT_STOP_NS);
        NS_ABORT_MSG_IF(!m_guard.Refill(refillTimeNs) || !m_guard.IsOperational(),
                        "Paired-value guard rejected a causal decision refill");
    }
    evidence.guardBalanceBeforeUs = m_guard.GetBalanceUs();
    evidence.guardDebtBeforeUs = m_guard.GetDebtUs();
    evidence.meterReservedBeforeUs = GetReconciledMeterReservedUs();
    const auto available =
        m_guard.GetAvailableBalanceUs(evidence.meterReservedBeforeUs);
    NS_ABORT_MSG_IF(!available || !std::isfinite(evidence.guardBalanceBeforeUs) ||
                        !std::isfinite(evidence.guardDebtBeforeUs),
                    "Paired-value guard before-state is invalid");
    evidence.guardAvailableBeforeUs = *available;
    if (UsesRemainingRefillBorrowing(m_admissionProfile))
    {
        const auto remainingRefillUs =
            m_guard.GetRemainingRefillCreditUs(MEASUREMENT_STOP_NS);
        NS_ABORT_MSG_IF(!remainingRefillUs || !std::isfinite(*remainingRefillUs) ||
                            *remainingRefillUs < 0,
                        "Paired-value remaining-refill credit is invalid");
        evidence.remainingRefillCreditUs = *remainingRefillUs;
    }
}

void
PairedValueT2Controller::CaptureAccountingAfter(DecisionEvidence& evidence)
{
    evidence.guardBalanceAfterUs = m_guard.GetBalanceUs();
    evidence.guardDebtAfterUs = m_guard.GetDebtUs();
    evidence.meterReservedAfterUs = GetReconciledMeterReservedUs();
    const auto available =
        m_guard.GetAvailableBalanceUs(evidence.meterReservedAfterUs);
    NS_ABORT_MSG_IF(!available || !std::isfinite(evidence.guardBalanceAfterUs) ||
                        !std::isfinite(evidence.guardDebtAfterUs),
                    "Paired-value guard after-state is invalid");
    evidence.guardAvailableAfterUs = *available;

    NS_ABORT_MSG_IF(!NearlyEqual(evidence.guardBalanceBeforeUs,
                                 evidence.guardBalanceAfterUs),
                    "Paired-value decision changed guard balance without measured airtime");
    if (evidence.secondaryLaunched)
    {
        NS_ABORT_MSG_IF(
            !NearlyEqual(evidence.meterReservedAfterUs,
                         evidence.meterReservedBeforeUs +
                             evidence.canonicalReservedAirtimeUs),
            "Paired-value action reservation does not reconcile");
    }
    else
    {
        NS_ABORT_MSG_IF(!NearlyEqual(evidence.meterReservedBeforeUs,
                                     evidence.meterReservedAfterUs),
                        "Paired-value non-action changed meter reservations");
    }
    NS_ABORT_MSG_IF(
        !NearlyEqual(evidence.guardAvailableBeforeUs,
                     evidence.guardBalanceBeforeUs - evidence.meterReservedBeforeUs) ||
            !NearlyEqual(evidence.guardAvailableAfterUs,
                         evidence.guardBalanceAfterUs - evidence.meterReservedAfterUs),
        "Paired-value available-airtime arithmetic does not reconcile");
}

void
PairedValueT2Controller::ProcessPair(const PredictionSample& primary,
                                     const PredictionSample& secondary)
{
    const auto history = m_predictor.ObservePrimary(primary);
    const bool inserted = m_decidedFrameIds.insert(primary.key.frameId).second;
    NS_ABORT_MSG_IF(!inserted, "Paired-value frame was decided more than once");
    ++m_pairedFrames;

    DecisionEvidence evidence;
    evidence.primary = &primary;
    evidence.secondary = &secondary;
    evidence.history = history;
    evidence.insideDecisionWindow = primary.sampleTimeNs >= DECISION_START_NS &&
                                    primary.sampleTimeNs < DECISION_STOP_NS;
    CaptureAccountingBefore(evidence);

    if (!evidence.insideDecisionWindow)
    {
        evidence.status = DecisionStatus::OUTSIDE_DECISION_WINDOW;
    }
    else if (!history.ready)
    {
        evidence.status = DecisionStatus::HISTORY_WARMUP;
    }
    else if (!TemporalT2ValuePredictor::PassesFrameGate(primary.frameType))
    {
        evidence.status = DecisionStatus::FRAME_TYPE_RESTRICTED;
    }
    else if (!primary.actionable)
    {
        evidence.status = DecisionStatus::NOT_ACTIONABLE;
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
                            "Invalid paired-value delayed descriptor: "
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
                "Paired-value canonical secondary cost is invalid");

            evidence.model = m_predictor.Evaluate(primary.key.frameId);
            evidence.featureEvaluated = true;
            evidence.policyScore =
                GetPolicyScore(m_admissionProfile, *evidence.model);
            NS_ABORT_MSG_IF(!std::isfinite(*evidence.policyScore),
                            "Paired-value active score is non-finite");
            evidence.passesScoreThreshold =
                *evidence.policyScore >=
                GetPolicyScoreThreshold(m_admissionProfile);
            ++m_featureEvaluated;
            m_learnedCostSumEvaluatedUs +=
                evidence.model->predictedSecondaryAirtimeUs;
            NS_ABORT_MSG_IF(!std::isfinite(m_learnedCostSumEvaluatedUs),
                            "Paired-value learned evaluated-cost sum overflowed");
            if (!evidence.passesScoreThreshold)
            {
                evidence.status = DecisionStatus::BELOW_SCORE_THRESHOLD;
            }
            else
            {
                ++m_scoreThresholdPassed;
                evidence.guardAdmissionConsidered = true;
                evidence.strictGuardAdmitted =
                    m_guard.CanReserve(evidence.canonicalReservedAirtimeUs,
                                       evidence.meterReservedBeforeUs);
                if (evidence.strictGuardAdmitted)
                {
                    ++m_strictGuardAdmitted;
                }
                else if (UsesScoreAwareEmergency(m_admissionProfile))
                {
                    evidence.passesEmergencyScore =
                        *evidence.policyScore >=
                        GetEmergencyScoreThreshold(m_admissionProfile);
                    if (evidence.passesEmergencyScore)
                    {
                        ++m_emergencyScorePassed;
                        evidence.emergencyAdmissionConsidered = true;
                        ++m_emergencyAdmissionConsidered;
                        evidence.emergencyAdmitted =
                            m_guard.CanReserveWithDebtLimit(
                                evidence.canonicalReservedAirtimeUs,
                                evidence.meterReservedBeforeUs,
                                EMERGENCY_MAXIMUM_DEBT_US);
                        if (evidence.emergencyAdmitted)
                        {
                            ++m_emergencyAdmitted;
                        }
                    }
                }
                if (!evidence.strictGuardAdmitted && !evidence.emergencyAdmitted &&
                    UsesRemainingRefillBorrowing(m_admissionProfile))
                {
                    evidence.remainingRefillAdmissionConsidered = true;
                    ++m_remainingRefillAdmissionConsidered;
                    evidence.remainingRefillAdmitted =
                        m_guard.CanReserveAgainstRemainingRefill(
                            evidence.canonicalReservedAirtimeUs,
                            evidence.meterReservedBeforeUs,
                            MEASUREMENT_STOP_NS);
                    if (evidence.remainingRefillAdmitted)
                    {
                        ++m_remainingRefillAdmitted;
                    }
                }
                evidence.guardAdmitted = evidence.strictGuardAdmitted ||
                                         evidence.emergencyAdmitted ||
                                         evidence.remainingRefillAdmitted;
                if (!evidence.guardAdmitted)
                {
                    evidence.status = DecisionStatus::AIRTIME_GUARD_REJECTED;
                }
                else
                {
                    evidence.launchAttempted = true;
                    ++m_launchAttempted;
                    evidence.secondaryLaunched =
                        m_sender->RequestSecondaryCopy(primary.key.frameId,
                                                       std::string(LAUNCH_REASON));
                    if (!evidence.secondaryLaunched)
                    {
                        evidence.status = DecisionStatus::LAUNCH_REJECTED;
                    }
                    else
                    {
                        m_meter->RegisterLaunchedCopy(
                            BuildReservation(*evidence.descriptor,
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
                            m_launchedFrames.emplace(primary.key.frameId, launched).second;
                        const bool indexed =
                            m_launchedFrameIds.insert(primary.key.frameId).second;
                        NS_ABORT_MSG_IF(!stored || !indexed,
                                        "Paired-value frame launched more than once");
                        m_expectedMeterReservedUs +=
                            evidence.canonicalReservedAirtimeUs;
                        NS_ABORT_MSG_IF(!std::isfinite(m_expectedMeterReservedUs),
                                        "Paired-value reservation total overflowed");
                        m_learnedCostSumLaunchedUs +=
                            evidence.model->predictedSecondaryAirtimeUs;
                        m_canonicalNominalLaunchedSumUs +=
                            evidence.canonicalNominalAirtimeUs;
                        m_canonicalReservedLaunchedSumUs +=
                            evidence.canonicalReservedAirtimeUs;
                        NS_ABORT_MSG_IF(
                            !std::isfinite(m_learnedCostSumLaunchedUs) ||
                                !std::isfinite(m_canonicalNominalLaunchedSumUs) ||
                                !std::isfinite(m_canonicalReservedLaunchedSumUs),
                            "Paired-value launched-cost sum overflowed");
                        GetReconciledMeterReservedUs();
                        evidence.status = DecisionStatus::ACTION;
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
PairedValueT2Controller::BuildReservation(
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

double
PairedValueT2Controller::GetReconciledMeterReservedUs() const
{
    NS_ABORT_MSG_IF(!m_meter, "Paired-value meter reservation queried before setup");
    const double actual = m_meter->GetReservedAirtimeUs();
    NS_ABORT_MSG_IF(!std::isfinite(actual) || actual < 0 ||
                        !std::isfinite(m_expectedMeterReservedUs) ||
                        m_expectedMeterReservedUs < 0 ||
                        !NearlyEqual(actual, m_expectedMeterReservedUs),
                    "Paired-value tracked and meter reservations disagree: tracked="
                        << m_expectedMeterReservedUs << " actual=" << actual);
    return actual;
}

void
PairedValueT2Controller::NotifyMeasuredAirtime(uint64_t frameId,
                                               double allocatedUs,
                                               double ppduDurationUs)
{
    auto launched = m_launchedFrames.find(frameId);
    NS_ABORT_MSG_IF(launched == m_launchedFrames.end() || launched->second.settled,
                    "Measured airtime references an unlaunched or settled paired-value frame");
    NS_ABORT_MSG_IF(!std::isfinite(allocatedUs) || !(allocatedUs > 0) ||
                        !std::isfinite(ppduDurationUs) || !(ppduDurationUs > 0) ||
                        allocatedUs > ppduDurationUs + ACCOUNTING_TOLERANCE_US,
                    "Paired-value measured-airtime callback is invalid");
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
                    "Paired-value measured-airtime accounting became invalid");
    GetReconciledMeterReservedUs();

    const uint64_t nowNs = CurrentTimeNs();
    NS_ABORT_MSG_IF(nowNs < MEASUREMENT_START_NS || nowNs >= MEASUREMENT_STOP_NS,
                    "Paired-value measured callback is outside the measurement window");
    NS_ABORT_MSG_IF(!m_guard.DebitMeasuredAirtime(nowNs, allocatedUs) ||
                        !m_guard.IsOperational(),
                    "Paired-value measured-airtime debit failed closed");
    m_meter->ObserveBudgetDebt(m_guard.GetDebtUs());
}

void
PairedValueT2Controller::NotifySettlement(uint64_t frameId,
                                          double releasedUs,
                                          double measuredUs,
                                          double nominalUs,
                                          bool fallback)
{
    (void)fallback;
    auto launched = m_launchedFrames.find(frameId);
    NS_ABORT_MSG_IF(launched == m_launchedFrames.end() || launched->second.settled,
                    "Settlement references an unlaunched or settled paired-value frame");
    auto& state = launched->second;
    NS_ABORT_MSG_IF(!std::isfinite(releasedUs) || releasedUs < 0 ||
                        !std::isfinite(measuredUs) || measuredUs < 0 ||
                        !std::isfinite(nominalUs) || !(nominalUs > 0) ||
                        !NearlyEqual(releasedUs, state.remainingReservedUs) ||
                        !NearlyEqual(measuredUs, state.measuredAirtimeUs) ||
                        !NearlyEqual(nominalUs, state.nominalAirtimeUs),
                    "Paired-value settlement evidence does not reconcile");
    m_expectedMeterReservedUs -= state.remainingReservedUs;
    state.remainingReservedUs = 0;
    state.settled = true;
    if (std::abs(m_expectedMeterReservedUs) <= ACCOUNTING_TOLERANCE_US)
    {
        m_expectedMeterReservedUs = 0;
    }
    NS_ABORT_MSG_IF(m_expectedMeterReservedUs < 0 ||
                        !std::isfinite(m_expectedMeterReservedUs),
                    "Paired-value settlement made reservations invalid");
    const bool inserted = m_settledFrameIds.insert(frameId).second;
    NS_ABORT_MSG_IF(!inserted, "Paired-value frame settled more than once");
    ++m_secondarySettled;
    GetReconciledMeterReservedUs();
}

std::string_view
PairedValueT2Controller::StatusName(DecisionStatus status)
{
    constexpr std::array<std::string_view, 9> names{
        "outside_decision_window",
        "history_warmup",
        "frame_type_restricted",
        "not_actionable",
        "descriptor_unavailable",
        "below_score_threshold",
        "airtime_guard_rejected",
        "launch_rejected",
        "action",
    };
    return names.at(StatusIndex(status));
}

std::size_t
PairedValueT2Controller::StatusIndex(DecisionStatus status)
{
    const auto index = static_cast<std::size_t>(status);
    NS_ABORT_MSG_IF(index >= 9, "Unknown paired-value decision status");
    return index;
}

void
PairedValueT2Controller::WriteDecisionHeader()
{
    m_decisions
        << "schema_version,run_id,frame_id,policy,decision_status,primary_path_id,"
           "primary_copy_id,secondary_path_id,secondary_copy_id,sample_stage,"
           "sample_offset_us,generation_time_ns,deadline_time_ns,primary_sample_time_ns,"
           "secondary_sample_time_ns,primary_feature_watermark_time_ns,"
           "primary_feature_watermark_sequence,frame_type,frame_size_bytes,"
           "frame_packet_count,primary_actionable,decision_window_start_ns,"
           "decision_window_stop_ns,inside_decision_window,history_ready,"
           "current_poll_capture_time_ns,current_poll_available_time_ns,lag1_frame_id,"
           "lag1_poll_capture_time_ns,lag3_frame_id,lag3_poll_capture_time_ns,"
           "lag8_frame_id,lag8_poll_capture_time_ns,feature_evaluated,model_spec_id,"
           "model_artifact_sha256,feature_family,feature_count,feature_adapter_id,"
           "ordered_feature_names_sha256,ranker,frame_gate,score_adapter_id,"
           "score_threshold_float32,primary_bad12_logit,primary_bad12_probability,"
           "treated_bad12_logit,treated_bad12_probability,predicted_log_airtime,"
           "predicted_secondary_airtime_us,nonnegative_bad12_value,"
           "value_per_cost_score_float32,passes_score_threshold,descriptor_checked,"
           "descriptor_available,descriptor_frame_packet_count,descriptor_packet_count,"
           "descriptor_packet_indices,descriptor_expected_mac_service_bytes,"
           "descriptor_deadline_time_ns,canonical_cost_estimator_id,cost_safety_factor,"
           "canonical_nominal_airtime_us,canonical_reserved_airtime_us,guard_fraction,"
           "guard_max_horizon_us,guard_initial_horizon_us,guard_capacity_us,"
           "guard_initial_credit_us,guard_balance_before_us,meter_reserved_before_us,"
           "guard_available_before_us,guard_debt_before_us,guard_admission_considered,"
           "guard_admitted,launch_attempted,secondary_launched,guard_balance_after_us,"
           "meter_reserved_after_us,guard_available_after_us,guard_debt_after_us,"
           "learned_cost_token_accounting";
    if (UsesScoreAwareEmergency(m_admissionProfile))
    {
        m_decisions
            << ",admission_profile_id,strict_guard_admitted,"
               "emergency_score_threshold_float32,passes_emergency_score_threshold,"
               "emergency_admission_considered,emergency_maximum_debt_us,"
               "emergency_admitted,admission_tier";
    }
    if (UsesRemainingRefillBorrowing(m_admissionProfile))
    {
        m_decisions
            << ",remaining_refill_credit_us,remaining_refill_admission_considered,"
               "remaining_refill_admitted";
    }
    if (UsesCostFreeScore(m_admissionProfile))
    {
        m_decisions << ",policy_score_float32";
    }
    m_decisions << '\n';
    m_decisions.flush();
}

void
PairedValueT2Controller::WriteDecision(const DecisionEvidence& evidence)
{
    NS_ABORT_MSG_IF(!evidence.primary || !evidence.secondary,
                    "Paired-value decision row lacks its endpoint pair");
    const auto& primary = *evidence.primary;
    const auto& secondary = *evidence.secondary;
    const auto& lags = evidence.history.lags;
    NS_ABORT_MSG_IF(lags.size() != 3 || lags[0].lagFrames != 1 ||
                        lags[1].lagFrames != 3 || lags[2].lagFrames != 8,
                    "Paired-value decision row has invalid exact-lag evidence");
    NS_ABORT_MSG_IF(evidence.featureEvaluated != evidence.model.has_value(),
                    "Paired-value decision model evidence is inconsistent");
    NS_ABORT_MSG_IF(evidence.featureEvaluated != evidence.policyScore.has_value(),
                    "Paired-value decision policy-score evidence is inconsistent");
    NS_ABORT_MSG_IF(evidence.descriptor.has_value() && !evidence.descriptorChecked,
                    "Paired-value descriptor evidence bypassed its gate");

    m_decisions << GetCsvSchemaVersion(m_admissionProfile) << ',' << m_runId << ','
                << primary.key.frameId << ','
                << POLICY_NAME << ',' << StatusName(evidence.status) << ','
                << +PRIMARY_PATH_ID << ',' << +PRIMARY_COPY_ID << ','
                << +SECONDARY_PATH_ID << ',' << +SECONDARY_COPY_ID << ',' << "T2" << ','
                << T2_OFFSET_US << ',' << primary.generationTimeNs << ','
                << primary.deadlineTimeNs << ',' << primary.sampleTimeNs << ','
                << secondary.sampleTimeNs << ',';
    WriteOptionalUint64(m_decisions, primary.latestFeatureEventTimeNs);
    m_decisions << ',' << primary.latestFeatureEventSequence << ','
                << FrameTypeToString(primary.frameType) << ',' << primary.frameSizeBytes << ','
                << primary.framePacketCount << ',' << primary.actionable << ','
                << DECISION_START_NS << ',' << DECISION_STOP_NS << ','
                << evidence.insideDecisionWindow << ',' << evidence.history.ready << ','
                << evidence.history.currentPollCaptureTimeNs << ','
                << evidence.history.currentPollAvailableTimeNs << ',';
    WriteOptionalUint64(m_decisions, lags[0].frameId);
    m_decisions << ',';
    WriteOptionalUint64(m_decisions, lags[0].pollCaptureTimeNs);
    m_decisions << ',';
    WriteOptionalUint64(m_decisions, lags[1].frameId);
    m_decisions << ',';
    WriteOptionalUint64(m_decisions, lags[1].pollCaptureTimeNs);
    m_decisions << ',';
    WriteOptionalUint64(m_decisions, lags[2].frameId);
    m_decisions << ',';
    WriteOptionalUint64(m_decisions, lags[2].pollCaptureTimeNs);
    m_decisions << ',' << evidence.featureEvaluated << ',' << MODEL_SPEC_ID << ','
                << MODEL_ARTIFACT_SHA256 << ',' << FEATURE_FAMILY << ','
                << TemporalT2ValuePredictor::FEATURE_COUNT << ',' << FEATURE_ADAPTER << ','
                << FEATURE_NAMES_SHA256 << ','
                << GetPolicyRanker(m_admissionProfile) << ',' << FRAME_GATE << ','
                << SCORE_ADAPTER << ','
                << GetPolicyScoreThreshold(m_admissionProfile) << ',';

    if (evidence.model)
    {
        m_decisions << evidence.model->primaryBad12Logit << ','
                    << evidence.model->primaryBad12Probability << ','
                    << evidence.model->treatedBad12Logit << ','
                    << evidence.model->treatedBad12Probability << ','
                    << evidence.model->predictedLogAirtime << ','
                    << evidence.model->predictedSecondaryAirtimeUs << ','
                    << evidence.model->nonnegativeBad12Value << ','
                    << evidence.model->valuePerCostScore << ','
                    << evidence.passesScoreThreshold;
    }
    else
    {
        m_decisions << ",,,,,,,,";
    }
    m_decisions << ',' << evidence.descriptorChecked << ','
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
    m_decisions << ',' << BUDGET_FRACTION << ','
                << GetBudgetMaxHorizonUs(m_admissionProfile) << ','
                << BUDGET_INITIAL_HORIZON_US << ','
                << GetBudgetCapacityUs(m_admissionProfile) << ','
                << BUDGET_INITIAL_CREDIT_US << ',' << evidence.guardBalanceBeforeUs << ','
                << evidence.meterReservedBeforeUs << ','
                << evidence.guardAvailableBeforeUs << ',' << evidence.guardDebtBeforeUs << ','
                << evidence.guardAdmissionConsidered << ',' << evidence.guardAdmitted << ','
                << evidence.launchAttempted << ',' << evidence.secondaryLaunched << ','
                << evidence.guardBalanceAfterUs << ',' << evidence.meterReservedAfterUs << ','
                << evidence.guardAvailableAfterUs << ',' << evidence.guardDebtAfterUs << ','
                << 0;
    if (UsesScoreAwareEmergency(m_admissionProfile))
    {
        const std::string_view admissionTier =
            evidence.strictGuardAdmitted
                ? "strict"
                : (evidence.emergencyAdmitted
                       ? "emergency"
                       : (evidence.remainingRefillAdmitted ? "remaining_refill" : "none"));
        m_decisions << ',' << AdmissionProfileName(m_admissionProfile) << ','
                    << evidence.strictGuardAdmitted << ','
                    << GetEmergencyScoreThreshold(m_admissionProfile) << ','
                    << evidence.passesEmergencyScore << ','
                    << evidence.emergencyAdmissionConsidered << ','
                    << EMERGENCY_MAXIMUM_DEBT_US << ','
                    << evidence.emergencyAdmitted << ',' << admissionTier;
    }
    if (UsesRemainingRefillBorrowing(m_admissionProfile))
    {
        m_decisions << ',' << evidence.remainingRefillCreditUs << ','
                    << evidence.remainingRefillAdmissionConsidered << ','
                    << evidence.remainingRefillAdmitted;
    }
    if (UsesCostFreeScore(m_admissionProfile))
    {
        m_decisions << ',';
        if (evidence.policyScore)
        {
            m_decisions << *evidence.policyScore;
        }
    }
    m_decisions << '\n';
    m_decisions.flush();
    NS_ABORT_MSG_IF(!m_decisions, "Paired-value decision row write failed");
}

void
PairedValueT2Controller::WriteSummary(
    uint64_t generatedFrames,
    const std::set<uint64_t>& duplicatedFrameIds)
{
    NS_ABORT_MSG_IF(!m_started, "Paired-value summary requires completed control");
    NS_ABORT_MSG_IF(m_summaryWritten, "Paired-value summary was written twice");
    NS_ABORT_MSG_IF(m_pendingPrimary,
                    "Paired-value summary has an unmatched pending primary endpoint");
    NS_ABORT_MSG_IF(m_decidedFrameIds.size() != m_pairedFrames,
                    "Paired-value decision-row identities do not reconcile");
    NS_ABORT_MSG_IF(generatedFrames != m_pairedFrames,
                    "Paired-value generated and paired frame counts differ");

    const uint64_t statusSum =
        std::accumulate(m_statusCounts.begin(), m_statusCounts.end(), uint64_t{0});
    const uint64_t featureStatusSum =
        m_statusCounts[StatusIndex(DecisionStatus::BELOW_SCORE_THRESHOLD)] +
        m_statusCounts[StatusIndex(DecisionStatus::AIRTIME_GUARD_REJECTED)] +
        m_statusCounts[StatusIndex(DecisionStatus::LAUNCH_REJECTED)] +
        m_statusCounts[StatusIndex(DecisionStatus::ACTION)];
    const uint64_t scorePassStatusSum =
        m_statusCounts[StatusIndex(DecisionStatus::AIRTIME_GUARD_REJECTED)] +
        m_statusCounts[StatusIndex(DecisionStatus::LAUNCH_REJECTED)] +
        m_statusCounts[StatusIndex(DecisionStatus::ACTION)];
    const uint64_t launchAttemptStatusSum =
        m_statusCounts[StatusIndex(DecisionStatus::LAUNCH_REJECTED)] +
        m_statusCounts[StatusIndex(DecisionStatus::ACTION)];
    NS_ABORT_MSG_IF(statusSum != m_pairedFrames ||
                        featureStatusSum != m_featureEvaluated ||
                        scorePassStatusSum != m_scoreThresholdPassed ||
                        launchAttemptStatusSum != m_launchAttempted ||
                        m_strictGuardAdmitted + m_emergencyAdmitted +
                                m_remainingRefillAdmitted !=
                            m_launchAttempted ||
                        m_emergencyScorePassed !=
                            m_emergencyAdmissionConsidered ||
                        m_emergencyAdmitted > m_emergencyAdmissionConsidered ||
                        m_remainingRefillAdmitted >
                            m_remainingRefillAdmissionConsidered ||
                        (UsesRemainingRefillBorrowing(m_admissionProfile) &&
                         m_remainingRefillAdmissionConsidered !=
                             m_remainingRefillAdmitted +
                                 m_statusCounts[StatusIndex(
                                     DecisionStatus::AIRTIME_GUARD_REJECTED)]) ||
                        (!UsesScoreAwareEmergency(m_admissionProfile) &&
                         (m_emergencyScorePassed != 0 ||
                          m_emergencyAdmissionConsidered != 0 ||
                          m_emergencyAdmitted != 0)) ||
                        (!UsesRemainingRefillBorrowing(m_admissionProfile) &&
                         (m_remainingRefillAdmissionConsidered != 0 ||
                          m_remainingRefillAdmitted != 0)) ||
                        m_launchedFrameIds.size() !=
                            m_statusCounts[StatusIndex(DecisionStatus::ACTION)] ||
                        m_launchedFrames.size() != m_launchedFrameIds.size() ||
                        m_settledFrameIds.size() != m_secondarySettled ||
                        m_launchedFrameIds != m_settledFrameIds ||
                        m_launchedFrameIds != duplicatedFrameIds,
                    "Paired-value final count or frame-ID reconciliation failed");

    for (const auto& [frameId, launched] : m_launchedFrames)
    {
        NS_ABORT_MSG_IF(!launched.settled ||
                            launched.remainingReservedUs != 0 ||
                            !m_settledFrameIds.contains(frameId),
                        "Paired-value final launch state is unsettled");
    }
    const double meterReservedRawUs = GetReconciledMeterReservedUs();
    NS_ABORT_MSG_IF(std::abs(meterReservedRawUs) > ACCOUNTING_TOLERANCE_US,
                    "Paired-value final meter reservation is nonzero");
    const double measuredDebitedUs = m_guard.GetMeasuredAirtimeDebitedUs();
    NS_ABORT_MSG_IF(!NearlyEqual(measuredDebitedUs,
                                 m_meter->GetMeasuredAirtimeTotalUs()) ||
                        !NearlyEqual(m_canonicalReservedLaunchedSumUs,
                                     m_meter->GetEstimatedActionAirtimeUs()),
                    "Paired-value guard or launch costs differ from meter evidence");
    const std::array<double, 5> airtimeSums{
        m_learnedCostSumEvaluatedUs,
        m_learnedCostSumLaunchedUs,
        m_canonicalNominalLaunchedSumUs,
        m_canonicalReservedLaunchedSumUs,
        measuredDebitedUs,
    };
    NS_ABORT_MSG_IF(std::any_of(airtimeSums.begin(), airtimeSums.end(), [](double value) {
                        return !std::isfinite(value) || value < 0;
                    }),
                    "Paired-value final airtime summary is invalid");

    std::ofstream summary(m_summaryFile, std::ios::out | std::ios::trunc);
    NS_ABORT_MSG_IF(!summary, "Cannot open paired-value summary " << m_summaryFile);
    summary.imbue(std::locale::classic());
    summary << std::setprecision(std::numeric_limits<double>::max_digits10);
    summary
        << "{\n"
        << "  \"schema_version\": "
        << GetSummarySchemaVersion(m_admissionProfile) << ",\n"
        << "  \"run_id\": \"" << JsonEscape(m_runId) << "\",\n"
        << "  \"policy\": \"" << POLICY_NAME << "\",\n"
        << "  \"runtime_contract_id\": \""
        << GetRuntimeContractId(m_admissionProfile) << "\",\n"
        << "  \"runtime_contract_sha256\": \""
        << GetRuntimeContractSha256(m_admissionProfile) << "\",\n"
        << "  \"source_artifacts\": {\n"
        << "    \"frozen_selection\": {\"path\": \"experiments/model-selection/"
           "temporal-t2-primary-only-two-objective-v1.json\", \"sha256\": "
           "\"c7f886a4ca1a29b9fbd2e25d19d78f994d7136ecdea4f6a16db77eacacf5ce9f\"},\n"
        << "    \"canonical_fit_manifest\": {\"path\": \"results/"
           "randomized_full_copy_exploration_collection_v1/"
           "temporal_t2_primary_only_two_objective_v1/artifact_manifest.json\", "
           "\"sha256\": \"b3af02b647c7671a631f3d43ebece75781989889358c845335d4003610a8208f\"},\n"
        << "    \"canonical_model_pickle\": {\"path\": \"results/"
           "randomized_full_copy_exploration_collection_v1/"
           "temporal_t2_primary_only_two_objective_v1/temporal_t2_value_models.pkl\", "
           "\"sha256\": \"" << MODEL_ARTIFACT_SHA256 << "\"},\n"
        << "    \"canonical_candidates\": {\"path\": \"results/"
           "randomized_full_copy_exploration_collection_v1/"
           "temporal_t2_primary_only_two_objective_v1/"
           "temporal_t2_value_policy_candidates.csv\", \"sha256\": "
           "\"7cbd5c622838df0a2f752c3bf9f4c54f333f7d280a9240cb80eda19efb1c28bb\"},\n"
        << "    \"canonical_metrics\": {\"path\": \"results/"
           "randomized_full_copy_exploration_collection_v1/"
           "temporal_t2_primary_only_two_objective_v1/"
           "temporal_t2_value_training_metrics.json\", \"sha256\": "
           "\"35929f0638b03ec79f2f3967dd947265c3d73b7fa51f487299cc1d96a555a014\"}\n"
        << "  },\n"
        << "  \"model\": {\n"
        << "    \"model_spec_id\": \"" << MODEL_SPEC_ID << "\",\n"
        << "    \"artifact_sha256\": \"" << MODEL_ARTIFACT_SHA256 << "\",\n"
        << "    \"feature_family\": \"" << FEATURE_FAMILY << "\",\n"
        << "    \"feature_count\": " << TemporalT2ValuePredictor::FEATURE_COUNT << ",\n"
        << "    \"feature_adapter_id\": \"" << FEATURE_ADAPTER << "\",\n"
        << "    \"ordered_feature_names_sha256\": \"" << FEATURE_NAMES_SHA256 << "\",\n"
        << "    \"ranker\": \"" << GetPolicyRanker(m_admissionProfile)
        << "\",\n"
        << "    \"frame_gate\": \"" << FRAME_GATE << "\",\n"
        << "    \"score_adapter_id\": \"" << SCORE_ADAPTER << "\",\n"
        << "    \"score_threshold_float32\": "
        << GetPolicyScoreThreshold(m_admissionProfile) << ",\n"
        << "    \"score_threshold_float32_bits_hex\": \""
        << (UsesCostFreeScore(m_admissionProfile) ? "0x3e3f68cf" : "0x38bbc0e5")
        << "\"\n"
        << "  },\n"
        << "  \"telemetry\": {\n"
        << "    \"telemetry_schema_version\": " << PREDICTION_TELEMETRY_SCHEMA_VERSION
        << ",\n"
        << "    \"polling_schema_version\": " << PREDICTION_POLLING_SCHEMA_VERSION << ",\n"
        << "    \"feature_support_mask_version\": " << FEATURE_SUPPORT_MASK_VERSION
        << ",\n"
        << "    \"primary_required_support_mask_hex\": \"" << REQUIRED_SUPPORT_MASK
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
        << "    \"interval_semantics\": \"half_open\",\n"
        << "    \"decision_stop_guard_us\": " << DECISION_STOP_GUARD_US << "\n"
        << "  },\n"
        << "  \"budget_guard\": {\n"
        << "    \"canonical_estimator_id\": \"" << COST_ESTIMATOR_ID << "\",\n"
        << "    \"cost_safety_factor\": " << COST_SAFETY_FACTOR << ",\n"
        << "    \"fraction\": " << BUDGET_FRACTION << ",\n"
        << "    \"max_horizon_us\": "
        << GetBudgetMaxHorizonUs(m_admissionProfile) << ",\n"
        << "    \"initial_horizon_us\": " << BUDGET_INITIAL_HORIZON_US << ",\n"
        << "    \"capacity_us\": " << GetBudgetCapacityUs(m_admissionProfile)
        << ",\n"
        << "    \"initial_credit_us\": " << BUDGET_INITIAL_CREDIT_US << ",\n"
        << "    \"initialization_time_ns\": " << MEASUREMENT_START_NS << ",\n"
        << "    \"accounting_absolute_tolerance_us\": " << ACCOUNTING_TOLERANCE_US;
    if (UsesScoreAwareEmergency(m_admissionProfile))
    {
        summary
            << ",\n"
            << "    \"admission_profile_id\": \""
            << AdmissionProfileName(m_admissionProfile) << "\",\n"
            << "    \"emergency_score_threshold_float32\": "
            << GetEmergencyScoreThreshold(m_admissionProfile) << ",\n"
            << "    \"emergency_score_threshold_float32_bits_hex\": "
            << (UsesCostFreeScore(m_admissionProfile) ? "\"0x3e9d2ac5\""
                                                      : "\"0x391d4952\"")
            << ",\n"
            << "    \"emergency_maximum_debt_us\": "
            << EMERGENCY_MAXIMUM_DEBT_US;
        if (UsesRemainingRefillBorrowing(m_admissionProfile))
        {
            summary
                << ",\n"
                << "    \"remaining_refill_borrowing_enabled\": true,\n"
                << "    \"remaining_refill_repayment_stop_ns\": "
                << MEASUREMENT_STOP_NS << ",\n"
                << "    \"remaining_refill_credit_formula\": "
                   "\"fraction * (repayment_stop_ns - "
                   "last_causal_guard_refill_ns) / 1000\"";
        }
        summary << "\n";
    }
    else
    {
        summary << "\n";
    }
    summary
        << "  },\n"
        << "  \"counts\": {\n"
        << "    \"generated_frames\": " << generatedFrames << ",\n"
        << "    \"paired_t2_frames\": " << m_pairedFrames << ",\n"
        << "    \"outside_decision_window\": "
        << m_statusCounts[StatusIndex(DecisionStatus::OUTSIDE_DECISION_WINDOW)] << ",\n"
        << "    \"history_warmup\": "
        << m_statusCounts[StatusIndex(DecisionStatus::HISTORY_WARMUP)] << ",\n"
        << "    \"frame_type_restricted\": "
        << m_statusCounts[StatusIndex(DecisionStatus::FRAME_TYPE_RESTRICTED)] << ",\n"
        << "    \"not_actionable\": "
        << m_statusCounts[StatusIndex(DecisionStatus::NOT_ACTIONABLE)] << ",\n"
        << "    \"descriptor_unavailable\": "
        << m_statusCounts[StatusIndex(DecisionStatus::DESCRIPTOR_UNAVAILABLE)] << ",\n"
        << "    \"feature_evaluated\": " << m_featureEvaluated << ",\n"
        << "    \"below_score_threshold\": "
        << m_statusCounts[StatusIndex(DecisionStatus::BELOW_SCORE_THRESHOLD)] << ",\n"
        << "    \"score_threshold_passed\": " << m_scoreThresholdPassed << ",\n"
        << "    \"airtime_guard_rejected\": "
        << m_statusCounts[StatusIndex(DecisionStatus::AIRTIME_GUARD_REJECTED)] << ",\n"
        << "    \"launch_attempted\": " << m_launchAttempted << ",\n"
        << "    \"launch_rejected\": "
        << m_statusCounts[StatusIndex(DecisionStatus::LAUNCH_REJECTED)] << ",\n"
        << "    \"secondary_launched\": " << m_launchedFrameIds.size() << ",\n"
        << "    \"secondary_settled\": " << m_secondarySettled;
    if (UsesScoreAwareEmergency(m_admissionProfile))
    {
        summary << ",\n"
                << "    \"strict_guard_admitted\": "
                << m_strictGuardAdmitted << ",\n"
                << "    \"emergency_score_threshold_passed\": "
                << m_emergencyScorePassed << ",\n"
                << "    \"emergency_admission_considered\": "
                << m_emergencyAdmissionConsidered << ",\n"
                << "    \"emergency_admitted\": " << m_emergencyAdmitted;
        if (UsesRemainingRefillBorrowing(m_admissionProfile))
        {
            summary << ",\n"
                    << "    \"remaining_refill_admission_considered\": "
                    << m_remainingRefillAdmissionConsidered << ",\n"
                    << "    \"remaining_refill_admitted\": "
                    << m_remainingRefillAdmitted;
        }
        summary << "\n";
    }
    else
    {
        summary << "\n";
    }
    summary
        << "  },\n"
        << "  \"airtime\": {\n"
        << "    \"learned_predicted_cost_sum_evaluated_us\": "
        << m_learnedCostSumEvaluatedUs << ",\n"
        << "    \"learned_predicted_cost_sum_launched_us\": "
        << m_learnedCostSumLaunchedUs << ",\n"
        << "    \"canonical_nominal_launched_sum_us\": "
        << m_canonicalNominalLaunchedSumUs << ",\n"
        << "    \"canonical_reserved_launched_sum_us\": "
        << m_canonicalReservedLaunchedSumUs << ",\n"
        << "    \"measured_secondary_airtime_debited_us\": " << measuredDebitedUs << "\n"
        << "  },\n"
        << "  \"integrity\": {\n"
        << "    \"pending_pair_empty\": true,\n"
        << "    \"generated_equals_paired\": true,\n"
        << "    \"status_counts_reconcile\": true,\n"
        << "    \"launches_equal_settlements\": true,\n"
        << "    \"launched_frame_ids_equal_duplicated_frame_ids\": true,\n"
        << "    \"meter_reserved_final_within_tolerance\": true,\n"
        << "    \"meter_reserved_final_raw_us\": " << meterReservedRawUs << ",\n"
        << "    \"meter_reserved_final_normalized_us\": 0,\n"
        << "    \"learned_cost_used_for_token_accounting\": false";
    if (UsesRemainingRefillBorrowing(m_admissionProfile))
    {
        summary
            << ",\n"
            << "    \"strict_plus_emergency_plus_remaining_refill_admitted_equals_"
               "launch_attempted\": true\n";
    }
    else if (UsesScoreAwareEmergency(m_admissionProfile))
    {
        summary
            << ",\n"
            << "    \"strict_plus_emergency_admitted_equals_launch_attempted\": true\n";
    }
    else
    {
        summary << "\n";
    }
    summary
        << "  }\n"
        << "}\n";
    summary.flush();
    NS_ABORT_MSG_IF(!summary, "Paired-value summary write failed");
    m_summaryWritten = true;
}

uint64_t
PairedValueT2Controller::GetPairedFrameCount() const
{
    return m_pairedFrames;
}

uint64_t
PairedValueT2Controller::GetFeatureEvaluationCount() const
{
    return m_featureEvaluated;
}

uint64_t
PairedValueT2Controller::GetLaunchCount() const
{
    return m_launchedFrameIds.size();
}

uint64_t
PairedValueT2Controller::GetSettlementCount() const
{
    return m_secondarySettled;
}

void
PairedValueT2Controller::DoDispose()
{
    m_sender = nullptr;
    if (m_meter)
    {
        m_meter->SetMeasuredAirtimeCallback(SecondaryAirtimeMeter::MeasuredAirtimeCallback());
        m_meter->SetSettlementCallback(SecondaryAirtimeMeter::SettlementCallback());
    }
    m_meter = nullptr;
    if (m_decisions.is_open())
    {
        m_decisions.close();
    }
    Object::DoDispose();
}

} // namespace ns3
