/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "ns3/csma-module.h"
#include "ns3/correlated-load-controller.h"
#include "ns3/experiment-output.h"
#include "ns3/frame-packetizer.h"
#include "ns3/frame-receiver.h"
#include "ns3/frame-source.h"
#include "ns3/inet-socket-address.h"
#include "ns3/internet-stack-helper.h"
#include "ns3/ipv4-address-helper.h"
#include "ns3/metrics-collector.h"
#include "ns3/multipath-sender.h"
#include "ns3/random-rate-on-off-application.h"
#include "ns3/redundancy-policy.h"
#include "ns3/simulator.h"
#include "ns3/string.h"
#include "ns3/streaming-header.h"
#include "ns3/test.h"
#include "ns3/udp-socket-factory.h"

#include <cstdio>
#include <filesystem>
#include <fstream>
#include <sstream>

using namespace ns3;

namespace
{

Ptr<Packet>
MakeStreamingPacket(uint64_t frameId,
                    uint32_t packetIndex,
                    uint32_t packetCount,
                    uint8_t copyId = 0,
                    uint8_t linkId = 0,
                    uint32_t deadlineUs = 10000,
                    uint64_t generationTimeNs = 0)
{
    StreamingHeader header;
    header.frameId = frameId;
    header.packetIndex = packetIndex;
    header.packetCount = packetCount;
    header.frameSizeBytes = packetCount * 100;
    header.frameType = FrameType::P_FRAME;
    header.generationTimeNs = generationTimeNs;
    header.deadlineUs = deadlineUs;
    header.copyId = copyId;
    header.senderLinkId = linkId;
    auto packet = Create<Packet>(100);
    packet->AddHeader(header);
    return packet;
}

class HeaderTestCase : public TestCase
{
  public:
    HeaderTestCase()
        : TestCase("StreamingHeader round trip and malformed input")
    {
    }

  private:
    void DoRun() override
    {
        StreamingHeader source;
        source.runIdHash = 0x1122334455667788;
        source.frameId = 0x8877665544332211;
        source.packetIndex = 3;
        source.packetCount = 7;
        source.frameSizeBytes = 7123;
        source.frameType = FrameType::PRIORITY_HIGH;
        source.generationTimeNs = 1234567890123;
        source.deadlineUs = 33333;
        source.copyId = 1;
        source.senderLinkId = 2;
        source.flags = 0xa55a;

        auto packet = Create<Packet>();
        packet->AddHeader(source);
        NS_TEST_ASSERT_MSG_EQ(packet->GetSize(),
                              StreamingHeader::SERIALIZED_SIZE,
                              "Unexpected wire size");
        StreamingHeader decoded;
        NS_TEST_ASSERT_MSG_EQ(packet->RemoveHeader(decoded),
                              StreamingHeader::SERIALIZED_SIZE,
                              "Header did not deserialize");
        NS_TEST_ASSERT_MSG_EQ(decoded.runIdHash, source.runIdHash, "Run hash changed");
        NS_TEST_ASSERT_MSG_EQ(decoded.frameId, source.frameId, "Frame ID changed");
        NS_TEST_ASSERT_MSG_EQ(decoded.packetIndex, source.packetIndex, "Packet index changed");
        NS_TEST_ASSERT_MSG_EQ(decoded.packetCount, source.packetCount, "Packet count changed");
        NS_TEST_ASSERT_MSG_EQ(decoded.frameSizeBytes, source.frameSizeBytes, "Frame size changed");
        NS_TEST_ASSERT_MSG_EQ(static_cast<uint8_t>(decoded.frameType),
                              static_cast<uint8_t>(source.frameType),
                              "Frame type changed");
        NS_TEST_ASSERT_MSG_EQ(decoded.generationTimeNs,
                              source.generationTimeNs,
                              "Generation time changed");
        NS_TEST_ASSERT_MSG_EQ(decoded.deadlineUs, source.deadlineUs, "Deadline changed");
        NS_TEST_ASSERT_MSG_EQ(decoded.copyId, source.copyId, "Copy ID changed");
        NS_TEST_ASSERT_MSG_EQ(decoded.senderLinkId, source.senderLinkId, "Link ID changed");
        NS_TEST_ASSERT_MSG_EQ(decoded.flags, source.flags, "Flags changed");

        StreamingHeader truncated;
        NS_TEST_ASSERT_MSG_EQ(Create<Packet>(10)->RemoveHeader(truncated),
                              0,
                              "Truncated header was accepted");
    }
};

class TraceSourceTestCase : public TestCase
{
  public:
    TraceSourceTestCase()
        : TestCase("Trace CSV parsing and deterministic synthetic streams")
    {
    }

