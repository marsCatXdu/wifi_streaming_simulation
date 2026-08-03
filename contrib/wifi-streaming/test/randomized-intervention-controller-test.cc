/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "ns3/csma-module.h"
#include "ns3/frame-receiver.h"
#include "ns3/frame-source.h"
#include "ns3/inet-socket-address.h"
#include "ns3/internet-stack-helper.h"
#include "ns3/ipv4-address-helper.h"
#include "ns3/multipath-sender.h"
#include "ns3/randomized-frame-assignment.h"
#include "ns3/randomized-intervention-controller.h"
#include "ns3/redundancy-policy.h"
#include "ns3/secondary-airtime-meter.h"
#include "ns3/simulator.h"
#include "ns3/string.h"
#include "ns3/test.h"
#include "ns3/udp-socket-factory.h"

#include <cstdio>
#include <filesystem>
#include <fstream>
#include <optional>
#include <sstream>
#include <string>
#include <vector>

namespace ns3
{

/**
 * Test-only access to randomized intervention state and pure validators.
 */
class RandomizedInterventionControllerTestAccess
{
  public:
    /**
     * Read one stored assignment.
     *
     * @param controller Controller under test.
     * @param frameId Frame identifier.
     * @return Stored immutable assignment.
     */
    static RandomizedExplorationAssignment GetAssignment(
        Ptr<RandomizedInterventionController> controller,
        uint64_t frameId)
    {
        return *controller->m_frames.at(frameId).assignment;
    }

    /**
     * Return the production secondary-before-primary transition error.
     *
     * @param secondary Secondary snapshot.
     * @return Stable error text.
     */
    static std::string GetSecondaryFirstError(const PredictionSample& secondary)
    {
        RandomizedInterventionController::FrameState state;
        return *RandomizedInterventionController::FindTransitionError(state, secondary);
    }

    /**
     * Return the production duplicate-primary transition error.
     *
     * @param primary Primary snapshot.
     * @return Stable error text.
     */
    static std::string GetDuplicatePrimaryError(const PredictionSample& primary)
    {
        RandomizedInterventionController::FrameState state;
        state.t2.primary = primary;
        return *RandomizedInterventionController::FindTransitionError(state, primary);
    }

    /**
     * Return the production completed-pair duplicate error.
     *
     * @param primary Primary snapshot.
     * @return Stable error text.
     */
    static std::string GetCompletedPairDuplicateError(const PredictionSample& primary)
    {
        RandomizedInterventionController::FrameState state;
        state.t2.completed = true;
        return *RandomizedInterventionController::FindTransitionError(state, primary);
    }

    /**
     * Return the production pair validator result.
     *
     * @param primary Primary snapshot.
     * @param secondary Secondary snapshot.
     * @return Error text, or empty when valid.
     */
    static std::optional<std::string> FindPairError(const PredictionSample& primary,
                                                    const PredictionSample& secondary)
    {
        return RandomizedInterventionController::FindPairError(primary, secondary);
    }

