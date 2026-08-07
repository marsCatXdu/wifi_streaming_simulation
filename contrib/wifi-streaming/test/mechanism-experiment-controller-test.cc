/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "ns3/csma-module.h"
#include "ns3/frame-receiver.h"
#include "ns3/frame-source.h"
#include "ns3/inet-socket-address.h"
#include "ns3/internet-stack-helper.h"
#include "ns3/ipv4-address-helper.h"
#include "ns3/mechanism-experiment-controller.h"
#include "ns3/metrics-collector.h"
#include "ns3/multipath-sender.h"
#include "ns3/prediction-telemetry-collector.h"
#include "ns3/redundancy-policy.h"
#include "ns3/secondary-airtime-meter.h"
#include "ns3/simulator.h"
#include "ns3/string.h"
#include "ns3/test.h"
#include "ns3/udp-socket-factory.h"

#include <cstdio>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

namespace ns3
{

namespace
{

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

/**
 * Verify paired T2 state logging and the ideal systematic action.
 */
class MechanismExperimentControllerTestCase : public TestCase
{
  public:
    MechanismExperimentControllerTestCase()
        : TestCase("Mechanism controller logs paired T2 state and launches repair")
    {
    }

  private:
    /** Objects and output paths retained for one scenario. */
    struct Scenario
    {
        Ptr<MultipathSender> sender; ///< Delayed-action sender.
        Ptr<FrameReceiver> receiver; ///< Frame receiver.
        Ptr<MechanismExperimentController> controller; ///< Controller under test.
        std::string snapshots; ///< Snapshot output path.
        std::string actions; ///< Action output path.
    };

    /**
     * Configure one single-frame repair scenario.
     *
     * @param label Unique temporary-file label.
     * @param port Receiver UDP port.
     * @param action Frozen mechanism action.
     * @param oracleFile Optional oracle packet-outcome sidecar.
     * @return Configured scenario.
     */
    Scenario Configure(const std::string& label,
                       uint16_t port,
                       MechanismT2Action action,
                       const std::string& oracleFile = "")
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

        auto metrics = CreateObject<MetricsCollector>();
        const std::string runId = "mechanism-test-" + label;
        metrics->SetRunId(runId);
        auto receiver = CreateObject<FrameReceiver>();
        receiver->SetLocal(InetSocketAddress(Ipv4Address::GetAny(), port));
        receiver->SetMetricsCollector(metrics);
        receiver->SetHoldForDelayedSecondary(true);
        nodes.Get(1)->AddApplication(receiver);
        receiver->SetStartTime(Time());
        receiver->SetStopTime(MilliSeconds(40));

        auto source = CreateObject<SyntheticFrameSource>();
        source->SetFps(30);
        source->SetDuration(MilliSeconds(1));
        source->SetConstantFrameSize(2501);
        source->SetDeadline(20000);

        auto prediction = CreateObject<PredictionTelemetryCollector>();
        prediction->SetRunId(runId);
        prediction->SetSampleOffsetsUs({0, 2000});

        auto policy = CreateObject<MechanismT2Policy>();
        policy->SetKind(action == MechanismT2Action::ORACLE_REPAIR
                            ? MechanismT2PolicyKind::ORACLE_REPAIR
                            : MechanismT2PolicyKind::SYSTEMATIC_REPAIR);
        auto sender = CreateObject<MultipathSender>();
        sender->SetFrameSource(source);
        sender->SetMetricsCollector(metrics);
        sender->SetPredictionTelemetryCollector(prediction);
        sender->SetDelayedSecondaryPredictionTrackingEnabled(true);
        sender->SetPacketPayloadSize(1000);
        sender->SetExpectedMacServiceOverhead(36);
        sender->SetPolicy(policy);
        sender->SetDelayedSecondaryPath(
            MechanismExperimentController::SECONDARY_PATH_ID);
        for (uint8_t pathId = 0; pathId < 2; ++pathId)
        {
            auto socket = Socket::CreateSocket(nodes.Get(0), UdpSocketFactory::GetTypeId());
            NS_ABORT_MSG_IF(socket->Bind(InetSocketAddress(interfaces.GetAddress(0), 0)) != 0,
                            "Mechanism test sender bind failed");
            NS_ABORT_MSG_IF(
                socket->Connect(InetSocketAddress(interfaces.GetAddress(1), port)) != 0,
                "Mechanism test sender connect failed");
            sender->AddPath(pathId, socket, devices.Get(0));
        }
        nodes.Get(0)->AddApplication(sender);
        sender->SetStartTime(MilliSeconds(10));
        sender->SetStopTime(MilliSeconds(40));

        const auto base = std::filesystem::temp_directory_path();
        const std::string snapshots =
            (base / ("ns3-mechanism-" + label + "-snapshots.csv")).string();
        const std::string actions =
            (base / ("ns3-mechanism-" + label + "-actions.csv")).string();
        std::remove(snapshots.c_str());
        std::remove(actions.c_str());