  private:
    void DoRun() override
    {
        const std::string fileName = "/tmp/ns3-wifi-streaming-trace.csv";
        {
            std::ofstream output(fileName);
            output << "frame_id,generation_time_us,size_bytes,frame_type,deadline_us\n"
                   << "10,0,126442,I_FRAME,33333\n"
                   << "11,33333,59122,P_FRAME,20000\n";
        }
        auto trace = CreateObject<TraceFrameSource>();
        trace->SetFileName(fileName);
        const auto frames = trace->GetFrames();
        std::remove(fileName.c_str());
        NS_TEST_ASSERT_MSG_EQ(frames.size(), 2, "Wrong trace frame count");
        NS_TEST_ASSERT_MSG_EQ(frames[1].generationTimeNs, 33333000, "Time was not converted");
        NS_TEST_ASSERT_MSG_EQ(frames[0].frameSizeBytes, 126442, "Size changed");
        NS_TEST_ASSERT_MSG_EQ(static_cast<uint8_t>(frames[0].frameType),
                              static_cast<uint8_t>(FrameType::I_FRAME),
                              "Type changed");

        auto first = CreateObject<SyntheticFrameSource>();
        auto second = CreateObject<SyntheticFrameSource>();
        first->SetDuration(MilliSeconds(100));
        second->SetDuration(MilliSeconds(100));
        first->SetLognormalFrameSize(8, 0.5);
        second->SetLognormalFrameSize(8, 0.5);
        first->AssignStreams(17);
        second->AssignStreams(17);
        const auto firstFrames = first->GetFrames();
        const auto secondFrames = second->GetFrames();
        NS_TEST_ASSERT_MSG_EQ(firstFrames.size(), secondFrames.size(), "Frame counts differ");
        for (std::size_t i = 0; i < firstFrames.size(); ++i)
        {
            NS_TEST_ASSERT_MSG_EQ(firstFrames[i].frameSizeBytes,
                                  secondFrames[i].frameSizeBytes,
                                  "Assigned streams are not deterministic");
        }
    }
};

class PacketizerTestCase : public TestCase
{
  public:
    PacketizerTestCase()
        : TestCase("Frame packetization and emission offsets")
    {
    }

  private:
    void DoRun() override
    {
        FrameDescriptor frame{9, 123, 2501, 0, 5000, FrameType::I_FRAME};
        FramePacketizer packetizer;
        packetizer.SetPayloadSize(1000);
        packetizer.SetEmissionMode(EmissionMode::UNIFORM_WITHIN_FRAME);
        packetizer.SetEmissionSpan(MilliSeconds(2));
        const auto emissions = packetizer.Packetize(frame, 42, 1, 3);
        NS_TEST_ASSERT_MSG_EQ(emissions.size(), 3, "Incorrect packet count");
        NS_TEST_ASSERT_MSG_EQ(emissions[0].packet->GetSize(),
                              1000 + StreamingHeader::SERIALIZED_SIZE,
                              "Incorrect full packet size");
        NS_TEST_ASSERT_MSG_EQ(emissions[2].packet->GetSize(),
                              501 + StreamingHeader::SERIALIZED_SIZE,
                              "Incorrect final packet size");
        NS_TEST_ASSERT_MSG_EQ(emissions[0].offset, Time(), "First offset is not zero");
        NS_TEST_ASSERT_MSG_EQ(emissions[1].offset, MilliSeconds(1), "Middle offset is wrong");
        NS_TEST_ASSERT_MSG_EQ(emissions[2].offset, MilliSeconds(2), "Last offset is wrong");
    }
};

class CorrelatedLoadControllerTestCase : public TestCase
{
  public:
    CorrelatedLoadControllerTestCase()
        : TestCase("Deterministic common/local transitions and replay")
    {
    }

