/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "randomized-intervention-controller.h"

#include "streaming-header.h"

#include "ns3/abort.h"
#include "ns3/eht-phy.h"
#include "ns3/log.h"
#include "ns3/wifi-phy.h"
#include "ns3/wifi-tx-vector.h"

#include <cmath>
#include <iomanip>
#include <limits>
#include <locale>

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("RandomizedInterventionController");
NS_OBJECT_ENSURE_REGISTERED(RandomizedInterventionController);

namespace
{

constexpr double COST_SAFETY_FACTOR = 1.25;
constexpr uint64_t NANOS_PER_MICROSECOND = 1000;
constexpr uint32_t WIFI_MAC_OVERHEAD_BYTES = 38;

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

void
WriteOptionalUint64(std::ostream& output, const std::optional<uint64_t>& value)
{
    if (value)
    {
        output << *value;
    }
}

void
WritePacketIndices(std::ostream& output, const DelayedCopyDescriptor* descriptor)
{
    if (!descriptor)
    {
        return;
    }
    for (std::size_t index = 0; index < descriptor->packetIndices.size(); ++index)
    {
        output << (index == 0 ? "" : ";") << descriptor->packetIndices[index];
    }
}

bool
ContainsCsvDelimiter(const std::string& value)
{
    return value.find_first_of(",\r\n\"") != std::string::npos;
}

} // namespace

TypeId
RandomizedInterventionController::GetTypeId()
{
    static TypeId tid = TypeId("ns3::RandomizedInterventionController")
                            .SetParent<Object>()
                            .SetGroupName("WifiStreaming")
                            .AddConstructor<RandomizedInterventionController>();
    return tid;
}

RandomizedInterventionController::RandomizedInterventionController() = default;

RandomizedInterventionController::~RandomizedInterventionController() = default;

void
RandomizedInterventionController::SetSender(MultipathSender* sender)
{
    NS_ABORT_MSG_IF(m_started, "Cannot change randomized sender after control starts");
    NS_ABORT_MSG_IF(!sender, "Randomized intervention requires a sender");
    m_sender = sender;
}

void
RandomizedInterventionController::SetAirtimeMeter(Ptr<SecondaryAirtimeMeter> meter)
{
    NS_ABORT_MSG_IF(m_started, "Cannot change randomized airtime meter after control starts");
    NS_ABORT_MSG_IF(!meter, "Randomized intervention requires an airtime meter");
    if (m_meter && m_meter != meter)
    {
        m_meter->SetSettlementCallback(SecondaryAirtimeMeter::SettlementCallback());
    }
    m_meter = meter;
    m_meter->SetSettlementCallback(
        MakeCallback(&RandomizedInterventionController::NotifySettlement, this));
}

void
RandomizedInterventionController::SetAssignmentParameters(uint64_t salt,
                                                          uint64_t seed,
                                                          uint64_t run,
                                                          double t2Probability,
                                                          double t4Probability)
{
    NS_ABORT_MSG_IF(m_started, "Cannot change randomized assignment after control starts");
    NS_ABORT_MSG_IF(!std::isfinite(t2Probability) || !std::isfinite(t4Probability) ||
                        t2Probability < 0 || t4Probability < 0 ||
                        t4Probability > 1.0 - t2Probability,
                    "Randomized probabilities must be finite, nonnegative, and sum to at most "
                    "one");
    m_salt = salt;
    m_seed = seed;
    m_run = run;
    m_t2Probability = t2Probability;
    m_t4Probability = t4Probability;
    m_assignmentConfigured = true;
}

void
RandomizedInterventionController::SetAssignmentWindow(uint64_t startTimeNs,
                                                      uint64_t stopTimeNs)
{
    NS_ABORT_MSG_IF(m_started, "Cannot change randomized window after control starts");
    NS_ABORT_MSG_IF(startTimeNs >= stopTimeNs,
                    "Randomized assignment window must be nonempty");
    m_windowStartNs = startTimeNs;
    m_windowStopNs = stopTimeNs;
    m_windowConfigured = true;
}