        auto meter = CreateObject<SecondaryAirtimeMeter>();
        meter->SetQueueMaxDelayMs(1);
        auto controller = CreateObject<MechanismExperimentController>();
        controller->SetSender(PeekPointer(sender));
        controller->SetAirtimeMeter(meter);
        controller->SetAction(action);
        controller->SetSystematicRepairDivisor(8);
        if (!oracleFile.empty())
        {
            controller->SetOraclePacketOutcomeFile(oracleFile);
        }
        controller->SetOutputFiles(runId, snapshots, actions);
        prediction->SetSnapshotCallback(
            MakeCallback(&MechanismExperimentController::NotifySnapshot,
                         PeekPointer(controller)));
        return {sender, receiver, controller, snapshots, actions};
    }

    /**
     * Run and dispose one scenario before reading its outputs.
     *
     * @param scenario Scenario to execute.
     */
    void Run(Scenario& scenario)
    {
        Simulator::Stop(MilliSeconds(35));
        Simulator::Run();
        scenario.controller->Dispose();
    }

    void DoRun() override
    {
        auto systematic = Configure("systematic", 9051,
                                    MechanismT2Action::SYSTEMATIC_REPAIR);
        Run(systematic);
        NS_TEST_ASSERT_MSG_EQ(systematic.controller->GetPairedFrameCount(),
                              1,
                              "Systematic action lacks a paired T2 observation");
        NS_TEST_ASSERT_MSG_EQ(systematic.controller->GetLaunchCount(),
                              1,
                              "Systematic action was not launched");
        NS_TEST_ASSERT_MSG_EQ(systematic.sender->GetPathRedundantBytesSent(0),
                              1000 + StreamingHeader::SERIALIZED_SIZE,
                              "Systematic repair did not account for one padded symbol");
        const auto snapshots = ReadLines(systematic.snapshots);
        const auto actions = ReadLines(systematic.actions);
        NS_TEST_ASSERT_MSG_EQ(snapshots.size(),
                              3,
                              "Mechanism snapshot CSV must contain two path rows");
        NS_TEST_ASSERT_MSG_EQ(actions.size(),
                              2,
                              "Mechanism action CSV must contain one frame row");
        NS_TEST_ASSERT_MSG_EQ(actions[1].find("IDEAL_SYSTEMATIC_REPAIR_T2") !=
                                  std::string::npos,
                              true,
                              "Systematic action provenance is absent");
        NS_TEST_ASSERT_MSG_EQ(actions[1].find(",3,") != std::string::npos,
                              true,
                              "Systematic repair symbol index is absent");
        std::remove(systematic.snapshots.c_str());
        std::remove(systematic.actions.c_str());
        Simulator::Destroy();

        const auto base = std::filesystem::temp_directory_path();
        const std::string oracleFile =
            (base / "ns3-mechanism-oracle-packet-outcomes.csv").string();
        {
            std::ofstream oracle(oracleFile);
            oracle << "run_id,frame_id,source_packet_count,"
                      "received_source_packet_indices,missing_source_packet_indices,"
                      "copy_0_source_packet_indices,copy_1_source_packet_indices,"
                      "link_0_source_packet_indices,link_1_source_packet_indices,"
                      "received_coded_repair_indices\n"
                   << "paired-primary,0,3,0;1,2,0;1,,0;1,,\n";
        }
        auto oracle = Configure("oracle", 9052,
                                MechanismT2Action::ORACLE_REPAIR, oracleFile);
        Run(oracle);
        NS_TEST_ASSERT_MSG_EQ(oracle.controller->GetLaunchCount(),
                              1,
                              "Oracle source repair was not launched");
        NS_TEST_ASSERT_MSG_EQ(oracle.sender->GetPathRedundantBytesSent(0),
                              501 + StreamingHeader::SERIALIZED_SIZE,
                              "Oracle repair did not preserve the short source payload");
        const auto oracleActions = ReadLines(oracle.actions);
        NS_TEST_ASSERT_MSG_EQ(oracleActions.size(),
                              2,
                              "Oracle action CSV must contain one frame row");
        NS_TEST_ASSERT_MSG_EQ(oracleActions[1].find(",2,") != std::string::npos,
                              true,
                              "Oracle-selected source index is absent");
        std::remove(oracle.snapshots.c_str());
        std::remove(oracle.actions.c_str());
        std::remove(oracleFile.c_str());
        Simulator::Destroy();
    }
};

/** Mechanism controller test suite. */
class MechanismExperimentControllerTestSuite : public TestSuite
{
  public:
    MechanismExperimentControllerTestSuite()
        : TestSuite("wifi-streaming-mechanism-controller", Type::UNIT)
    {
        AddTestCase(new MechanismExperimentControllerTestCase, Duration::QUICK);
    }
};

static MechanismExperimentControllerTestSuite g_mechanismExperimentControllerTestSuite;

} // namespace

} // namespace ns3