  private:
    void DoRun() override
    {
        auto common = CreateObject<CorrelatedLoadController>();
        common->SetLinkCount(2);
        common->SetMode("common_bursts");
        common->SetCommonDeterministicDurations(MilliSeconds(20), MilliSeconds(10));
        common->Start(Time(), MilliSeconds(55));
        Simulator::Stop(MilliSeconds(55));
        Simulator::Run();
        const auto commonTransitions = common->GetTransitions();
        NS_TEST_ASSERT_MSG_EQ(commonTransitions.size(), 6, "Wrong number of common events");
        NS_TEST_ASSERT_MSG_EQ(commonTransitions[0].time, MilliSeconds(10), "First event moved");
        NS_TEST_ASSERT_MSG_EQ(commonTransitions[0].link, 0, "Common event omitted link 0");
        NS_TEST_ASSERT_MSG_EQ(commonTransitions[1].link, 1, "Common event omitted link 1");
        NS_TEST_ASSERT_MSG_EQ(commonTransitions[0].on, true, "Common ON state is wrong");
        NS_TEST_ASSERT_MSG_EQ(commonTransitions[0].source, "common", "Wrong event source");
        NS_TEST_ASSERT_MSG_EQ(commonTransitions[2].time, MilliSeconds(30), "OFF event moved");
        NS_TEST_ASSERT_MSG_EQ(commonTransitions[2].on, false, "Common OFF state is wrong");
        Simulator::Destroy();

        auto mixed = CreateObject<CorrelatedLoadController>();
        mixed->SetLinkCount(2);
        mixed->SetMode("mixed_common_and_independent");
        mixed->SetCommonDeterministicDurations(MilliSeconds(10), MilliSeconds(10));
        mixed->SetLocalDeterministicDurations(MilliSeconds(30), MilliSeconds(15));
        mixed->Start(Time(), MilliSeconds(26));
        Simulator::Stop(MilliSeconds(26));
        Simulator::Run();
        bool sawCommon0 = false;
        bool sawCommon1 = false;
        bool sawLocal = false;
        for (const auto& transition : mixed->GetTransitions())
        {
            sawCommon0 |= transition.source == "common" && transition.link == 0;
            sawCommon1 |= transition.source == "common" && transition.link == 1;
            sawLocal |= transition.source == "local";
        }
        NS_TEST_ASSERT_MSG_EQ(sawCommon0 && sawCommon1,
                              true,
                              "Common process was not explicitly applied to both links");
        NS_TEST_ASSERT_MSG_EQ(sawLocal, true, "Local process did not run independently");
        Simulator::Destroy();

        const std::string traceFile = "/tmp/ns3-wifi-streaming-load-trace.csv";
        {
            std::ofstream trace(traceFile);
            trace << "timestamp_s,link,on\n"
                  << "0.001,1,on\n"
                  << "0.003,0,1\n"
                  << "0.004,1,off\n";
        }
        auto replay = CreateObject<CorrelatedLoadController>();
        replay->SetLinkCount(2);
        replay->SetMode("trace_replay");
        replay->SetTraceFile(traceFile);
        replay->Start(MilliSeconds(5), MilliSeconds(15));
        Simulator::Stop(MilliSeconds(15));
        Simulator::Run();
        const auto replayTransitions = replay->GetTransitions();
        NS_TEST_ASSERT_MSG_EQ(replayTransitions.size(), 3, "Trace event count changed");
        NS_TEST_ASSERT_MSG_EQ(replayTransitions[0].time,
                              MilliSeconds(6),
                              "Trace time was not relative to controller start");
        NS_TEST_ASSERT_MSG_EQ(replayTransitions[0].link, 1, "Trace link changed");
        NS_TEST_ASSERT_MSG_EQ(replayTransitions[2].on, false, "Trace OFF state changed");
        std::remove(traceFile.c_str());
        Simulator::Destroy();
    }
};

class CorrelationSanityTestCase : public TestCase
{
  public:
    CorrelationSanityTestCase()
        : TestCase("Independent events are not joint while common events are joint")
    {
    }

  private:
    void DoRun() override
    {
        auto independent = CreateObject<CorrelatedLoadController>();
        independent->SetLinkCount(2);
        independent->SetMode("independent");
        independent->SetLocalMeans(MilliSeconds(5), MilliSeconds(5));
        independent->SetLocalDeterministicDurations(Seconds(1), NanoSeconds(0));
        independent->AssignStreams(900);
        independent->Start(Time(), MilliSeconds(50));
        Simulator::Stop(MilliSeconds(50));
        Simulator::Run();
        const auto independentTransitions = independent->GetTransitions();
        std::optional<Time> first0;
        std::optional<Time> first1;
        for (const auto& transition : independentTransitions)
        {
            if (transition.link == 0 && !first0)
            {
                first0 = transition.time;
            }
            if (transition.link == 1 && !first1)
            {
                first1 = transition.time;
            }
        }
        NS_TEST_ASSERT_MSG_EQ(first0.has_value() && first1.has_value(),
                              true,
                              "Independent links did not both transition");
        NS_TEST_ASSERT_MSG_EQ(*first0 != *first1,
                              true,
                              "Independent streams produced an identical first event");
        Simulator::Destroy();

        auto common = CreateObject<CorrelatedLoadController>();
        common->SetLinkCount(2);
        common->SetMode("common_bursts");
        common->SetCommonDeterministicDurations(MilliSeconds(5), MilliSeconds(5));
        common->Start(Time(), MilliSeconds(20));
        Simulator::Stop(MilliSeconds(20));
        Simulator::Run();
        const auto transitions = common->GetTransitions();
        NS_TEST_ASSERT_MSG_EQ(transitions.size() >= 2, true, "No common transition occurred");
        NS_TEST_ASSERT_MSG_EQ(transitions[0].time,
                              transitions[1].time,
                              "Common transition was not joint");
        NS_TEST_ASSERT_MSG_EQ(transitions[0].link != transitions[1].link,
                              true,
                              "Common transition did not cover distinct links");
        Simulator::Destroy();
    }
};

class PolicyTestCase : public TestCase
{
  public:
    PolicyTestCase()
        : TestCase("Fixed, static-best, and full-duplication policy decisions")
    {
    }

