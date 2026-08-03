/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "ns3/adaptive-airtime-duplication-controller.h"
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
#include "ns3/mobility-module.h"
#include "ns3/multipath-sender.h"
#include "ns3/prediction-model-evaluator.h"
#include "ns3/prediction-telemetry-collector.h"
#include "ns3/random-variable-stream.h"
#include "ns3/randomized-frame-assignment.h"
#include "ns3/random-rate-on-off-application.h"
#include "ns3/redundancy-policy.h"
#include "ns3/secondary-airtime-meter.h"
#include "ns3/selective-duplication-controller.h"
#include "ns3/simulator.h"
#include "ns3/string.h"
#include "ns3/streaming-frame-tag.h"
#include "ns3/streaming-header.h"
#include "ns3/test.h"
#include "ns3/udp-socket-factory.h"
#include "ns3/wifi-module.h"

#include "prediction-model-golden-v1.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <limits>
#include <sstream>
#include <stdexcept>

namespace ns3
{

/**
 * Test-only access to deterministic prediction accounting callbacks.
 */
class PredictionTelemetryCollectorTestAccess
{
  public:
    static void AddPath(Ptr<PredictionTelemetryCollector> collector, uint8_t pathId)
    {
        collector->m_paths.try_emplace(pathId);
    }

    static void StartPolling(Ptr<PredictionTelemetryCollector> collector, uint8_t pathId)
    {
        collector->m_paths.at(pathId).pollingEvent =
            Simulator::ScheduleNow(&PredictionTelemetryCollector::PollPath,
                                   PeekPointer(collector),
                                   pathId);
    }

    static uint64_t GetPollingCaptureCount(Ptr<PredictionTelemetryCollector> collector,
                                           uint8_t pathId)
    {
        return collector->m_paths.at(pathId).pollingCaptureCount;
    }

    static std::vector<uint64_t> GetPollingCaptureTimes(
        Ptr<PredictionTelemetryCollector> collector,
        uint8_t pathId)
    {
        std::vector<uint64_t> times;
        for (const auto& report : collector->m_paths.at(pathId).pollingReports)
        {
            times.push_back(report.captureTimeNs);
        }
        return times;
    }

    static std::optional<PredictionPollingReport> SelectPollingReport(
        Ptr<PredictionTelemetryCollector> collector,
        uint8_t pathId,
        uint64_t sampleTimeNs)
    {
        const auto* report =
            collector->SelectPollingReport(collector->m_paths.at(pathId), sampleTimeNs);
        return report ? std::optional<PredictionPollingReport>(*report) : std::nullopt;
    }

    static void Enqueue(Ptr<PredictionTelemetryCollector> collector,
                        uint8_t pathId,
                        Ptr<const WifiMpdu> mpdu)
    {
        collector->NotifyQueueEnqueue(pathId, mpdu);
    }

    static void Dequeue(Ptr<PredictionTelemetryCollector> collector,
                        uint8_t pathId,
                        Ptr<const WifiMpdu> mpdu)
    {
        collector->NotifyQueueDequeue(pathId, mpdu);
    }

    static void Attempt(Ptr<PredictionTelemetryCollector> collector,
                        uint8_t pathId,
                        Ptr<const WifiMpdu> mpdu)
    {
        collector->NotifyPhyTxBegin(pathId, mpdu->GetPacket());
    }

    static void Timeout(Ptr<PredictionTelemetryCollector> collector,
                        uint8_t pathId,
                        Ptr<const WifiMpdu> mpdu)
    {
        collector->NotifyResponseTimeout(pathId, mpdu);
    }

    static void Nack(Ptr<PredictionTelemetryCollector> collector,
                     uint8_t pathId,
                     Ptr<const WifiMpdu> mpdu)
    {
        collector->NotifyNackedMpdu(pathId, mpdu);
    }

    static void Ack(Ptr<PredictionTelemetryCollector> collector,
                    uint8_t pathId,
                    Ptr<const WifiMpdu> mpdu)
    {
        collector->NotifyAckedMpdu(pathId, mpdu);
    }

    static void Drop(Ptr<PredictionTelemetryCollector> collector,
                     uint8_t pathId,
                     WifiMacDropReason reason,
                     Ptr<const WifiMpdu> mpdu)
    {
        collector->NotifyDroppedMpdu(pathId, reason, mpdu);
    }

    static std::array<uint64_t, 12> GetCounts(Ptr<PredictionTelemetryCollector> collector,
                                              const StreamingFrameTag& tag)
    {
        const auto& path = collector->m_paths.at(tag.pathId);
        const auto& frame = collector->m_frames.at(PredictionTelemetryCollector::MakeKey(tag));
        return {path.mpduAttempts,
                path.mpduPositiveAcks,
                path.mpduAttemptFailures,
                path.mpduRetries,
                path.mpduTerminalDrops,
                path.mpduRetryLimitDrops,
                path.mpduLifetimeDrops,
                path.mpduQueueDrops,
                frame.mpduAttemptFailures,
                frame.packetsTxSucceeded,
                frame.packetsTerminallyDropped,
                path.queueEntries.size()};
    }

    static void AcknowledgePacket(Ptr<PredictionTelemetryCollector> collector,
                                  const PredictionFrameKey& key,
                                  uint32_t packetIndex)
    {
        auto& frame = collector->m_frames.at(key);
        auto& packet = frame.packets.at(packetIndex);
        if (!packet.acknowledged)
        {
            packet.acknowledged = true;
            ++frame.packetsTxSucceeded;
            frame.senderMacComplete = frame.packetsTxSucceeded == frame.packets.size();
        }
    }

    static std::array<uint64_t, 4> GetAttemptLedger(
        Ptr<PredictionTelemetryCollector> collector,
        uint8_t pathId)
    {
        const auto& path = collector->m_paths.at(pathId);
        uint64_t unresolved = 0;
        for (const auto& [key, frame] : collector->m_frames)
        {
            if (key.pathId != pathId)
            {
                continue;
            }
            unresolved += std::count_if(
                frame.packets.begin(),
                frame.packets.end(),
                [](const PredictionTelemetryCollector::PacketState& packet) {
                    return packet.attemptPending;
                });
        }
        return {path.mpduAttempts,
                path.mpduAttemptSuccesses,
                path.mpduAttemptFailures,
                unresolved};
    }

    static std::array<double, 2> GetLastPositiveAckLatencies(
        Ptr<PredictionTelemetryCollector> collector,
        uint8_t pathId)
    {
        const auto& event = collector->m_paths.at(pathId).macEvents.back();
        return {*event.queueToAckUs, *event.firstAttemptToAckUs};
    }

    static std::array<uint64_t, 3> GetPendingState(
        Ptr<PredictionTelemetryCollector> collector,
        const StreamingFrameTag& tag)
    {
        const auto& frame = collector->m_frames.at(PredictionTelemetryCollector::MakeKey(tag));
        uint64_t unacknowledgedBytes = 0;
        uint64_t pendingBytes = 0;
        uint64_t pendingPackets = 0;
        for (const auto& packet : frame.packets)
        {
            if (!packet.acknowledged)
            {
                unacknowledgedBytes += packet.macServiceBytes.value_or(0);
            }
            if (!packet.acknowledged && !packet.terminallyDropped)
            {
                ++pendingPackets;
                pendingBytes += packet.macServiceBytes.value_or(0);
            }
        }
        return {pendingPackets, unacknowledgedBytes, pendingBytes};
    }

    static std::size_t PruneAndGetMacHistorySize(
        Ptr<PredictionTelemetryCollector> collector,
        uint8_t pathId,
        uint64_t nowNs)
    {
        auto& path = collector->m_paths.at(pathId);
        collector->PruneHistories(path, nowNs);
        return path.macEvents.size();
    }

    static double Percentile(std::vector<double> values, double probability)
    {
        return PredictionTelemetryCollector::Percentile(std::move(values), probability);
    }

    static PredictionRollingSample BuildBoundaryWindow(
        Ptr<PredictionTelemetryCollector> collector,
        uint8_t pathId)
    {
        auto& path = collector->m_paths.at(pathId);
        constexpr uint64_t nowNs = 2000000;
        constexpr uint64_t lowerNs = 1000000;
        path.telemetryStartNs = nowNs;
        path.macEvents.clear();
        path.macEvents.push_back({lowerNs,
                                  PredictionTelemetryCollector::MacEventKind::POSITIVE_ACK,
                                  false,
                                  100,
                                  1.0,
                                  1.0});
        path.macEvents.push_back({lowerNs + 1,
                                  PredictionTelemetryCollector::MacEventKind::POSITIVE_ACK,
                                  false,
                                  100,
                                  10.0,
                                  20.0});
        path.macEvents.push_back({nowNs,
                                  PredictionTelemetryCollector::MacEventKind::POSITIVE_ACK,
                                  false,
                                  100,
                                  30.0,
                                  40.0});
        path.macEvents.push_back({nowNs + 1,
                                  PredictionTelemetryCollector::MacEventKind::POSITIVE_ACK,
                                  false,
                                  100,
                                  50.0,
                                  60.0});
        return collector->BuildRollingSample(path, nowNs, 1000);
    }

    static void InitializePhyHistory(Ptr<PredictionTelemetryCollector> collector,
                                     uint8_t pathId,
                                     WifiPhyState state)
    {
        auto& path = collector->m_paths.at(pathId);
        path.telemetryStartNs = Simulator::Now().GetNanoSeconds();
        path.latestFeatureEventTimeNs =
            static_cast<uint64_t>(std::max<int64_t>(path.telemetryStartNs, 0));
        path.latestFeatureEventSequence = ++collector->m_featureEventSequence;
        path.phyIntervals.clear();
        path.phyIntervalSerial = 0;
        path.phyIntervals.push_back({path.telemetryStartNs,
                                     std::numeric_limits<int64_t>::max(),
                                     state,
                                     *path.latestFeatureEventTimeNs,
                                     path.phyIntervalSerial++});
    }

    static void PhyActivity(Ptr<PredictionTelemetryCollector> collector,
                            uint8_t pathId,
                            WifiPhyState state,
                            Time duration)
    {
        collector->NotifyPhyActivity(pathId, state, duration);
    }

    static void PhyStateTrace(Ptr<PredictionTelemetryCollector> collector,
                              uint8_t pathId,
                              Time start,
                              Time duration,
                              WifiPhyState state)
    {
        collector->NotifyPhyState(pathId, start, duration, state);
    }

    static PredictionRollingSample BuildPhyWindow(
        Ptr<PredictionTelemetryCollector> collector,
        uint8_t pathId,
        uint64_t nowNs)
    {
        return collector->BuildRollingSample(collector->m_paths.at(pathId), nowNs, 1000);
    }

    static std::string MakeSupportMask(bool wifiBound, bool oracleSupported)
    {
        return PredictionTelemetryCollector::MakeSupportMask(wifiBound, oracleSupported);
    }
};

/** Test-only access to deterministic secondary-airtime settlement callbacks. */
class SecondaryAirtimeMeterTestAccess
{
  public:
    /**
     * Mark one packet terminal through the production de-duplication path.
     *
     * @param meter Meter under test.
     * @param frameId Application frame identifier.
     * @param packetIndex Packet index within the copy.
     */
    static void Terminal(Ptr<SecondaryAirtimeMeter> meter,
                         uint64_t frameId,
                         uint32_t packetIndex)
    {
        meter->MarkPacketTerminal(frameId, packetIndex);
    }
};

/** Test-only access to adaptive-airtime bucket initialization. */
class AdaptiveAirtimeDuplicationControllerTestAccess
{
  public:
    /**
     * Initialize a controller bucket at a deterministic timestamp.
     *
     * @param controller Controller under test.
     * @param nowNs Initialization time in nanoseconds.
     */
    static void Initialize(Ptr<AdaptiveAirtimeDuplicationController> controller,
                           uint64_t nowNs)
    {
        controller->InitializeBucket(nowNs);
    }

    /**
     * Refill a controller bucket at a deterministic timestamp.
     *
     * @param controller Controller under test.
     * @param nowNs Refill time in nanoseconds.
     */
    static void Refill(Ptr<AdaptiveAirtimeDuplicationController> controller,
                       uint64_t nowNs)
    {
        controller->RefillBucket(nowNs);
    }

    /**
     * Apply a shadow-price update at a deterministic timestamp.
     *
     * @param controller Controller under test.
     * @param nowNs Update time in nanoseconds.
     */
    static void UpdateShadowPrice(Ptr<AdaptiveAirtimeDuplicationController> controller,
                                  uint64_t nowNs)
    {
        controller->UpdateShadowPrice(nowNs);
    }

    /**
     * Set measured airtime pending the next shadow-price update.
     *
     * @param controller Controller under test.
     * @param measuredUs Pending measured airtime in microseconds.
     */
    static void SetMeasuredSinceLastT0Us(
        Ptr<AdaptiveAirtimeDuplicationController> controller,
        double measuredUs)
    {
        controller->m_measuredSinceLastT0Us = measuredUs;
    }

    /**
     * Set retry inflation for deterministic admission-versus-reservation checks.
     *
     * @param controller Controller under test.
     * @param inflation Retry inflation multiplier.
     */
    static void SetRetryInflation(Ptr<AdaptiveAirtimeDuplicationController> controller,
                                  double inflation)
    {
        controller->m_retryInflation = inflation;
    }

    /**
     * Return the maximum token balance.
     *
     * @param controller Controller under test.
     * @return Bucket capacity in microseconds.
     */
    static double GetCapacityUs(Ptr<AdaptiveAirtimeDuplicationController> controller)
    {
        return controller->m_bucketCapacityUs;
    }

    /**
     * Resolve the effective admission price for one decision offset.
     *
     * @param controller Controller under test.
     * @param offsetUs Decision offset in microseconds.
     * @return Effective admission price.
     */
    static double ResolveDecisionShadowPrice(
        Ptr<AdaptiveAirtimeDuplicationController> controller,
        uint64_t offsetUs)
    {
        return controller->ResolveDecisionShadowPrice(offsetUs);
    }

    /**
     * Check frame-type admission through the production restriction.
     *
     * @param controller Controller under test.
     * @param offsetUs Decision offset in microseconds.
     * @param frameType Candidate frame type.
     * @return True when the frame type is eligible.
     */
    static bool IsFrameTypeEligible(
        Ptr<AdaptiveAirtimeDuplicationController> controller,
        uint64_t offsetUs,
        FrameType frameType)
    {
        PredictionSample sample;
        sample.sampleOffsetUs = offsetUs;
        sample.frameType = frameType;
        return controller->IsFrameTypeEligible(sample);
    }

    /**
     * Check one maximum/initial horizon pair using the production guard.
     *
     * @param bucketHorizonUs Maximum-balance horizon in microseconds.
     * @param initialHorizonUs Initial-credit horizon in microseconds.
     * @return True when the pair is valid.
     */
    static bool AreHorizonsValid(uint64_t bucketHorizonUs, uint64_t initialHorizonUs)
    {
        return AdaptiveAirtimeDuplicationController::AreBucketHorizonsValid(
            bucketHorizonUs,
            initialHorizonUs);
    }
};

} // namespace ns3

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
                    uint64_t generationTimeNs = 0,
                    uint16_t flags = 0)
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
    header.flags = flags;
    auto packet = Create<Packet>(100);
    packet->AddHeader(header);
    return packet;
}

class FrameTagTestCase : public TestCase
{
  public:
    FrameTagTestCase()
        : TestCase("StreamingFrameTag serialization, copying, and printing")
    {
    }

  private:
    void DoRun() override
    {
        StreamingFrameTag source;
        source.frameId = 0x1122334455667788;
        source.pathId = 3;
        source.copyId = 1;
        source.packetIndex = 4;
        source.packetCount = 9;
        source.generationTimeNs = 123456789;
        source.deadlineTimeNs = 156789789;
        source.frameSizeBytes = 54321;
        source.frameType = FrameType::I_FRAME;

        auto packet = Create<Packet>(100);
        const uint32_t wireSize = packet->GetSize();
        packet->AddPacketTag(source);
        NS_TEST_ASSERT_MSG_EQ(packet->GetSize(), wireSize, "Packet tag changed wire bytes");

        StreamingFrameTag decoded;
        NS_TEST_ASSERT_MSG_EQ(packet->PeekPacketTag(decoded), true, "Packet tag is absent");
        NS_TEST_ASSERT_MSG_EQ(decoded.frameId, source.frameId, "Frame ID changed");
        NS_TEST_ASSERT_MSG_EQ(decoded.pathId, source.pathId, "Path ID changed");
        NS_TEST_ASSERT_MSG_EQ(decoded.copyId, source.copyId, "Copy ID changed");
        NS_TEST_ASSERT_MSG_EQ(decoded.packetIndex, source.packetIndex, "Packet index changed");
        NS_TEST_ASSERT_MSG_EQ(decoded.packetCount, source.packetCount, "Packet count changed");
        NS_TEST_ASSERT_MSG_EQ(decoded.generationTimeNs,
                              source.generationTimeNs,
                              "Generation time changed");
        NS_TEST_ASSERT_MSG_EQ(decoded.deadlineTimeNs,
                              source.deadlineTimeNs,
                              "Deadline time changed");
        NS_TEST_ASSERT_MSG_EQ(decoded.frameSizeBytes,
                              source.frameSizeBytes,
                              "Frame size changed");
        NS_TEST_ASSERT_MSG_EQ(static_cast<uint8_t>(decoded.frameType),
                              static_cast<uint8_t>(source.frameType),
                              "Frame type changed");
        NS_TEST_ASSERT_MSG_EQ(decoded.IsValid(), true, "Valid tag was rejected");

        auto copy = packet->Copy();
        StreamingFrameTag copied;
        NS_TEST_ASSERT_MSG_EQ(copy->PeekPacketTag(copied), true, "Copied packet lost tag");
        NS_TEST_ASSERT_MSG_EQ(copied.frameId, source.frameId, "Copied tag changed identity");

        std::ostringstream printed;
        decoded.Print(printed);
        NS_TEST_ASSERT_MSG_EQ(printed.str().find("frame=1234605616436508552") !=
                                  std::string::npos,
                              true,
                              "Printed tag omits frame identity");
    }
};

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

        auto gop = CreateObject<SyntheticFrameSource>();
        gop->SetFps(30);
        gop->SetDuration(MilliSeconds(200));
        gop->SetConstantFrameSize(12000);
        gop->SetGopLength(3);
        gop->SetKeyframeSizeMultiplier(4);
        const auto gopFrames = gop->GetFrames();
        NS_TEST_ASSERT_MSG_EQ(gopFrames.size(), 6, "Wrong GOP frame count");
        for (std::size_t i = 0; i < gopFrames.size(); ++i)
        {
            const bool keyframe = i % 3 == 0;
            NS_TEST_ASSERT_MSG_EQ(
                static_cast<uint8_t>(gopFrames[i].frameType),
                static_cast<uint8_t>(keyframe ? FrameType::I_FRAME : FrameType::P_FRAME),
                "Wrong GOP frame type");
            NS_TEST_ASSERT_MSG_EQ(gopFrames[i].frameSizeBytes,
                                  keyframe ? 48000 : 12000,
                                  "Wrong GOP frame size");
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
                packetizer.SetExpectedMacServiceOverhead(36);
        const auto plan = packetizer.Plan(frame, 42, 1, 3);
        NS_TEST_ASSERT_MSG_EQ(plan.frame.packetCount, 3, "Plan has wrong packet count");
        NS_TEST_ASSERT_MSG_EQ(plan.packets.size(), 3, "Plan has wrong packet vector size");
        NS_TEST_ASSERT_MSG_EQ(plan.packets[0].applicationPayloadBytes,
                              1000,
                              "Plan has wrong full payload");
        NS_TEST_ASSERT_MSG_EQ(plan.packets[2].applicationPayloadBytes,
                              501,
                              "Plan has wrong final payload");
                NS_TEST_ASSERT_MSG_EQ(plan.packets[0].expectedMacServiceBytes.has_value(),
                                      true,
                                      "Plan omitted full MAC service size");
                NS_TEST_ASSERT_MSG_EQ(*plan.packets[0].expectedMacServiceBytes,
                                      1086,
                                      "Plan has wrong full MAC service size");
                NS_TEST_ASSERT_MSG_EQ(plan.packets[2].expectedMacServiceBytes.has_value(),
                                      true,
                                      "Plan omitted final MAC service size");
                NS_TEST_ASSERT_MSG_EQ(*plan.packets[2].expectedMacServiceBytes,
                                      587,
                                      "Plan has wrong final MAC service size");
        NS_TEST_ASSERT_MSG_EQ(plan.packets[1].offset,
                              MilliSeconds(1),
                              "Plan has wrong emission offset");
        const auto emissions = packetizer.Materialize(plan);
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
        NS_TEST_ASSERT_MSG_EQ(emissions[2].frameTag.packetIndex,
                              2,
                              "Materialization changed packet index");
        NS_TEST_ASSERT_MSG_EQ(emissions[2].frameTag.deadlineTimeNs,
                              5000123,
                              "Materialization changed absolute deadline");
        NS_TEST_ASSERT_MSG_EQ(emissions[2].frameTag.IsValid(),
                              true,
                              "Materialized frame tag is invalid");

        const auto reverseTail = FramePacketizer::SelectReverseTail(plan, 2);
        NS_TEST_ASSERT_MSG_EQ(reverseTail.frame.packetCount,
                              3,
                              "Reverse tail changed total frame packet count");
        NS_TEST_ASSERT_MSG_EQ(reverseTail.packets.size(),
                              2,
                              "Reverse tail selected the wrong packet count");
        NS_TEST_ASSERT_MSG_EQ(reverseTail.packets[0].packetIndex,
                              2,
                              "Reverse tail did not start at the final packet");
        NS_TEST_ASSERT_MSG_EQ(reverseTail.packets[1].packetIndex,
                              1,
                              "Reverse tail did not descend by packet index");
        NS_TEST_ASSERT_MSG_EQ(reverseTail.packets[0].offset,
                              Time(),
                              "Reverse tail did not rebase its first emission");
        NS_TEST_ASSERT_MSG_EQ(reverseTail.packets[1].offset,
                              MilliSeconds(1),
                              "Reverse tail did not preserve packet pacing");
        const auto tailEmissions = packetizer.Materialize(reverseTail);
        NS_TEST_ASSERT_MSG_EQ(tailEmissions[0].frameTag.packetIndex,
                              2,
                              "Materialization renumbered a projected packet");
        StreamingHeader tailHeader;
        tailEmissions[0].packet->PeekHeader(tailHeader);
        NS_TEST_ASSERT_MSG_EQ(tailHeader.packetCount,
                              3,
                              "Projected packet lost total frame cardinality");
        NS_TEST_ASSERT_MSG_EQ(reverseTail.packets[0].applicationPayloadBytes,
                              501,
                              "Reverse tail detached the short final payload");
        NS_TEST_ASSERT_MSG_EQ(*reverseTail.packets[0].expectedMacServiceBytes,
                              587,
                              "Reverse tail detached final-packet service bytes");

        const auto fullReverse = FramePacketizer::SelectReverseTail(plan, 3);
        NS_TEST_ASSERT_MSG_EQ(fullReverse.packets[0].packetIndex,
                              2,
                              "Full reverse plan has the wrong first packet");
        NS_TEST_ASSERT_MSG_EQ(fullReverse.packets[2].packetIndex,
                              0,
                              "Full reverse plan has the wrong final packet");
        const auto explicitSelection =
            FramePacketizer::SelectPackets(plan, std::vector<uint32_t>{2, 0});
        NS_TEST_ASSERT_MSG_EQ(explicitSelection.packets.size(),
                              2,
                              "Explicit projection selected the wrong count");
        NS_TEST_ASSERT_MSG_EQ(explicitSelection.packets[0].packetIndex,
                              2,
                              "Explicit projection changed launch order");
        NS_TEST_ASSERT_MSG_EQ(explicitSelection.packets[1].packetIndex,
                              0,
                              "Explicit projection changed a noncontiguous index");
    }
};