void
RandomizedInterventionController::SetOutputFiles(const std::string& runId,
                                                 const std::string& assignmentsFile,
                                                 const std::string& executionsFile)
{
    NS_ABORT_MSG_IF(m_started || m_assignments.is_open() || m_executions.is_open(),
                    "Randomized output files may be configured only once before control");
    NS_ABORT_MSG_IF(runId.empty() || ContainsCsvDelimiter(runId),
                    "Randomized run ID must be nonempty and CSV-safe");
    NS_ABORT_MSG_IF(assignmentsFile.empty() || executionsFile.empty() ||
                        assignmentsFile == executionsFile,
                    "Randomized assignment and execution paths must be distinct and nonempty");
    m_runId = runId;
    m_assignments.open(assignmentsFile);
    NS_ABORT_MSG_IF(!m_assignments, "Cannot open randomized assignment output " << assignmentsFile);
    m_executions.open(executionsFile);
    NS_ABORT_MSG_IF(!m_executions, "Cannot open randomized execution output " << executionsFile);
    m_assignments.imbue(std::locale::classic());
    m_executions.imbue(std::locale::classic());
    m_assignments << std::setprecision(std::numeric_limits<double>::max_digits10);
    m_executions << std::setprecision(std::numeric_limits<double>::max_digits10);
    WriteAssignmentHeader();
    WriteExecutionHeader();
}

void
RandomizedInterventionController::StartControl()
{
    if (m_started)
    {
        return;
    }
    NS_ABORT_MSG_IF(!m_sender, "Randomized intervention sender is not configured");
    NS_ABORT_MSG_IF(!m_meter, "Randomized intervention airtime meter is not configured");
    NS_ABORT_MSG_IF(!m_assignmentConfigured,
                    "Randomized intervention assignment is not configured");
    NS_ABORT_MSG_IF(!m_windowConfigured,
                    "Randomized intervention window is not configured");
    NS_ABORT_MSG_IF(m_runId.empty() || !m_assignments || !m_executions,
                    "Randomized intervention outputs are not configured");
    m_started = true;
}

std::string_view
RandomizedInterventionController::ArmName(RandomizedExplorationArm arm)
{
    switch (arm)
    {
    case RandomizedExplorationArm::CONTROL:
        return "CONTROL";
    case RandomizedExplorationArm::FULL_COPY_T2:
        return "FULL_COPY_T2";
    case RandomizedExplorationArm::FULL_COPY_T4:
        return "FULL_COPY_T4";
    }
    NS_ABORT_MSG("Unknown randomized exploration arm");
    return "UNKNOWN";
}

std::string_view
RandomizedInterventionController::StageName(uint64_t offsetUs)
{
    if (offsetUs == T2_OFFSET_US)
    {
        return "T2";
    }
    if (offsetUs == T4_OFFSET_US)
    {
        return "T4";
    }
    return "";
}

bool
RandomizedInterventionController::IsPrimary(const PredictionSample& sample)
{
    return sample.key.pathId == PRIMARY_PATH_ID && sample.key.copyId == PRIMARY_COPY_ID;
}

bool
RandomizedInterventionController::IsSecondary(const PredictionSample& sample)
{
    return sample.key.pathId == SECONDARY_PATH_ID && sample.key.copyId == SECONDARY_COPY_ID;
}

std::optional<std::string>
RandomizedInterventionController::FindSampleError(const PredictionSample& sample) const
{
    if (sample.runId != m_runId)
    {
        return "snapshot run ID differs from randomized run ID";
    }
    if (sample.telemetrySchemaVersion != PREDICTION_TELEMETRY_SCHEMA_VERSION)
    {
        return "snapshot telemetry schema is unsupported";
    }
    if (!IsPrimary(sample) && !IsSecondary(sample))
    {
        return "snapshot does not have primary path 1/copy 0 or secondary path 0/copy 1 identity";
    }
    const auto expectedStage = StageName(sample.sampleOffsetUs);
    if (expectedStage.empty() || sample.sampleStage != expectedStage)
    {
        return "snapshot stage disagrees with its randomized offset";
    }
    if (sample.sampleOffsetUs >
        (std::numeric_limits<uint64_t>::max() - sample.generationTimeNs) /
            NANOS_PER_MICROSECOND)
    {
        return "snapshot timestamp calculation overflows";
    }
    const uint64_t expectedTimeNs =
        sample.generationTimeNs + sample.sampleOffsetUs * NANOS_PER_MICROSECOND;
    if (sample.sampleTimeNs != expectedTimeNs)
    {
        return "snapshot time disagrees with generation time and offset";
    }
    if (sample.deadlineTimeNs <= sample.sampleTimeNs)
    {
        return "snapshot is at or after its deadline";
    }
    const uint64_t slackNs = sample.deadlineTimeNs - sample.sampleTimeNs;
    if (slackNs % NANOS_PER_MICROSECOND != 0 ||
        sample.deadlineSlackUs != slackNs / NANOS_PER_MICROSECOND)
    {
        return "snapshot deadline slack is inconsistent";
    }
    if (sample.frameAgeUs != sample.sampleOffsetUs)
    {
        return "snapshot frame age differs from its offset";
    }
    if (sample.frameSizeBytes == 0 || sample.framePacketCount == 0)
    {
        return "snapshot frame metadata is empty";
    }
    if (sample.packetsSubmitted > sample.framePacketCount ||
        sample.packetsRemainingToSubmit > sample.framePacketCount ||
        static_cast<uint64_t>(sample.packetsSubmitted) +
                sample.packetsRemainingToSubmit !=
            sample.framePacketCount)
    {
        return "snapshot application packet progress does not conserve the plan";
    }
    if (sample.senderMacComplete &&
        (sample.packetsSubmitted != sample.framePacketCount ||
         sample.packetsRemainingToSubmit != 0))
    {
        return "MAC-complete snapshot has unsubmitted application packets";
    }
    if (sample.actionable == sample.senderMacComplete)
    {
        return "snapshot actionability disagrees with pre-deadline MAC completion";
    }
    if (sample.latestFeatureEventTimeNs &&
        *sample.latestFeatureEventTimeNs > sample.sampleTimeNs)
    {
        return "snapshot feature watermark is in the future";
    }
    if (!sample.latestFeatureEventTimeNs && sample.latestFeatureEventSequence != 0)
    {
        return "snapshot feature watermark sequence has no timestamp";
    }
    return std::nullopt;
}