  private:
    void DoRun() override
    {
        const FrameDescriptor frame{1, 0, 1200, 1, 10000, FrameType::P_FRAME};
        LinkTelemetrySnapshot telemetry;

        auto fixed = CreateObject<FixedLinkPolicy>();
        fixed->SetPath(1);
        auto decision = fixed->Decide(frame, telemetry);
        NS_TEST_ASSERT_MSG_EQ(decision.primaryPath, 1, "Fixed policy chose wrong path");
        NS_TEST_ASSERT_MSG_EQ(decision.duplicate, false, "Fixed policy duplicated");
        NS_TEST_ASSERT_MSG_EQ(fixed->GetName(), "fixed_link_1", "Fixed policy name is wrong");

        auto best = CreateObject<StaticBestLinkPolicy>();
        best->SetPathScores(8.0, 2.0);
        decision = best->Decide(frame, telemetry);
        NS_TEST_ASSERT_MSG_EQ(decision.primaryPath, 1, "Static policy ignored ranking");
        NS_TEST_ASSERT_MSG_EQ(decision.duplicate, false, "Static policy duplicated");
        best->SetPathScores(1.0, 1.0);
        NS_TEST_ASSERT_MSG_EQ(best->Decide(frame, telemetry).primaryPath,
                              0,
                              "Static policy tie-break is not deterministic");

        auto duplicate = CreateObject<FullDuplicationPolicy>();
        decision = duplicate->Decide(frame, telemetry);
        NS_TEST_ASSERT_MSG_EQ(decision.primaryPath, 0, "Wrong duplication primary");
        NS_TEST_ASSERT_MSG_EQ(decision.duplicate, true, "Duplication policy did not duplicate");
        NS_TEST_ASSERT_MSG_EQ(decision.secondaryPath.has_value(),
                              true,
                              "Duplication secondary is absent");
        NS_TEST_ASSERT_MSG_EQ(*decision.secondaryPath, 1, "Wrong duplication secondary");
    }
};

class OutputStatisticsTestCase : public TestCase
{
  public:
    OutputStatisticsTestCase()
        : TestCase("Output statistics, schemas, and accounting invariants")
    {
    }