class PredictionCollectorFoundationTestCase : public TestCase
{
  public:
    PredictionCollectorFoundationTestCase()
        : TestCase("Prediction collector synchronous T0 and causal event ordering")
    {
    }

  private:
    void DoRun() override
    {
        FramePacketizer packetizer;
        packetizer.SetPayloadSize(1000);
        const FrameDescriptor frame{17, 0, 2500, 0, 5000, FrameType::P_FRAME};
        const auto plan = packetizer.Plan(frame, 91, 0, 1);
        const auto emissions = packetizer.Materialize(plan);

        auto collector = CreateObject<PredictionTelemetryCollector>();
        collector->SetRunId("prediction-foundation");
        collector->SetSampleOffsetsUs({0, 1000, 2000});
        NS_TEST_ASSERT_MSG_EQ(
            PredictionTelemetryCollectorTestAccess::MakeSupportMask(false, false),
            "0x0",
            "Unbound support mask is not canonical");
        const uint64_t wifiMask = std::stoull(
            PredictionTelemetryCollectorTestAccess::MakeSupportMask(true, false),
            nullptr,
            16);
        for (uint32_t bit = 0; bit <= 60; ++bit)
        {
            const bool expected = bit <= 16 || (bit >= 18 && bit <= 53);
            NS_TEST_ASSERT_MSG_EQ(bool(wifiMask & (1ULL << bit)),
                                  expected,
                                  "Per-field Wi-Fi support bit is incorrect");
        }
        const uint64_t oracleMask = std::stoull(
            PredictionTelemetryCollectorTestAccess::MakeSupportMask(true, true),
            nullptr,
            16);
        for (const uint32_t bit : {54U, 56U, 57U, 58U, 59U})
        {
            NS_TEST_ASSERT_MSG_EQ(bool(oracleMask & (1ULL << bit)),
                                  true,
                                  "Supported oracle bit is clear");
        }
        for (const uint32_t bit : {17U, 55U, 60U})
        {
            NS_TEST_ASSERT_MSG_EQ(bool(oracleMask & (1ULL << bit)),
                                  false,
                                  "Unsupported field bit is set");
        }

        // This event is inserted before RegisterFrame schedules the T1
        // snapshot and must therefore be visible at T1.
        Simulator::Schedule(MicroSeconds(1000),
                            &PredictionTelemetryCollector::RecordPacketSubmitted,
                            PeekPointer(collector),
                            emissions[0].frameTag,
                            emissions[0].packet->GetSize());
        collector->RegisterFrame(plan);
        NS_TEST_ASSERT_MSG_EQ(collector->GetSamples().size(),
                              1,
                              "T0 was not captured synchronously");
        NS_TEST_ASSERT_MSG_EQ(collector->GetSamples()[0].sampleStage,
                              "T0",
                              "Synchronous sample is not T0");
        NS_TEST_ASSERT_MSG_EQ(collector->GetSamples()[0].packetsSubmitted,
                              0,
                              "T0 observed a scheduled submission");
        NS_TEST_ASSERT_MSG_EQ(collector->GetSamples()[0].packetsRemainingToSubmit,
                              3,
                              "T0 did not use the packetization plan");
        NS_TEST_ASSERT_MSG_EQ(collector->GetSamples()[0].latestFeatureEventSequence,
                              0,
                              "T0 empty watermark has a nonzero sequence");
        NS_TEST_ASSERT_MSG_EQ(
            collector->GetSamples()[0].latestFeatureEventTimeNs.has_value(),
            false,
            "T0 empty watermark has a timestamp");

        // This event is inserted after the T1 snapshot and must not be visible
        // until T2, despite sharing the T1 timestamp.
        Simulator::Schedule(MicroSeconds(1000),
                            &PredictionTelemetryCollector::RecordPacketSubmitted,
                            PeekPointer(collector),
                            emissions[1].frameTag,
                            emissions[1].packet->GetSize());
        Simulator::Stop(MicroSeconds(2000));
        Simulator::Run();

        const auto& samples = collector->GetSamples();
        NS_TEST_ASSERT_MSG_EQ(samples.size(), 3, "Configured snapshots are missing");
        NS_TEST_ASSERT_MSG_EQ(samples[1].sampleStage, "T1", "Wrong T1 stage name");
        NS_TEST_ASSERT_MSG_EQ(samples[1].packetsSubmitted,
                              1,
                              "T1 included an event processed after its callback");
        NS_TEST_ASSERT_MSG_EQ(samples[1].latestFeatureEventTimeNs.has_value(),
                              true,
                              "T1 feature timestamp is absent");
        NS_TEST_ASSERT_MSG_EQ(*samples[1].latestFeatureEventTimeNs,
                              1000000,
                              "T1 feature timestamp is wrong");
        NS_TEST_ASSERT_MSG_EQ(samples[1].latestFeatureEventSequence,
                              2,
                              "T1 feature sequence is wrong");
        NS_TEST_ASSERT_MSG_EQ(samples[2].packetsSubmitted,
                              2,
                              "T2 omitted a prior same-timestamp event");
        NS_TEST_ASSERT_MSG_EQ(samples[2].packetsRemainingToSubmit,
                              1,
                              "T2 remaining packet count is wrong");
        NS_TEST_ASSERT_MSG_EQ(samples[2].applicationSocketPacketBytesSubmitted,
                              emissions[0].packet->GetSize() +
                                  emissions[1].packet->GetSize(),
                              "Submitted byte accounting is wrong");
        NS_TEST_ASSERT_MSG_EQ(*samples[2].latestFeatureEventTimeNs <=
                                  samples[2].sampleTimeNs,
                              true,
                              "Snapshot contains a future feature event");
        NS_TEST_ASSERT_MSG_EQ(samples[2].latestFeatureEventSequence,
                              3,
                              "T2 feature sequence is wrong");
        NS_TEST_ASSERT_MSG_EQ(samples[2].deadlineSlackUs, 3000, "Deadline slack is wrong");
        NS_TEST_ASSERT_MSG_EQ(samples[2].actionable, true, "Incomplete frame is not actionable");
        Simulator::Destroy();
    }
};

class PredictionPollingTestCase : public TestCase
{
  public:
    PredictionPollingTestCase()
        : TestCase("Prediction polling is frame-independent, delayed, and latest-report")
    {
    }

  private:
    void DoRun() override
    {
        auto collector = CreateObject<PredictionTelemetryCollector>();
        collector->SetSampleOffsetsUs({0, 1000, 2000});
        collector->SetPollingIntervalUs(1000);
        collector->SetPollingReportDelayUs(1000);
        const auto samplesFile = CreateTempDirFilename("polling-frame-samples.csv");
        const auto pollingFile = CreateTempDirFilename("prediction-polling-samples.csv");
        collector->SetOutputFiles(samplesFile, "", pollingFile);
        PredictionTelemetryCollectorTestAccess::AddPath(collector, 0);
        PredictionTelemetryCollectorTestAccess::InitializePhyHistory(collector,
                                                                    0,
                                                                    WifiPhyState::IDLE);

        std::optional<PredictionPollingReport> beforeAvailability;
        std::optional<PredictionPollingReport> firstAvailable;
        std::optional<PredictionPollingReport> stillFirst;
        std::optional<PredictionPollingReport> secondAvailable;
        std::optional<PredictionPollingReport> newestAtThreeAndHalf;
        PredictionTelemetryCollectorTestAccess::StartPolling(collector, 0);
        FramePacketizer packetizer;
        packetizer.SetPayloadSize(1000);
        const FrameDescriptor frame{99, 0, 1000, 0, 5000, FrameType::P_FRAME};
        collector->RegisterFrame(packetizer.Plan(frame, 1, 0, 0));
        NS_TEST_ASSERT_MSG_EQ(collector->GetSamples().front().pollingReport.has_value(),
                              false,
                              "Synchronous T0 snapshot exposed a report before its delay");
        Simulator::Schedule(MicroSeconds(999), [&]() {
            beforeAvailability =
                PredictionTelemetryCollectorTestAccess::SelectPollingReport(collector, 0, 999000);
        });
        Simulator::Schedule(MicroSeconds(1000), [&]() {
            firstAvailable =
                PredictionTelemetryCollectorTestAccess::SelectPollingReport(collector, 0, 1000000);
        });
        Simulator::Schedule(MicroSeconds(1999), [&]() {
            stillFirst =
                PredictionTelemetryCollectorTestAccess::SelectPollingReport(collector, 0, 1999000);
        });
        Simulator::Schedule(MicroSeconds(2000), [&]() {
            secondAvailable =
                PredictionTelemetryCollectorTestAccess::SelectPollingReport(collector, 0, 2000000);
        });
        Simulator::Schedule(MicroSeconds(3500), [&]() {
            newestAtThreeAndHalf =
                PredictionTelemetryCollectorTestAccess::SelectPollingReport(collector, 0, 3500000);
        });
        Simulator::Stop(MicroSeconds(3500));
        Simulator::Run();

        NS_TEST_ASSERT_MSG_EQ(beforeAvailability.has_value(),
                              false,
                              "Startup incorrectly exposed an unavailable T0 report");
        NS_TEST_ASSERT_MSG_EQ(firstAvailable.has_value(),
                              true,
                              "The time-zero poll was not available after 1 ms");
        NS_TEST_ASSERT_MSG_EQ(firstAvailable->captureTimeNs,
                              0,
                              "The first report was not captured at simulation time zero");
        NS_TEST_ASSERT_MSG_EQ(firstAvailable->availableTimeNs,
                              1000000,
                              "The first report did not model the 1 ms delay");
        NS_TEST_ASSERT_MSG_EQ(stillFirst->captureTimeNs,
                              0,
                              "An unavailable newer report replaced the retained report");
        NS_TEST_ASSERT_MSG_EQ(secondAvailable->captureTimeNs,
                              1000000,
                              "The newest available report was not selected at 2 ms");
        NS_TEST_ASSERT_MSG_EQ(newestAtThreeAndHalf->captureTimeNs,
                              2000000,
                              "Latest-report selection did not honor availability");
        NS_TEST_ASSERT_MSG_EQ(
            PredictionTelemetryCollectorTestAccess::GetPollingCaptureCount(collector, 0),
            4,
            "Polling did not run at 0, 1, 2, and 3 ms without any frames");
        const auto retained =
            PredictionTelemetryCollectorTestAccess::GetPollingCaptureTimes(collector, 0);
        NS_TEST_ASSERT_MSG_EQ(retained.back(), 3000000, "Polling cadence drifted from 1 ms");
        const auto& samples = collector->GetSamples();
        NS_TEST_ASSERT_MSG_EQ(samples.size(), 3, "Frame snapshots were not all captured");
        NS_TEST_ASSERT_MSG_EQ(samples[1].pollingReport->captureTimeNs,
                              0,
                              "T1 snapshot did not attach the time-zero report");
        NS_TEST_ASSERT_MSG_EQ(samples[2].pollingReport->captureTimeNs,
                              1000000,
                              "T2 snapshot did not attach the newest available report");
        collector->WriteOutputs();
        std::ifstream pollingInput(pollingFile);
        std::string pollingHeader;
        std::getline(pollingInput, pollingHeader);
        NS_TEST_ASSERT_MSG_NE(
            pollingHeader.find("capture_time_ns,available_time_ns,staleness_us"),
            std::string::npos,
            "Polling sidecar omitted timing metadata");
        NS_TEST_ASSERT_MSG_NE(pollingHeader.find("mpdu_attempts_20ms"),
                              std::string::npos,
                              "Polling sidecar omitted configured rolling F1 fields");
        Simulator::Destroy();
    }
};

class PredictionPhyHistoryTestCase : public TestCase
{
  public:
    PredictionPhyHistoryTestCase()
        : TestCase("Prediction PHY histories reconcile causal activity intervals")
    {
    }

  private:
    void DoRun() override
    {
        auto collector = CreateObject<PredictionTelemetryCollector>();
        PredictionTelemetryCollectorTestAccess::AddPath(collector, 0);
        PredictionTelemetryCollectorTestAccess::AddPath(collector, 1);
        PredictionTelemetryCollectorTestAccess::InitializePhyHistory(
            collector,
            0,
            WifiPhyState::IDLE);
        PredictionTelemetryCollectorTestAccess::InitializePhyHistory(
            collector,
            1,
            WifiPhyState::IDLE);

        // CCA is known at its start and resumes after an overlapping local TX.
        Simulator::Schedule(
            MicroSeconds(100),
            &PredictionTelemetryCollectorTestAccess::PhyActivity,
            collector,
            0,
            WifiPhyState::CCA_BUSY,
            MicroSeconds(200));
        Simulator::Schedule(
            MicroSeconds(150),
            &PredictionTelemetryCollectorTestAccess::PhyActivity,
            collector,
            0,
            WifiPhyState::TX,
            MicroSeconds(100));

        // RX starts with a predicted duration, then an authoritative state trace
        // truncates it when a local TX interrupts reception.
        Simulator::Schedule(
            MicroSeconds(100),
            &PredictionTelemetryCollectorTestAccess::PhyActivity,
            collector,
            1,
            WifiPhyState::RX,
            MicroSeconds(300));
        Simulator::Schedule(
            MicroSeconds(200),
            &PredictionTelemetryCollectorTestAccess::PhyStateTrace,
            collector,
            1,
            MicroSeconds(100),
            MicroSeconds(100),
            WifiPhyState::RX);
        Simulator::Schedule(
            MicroSeconds(200),
            &PredictionTelemetryCollectorTestAccess::PhyActivity,
            collector,
            1,
            WifiPhyState::TX,
            MicroSeconds(100));

        Simulator::Stop(MicroSeconds(500));
        Simulator::Run();

        const auto ccaWindow =
            PredictionTelemetryCollectorTestAccess::BuildPhyWindow(collector, 0, 500000);
        NS_TEST_ASSERT_MSG_EQ_TOL(ccaWindow.historyCoverageUs,
                                  500,
                                  1e-9,
                                  "CCA history coverage is incorrect");
        NS_TEST_ASSERT_MSG_EQ_TOL(ccaWindow.phyIdleTimeUs,
                                  300,
                                  1e-9,
                                  "CCA history idle duration is incorrect");
        NS_TEST_ASSERT_MSG_EQ_TOL(ccaWindow.phyBusyTimeUs,
                                  100,
                                  1e-9,
                                  "Overlapping TX did not mask CCA");
        NS_TEST_ASSERT_MSG_EQ_TOL(ccaWindow.phyTxTimeUs,
                                  100,
                                  1e-9,
                                  "CCA history TX duration is incorrect");

        const auto rxWindow =
            PredictionTelemetryCollectorTestAccess::BuildPhyWindow(collector, 1, 500000);
        NS_TEST_ASSERT_MSG_EQ_TOL(rxWindow.phyIdleTimeUs,
                                  300,
                                  1e-9,
                                  "Corrected RX history idle duration is incorrect");
        NS_TEST_ASSERT_MSG_EQ_TOL(rxWindow.phyRxTimeUs,
                                  100,
                                  1e-9,
                                  "Authoritative trace did not truncate RX");
        NS_TEST_ASSERT_MSG_EQ_TOL(rxWindow.phyTxTimeUs,
                                  100,
                                  1e-9,
                                  "Corrected RX history TX duration is incorrect");
        Simulator::Destroy();
    }
};

class PredictionMpduAccountingTestCase : public TestCase
{
  public:
    PredictionMpduAccountingTestCase()
        : TestCase("Prediction MPDU retry de-duplication and terminal accounting")
    {
    }