    /**
     * Return the production descriptor validator result.
     *
     * @param sample Primary snapshot.
     * @param descriptor Descriptor to validate.
     * @return Error text, or empty when valid.
     */
    static std::optional<std::string> FindDescriptorError(
        const PredictionSample& sample,
        const DelayedCopyDescriptor& descriptor)
    {
        return RandomizedInterventionController::FindDescriptorError(sample, descriptor);
    }
};

namespace
{

constexpr uint64_t GENERATION_TIME_NS = 10000000;
constexpr uint64_t DEADLINE_TIME_NS = 30000000;
constexpr uint32_t FRAME_SIZE_BYTES = 2501;
constexpr uint32_t FRAME_PACKET_COUNT = 3;

PredictionSample
MakeSample(const std::string& runId,
           uint64_t offsetUs,
           bool primary,
           bool primaryActionable = true)
{
    PredictionSample sample;
    sample.runId = runId;
    sample.key = {0,
                  primary ? RandomizedInterventionController::PRIMARY_PATH_ID
                          : RandomizedInterventionController::SECONDARY_PATH_ID,
                  primary ? RandomizedInterventionController::PRIMARY_COPY_ID
                          : RandomizedInterventionController::SECONDARY_COPY_ID};
    sample.sampleStage =
        offsetUs == RandomizedInterventionController::T2_OFFSET_US ? "T2" : "T4";
    sample.sampleOffsetUs = offsetUs;
    sample.sampleTimeNs = GENERATION_TIME_NS + offsetUs * 1000;
    sample.latestFeatureEventTimeNs = sample.sampleTimeNs - (primary ? 100 : 200);
    sample.latestFeatureEventSequence = primary ? 11 : 22;
    sample.generationTimeNs = GENERATION_TIME_NS;
    sample.deadlineTimeNs = DEADLINE_TIME_NS;
    sample.frameAgeUs = offsetUs;
    sample.deadlineSlackUs = (DEADLINE_TIME_NS - sample.sampleTimeNs) / 1000;
    sample.frameSizeBytes = FRAME_SIZE_BYTES;
    sample.framePacketCount = FRAME_PACKET_COUNT;
    sample.frameType = FrameType::I_FRAME;
    if (primary)
    {
        sample.senderMacComplete = !primaryActionable;
        sample.actionable = primaryActionable;
        sample.packetsSubmitted = FRAME_PACKET_COUNT;
        sample.applicationSocketPacketBytesSubmitted = FRAME_SIZE_BYTES;
        sample.packetsRemainingToSubmit = 0;
    }
    else
    {
        sample.actionable = true;
        sample.packetsRemainingToSubmit = FRAME_PACKET_COUNT;
        sample.framePacketsMacEnqueued = 0;
        sample.framePacketsMacDequeued = 0;
        sample.framePacketsTxSucceeded = 0;
        sample.frameMpduAttemptFailures = 0;
        sample.framePacketsTerminallyDropped = 0;
        sample.framePacketsCurrentlyQueued = 0;
        sample.frameMacServiceBytesCurrentlyQueued = 0;
    }
    return sample;
}

std::vector<std::string>
ReadLines(const std::string& path)
{
    std::ifstream input(path);
    std::vector<std::string> lines;
    std::string line;
    while (std::getline(input, line))
    {
        lines.push_back(line);
    }
    return lines;
}

std::vector<std::string>
SplitCsv(const std::string& row)
{
    std::vector<std::string> fields;
    std::istringstream input(row);
    std::string field;
    while (std::getline(input, field, ','))
    {
        fields.push_back(field);
    }
    return fields;
}

} // namespace

/**
 * Exercise all randomized arms, eligibility, and settlement behavior.
 */
class RandomizedInterventionBehaviorTestCase : public TestCase
{
  public:
    RandomizedInterventionBehaviorTestCase()
        : TestCase("Randomized intervention executes paired T2/T4 assignments exactly once")
    {
    }

  private:
    /** Objects and outputs retained for one isolated simulation. */
    struct Scenario
    {
        Ptr<MultipathSender> sender;                         ///< Delayed-copy sender.
        Ptr<SecondaryAirtimeMeter> meter;                    ///< Reservation meter.
        Ptr<RandomizedInterventionController> controller;    ///< Controller under test.
        std::string runId;                                   ///< Stable test run ID.
        std::string assignmentsFile;                         ///< Assignment CSV path.
        std::string executionsFile;                          ///< Execution CSV path.
    };