  private:
    void DoRun() override
    {
        FrameResult first;
        first.frame = {1, 0, 1000, 1, 100, FrameType::P_FRAME};
        first.unionCompletionUs = 10;
        FrameResult recovered;
        recovered.frame = {2, 100000, 2000, 2, 100, FrameType::P_FRAME};
        recovered.duplicated = true;
        recovered.unionCompletionUs = 120;
        recovered.copy0CompletionUs = 130;
        FrameResult noBenefit;
        noBenefit.frame = {3, 200000, 3000, 3, 100, FrameType::P_FRAME};
        noBenefit.duplicated = true;
        noBenefit.unionCompletionUs = 230;
        noBenefit.copy0CompletionUs = 230;
        FrameResult missing;
        missing.frame = {4, 300000, 4000, 4, 100, FrameType::P_FRAME};
        missing.duplicated = true;
        missing.incomplete = true;
        missing.deadlineMiss = true;

        LinkIntervalRecord link;
        link.successfulMpdus = 10;
        link.failedMpdus = 2;
        link.retransmissions = 3;
        link.phyTxTimeUs = 40;
        const auto summary = ExperimentOutput::ComputeSummary(
            {first, recovered, noBenefit, missing},
            1.0,
            10000,
            2500,
            {link});
        NS_TEST_ASSERT_MSG_EQ(summary.frameCount, 4, "Frame denominator is wrong");
        NS_TEST_ASSERT_MSG_EQ(summary.completeFrameCount, 3, "Complete count is wrong");
        NS_TEST_ASSERT_MSG_EQ(summary.incompleteFrameCount, 1, "Incomplete count is wrong");
        NS_TEST_ASSERT_MSG_EQ_TOL(summary.completeRatio, 0.75, 1e-12, "Complete ratio is wrong");
        NS_TEST_ASSERT_MSG_EQ_TOL(*summary.latencyP50Us, 20, 1e-12, "P50 is wrong");
        NS_TEST_ASSERT_MSG_EQ(summary.latencyP999Us.has_value(),
                              false,
                              "P99.9 was reported for an indefensible sample");
        NS_TEST_ASSERT_MSG_EQ(summary.duplicateRecoveryCount, 1, "Recovery count is wrong");
        NS_TEST_ASSERT_MSG_EQ(summary.duplicateNoBenefitCount, 1, "No-benefit count is wrong");
        NS_TEST_ASSERT_MSG_EQ_TOL(summary.redundantByteRatio,
                                  0.25,
                                  1e-12,
                                  "Redundant ratio is wrong");
        NS_TEST_ASSERT_MSG_EQ(summary.successfulMpdus, 10, "MAC total is wrong");
        NS_TEST_ASSERT_MSG_EQ(summary.phyTxTimeUs, 40, "Airtime total is wrong");

        const std::string directory = "/tmp/ns3-wifi-streaming-output-test";
        std::filesystem::remove_all(directory);
        ExperimentOutput::PrepareRunDirectory(directory);
        StreamingRunConfig config;
        config.runId = "schema-test";
        ExperimentOutput::WriteResolvedConfig(directory, config);
        StreamingBuildInfo build;
        build.ns3Version = "ns-3.48";
        build.ns3UpstreamCommit = ExperimentOutput::NS3_UPSTREAM_COMMIT;
        ExperimentOutput::WriteBuildInfo(directory, build);
        ExperimentOutput::WriteLinkIntervals(directory, {link});
        ExperimentOutput::WriteMacSummary(directory, {MacSummaryRecord{}});
        ExperimentOutput::WriteBackgroundFlows(directory, {BackgroundFlowRecord{}});
        ExperimentOutput::WriteBackgroundRatePeriods(
            directory,
            {BackgroundRatePeriodRecord{}});
        ExperimentOutput::WriteSummary(directory, summary);
        {
            auto collector = CreateObject<MetricsCollector>();
            collector->SetOutputFiles(directory + "/frames.csv",
                                      directory + "/policy_decisions.csv");
        }
        const std::vector<std::string> required{"resolved_config.json",
                                                "build_info.json",
                                                "frames.csv",
                                                "policy_decisions.csv",
                                                "link_intervals.csv",
                                                "mac_summary.csv",
                                                "background_flows.csv",
                                                "background_rate_periods.csv",
                                                "summary.json"};
        for (const auto& name : required)
        {
            NS_TEST_ASSERT_MSG_EQ(std::filesystem::is_regular_file(
                                      std::filesystem::path(directory) / name),
                                  true,
                                  "Missing required output " << name);
        }
        std::ifstream frames(directory + "/frames.csv");
        std::string header;
        std::getline(frames, header);
        NS_TEST_ASSERT_MSG_EQ(header.find("union_latency_us") != std::string::npos,
                              true,
                              "frames.csv schema changed");
        std::ifstream summaryFile(directory + "/summary.json");
        std::stringstream contents;
        contents << summaryFile.rdbuf();
        NS_TEST_ASSERT_MSG_EQ(contents.str().find("\"latency_p99_9_us\": null") !=
                                  std::string::npos,
                              true,
                              "Summary null semantics changed");
        std::filesystem::remove_all(directory);
    }
};

class ReassemblyTestCase : public TestCase
{
  public:
    ReassemblyTestCase()
        : TestCase("Out-of-order union reassembly and duplicate suppression")
    {
    }

  private:
    void DoRun() override
    {
        auto collector = CreateObject<MetricsCollector>();
        auto receiver = CreateObject<FrameReceiver>();
        receiver->SetMetricsCollector(collector);
        receiver->ProcessPacket(MakeStreamingPacket(1, 2, 3, 0, 0));
        receiver->ProcessPacket(MakeStreamingPacket(1, 0, 3, 0, 0));
        receiver->ProcessPacket(MakeStreamingPacket(1, 0, 3, 1, 1));
        receiver->ProcessPacket(MakeStreamingPacket(1, 1, 3, 1, 1));
        NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults().size(), 1, "Frame did not complete");
        const auto& result = collector->GetFrameResults().front();
        NS_TEST_ASSERT_MSG_EQ(result.uniquePacketsReceived, 3, "Wrong unique count");
        NS_TEST_ASSERT_MSG_EQ(result.duplicatePacketsReceived, 1, "Duplicate was not counted");
        NS_TEST_ASSERT_MSG_EQ(result.incomplete, false, "Complete frame marked incomplete");
        NS_TEST_ASSERT_MSG_EQ(result.completionMode, "mixed", "Mixed-link union not detected");
        Simulator::Destroy();