std::optional<std::string>
RandomizedInterventionController::FindPairError(const PredictionSample& primary,
                                                const PredictionSample& secondary)
{
    if (!IsPrimary(primary) || !IsSecondary(secondary))
    {
        return "paired snapshots have reversed or invalid identities";
    }
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
        return "paired snapshots disagree on immutable frame metadata";
    }
    return std::nullopt;
}

std::optional<std::string>
RandomizedInterventionController::FindCrossStageError(const PredictionSample& t2,
                                                      const PredictionSample& t4)
{
    if (t2.runId != t4.runId ||
        t2.telemetrySchemaVersion != t4.telemetrySchemaVersion ||
        t2.key.frameId != t4.key.frameId || t2.key.pathId != t4.key.pathId ||
        t2.key.copyId != t4.key.copyId || t2.generationTimeNs != t4.generationTimeNs ||
        t2.deadlineTimeNs != t4.deadlineTimeNs ||
        t2.frameSizeBytes != t4.frameSizeBytes ||
        t2.framePacketCount != t4.framePacketCount || t2.frameType != t4.frameType)
    {
        return "T4 snapshot changed immutable T2 frame metadata";
    }
    return std::nullopt;
}

std::optional<std::string>
RandomizedInterventionController::FindTransitionError(const FrameState& state,
                                                      const PredictionSample& sample)
{
    if (sample.sampleOffsetUs == T4_OFFSET_US && !state.t2.completed)
    {
        return "T4 snapshot arrived before the paired T2 assignment";
    }
    const StagePair& pair =
        sample.sampleOffsetUs == T2_OFFSET_US ? state.t2 : state.t4;
    if (pair.completed)
    {
        return "duplicate snapshot arrived for a completed pair";
    }
    if (IsPrimary(sample))
    {
        if (pair.primary)
        {
            return "duplicate primary snapshot arrived before pair completion";
        }
        if (pair.secondary)
        {
            return "primary snapshot arrived after a buffered secondary snapshot";
        }
        return std::nullopt;
    }
    if (!pair.primary)
    {
        return "secondary snapshot arrived before its primary snapshot";
    }
    if (pair.secondary)
    {
        return "duplicate secondary snapshot arrived before pair completion";
    }
    return std::nullopt;
}

std::optional<std::string>
RandomizedInterventionController::FindDescriptorError(
    const PredictionSample& sample,
    const DelayedCopyDescriptor& descriptor)
{
    if (descriptor.frameId != sample.key.frameId ||
        descriptor.framePacketCount != sample.framePacketCount ||
        descriptor.deadlineTimeNs != sample.deadlineTimeNs)
    {
        return "delayed descriptor disagrees with immutable frame metadata";
    }
    if (descriptor.packetCount == 0 ||
        descriptor.packetCount != descriptor.framePacketCount ||
        descriptor.packetIndices.size() != descriptor.packetCount)
    {
        return "delayed descriptor is not a full frame copy";
    }
    if (descriptor.expectedMacServiceBytes == 0)
    {
        return "delayed descriptor lacks MAC service bytes";
    }
    for (uint32_t index = 0; index < descriptor.packetCount; ++index)
    {
        if (descriptor.packetIndices[index] != index)
        {
            return "delayed descriptor packet order is not canonical full-copy order";
        }
    }
    return std::nullopt;
}