    /**
     * Configure a single-frame delayed-copy scenario.
     *
     * @param label Unique output label.
     * @param port Receiver UDP port.
     * @param t2Probability T2 assignment probability.
     * @param t4Probability T4 assignment probability.
     * @param windowStopNs Exclusive assignment-window stop.
     * @return Configured scenario.
     */
    Scenario Configure(const std::string& label,
                       uint16_t port,
                       double t2Probability,
                       double t4Probability,
                       uint64_t windowStopNs)
    {
        NodeContainer nodes;
        nodes.Create(2);
        InternetStackHelper internet;
        internet.Install(nodes);
        CsmaHelper csma;
        csma.SetChannelAttribute("DataRate", StringValue("100Mbps"));
        csma.SetChannelAttribute("Delay", TimeValue(MicroSeconds(10)));
        const auto devices = csma.Install(nodes);
        Ipv4AddressHelper address;
        address.SetBase("10.91.0.0", "255.255.255.0");
        const auto interfaces = address.Assign(devices);

        auto receiver = CreateObject<FrameReceiver>();
        receiver->SetLocal(InetSocketAddress(Ipv4Address::GetAny(), port));
        receiver->SetHoldForDelayedSecondary(true);
        nodes.Get(1)->AddApplication(receiver);
        receiver->SetStartTime(Time());
        receiver->SetStopTime(MilliSeconds(40));

        auto source = CreateObject<SyntheticFrameSource>();
        source->SetFps(30);
        source->SetDuration(MilliSeconds(1));
        source->SetConstantFrameSize(FRAME_SIZE_BYTES);
        source->SetDeadline(20000);

        auto policy = CreateObject<SelectiveDuplicationPolicy>();
        policy->SetPrimaryPath(RandomizedInterventionController::PRIMARY_PATH_ID);
        auto sender = CreateObject<MultipathSender>();
        sender->SetFrameSource(source);
        sender->SetPacketPayloadSize(1000);
        sender->SetExpectedMacServiceOverhead(36);
        sender->SetPolicy(policy);
        sender->SetDelayedSecondaryPath(
            RandomizedInterventionController::SECONDARY_PATH_ID);
        for (uint8_t pathId = 0; pathId < 2; ++pathId)
        {
            auto socket = Socket::CreateSocket(nodes.Get(0), UdpSocketFactory::GetTypeId());
            NS_ABORT_MSG_IF(socket->Bind(InetSocketAddress(interfaces.GetAddress(0), 0)) != 0,
                            "Randomized test sender bind failed");
            NS_ABORT_MSG_IF(
                socket->Connect(InetSocketAddress(interfaces.GetAddress(1), port)) != 0,
                "Randomized test sender connect failed");
            sender->AddPath(pathId, socket, devices.Get(0));
        }
        nodes.Get(0)->AddApplication(sender);
        sender->SetStartTime(MilliSeconds(10));
        sender->SetStopTime(MilliSeconds(40));

        const auto base = std::filesystem::temp_directory_path();
        const std::string assignments =
            (base / ("ns3-randomized-" + label + "-assignments.csv")).string();
        const std::string executions =
            (base / ("ns3-randomized-" + label + "-executions.csv")).string();
        std::remove(assignments.c_str());
        std::remove(executions.c_str());

        auto meter = CreateObject<SecondaryAirtimeMeter>();
        meter->SetQueueMaxDelayMs(1);
        auto controller = CreateObject<RandomizedInterventionController>();
        controller->SetSender(PeekPointer(sender));
        controller->SetAirtimeMeter(meter);
        controller->SetAssignmentParameters(0x123456789abcdef0ULL,
                                            41,
                                            7,
                                            t2Probability,
                                            t4Probability);
        controller->SetAssignmentWindow(0, windowStopNs);
        const std::string runId = "randomized-test-" + label;
        controller->SetOutputFiles(runId, assignments, executions);
        return {sender, meter, controller, runId, assignments, executions};
    }

    /**
     * Schedule a paired stage with an observation between callbacks.
     *
     * @param scenario Scenario to notify.
     * @param offsetUs T2 or T4 offset.
     * @param primaryActionable Primary actionability at this stage.
     * @param launchCounts Destination for launch counts observed after each callback.
     */
    void SchedulePair(const Scenario& scenario,
                      uint64_t offsetUs,
                      bool primaryActionable,
                      std::vector<uint64_t>& launchCounts)
    {
        const Time at = NanoSeconds(GENERATION_TIME_NS + offsetUs * 1000);
        const auto primary = MakeSample(scenario.runId, offsetUs, true, primaryActionable);
        const auto secondary = MakeSample(scenario.runId, offsetUs, false);
        Simulator::Schedule(at,
                            &RandomizedInterventionController::NotifySnapshot,
                            scenario.controller,
                            primary);
        Simulator::Schedule(at, [&scenario, &launchCounts]() {
            launchCounts.push_back(scenario.controller->GetLaunchCount());
        });
        Simulator::Schedule(at,
                            &RandomizedInterventionController::NotifySnapshot,
                            scenario.controller,
                            secondary);
        Simulator::Schedule(at, [&scenario, &launchCounts]() {
            launchCounts.push_back(scenario.controller->GetLaunchCount());
        });
    }

    /**
     * Close controller outputs and return their two data rows.
     *
     * @param scenario Completed scenario.
     * @param rows Destination for assignment and execution data rows.
     */
    void ReadRows(Scenario& scenario, std::pair<std::string, std::string>& rows)
    {
        scenario.controller->Dispose();
        const auto assignments = ReadLines(scenario.assignmentsFile);
        const auto executions = ReadLines(scenario.executionsFile);
        NS_TEST_ASSERT_MSG_EQ(assignments.size(), 2, "Assignment CSV must have one frame row");
        NS_TEST_ASSERT_MSG_EQ(executions.size(), 2, "Execution CSV must have one frame row");
        const std::string assignmentRow = assignments.size() == 2 ? assignments[1] : "";
        const std::string executionRow = executions.size() == 2 ? executions[1] : "";
        std::remove(scenario.assignmentsFile.c_str());
        std::remove(scenario.executionsFile.c_str());
        rows = {assignmentRow, executionRow};
    }