        collector = CreateObject<MetricsCollector>();
        receiver = CreateObject<FrameReceiver>();
        receiver->SetMetricsCollector(collector);
        receiver->ProcessPacket(MakeStreamingPacket(2, 0, 2, 1, 1));
        receiver->ProcessPacket(MakeStreamingPacket(2, 0, 2, 0, 0));
        receiver->ProcessPacket(MakeStreamingPacket(2, 1, 2, 1, 1));
        NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults().size(),
                              1,
                              "Duplicated frame did not complete");
        const auto& duplicatedResult = collector->GetFrameResults().front();
        NS_TEST_ASSERT_MSG_EQ(duplicatedResult.uniquePacketsReceived, 2, "Wrong union size");
        NS_TEST_ASSERT_MSG_EQ(duplicatedResult.duplicatePacketsReceived,
                              1,
                              "Duplicate suppression count is wrong");
        NS_TEST_ASSERT_MSG_EQ(duplicatedResult.copy1CompletionUs.has_value(),
                              true,
                              "Complete copy 1 was not detected");
        NS_TEST_ASSERT_MSG_EQ(duplicatedResult.copy0CompletionUs.has_value(),
                              false,
                              "Incomplete copy 0 marked complete");
        NS_TEST_ASSERT_MSG_EQ(duplicatedResult.completionMode,
                              "link_1_only",
                              "Recovery copy was not identified");
        Simulator::Destroy();
    }
};

class FinalizationTestCase : public TestCase
{
  public:
    FinalizationTestCase()
        : TestCase("Deadline and cleanup finalize incomplete frames")
    {
    }

  private:
    void DoRun() override
    {
        auto collector = CreateObject<MetricsCollector>();
        auto receiver = CreateObject<FrameReceiver>();
        receiver->SetMetricsCollector(collector);
        receiver->SetCleanupTimeout(MilliSeconds(2));
        receiver->ProcessPacket(MakeStreamingPacket(1, 0, 2, 0, 0, 100));
        Simulator::Stop(MicroSeconds(101));
        Simulator::Run();
        NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults().size(), 1, "Deadline did not finalize");
        NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults()[0].deadlineMiss,
                              true,
                              "Deadline miss not classified");
        NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults()[0].incomplete,
                              true,
                              "Expired frame marked complete");
        Simulator::Destroy();

        collector = CreateObject<MetricsCollector>();
        receiver = CreateObject<FrameReceiver>();
        receiver->SetMetricsCollector(collector);
        receiver->SetCleanupTimeout(MilliSeconds(1));
        receiver->ProcessPacket(MakeStreamingPacket(2, 0, 2, 0, 0, 0));
        Simulator::Stop(MilliSeconds(2));
        Simulator::Run();
        NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults().size(), 1, "Cleanup did not finalize");
        NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults()[0].deadlineMiss,
                              false,
                              "No-deadline frame marked late");
        Simulator::Destroy();
    }
};

class CountingFixedPolicy : public RedundancyPolicy
{
  public:
    PolicyDecision Decide(const FrameDescriptor&, const LinkTelemetrySnapshot&) override
    {
        ++m_decisions;
        PolicyDecision decision;
        decision.primaryPath = 0;
        decision.reason = "test";
        return decision;
    }

    std::string GetName() const override
    {
        return "counting_fixed";
    }

    uint32_t GetDecisionCount() const
    {
        return m_decisions;
    }

  private:
    uint32_t m_decisions{0};
};

class IntegrationDeliveryTestCase : public TestCase
{
  public:
    IntegrationDeliveryTestCase()
        : TestCase("Single-path UDP application delivery")
    {
    }

  private:
    void DoRun() override
    {
        NodeContainer nodes;
        nodes.Create(2);
        InternetStackHelper internet;
        internet.Install(nodes);
        CsmaHelper csma;
        csma.SetChannelAttribute("DataRate", StringValue("100Mbps"));
        csma.SetChannelAttribute("Delay", TimeValue(MicroSeconds(10)));
        NetDeviceContainer devices = csma.Install(nodes);
        Ipv4AddressHelper address;
        address.SetBase("10.8.0.0", "255.255.255.0");
        auto interfaces = address.Assign(devices);

        auto collector = CreateObject<MetricsCollector>();
        auto receiver = CreateObject<FrameReceiver>();
        receiver->SetLocal(InetSocketAddress(Ipv4Address::GetAny(), 9000));
        receiver->SetMetricsCollector(collector);
        nodes.Get(1)->AddApplication(receiver);
        receiver->SetStartTime(Time());
        receiver->SetStopTime(Seconds(1));

        auto source = CreateObject<SyntheticFrameSource>();
        source->SetFps(20);
        source->SetDuration(MilliSeconds(100));
        source->SetConstantFrameSize(2401);
        source->SetDeadline(100000);

        auto socket = Socket::CreateSocket(nodes.Get(0), UdpSocketFactory::GetTypeId());
        NS_TEST_ASSERT_MSG_EQ(socket->Bind(InetSocketAddress(interfaces.GetAddress(0), 0)),
                              0,
                              "Sender bind failed");
        auto sender = CreateObject<MultipathSender>();
        auto policy = CreateObject<CountingFixedPolicy>();
        sender->SetFrameSource(source);
        sender->SetPacketPayloadSize(1200);
        sender->SetPolicy(policy);
        sender->AddPath(0, socket, devices.Get(0));
        NS_TEST_ASSERT_MSG_EQ(socket->Connect(InetSocketAddress(interfaces.GetAddress(1), 9000)),
                              0,
                              "Sender connect failed");
        nodes.Get(0)->AddApplication(sender);
        sender->SetStartTime(MilliSeconds(10));
        sender->SetStopTime(MilliSeconds(500));

        Simulator::Stop(Seconds(1));
        Simulator::Run();
        NS_TEST_ASSERT_MSG_EQ(sender->GetPacketsSent(), 6, "Wrong number of packets sent");
        NS_TEST_ASSERT_MSG_EQ(policy->GetDecisionCount(),
                              2,
                              "Sender did not make exactly one decision per frame");
        NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults().size(), 2, "Not all frames delivered");
        for (const auto& result : collector->GetFrameResults())
        {
            NS_TEST_ASSERT_MSG_EQ(result.incomplete, false, "Delivered frame is incomplete");
            NS_TEST_ASSERT_MSG_EQ(result.deadlineMiss, false, "Generous deadline was missed");
        }
        Simulator::Destroy();
    }
};