std::optional<std::string>
RandomizedInterventionController::FindDescriptorMismatch(
    const DelayedCopyDescriptor& expected,
    const DelayedCopyDescriptor& actual)
{
    if (expected.frameId != actual.frameId ||
        expected.framePacketCount != actual.framePacketCount ||
        expected.packetCount != actual.packetCount ||
        expected.packetIndices != actual.packetIndices ||
        expected.expectedMacServiceBytes != actual.expectedMacServiceBytes ||
        expected.deadlineTimeNs != actual.deadlineTimeNs)
    {
        return "delayed descriptor changed between assignment and execution";
    }
    return std::nullopt;
}

std::optional<std::string>
RandomizedInterventionController::FindUntreatedSecondaryError(
    const PredictionSample& sample)
{
    if (!IsSecondary(sample))
    {
        return "untreated-copy validation requires the secondary snapshot";
    }
    if (sample.packetsSubmitted != 0 ||
        sample.applicationSocketPacketBytesSubmitted != 0 ||
        sample.packetsRemainingToSubmit != sample.framePacketCount ||
        sample.senderMacComplete || !sample.actionable)
    {
        return "hypothetical secondary copy contains application progress";
    }
    const auto nonzero = [](const auto& value) { return value && *value != 0; };
    if (nonzero(sample.framePacketsMacEnqueued) ||
        nonzero(sample.framePacketsMacDequeued) ||
        nonzero(sample.framePacketsTxSucceeded) ||
        nonzero(sample.frameMpduAttemptFailures) ||
        nonzero(sample.framePacketsTerminallyDropped) ||
        nonzero(sample.framePacketsCurrentlyQueued) ||
        nonzero(sample.frameMacServiceBytesCurrentlyQueued))
    {
        return "hypothetical secondary copy contains MAC progress";
    }
    return std::nullopt;
}

void
RandomizedInterventionController::NotifySnapshot(const PredictionSample& sample)
{
    if (sample.sampleOffsetUs != T2_OFFSET_US && sample.sampleOffsetUs != T4_OFFSET_US)
    {
        return;
    }
    StartControl();
    const auto sampleError = FindSampleError(sample);
    NS_ABORT_MSG_IF(sampleError.has_value(), *sampleError);

    auto& frame = m_frames[sample.key.frameId];
    const auto transitionError = FindTransitionError(frame, sample);
    NS_ABORT_MSG_IF(transitionError.has_value(), *transitionError);
    StagePair& pair = sample.sampleOffsetUs == T2_OFFSET_US ? frame.t2 : frame.t4;
    if (IsPrimary(sample))
    {
        pair.primary = sample;
        return;
    }

    const auto pairError = FindPairError(*pair.primary, sample);
    NS_ABORT_MSG_IF(pairError.has_value(), *pairError);
    if (sample.sampleOffsetUs == T2_OFFSET_US ||
        !m_launchedFrames.contains(sample.key.frameId))
    {
        const auto untreatedError = FindUntreatedSecondaryError(sample);
        NS_ABORT_MSG_IF(untreatedError.has_value(), *untreatedError);
    }
    if (sample.sampleOffsetUs == T4_OFFSET_US)
    {
        const auto primaryError = FindCrossStageError(*frame.t2.primary, *pair.primary);
        const auto secondaryError = FindCrossStageError(*frame.t2.secondary, sample);
        NS_ABORT_MSG_IF(primaryError.has_value(), *primaryError);
        NS_ABORT_MSG_IF(secondaryError.has_value(), *secondaryError);
    }
    pair.secondary = sample;
    pair.completed = true;
    ProcessCompletedPair(frame, sample.sampleOffsetUs);
}

void
RandomizedInterventionController::ProcessCompletedPair(FrameState& frame, uint64_t offsetUs)
{
    if (offsetUs == T2_OFFSET_US)
    {
        ProcessT2(frame);
        return;
    }
    ProcessT4(frame);
}