    /** Exercise a probability-one T2 assignment and fallback settlement. */
    void TestT2Arm()
    {
        auto scenario = Configure("t2", 9191, 1.0, 0.0, 25000000);
        std::vector<uint64_t> launchCounts;
        SchedulePair(scenario,
                     RandomizedInterventionController::T2_OFFSET_US,
                     true,
                     launchCounts);
        SchedulePair(scenario,
                     RandomizedInterventionController::T4_OFFSET_US,
                     true,
                     launchCounts);
        Simulator::Stop(MilliSeconds(33));
        Simulator::Run();
        NS_TEST_ASSERT_MSG_EQ(launchCounts.size(), 4, "T2/T4 callback probes are missing");
        if (launchCounts.size() == 4)
        {
            NS_TEST_ASSERT_MSG_EQ(launchCounts[0], 0, "T2 launched before secondary snapshot");
            NS_TEST_ASSERT_MSG_EQ(launchCounts[1], 1, "T2 did not launch after its pair");
            NS_TEST_ASSERT_MSG_EQ(launchCounts[2], 1, "T4 primary caused a retry");
            NS_TEST_ASSERT_MSG_EQ(launchCounts[3], 1, "T4 secondary caused a retry");
        }
        NS_TEST_ASSERT_MSG_EQ(scenario.controller->GetAssignmentCount(),
                              1,
                              "T2 frame was not assigned exactly once");
        NS_TEST_ASSERT_MSG_EQ(scenario.controller->GetT2ArmCount(), 1, "Wrong T2 arm count");
        NS_TEST_ASSERT_MSG_EQ(scenario.controller->GetLaunchAttemptCount(),
                              1,
                              "T2 assignment was attempted more than once");
        NS_TEST_ASSERT_MSG_EQ(scenario.controller->GetLaunchCount(), 1, "T2 launch failed");
        NS_TEST_ASSERT_MSG_EQ(scenario.controller->GetSettlementCount(),
                              1,
                              "T2 launch did not settle exactly once");
        NS_TEST_ASSERT_MSG_EQ(scenario.meter->GetForcedReservationSettlements(),
                              1,
                              "T2 fallback settlement was not recorded");
        const auto actual =
            RandomizedInterventionControllerTestAccess::GetAssignment(scenario.controller, 0);
        const auto expected = RandomizedFrameAssignment::Assign(
            0x123456789abcdef0ULL,
            41,
            7,
            0,
            1.0,
            0.0);
        NS_TEST_ASSERT_MSG_EQ(actual.rawDraw, expected.rawDraw, "Stored raw draw changed");
        NS_TEST_ASSERT_MSG_EQ(actual.unitDraw, expected.unitDraw, "Stored unit draw changed");
        std::pair<std::string, std::string> rows;
        ReadRows(scenario, rows);
        const auto assignment = SplitCsv(rows.first);
        const auto execution = SplitCsv(rows.second);
        NS_TEST_ASSERT_MSG_EQ(assignment.size(), 36, "Assignment CSV field count changed");
        if (assignment.size() != 36)
        {
            Simulator::Destroy();
            return;
        }
        NS_TEST_ASSERT_MSG_EQ(
            assignment[32],
            "eht_mcs5_20mhz_gi800_nss1_one_ppdu_safety125_v1",
            "Randomized cost-estimator provenance changed");
        NS_TEST_ASSERT_MSG_EQ(execution.back(), "launched_t2", "Wrong T2 execution status");
        Simulator::Destroy();
    }