class FullDuplicationDeliveryTestCase : public TestCase
{
  public:
    FullDuplicationDeliveryTestCase()
        : TestCase("Complete copies use separate bound devices")
    {
    }

  private:
    void DoRun() override
    {
        NodeContainer nodes;
        nodes.Create(2);
        InternetStackHelper internet;
        internet.Install(nodes);

        CsmaHelper csma0;
        csma0.SetChannelAttribute("DataRate", StringValue("100Mbps"));
        csma0.SetChannelAttribute("Delay", TimeValue(MicroSeconds(10)));
        const NetDeviceContainer devices0 = csma0.Install(nodes);
        CsmaHelper csma1;
        csma1.SetChannelAttribute("DataRate", StringValue("80Mbps"));
        csma1.SetChannelAttribute("Delay", TimeValue(MicroSeconds(20)));
        const NetDeviceContainer devices1 = csma1.Install(nodes);

        Ipv4AddressHelper address;
        address.SetBase("10.9.0.0", "255.255.255.0");
        const auto interfaces0 = address.Assign(devices0);
        address.SetBase("10.9.1.0", "255.255.255.0");
        const auto interfaces1 = address.Assign(devices1);

        auto collector = CreateObject<MetricsCollector>();
        auto receiver = CreateObject<FrameReceiver>();
        receiver->SetLocal(InetSocketAddress(Ipv4Address::GetAny(), 9001));
        receiver->SetMetricsCollector(collector);
        nodes.Get(1)->AddApplication(receiver);
        receiver->SetStartTime(Time());
        receiver->SetStopTime(Seconds(1));

        auto source = CreateObject<SyntheticFrameSource>();
        source->SetFps(30);
        source->SetDuration(MilliSeconds(1));
        source->SetConstantFrameSize(2401);
        source->SetDeadline(100000);

        auto sender = CreateObject<MultipathSender>();
        sender->SetFrameSource(source);
        sender->SetPacketPayloadSize(1200);
        sender->SetPolicy(CreateObject<FullDuplicationPolicy>());
        auto socket0 = Socket::CreateSocket(nodes.Get(0), UdpSocketFactory::GetTypeId());
        NS_TEST_ASSERT_MSG_EQ(socket0->Bind(InetSocketAddress(interfaces0.GetAddress(0), 0)),
                              0,
                              "Path 0 bind failed");
        sender->AddPath(0, socket0, devices0.Get(0));
        NS_TEST_ASSERT_MSG_EQ(socket0->Connect(InetSocketAddress(interfaces0.GetAddress(1), 9001)),
                              0,
                              "Path 0 connect failed");
        auto socket1 = Socket::CreateSocket(nodes.Get(0), UdpSocketFactory::GetTypeId());
        NS_TEST_ASSERT_MSG_EQ(socket1->Bind(InetSocketAddress(interfaces1.GetAddress(0), 0)),
                              0,
                              "Path 1 bind failed");
        sender->AddPath(1, socket1, devices1.Get(0));
        NS_TEST_ASSERT_MSG_EQ(socket1->Connect(InetSocketAddress(interfaces1.GetAddress(1), 9001)),
                              0,
                              "Path 1 connect failed");
        nodes.Get(0)->AddApplication(sender);
        sender->SetStartTime(MilliSeconds(10));
        sender->SetStopTime(MilliSeconds(500));

        Simulator::Stop(Seconds(1));
        Simulator::Run();
        const uint64_t copyBytes = 2401 + 3 * StreamingHeader::SERIALIZED_SIZE;
        NS_TEST_ASSERT_MSG_EQ(sender->GetPacketsSent(), 6, "Two complete copies were not sent");
        NS_TEST_ASSERT_MSG_EQ(sender->GetPathBytesSent(0), copyBytes, "Path 0 copy is incomplete");
        NS_TEST_ASSERT_MSG_EQ(sender->GetPathBytesSent(1), copyBytes, "Path 1 copy is incomplete");
        NS_TEST_ASSERT_MSG_EQ(sender->GetBytesSent(), 2 * copyBytes, "Total byte count is wrong");
        NS_TEST_ASSERT_MSG_EQ(sender->GetRedundantBytesSent(),
                              copyBytes,
                              "Redundant byte count is wrong");
        NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults().size(), 1, "Union did not complete");
        NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults()[0].incomplete,
                              false,
                              "Duplicated frame union is incomplete");
        NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults()[0].copy0CompletionUs.has_value(),
                              true,
                              "Primary copy completion was not recorded");
        NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults()[0].copy1CompletionUs.has_value(),
                              true,
                              "Secondary copy completion was not recorded");
        NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults()[0].duplicatePacketsReceived,
                              3,
                              "Duplicated packets were not counted");
        Simulator::Destroy();
    }
};