  private:
    void DoRun() override
    {
        FramePacketizer packetizer;
        packetizer.SetPayloadSize(1000);
        const FrameDescriptor frame{21, 0, 4000, 0, 5000, FrameType::P_FRAME};
        const auto plan = packetizer.Plan(frame, 27, 0, 0);
        auto emissions = packetizer.Materialize(plan);

        auto collector = CreateObject<PredictionTelemetryCollector>();
        collector->SetRunId("prediction-mpdu-accounting");
        collector->SetSampleOffsetsUs({0});
        const auto samplesFile = CreateTempDirFilename("prediction-accounting-samples.csv");
        const auto eventsFile = CreateTempDirFilename("prediction-accounting-events.csv");
        collector->SetOutputFiles(samplesFile, eventsFile);
        collector->RegisterFrame(plan);
        PredictionTelemetryCollectorTestAccess::AddPath(collector, 0);

        WifiMacHeader header;
        header.SetType(WIFI_MAC_QOSDATA);
        header.SetAddr1(Mac48Address("00:00:00:00:00:01"));
        header.SetAddr2(Mac48Address("00:00:00:00:00:02"));
        header.SetQosTid(0);
        std::array<Ptr<WifiMpdu>, 4> mpdus;
        for (std::size_t index = 0; index < emissions.size(); ++index)
        {
            emissions[index].packet->AddPacketTag(emissions[index].frameTag);
            mpdus[index] = Create<WifiMpdu>(emissions[index].packet, header);
            collector->RecordPacketSubmitted(emissions[index].frameTag,
                                             emissions[index].packet->GetSize());
        }

        Simulator::Schedule(MicroSeconds(100),
                            &PredictionTelemetryCollectorTestAccess::Enqueue,
                            collector,
                            0,
                            mpdus[0]);
        Simulator::Schedule(MicroSeconds(100),
                            &PredictionTelemetryCollectorTestAccess::Attempt,
                            collector,
                            0,
                            mpdus[0]);
        Simulator::Schedule(MicroSeconds(200),
                            &PredictionTelemetryCollectorTestAccess::Timeout,
                            collector,
                            0,
                            mpdus[0]);
        // The NACK callback reports the same failed exchange and must not
        // increment the failure count a second time.
        Simulator::Schedule(MicroSeconds(200),
                            &PredictionTelemetryCollectorTestAccess::Nack,
                            collector,
                            0,
                            mpdus[0]);
        Simulator::Schedule(MicroSeconds(300),
                            &PredictionTelemetryCollectorTestAccess::Attempt,
                            collector,
                            0,
                            mpdus[0]);
        Simulator::Schedule(MicroSeconds(400),
                            &PredictionTelemetryCollectorTestAccess::Ack,
                            collector,
                            0,
                            mpdus[0]);
        Simulator::Schedule(MicroSeconds(400),
                            &PredictionTelemetryCollectorTestAccess::Dequeue,
                            collector,
                            0,
                            mpdus[0]);
        Simulator::Schedule(MicroSeconds(100),
                            &PredictionTelemetryCollectorTestAccess::Enqueue,
                            collector,
                            0,
                            mpdus[1]);
        Simulator::Schedule(MicroSeconds(110),
                            &PredictionTelemetryCollectorTestAccess::Attempt,
                            collector,
                            0,
                            mpdus[1]);
        Simulator::Schedule(MicroSeconds(210),
                            &PredictionTelemetryCollectorTestAccess::Timeout,
                            collector,
                            0,
                            mpdus[1]);
        // A later explicit BAR can positively acknowledge the timed-out data
        // attempt. It is a success outcome, but not a second PHY attempt.
        Simulator::Schedule(MicroSeconds(250),
                            &PredictionTelemetryCollectorTestAccess::Ack,
                            collector,
                            0,
                            mpdus[1]);
        Simulator::Schedule(MicroSeconds(250),
                            &PredictionTelemetryCollectorTestAccess::Dequeue,
                            collector,
                            0,
                            mpdus[1]);
        Simulator::Schedule(MicroSeconds(100),
                            &PredictionTelemetryCollectorTestAccess::Enqueue,
                            collector,
                            0,
                            mpdus[2]);
        // Some queue-removal paths emit Dequeue before DroppedMpdu.
        Simulator::Schedule(MicroSeconds(250),
                            &PredictionTelemetryCollectorTestAccess::Dequeue,
                            collector,
                            0,
                            mpdus[2]);
        Simulator::Schedule(MicroSeconds(250),
                            &PredictionTelemetryCollectorTestAccess::Drop,
                            collector,
                            0,
                            WIFI_MAC_DROP_EXPIRED_LIFETIME,
                            mpdus[2]);
        Simulator::Schedule(MicroSeconds(100),
                            &PredictionTelemetryCollectorTestAccess::Enqueue,
                            collector,
                            0,
                            mpdus[3]);
        Simulator::Schedule(MicroSeconds(120),
                            &PredictionTelemetryCollectorTestAccess::Attempt,
                            collector,
                            0,
                            mpdus[3]);
        Simulator::Schedule(MicroSeconds(220),
                            &PredictionTelemetryCollectorTestAccess::Timeout,
                            collector,
                            0,
                            mpdus[3]);
        Simulator::Schedule(MicroSeconds(250),
                            &PredictionTelemetryCollectorTestAccess::Drop,
                            collector,
                            0,
                            WIFI_MAC_DROP_EXPIRED_LIFETIME,
                            mpdus[3]);
        // Other paths emit DroppedMpdu before Dequeue.
        Simulator::Schedule(MicroSeconds(250),
                            &PredictionTelemetryCollectorTestAccess::Dequeue,
                            collector,
                            0,
                            mpdus[3]);
        // A positive ACK after terminal removal changes the current packet state
        // without reversing either the failed attempt or terminal-drop event.
        Simulator::Schedule(MicroSeconds(300),
                            &PredictionTelemetryCollectorTestAccess::Ack,
                            collector,
                            0,
                            mpdus[3]);
        Simulator::Stop(MicroSeconds(275));
        Simulator::Run();
        const auto beforeLateAck =
            PredictionTelemetryCollectorTestAccess::GetCounts(collector,
                                                               emissions[0].frameTag);
        NS_TEST_ASSERT_MSG_EQ(beforeLateAck[4],
                              2,
                              "Terminal-drop event count before late ACK is incorrect");
        NS_TEST_ASSERT_MSG_EQ(beforeLateAck[9],
                              1,
                              "Positive-ACK state before late ACK is incorrect");
        NS_TEST_ASSERT_MSG_EQ(beforeLateAck[10],
                              2,
                              "Current terminal state before late ACK is incorrect");

        Simulator::Stop(MicroSeconds(75));
        Simulator::Run();
        const auto afterLateAck =
            PredictionTelemetryCollectorTestAccess::GetCounts(collector,
                                                               emissions[0].frameTag);
        NS_TEST_ASSERT_MSG_EQ(afterLateAck[4],
                              2,
                              "Late ACK reversed the terminal-drop event count");
        NS_TEST_ASSERT_MSG_EQ(afterLateAck[9],
                              2,
                              "Late ACK did not increase the positive-ACK state");
        NS_TEST_ASSERT_MSG_EQ(afterLateAck[10],
                              1,
                              "Late ACK did not decrease the current terminal state");

        Simulator::Stop(MicroSeconds(150));
        Simulator::Run();

        const auto counts =
            PredictionTelemetryCollectorTestAccess::GetCounts(collector,
                                                               emissions[0].frameTag);
        NS_TEST_ASSERT_MSG_EQ(counts[0], 4, "Incorrect attempt count");
        NS_TEST_ASSERT_MSG_EQ(counts[1], 3, "Late positive acknowledgements were not counted");
        NS_TEST_ASSERT_MSG_EQ(counts[2], 3, "Duplicate failure callback was counted");
        NS_TEST_ASSERT_MSG_EQ(counts[3], 1, "Retry was not identified");
        NS_TEST_ASSERT_MSG_EQ(counts[4], 2, "Terminal drops were not counted");
        NS_TEST_ASSERT_MSG_EQ(counts[5], 0, "Retry-limit drop was invented");
        NS_TEST_ASSERT_MSG_EQ(counts[6], 2, "Lifetime drops were not classified");
        NS_TEST_ASSERT_MSG_EQ(counts[7], 0, "Queue drop was invented");
        NS_TEST_ASSERT_MSG_EQ(counts[8], 3, "Frame failure count is incorrect");
        NS_TEST_ASSERT_MSG_EQ(counts[9], 3, "Distinct frame success count is incorrect");
        NS_TEST_ASSERT_MSG_EQ(counts[10],
                              1,
                              "Late ACK did not leave one current terminal packet");
        NS_TEST_ASSERT_MSG_EQ(counts[11], 0, "Dropped packet remained in the queue");
        const auto attemptLedger =
            PredictionTelemetryCollectorTestAccess::GetAttemptLedger(collector, 0);
        NS_TEST_ASSERT_MSG_EQ(attemptLedger[0],
                              attemptLedger[1] + attemptLedger[2] + attemptLedger[3],
                              "Attempt outcomes do not conserve transmitted MPDUs");
        NS_TEST_ASSERT_MSG_EQ(attemptLedger[1],
                              1,
                              "Late positive ACK was misclassified as attempt success");

        const auto latencies =
            PredictionTelemetryCollectorTestAccess::GetLastPositiveAckLatencies(collector, 0);
        NS_TEST_ASSERT_MSG_EQ_TOL(latencies[0],
                                  300,
                                  1e-9,
                                  "Queue-to-ACK latency is incorrect");
        NS_TEST_ASSERT_MSG_EQ_TOL(latencies[1],
                                  300,
                                  1e-9,
                                  "First-attempt-to-ACK latency is incorrect");
        const auto pending =
            PredictionTelemetryCollectorTestAccess::GetPendingState(collector,
                                                                     emissions[0].frameTag);
        NS_TEST_ASSERT_MSG_EQ(pending[0], 0, "Terminal work remains pending on primary");
        NS_TEST_ASSERT_MSG_EQ(pending[1],
                              mpdus[2]->GetPacketSize(),
                              "Unacknowledged bytes excluded the terminal drop");
        NS_TEST_ASSERT_MSG_EQ(pending[2],
                              0,
                              "Primary-pending bytes included the terminal drop");
        const auto unacknowledged = collector->GetUnacknowledgedPacketIndices(
            PredictionFrameKey{frame.frameId, plan.pathId, plan.copyId});
        NS_TEST_ASSERT_MSG_EQ(unacknowledged.has_value(),
                              true,
                              "Registered frame has no unacknowledged-index view");
        NS_TEST_ASSERT_MSG_EQ(unacknowledged->size(),
                              1,
                              "Unacknowledged-index view has the wrong cardinality");
        NS_TEST_ASSERT_MSG_EQ(unacknowledged->front(),
                              2,
                              "Terminally dropped packet disappeared from the deficit");
        NS_TEST_ASSERT_MSG_EQ_TOL(
            PredictionTelemetryCollectorTestAccess::Percentile({0, 10, 20, 30}, 0.95),
            28.5,
            1e-9,
            "Rolling percentile does not use type-7 interpolation");
        const auto boundary =
            PredictionTelemetryCollectorTestAccess::BuildBoundaryWindow(collector, 0);
        NS_TEST_ASSERT_MSG_EQ(boundary.mpduPositiveAcks,
                              2,
                              "Rolling window did not use (t - w, t]");
        NS_TEST_ASSERT_MSG_EQ(boundary.acknowledgedMacServiceBytes,
                              200,
                              "ACK-timestamp byte accounting is incorrect");
        NS_TEST_ASSERT_MSG_EQ_TOL(*boundary.mpduQueueToAckP95Us,
                                  29,
                                  1e-9,
                                  "Rolling latency P95 is incorrect");
        NS_TEST_ASSERT_MSG_EQ(boundary.mpduRetryRatio.has_value(),
                              false,
                              "Zero-attempt retry ratio is not null");
        NS_TEST_ASSERT_MSG_EQ(
            PredictionTelemetryCollectorTestAccess::PruneAndGetMacHistorySize(collector,
                                                                               0,
                                                                               30000000),
            0,
            "MAC event history was not bounded by the configured window");
        collector->WriteOutputs();
        std::ifstream events(eventsFile);
        std::string line;
        std::getline(events, line);
        uint32_t positiveAckRows = 0;
        uint32_t attemptSuccessRows = 0;
        uint32_t lateAckRows = 0;
        while (std::getline(events, line))
        {
            std::stringstream stream(line);
            std::vector<std::string> fields;
            std::string field;
            while (std::getline(stream, field, ','))
            {
                fields.push_back(field);
            }
            if (fields.size() > 10 && fields[4] == "MPDU_POSITIVE_ACK")
            {
                ++positiveAckRows;
                if (fields[10] == "1")
                {
                    ++attemptSuccessRows;
                    NS_TEST_ASSERT_MSG_EQ(fields[9].empty(),
                                          false,
                                          "Attempt-success ACK omitted attempt number");
                }
                else
                {
                    ++lateAckRows;
                    NS_TEST_ASSERT_MSG_EQ(fields[9].empty(),
                                          true,
                                          "Late ACK retained an attempt number");
                }
            }
        }
        NS_TEST_ASSERT_MSG_EQ(positiveAckRows, 3, "Positive-ACK event count is incorrect");
        NS_TEST_ASSERT_MSG_EQ(attemptSuccessRows,
                              1,
                              "Attempt-success finalizer count is incorrect");
        NS_TEST_ASSERT_MSG_EQ(lateAckRows, 2, "Late positive-ACK count is incorrect");
        Simulator::Destroy();
    }
};

class CorrelatedLoadControllerTestCase : public TestCase
{
  public:
    CorrelatedLoadControllerTestCase()
        : TestCase("Deterministic common and local transitions and replay")
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

/**
 * Pin the splitmix64_v1 tuple fold and top-53-bit conversion.
 */
class RandomizedFrameAssignmentGoldenTestCase : public TestCase
{
  public:
    RandomizedFrameAssignmentGoldenTestCase()
        : TestCase("Randomized frame assignment matches splitmix64_v1 golden vectors")
    {
    }

  private:
    void DoRun() override
    {
        struct GoldenCase
        {
            uint64_t salt;                ///< Experiment salt.
            uint64_t seed;                ///< Experiment seed.
            uint64_t run;                 ///< Experiment run.
            uint64_t frameId;             ///< Frame identifier.
            uint64_t rawDraw;             ///< Expected full-width draw.
            double unitDraw;              ///< Expected unit draw.
            RandomizedExplorationArm arm; ///< Expected arm for pT2=0.2 and pT4=0.5.
            double armProbability;        ///< Expected assigned-arm probability.
        };

        constexpr std::array<GoldenCase, 7> goldenCases{{
            {0x0ULL,
             0x0ULL,
             0x0ULL,
             0x0ULL,
             0x2130748aaac80268ULL,
             0x1.0983a45556400p-3,
             RandomizedExplorationArm::FULL_COPY_T2,
             0.2},
            {0x1ULL,
             0x0ULL,
             0x0ULL,
             0x0ULL,
             0xe28195ddd9ee4956ULL,
             0x1.c5032bbbb3dc9p-1,
             RandomizedExplorationArm::CONTROL,
             0.3},
            {0x0ULL,
             0x1ULL,
             0x0ULL,
             0x0ULL,
             0xd1c0270687984b37ULL,
             0x1.a3804e0d0f309p-1,
             RandomizedExplorationArm::CONTROL,
             0.3},
            {0x0ULL,
             0x0ULL,
             0x1ULL,
             0x0ULL,
             0xd9eef7f073d37c42ULL,
             0x1.b3ddefe0e7a6fp-1,
             RandomizedExplorationArm::CONTROL,
             0.3},
            {0x0ULL,
             0x0ULL,
             0x0ULL,
             0x1ULL,
             0x2a4f111b3be57715ULL,
             0x1.527888d9df2b8p-3,
             RandomizedExplorationArm::FULL_COPY_T2,
             0.2},
            {0xdecafbad12345678ULL,
             123ULL,
             17ULL,
             999999ULL,
             0xc55c5329fe091c3bULL,
             0x1.8ab8a653fc123p-1,
             RandomizedExplorationArm::CONTROL,
             0.3},
            {0xffffffffffffffffULL,
             0xffffffffffffffffULL,
             0xffffffffffffffffULL,
             0xffffffffffffffffULL,
             0x9c666618c8f279d7ULL,
             0x1.38cccc3191e4fp-1,
             RandomizedExplorationArm::FULL_COPY_T4,
             0.5},
        }};

        NS_TEST_ASSERT_MSG_EQ(RandomizedFrameAssignment::GetAlgorithmId(),
                              "splitmix64_v1",
                              "Assignment algorithm provenance changed");
        for (const auto& golden : goldenCases)
        {
            const auto assignment = RandomizedFrameAssignment::Assign(golden.salt,
                                                                      golden.seed,
                                                                      golden.run,
                                                                      golden.frameId,
                                                                      0.2,
                                                                      0.5);
            NS_TEST_ASSERT_MSG_EQ(assignment.rawDraw,
                                  golden.rawDraw,
                                  "SplitMix64 tuple fold changed");
            NS_TEST_ASSERT_MSG_EQ_TOL(assignment.unitDraw,
                                      golden.unitDraw,
                                      0.0,
                                      "Top-53-bit conversion changed");
            NS_TEST_ASSERT_MSG_EQ(static_cast<uint8_t>(assignment.arm),
                                  static_cast<uint8_t>(golden.arm),
                                  "Golden vector selected the wrong arm");
            NS_TEST_ASSERT_MSG_EQ_TOL(assignment.armProbability,
                                      golden.armProbability,
                                      1e-15,
                                      "Golden vector reported the wrong propensity");
        }
    }
};

/**
 * Verify half-open arm boundaries and probability validation.
 */
class RandomizedFrameAssignmentBoundaryTestCase : public TestCase
{
  public:
    RandomizedFrameAssignmentBoundaryTestCase()
        : TestCase("Randomized frame assignment validates probabilities and arm boundaries")
    {
    }

  private:
    void DoRun() override
    {
        const auto baseline = RandomizedFrameAssignment::Assign(0, 0, 0, 0, 0.0, 0.0);
        NS_TEST_ASSERT_MSG_EQ(static_cast<uint8_t>(baseline.arm),
                              static_cast<uint8_t>(RandomizedExplorationArm::CONTROL),
                              "Zero intervention probability did not select control");
        NS_TEST_ASSERT_MSG_EQ(baseline.unitDraw >= 0.0 && baseline.unitDraw < 1.0,
                              true,
                              "Unit draw is outside [0, 1)");

        auto boundary = RandomizedFrameAssignment::Assign(0,
                                                           0,
                                                           0,
                                                           0,
                                                           baseline.unitDraw,
                                                           0.5);
        NS_TEST_ASSERT_MSG_EQ(static_cast<uint8_t>(boundary.arm),
                              static_cast<uint8_t>(RandomizedExplorationArm::FULL_COPY_T4),
                              "T2 upper boundary was not assigned to T4");

        boundary = RandomizedFrameAssignment::Assign(0,
                                                      0,
                                                      0,
                                                      0,
                                                      std::nextafter(baseline.unitDraw, 1.0),
                                                      0.5);
        NS_TEST_ASSERT_MSG_EQ(static_cast<uint8_t>(boundary.arm),
                              static_cast<uint8_t>(RandomizedExplorationArm::FULL_COPY_T2),
                              "Draw immediately below the T2 boundary did not select T2");

        boundary = RandomizedFrameAssignment::Assign(0,
                                                      0,
                                                      0,
                                                      0,
                                                      0.0,
                                                      baseline.unitDraw);
        NS_TEST_ASSERT_MSG_EQ(static_cast<uint8_t>(boundary.arm),
                              static_cast<uint8_t>(RandomizedExplorationArm::CONTROL),
                              "T4 upper boundary was not assigned to control");

        boundary = RandomizedFrameAssignment::Assign(0,
                                                      0,
                                                      0,
                                                      0,
                                                      0.0,
                                                      std::nextafter(baseline.unitDraw, 1.0));
        NS_TEST_ASSERT_MSG_EQ(static_cast<uint8_t>(boundary.arm),
                              static_cast<uint8_t>(RandomizedExplorationArm::FULL_COPY_T4),
                              "Draw immediately below the T4 boundary did not select T4");

        NS_TEST_ASSERT_MSG_EQ(
            static_cast<uint8_t>(RandomizedFrameAssignment::Assign(0, 0, 0, 0, 1.0, 0.0).arm),
            static_cast<uint8_t>(RandomizedExplorationArm::FULL_COPY_T2),
            "Probability-one T2 boundary was rejected");
        NS_TEST_ASSERT_MSG_EQ(
            static_cast<uint8_t>(RandomizedFrameAssignment::Assign(0, 0, 0, 0, 0.0, 1.0).arm),
            static_cast<uint8_t>(RandomizedExplorationArm::FULL_COPY_T4),
            "Probability-one T4 boundary was rejected");

        const double infinity = std::numeric_limits<double>::infinity();
        const double nan = std::numeric_limits<double>::quiet_NaN();
        const std::array<std::array<double, 2>, 10> invalidProbabilities{{
            {{-0.1, 0.0}},
            {{0.0, -0.1}},
            {{0.8, 0.3}},
            {{1.1, 0.0}},
            {{0.0, 1.1}},
            {{infinity, 0.0}},
            {{0.0, infinity}},
            {{nan, 0.0}},
            {{0.0, nan}},
            {{0.0, -infinity}},
        }};
        for (const auto& probabilities : invalidProbabilities)
        {
            bool rejected = false;
            try
            {
                RandomizedFrameAssignment::Assign(0,
                                                  0,
                                                  0,
                                                  0,
                                                  probabilities[0],
                                                  probabilities[1]);
            }
            catch (const std::invalid_argument&)
            {
                rejected = true;
            }
            NS_TEST_ASSERT_MSG_EQ(rejected, true, "Invalid exploration probabilities accepted");
        }
    }
};

/**
 * Verify deterministic assignment without consuming ns-3 RNG streams.
 */
class RandomizedFrameAssignmentDeterminismTestCase : public TestCase
{
  public:
    RandomizedFrameAssignmentDeterminismTestCase()
        : TestCase("Randomized frame assignment is deterministic and stream-independent")
    {
    }

  private:
    void DoRun() override
    {
        const auto first =
            RandomizedFrameAssignment::Assign(0x123456789abcdef0ULL, 9, 41, 73, 0.2, 0.5);
        const auto repeated =
            RandomizedFrameAssignment::Assign(0x123456789abcdef0ULL, 9, 41, 73, 0.2, 0.5);
        NS_TEST_ASSERT_MSG_EQ(first.rawDraw, repeated.rawDraw, "Repeated assignment changed");
        NS_TEST_ASSERT_MSG_EQ_TOL(first.unitDraw,
                                  repeated.unitDraw,
                                  0.0,
                                  "Repeated unit draw changed");
        NS_TEST_ASSERT_MSG_EQ(static_cast<uint8_t>(first.arm),
                              static_cast<uint8_t>(repeated.arm),
                              "Repeated arm assignment changed");

        auto observedStream = CreateObject<UniformRandomVariable>();
        auto referenceStream = CreateObject<UniformRandomVariable>();
        observedStream->SetStream(741);
        referenceStream->SetStream(741);
        NS_TEST_ASSERT_MSG_EQ_TOL(observedStream->GetValue(),
                                  referenceStream->GetValue(),
                                  0.0,
                                  "Explicit RNG streams did not begin identically");
        for (uint64_t frameId = 0; frameId < 1000; ++frameId)
        {
            const auto assignment =
                RandomizedFrameAssignment::Assign(0x123456789abcdef0ULL,
                                                  9,
                                                  41,
                                                  frameId,
                                                  0.2,
                                                  0.5);
            NS_TEST_ASSERT_MSG_EQ(assignment.unitDraw >= 0.0 && assignment.unitDraw < 1.0,
                                  true,
                                  "Generated unit draw is outside [0, 1)");
        }
        NS_TEST_ASSERT_MSG_EQ_TOL(observedStream->GetValue(),
                                  referenceStream->GetValue(),
                                  0.0,
                                  "Frame assignment consumed an ns-3 RNG stream");
    }
};

/**
 * Verify the distinct delayed-primary policy contract and provenance.
 */
class RandomizedFullCopyExplorationPolicyTestCase : public TestCase
{
  public:
    RandomizedFullCopyExplorationPolicyTestCase()
        : TestCase("Randomized full-copy exploration policy preserves delayed provenance")
    {
    }