    /** Exercise a probability-one T4 assignment. */
    void TestT4Arm()
    {
        auto scenario = Configure("t4", 9192, 0.0, 1.0, 25000000);
        std::vector<uint64_t> launchCounts;
        SchedulePair(scenario,
                     RandomizedInterventionController::T2_OFFSET_US,
                     true,
                     launchCounts);
        SchedulePair(scenario,
                     RandomizedInterventionController::T4_OFFSET_US,
                     true,
                     launchCounts);
        Simulator::Stop(MilliSeconds(33));
        Simulator::Run();
        NS_TEST_ASSERT_MSG_EQ(launchCounts.size(), 4, "T4 callback probes are missing");
        if (launchCounts.size() == 4)
        {
            NS_TEST_ASSERT_MSG_EQ(launchCounts[0], 0, "T2 primary launched T4 arm");
            NS_TEST_ASSERT_MSG_EQ(launchCounts[1], 0, "T2 pair launched T4 arm");
            NS_TEST_ASSERT_MSG_EQ(launchCounts[2], 0, "T4 launched before secondary snapshot");
            NS_TEST_ASSERT_MSG_EQ(launchCounts[3], 1, "T4 did not launch after its pair");
        }
        NS_TEST_ASSERT_MSG_EQ(scenario.controller->GetT4ArmCount(), 1, "Wrong T4 arm count");
        NS_TEST_ASSERT_MSG_EQ(scenario.controller->GetLaunchAttemptCount(),
                              1,
                              "T4 assignment was attempted more than once");
        NS_TEST_ASSERT_MSG_EQ(scenario.controller->GetSettlementCount(),
                              1,
                              "T4 launch did not settle exactly once");
        std::pair<std::string, std::string> rows;
        ReadRows(scenario, rows);
        const auto execution = SplitCsv(rows.second);
        NS_TEST_ASSERT_MSG_EQ(execution.back(), "launched_t4", "Wrong T4 execution status");
        Simulator::Destroy();
    }

    /** Exercise control behavior without a launch. */
    void TestControlArm()
    {
        auto scenario = Configure("control", 9193, 0.0, 0.0, 25000000);
        std::vector<uint64_t> launchCounts;
        SchedulePair(scenario,
                     RandomizedInterventionController::T2_OFFSET_US,
                     true,
                     launchCounts);
        SchedulePair(scenario,
                     RandomizedInterventionController::T4_OFFSET_US,
                     true,
                     launchCounts);
        Simulator::Stop(MilliSeconds(16));
        Simulator::Run();
        NS_TEST_ASSERT_MSG_EQ(scenario.controller->GetControlArmCount(),
                              1,
                              "Wrong control arm count");
        NS_TEST_ASSERT_MSG_EQ(scenario.controller->GetLaunchAttemptCount(),
                              0,
                              "Control attempted a launch");
        NS_TEST_ASSERT_MSG_EQ(scenario.controller->GetLaunchCount(), 0, "Control launched");
        std::pair<std::string, std::string> rows;
        ReadRows(scenario, rows);
        const auto execution = SplitCsv(rows.second);
        NS_TEST_ASSERT_MSG_EQ(execution.back(),
                              "control_no_launch",
                              "Wrong control execution status");
        Simulator::Destroy();
    }

    /** Exercise protocol-defined T4 non-exposure after primary completion. */
    void TestT4NoLongerActionable()
    {
        auto scenario = Configure("t4-complete", 9194, 0.0, 1.0, 25000000);
        std::vector<uint64_t> launchCounts;
        SchedulePair(scenario,
                     RandomizedInterventionController::T2_OFFSET_US,
                     true,
                     launchCounts);
        SchedulePair(scenario,
                     RandomizedInterventionController::T4_OFFSET_US,
                     false,
                     launchCounts);
        Simulator::Stop(MilliSeconds(16));
        Simulator::Run();
        NS_TEST_ASSERT_MSG_EQ(scenario.controller->GetLaunchAttemptCount(),
                              0,
                              "Nonactionable T4 attempted a launch");
        NS_TEST_ASSERT_MSG_EQ(scenario.controller->GetNoncomplianceCount(),
                              0,
                              "Protocol-defined T4 non-exposure counted as noncompliance");
        std::pair<std::string, std::string> rows;
        ReadRows(scenario, rows);
        const auto execution = SplitCsv(rows.second);
        NS_TEST_ASSERT_MSG_EQ(execution.back(),
                              "primary_not_actionable_t4",
                              "Wrong T4 non-exposure status");
        Simulator::Destroy();
    }