class RandomRateOnOffApplicationTestCase : public TestCase
{
  public:
    RandomRateOnOffApplicationTestCase()
        : TestCase("Random-rate ON/OFF application resamples every ON period")
    {
    }

  private:
    void DoRun() override
    {
        NodeContainer nodes;
        nodes.Create(2);
        InternetStackHelper internet;
        internet.Install(nodes);
        CsmaHelper csma;
        const auto devices = csma.Install(nodes);
        Ipv4AddressHelper address;
        address.SetBase("10.10.0.0", "255.255.255.0");
        const auto interfaces = address.Assign(devices);

        auto application = CreateObject<RandomRateOnOffApplication>();
        application->SetRemote(InetSocketAddress(interfaces.GetAddress(1), 9002));
        application->SetLocal(InetSocketAddress(interfaces.GetAddress(0), 0));
        application->SetPacketSize(200);
        application->SetRateRange(DataRate("1Mbps"), DataRate("50Mbps"));
        application->SetMeans(MilliSeconds(5), MilliSeconds(5));
        NS_TEST_ASSERT_MSG_EQ(application->AssignStreams(71), 3, "Wrong RNG stream count");
        nodes.Get(0)->AddApplication(application);
        application->SetStartTime(Time());
        application->SetStopTime(MilliSeconds(200));

        Simulator::Stop(MilliSeconds(201));
        Simulator::Run();
        const auto& periods = application->GetPeriodRecords();
        NS_TEST_ASSERT_MSG_GT(periods.size(), 2, "Too few ON periods were sampled");
        bool rateChanged = false;
        for (std::size_t i = 0; i < periods.size(); ++i)
        {
            NS_TEST_ASSERT_MSG_EQ(periods[i].rateBps >= 1e6,
                                  true,
                                  "Sampled rate is below minimum");
            NS_TEST_ASSERT_MSG_EQ(periods[i].rateBps <= 50e6,
                                  true,
                                  "Sampled rate is above maximum");
            NS_TEST_ASSERT_MSG_EQ(periods[i].end >= periods[i].start,
                                  true,
                                  "ON period has negative time");
            if (i > 0 && periods[i].rateBps != periods[i - 1].rateBps)
            {
                rateChanged = true;
            }
        }
        NS_TEST_ASSERT_MSG_EQ(rateChanged, true, "Rate was not resampled between ON periods");
        NS_TEST_ASSERT_MSG_GT(application->GetTotalTxBytes(), 0, "No UDP data was sent");
        Simulator::Destroy();
    }
};

class WifiStreamingTestSuite : public TestSuite
{
  public:
    WifiStreamingTestSuite()
        : TestSuite("wifi-streaming", Type::UNIT)
    {
        AddTestCase(new HeaderTestCase, TestCase::Duration::QUICK);
        AddTestCase(new TraceSourceTestCase, TestCase::Duration::QUICK);
        AddTestCase(new PacketizerTestCase, TestCase::Duration::QUICK);
        AddTestCase(new CorrelatedLoadControllerTestCase, TestCase::Duration::QUICK);
        AddTestCase(new CorrelationSanityTestCase, TestCase::Duration::QUICK);
        AddTestCase(new PolicyTestCase, TestCase::Duration::QUICK);
        AddTestCase(new OutputStatisticsTestCase, TestCase::Duration::QUICK);
        AddTestCase(new ReassemblyTestCase, TestCase::Duration::QUICK);
        AddTestCase(new FinalizationTestCase, TestCase::Duration::QUICK);
        AddTestCase(new IntegrationDeliveryTestCase, TestCase::Duration::QUICK);
        AddTestCase(new FullDuplicationDeliveryTestCase, TestCase::Duration::QUICK);
        AddTestCase(new RandomRateOnOffApplicationTestCase, TestCase::Duration::QUICK);
    }
};

static WifiStreamingTestSuite g_wifiStreamingTestSuite;

} // namespace