  private:
    void DoRun() override
    {
        const FrameDescriptor frame{1, 0, 1200, 1, 10000, FrameType::P_FRAME};
        LinkTelemetrySnapshot telemetry;
        telemetry.pathScores.emplace(0, 7.25);

        auto policy = CreateObject<RandomizedFullCopyExplorationPolicy>();
        policy->SetPrimaryPath(0);
        const auto decision = policy->Decide(frame, telemetry);
        NS_TEST_ASSERT_MSG_EQ(decision.primaryPath, 0, "Exploration policy chose wrong primary");
        NS_TEST_ASSERT_MSG_EQ(decision.duplicate,
                              false,
                              "Exploration policy duplicated before its assigned delay");
        NS_TEST_ASSERT_MSG_EQ(decision.secondaryPath.has_value(),
                              false,
                              "Exploration policy selected an immediate secondary");
        NS_TEST_ASSERT_MSG_EQ_TOL(decision.primaryScore,
                                  7.25,
                                  0.0,
                                  "Exploration policy lost primary telemetry");
        NS_TEST_ASSERT_MSG_EQ(decision.reason,
                              "randomized delayed full-copy exploration primary",
                              "Exploration decision provenance changed");
        NS_TEST_ASSERT_MSG_EQ(policy->GetName(),
                              "randomized_full_copy_exploration",
                              "Exploration policy name changed");
        NS_TEST_ASSERT_MSG_EQ(policy->GetInstanceTypeId().GetName(),
                              "ns3::RandomizedFullCopyExplorationPolicy",
                              "Exploration policy TypeId changed");
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

        MloRuntimeInfo mloRuntime;
        mloRuntime.mode = "EMLSR";
        mloRuntime.profile = "advanced_sta_ap_fixed_aux_v4";
        mloRuntime.stationEmlsrActivated = true;
        mloRuntime.apEmlsrActivated = true;
        mloRuntime.emlsrManager = "ns3::AdvancedEmlsrManager";
        mloRuntime.apEmlsrManager = "ns3::AdvancedApEmlsrManager";
        mloRuntime.emlsrLinkIds = {0, 1};
        mloRuntime.apEmlsrEnabledPerLink = {true, true};
        mloRuntime.mainPhyId = 1;
        mloRuntime.initialMainPhyLinkId = 1;
        mloRuntime.initialMainPhyBand = "5 GHz";
        mloRuntime.mainPhyFrequencyRanges = {"WIFI_SPECTRUM_2_4_GHZ",
                                              "WIFI_SPECTRUM_5_GHZ"};
        mloRuntime.successfulMpdusPerLink = {4, 5};
        mloRuntime.phyTxTimeUsPerLink = {40, 50};
        ExperimentOutput::WriteMloRuntime(directory, mloRuntime);
        std::ifstream mloRuntimeFile(directory + "/mlo_runtime.json");
        std::ostringstream mloRuntimeText;
        mloRuntimeText << mloRuntimeFile.rdbuf();
        NS_TEST_ASSERT_MSG_NE(mloRuntimeText.str().find(
                                  "\"emlsr_manager\": \"ns3::AdvancedEmlsrManager\""),
                              std::string::npos,
                              "EMLSR runtime manager is missing");
        NS_TEST_ASSERT_MSG_NE(
            mloRuntimeText.str().find(
                "\"ap_emlsr_manager\": \"ns3::AdvancedApEmlsrManager\""),
            std::string::npos,
            "AP EMLSR runtime manager is missing");
        NS_TEST_ASSERT_MSG_NE(mloRuntimeText.str().find(
                                  "\"successful_mpdus_per_link\": [4, 5]"),
                              std::string::npos,
                              "EMLSR runtime per-link activity is missing");

        const std::string adaptiveDirectory = directory + "/adaptive";
        ExperimentOutput::PrepareRunDirectory(adaptiveDirectory);
        StreamingRunConfig adaptiveConfig;
        adaptiveConfig.runId = "adaptive-stages";
        adaptiveConfig.policy = "adaptive_airtime_duplication";
        adaptiveConfig.adaptiveAirtimeBudgetFraction = 0.05;
        adaptiveConfig.adaptiveAirtimeBucketHorizonUs = 200000;
        adaptiveConfig.adaptiveAirtimeInitialBucketHorizonUs = 100000;
        adaptiveConfig.adaptiveAirtimeAdmissionUsesRetryInflation = false;
        adaptiveConfig.adaptiveAirtimeDecisionOffsetsUs = {0, 4000};
        adaptiveConfig.adaptiveAirtimeDecisionOffsetShadowPrices = {
            {0, 0.034},
            {4000, 0.059723},
        };
        adaptiveConfig.adaptiveAirtimeIFrameOnlyDecisionOffsetsUs = {0};
        ExperimentOutput::WriteResolvedConfig(adaptiveDirectory, adaptiveConfig);
        std::ifstream adaptiveResolved(adaptiveDirectory + "/resolved_config.json");
        std::ostringstream adaptiveText;
        adaptiveText << adaptiveResolved.rdbuf();
        NS_TEST_ASSERT_MSG_NE(adaptiveText.str().find(
                                  "\"stages\": [\"T0\", \"T4\"]"),
                              std::string::npos,
                              "Adaptive resolved stages do not follow configured offsets");
        NS_TEST_ASSERT_MSG_NE(
            adaptiveText.str().find(
                "\"score_kind\": \"weighted_head_probability_admission_score\""),
            std::string::npos,
            "Adaptive T4 score semantics are missing");
        NS_TEST_ASSERT_MSG_NE(adaptiveText.str().find(
                                  "\"initial_bucket_horizon_us\": 100000"),
                              std::string::npos,
                              "Adaptive resolved initial horizon is missing");
        NS_TEST_ASSERT_MSG_NE(adaptiveText.str().find(
                                  "\"initial_bucket_capacity_us\": 5000"),
                              std::string::npos,
                              "Adaptive initial capacity does not use its own horizon");
        NS_TEST_ASSERT_MSG_NE(adaptiveText.str().find(
                                  "\"admission_uses_retry_inflation\": false"),
                              std::string::npos,
                              "Adaptive admission inflation mode is missing");
        NS_TEST_ASSERT_MSG_NE(
            adaptiveText.str().find(
                "\"admission_cost_definition\": "
                "\"nominal_estimated_whole_copy_secondary_sender_phy_tx_airtime\""),
            std::string::npos,
            "Adaptive nominal admission cost definition is missing");
        NS_TEST_ASSERT_MSG_NE(
            adaptiveText.str().find(
                "\"reservation_cost_definition\": "
                "\"retry_inflated_estimated_launched_packet_set_"
                "secondary_sender_phy_tx_airtime\""),
            std::string::npos,
            "Adaptive inflated reservation cost definition is missing");
        NS_TEST_ASSERT_MSG_NE(
            adaptiveText.str().find(
                "\"operating_profile\": \"full_forward+whole_copy_priced\""),
            std::string::npos,
            "Adaptive operating profile is missing");
        NS_TEST_ASSERT_MSG_NE(
            adaptiveText.str().find(
                "\"shadow_price_mode\": \"offset_override_with_global_dual_fallback\""),
            std::string::npos,
            "Adaptive offset-price mode is missing");
        NS_TEST_ASSERT_MSG_NE(
            adaptiveText.str().find(
                "\"decision_offset_shadow_prices\": {\"0\": 0.034, \"4000\": 0.059723}"),
            std::string::npos,
            "Adaptive offset prices are missing");
        NS_TEST_ASSERT_MSG_NE(
            adaptiveText.str().find("\"i_frame_only_decision_offsets_us\": [0]"),
            std::string::npos,
            "Adaptive frame-type restriction is missing");

        const std::string deficitDirectory = directory + "/deficit";
        ExperimentOutput::PrepareRunDirectory(deficitDirectory);
        StreamingRunConfig deficitConfig = adaptiveConfig;
        deficitConfig.runId = "adaptive-deficit-stages";
        deficitConfig.policy = "adaptive_deficit_duplication";
        deficitConfig.adaptiveAirtimeAdmissionPacketCost = "whole_copy";
        ExperimentOutput::WriteResolvedConfig(deficitDirectory, deficitConfig);
        std::ifstream deficitResolved(deficitDirectory + "/resolved_config.json");
        std::ostringstream deficitText;
        deficitText << deficitResolved.rdbuf();
        NS_TEST_ASSERT_MSG_NE(deficitText.str().find(
                                  "\"adaptiveDeficitDuplication\": {"),
                              std::string::npos,
                              "Primary-deficit resolved object is missing");
        NS_TEST_ASSERT_MSG_NE(deficitText.str().find(
                                  "\"packet_selection\": "
                                  "\"primary_unacknowledged_reverse\""),
                              std::string::npos,
                              "Primary-deficit packet selection is not recorded");
        NS_TEST_ASSERT_MSG_NE(deficitText.str().find(
                                  "\"packet_selection_feature_set\": "
                                  "\"F2-primary-frame-ack-state\""),
                              std::string::npos,
                              "Primary-deficit F2 dependency is not recorded");
        NS_TEST_ASSERT_MSG_NE(
            deficitText.str().find(
                "\"operating_profile\": "
                "\"primary_unacknowledged+whole_copy_priced\""),
            std::string::npos,
            "Primary-deficit whole-copy-priced profile is missing");

        const std::string selectiveDirectory = directory + "/selective";
        ExperimentOutput::PrepareRunDirectory(selectiveDirectory);
        StreamingRunConfig selectiveConfig;
        selectiveConfig.runId = "selective-stages";
        selectiveConfig.policy = "selective_duplication";
        selectiveConfig.selectiveDuplicationDecisionOffsetsUs = {0, 2500};
        selectiveConfig.adaptiveAirtimeDecisionOffsetsUs = {0, 1000, 2000, 4000};
        ExperimentOutput::WriteResolvedConfig(selectiveDirectory, selectiveConfig);
        std::ifstream selectiveResolved(selectiveDirectory + "/resolved_config.json");
        std::ostringstream selectiveText;
        selectiveText << selectiveResolved.rdbuf();
        NS_TEST_ASSERT_MSG_NE(selectiveText.str().find(
                                  "\"stages\": [\"T0\", \"offset_2500us\"]"),
                              std::string::npos,
                              "Selective resolved stages use the wrong offset list");
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

        collector = CreateObject<MetricsCollector>();
        receiver = CreateObject<FrameReceiver>();
        receiver->SetMetricsCollector(collector);
        receiver->ProcessPacket(MakeStreamingPacket(3, 0, 2, 0, 0));
        receiver->ProcessPacket(MakeStreamingPacket(3,
                                                    1,
                                                    2,
                                                    1,
                                                    1,
                                                    10000,
                                                    0,
                                                    StreamingHeader::FLAG_DUPLICATED_FRAME));
        NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults().empty(),
                              true,
                              "Delayed duplicate union finalized before copy accounting");
        receiver->ProcessPacket(MakeStreamingPacket(3,
                                                    0,
                                                    2,
                                                    1,
                                                    1,
                                                    10000,
                                                    0,
                                                    StreamingHeader::FLAG_DUPLICATED_FRAME));
        receiver->ProcessPacket(MakeStreamingPacket(3, 1, 2, 0, 0));
        NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults().size(),
                              1,
                              "Delayed duplication frame did not finalize");
        NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults().front().copy0CompletionUs.has_value(),
                              true,
                              "Delayed duplication lost primary copy completion");
        NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults().front().copy1CompletionUs.has_value(),
                              true,
                              "Delayed duplication lost secondary copy completion");
        Simulator::Destroy();

        collector = CreateObject<MetricsCollector>();
        receiver = CreateObject<FrameReceiver>();
        receiver->SetMetricsCollector(collector);
        receiver->ProcessPacket(MakeStreamingPacket(4,
                                                    0,
                                                    4,
                                                    0,
                                                    0,
                                                    10000,
                                                    0,
                                                    StreamingHeader::FLAG_DUPLICATED_FRAME));
        receiver->ProcessPacket(MakeStreamingPacket(4,
                                                    1,
                                                    4,
                                                    0,
                                                    0,
                                                    10000,
                                                    0,
                                                    StreamingHeader::FLAG_DUPLICATED_FRAME));
        receiver->ProcessPacket(MakeStreamingPacket(4,
                                                    3,
                                                    4,
                                                    1,
                                                    1,
                                                    10000,
                                                    0,
                                                    StreamingHeader::FLAG_DUPLICATED_FRAME));
        receiver->ProcessPacket(MakeStreamingPacket(4,
                                                    2,
                                                    4,
                                                    1,
                                                    1,
                                                    10000,
                                                    0,
                                                    StreamingHeader::FLAG_DUPLICATED_FRAME));
        NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults().empty(),
                              true,
                              "Partial-copy union finalized before its accounting hold");
        Simulator::Stop(MilliSeconds(11));
        Simulator::Run();
        NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults().size(),
                              1,
                              "Complementary partial copies did not finalize");
        const auto& partialResult = collector->GetFrameResults().front();
        NS_TEST_ASSERT_MSG_EQ(partialResult.incomplete,
                              false,
                              "Complementary packet union was marked incomplete");
        NS_TEST_ASSERT_MSG_EQ(partialResult.unionCompletionUs.has_value(),
                              true,
                              "Complementary packet union has no completion timestamp");
        NS_TEST_ASSERT_MSG_EQ(partialResult.deadlineMiss,
                              false,
                              "Complementary packet union missed its deadline");
        NS_TEST_ASSERT_MSG_EQ(partialResult.uniquePacketsReceived,
                              4,
                              "Complementary packet union lost a packet index");
        NS_TEST_ASSERT_MSG_EQ(partialResult.copy0CompletionUs.has_value(),
                              false,
                              "Partial primary was marked as a complete copy");
        NS_TEST_ASSERT_MSG_EQ(partialResult.copy1CompletionUs.has_value(),
                              false,
                              "Partial secondary was marked as a complete copy");
        NS_TEST_ASSERT_MSG_EQ(partialResult.completionMode,
                              "mixed",
                              "Complementary union did not retain both links");
        Simulator::Destroy();
    }
};

class DelayedSecondaryHoldTestCase : public TestCase
{
  public:
    DelayedSecondaryHoldTestCase()
        : TestCase("Delayed secondary hold keeps primary-only frames open")
    {
    }

  private:
    void DoRun() override
    {
        // Without the hold, primary-only completion finalizes immediately and a
        // later secondary is ignored.
        {
            auto collector = CreateObject<MetricsCollector>();
            auto receiver = CreateObject<FrameReceiver>();
            receiver->SetMetricsCollector(collector);
            receiver->ProcessPacket(MakeStreamingPacket(1, 0, 1, 0, 0, 10000, 0, 0));
            NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults().size(),
                                  1,
                                  "Primary-only frame did not finalize without hold");
            receiver->ProcessPacket(
                MakeStreamingPacket(1, 0, 1, 1, 1, 10000, 0, StreamingHeader::FLAG_DUPLICATED_FRAME));
            NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults().size(),
                                  1,
                                  "Late secondary created a second frame result");
            NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults().front().copy1CompletionUs.has_value(),
                                  false,
                                  "Late secondary was accepted after early finalize");
            Simulator::Destroy();
        }

        // With the hold, primary-only completion waits for the secondary.
        {
            auto collector = CreateObject<MetricsCollector>();
            auto receiver = CreateObject<FrameReceiver>();
            receiver->SetMetricsCollector(collector);
            receiver->SetHoldForDelayedSecondary(true);
            receiver->ProcessPacket(MakeStreamingPacket(2, 0, 1, 0, 0, 10000, 0, 0));
            NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults().size(),
                                  0,
                                  "Primary-only frame finalized despite delayed-secondary hold");
            NS_TEST_ASSERT_MSG_EQ(receiver->GetPendingFrameCount(),
                                  1,
                                  "Held frame is not pending");
            receiver->ProcessPacket(
                MakeStreamingPacket(2, 0, 1, 1, 1, 10000, 0, StreamingHeader::FLAG_DUPLICATED_FRAME));
            NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults().size(),
                                  1,
                                  "Held frame did not finalize after secondary arrival");
            const auto& result = collector->GetFrameResults().front();
            NS_TEST_ASSERT_MSG_EQ(result.copy0CompletionUs.has_value(),
                                  true,
                                  "Primary copy completion missing");
            NS_TEST_ASSERT_MSG_EQ(result.copy1CompletionUs.has_value(),
                                  true,
                                  "Secondary copy completion missing");
            Simulator::Destroy();
        }

        // With the hold and no secondary, the deadline finalizes the frame.
        {
            auto collector = CreateObject<MetricsCollector>();
            auto receiver = CreateObject<FrameReceiver>();
            receiver->SetMetricsCollector(collector);
            receiver->SetHoldForDelayedSecondary(true);
            receiver->ProcessPacket(MakeStreamingPacket(3, 0, 1, 0, 0, 1000, 0, 0));
            NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults().size(),
                                  0,
                                  "Held frame finalized before the deadline");
            Simulator::Stop(MicroSeconds(1000) + NanoSeconds(1));
            Simulator::Run();
            NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults().size(),
                                  1,
                                  "Held frame did not finalize after the deadline boundary");
            NS_TEST_ASSERT_MSG_EQ(
                collector->GetFrameResults().front().copy1CompletionUs.has_value(),
                false,
                "Deadline finalize invented a secondary copy");
            Simulator::Destroy();
        }
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
        Simulator::Schedule(MicroSeconds(100),
                            &FrameReceiver::ProcessPacket,
                            PeekPointer(receiver),
                            MakeStreamingPacket(2, 1, 2, 0, 0, 100));
        receiver->ProcessPacket(MakeStreamingPacket(2, 0, 2, 0, 0, 100));
        Simulator::Stop(MicroSeconds(101));
        Simulator::Run();
        NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults().size(),
                              1,
                              "Exact-deadline frame did not finalize");
        NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults()[0].incomplete,
                              false,
                              "Exact-deadline packet was discarded");
        NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults()[0].deadlineMiss,
                              false,
                              "Exact-deadline completion was marked late");
        Simulator::Destroy();

        collector = CreateObject<MetricsCollector>();
        receiver = CreateObject<FrameReceiver>();
        receiver->SetMetricsCollector(collector);
        Simulator::Schedule(MicroSeconds(100) + NanoSeconds(1),
                            &FrameReceiver::ProcessPacket,
                            PeekPointer(receiver),
                            MakeStreamingPacket(3, 1, 2, 0, 0, 100));
        receiver->ProcessPacket(MakeStreamingPacket(3, 0, 2, 0, 0, 100));
        Simulator::Stop(MicroSeconds(101));
        Simulator::Run();
        NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults().size(),
                              1,
                              "Post-deadline frame did not finalize");
        NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults()[0].incomplete,
                              false,
                              "Post-deadline completion was lost before classification");
        NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults()[0].deadlineMiss,
                              true,
                              "Sub-microsecond-late completion was marked on time");
        Simulator::Destroy();

        collector = CreateObject<MetricsCollector>();
        receiver = CreateObject<FrameReceiver>();
        receiver->SetMetricsCollector(collector);
        receiver->SetCleanupTimeout(MilliSeconds(1));
        receiver->ProcessPacket(MakeStreamingPacket(4, 0, 2, 0, 0, 0));
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
        auto predictionCollector = CreateObject<PredictionTelemetryCollector>();
        predictionCollector->SetSampleOffsetsUs({0, 1000});
        sender->SetFrameSource(source);
        sender->SetPacketPayloadSize(1200);
        sender->SetPolicy(policy);
        sender->SetPredictionTelemetryCollector(predictionCollector);
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
        NS_TEST_ASSERT_MSG_EQ(predictionCollector->GetRegisteredFrameCount(),
                              2,
                              "Sender did not register prediction frame plans");
        NS_TEST_ASSERT_MSG_EQ(predictionCollector->GetSamples().size(),
                              4,
                              "Sender did not produce every configured snapshot");
        NS_TEST_ASSERT_MSG_EQ(predictionCollector->GetSamples()[0].packetsSubmitted,
                              0,
                              "Sender T0 was captured after packet submission");
        NS_TEST_ASSERT_MSG_EQ(collector->GetFrameResults().size(), 2, "Not all frames delivered");
        for (const auto& result : collector->GetFrameResults())
        {
            NS_TEST_ASSERT_MSG_EQ(result.incomplete, false, "Delivered frame is incomplete");
            NS_TEST_ASSERT_MSG_EQ(result.deadlineMiss, false, "Generous deadline was missed");
        }
        Simulator::Destroy();
    }
};

/**
 * Verify opt-in prediction tracking for a canonical delayed secondary plan.
 */
class DelayedSecondaryPredictionTrackingTestCase : public TestCase
{
  public:
    DelayedSecondaryPredictionTrackingTestCase()
        : TestCase("Delayed secondary prediction tracking is paired and opt-in")
    {
    }

  private:
    /**
     * Objects retained for one sender scenario.
     */
    struct Scenario
    {
        Ptr<MultipathSender> sender;                   ///< Sender under test.
        Ptr<PredictionTelemetryCollector> prediction; ///< Prediction collector under test.
    };