    /** Exercise the common window guard before interpreting the assigned arm. */
    void TestAssignmentWindow()
    {
        auto scenario = Configure("window", 9195, 1.0, 0.0, 13000000);
        std::vector<uint64_t> launchCounts;
        SchedulePair(scenario,
                     RandomizedInterventionController::T2_OFFSET_US,
                     true,
                     launchCounts);
        Simulator::Stop(MilliSeconds(13));
        Simulator::Run();
        NS_TEST_ASSERT_MSG_EQ(scenario.controller->GetAssignmentCount(),
                              1,
                              "Out-of-window frame was not logged");
        NS_TEST_ASSERT_MSG_EQ(scenario.controller->GetEligibleT2Count(),
                              0,
                              "Prospective T4 at stop passed a half-open window");
        NS_TEST_ASSERT_MSG_EQ(scenario.controller->GetLaunchAttemptCount(),
                              0,
                              "Out-of-window assignment attempted a launch");
        std::pair<std::string, std::string> rows;
        ReadRows(scenario, rows);
        const auto assignment = SplitCsv(rows.first);
        const auto execution = SplitCsv(rows.second);
        NS_TEST_ASSERT_MSG_EQ(assignment[4],
                              "outside_assignment_window",
                              "Wrong assignment-window reason");
        NS_TEST_ASSERT_MSG_EQ(execution.back(),
                              "not_exposed_ineligible_t2",
                              "Wrong ineligible execution status");
        Simulator::Destroy();
    }

    void DoRun() override
    {
        TestT2Arm();
        TestT4Arm();
        TestControlArm();
        TestT4NoLongerActionable();
        TestAssignmentWindow();
    }
};

/**
 * Exercise production ordering, pair, and descriptor rejection predicates.
 */
class RandomizedInterventionValidationTestCase : public TestCase
{
  public:
    RandomizedInterventionValidationTestCase()
        : TestCase("Randomized intervention rejects malformed and duplicate pairs")
    {
    }

  private:
    void DoRun() override
    {
        const std::string runId = "validation";
        const auto primary = MakeSample(runId,
                                        RandomizedInterventionController::T2_OFFSET_US,
                                        true);
        const auto secondary = MakeSample(runId,
                                          RandomizedInterventionController::T2_OFFSET_US,
                                          false);
        NS_TEST_ASSERT_MSG_EQ(
            RandomizedInterventionControllerTestAccess::GetSecondaryFirstError(secondary),
            "secondary snapshot arrived before its primary snapshot",
            "Secondary-first guard changed");
        NS_TEST_ASSERT_MSG_EQ(
            RandomizedInterventionControllerTestAccess::GetDuplicatePrimaryError(primary),
            "duplicate primary snapshot arrived before pair completion",
            "Duplicate-primary guard changed");
        NS_TEST_ASSERT_MSG_EQ(
            RandomizedInterventionControllerTestAccess::GetCompletedPairDuplicateError(primary),
            "duplicate snapshot arrived for a completed pair",
            "Completed-pair duplicate guard changed");
        NS_TEST_ASSERT_MSG_EQ(
            RandomizedInterventionControllerTestAccess::FindPairError(primary, secondary)
                .has_value(),
            false,
            "Valid paired metadata was rejected");
        auto malformedSecondary = secondary;
        ++malformedSecondary.frameSizeBytes;
        NS_TEST_ASSERT_MSG_EQ(
            RandomizedInterventionControllerTestAccess::FindPairError(primary,
                                                                      malformedSecondary)
                .has_value(),
            true,
            "Immutable pair mismatch was accepted");

        DelayedCopyDescriptor descriptor;
        descriptor.frameId = 0;
        descriptor.framePacketCount = FRAME_PACKET_COUNT;
        descriptor.packetCount = FRAME_PACKET_COUNT;
        descriptor.packetIndices = {0, 1, 2};
        descriptor.expectedMacServiceBytes = 2600;
        descriptor.deadlineTimeNs = DEADLINE_TIME_NS;
        NS_TEST_ASSERT_MSG_EQ(
            RandomizedInterventionControllerTestAccess::FindDescriptorError(primary, descriptor)
                .has_value(),
            false,
            "Valid full-copy descriptor was rejected");
        descriptor.packetIndices = {0, 2, 1};
        NS_TEST_ASSERT_MSG_EQ(
            RandomizedInterventionControllerTestAccess::FindDescriptorError(primary, descriptor)
                .has_value(),
            true,
            "Noncanonical full-copy packet order was accepted");
    }
};

/** Randomized intervention controller test suite. */
class RandomizedInterventionControllerTestSuite : public TestSuite
{
  public:
    RandomizedInterventionControllerTestSuite()
        : TestSuite("wifi-streaming-randomized-intervention", Type::UNIT)
    {
        AddTestCase(new RandomizedInterventionBehaviorTestCase(), Duration::QUICK);
        AddTestCase(new RandomizedInterventionValidationTestCase(), Duration::QUICK);
    }
};

static RandomizedInterventionControllerTestSuite g_randomizedInterventionControllerTestSuite;

} // namespace ns3
