/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "ns3/csma-module.h"
#include "ns3/distributional-shadow-t2-controller.h"
#include "ns3/frame-receiver.h"
#include "ns3/frame-source.h"
#include "ns3/inet-socket-address.h"
#include "ns3/internet-stack-helper.h"
#include "ns3/ipv4-address-helper.h"
#include "ns3/multipath-sender.h"
#include "ns3/redundancy-policy.h"
#include "ns3/secondary-airtime-meter.h"
#include "ns3/simulator.h"
#include "ns3/string.h"
#include "ns3/test.h"
#include "ns3/udp-socket-factory.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <optional>
#include <set>
#include <sstream>
#include <string>
#include <string_view>
#include <vector>

namespace ns3
{

/**
 * Test-only access to immutable configuration and final ledger state.
 *
 * @ingroup tests
 */
class DistributionalShadowT2ControllerTestAccess
{
  public:
    /** @return Frozen ledger refill fraction. */
    static double GetRefillFraction(Ptr<DistributionalShadowT2Controller> controller)
    {
        return controller->m_ledger.GetRefillFraction();
    }

    /** @return Frozen ledger positive carry-over capacity. */
    static double GetCapacityUs(Ptr<DistributionalShadowT2Controller> controller)
    {
        return controller->m_ledger.GetPositiveBalanceCapacityUs();
    }

    /** @return Frozen ledger startup credit. */
    static double GetInitialCreditUs(Ptr<DistributionalShadowT2Controller> controller)
    {
        return controller->m_ledger.GetInitialCreditUs();
    }

    /** @return True after summary-time repayment closure. */
    static bool IsLedgerFinalized(Ptr<DistributionalShadowT2Controller> controller)
    {
        return controller->m_ledger.IsFinalized();
    }

    /** @return Final ledger balance. */
    static double GetFinalBalanceUs(Ptr<DistributionalShadowT2Controller> controller)
    {
        return controller->m_ledger.GetBalanceUs();
    }