void
RandomizedInterventionController::ProcessT2(FrameState& frame)
{
    NS_ABORT_MSG_IF(!frame.t2.completed || !frame.t2.primary || !frame.t2.secondary,
                    "Randomized T2 processing requires a complete pair");
    NS_ABORT_MSG_IF(frame.assignment, "Randomized frame was assigned more than once");
    const auto& primary = *frame.t2.primary;
    frame.assignment = RandomizedFrameAssignment::Assign(m_salt,
                                                         m_seed,
                                                         m_run,
                                                         primary.key.frameId,
                                                         m_t2Probability,
                                                         m_t4Probability);
    ++m_assignmentCount;
    switch (frame.assignment->arm)
    {
    case RandomizedExplorationArm::FULL_COPY_T2:
        ++m_t2ArmCount;
        break;
    case RandomizedExplorationArm::FULL_COPY_T4:
        ++m_t4ArmCount;
        break;
    case RandomizedExplorationArm::CONTROL:
        ++m_controlArmCount;
        break;
    }

    frame.descriptor = m_sender->GetDelayedSecondaryCopyDescriptor(primary.key.frameId);
    if (frame.descriptor)
    {
        const auto descriptorError = FindDescriptorError(primary, *frame.descriptor);
        NS_ABORT_MSG_IF(descriptorError.has_value(), *descriptorError);
        frame.nominalAirtimeUs = EstimateNominalAirtimeUs(
            frame.descriptor->packetCount,
            frame.descriptor->expectedMacServiceBytes);
        frame.estimatedAirtimeUs = COST_SAFETY_FACTOR * frame.nominalAirtimeUs;
    }

    NS_ABORT_MSG_IF(primary.generationTimeNs >
                        std::numeric_limits<uint64_t>::max() -
                            T4_OFFSET_US * NANOS_PER_MICROSECOND,
                    "Prospective randomized T4 timestamp overflows");
    const uint64_t prospectiveT4TimeNs =
        primary.generationTimeNs + T4_OFFSET_US * NANOS_PER_MICROSECOND;
    if (primary.sampleTimeNs < m_windowStartNs || prospectiveT4TimeNs >= m_windowStopNs)
    {
        frame.eligibilityReason = "outside_assignment_window";
    }
    else if (!primary.actionable)
    {
        frame.eligibilityReason = "primary_not_actionable_t2";
    }
    else if (!frame.descriptor)
    {
        frame.eligibilityReason = "delayed_copy_unavailable_t2";
    }
    else
    {
        frame.eligibleT2 = true;
        frame.eligibilityReason = "eligible";
        ++m_eligibleT2Count;
    }
    WriteAssignment(frame);

    if (!frame.eligibleT2)
    {
        WriteExecution(frame,
                       frame.t2,
                       frame.descriptor.has_value(),
                       primary.actionable,
                       false,
                       false,
                       false,
                       "not_exposed_ineligible_t2");
        return;
    }
    if (frame.assignment->arm == RandomizedExplorationArm::CONTROL)
    {
        WriteExecution(frame,
                       frame.t2,
                       true,
                       primary.actionable,
                       false,
                       false,
                       false,
                       "control_no_launch");
        return;
    }
    if (frame.assignment->arm == RandomizedExplorationArm::FULL_COPY_T2)
    {
        AttemptLaunch(frame, frame.t2, "T2", *frame.descriptor);
    }
}

void
RandomizedInterventionController::ProcessT4(FrameState& frame)
{
    NS_ABORT_MSG_IF(!frame.t4.completed || !frame.t4.primary || !frame.t4.secondary,
                    "Randomized T4 processing requires a complete pair");
    if (frame.executionLogged)
    {
        return;
    }
    NS_ABORT_MSG_IF(!frame.assignment || !frame.eligibleT2 ||
                        frame.assignment->arm != RandomizedExplorationArm::FULL_COPY_T4,
                    "Only an eligible FULL_COPY_T4 assignment may remain unresolved at T4");
    const auto& primary = *frame.t4.primary;
    auto descriptor = m_sender->GetDelayedSecondaryCopyDescriptor(primary.key.frameId);
    if (descriptor)
    {
        const auto descriptorError = FindDescriptorError(primary, *descriptor);
        const auto descriptorMismatch = FindDescriptorMismatch(*frame.descriptor, *descriptor);
        NS_ABORT_MSG_IF(descriptorError.has_value(), *descriptorError);
        NS_ABORT_MSG_IF(descriptorMismatch.has_value(), *descriptorMismatch);
    }
    if (!primary.actionable)
    {
        WriteExecution(frame,
                       frame.t4,
                       descriptor.has_value(),
                       false,
                       false,
                       false,
                       false,
                       "primary_not_actionable_t4");
        return;
    }
    if (!descriptor)
    {
        WriteExecution(frame,
                       frame.t4,
                       false,
                       true,
                       false,
                       false,
                       true,
                       "assigned_t4_not_launched");
        return;
    }
    AttemptLaunch(frame, frame.t4, "T4", *descriptor);
}