    /**
     * Minimal snapshot-callback observation used to pin delivery order.
     */
    struct CallbackObservation
    {
        uint64_t offsetUs{0}; ///< Snapshot offset.
        uint8_t pathId{0};    ///< Snapshot path.
        uint8_t copyId{0};    ///< Snapshot copy.
    };

    /**
     * Record one snapshot callback in delivery order.
     *
     * @param sample Immutable prediction snapshot.
     */
    void RecordSnapshot(const PredictionSample& sample)
    {
        m_callbackOrder.push_back(
            {sample.sampleOffsetUs, sample.key.pathId, sample.key.copyId});
    }

    /**
     * Configure one single-frame two-path sender scenario.
     *
     * @param trackingEnabled Whether delayed secondary prediction tracking is enabled.
     * @param port Receiver UDP port.
     * @return Configured sender and prediction collector.
     */
    Scenario ConfigureScenario(bool trackingEnabled, uint16_t port)
    {
        m_callbackOrder.clear();
        NodeContainer nodes;
        nodes.Create(2);
        InternetStackHelper internet;
        internet.Install(nodes);
        CsmaHelper csma;
        csma.SetChannelAttribute("DataRate", StringValue("100Mbps"));
        csma.SetChannelAttribute("Delay", TimeValue(MicroSeconds(10)));
        const auto devices = csma.Install(nodes);
        Ipv4AddressHelper address;
        address.SetBase("10.31.0.0", "255.255.255.0");
        const auto interfaces = address.Assign(devices);

        auto receiver = CreateObject<FrameReceiver>();
        receiver->SetLocal(InetSocketAddress(Ipv4Address::GetAny(), port));
        receiver->SetHoldForDelayedSecondary(true);
        nodes.Get(1)->AddApplication(receiver);
        receiver->SetStartTime(Time());
        receiver->SetStopTime(MilliSeconds(30));

        auto source = CreateObject<SyntheticFrameSource>();
        source->SetFps(30);
        source->SetDuration(MilliSeconds(1));
        source->SetConstantFrameSize(2501);
        source->SetDeadline(20000);

        auto prediction = CreateObject<PredictionTelemetryCollector>();
        prediction->SetSampleOffsetsUs({0, 2000, 4000});
        prediction->SetSnapshotCallback(
            MakeCallback(&DelayedSecondaryPredictionTrackingTestCase::RecordSnapshot, this));
        PredictionTelemetryCollectorTestAccess::AddPath(prediction, 0);
        PredictionTelemetryCollectorTestAccess::AddPath(prediction, 1);
        PredictionTelemetryCollectorTestAccess::InitializePhyHistory(prediction,
                                                                     0,
                                                                     WifiPhyState::IDLE);
        PredictionTelemetryCollectorTestAccess::InitializePhyHistory(prediction,
                                                                     1,
                                                                     WifiPhyState::IDLE);

        auto policy = CreateObject<SelectiveDuplicationPolicy>();
        policy->SetPrimaryPath(0);
        auto sender = CreateObject<MultipathSender>();
        sender->SetFrameSource(source);
        sender->SetPacketPayloadSize(1000);
        sender->SetExpectedMacServiceOverhead(36);
        sender->SetPolicy(policy);
        sender->SetDelayedSecondaryPath(1);
        if (trackingEnabled)
        {
            // Deliberately enable before attaching the collector to pin
            // order-independent configuration semantics.
            sender->SetDelayedSecondaryPredictionTrackingEnabled(true);
        }
        sender->SetPredictionTelemetryCollector(prediction);
        for (uint8_t path = 0; path < 2; ++path)
        {
            auto socket = Socket::CreateSocket(nodes.Get(0), UdpSocketFactory::GetTypeId());
            NS_ABORT_MSG_IF(socket->Bind(InetSocketAddress(interfaces.GetAddress(0), 0)) != 0,
                            "Delayed-telemetry sender bind failed");
            NS_ABORT_MSG_IF(
                socket->Connect(InetSocketAddress(interfaces.GetAddress(1), port)) != 0,
                "Delayed-telemetry sender connect failed");
            sender->AddPath(path, socket, devices.Get(0));
        }
        nodes.Get(0)->AddApplication(sender);
        sender->SetStartTime(MilliSeconds(10));
        sender->SetStopTime(MilliSeconds(30));
        return {sender, prediction};
    }

    /**
     * Assert primary-before-secondary callback order at T0, T2, and T4.
     *
     * @param samples Captured paired samples.
     */
    void AssertPairedOrder(const std::vector<PredictionSample>& samples)
    {
        constexpr std::array<uint64_t, 6> expectedOffsets{0, 0, 2000, 2000, 4000, 4000};
        constexpr std::array<uint8_t, 6> expectedPaths{0, 1, 0, 1, 0, 1};
        constexpr std::array<uint8_t, 6> expectedCopies{0, 1, 0, 1, 0, 1};
        NS_TEST_ASSERT_MSG_EQ(samples.size(),
                              expectedOffsets.size(),
                              "Paired prediction snapshots are missing");
        if (samples.size() != expectedOffsets.size())
        {
            return;
        }
        for (std::size_t index = 0; index < samples.size(); ++index)
        {
            NS_TEST_ASSERT_MSG_EQ(samples[index].sampleOffsetUs,
                                  expectedOffsets[index],
                                  "Paired snapshot offset order changed");
            NS_TEST_ASSERT_MSG_EQ(samples[index].key.pathId,
                                  expectedPaths[index],
                                  "Primary-before-secondary path order changed");
            NS_TEST_ASSERT_MSG_EQ(samples[index].key.copyId,
                                  expectedCopies[index],
                                  "Primary-before-secondary copy order changed");
        }
        NS_TEST_ASSERT_MSG_EQ(m_callbackOrder.size(),
                              expectedOffsets.size(),
                              "Paired snapshot callbacks are missing");
        if (m_callbackOrder.size() != expectedOffsets.size())
        {
            return;
        }
        for (std::size_t index = 0; index < m_callbackOrder.size(); ++index)
        {
            NS_TEST_ASSERT_MSG_EQ(m_callbackOrder[index].offsetUs,
                                  expectedOffsets[index],
                                  "Paired callback offset order changed");
            NS_TEST_ASSERT_MSG_EQ(m_callbackOrder[index].pathId,
                                  expectedPaths[index],
                                  "Primary-before-secondary callback path order changed");
            NS_TEST_ASSERT_MSG_EQ(m_callbackOrder[index].copyId,
                                  expectedCopies[index],
                                  "Primary-before-secondary callback copy order changed");
        }
    }

    /**
     * Run one scenario through all configured snapshots.
     */
    void RunScenario()
    {
        Simulator::Stop(MilliSeconds(25));
        Simulator::Run();
    }

    void DoRun() override
    {
        // The default keeps delayed launch telemetry entirely unchanged: only
        // the primary is registered, even when the delayed copy launches.
        auto scenario = ConfigureScenario(false, 9031);
        bool fullLaunchAccepted = false;
        Simulator::Schedule(MilliSeconds(11), [&]() {
            fullLaunchAccepted =
                scenario.sender->RequestSecondaryCopy(0, "default untracked launch");
        });
        RunScenario();
        NS_TEST_ASSERT_MSG_EQ(fullLaunchAccepted, true, "Default delayed launch was rejected");
        NS_TEST_ASSERT_MSG_EQ(scenario.prediction->GetRegisteredFrameCount(),
                              1,
                              "Default behavior registered a delayed prediction frame");
        NS_TEST_ASSERT_MSG_EQ(scenario.prediction->GetSamples().size(),
                              3,
                              "Default behavior emitted secondary prediction samples");
        for (const auto& sample : scenario.prediction->GetSamples())
        {
            NS_TEST_ASSERT_MSG_EQ(sample.key.pathId,
                                  0,
                                  "Default prediction sample moved off the primary path");
            NS_TEST_ASSERT_MSG_EQ(sample.key.copyId,
                                  0,
                                  "Default prediction sample changed copy identity");
        }
        NS_TEST_ASSERT_MSG_EQ(scenario.sender->GetPacketsSent(),
                              6,
                              "Default tracked state changed delayed packet transmission");
        Simulator::Destroy();

        // With paired tracking enabled and no intervention, every stage is
        // emitted primary-first and the secondary has path context but no
        // sender, MAC, or PHY frame progress.
        scenario = ConfigureScenario(true, 9032);
        RunScenario();
        NS_TEST_ASSERT_MSG_EQ(scenario.prediction->GetRegisteredFrameCount(),
                              2,
                              "Opt-in did not register both frame copies");
        const auto& idleSamples = scenario.prediction->GetSamples();
        AssertPairedOrder(idleSamples);
        if (idleSamples.size() == 6)
        {
            for (const std::size_t index : {1U, 3U, 5U})
            {
                const auto& sample = idleSamples[index];
                NS_TEST_ASSERT_MSG_EQ(sample.packetsSubmitted,
                                      0,
                                      "Idle delayed copy gained application progress");
                NS_TEST_ASSERT_MSG_EQ(sample.framePacketsMacEnqueued.has_value() &&
                                          *sample.framePacketsMacEnqueued == 0,
                                      true,
                                      "Idle delayed copy gained MAC enqueue progress");
                NS_TEST_ASSERT_MSG_EQ(sample.framePacketsTxSucceeded.has_value() &&
                                          *sample.framePacketsTxSucceeded == 0,
                                      true,
                                      "Idle delayed copy gained MAC success progress");
                NS_TEST_ASSERT_MSG_EQ(sample.mpduTxAttemptsTotal.has_value(),
                                      true,
                                      "Delayed sample lacks its bound path context");
                NS_TEST_ASSERT_MSG_EQ(
                    sample.featureSupportMask,
                    PredictionTelemetryCollectorTestAccess::MakeSupportMask(true, false),
                    "Delayed sample has the wrong bound-path support mask");
            }
        }
        Simulator::Destroy();

        // A full delayed launch updates the already registered canonical copy.
        scenario = ConfigureScenario(true, 9033);
        fullLaunchAccepted = false;
        Simulator::Schedule(MilliSeconds(11), [&]() {
            fullLaunchAccepted =
                scenario.sender->RequestSecondaryCopy(0, "tracked full launch");
        });
        RunScenario();
        NS_TEST_ASSERT_MSG_EQ(fullLaunchAccepted, true, "Tracked full launch was rejected");
        const auto& fullSamples = scenario.prediction->GetSamples();
        AssertPairedOrder(fullSamples);
        if (fullSamples.size() == 6)
        {
            const auto& fullT2 = fullSamples[3];
            NS_TEST_ASSERT_MSG_EQ(fullT2.packetsSubmitted,
                                  3,
                                  "Tracked full launch did not submit every secondary packet");
            NS_TEST_ASSERT_MSG_EQ(fullT2.packetsRemainingToSubmit,
                                  0,
                                  "Tracked full launch retained unsent secondary packets");
            NS_TEST_ASSERT_MSG_EQ(fullT2.applicationSocketPacketBytesSubmitted,
                                  2501 + 3 * StreamingHeader::SERIALIZED_SIZE,
                                  "Tracked full launch recorded the wrong socket bytes");
        }
        Simulator::Destroy();

        // A projected launch records only its original selected packet indexes
        // against the full immutable plan and leaves the remainder unsent.
        scenario = ConfigureScenario(true, 9034);
        bool partialLaunchAccepted = false;
        Simulator::Schedule(MilliSeconds(11), [&]() {
            partialLaunchAccepted = scenario.sender->RequestSecondaryPackets(
                0,
                std::vector<uint32_t>{2, 0},
                "tracked projected launch");
        });
        RunScenario();
        NS_TEST_ASSERT_MSG_EQ(partialLaunchAccepted,
                              true,
                              "Tracked projected launch was rejected");
        const auto& projectedSamples = scenario.prediction->GetSamples();
        AssertPairedOrder(projectedSamples);
        if (projectedSamples.size() == 6)
        {
            const auto& projectedT2 = projectedSamples[3];
            NS_TEST_ASSERT_MSG_EQ(projectedT2.packetsSubmitted,
                                  2,
                                  "Tracked projection submitted the wrong packet count");
            NS_TEST_ASSERT_MSG_EQ(projectedT2.packetsRemainingToSubmit,
                                  1,
                                  "Tracked projection changed canonical frame cardinality");
            NS_TEST_ASSERT_MSG_EQ(projectedT2.applicationSocketPacketBytesSubmitted,
                                  1501 + 2 * StreamingHeader::SERIALIZED_SIZE,
                                  "Tracked projection recorded the wrong socket bytes");
        }
        NS_TEST_ASSERT_MSG_EQ(scenario.sender->GetRedundantBytesSent(),
                              1501 + 2 * StreamingHeader::SERIALIZED_SIZE,
                              "Tracked projection changed redundant byte accounting");
        Simulator::Destroy();
    }

    std::vector<CallbackObservation> m_callbackOrder; ///< Delivered snapshot callbacks.
};

class SelectiveDuplicationControllerTestCase : public TestCase
{
  public:
    SelectiveDuplicationControllerTestCase()
        : TestCase("Selective duplication enforces causal frame-token budget")
    {
    }

  private:
    double Score(const PredictionSample&)
    {
        return 1.0;
    }

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
        address.SetBase("10.18.0.0", "255.255.255.0");
        auto interfaces = address.Assign(devices);

        auto metrics = CreateObject<MetricsCollector>();
        auto receiver = CreateObject<FrameReceiver>();
        receiver->SetLocal(InetSocketAddress(Ipv4Address::GetAny(), 9020));
        receiver->SetMetricsCollector(metrics);
        receiver->SetHoldForDelayedSecondary(true);
        nodes.Get(1)->AddApplication(receiver);
        receiver->SetStartTime(Time());
        receiver->SetStopTime(Seconds(1));

        auto source = CreateObject<SyntheticFrameSource>();
        source->SetFps(20);
        source->SetDuration(MilliSeconds(100));
        source->SetConstantFrameSize(2400);
        source->SetDeadline(100000);

        auto sender = CreateObject<MultipathSender>();
        auto policy = CreateObject<SelectiveDuplicationPolicy>();
        policy->SetPrimaryPath(0);
        auto prediction = CreateObject<PredictionTelemetryCollector>();
        prediction->SetSampleOffsetsUs({0});
        auto controller = CreateObject<SelectiveDuplicationController>();
        controller->SetSender(PeekPointer(sender));
        controller->SetRiskScorer(
            MakeCallback(&SelectiveDuplicationControllerTestCase::Score, this));
        controller->SetPrimaryPath(0);
        controller->SetProbabilityThreshold(0.2);
        controller->SetFrameBudget(0.3);
        controller->SetBurstHorizonFrames(1);
        controller->SetDecisionOffsetsUs({0});
        prediction->SetSnapshotCallback(
            MakeCallback(&SelectiveDuplicationController::NotifySnapshot,
                         PeekPointer(controller)));

        sender->SetFrameSource(source);
        sender->SetMetricsCollector(metrics);
        sender->SetPredictionTelemetryCollector(prediction);
        sender->SetPacketPayloadSize(1200);
        sender->SetPolicy(policy);
        sender->SetDelayedSecondaryPath(1);
        for (uint8_t path = 0; path < 2; ++path)
        {
            auto socket =
                Socket::CreateSocket(nodes.Get(0), UdpSocketFactory::GetTypeId());
            NS_TEST_ASSERT_MSG_EQ(socket->Bind(InetSocketAddress(interfaces.GetAddress(0), 0)),
                                  0,
                                  "Selective sender bind failed");
            NS_TEST_ASSERT_MSG_EQ(
                socket->Connect(InetSocketAddress(interfaces.GetAddress(1), 9020)),
                0,
                "Selective sender connect failed");
            sender->AddPath(path, socket, devices.Get(0));
        }
        nodes.Get(0)->AddApplication(sender);
        sender->SetStartTime(MilliSeconds(10));
        sender->SetStopTime(MilliSeconds(500));

        Simulator::Stop(Seconds(1));
        Simulator::Run();
        NS_TEST_ASSERT_MSG_EQ(controller->GetActionCount(),
                              1,
                              "Initial full token was not consumed exactly once");
        NS_TEST_ASSERT_MSG_EQ(controller->GetBudgetSuppressionCount(),
                              1,
                              "Second threshold crossing was not budget-suppressed");
        NS_TEST_ASSERT_MSG_EQ(sender->GetRedundantBytesSent(),
                              2500,
                              "Selective action sent the wrong redundant bytes");
        NS_TEST_ASSERT_MSG_EQ(metrics->GetFrameResults().size(),
                              2,
                              "Selective test did not finalize every frame");
        uint32_t duplicated = 0;
        for (const auto& result : metrics->GetFrameResults())
        {
            duplicated += result.duplicated;
        }
        NS_TEST_ASSERT_MSG_EQ(duplicated,
                              1,
                              "Frame results do not identify the selective action");
        Simulator::Destroy();
    }
};

class ExplicitSecondaryPacketSelectionTestCase : public TestCase
{
  public:
    ExplicitSecondaryPacketSelectionTestCase()
        : TestCase("Adaptive deficit controller launches the exact primary complement")
    {
    }

  private:
    ClosedLoopRiskScore Score(const PredictionSample& sample) const
    {
        const double value =
            sample.frameType == FrameType::P_FRAME && sample.sampleOffsetUs == 1000
                ? 0.0
                : 1.0;
        return {
            value,
            ClosedLoopRiskScoreKind::WEIGHTED_HEAD_PROBABILITY_ADMISSION_SCORE,
            "test_admission_score",
            "test_staged_model",
            "test_source_model_sha256",
            "test_target_provenance_sha256",
            "test_feature_contract_sha256",
            "test_combiner_sha256",
            value,
            value,
        };
    }

    void NotifySnapshot(const PredictionSample& sample)
    {
        if (sample.sampleOffsetUs != 0)
        {
            PredictionSample coherentSample = sample;
            const auto unacknowledged =
                m_sender->GetUnacknowledgedPacketIndices(sample.key);
            NS_TEST_ASSERT_MSG_EQ(unacknowledged.has_value(),
                                  true,
                                  "Late deficit snapshot lacks packet state");
            coherentSample.framePacketsTxSucceeded =
                sample.framePacketCount - unacknowledged->size();
            if (sample.key.frameId == 1 && sample.sampleOffsetUs == 4000)
            {
                NS_TEST_ASSERT_MSG_EQ(unacknowledged->empty(),
                                      false,
                                      "T4 P-frame deficit unexpectedly vanished");
                auto selection = *unacknowledged;
                std::reverse(selection.begin(), selection.end());
                m_lateDescriptor = m_sender->GetDelayedSecondaryPacketDescriptor(
                    sample.key.frameId,
                    selection);
            }
            m_controller->NotifySnapshot(coherentSample);
            return;
        }
        PredictionSample coherentSample = sample;
        if (sample.key.frameId != 0)
        {
            const auto unacknowledged =
                m_sender->GetUnacknowledgedPacketIndices(sample.key);
            NS_TEST_ASSERT_MSG_EQ(unacknowledged.has_value(),
                                  true,
                                  "T0 deficit snapshot lacks packet state");
            coherentSample.framePacketsTxSucceeded =
                sample.framePacketCount - unacknowledged->size();
            m_controller->NotifySnapshot(coherentSample);
            return;
        }
        PredictionTelemetryCollectorTestAccess::AcknowledgePacket(m_prediction,
                                                                   sample.key,
                                                                   1);
        coherentSample.framePacketsTxSucceeded = 1;
        const std::vector<uint32_t> selection{2, 0};
        m_descriptor = m_sender->GetDelayedSecondaryPacketDescriptor(sample.key.frameId,
                                                                      selection);
        m_controller->NotifySnapshot(coherentSample);
        m_repeatRejected = !m_sender->RequestSecondaryPackets(
            sample.key.frameId,
            selection,
            "test repeated packet selection");
    }

    void DoRun() override
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
        address.SetBase("10.20.0.0", "255.255.255.0");
        const auto interfaces = address.Assign(devices);

        auto metrics = CreateObject<MetricsCollector>();
        auto receiver = CreateObject<FrameReceiver>();
        receiver->SetLocal(InetSocketAddress(Ipv4Address::GetAny(), 9022));
        receiver->SetMetricsCollector(metrics);
        receiver->SetHoldForDelayedSecondary(true);
        nodes.Get(1)->AddApplication(receiver);
        receiver->SetStartTime(Time());
        receiver->SetStopTime(Seconds(1));

        auto source = CreateObject<SyntheticFrameSource>();
        source->SetFps(30);
        source->SetDuration(MilliSeconds(40));
        source->SetConstantFrameSize(2501);
        source->SetDeadline(100000);

        auto sender = CreateObject<MultipathSender>();
        m_sender = PeekPointer(sender);
        auto policy = CreateObject<AdaptiveDeficitDuplicationPolicy>();
        policy->SetPrimaryPath(0);
        NS_TEST_ASSERT_MSG_EQ(policy->GetName(),
                              "adaptive_deficit_duplication",
                              "Primary-deficit policy name is wrong");
        auto prediction = CreateObject<PredictionTelemetryCollector>();
        prediction->SetSampleOffsetsUs({0, 1000, 4000});
        m_prediction = prediction;