    /** @return Accepted launch identities for independent-summary fixture setup. */
    static std::set<uint64_t> GetLaunchedFrameIds(
        Ptr<DistributionalShadowT2Controller> controller)
    {
        return controller->m_launchedFrameIds;
    }
};

namespace
{

constexpr uint64_t START_NS = DistributionalShadowT2Controller::MEASUREMENT_START_NS;
constexpr uint64_t NANOS_PER_MILLISECOND = 1000000;
constexpr uint64_t NANOS_PER_MICROSECOND = 1000;
constexpr uint64_t FRAME_PERIOD_NUMERATOR_NS = 1000000000;
constexpr uint64_t FRAME_PERIOD_DENOMINATOR = 30;
constexpr uint64_t DEADLINE_US = 33333;
constexpr std::array<uint64_t, 3> WINDOWS_US{1000, 5000, 20000};

uint64_t
FrameOffsetNs(uint64_t frameId)
{
    return std::llround(static_cast<double>(frameId) * FRAME_PERIOD_NUMERATOR_NS /
                        FRAME_PERIOD_DENOMINATOR);
}

PredictionRollingSample
MakeRolling(uint64_t frameId, std::size_t windowIndex, bool secondary)
{
    PredictionRollingSample rolling;
    rolling.windowUs = WINDOWS_US.at(windowIndex);
    rolling.mpduAttempts = frameId + windowIndex;
    rolling.mpduPositiveAcks = 0;
    rolling.mpduAttemptFailures = frameId + windowIndex;
    rolling.mpduRetries = rolling.mpduAttempts / 2;
    if (rolling.mpduAttempts != 0)
    {
        rolling.mpduRetryRatio =
            static_cast<double>(rolling.mpduRetries) / rolling.mpduAttempts;
    }
    rolling.acknowledgedMacServiceBytes = frameId * 100 + windowIndex;
    const double adjustment = secondary ? frameId * 0.001 : 0.0;
    rolling.phyTxFraction = 0.10 + adjustment;
    rolling.phyRxFraction = 0.15;
    rolling.phyBusyFraction = 0.35 - adjustment / 2.0;
    rolling.phyOtherFraction = 0.05;
    rolling.phyIdleFraction =
        1.0 - *rolling.phyTxFraction - *rolling.phyRxFraction -
        *rolling.phyBusyFraction - *rolling.phyOtherFraction;
    rolling.historyCoverageUs = static_cast<double>(rolling.windowUs);
    return rolling;
}

PredictionPollingReport
MakeReport(uint64_t frameId, uint64_t sampleTimeNs, bool secondary)
{
    PredictionPollingReport report;
    report.captureTimeNs =
        (sampleTimeNs / NANOS_PER_MILLISECOND - 1) * NANOS_PER_MILLISECOND;
    report.availableTimeNs = report.captureTimeNs + NANOS_PER_MILLISECOND;
    report.latestFeatureEventTimeNs = report.captureTimeNs - 100;
    report.latestFeatureEventSequence = (secondary ? 2000 : 1000) + frameId;
    const uint64_t counter = 20 + frameId * 5;
    report.mpduTxAttemptsTotal = counter;
    report.mpduPositiveAcksTotal = counter;
    report.mpduTxAttemptFailuresTotal = frameId;
    report.mpduRetriesTotal = frameId;
    report.mpduTerminalDropsTotal = 0;
    report.mpduRetryLimitDropsTotal = 0;
    report.mpduLifetimeDropsTotal = 0;
    report.mpduQueueDropsTotal = 0;
    report.ppduTxCountTotal = counter;
    report.lastTxAttemptTimeNs = report.captureTimeNs - 1000;
    report.lastPositiveAckTimeNs = report.captureTimeNs - 2000;
    report.currentMcs = 5;
    report.currentNss = 1;
    report.currentChannelWidthMhz = 20;
    report.currentGuardIntervalNs = 800;
    report.frequencyBand = secondary ? "2.4GHz" : "5GHz";
    report.centerFrequencyMhz = secondary ? 2437.0 : 5180.0;
    report.rolling = {MakeRolling(frameId, 0, secondary),
                      MakeRolling(frameId, 1, secondary),
                      MakeRolling(frameId, 2, secondary)};
    report.featureSupportMask = "0x3ffffffffdffff";
    return report;
}

PredictionSample
MakePrimary(const std::string& runId, uint64_t frameId)
{
    const uint64_t generationTimeNs = START_NS + FrameOffsetNs(frameId);
    PredictionSample sample;
    sample.runId = runId;
    sample.key = {frameId,
                  DistributionalShadowT2Controller::PRIMARY_PATH_ID,
                  DistributionalShadowT2Controller::PRIMARY_COPY_ID};
    sample.sampleStage = "T2";
    sample.sampleOffsetUs = DistributionalShadowT2Controller::T2_OFFSET_US;
    sample.sampleTimeNs =
        generationTimeNs + sample.sampleOffsetUs * NANOS_PER_MICROSECOND;
    sample.latestFeatureEventTimeNs = sample.sampleTimeNs - 100;
    sample.latestFeatureEventSequence = 3000 + frameId;
    sample.generationTimeNs = generationTimeNs;
    sample.deadlineTimeNs = generationTimeNs + DEADLINE_US * NANOS_PER_MICROSECOND;
    sample.frameAgeUs = sample.sampleOffsetUs;
    sample.deadlineSlackUs = DEADLINE_US - sample.sampleOffsetUs;
    sample.senderMacComplete = false;
    sample.actionable = true;
    sample.frameType = frameId == 0 ? FrameType::I_FRAME : FrameType::P_FRAME;
    sample.frameSizeBytes = frameId == 0 ? 48000 : 12000;
    sample.framePacketCount = sample.frameSizeBytes / 1200;
    sample.packetsSubmitted = sample.framePacketCount;
    sample.applicationSocketPacketBytesSubmitted =
        sample.frameSizeBytes + 50ULL * sample.framePacketCount;
    sample.packetsRemainingToSubmit = 0;
    sample.framePacketsMacEnqueued = sample.framePacketCount;
    sample.framePacketsMacDequeued = sample.framePacketCount;
    sample.framePacketsTxSucceeded = frameId % sample.framePacketCount;
    sample.frameMpduAttemptFailures = frameId;
    sample.framePacketsTerminallyDropped = 0;
    sample.framePacketsCurrentlyQueued = 0;
    sample.frameMacServiceBytesCurrentlyQueued = 0;
    sample.macQueuePackets = frameId;
    sample.macQueueServiceBytes = frameId * 100;
    sample.packetsAheadOfFrame = frameId % 3;
    sample.macServiceBytesAheadOfFrame = (frameId % 3) * 100;
    sample.framePacketsPendingPrimary =
        sample.framePacketCount - *sample.framePacketsTxSucceeded;
    sample.frameMacServiceBytesNotAcknowledged =
        static_cast<uint64_t>(*sample.framePacketsPendingPrimary) * 1286;
    sample.frameMacServiceBytesPendingPrimary =
        sample.frameMacServiceBytesNotAcknowledged;
    sample.featureSupportMask = "0x3ffffffffdffff";
    sample.pollingReport = MakeReport(frameId, sample.sampleTimeNs, false);
    return sample;
}

PredictionSample
MakeSecondary(const PredictionSample& primary)
{
    PredictionSample secondary = primary;
    secondary.key.pathId = DistributionalShadowT2Controller::SECONDARY_PATH_ID;
    secondary.key.copyId = DistributionalShadowT2Controller::SECONDARY_COPY_ID;
    secondary.latestFeatureEventTimeNs = secondary.sampleTimeNs - 50;
    secondary.latestFeatureEventSequence = 4000 + primary.key.frameId;
    secondary.senderMacComplete = false;
    secondary.actionable = true;
    secondary.packetsSubmitted = 0;
    secondary.applicationSocketPacketBytesSubmitted = 0;
    secondary.packetsRemainingToSubmit = secondary.framePacketCount;
    secondary.framePacketsMacEnqueued = 0;
    secondary.framePacketsMacDequeued = 0;
    secondary.framePacketsTxSucceeded = 0;
    secondary.frameMpduAttemptFailures = 0;
    secondary.framePacketsTerminallyDropped = 0;
    secondary.framePacketsCurrentlyQueued = 0;
    secondary.frameMacServiceBytesCurrentlyQueued = 0;
    secondary.macQueuePackets = 100 + primary.key.frameId;
    secondary.macQueueServiceBytes = 10000 + primary.key.frameId * 100;
    secondary.pollingReport =
        MakeReport(primary.key.frameId, primary.sampleTimeNs, true);
    return secondary;
}

void
SchedulePair(Ptr<DistributionalShadowT2Controller> controller,
             const PredictionSample& primary)
{
    const auto secondary = MakeSecondary(primary);
    Simulator::Schedule(NanoSeconds(primary.sampleTimeNs),
                        &DistributionalShadowT2Controller::NotifySnapshot,
                        controller,
                        primary);
    Simulator::Schedule(NanoSeconds(primary.sampleTimeNs),
                        &DistributionalShadowT2Controller::NotifySnapshot,
                        controller,
                        secondary);
}

std::vector<std::string>
ReadLines(const std::string& path)
{
    std::ifstream input(path);
    NS_ABORT_MSG_IF(!input, "Cannot open test output " << path);
    std::vector<std::string> lines;
    std::string line;
    while (std::getline(input, line))
    {
        lines.push_back(line);
    }
    return lines;
}

std::string
ReadText(const std::string& path)
{
    std::ifstream input(path);
    NS_ABORT_MSG_IF(!input, "Cannot open test output " << path);
    std::ostringstream text;
    text << input.rdbuf();
    return text.str();
}

std::vector<std::string>
SplitCsv(const std::string& row)
{
    std::vector<std::string> fields;
    std::string field;
    bool quoted = false;
    for (std::size_t index = 0; index < row.size(); ++index)
    {
        const char character = row[index];
        if (character == '\"')
        {
            if (quoted && index + 1 < row.size() && row[index + 1] == '\"')
            {
                field.push_back('\"');
                ++index;
            }
            else
            {
                quoted = !quoted;
            }
        }
        else if (character == ',' && !quoted)
        {
            fields.push_back(std::move(field));
            field.clear();
        }
        else
        {
            field.push_back(character);
        }
    }
    NS_ABORT_MSG_IF(quoted, "Test CSV row has an unterminated quote");
    fields.push_back(std::move(field));
    return fields;
}

struct OutputPaths
{
    std::string decisions;
    std::string controllerSummary;
    std::string meterEvents;
    std::string meterSettlements;
    std::string meterSummary;
};

OutputPaths
MakePaths()
{
    const auto base = std::filesystem::temp_directory_path();
    OutputPaths paths{
        (base / "ns3-distributional-shadow-decisions.csv").string(),
        (base / "ns3-distributional-shadow-summary.json").string(),
        (base / "ns3-distributional-shadow-meter-events.csv").string(),
        (base / "ns3-distributional-shadow-meter-settlements.csv").string(),
        (base / "ns3-distributional-shadow-meter-summary.json").string(),
    };
    for (const auto* path : {&paths.decisions,
                             &paths.controllerSummary,
                             &paths.meterEvents,
                             &paths.meterSettlements,
                             &paths.meterSummary})
    {
        std::remove(path->c_str());
    }
    return paths;
}

void
RemovePaths(const OutputPaths& paths)
{
    for (const auto* path : {&paths.decisions,
                             &paths.controllerSummary,
                             &paths.meterEvents,
                             &paths.meterSettlements,
                             &paths.meterSummary})
    {
        std::remove(path->c_str());
    }
}

struct ControllerSetup
{
    Ptr<MultipathSender> sender;
    Ptr<SecondaryAirtimeMeter> meter;
    Ptr<DistributionalShadowT2Controller> controller;
    OutputPaths paths;
};

ControllerSetup
ConfigureController(const std::string& runId)
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
    address.SetBase("10.93.0.0", "255.255.255.0");
    const auto interfaces = address.Assign(devices);