void
RandomizedInterventionController::AttemptLaunch(FrameState& frame,
                                                const StagePair& pair,
                                                std::string_view stage,
                                                const DelayedCopyDescriptor& descriptor)
{
    NS_ABORT_MSG_IF(frame.executionLogged, "Randomized launch retried after final resolution");
    NS_ABORT_MSG_IF(!pair.completed || !pair.primary || !pair.secondary,
                    "Randomized launch requires a complete paired snapshot");
    NS_ABORT_MSG_IF(stage != "T2" && stage != "T4", "Unknown randomized launch stage");
    ++m_launchAttemptCount;
    const std::string reason =
        stage == "T2" ? "randomized full copy assigned at T2"
                      : "randomized full copy assigned at T4";
    const bool launched = m_sender->RequestSecondaryCopy(pair.primary->key.frameId, reason);
    if (!launched)
    {
        WriteExecution(frame,
                       pair,
                       true,
                       pair.primary->actionable,
                       true,
                       false,
                       true,
                       stage == "T2" ? "launch_rejected_t2" : "launch_rejected_t4");
        return;
    }

    m_meter->RegisterLaunchedCopy(BuildReservation(frame, descriptor));
    const bool inserted = m_launchedFrames.insert(pair.primary->key.frameId).second;
    NS_ABORT_MSG_IF(!inserted, "Randomized frame launched more than once");
    ++m_launchCount;
    WriteExecution(frame,
                   pair,
                   true,
                   pair.primary->actionable,
                   true,
                   true,
                   false,
                   stage == "T2" ? "launched_t2" : "launched_t4");
}

SecondaryAirtimeReservation
RandomizedInterventionController::BuildReservation(
    const FrameState& frame,
    const DelayedCopyDescriptor& descriptor) const
{
    NS_ABORT_MSG_IF(!(frame.nominalAirtimeUs > 0) || !(frame.estimatedAirtimeUs > 0),
                    "Randomized launch lacks positive cost evidence");
    SecondaryAirtimeReservation reservation;
    reservation.frameId = descriptor.frameId;
    reservation.packetCount = descriptor.packetCount;
    reservation.reservedAirtimeUs = frame.estimatedAirtimeUs;
    reservation.estimatedAirtimeUs = frame.estimatedAirtimeUs;
    reservation.nominalAirtimeUs = frame.nominalAirtimeUs;
    reservation.deadlineTimeNs = descriptor.deadlineTimeNs;
    reservation.expectedPacketIndices.insert(descriptor.packetIndices.begin(),
                                             descriptor.packetIndices.end());
    return reservation;
}

double
RandomizedInterventionController::EstimateNominalAirtimeUs(
    uint32_t packetCount,
    uint64_t expectedMacServiceBytes)
{
    NS_ABORT_MSG_IF(packetCount == 0, "Randomized airtime estimate requires packets");
    NS_ABORT_MSG_IF(expectedMacServiceBytes == 0,
                    "Randomized airtime estimate requires MAC service bytes");
    const auto txVector = MakeEstimatorTxVector();
    const uint64_t rateBps =
        EhtPhy::GetEhtMcs5().GetDataRate(MHz_u{20}, NanoSeconds(800), 1);
    NS_ABORT_MSG_IF(rateBps == 0, "Randomized EHT MCS5 estimate resolved to zero rate");
    const double preambleUs =
        WifiPhy::CalculatePhyPreambleAndHeaderDuration(txVector).GetSeconds() * 1e6;
    const double macBytes =
        static_cast<double>(expectedMacServiceBytes) +
        static_cast<double>(WIFI_MAC_OVERHEAD_BYTES) * packetCount;
    const double payloadUs = 8.0 * macBytes / static_cast<double>(rateBps) * 1e6;
    return preambleUs + payloadUs;
}

double
RandomizedInterventionController::EstimateFullCopyAirtimeUs(
    uint32_t packetCount,
    uint64_t expectedMacServiceBytes) const
{
    return COST_SAFETY_FACTOR *
           EstimateNominalAirtimeUs(packetCount, expectedMacServiceBytes);
}

std::string_view
RandomizedInterventionController::GetCostEstimatorId()
{
    return "eht_mcs5_20mhz_gi800_nss1_one_ppdu_safety125_v1";
}