        auto meter = CreateObject<SecondaryAirtimeMeter>();
        auto controller = CreateObject<AdaptiveAirtimeDuplicationController>();
        m_controller = PeekPointer(controller);
        controller->SetSender(PeekPointer(sender));
        controller->SetRiskScorer(
            MakeCallback(&ExplicitSecondaryPacketSelectionTestCase::Score, this));
        controller->SetAirtimeMeter(meter);
        controller->SetPrimaryPath(0);
        controller->SetSecondaryPacketSelection(
            AdaptiveSecondaryPacketSelection::PRIMARY_UNACKNOWLEDGED);
        controller->SetAdmissionPacketCost(AdaptiveAdmissionPacketCost::WHOLE_COPY);
        controller->SetBudgetFraction(0.02);
        controller->SetBucketHorizonUs(100000);
        controller->SetInitialShadowPrice(0.37);
        controller->SetDualStep(0.10);
        controller->SetDecisionOffsetsUs({0, 1000, 4000});
        controller->SetDecisionOffsetShadowPrices({{0, 0.034}, {4000, 0.059723}});
        controller->SetIFrameOnlyDecisionOffsetsUs({0});
        const std::string directory = "/tmp/ns3-wifi-streaming-deficit-controller-test";
        std::filesystem::remove_all(directory);
        std::filesystem::create_directories(directory);
        meter->SetOutputFiles("deficit-test",
                              directory + "/secondary_airtime_events.csv",
                              directory + "/secondary_airtime_settlements.csv",
                              directory + "/secondary_airtime_summary.json");
        controller->SetOutputFile("deficit-test",
                                  directory + "/adaptive_airtime_decisions.csv");
        prediction->SetSnapshotCallback(
            MakeCallback(&ExplicitSecondaryPacketSelectionTestCase::NotifySnapshot, this));

        sender->SetFrameSource(source);
        sender->SetMetricsCollector(metrics);
        sender->SetPredictionTelemetryCollector(prediction);
        sender->SetPacketPayloadSize(1000);
        sender->SetExpectedMacServiceOverhead(36);
        sender->SetPolicy(policy);
        sender->SetDelayedSecondaryPath(1);
        for (uint8_t path = 0; path < 2; ++path)
        {
            auto socket = Socket::CreateSocket(nodes.Get(0), UdpSocketFactory::GetTypeId());
            NS_TEST_ASSERT_MSG_EQ(socket->Bind(InetSocketAddress(interfaces.GetAddress(0), 0)),
                                  0,
                                  "Explicit-selection sender bind failed");
            NS_TEST_ASSERT_MSG_EQ(
                socket->Connect(InetSocketAddress(interfaces.GetAddress(1), 9022)),
                0,
                "Explicit-selection sender connect failed");
            sender->AddPath(path, socket, devices.Get(0));
        }
        nodes.Get(0)->AddApplication(sender);
        sender->SetStartTime(MilliSeconds(10));
        sender->SetStopTime(MilliSeconds(500));

        Simulator::Stop(Seconds(1));
        Simulator::Run();

        NS_TEST_ASSERT_MSG_EQ(m_descriptor.has_value(),
                              true,
                              "Explicit secondary descriptor is missing");
        NS_TEST_ASSERT_MSG_EQ(m_descriptor->framePacketCount,
                              3,
                              "Explicit descriptor changed total frame cardinality");
        NS_TEST_ASSERT_MSG_EQ(m_descriptor->packetCount,
                              2,
                              "Explicit descriptor has the wrong selected count");
        NS_TEST_ASSERT_MSG_EQ(m_descriptor->packetIndices.size(),
                              2,
                              "Explicit descriptor omitted packet indexes");
        NS_TEST_ASSERT_MSG_EQ(m_descriptor->packetIndices[0],
                              2,
                              "Explicit descriptor changed its first packet");
        NS_TEST_ASSERT_MSG_EQ(m_descriptor->packetIndices[1],
                              0,
                              "Explicit descriptor changed its second packet");
        constexpr uint64_t expectedServiceBytes =
            (501 + StreamingHeader::SERIALIZED_SIZE + 36) +
            (1000 + StreamingHeader::SERIALIZED_SIZE + 36);
        NS_TEST_ASSERT_MSG_EQ(m_descriptor->expectedMacServiceBytes,
                              expectedServiceBytes,
                              "Explicit descriptor priced the wrong packets");
        NS_TEST_ASSERT_MSG_EQ(m_lateDescriptor.has_value(),
                              true,
                              "T4 P-frame descriptor is missing");
        NS_TEST_ASSERT_MSG_EQ(m_lateDescriptor->packetCount > 0,
                              true,
                              "T4 P-frame launched an empty deficit");
        NS_TEST_ASSERT_MSG_EQ(controller->GetActionCount(),
                              2,
                              "Primary-deficit controller did not launch once per test frame");
        NS_TEST_ASSERT_MSG_EQ(m_repeatRejected,
                              true,
                              "Repeated explicit secondary launch was accepted");
        NS_TEST_ASSERT_MSG_EQ(sender->GetPacketsSent(),
                              8 + m_lateDescriptor->packetCount,
                              "Explicit secondary launch sent the wrong packet count");
        constexpr uint64_t expectedRedundantBytes =
            501 + 1000 + 2 * StreamingHeader::SERIALIZED_SIZE;
        NS_TEST_ASSERT_MSG_EQ(sender->GetRedundantBytesSent(),
                              expectedRedundantBytes +
                                  m_lateDescriptor->expectedMacServiceBytes -
                                  36 * m_lateDescriptor->packetCount,
                              "Explicit secondary launch sent the wrong bytes");
        NS_TEST_ASSERT_MSG_EQ(metrics->GetFrameResults().size(),
                              2,
                              "Explicit secondary frame did not finalize");
        NS_TEST_ASSERT_MSG_EQ(metrics->GetFrameResults().front().duplicated,
                              true,
                              "Explicit secondary frame lacks duplication metadata");
        const auto duplicatedFrames = std::count_if(
            metrics->GetFrameResults().begin(),
            metrics->GetFrameResults().end(),
            [](const FrameResult& result) { return result.duplicated; });
        NS_TEST_ASSERT_MSG_EQ(duplicatedFrames,
                              2,
                              "T4 P-frame action lacks duplication metadata");
        NS_TEST_ASSERT_MSG_EQ_TOL(meter->GetReservedAirtimeUs(),
                                  0.0,
                                  1e-9,
                                  "Adaptive deficit reservations did not settle");

        std::ifstream decisions(directory + "/adaptive_airtime_decisions.csv");
        std::string header;
        std::getline(decisions, header);
        const auto split = [](const std::string& value) {
            std::vector<std::string> fields;
            std::stringstream stream(value);
            std::string field;
            while (std::getline(stream, field, ','))
            {
                fields.push_back(field);
            }
            return fields;
        };
        const auto columns = split(header);
        const auto columnFor = [&columns](const std::string& name) {
            const auto column = std::find(columns.begin(), columns.end(), name);
            NS_ABORT_MSG_IF(column == columns.end(), "Missing deficit decision column " << name);
            return std::distance(columns.begin(), column);
        };
        const auto decisionColumn = columnFor("decision");
        const auto admissionPacketCountColumn = columnFor("admission_packet_count");
        const auto admissionAirtimeColumn = columnFor("admission_airtime_us");
        const auto estimatedAirtimeColumn = columnFor("estimated_airtime_us");
        bool sawEarlyAction = false;
        bool sawLateAction = false;
        bool sawGlobalFallback = false;
        bool sawFrameTypeRestriction = false;
        bool sawAlreadyResolved = false;
        std::vector<double> t0DualPrices;
        std::optional<double> frameOneFallbackPrice;
        std::string row;
        while (std::getline(decisions, row))
        {
            const auto values = split(row);
            const uint64_t frameId = std::stoull(values.at(columnFor("frame_id")));
            const uint64_t offsetUs =
                std::stoull(values.at(columnFor("sample_offset_us")));
            const double price = std::stod(values.at(columnFor("shadow_price")));
            const double dualPrice =
                std::stod(values.at(columnFor("dual_shadow_price")));
            if (offsetUs == 1000)
            {
                NS_TEST_ASSERT_MSG_EQ_TOL(price,
                                          dualPrice,
                                          1e-12,
                                          "Unspecified stage did not use the global dual price");
                NS_TEST_ASSERT_MSG_EQ(values.at(columnFor("shadow_price_source")),
                                      "global_dual",
                                      "Unspecified stage did not identify its global price");
                sawGlobalFallback =
                    sawGlobalFallback ||
                    (frameId == 1 && values.at(decisionColumn) == "price_rejected");
                if (frameId == 1)
                {
                    frameOneFallbackPrice = price;
                }
            }
            else
            {
                const double expectedPrice = offsetUs == 0 ? 0.034 : 0.059723;
                NS_TEST_ASSERT_MSG_EQ_TOL(price,
                                          expectedPrice,
                                          1e-12,
                                          "Decision did not use its offset price override");
                NS_TEST_ASSERT_MSG_EQ(values.at(columnFor("shadow_price_source")),
                                      "offset_override",
                                      "Decision did not identify its fixed offset price");
            }
            if (offsetUs == 0)
            {
                t0DualPrices.push_back(dualPrice);
            }
            if (values.at(decisionColumn) == "action")
            {
                if (frameId == 0)
                {
                    sawEarlyAction = offsetUs == 0;
                    NS_TEST_ASSERT_MSG_EQ(
                        std::stoul(values.at(admissionPacketCountColumn)),
                        3,
                        "Whole-copy admission did not price the full frame");
                    NS_TEST_ASSERT_MSG_EQ(
                        values.at(columnFor("configured_admission_packet_cost")),
                        "whole_copy",
                        "Deficit ablation did not record whole-copy pricing");
                    NS_TEST_ASSERT_MSG_EQ(
                        values.at(columnFor("effective_admission_packet_cost")),
                        "whole_copy",
                        "Deficit ablation recorded the wrong effective cost basis");
                    NS_TEST_ASSERT_MSG_EQ(
                        std::stod(values.at(admissionAirtimeColumn)) >
                            std::stod(values.at(estimatedAirtimeColumn)),
                        true,
                        "Whole-copy admission cost did not exceed the partial reservation");
                    NS_TEST_ASSERT_MSG_EQ(values.at(columnFor("primary_acked_packet_indices")),
                                          "1",
                                          "T0 action omitted the exact primary ACK set");
                    NS_TEST_ASSERT_MSG_EQ(values.at(columnFor("secondary_packet_indices")),
                                          "2;0",
                                          "T0 action did not reverse the exact complement");
                }
                else
                {
                    sawLateAction = offsetUs == 4000;
                    NS_TEST_ASSERT_MSG_EQ(
                        std::stoul(values.at(columnFor("secondary_packet_count"))),
                        m_lateDescriptor->packetCount,
                        "T4 action changed its nonzero primary deficit");
                    std::ostringstream expectedIndices;
                    for (std::size_t index = 0;
                         index < m_lateDescriptor->packetIndices.size();
                         ++index)
                    {
                        expectedIndices << (index == 0 ? "" : ";")
                                        << m_lateDescriptor->packetIndices[index];
                    }
                    NS_TEST_ASSERT_MSG_EQ(values.at(columnFor("secondary_packet_indices")),
                                          expectedIndices.str(),
                                          "T4 action changed its exact packet set");
                }
                NS_TEST_ASSERT_MSG_EQ(values.at(columnFor("secondary_packet_order")),
                                      "primary_unacknowledged_reverse",
                                      "Deficit decision recorded the wrong packet order");
            }
            sawFrameTypeRestriction =
                sawFrameTypeRestriction ||
                (frameId == 1 && offsetUs == 0 &&
                 values.at(decisionColumn) == "frame_type_restricted");
            sawAlreadyResolved =
                sawAlreadyResolved ||
                (frameId == 0 && offsetUs == 1000 &&
                 values.at(decisionColumn) == "already_resolved");
        }
        NS_TEST_ASSERT_MSG_EQ(sawEarlyAction,
                              true,
                              "Deficit decision CSV omitted its T0 I-frame action");
        NS_TEST_ASSERT_MSG_EQ(sawLateAction,
                              true,
                              "Restricted P-frame did not launch its nonzero T4 deficit");
        NS_TEST_ASSERT_MSG_EQ(sawGlobalFallback,
                              true,
                              "Deficit controller did not exercise global-price fallback");
        NS_TEST_ASSERT_MSG_EQ(sawFrameTypeRestriction,
                              true,
                              "T0 did not restrict a P-frame before admission");
        NS_TEST_ASSERT_MSG_EQ(sawAlreadyResolved,
                              true,
                              "Later stage did not retain already-resolved behavior");
        NS_TEST_ASSERT_MSG_EQ(t0DualPrices.size(),
                              2,
                              "Deficit test did not observe both T0 dual prices");
        NS_TEST_ASSERT_MSG_EQ(t0DualPrices[1] < t0DualPrices[0],
                              true,
                              "Underlying dual price did not evolve under fixed T0 admission");
        NS_TEST_ASSERT_MSG_EQ(frameOneFallbackPrice.has_value(),
                              true,
                              "P-frame did not reach its global-price fallback stage");
        NS_TEST_ASSERT_MSG_EQ_TOL(*frameOneFallbackPrice,
                                  t0DualPrices[1],
                                  1e-12,
                                  "Fallback stage did not retain the latest dual price");

        std::ifstream settlements(directory + "/secondary_airtime_settlements.csv");
        std::string settlementHeader;
        std::getline(settlements, settlementHeader);
        const auto settlementColumns = split(settlementHeader);
        const auto settlementFrame = std::find(settlementColumns.begin(),
                                               settlementColumns.end(),
                                               "frame_id");
        NS_TEST_ASSERT_MSG_EQ(settlementFrame != settlementColumns.end(),
                              true,
                              "Settlement output lacks frame_id");
        const auto settlementFrameColumn =
            std::distance(settlementColumns.begin(), settlementFrame);
        const auto settlementNominal = std::find(settlementColumns.begin(),
                                                 settlementColumns.end(),
                                                 "nominal_airtime_us");
        NS_TEST_ASSERT_MSG_EQ(settlementNominal != settlementColumns.end(),
                              true,
                              "Settlement output lacks nominal_airtime_us");
        const auto settlementNominalColumn =
            std::distance(settlementColumns.begin(), settlementNominal);
        std::map<uint64_t, uint32_t> settlementsByFrame;
        std::map<uint64_t, double> settlementNominalsByFrame;
        while (std::getline(settlements, row))
        {
            const auto values = split(row);
            const auto frameId = std::stoull(values.at(settlementFrameColumn));
            ++settlementsByFrame[frameId];
            settlementNominalsByFrame[frameId] =
                std::stod(values.at(settlementNominalColumn));
        }
        NS_TEST_ASSERT_MSG_EQ_TOL(
            settlementNominalsByFrame[0],
            controller->EstimateSecondaryAirtimeUs(m_descriptor->packetCount,
                                                    m_descriptor->expectedMacServiceBytes,
                                                    1.0),
            1e-9,
            "T0 settlement nominal did not price its actual selected packet set");
        NS_TEST_ASSERT_MSG_EQ(settlementsByFrame[1],
                              1,
                              "T4 P-frame reservation did not settle exactly once");
        NS_TEST_ASSERT_MSG_EQ_TOL(
            settlementNominalsByFrame[1],
            controller->EstimateSecondaryAirtimeUs(m_lateDescriptor->packetCount,
                                                    m_lateDescriptor->expectedMacServiceBytes,
                                                    1.0),
            1e-9,
            "T4 settlement nominal did not price its actual selected packet set");
        Simulator::Destroy();
        std::filesystem::remove_all(directory);
        m_sender = nullptr;
        m_controller = nullptr;
        m_prediction = nullptr;
    }

    MultipathSender* m_sender{nullptr};
    AdaptiveAirtimeDuplicationController* m_controller{nullptr};
    Ptr<PredictionTelemetryCollector> m_prediction;
    std::optional<DelayedCopyDescriptor> m_descriptor;
    std::optional<DelayedCopyDescriptor> m_lateDescriptor;
    bool m_repeatRejected{false};
};

class SecondaryAirtimeMeterTestCase : public TestCase
{
  public:
    SecondaryAirtimeMeterTestCase()
        : TestCase("Secondary airtime meter allocates, detects mixed PPDUs, and ignores untagged")
    {
    }

  private:
    void DoRun() override
    {
        auto meter = CreateObject<SecondaryAirtimeMeter>();
        SecondaryAirtimeReservation first;
        first.frameId = 1;
        first.packetCount = 2;
        first.reservedAirtimeUs = 100;
        first.estimatedAirtimeUs = 100;
        first.nominalAirtimeUs = 80;
        first.deadlineTimeNs = 1'000'000;
        SecondaryAirtimeReservation second = first;
        second.frameId = 2;
        meter->RegisterLaunchedCopy(std::move(first));
        meter->RegisterLaunchedCopy(std::move(second));
        NS_TEST_ASSERT_MSG_EQ_TOL(meter->GetReservedAirtimeUs(),
                                  200.0,
                                  1e-9,
                                  "Reservations were not accumulated");

        // Untagged / empty map must be ignored.
        meter->ApplyTestPpdu({}, 40.0, 0);
        NS_TEST_ASSERT_MSG_EQ_TOL(meter->GetMeasuredAirtimeTotalUs(),
                                  0.0,
                                  1e-9,
                                  "Untagged PPDU was counted");

        // Multi-frame A-MPDU counted once and split by bytes.
        meter->ApplyTestPpdu({{1, 300}, {2, 100}}, 40.0, 0);
        NS_TEST_ASSERT_MSG_EQ_TOL(meter->GetMeasuredAirtimeTotalUs(),
                                  40.0,
                                  1e-9,
                                  "Multi-frame PPDU duration was not counted once");
        NS_TEST_ASSERT_MSG_EQ_TOL(meter->GetReservedAirtimeUs(),
                                  160.0,
                                  1e-9,
                                  "Reservation was not reduced by measured airtime");

        // Retransmission of the same frames counts again.
        meter->ApplyTestPpdu({{1, 300}, {2, 100}}, 40.0, 0);
        NS_TEST_ASSERT_MSG_EQ_TOL(meter->GetMeasuredAirtimeTotalUs(),
                                  80.0,
                                  1e-9,
                                  "Retransmitted PPDU was not counted again");

        // Mixed tagged/untagged detection.
        meter->ApplyTestPpdu({{1, 100}}, 10.0, 50);
        NS_TEST_ASSERT_MSG_EQ(meter->GetMixedPpduCount(), 1, "Mixed PPDU was not detected");

        // Debt observation tracks the deepest negative balance.
        meter->ObserveBudgetDebt(12.5);
        meter->ObserveBudgetDebt(7.5);
        NS_TEST_ASSERT_MSG_EQ_TOL(meter->GetMaximumBudgetDebtUs(),
                                  12.5,
                                  1e-9,
                                  "Maximum budget debt was not retained");

        // ACK/drop callbacks for the same packet must settle it only once.
        SecondaryAirtimeReservation third;
        third.frameId = 3;
        third.packetCount = 2;
        third.reservedAirtimeUs = 100;
        third.estimatedAirtimeUs = 100;
        third.nominalAirtimeUs = 80;
        third.deadlineTimeNs = 1'000'000;
        meter->RegisterLaunchedCopy(std::move(third));
        const double beforeTerminal = meter->GetReservedAirtimeUs();
        SecondaryAirtimeMeterTestAccess::Terminal(meter, 3, 0);
        SecondaryAirtimeMeterTestAccess::Terminal(meter, 3, 0);
        NS_TEST_ASSERT_MSG_EQ_TOL(meter->GetReservedAirtimeUs(),
                                  beforeTerminal,
                                  1e-9,
                                  "Duplicate terminal callback released a reservation");
        SecondaryAirtimeMeterTestAccess::Terminal(meter, 3, 1);
        NS_TEST_ASSERT_MSG_EQ_TOL(meter->GetReservedAirtimeUs(),
                                  beforeTerminal - 100.0,
                                  1e-9,
                                  "Distinct terminal packets did not settle the frame");

        SecondaryAirtimeReservation suffix;
        suffix.frameId = 30;
        suffix.packetCount = 2;
        suffix.expectedPacketIndices = {8, 9};
        suffix.reservedAirtimeUs = 100;
        suffix.estimatedAirtimeUs = 100;
        suffix.nominalAirtimeUs = 80;
        suffix.deadlineTimeNs = 1'000'000;
        meter->RegisterLaunchedCopy(std::move(suffix));
        const double beforeSuffix = meter->GetReservedAirtimeUs();
        SecondaryAirtimeMeterTestAccess::Terminal(meter, 30, 9);
        NS_TEST_ASSERT_MSG_EQ_TOL(meter->GetReservedAirtimeUs(),
                                  beforeSuffix,
                                  1e-9,
                                  "First suffix terminal released the reservation");
        SecondaryAirtimeMeterTestAccess::Terminal(meter, 30, 8);
        NS_TEST_ASSERT_MSG_EQ_TOL(meter->GetReservedAirtimeUs(),
                                  beforeSuffix - 100.0,
                                  1e-9,
                                  "Original suffix indexes did not settle the reservation");

        // Only events in the configured half-open interval are measured.
        auto windowMeter = CreateObject<SecondaryAirtimeMeter>();
        windowMeter->SetMeasurementWindow(1000, 2000);
        Simulator::Schedule(NanoSeconds(500),
                            &SecondaryAirtimeMeter::ApplyTestPpdu,
                            PeekPointer(windowMeter),
                            std::map<uint64_t, uint64_t>{{4, 100}},
                            10.0,
                            static_cast<uint64_t>(0));
        Simulator::Schedule(NanoSeconds(1000),
                            &SecondaryAirtimeMeter::ApplyTestPpdu,
                            PeekPointer(windowMeter),
                            std::map<uint64_t, uint64_t>{{4, 100}},
                            10.0,
                            static_cast<uint64_t>(0));
        Simulator::Schedule(NanoSeconds(1999),
                            &SecondaryAirtimeMeter::ApplyTestPpdu,
                            PeekPointer(windowMeter),
                            std::map<uint64_t, uint64_t>{{4, 100}},
                            10.0,
                            static_cast<uint64_t>(0));
        Simulator::Schedule(NanoSeconds(2000),
                            &SecondaryAirtimeMeter::ApplyTestPpdu,
                            PeekPointer(windowMeter),
                            std::map<uint64_t, uint64_t>{{4, 100}},
                            10.0,
                            static_cast<uint64_t>(0));
        Simulator::Stop(NanoSeconds(3000));
        Simulator::Run();
        NS_TEST_ASSERT_MSG_EQ(windowMeter->GetTaggedPpduCount(),
                              2,
                              "Measurement-window boundary filtering is incorrect");
        NS_TEST_ASSERT_MSG_EQ_TOL(windowMeter->GetMeasuredAirtimeTotalUs(),
                                  20.0,
                                  1e-9,
                                  "Out-of-window airtime was measured");
        Simulator::Destroy();
    }
};