    constexpr uint16_t port = 9391;
    auto receiver = CreateObject<FrameReceiver>();
    receiver->SetLocal(InetSocketAddress(Ipv4Address::GetAny(), port));
    receiver->SetHoldForDelayedSecondary(true);
    nodes.Get(1)->AddApplication(receiver);
    receiver->SetStartTime(Seconds(0.9));
    receiver->SetStopTime(Seconds(1.5));

    auto source = CreateObject<SyntheticFrameSource>();
    source->SetFps(30);
    source->SetDuration(MilliSeconds(290));
    source->SetConstantFrameSize(12000);
    source->SetKeyframeSizeMultiplier(4);
    source->SetGopLength(60);
    source->SetDeadline(DEADLINE_US);

    auto policy = CreateObject<PairedValueT2Policy>();
    auto sender = CreateObject<MultipathSender>();
    sender->SetFrameSource(source);
    sender->SetPacketPayloadSize(1200);
    sender->SetExpectedMacServiceOverhead(36);
    sender->SetEmissionMode(EmissionMode::BURST);
    sender->SetPolicy(policy);
    sender->SetDelayedSecondaryPath(
        DistributionalShadowT2Controller::SECONDARY_PATH_ID);
    for (uint8_t pathId = 0; pathId < 2; ++pathId)
    {
        auto socket = Socket::CreateSocket(nodes.Get(0), UdpSocketFactory::GetTypeId());
        NS_ABORT_MSG_IF(socket->Bind(InetSocketAddress(interfaces.GetAddress(0), 0)) != 0,
                        "Distributional-shadow test sender bind failed");
        NS_ABORT_MSG_IF(
            socket->Connect(InetSocketAddress(interfaces.GetAddress(1), port)) != 0,
            "Distributional-shadow test sender connect failed");
        sender->AddPath(pathId, socket, devices.Get(0));
    }
    nodes.Get(0)->AddApplication(sender);
    sender->SetStartTime(NanoSeconds(START_NS));
    sender->SetStopTime(Seconds(1.4));