void
RandomizedInterventionController::WriteAssignment(const FrameState& frame)
{
    NS_ABORT_MSG_IF(!frame.assignment || !frame.t2.primary || !frame.t2.secondary,
                    "Randomized assignment row lacks causal state");
    const auto& primary = *frame.t2.primary;
    const auto& secondary = *frame.t2.secondary;
    const auto* descriptor = frame.descriptor ? &*frame.descriptor : nullptr;
    const uint64_t prospectiveT4TimeNs =
        primary.generationTimeNs + T4_OFFSET_US * NANOS_PER_MICROSECOND;
    m_assignments << CSV_SCHEMA_VERSION << ',' << m_runId << ',' << primary.key.frameId << ','
                  << frame.eligibleT2 << ',' << frame.eligibilityReason << ','
                  << ArmName(frame.assignment->arm) << ',' << m_seed << ',' << m_run << ','
                  << m_salt << ',' << RandomizedFrameAssignment::GetAlgorithmId() << ','
                  << frame.assignment->rawDraw << ',' << frame.assignment->unitDraw << ','
                  << m_t2Probability << ',' << m_t4Probability << ','
                  << (1.0 - m_t2Probability) - m_t4Probability << ','
                  << frame.assignment->armProbability << ',' << primary.sampleTimeNs << ','
                  << secondary.sampleTimeNs << ',';
    WriteOptionalUint64(m_assignments, primary.latestFeatureEventTimeNs);
    m_assignments << ',' << primary.latestFeatureEventSequence << ',';
    WriteOptionalUint64(m_assignments, secondary.latestFeatureEventTimeNs);
    m_assignments << ',' << secondary.latestFeatureEventSequence << ','
                  << primary.generationTimeNs << ',' << primary.deadlineTimeNs << ','
                  << prospectiveT4TimeNs << ',' << primary.frameSizeBytes << ','
                  << primary.framePacketCount << ',' << FrameTypeToString(primary.frameType) << ','
                  << static_cast<bool>(descriptor) << ','
                  << (descriptor ? descriptor->packetCount : 0) << ',';
    WritePacketIndices(m_assignments, descriptor);
    m_assignments << ',' << (descriptor ? descriptor->expectedMacServiceBytes : 0) << ','
                  << GetCostEstimatorId() << ',' << COST_SAFETY_FACTOR << ','
                  << frame.nominalAirtimeUs << ',' << frame.estimatedAirtimeUs << '\n';
    m_assignments.flush();
}

void
RandomizedInterventionController::WriteExecution(
    FrameState& frame,
    const StagePair& pair,
    bool descriptorAvailableAtExecution,
    bool primaryActionable,
    bool attempted,
    bool launched,
    bool noncompliance,
    std::string_view status)
{
    NS_ABORT_MSG_IF(frame.executionLogged || !frame.assignment || !pair.primary ||
                        !pair.secondary || !pair.completed,
                    "Randomized execution row is duplicate or incomplete");
    const auto& primary = *pair.primary;
    const auto& secondary = *pair.secondary;
    const auto* descriptor = frame.descriptor ? &*frame.descriptor : nullptr;
    m_executions << CSV_SCHEMA_VERSION << ',' << m_runId << ',' << primary.key.frameId << ','
                 << frame.eligibleT2 << ',' << frame.eligibilityReason << ','
                 << ArmName(frame.assignment->arm) << ',' << m_seed << ',' << m_run << ','
                 << m_salt << ',' << RandomizedFrameAssignment::GetAlgorithmId() << ','
                 << frame.assignment->rawDraw << ',' << frame.assignment->unitDraw << ','
                 << m_t2Probability << ',' << m_t4Probability << ','
                 << (1.0 - m_t2Probability) - m_t4Probability << ','
                 << frame.assignment->armProbability << ',' << primary.sampleStage << ','
                 << primary.sampleTimeNs << ',' << secondary.sampleTimeNs << ',';
    WriteOptionalUint64(m_executions, primary.latestFeatureEventTimeNs);
    m_executions << ',' << primary.latestFeatureEventSequence << ',';
    WriteOptionalUint64(m_executions, secondary.latestFeatureEventTimeNs);
    m_executions << ',' << secondary.latestFeatureEventSequence << ','
                 << primary.generationTimeNs << ',' << primary.deadlineTimeNs << ','
                 << static_cast<bool>(descriptor) << ',' << descriptorAvailableAtExecution << ','
                 << (descriptor ? descriptor->packetCount : 0) << ',';
    WritePacketIndices(m_executions, descriptor);
    m_executions << ',' << (descriptor ? descriptor->expectedMacServiceBytes : 0) << ','
                 << GetCostEstimatorId() << ',' << COST_SAFETY_FACTOR << ','
                 << frame.nominalAirtimeUs << ',' << frame.estimatedAirtimeUs << ','
                 << primaryActionable << ',' << attempted << ',' << launched << ','
                 << noncompliance << ',' << status << '\n';
    m_executions.flush();
    frame.executionLogged = true;
    ++m_executionCount;
    if (noncompliance)
    {
        ++m_noncomplianceCount;
    }
}