class SecondaryAirtimeMeterWifiTraceTestCase : public TestCase
{
  public:
    SecondaryAirtimeMeterWifiTraceTestCase()
        : TestCase("Secondary airtime meter observes tagged Wi-Fi PHY transmissions")
    {
    }

  private:
    /**
     * Submit one tagged secondary packet through the real UDP/Wi-Fi stack.
     *
     * @param socket Connected sender socket.
     */
    void SendTaggedPacket(Ptr<Socket> socket)
    {
        StreamingFrameTag tag;
        tag.frameId = 77;
        tag.pathId = 0;
        tag.copyId = 1;
        tag.packetIndex = 0;
        tag.packetCount = 1;
        tag.generationTimeNs = Simulator::Now().GetNanoSeconds();
        tag.deadlineTimeNs = tag.generationTimeNs + 100000000;
        tag.frameSizeBytes = 1000;
        tag.frameType = FrameType::P_FRAME;
        auto packet = Create<Packet>(1000);
        packet->AddPacketTag(tag);
        NS_TEST_ASSERT_MSG_EQ(socket->Send(packet) >= 0,
                              true,
                              "Tagged Wi-Fi packet submission failed");
    }

    void DoRun() override
    {
        NodeContainer station;
        NodeContainer accessPoint;
        station.Create(1);
        accessPoint.Create(1);
        InternetStackHelper internet;
        internet.Install(station);
        internet.Install(accessPoint);

        YansWifiChannelHelper channel = YansWifiChannelHelper::Default();
        YansWifiPhyHelper phy;
        phy.SetChannel(channel.Create());
        phy.Set("ChannelSettings", StringValue("{1, 20, BAND_2_4GHZ, 0}"));
        WifiHelper wifi;
        wifi.SetStandard(WIFI_STANDARD_80211be);
        wifi.SetRemoteStationManager("ns3::ConstantRateWifiManager",
                                     "DataMode",
                                     StringValue("EhtMcs5"),
                                     "ControlMode",
                                     StringValue("ErpOfdmRate24Mbps"),
                                     "FragmentationThreshold",
                                     UintegerValue(65535));
        const Ssid ssid("secondary-airtime-meter-test");
        WifiMacHelper mac;
        mac.SetType("ns3::StaWifiMac",
                    "Ssid",
                    SsidValue(ssid),
                    "ActiveProbing",
                    BooleanValue(false),
                    "BE_MaxAmsduSize",
                    UintegerValue(0));
        const auto stationDevice = wifi.Install(phy, mac, station);
        mac.SetType("ns3::ApWifiMac",
                    "Ssid",
                    SsidValue(ssid),
                    "BE_MaxAmsduSize",
                    UintegerValue(0));
        const auto accessPointDevice = wifi.Install(phy, mac, accessPoint);

        MobilityHelper mobility;
        mobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
        mobility.Install(station);
        mobility.Install(accessPoint);
        station.Get(0)->GetObject<MobilityModel>()->SetPosition(Vector(0, 0, 0));
        accessPoint.Get(0)->GetObject<MobilityModel>()->SetPosition(Vector(1, 0, 0));

        Ipv4AddressHelper address;
        address.SetBase("10.20.0.0", "255.255.255.0");
        const auto stationInterface = address.Assign(stationDevice);
        const auto accessPointInterface = address.Assign(accessPointDevice);
        auto socket = Socket::CreateSocket(station.Get(0), UdpSocketFactory::GetTypeId());
        NS_TEST_ASSERT_MSG_EQ(
            socket->Bind(InetSocketAddress(stationInterface.GetAddress(0), 0)),
            0,
            "Airtime-meter sender bind failed");
        NS_TEST_ASSERT_MSG_EQ(
            socket->Connect(InetSocketAddress(accessPointInterface.GetAddress(0), 9090)),
            0,
            "Airtime-meter sender connect failed");

        const std::string directory = "/tmp/ns3-wifi-streaming-airtime-meter-test";
        std::filesystem::remove_all(directory);
        std::filesystem::create_directories(directory);
        auto meter = CreateObject<SecondaryAirtimeMeter>();
        meter->SetMeasurementWindow(Seconds(0.9).GetNanoSeconds(),
                                    Seconds(1.2).GetNanoSeconds());
        meter->BindPath(0, stationDevice.Get(0));
        meter->SetOutputFiles("airtime-wifi-test",
                              directory + "/secondary_airtime_events.csv",
                              directory + "/secondary_airtime_settlements.csv",
                              directory + "/secondary_airtime_summary.json");
        Simulator::Schedule(Seconds(1),
                            &SecondaryAirtimeMeterWifiTraceTestCase::SendTaggedPacket,
                            this,
                            socket);
        Simulator::Stop(Seconds(1.3));
        Simulator::Run();
        meter->WriteSummary();

        NS_TEST_ASSERT_MSG_EQ(meter->GetTaggedPpduCount() > 0,
                              true,
                              "Real Wi-Fi trace produced no tagged PPDU event");
        NS_TEST_ASSERT_MSG_EQ(meter->GetMeasuredAirtimeTotalUs() > 0,
                              true,
                              "Real Wi-Fi trace produced no measured airtime");
        NS_TEST_ASSERT_MSG_EQ(meter->GetMixedPpduCount(),
                              0,
                              "Single tagged Wi-Fi packet was classified as mixed");
        NS_TEST_ASSERT_MSG_EQ(std::filesystem::is_regular_file(
                                  directory + "/secondary_airtime_summary.json"),
                              true,
                              "Airtime summary output is missing");
        Simulator::Destroy();
        std::filesystem::remove_all(directory);
    }
};

class AdaptiveAirtimeBucketCreditTestCase : public TestCase
{
  public:
    AdaptiveAirtimeBucketCreditTestCase()
        : TestCase("Adaptive airtime separates burst capacity from initial credit")
    {
    }

  private:
    void DoRun() override
    {
        constexpr double budgetFraction = 0.01;
        constexpr uint64_t bucketHorizonUs = 2000000;
        constexpr uint64_t initialHorizonUs = 100000;
        constexpr uint64_t initializeTimeNs = 1000000000;

        auto controller = CreateObject<AdaptiveAirtimeDuplicationController>();
        controller->SetBudgetFraction(budgetFraction);
        // Resolve the maximum first so the limited initial horizon is checked
        // against the intended burst horizon rather than the default.
        controller->SetBucketHorizonUs(bucketHorizonUs);
        controller->SetInitialBucketHorizonUs(initialHorizonUs);
        AdaptiveAirtimeDuplicationControllerTestAccess::Initialize(controller,
                                                                   initializeTimeNs);

        constexpr double expectedCapacityUs = budgetFraction * bucketHorizonUs;
        constexpr double expectedInitialCapacityUs = budgetFraction * initialHorizonUs;
        NS_TEST_ASSERT_MSG_EQ_TOL(
            AdaptiveAirtimeDuplicationControllerTestAccess::GetCapacityUs(controller),
            expectedCapacityUs,
            1e-9,
            "Maximum balance did not use the burst horizon");
        NS_TEST_ASSERT_MSG_EQ_TOL(controller->GetInitialCapacityUs(),
                                  expectedInitialCapacityUs,
                                  1e-9,
                                  "Startup credit did not use the initial horizon");
        NS_TEST_ASSERT_MSG_EQ_TOL(controller->GetBucketBalanceUs(),
                                  expectedInitialCapacityUs,
                                  1e-9,
                                  "Bucket did not start at the limited initial credit");

        const uint64_t refillTimeNs =
            initializeTimeNs + bucketHorizonUs * 1000;
        AdaptiveAirtimeDuplicationControllerTestAccess::Refill(controller, refillTimeNs);
        NS_TEST_ASSERT_MSG_EQ_TOL(controller->GetBucketBalanceUs(),
                                  expectedCapacityUs,
                                  1e-9,
                                  "Limited startup credit prevented later burst accumulation");

        auto legacy = CreateObject<AdaptiveAirtimeDuplicationController>();
        legacy->SetBudgetFraction(budgetFraction);
        legacy->SetBucketHorizonUs(bucketHorizonUs);
        AdaptiveAirtimeDuplicationControllerTestAccess::Initialize(legacy,
                                                                   initializeTimeNs);
        NS_TEST_ASSERT_MSG_EQ_TOL(legacy->GetInitialCapacityUs(),
                                  expectedCapacityUs,
                                  1e-9,
                                  "Unset initial horizon no longer defaults to a full bucket");
        NS_TEST_ASSERT_MSG_EQ_TOL(legacy->GetBucketBalanceUs(),
                                  expectedCapacityUs,
                                  1e-9,
                                  "Legacy bucket did not start full");

        auto fixedGate = CreateObject<AdaptiveAirtimeDuplicationController>();
        fixedGate->SetInitialShadowPrice(0.37);
        fixedGate->SetDualStep(0.0);
        fixedGate->SetDecisionOffsetsUs({0, 4000});
        fixedGate->SetDecisionOffsetShadowPrices({{0, 0.034}});
        fixedGate->SetIFrameOnlyDecisionOffsetsUs({0});
        AdaptiveAirtimeDuplicationControllerTestAccess::Initialize(fixedGate,
                                                                   initializeTimeNs);
        AdaptiveAirtimeDuplicationControllerTestAccess::SetMeasuredSinceLastT0Us(
            fixedGate,
            10000.0);
        AdaptiveAirtimeDuplicationControllerTestAccess::UpdateShadowPrice(
            fixedGate,
            initializeTimeNs + 1000000);
        NS_TEST_ASSERT_MSG_EQ_TOL(fixedGate->GetShadowPrice(),
                                  0.37,
                                  1e-12,
                                  "Zero dual step did not freeze the shadow price");
        NS_TEST_ASSERT_MSG_EQ_TOL(
            AdaptiveAirtimeDuplicationControllerTestAccess::ResolveDecisionShadowPrice(
                fixedGate,
                0),
            0.034,
            1e-12,
            "T0 did not use its fixed shadow-price override");
        NS_TEST_ASSERT_MSG_EQ_TOL(
            AdaptiveAirtimeDuplicationControllerTestAccess::ResolveDecisionShadowPrice(
                fixedGate,
                4000),
            0.37,
            1e-12,
            "Unspecified stage did not retain the global dual price");
        NS_TEST_ASSERT_MSG_EQ(
            AdaptiveAirtimeDuplicationControllerTestAccess::IsFrameTypeEligible(
                fixedGate,
                0,
                FrameType::P_FRAME),
            false,
            "T0 admitted a P-frame despite its restriction");
        NS_TEST_ASSERT_MSG_EQ(
            AdaptiveAirtimeDuplicationControllerTestAccess::IsFrameTypeEligible(
                fixedGate,
                0,
                FrameType::I_FRAME),
            true,
            "T0 rejected an I-frame despite its restriction");
        NS_TEST_ASSERT_MSG_EQ(
            AdaptiveAirtimeDuplicationControllerTestAccess::IsFrameTypeEligible(
                fixedGate,
                4000,
                FrameType::P_FRAME),
            true,
            "Unrestricted later stage rejected a P-frame");

        NS_TEST_ASSERT_MSG_EQ(
            AdaptiveAirtimeDuplicationControllerTestAccess::AreHorizonsValid(
                bucketHorizonUs,
                initialHorizonUs),
            true,
            "Valid bucket horizon ordering was rejected");
        NS_TEST_ASSERT_MSG_EQ(
            AdaptiveAirtimeDuplicationControllerTestAccess::AreHorizonsValid(0,
                                                                             initialHorizonUs),
            false,
            "Zero burst horizon was accepted");
        NS_TEST_ASSERT_MSG_EQ(
            AdaptiveAirtimeDuplicationControllerTestAccess::AreHorizonsValid(bucketHorizonUs, 0),
            false,
            "Zero initial horizon was accepted");
        NS_TEST_ASSERT_MSG_EQ(
            AdaptiveAirtimeDuplicationControllerTestAccess::AreHorizonsValid(initialHorizonUs,
                                                                             bucketHorizonUs),
            false,
            "Initial credit larger than burst capacity was accepted");
    }
};

class AdaptiveAirtimeDuplicationControllerTestCase : public TestCase
{
  public:
    AdaptiveAirtimeDuplicationControllerTestCase()
        : TestCase("Adaptive airtime controller prices, defers, and launches at most once")
    {
    }

  private:
    ClosedLoopRiskScore Score(const PredictionSample& sample)
    {
        // Reject at T0, then become strongly actionable so a later stage may launch.
        const double value = sample.sampleOffsetUs == 0 ? 0.01 : 0.95;
        return {
            value,
            sample.sampleOffsetUs == 0
                ? ClosedLoopRiskScoreKind::CALIBRATED_PRIMARY_MISS_PROBABILITY
                : ClosedLoopRiskScoreKind::WEIGHTED_HEAD_PROBABILITY_ADMISSION_SCORE,
            sample.sampleOffsetUs == 0 ? "test_primary_miss_probability"
                                       : "test_admission_score",
            sample.sampleOffsetUs == 0 ? "test_t0_model" : "test_later_model",
            "test_source_model_sha256",
            "test_target_provenance_sha256",
            "test_feature_contract_sha256",
            sample.sampleOffsetUs == 0 ? "" : "test_combiner_sha256",
            value,
            sample.sampleOffsetUs == 0 ? std::nullopt
                                       : std::optional<double>(value),
        };
    }

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
        address.SetBase("10.19.0.0", "255.255.255.0");
        auto interfaces = address.Assign(devices);

        auto metrics = CreateObject<MetricsCollector>();
        auto receiver = CreateObject<FrameReceiver>();
        receiver->SetLocal(InetSocketAddress(Ipv4Address::GetAny(), 9021));
        receiver->SetMetricsCollector(metrics);
        receiver->SetHoldForDelayedSecondary(true);
        nodes.Get(1)->AddApplication(receiver);
        receiver->SetStartTime(Time());
        receiver->SetStopTime(Seconds(2));

        auto source = CreateObject<SyntheticFrameSource>();
        source->SetFps(20);
        source->SetDuration(MilliSeconds(150));
        source->SetConstantFrameSize(2400);
        source->SetDeadline(100000);

        auto sender = CreateObject<MultipathSender>();
        auto policy = CreateObject<AdaptiveAirtimeDuplicationPolicy>();
        policy->SetPrimaryPath(1);
        NS_TEST_ASSERT_MSG_EQ(policy->GetName(),
                              "adaptive_airtime_duplication",
                              "Adaptive policy name is wrong");

        auto prediction = CreateObject<PredictionTelemetryCollector>();
        prediction->SetSampleOffsetsUs({0, 1000, 2000, 4000});
        auto meter = CreateObject<SecondaryAirtimeMeter>();
        auto controller = CreateObject<AdaptiveAirtimeDuplicationController>();
        controller->SetSender(PeekPointer(sender));
        controller->SetRiskScorer(
            MakeCallback(&AdaptiveAirtimeDuplicationControllerTestCase::Score, this));
        controller->SetAirtimeMeter(meter);
        controller->SetPrimaryPath(1);
        controller->SetBudgetFraction(0.02);
        controller->SetBucketHorizonUs(1000000);
        controller->SetInitialShadowPrice(0.20);
        controller->SetDualStep(0.01);
        controller->SetAdmissionUsesRetryInflation(false);
        AdaptiveAirtimeDuplicationControllerTestAccess::SetRetryInflation(controller, 2.0);
        controller->SetCostSafetyFactor(1.25);
        controller->SetCostEwmaAlpha(0.10);
        controller->SetDecisionOffsetsUs({0, 1000, 2000, 4000});
        const std::string directory = "/tmp/ns3-wifi-streaming-adaptive-airtime-test";
        std::filesystem::remove_all(directory);
        std::filesystem::create_directories(directory);
        controller->SetOutputFile("adaptive-test",
                                  directory + "/adaptive_airtime_decisions.csv");
        prediction->SetSnapshotCallback(
            MakeCallback(&AdaptiveAirtimeDuplicationController::NotifySnapshot,
                         PeekPointer(controller)));

        sender->SetFrameSource(source);
        sender->SetMetricsCollector(metrics);
        sender->SetPredictionTelemetryCollector(prediction);
        sender->SetPacketPayloadSize(1200);
        sender->SetExpectedMacServiceOverhead(36);
        sender->SetPolicy(policy);
        sender->SetDelayedSecondaryPath(0);
        for (uint8_t path = 0; path < 2; ++path)
        {
            auto socket =
                Socket::CreateSocket(nodes.Get(0), UdpSocketFactory::GetTypeId());
            NS_TEST_ASSERT_MSG_EQ(socket->Bind(InetSocketAddress(interfaces.GetAddress(0), 0)),
                                  0,
                                  "Adaptive sender bind failed");
            NS_TEST_ASSERT_MSG_EQ(
                socket->Connect(InetSocketAddress(interfaces.GetAddress(1), 9021)),
                0,
                "Adaptive sender connect failed");
            sender->AddPath(path, socket, devices.Get(0));
        }
        nodes.Get(0)->AddApplication(sender);
        sender->SetStartTime(MilliSeconds(10));
        sender->SetStopTime(MilliSeconds(500));

        // Charge frame 0 above its reservation so debt rises and later frames defer.
        Simulator::Schedule(MilliSeconds(40),
                            &SecondaryAirtimeMeter::ApplyTestPpdu,
                            PeekPointer(meter),
                            std::map<uint64_t, uint64_t>{{0, 1000}},
                            25000.0,
                            static_cast<uint64_t>(0));

        Simulator::Stop(Seconds(1));
        Simulator::Run();

        NS_TEST_ASSERT_MSG_EQ(controller->GetActionCount() > 0,
                              true,
                              "Adaptive controller never launched a secondary copy");
        NS_TEST_ASSERT_MSG_EQ(controller->GetShadowPrice() > 0.20,
                              true,
                              "Shadow price did not rise after overspending");
        NS_TEST_ASSERT_MSG_EQ(meter->GetMaximumBudgetDebtUs() > 0,
                              true,
                              "Actual airtime above the reservation did not create debt");
        NS_TEST_ASSERT_MSG_EQ_TOL(meter->GetReservedAirtimeUs(),
                                  0.0,
                                  1e-9,
                                  "Measured airtime did not reduce the frame reservation");
        NS_TEST_ASSERT_MSG_EQ(controller->GetBucketBalanceUs() <
                                  controller->GetInitialCapacityUs(),
                              true,
                              "Measured airtime did not reduce bucket balance");