    auto meter = CreateObject<SecondaryAirtimeMeter>();
    auto controller = CreateObject<DistributionalShadowT2Controller>();
    const auto paths = MakePaths();
    meter->SetOutputFiles(runId,
                          paths.meterEvents,
                          paths.meterSettlements,
                          paths.meterSummary);
    controller->SetSender(PeekPointer(sender));
    controller->SetAirtimeMeter(meter);
    controller->SetOutputFiles(runId, paths.decisions, paths.controllerSummary);
    return {sender, meter, controller, paths};
}

} // namespace

/**
 * Exercise pairing, prediction, shadow admission, permanent debit, and summary.
 *
 * @ingroup tests
 */
class DistributionalShadowT2ClosedLoopTestCase : public TestCase
{
  public:
    DistributionalShadowT2ClosedLoopTestCase()
        : TestCase("Distributional shadow T2 closes paired decisions and repayment")
    {
    }

  private:
    void DoRun() override
    {
        const std::string runId = "distributional-shadow-test";
        auto setup = ConfigureController(runId);
        for (uint64_t frameId = 0; frameId < 9; ++frameId)
        {
            SchedulePair(setup.controller, MakePrimary(runId, frameId));
        }
        Simulator::Schedule(Seconds(1.30),
                            &SecondaryAirtimeMeter::WriteSummary,
                            setup.meter);
        Simulator::Schedule(Seconds(1.300001), [controller = setup.controller]() {
            controller->WriteSummary(
                9,
                DistributionalShadowT2ControllerTestAccess::GetLaunchedFrameIds(
                    controller));
        });
        Simulator::Stop(Seconds(1.31));
        Simulator::Run();

        NS_TEST_ASSERT_MSG_EQ(setup.controller->GetPairedFrameCount(),
                              9,
                              "Every generated frame must form one pair");
        NS_TEST_ASSERT_MSG_EQ(setup.controller->GetFeatureEvaluationCount(),
                              1,
                              "Only the history-ready P frame may be evaluated");
        NS_TEST_ASSERT_MSG_EQ(setup.controller->GetLaunchCount(),
                              setup.controller->GetSettlementCount(),
                              "Every accepted launch must settle");
        NS_TEST_ASSERT_MSG_GT(setup.controller->GetLaunchCount(),
                              0,
                              "Synthetic positive-reward fixture did not exercise an action");
        NS_TEST_ASSERT_MSG_EQ(
            DistributionalShadowT2ControllerTestAccess::IsLedgerFinalized(
                setup.controller),
            true,
            "Permanent ledger was not finalized");
        NS_TEST_ASSERT_MSG_EQ(
            DistributionalShadowT2ControllerTestAccess::GetFinalBalanceUs(
                setup.controller) >= 0.0,
            true,
            "Permanent ledger did not repay by stop");

        const auto lines = ReadLines(setup.paths.decisions);
        NS_TEST_ASSERT_MSG_EQ(lines.size(),
                              10,
                              "Decision CSV cardinality changed");
        if (lines.size() == 10)
        {
            const auto header = SplitCsv(lines.front());
            NS_TEST_ASSERT_MSG_GT(header.size(),
                                  90,
                                  "Decision evidence schema is unexpectedly narrow");
            const auto uniqueHeader = std::set<std::string>(header.begin(), header.end());
            NS_TEST_ASSERT_MSG_EQ(uniqueHeader.size(),
                                  header.size(),
                                  "Decision evidence contains duplicate columns");
            for (std::size_t row = 1; row < lines.size(); ++row)
            {
                NS_TEST_ASSERT_MSG_EQ(SplitCsv(lines[row]).size(),
                                      header.size(),
                                      "Decision row width differs at row " << row);
            }
        }
        const std::string summary = ReadText(setup.paths.controllerSummary);
        NS_TEST_ASSERT_MSG_NE(summary.find("\"repayment_closed\": true"),
                              std::string::npos,
                              "Summary lacks repayment proof");
        NS_TEST_ASSERT_MSG_NE(
            summary.find("\"measured_settlement_refunds_ledger\": false"),
            std::string::npos,
            "Summary does not preserve independent measurement accounting");
        NS_TEST_ASSERT_MSG_NE(summary.find("\"status_counts_reconcile\": true"),
                              std::string::npos,
                              "Summary lacks status reconciliation proof");

        setup.controller->Dispose();
        setup.meter->Dispose();
        setup.sender->Dispose();
        Simulator::Destroy();
        RemovePaths(setup.paths);
    }
};