void
RandomizedInterventionController::WriteAssignmentHeader()
{
    m_assignments
        << "schema_version,run_id,frame_id,eligible_t2,eligibility_reason,assigned_arm,"
           "assignment_seed,assignment_run,assignment_salt,assignment_algorithm,raw_draw,"
           "unit_draw,t2_probability,t4_probability,control_probability,propensity,"
           "primary_sample_time_ns,secondary_sample_time_ns,"
           "primary_feature_watermark_time_ns,primary_feature_watermark_sequence,"
           "secondary_feature_watermark_time_ns,secondary_feature_watermark_sequence,"
           "generation_time_ns,deadline_time_ns,prospective_t4_time_ns,frame_size_bytes,"
           "frame_packet_count,frame_type,descriptor_available,secondary_packet_count,"
           "secondary_packet_indices,secondary_expected_mac_service_bytes,cost_estimator,"
           "cost_safety_factor,nominal_airtime_us,estimated_airtime_us\n";
    m_assignments.flush();
}

void
RandomizedInterventionController::WriteExecutionHeader()
{
    m_executions
        << "schema_version,run_id,frame_id,eligible_t2,eligibility_reason,assigned_arm,"
           "assignment_seed,assignment_run,assignment_salt,assignment_algorithm,raw_draw,"
           "unit_draw,t2_probability,t4_probability,control_probability,propensity,"
           "execution_stage,primary_sample_time_ns,secondary_sample_time_ns,"
           "primary_feature_watermark_time_ns,primary_feature_watermark_sequence,"
           "secondary_feature_watermark_time_ns,secondary_feature_watermark_sequence,"
           "generation_time_ns,deadline_time_ns,descriptor_available_at_assignment,"
           "descriptor_available_at_execution,secondary_packet_count,secondary_packet_indices,"
           "secondary_expected_mac_service_bytes,cost_estimator,cost_safety_factor,"
           "nominal_airtime_us,estimated_airtime_us,primary_actionable,attempted,launched,"
           "noncompliance,status\n";
    m_executions.flush();
}

void
RandomizedInterventionController::NotifySettlement(uint64_t frameId,
                                                   double releasedUs,
                                                   double measuredUs,
                                                   double nominalUs,
                                                   bool fallback)
{
    (void)releasedUs;
    (void)measuredUs;
    (void)fallback;
    NS_ABORT_MSG_IF(!m_launchedFrames.contains(frameId),
                    "Airtime settlement references an unlaunched randomized frame");
    const bool inserted = m_settledFrames.insert(frameId).second;
    NS_ABORT_MSG_IF(!inserted, "Randomized frame settled more than once");
    const auto frame = m_frames.find(frameId);
    NS_ABORT_MSG_IF(frame == m_frames.end() ||
                        std::abs(frame->second.nominalAirtimeUs - nominalUs) > 1e-9,
                    "Airtime settlement cost differs from randomized launch evidence");
    ++m_settlementCount;
}

uint64_t
RandomizedInterventionController::GetAssignmentCount() const
{
    return m_assignmentCount;
}

uint64_t
RandomizedInterventionController::GetEligibleT2Count() const
{
    return m_eligibleT2Count;
}

uint64_t
RandomizedInterventionController::GetT2ArmCount() const
{
    return m_t2ArmCount;
}

uint64_t
RandomizedInterventionController::GetT4ArmCount() const
{
    return m_t4ArmCount;
}

uint64_t
RandomizedInterventionController::GetControlArmCount() const
{
    return m_controlArmCount;
}

uint64_t
RandomizedInterventionController::GetExecutionCount() const
{
    return m_executionCount;
}

uint64_t
RandomizedInterventionController::GetLaunchAttemptCount() const
{
    return m_launchAttemptCount;
}

uint64_t
RandomizedInterventionController::GetLaunchCount() const
{
    return m_launchCount;
}

uint64_t
RandomizedInterventionController::GetNoncomplianceCount() const
{
    return m_noncomplianceCount;
}

uint64_t
RandomizedInterventionController::GetSettlementCount() const
{
    return m_settlementCount;
}

void
RandomizedInterventionController::DoDispose()
{
    m_sender = nullptr;
    if (m_meter)
    {
        m_meter->SetSettlementCallback(SecondaryAirtimeMeter::SettlementCallback());
    }
    m_meter = nullptr;
    if (m_assignments.is_open())
    {
        m_assignments.close();
    }
    if (m_executions.is_open())
    {
        m_executions.close();
    }
    Object::DoDispose();
}

} // namespace ns3