        std::ifstream decisions(directory + "/adaptive_airtime_decisions.csv");
        NS_TEST_ASSERT_MSG_EQ(static_cast<bool>(decisions),
                              true,
                              "Adaptive decision CSV is missing");
        std::string header;
        std::getline(decisions, header);
        std::stringstream headerStream(header);
        std::string columnName;
        std::vector<std::string> columnNames;
        std::map<std::string, std::size_t> columns;
        while (std::getline(headerStream, columnName, ','))
        {
            columns.emplace(columnName, columnNames.size());
            columnNames.push_back(columnName);
        }
        NS_TEST_ASSERT_MSG_EQ(columns.contains("shadow_price"),
                              true,
                              "Adaptive decision schema is missing shadow_price");
        NS_TEST_ASSERT_MSG_EQ(columns.contains("admission_airtime_us"),
                              true,
                              "Adaptive decision schema is missing admission airtime");
        NS_TEST_ASSERT_MSG_EQ(columns.contains("primary_acked_packet_indices"),
                              true,
                              "Unified adaptive decision schema lacks packet audit fields");
        NS_TEST_ASSERT_MSG_EQ(columns.contains("admission_score"),
                              true,
                              "Adaptive decision schema lacks its truthful score field");
        NS_TEST_ASSERT_MSG_EQ(columns.contains("score_kind"),
                              true,
                              "Adaptive decision schema lacks score semantics");
        std::map<uint64_t, uint32_t> actionsByFrame;
        std::map<uint64_t, bool> rejectedThenActed;
        std::vector<double> t0Prices;
        bool sawAirtimeDeferred = false;
        bool sawInflatedReservation = false;
        std::string line;
        while (std::getline(decisions, line))
        {
            if (line.empty())
            {
                continue;
            }
            std::stringstream row(line);
            std::string field;
            std::vector<std::string> fields;
            while (std::getline(row, field, ','))
            {
                fields.push_back(field);
            }
            NS_TEST_ASSERT_MSG_EQ(fields.size(),
                                  columnNames.size(),
                                  "Adaptive decision row is truncated");
            const auto get = [&fields, &columns](const std::string& name) -> const std::string& {
                return fields.at(columns.at(name));
            };
            const uint64_t frameId = std::stoull(get("frame_id"));
            if (get("sample_stage") == "T0")
            {
                t0Prices.push_back(std::stod(get("shadow_price")));
            }
            const std::string decision = get("decision");
            const bool launched = get("secondary_launched") == "1";
            const double admissionUs = std::stod(get("admission_airtime_us"));
            const double estimatedUs = std::stod(get("estimated_airtime_us"));
            NS_TEST_ASSERT_MSG_EQ(get("shadow_price_source"),
                                  "global_dual",
                                  "Legacy controller unexpectedly selected an offset price");
            NS_TEST_ASSERT_MSG_EQ_TOL(std::stod(get("shadow_price")),
                                      std::stod(get("dual_shadow_price")),
                                      1e-12,
                                      "Legacy controller changed shadow-price semantics");
            if (estimatedUs > 0)
            {
                const double referenceUs = std::stod(get("reference_airtime_us"));
                const double normalizedCost = std::stod(get("normalized_cost"));
                NS_TEST_ASSERT_MSG_EQ_TOL(normalizedCost,
                                          admissionUs / referenceUs,
                                          1e-9,
                                          "Normalized cost did not use nominal admission airtime");
                NS_TEST_ASSERT_MSG_EQ(estimatedUs + 1e-9 >= admissionUs,
                                      true,
                                      "Retry-inflated reservation fell below nominal admission");
                sawInflatedReservation =
                    sawInflatedReservation || estimatedUs > admissionUs + 1e-9;
            }
            if (decision == "price_rejected")
            {
                rejectedThenActed[frameId] = false;
            }
            if (decision == "action")
            {
                ++actionsByFrame[frameId];
                if (rejectedThenActed.contains(frameId))
                {
                    rejectedThenActed[frameId] = true;
                }
            }
            NS_TEST_ASSERT_MSG_EQ(launched == (decision == "action"),
                                  true,
                                  "Adaptive launch flag mismatches decision");
            if (decision == "airtime_deferred")
            {
                sawAirtimeDeferred = true;
                NS_TEST_ASSERT_MSG_EQ(launched,
                                      false,
                                      "airtime_deferred must not launch");
            }
        }
        for (const auto& [frameId, count] : actionsByFrame)
        {
            NS_TEST_ASSERT_MSG_EQ(count,
                                  1,
                                  "Frame launched more than one secondary copy: " << frameId);
        }
        bool sawRejectedThenActed = false;
        for (const auto& [frameId, acted] : rejectedThenActed)
        {
            (void)frameId;
            if (acted)
            {
                sawRejectedThenActed = true;
                break;
            }
        }
        NS_TEST_ASSERT_MSG_EQ(sawRejectedThenActed,
                              true,
                              "price_rejected never became an action at a later stage");
        NS_TEST_ASSERT_MSG_EQ(sawAirtimeDeferred,
                              true,
                              "Controller test never exercised airtime deferral");
        NS_TEST_ASSERT_MSG_EQ(sawInflatedReservation,
                              true,
                              "Controller test did not separate nominal admission from an "
                              "inflated reservation");
        bool sawPriceRise = false;
        bool sawPriceFall = false;
        for (std::size_t index = 1; index < t0Prices.size(); ++index)
        {
            sawPriceRise = sawPriceRise || t0Prices[index] > t0Prices[index - 1] + 1e-12;
            sawPriceFall = sawPriceFall || t0Prices[index] + 1e-12 < t0Prices[index - 1];
        }
        NS_TEST_ASSERT_MSG_EQ(sawPriceRise, true, "Shadow price never rose after overspending");
        NS_TEST_ASSERT_MSG_EQ(sawPriceFall, true, "Shadow price never fell after underspending");

        // Larger frames must expose more secondary packets / MAC service bytes.
        FramePacketizer packetizer;
        packetizer.SetPayloadSize(1200);
        packetizer.SetExpectedMacServiceOverhead(36);
        FrameDescriptor smallFrame;
        smallFrame.frameId = 100;
        smallFrame.frameSizeBytes = 2400;
        smallFrame.deadlineUs = 100000;
        FrameDescriptor largeFrame = smallFrame;
        largeFrame.frameId = 101;
        largeFrame.frameSizeBytes = 12000;
        const auto smallPlan = packetizer.Plan(smallFrame, 0, 1, 0, StreamingHeader::FLAG_DUPLICATED_FRAME);
        const auto largePlan = packetizer.Plan(largeFrame, 0, 1, 0, StreamingHeader::FLAG_DUPLICATED_FRAME);
        uint64_t smallBytes = 0;
        uint64_t largeBytes = 0;
        for (const auto& packet : smallPlan.packets)
        {
            smallBytes += *packet.expectedMacServiceBytes;
        }
        for (const auto& packet : largePlan.packets)
        {
            largeBytes += *packet.expectedMacServiceBytes;
        }
        NS_TEST_ASSERT_MSG_EQ(largePlan.packets.size() > smallPlan.packets.size(),
                              true,
                              "Larger frame did not increase packet count");
        NS_TEST_ASSERT_MSG_EQ(largeBytes > smallBytes,
                              true,
                              "Larger frame did not increase expected MAC service bytes");
        const double smallEstimate = controller->EstimateSecondaryAirtimeUs(
            static_cast<uint32_t>(smallPlan.packets.size()),
            smallBytes,
            1.0);
        const double largeEstimate = controller->EstimateSecondaryAirtimeUs(
            static_cast<uint32_t>(largePlan.packets.size()),
            largeBytes,
            1.0);
        NS_TEST_ASSERT_MSG_EQ(largeEstimate > smallEstimate,
                              true,
                              "Larger frame did not increase estimated airtime");
        constexpr uint32_t referencePackets = 10;
        constexpr uint64_t referenceBytes =
            10ULL * (1200 + StreamingHeader::SERIALIZED_SIZE + 36);
        NS_TEST_ASSERT_MSG_EQ_TOL(controller->EstimateSecondaryAirtimeUs(referencePackets,
                                                                         referenceBytes,
                                                                         1.0),
                                  controller->GetReferenceAirtimeUs(),
                                  1e-9,
                                  "Normal-frame reference cost differs from its estimate");

        Simulator::Destroy();
        std::filesystem::remove_all(directory);
    }
};

class PredictionWifiTelemetryTestCase : public TestCase
{
  public:
    PredictionWifiTelemetryTestCase()
        : TestCase("Prediction telemetry observes tagged Wi-Fi MAC and PHY state")
    {
    }

  private:
    void DoRun() override
    {
        NodeContainer station;
        NodeContainer accessPoint;
        station.Create(1);
        accessPoint.Create(1);
        InternetStackHelper internet;
        internet.Install(station);
        internet.Install(accessPoint);

        YansWifiChannelHelper channel = YansWifiChannelHelper::Default();
        YansWifiPhyHelper phy;
        phy.SetChannel(channel.Create());
        phy.Set("ChannelSettings", StringValue("{36, 20, BAND_5GHZ, 0}"));
        WifiHelper wifi;
        wifi.SetStandard(WIFI_STANDARD_80211be);
        wifi.SetRemoteStationManager("ns3::ConstantRateWifiManager",
                                     "DataMode",
                                     StringValue("EhtMcs5"),
                                     "ControlMode",
                                     StringValue("OfdmRate24Mbps"),
                                     "FragmentationThreshold",
                                     UintegerValue(65535));
        const Ssid ssid("prediction-telemetry-test");
        WifiMacHelper mac;
        mac.SetType("ns3::StaWifiMac",
                    "Ssid",
                    SsidValue(ssid),
                    "ActiveProbing",
                    BooleanValue(false),
                    "BE_MaxAmsduSize",
                    UintegerValue(0));
        const auto stationDevice = wifi.Install(phy, mac, station);
        mac.SetType("ns3::ApWifiMac",
                    "Ssid",
                    SsidValue(ssid),
                    "BE_MaxAmsduSize",
                    UintegerValue(0));
        const auto accessPointDevice = wifi.Install(phy, mac, accessPoint);

        MobilityHelper mobility;
        mobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
        mobility.Install(station);
        mobility.Install(accessPoint);
        station.Get(0)->GetObject<MobilityModel>()->SetPosition(Vector(0, 0, 0));
        accessPoint.Get(0)->GetObject<MobilityModel>()->SetPosition(Vector(1, 0, 0));

        Ipv4AddressHelper address;
        address.SetBase("10.11.0.0", "255.255.255.0");
        const auto stationInterface = address.Assign(stationDevice);
        const auto accessPointInterface = address.Assign(accessPointDevice);

        auto metrics = CreateObject<MetricsCollector>();
        auto receiver = CreateObject<FrameReceiver>();
        receiver->SetLocal(InetSocketAddress(Ipv4Address::GetAny(), 9010));
        receiver->SetMetricsCollector(metrics);
        accessPoint.Get(0)->AddApplication(receiver);
        receiver->SetStartTime(Time());
        receiver->SetStopTime(Seconds(2));

        auto source = CreateObject<SyntheticFrameSource>();
        source->SetFps(30);
        source->SetDuration(MilliSeconds(1));
        source->SetConstantFrameSize(2400);
        source->SetDeadline(100000);

        const std::string directory = "/tmp/ns3-wifi-streaming-prediction-test";
        std::filesystem::remove_all(directory);
        std::filesystem::create_directories(directory);
        auto prediction = CreateObject<PredictionTelemetryCollector>();
        prediction->SetRunId("prediction-wifi-test");
        prediction->SetSampleOffsetsUs({0, 5000, 20000});
        prediction->SetHistoryWindowsUs({1000, 5000, 20000});
        prediction->SetOracleFeaturesEnabled(true);
        prediction->BindWifiPath(0, stationDevice.Get(0), 0, AC_BE);
        prediction->SetOutputFiles(directory + "/prediction_samples.csv",
                                   directory + "/prediction_events.csv",
                                   directory + "/prediction_polling_samples.csv");

        auto socket = Socket::CreateSocket(station.Get(0), UdpSocketFactory::GetTypeId());
        NS_TEST_ASSERT_MSG_EQ(
            socket->Bind(InetSocketAddress(stationInterface.GetAddress(0), 0)),
            0,
            "Wi-Fi sender bind failed");
        NS_TEST_ASSERT_MSG_EQ(
            socket->Connect(InetSocketAddress(accessPointInterface.GetAddress(0), 9010)),
            0,
            "Wi-Fi sender connect failed");
        auto sender = CreateObject<MultipathSender>();
        sender->SetFrameSource(source);
        sender->SetMetricsCollector(metrics);
        sender->SetPredictionTelemetryCollector(prediction);
        sender->SetPacketPayloadSize(1200);
        sender->SetExpectedMacServiceOverhead(36);
        sender->AddPath(0, socket, stationDevice.Get(0));
        station.Get(0)->AddApplication(sender);
        sender->SetStartTime(Seconds(1));
        sender->SetStopTime(Seconds(1.5));

        Simulator::Stop(Seconds(1.5));
        Simulator::Run();
        prediction->WriteOutputs();

        const auto& samples = prediction->GetSamples();
        NS_TEST_ASSERT_MSG_EQ(samples.size(), 3, "Prediction Wi-Fi snapshots are missing");
        NS_TEST_ASSERT_MSG_EQ(samples.front().packetsSubmitted,
                              0,
                              "Wi-Fi T0 includes sender-caused state");
        NS_TEST_ASSERT_MSG_EQ(samples.front().mpduTxAttemptsTotal.has_value(),
                              true,
                              "Bound Wi-Fi MPDU counters are unsupported");
        NS_TEST_ASSERT_MSG_EQ(samples.front().pollingReport.has_value(),
                              true,
                              "Wi-Fi snapshot omitted its latest available polling report");
        NS_TEST_ASSERT_MSG_EQ(samples.front().sampleTimeNs -
                                  samples.front().pollingReport->captureTimeNs,
                              1000000,
                              "Wi-Fi snapshot did not select the 1 ms-old report");
        NS_TEST_ASSERT_MSG_EQ(samples.front().frameMacServiceBytesNotAcknowledged.has_value(),
                              true,
                              "T0 omitted deterministic MAC service bytes");
        NS_TEST_ASSERT_MSG_EQ(*samples.front().frameMacServiceBytesNotAcknowledged,
                              2572,
                              "T0 MAC service-byte plan is incorrect");
        NS_TEST_ASSERT_MSG_EQ(*samples.front().frameMacServiceBytesPendingPrimary,
                              2572,
                              "T0 primary-pending service bytes are incorrect");
        NS_TEST_ASSERT_MSG_EQ(samples.back().framePacketsTxSucceeded.has_value(),
                              true,
                              "Tagged packet success count is unsupported");
        NS_TEST_ASSERT_MSG_EQ(*samples.back().framePacketsTxSucceeded,
                              2,
                              "Tagged packets were not acknowledged exactly once");
        NS_TEST_ASSERT_MSG_EQ(samples.back().senderMacComplete,
                              true,
                              "MAC-complete frame remains actionable");
        NS_TEST_ASSERT_MSG_EQ(samples.back().actionable,
                              false,
                              "MAC-complete frame was marked actionable");
        NS_TEST_ASSERT_MSG_EQ(samples.back().macQueuePackets.has_value() &&
                                  *samples.back().macQueuePackets == 0,
                              true,
                              "Logical queue mirror retained acknowledged packets");
        NS_TEST_ASSERT_MSG_EQ(samples.back().currentCw.has_value(),
                              true,
                              "Passive current-CW oracle is unsupported");
        NS_TEST_ASSERT_MSG_EQ(samples.back().remainingBackoffSlots.has_value(),
                              false,
                              "Lazily updated backoff getter was exposed as exact");
        NS_TEST_ASSERT_MSG_EQ(
            samples.back().expectedAccessReasonWithinSlack.has_value(),
            false,
            "Behavior-changing expected-access query was exposed as passive");
        NS_TEST_ASSERT_MSG_EQ(samples.back().rolling.size(),
                              3,
                              "Rolling Wi-Fi histories are missing");
        for (const auto& window : samples.back().rolling)
        {
            const double stateSum = window.phyTxTimeUs + window.phyRxTimeUs +
                                    window.phyBusyTimeUs + window.phyIdleTimeUs +
                                    window.phyOtherTimeUs;
            NS_TEST_ASSERT_MSG_EQ_TOL(stateSum,
                                      window.historyCoverageUs,
                                      1e-6,
                                      "PHY state accounting does not cover the full window");
        }
        NS_TEST_ASSERT_MSG_EQ(std::filesystem::is_regular_file(
                                  directory + "/prediction_samples.csv"),
                              true,
                              "Prediction sample output is missing");
        NS_TEST_ASSERT_MSG_EQ(std::filesystem::is_regular_file(
                                  directory + "/prediction_events.csv"),
                              true,
                              "Prediction event output is missing");
        Simulator::Destroy();
        std::filesystem::remove_all(directory);
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
        : TestCase("Random-rate ON-OFF application resamples every ON period")
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

/**
 * Verify compiled predictor parity with sklearn-generated golden vectors.
 */
class PredictionModelParityTestCase : public TestCase
{
  public:
    PredictionModelParityTestCase()
        : TestCase("Compiled prediction models match sklearn")
    {
    }

  private:
    void
    DoRun() override
    {
        NS_TEST_ASSERT_MSG_EQ(PredictionModelEvaluator::GetModelId(),
                              prediction_model_golden_v1::g_modelId,
                              "Compiled model identity differs from its golden artifact");
        NS_TEST_ASSERT_MSG_EQ(PredictionModelEvaluator::GetTargetId(),
                              prediction_model_golden_v1::g_targetId,
                              "Compiled target identity differs from its golden artifact");
        NS_TEST_ASSERT_MSG_EQ(
            PredictionModelEvaluator::GetTargetProvenanceSha256(),
            prediction_model_golden_v1::g_targetProvenanceSha256,
            "Compiled target provenance differs from its golden artifact");
        const auto names = PredictionModelEvaluator::GetFeatureNames();
        NS_TEST_ASSERT_MSG_EQ(names.size(), 86, "Unexpected compiled feature count");
        NS_TEST_ASSERT_MSG_EQ(names.front(),
                              "application_socket_packet_bytes_submitted",
                              "Unexpected first compiled feature");
        NS_TEST_ASSERT_MSG_EQ(names.back(),
                              "ppdu_tx_count_total",
                              "Unexpected last compiled feature");

        for (const auto& golden : prediction_model_golden_v1::g_cases)
        {
            const auto result = PredictionModelEvaluator::Evaluate(golden.stage, golden.features);
            NS_TEST_ASSERT_MSG_EQ_TOL(result.rankingScore,
                                      golden.rankingScore,
                                      1e-11,
                                      "Histogram-gradient-boosting score differs from sklearn");
            NS_TEST_ASSERT_MSG_EQ_TOL(result.calibratedProbability,
                                      golden.calibratedProbability,
                                      1e-12,
                                      "Platt-calibrated probability differs from sklearn");
        }

        bool rejectedWrongWidth = false;
        try
        {
            const std::array<double, 1> wrongWidth{0.0};
            PredictionModelEvaluator::Evaluate(PredictionStage::T0, wrongWidth);
        }
        catch (const std::invalid_argument&)
        {
            rejectedWrongWidth = true;
        }
        NS_TEST_ASSERT_MSG_EQ(rejectedWrongWidth, true, "Wrong-width model input was accepted");
    }
};

class WifiStreamingTestSuite : public TestSuite
{
  public:
    WifiStreamingTestSuite()
        : TestSuite("wifi-streaming", Type::UNIT)
    {
        AddTestCase(new FrameTagTestCase, TestCase::Duration::QUICK);
        AddTestCase(new HeaderTestCase, TestCase::Duration::QUICK);
        AddTestCase(new TraceSourceTestCase, TestCase::Duration::QUICK);
        AddTestCase(new PacketizerTestCase, TestCase::Duration::QUICK);
        AddTestCase(new PredictionCollectorFoundationTestCase, TestCase::Duration::QUICK);
        AddTestCase(new PredictionPollingTestCase, TestCase::Duration::QUICK);
        AddTestCase(new PredictionPhyHistoryTestCase, TestCase::Duration::QUICK);
        AddTestCase(new PredictionMpduAccountingTestCase, TestCase::Duration::QUICK);
        AddTestCase(new CorrelatedLoadControllerTestCase, TestCase::Duration::QUICK);
        AddTestCase(new CorrelationSanityTestCase, TestCase::Duration::QUICK);
        AddTestCase(new PolicyTestCase, TestCase::Duration::QUICK);
        AddTestCase(new RandomizedFrameAssignmentGoldenTestCase, TestCase::Duration::QUICK);
        AddTestCase(new RandomizedFrameAssignmentBoundaryTestCase, TestCase::Duration::QUICK);
        AddTestCase(new RandomizedFrameAssignmentDeterminismTestCase, TestCase::Duration::QUICK);
        AddTestCase(new RandomizedFullCopyExplorationPolicyTestCase,
                    TestCase::Duration::QUICK);
        AddTestCase(new OutputStatisticsTestCase, TestCase::Duration::QUICK);
        AddTestCase(new ReassemblyTestCase, TestCase::Duration::QUICK);
        AddTestCase(new DelayedSecondaryHoldTestCase, TestCase::Duration::QUICK);
        AddTestCase(new FinalizationTestCase, TestCase::Duration::QUICK);
        AddTestCase(new IntegrationDeliveryTestCase, TestCase::Duration::QUICK);
        AddTestCase(new DelayedSecondaryPredictionTrackingTestCase,
                    TestCase::Duration::QUICK);
        AddTestCase(new SelectiveDuplicationControllerTestCase, TestCase::Duration::QUICK);
        AddTestCase(new ExplicitSecondaryPacketSelectionTestCase, TestCase::Duration::QUICK);
        AddTestCase(new SecondaryAirtimeMeterTestCase, TestCase::Duration::QUICK);
        AddTestCase(new SecondaryAirtimeMeterWifiTraceTestCase, TestCase::Duration::QUICK);
        AddTestCase(new AdaptiveAirtimeBucketCreditTestCase, TestCase::Duration::QUICK);
        AddTestCase(new AdaptiveAirtimeDuplicationControllerTestCase, TestCase::Duration::QUICK);
        AddTestCase(new PredictionWifiTelemetryTestCase, TestCase::Duration::QUICK);
        AddTestCase(new FullDuplicationDeliveryTestCase, TestCase::Duration::QUICK);
        AddTestCase(new RandomRateOnOffApplicationTestCase, TestCase::Duration::QUICK);
        AddTestCase(new PredictionModelParityTestCase, TestCase::Duration::QUICK);
    }
};

static WifiStreamingTestSuite g_wifiStreamingTestSuite;

} // namespace