/**
 * Verify immutable runtime and credit metadata before execution.
 *
 * @ingroup tests
 */
class DistributionalShadowT2MetadataTestCase : public TestCase
{
  public:
    DistributionalShadowT2MetadataTestCase()
        : TestCase("Distributional shadow T2 metadata matches frozen runtime")
    {
    }

  private:
    void DoRun() override
    {
        auto controller = CreateObject<DistributionalShadowT2Controller>();
        NS_TEST_ASSERT_MSG_EQ(
            DistributionalShadowT2Controller::GetPolicyName(),
            std::string_view("distributional_shadow_duplication_t2"),
            "Policy identifier changed");
        NS_TEST_ASSERT_MSG_EQ(
            DistributionalShadowT2Controller::GetRuntimeContractId(),
            std::string_view("temporal-t2-shadow-borrow-runtime-v1"),
            "Runtime contract identifier changed");
        NS_TEST_ASSERT_MSG_EQ(
            DistributionalShadowT2Controller::GetRuntimeContractSha256(),
            TemporalT2DistributionModelEvaluator::GetProvenance().runtimeContractSha256,
            "Runtime contract digest changed");
        NS_TEST_ASSERT_MSG_EQ_TOL(
            DistributionalShadowT2ControllerTestAccess::GetRefillFraction(controller),
            0.006,
            1e-15,
            "Credit refill fraction changed");
        NS_TEST_ASSERT_MSG_EQ_TOL(
            DistributionalShadowT2ControllerTestAccess::GetCapacityUs(controller),
            360000.0,
            1e-12,
            "Positive credit capacity changed");
        NS_TEST_ASSERT_MSG_EQ_TOL(
            DistributionalShadowT2ControllerTestAccess::GetInitialCreditUs(controller),
            12000.0,
            1e-12,
            "Initial credit changed");
        controller->Dispose();
    }
};

/**
 * Distributional shadow T2 controller regression suite.
 *
 * @ingroup tests
 */
class DistributionalShadowT2ControllerTestSuite : public TestSuite
{
  public:
    DistributionalShadowT2ControllerTestSuite()
        : TestSuite("wifi-streaming-distributional-shadow-t2-controller", Type::UNIT)
    {
        AddTestCase(new DistributionalShadowT2MetadataTestCase, Duration::QUICK);
        AddTestCase(new DistributionalShadowT2ClosedLoopTestCase, Duration::QUICK);
    }
};

static DistributionalShadowT2ControllerTestSuite
    g_distributionalShadowT2ControllerTestSuite;

} // namespace ns3
