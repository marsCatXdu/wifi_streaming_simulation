/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "mechanism-experiment-controller.h"

#include "canonical-secondary-airtime-estimator.h"

#include "ns3/abort.h"
#include "ns3/simulator.h"

#include <charconv>
#include <iomanip>
#include <limits>
#include <locale>
#include <set>
#include <sstream>
#include <string_view>

namespace ns3
{

NS_OBJECT_ENSURE_REGISTERED(MechanismExperimentController);

namespace
{

constexpr std::string_view PACKET_OUTCOME_HEADER =
    "run_id,frame_id,source_packet_count,received_source_packet_indices,"
    "missing_source_packet_indices,copy_0_source_packet_indices,"
    "copy_1_source_packet_indices,link_0_source_packet_indices,"
    "link_1_source_packet_indices,received_coded_repair_indices";

std::vector<std::string>
Split(const std::string& value, char delimiter)
{
    std::vector<std::string> fields;
    std::size_t start = 0;
    while (true)
    {
        const std::size_t end = value.find(delimiter, start);
        fields.push_back(value.substr(start, end - start));
        if (end == std::string::npos)
        {
            break;
        }
        start = end + 1;
    }
    return fields;
}

uint64_t
ParseUint64(const std::string& value, const std::string& label)
{
    uint64_t parsed = 0;
    const auto result =
        std::from_chars(value.data(), value.data() + value.size(), parsed);
    NS_ABORT_MSG_IF(value.empty() || result.ec != std::errc() ||
                        result.ptr != value.data() + value.size(),
                    "Invalid " << label << " in oracle packet outcomes: " << value);
    return parsed;
}

std::vector<uint32_t>
ParsePacketIndices(const std::string& value, uint32_t packetCount)
{
    std::vector<uint32_t> indices;
    if (value.empty())
    {
        return indices;
    }
    for (const auto& token : Split(value, ';'))
    {
        const uint64_t parsed = ParseUint64(token, "packet index");
        NS_ABORT_MSG_IF(parsed >= packetCount,
                        "Oracle packet index exceeds source packet count");
        NS_ABORT_MSG_IF(!indices.empty() && parsed <= indices.back(),
                        "Oracle packet indexes must be strictly increasing");
        indices.push_back(static_cast<uint32_t>(parsed));
    }
    return indices;
}

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

bool
ContainsCsvDelimiter(const std::string& value)
{
    return value.find_first_of(",\r\n\"") != std::string::npos;
}

} // namespace

TypeId
MechanismExperimentController::GetTypeId()
{
    static TypeId tid = TypeId("ns3::MechanismExperimentController")
                            .SetParent<Object>()
                            .SetGroupName("WifiStreaming")
                            .AddConstructor<MechanismExperimentController>();
    return tid;
}

MechanismExperimentController::MechanismExperimentController() = default;

MechanismExperimentController::~MechanismExperimentController() = default;

void
MechanismExperimentController::SetSender(MultipathSender* sender)
{
    NS_ABORT_MSG_IF(m_started, "Cannot change mechanism sender after control starts");
    NS_ABORT_MSG_IF(!sender, "Mechanism experiment requires a sender");
    m_sender = sender;
}

void
MechanismExperimentController::SetAirtimeMeter(Ptr<SecondaryAirtimeMeter> meter)
{
    NS_ABORT_MSG_IF(m_started, "Cannot change mechanism meter after control starts");
    NS_ABORT_MSG_IF(!meter, "Mechanism experiment requires a non-null meter");
    m_meter = meter;
}

void
MechanismExperimentController::SetAction(MechanismT2Action action)
{
    NS_ABORT_MSG_IF(m_started, "Cannot change mechanism action after control starts");
    m_action = action;
}

void
MechanismExperimentController::SetSystematicRepairDivisor(uint32_t divisor)
{
    NS_ABORT_MSG_IF(m_started, "Cannot change repair divisor after control starts");
    NS_ABORT_MSG_IF(divisor == 0, "Systematic repair divisor must be positive");
    m_repairDivisor = divisor;
}

void
MechanismExperimentController::SetOraclePacketOutcomeFile(const std::string& path)
{
    NS_ABORT_MSG_IF(m_started || !m_oraclePlans.empty(),
                    "Oracle packet outcomes may be loaded only once before control");
    NS_ABORT_MSG_IF(path.empty(), "Oracle packet-outcome path cannot be empty");
    std::ifstream input(path);
    NS_ABORT_MSG_IF(!input, "Cannot open oracle packet outcomes " << path);
    std::string line;
    NS_ABORT_MSG_IF(!std::getline(input, line) || line != PACKET_OUTCOME_HEADER,
                    "Oracle packet-outcome header is not canonical");
    std::optional<std::string> sourceRunId;
    while (std::getline(input, line))
    {
        NS_ABORT_MSG_IF(line.empty(), "Oracle packet outcomes contain an empty row");
        const auto fields = Split(line, ',');
        NS_ABORT_MSG_IF(fields.size() != 10,
                        "Oracle packet outcomes must contain exactly 10 columns");
        NS_ABORT_MSG_IF(fields[0].empty() || ContainsCsvDelimiter(fields[0]),
                        "Oracle packet outcomes contain an invalid run ID");
        if (!sourceRunId)
        {
            sourceRunId = fields[0];
        }
        NS_ABORT_MSG_IF(*sourceRunId != fields[0],
                        "Oracle packet outcomes mix multiple source run IDs");
        const uint64_t frameId = ParseUint64(fields[1], "frame ID");
        const uint64_t packetCount64 = ParseUint64(fields[2], "source packet count");
        NS_ABORT_MSG_IF(packetCount64 == 0 ||
                            packetCount64 > std::numeric_limits<uint32_t>::max(),
                        "Oracle source packet count is outside uint32_t");
        OraclePlan plan;
        plan.sourcePacketCount = static_cast<uint32_t>(packetCount64);
        plan.packetIndices = ParsePacketIndices(fields[4], plan.sourcePacketCount);
        NS_ABORT_MSG_IF(!m_oraclePlans.emplace(frameId, std::move(plan)).second,
                        "Oracle packet outcomes contain a duplicate frame ID");
    }
    NS_ABORT_MSG_IF(m_oraclePlans.empty(), "Oracle packet outcomes contain no frames");
    m_oraclePacketOutcomeFile = path;
}

void
MechanismExperimentController::SetOutputFiles(const std::string& runId,
                                              const std::string& snapshotsFile,
                                              const std::string& actionsFile)
{
    NS_ABORT_MSG_IF(m_started || m_snapshots.is_open() || m_actions.is_open(),
                    "Mechanism outputs may be configured only once before control");
    NS_ABORT_MSG_IF(runId.empty() || ContainsCsvDelimiter(runId),
                    "Mechanism run ID must be nonempty and CSV-safe");
    NS_ABORT_MSG_IF(snapshotsFile.empty() || actionsFile.empty() ||
                        snapshotsFile == actionsFile,
                    "Mechanism output paths must be distinct and nonempty");
    m_runId = runId;
    m_snapshots.open(snapshotsFile);
    NS_ABORT_MSG_IF(!m_snapshots, "Cannot open mechanism snapshots " << snapshotsFile);
    m_actions.open(actionsFile);
    NS_ABORT_MSG_IF(!m_actions, "Cannot open mechanism actions " << actionsFile);
    m_snapshots.imbue(std::locale::classic());
    m_actions.imbue(std::locale::classic());
    WriteSnapshotHeader();
    WriteActionHeader();
}

void
MechanismExperimentController::StartControl()
{
    if (m_started)
    {
        return;
    }
    NS_ABORT_MSG_IF(!m_sender, "Mechanism sender is not configured");
    NS_ABORT_MSG_IF(m_runId.empty() || !m_snapshots || !m_actions,
                    "Mechanism outputs are not configured");
    NS_ABORT_MSG_IF(m_action != MechanismT2Action::OBSERVE && !m_meter,
                    "A mechanism action requires the secondary airtime meter");
    NS_ABORT_MSG_IF(m_action == MechanismT2Action::ORACLE_REPAIR &&
                        (m_oraclePacketOutcomeFile.empty() || m_oraclePlans.empty()),
                    "Oracle repair requires paired packet outcomes");
    m_started = true;
}

bool
MechanismExperimentController::IsPrimary(const PredictionSample& sample)
{
    return sample.key.pathId == PRIMARY_PATH_ID &&
           sample.key.copyId == PRIMARY_COPY_ID;
}

bool
MechanismExperimentController::IsSecondary(const PredictionSample& sample)
{
    return sample.key.pathId == SECONDARY_PATH_ID &&
           sample.key.copyId == SECONDARY_COPY_ID;
}

void
MechanismExperimentController::NotifySnapshot(const PredictionSample& sample)
{
    if (sample.sampleOffsetUs != T2_OFFSET_US)
    {
        return;
    }
    StartControl();
    NS_ABORT_MSG_IF(sample.runId != m_runId,
                    "Mechanism snapshot run ID differs from configured output");
    NS_ABORT_MSG_IF(sample.sampleStage != "T2" ||
                        (!IsPrimary(sample) && !IsSecondary(sample)),
                    "Mechanism controller received a noncanonical T2 sample");
    auto& state = m_frames[sample.key.frameId];
    NS_ABORT_MSG_IF(state.processed, "Mechanism frame was already processed");
    if (IsPrimary(sample))
    {
        NS_ABORT_MSG_IF(state.primary || state.secondary,
                        "Mechanism primary T2 sample is duplicate or out of order");
        state.primary = sample;
        return;
    }
    NS_ABORT_MSG_IF(!state.primary || state.secondary,
                    "Mechanism secondary T2 sample is duplicate or out of order");
    const auto& primary = *state.primary;
    NS_ABORT_MSG_IF(primary.key.frameId != sample.key.frameId ||
                        primary.sampleTimeNs != sample.sampleTimeNs ||
                        primary.generationTimeNs != sample.generationTimeNs ||
                        primary.deadlineTimeNs != sample.deadlineTimeNs ||
                        primary.frameSizeBytes != sample.frameSizeBytes ||
                        primary.framePacketCount != sample.framePacketCount ||
                        primary.frameType != sample.frameType,
                    "Mechanism paired T2 samples disagree on immutable metadata");
    state.secondary = sample;
    ProcessPair(sample.key.frameId, state);
}

void
MechanismExperimentController::ProcessPair(uint64_t frameId, FrameState& state)
{
    NS_ABORT_MSG_IF(!state.primary || !state.secondary || state.processed,
                    "Mechanism pair is incomplete or repeated");
    const auto ackDeficit = m_sender->GetUnacknowledgedPacketIndices(state.primary->key);
    NS_ABORT_MSG_IF(!ackDeficit,
                    "Mechanism primary ACK-deficit state is unavailable at T2");
    WriteSnapshot(*state.primary, *ackDeficit);
    WriteSnapshot(*state.secondary, *ackDeficit);

    std::vector<uint32_t> selectedIndices;
    std::string reason;
    const auto descriptor =
        SelectActionDescriptor(*state.primary, selectedIndices, reason);
    double nominalAirtimeUs = 0;
    bool launched = false;
    if (descriptor)
    {
        nominalAirtimeUs = CanonicalSecondaryAirtimeEstimator::EstimateNominalUs(
            descriptor->packetCount,
            descriptor->expectedMacServiceBytes);
        SecondaryAirtimeReservation reservation;
        reservation.frameId = frameId;
        reservation.packetCount = descriptor->packetCount;
        // The mechanism experiment has no admission budget, but retaining the
        // complete nominal reservation makes the passive meter's settlement
        // ledger independently reconcilable.
        reservation.reservedAirtimeUs = nominalAirtimeUs;
        reservation.estimatedAirtimeUs = nominalAirtimeUs;
        reservation.nominalAirtimeUs = nominalAirtimeUs;
        reservation.deadlineTimeNs = descriptor->deadlineTimeNs;
        reservation.expectedPacketIndices.insert(descriptor->packetIndices.begin(),
                                                 descriptor->packetIndices.end());
        m_meter->RegisterLaunchedCopy(std::move(reservation));
        launched = LaunchAction(frameId, selectedIndices, reason);
        if (!launched)
        {
            m_meter->ReleaseReservation(frameId);
            reason += ":sender_rejected";
        }
        else
        {
            ++m_launches;
        }
    }
    WriteAction(*state.primary,
                descriptor.has_value(),
                launched,
                reason,
                descriptor,
                nominalAirtimeUs);
    state.processed = true;
    ++m_pairedFrames;
}

std::optional<DelayedCopyDescriptor>
MechanismExperimentController::SelectActionDescriptor(
    const PredictionSample& primary,
    std::vector<uint32_t>& selectedIndices,
    std::string& reason) const
{
    switch (m_action)
    {
    case MechanismT2Action::OBSERVE:
        reason = "observe_only";
        return std::nullopt;
    case MechanismT2Action::FULL_COPY:
    {
        reason = "unconditional_full_copy_t2";
        const auto descriptor =
            m_sender->GetDelayedSecondaryCopyDescriptor(primary.key.frameId);
        NS_ABORT_MSG_IF(!descriptor, "Full-copy T2 descriptor is unavailable");
        selectedIndices = descriptor->packetIndices;
        return descriptor;
    }
    case MechanismT2Action::ORACLE_REPAIR:
    {
        const auto plan = m_oraclePlans.find(primary.key.frameId);
        NS_ABORT_MSG_IF(plan == m_oraclePlans.end(),
                        "Oracle packet outcomes omit frame " << primary.key.frameId);
        NS_ABORT_MSG_IF(plan->second.sourcePacketCount != primary.framePacketCount,
                        "Oracle source packet count differs from the action frame");
        selectedIndices = plan->second.packetIndices;
        if (selectedIndices.empty())
        {
            reason = "oracle_no_eventual_missing_source_packet";
            return std::nullopt;
        }
        reason = "privileged_eventual_missing_source_repair_t2";
        const auto descriptor = m_sender->GetDelayedSecondaryPacketDescriptor(
            primary.key.frameId,
            selectedIndices);
        NS_ABORT_MSG_IF(!descriptor, "Oracle repair descriptor is unavailable");
        return descriptor;
    }
    case MechanismT2Action::SYSTEMATIC_REPAIR:
    {
        const uint32_t repairCount =
            (primary.framePacketCount + m_repairDivisor - 1) / m_repairDivisor;
        NS_ABORT_MSG_IF(repairCount == 0 || repairCount > 255 ||
                            repairCount > primary.framePacketCount,
                        "Systematic repair count is outside the wire contract");
        reason = "ideal_systematic_repair_t2";
        const auto descriptor =
            m_sender->GetDelayedSecondaryCodedRepairDescriptor(primary.key.frameId,
                                                                repairCount);
        NS_ABORT_MSG_IF(!descriptor, "Systematic repair descriptor is unavailable");
        selectedIndices = descriptor->packetIndices;
        return descriptor;
    }
    }
    NS_ABORT_MSG("Unknown mechanism action");
    return std::nullopt;
}

bool
MechanismExperimentController::LaunchAction(
    uint64_t frameId,
    const std::vector<uint32_t>& selectedIndices,
    const std::string& reason)
{
    switch (m_action)
    {
    case MechanismT2Action::FULL_COPY:
        return m_sender->RequestSecondaryCopy(frameId, reason);
    case MechanismT2Action::ORACLE_REPAIR:
        return m_sender->RequestSecondaryPackets(frameId, selectedIndices, reason);
    case MechanismT2Action::SYSTEMATIC_REPAIR:
        return m_sender->RequestSecondaryCodedRepair(
            frameId,
            static_cast<uint32_t>(selectedIndices.size()),
            reason);
    case MechanismT2Action::OBSERVE:
        break;
    }
    NS_ABORT_MSG("Observe-only mechanism cannot launch an action");
    return false;
}

void
MechanismExperimentController::WriteSnapshot(
    const PredictionSample& sample,
    const std::vector<uint32_t>& ackDeficit)
{
    m_snapshots << CSV_SCHEMA_VERSION << ',' << m_runId << ',' << sample.key.frameId << ','
                << +sample.key.pathId << ',' << +sample.key.copyId << ','
                << sample.sampleTimeNs << ',' << sample.framePacketCount << ',';
    WriteOptional(m_snapshots, sample.framePacketsTxSucceeded);
    m_snapshots << ',';
    WriteOptional(m_snapshots, sample.framePacketsPendingPrimary);
    m_snapshots << ',';
    WriteOptional(m_snapshots, sample.framePacketsCurrentlyQueued);
    m_snapshots << ',';
    WriteOptional(m_snapshots, sample.frameMacServiceBytesCurrentlyQueued);
    m_snapshots << ',';
    WriteOptional(m_snapshots, sample.macQueuePackets);
    m_snapshots << ',';
    WriteOptional(m_snapshots, sample.macQueueServiceBytes);
    m_snapshots << ',';
    WriteOptional(m_snapshots, sample.packetsAheadOfFrame);
    m_snapshots << ',';
    WriteOptional(m_snapshots, sample.macServiceBytesAheadOfFrame);
    m_snapshots << ',' << ackDeficit.size() << ',';
    WriteIndices(m_snapshots, ackDeficit);
    m_snapshots << '\n';
    m_snapshots.flush();
}

void
MechanismExperimentController::WriteAction(
    const PredictionSample& primary,
    bool requested,
    bool launched,
    const std::string& reason,
    const std::optional<DelayedCopyDescriptor>& descriptor,
    double nominalAirtimeUs)
{
    m_actions << CSV_SCHEMA_VERSION << ',' << m_runId << ',' << primary.key.frameId << ','
              << primary.generationTimeNs << ',' << GetActionName() << ',' << requested << ','
              << launched << ',' << reason << ',' << primary.framePacketCount << ',';
    if (descriptor)
    {
        m_actions << descriptor->packetCount;
    }
    m_actions << ',';
    if (descriptor)
    {
        WriteIndices(m_actions, descriptor->packetIndices);
    }
    m_actions << ',';
    if (descriptor)
    {
        m_actions << descriptor->expectedMacServiceBytes;
    }
    m_actions << ',';
    if (descriptor)
    {
        m_actions << std::setprecision(12) << nominalAirtimeUs;
    }
    m_actions << ',' << primary.sampleTimeNs / 1000 << '\n';
    m_actions.flush();
}

void
MechanismExperimentController::WriteSnapshotHeader()
{
    m_snapshots
        << "schema_version,run_id,frame_id,path_id,copy_id,sample_time_ns,"
           "source_packet_count,frame_packets_tx_succeeded,"
           "frame_packets_pending_primary,frame_packets_currently_queued,"
           "frame_mac_service_bytes_currently_queued,mac_queue_packets,"
           "mac_queue_service_bytes,packets_ahead_of_frame,"
           "mac_service_bytes_ahead_of_frame,primary_ack_deficit_count,"
           "primary_ack_deficit_packet_indices\n";
}

void
MechanismExperimentController::WriteActionHeader()
{
    m_actions
        << "schema_version,run_id,frame_id,generation_time_ns,action,requested,"
           "launched,reason,source_packet_count,action_packet_count,"
           "action_packet_indices,expected_mac_service_bytes,"
           "nominal_airtime_us,action_time_us\n";
}

uint64_t
MechanismExperimentController::GetPairedFrameCount() const
{
    return m_pairedFrames;
}

uint64_t
MechanismExperimentController::GetLaunchCount() const
{
    return m_launches;
}

std::string
MechanismExperimentController::GetActionName() const
{
    switch (m_action)
    {
    case MechanismT2Action::OBSERVE:
        return "OBSERVE";
    case MechanismT2Action::FULL_COPY:
        return "FULL_COPY_T2";
    case MechanismT2Action::ORACLE_REPAIR:
        return "ORACLE_EVENTUAL_MISSING_REPAIR_T2";
    case MechanismT2Action::SYSTEMATIC_REPAIR:
        return "IDEAL_SYSTEMATIC_REPAIR_T2";
    }
    return "UNKNOWN";
}

void
MechanismExperimentController::DoDispose()
{
    m_sender = nullptr;
    m_meter = nullptr;
    m_frames.clear();
    Object::DoDispose();
}

} // namespace ns3
