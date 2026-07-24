/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "prediction-telemetry-collector.h"

#include "ns3/abort.h"
#include "ns3/simulator.h"

#include <sstream>
#include <tuple>
#include <utility>

namespace ns3
{

NS_OBJECT_ENSURE_REGISTERED(PredictionTelemetryCollector);

bool
PredictionFrameKey::operator<(const PredictionFrameKey& other) const
{
    return std::tie(frameId, pathId, copyId) <
           std::tie(other.frameId, other.pathId, other.copyId);
}

TypeId
PredictionTelemetryCollector::GetTypeId()
{
    static TypeId tid = TypeId("ns3::PredictionTelemetryCollector")
                            .SetParent<Object>()
                            .SetGroupName("WifiStreaming")
                            .AddConstructor<PredictionTelemetryCollector>();
    return tid;
}

PredictionTelemetryCollector::PredictionTelemetryCollector() = default;

PredictionTelemetryCollector::~PredictionTelemetryCollector() = default;

void
PredictionTelemetryCollector::SetRunId(const std::string& runId)
{
    NS_ABORT_MSG_IF(runId.empty(), "Prediction telemetry run ID cannot be empty");
    m_runId = runId;
}

void
PredictionTelemetryCollector::SetSampleOffsetsUs(const std::vector<uint64_t>& offsetsUs)
{
    NS_ABORT_MSG_IF(!m_frames.empty(), "Cannot change sample offsets after frame registration");
    NS_ABORT_MSG_IF(offsetsUs.empty(), "Prediction sample offsets cannot be empty");
    NS_ABORT_MSG_IF(offsetsUs.front() != 0, "Prediction sample offsets must begin with T0");
    for (std::size_t index = 1; index < offsetsUs.size(); ++index)
    {
        NS_ABORT_MSG_IF(offsetsUs[index] <= offsetsUs[index - 1],
                        "Prediction sample offsets must be strictly increasing");
    }
    m_sampleOffsetsUs = offsetsUs;
}

const std::vector<uint64_t>&
PredictionTelemetryCollector::GetSampleOffsetsUs() const
{
    return m_sampleOffsetsUs;
}

PredictionFrameKey
PredictionTelemetryCollector::MakeKey(const PacketizationPlan& plan)
{
    return {plan.frame.frameId, plan.pathId, plan.copyId};
}

PredictionFrameKey
PredictionTelemetryCollector::MakeKey(const StreamingFrameTag& tag)
{
    return {tag.frameId, tag.pathId, tag.copyId};
}

void
PredictionTelemetryCollector::RegisterFrame(const PacketizationPlan& plan)
{
    const int64_t nowNs = Simulator::Now().GetNanoSeconds();
    NS_ABORT_MSG_IF(nowNs < 0 || plan.frame.generationTimeNs != static_cast<uint64_t>(nowNs),
                    "Prediction frame registration must occur at generation time");
    NS_ABORT_MSG_IF(plan.frame.packetCount == 0 ||
                        plan.frame.packetCount != plan.packets.size(),
                    "Packetization plan has inconsistent packet count");
    NS_ABORT_MSG_IF(plan.frame.deadlineUs == 0,
                    "Prediction telemetry requires a positive frame deadline");

    uint64_t payloadBytes = 0;
    for (std::size_t index = 0; index < plan.packets.size(); ++index)
    {
        NS_ABORT_MSG_IF(plan.packets[index].packetIndex != index,
                        "Packetization plan indexes must be contiguous");
        payloadBytes += plan.packets[index].applicationPayloadBytes;
    }
    NS_ABORT_MSG_IF(payloadBytes != plan.frame.frameSizeBytes,
                    "Packetization plan payload bytes do not equal frame size");
    for (const auto offsetUs : m_sampleOffsetsUs)
    {
        NS_ABORT_MSG_IF(offsetUs >= plan.frame.deadlineUs,
                        "Prediction sample offset must precede the frame deadline");
    }

    const auto key = MakeKey(plan);
    FrameState state;
    state.plan = plan;
    state.submitted.resize(plan.frame.packetCount, false);
    state.latestFeatureEventTimeNs = plan.frame.generationTimeNs;
    const auto [iterator, inserted] = m_frames.emplace(key, std::move(state));
    NS_ABORT_MSG_IF(!inserted,
                    "Prediction frame copy was registered more than once: frame "
                        << key.frameId << " path " << +key.pathId << " copy " << +key.copyId);
    (void)iterator;

    // T0 is deliberately synchronous. No packet has been materialized,
    // submitted, or queued when this call executes in MultipathSender.
    CaptureSnapshot(key, 0);
    for (std::size_t index = 1; index < m_sampleOffsetsUs.size(); ++index)
    {
        const auto offsetUs = m_sampleOffsetsUs[index];
        m_snapshotEvents.push_back(
            Simulator::Schedule(MicroSeconds(offsetUs),
                                &PredictionTelemetryCollector::CaptureSnapshot,
                                this,
                                key,
                                offsetUs));
    }
}

void
PredictionTelemetryCollector::RecordPacketSubmitted(
    const StreamingFrameTag& tag,
    uint32_t applicationSocketPacketBytes)
{
    NS_ABORT_MSG_IF(!tag.IsValid(), "Cannot record an invalid streaming frame tag");
    const auto key = MakeKey(tag);
    auto iterator = m_frames.find(key);
    NS_ABORT_MSG_IF(iterator == m_frames.end(),
                    "Submitted packet belongs to an unregistered prediction frame");
    auto& state = iterator->second;
    NS_ABORT_MSG_IF(tag.packetCount != state.plan.frame.packetCount ||
                        tag.generationTimeNs != state.plan.frame.generationTimeNs ||
                        tag.deadlineTimeNs != state.plan.frame.generationTimeNs +
                                                  static_cast<uint64_t>(
                                                      state.plan.frame.deadlineUs) *
                                                      1000 ||
                        tag.frameSizeBytes != state.plan.frame.frameSizeBytes ||
                        tag.frameType != state.plan.frame.frameType,
                    "Submitted packet tag disagrees with its immutable frame plan");
    NS_ABORT_MSG_IF(tag.packetIndex >= state.submitted.size(),
                    "Submitted packet index exceeds the frame plan");
    NS_ABORT_MSG_IF(state.submitted[tag.packetIndex],
                    "Application packet was submitted more than once on one frame copy");

    state.submitted[tag.packetIndex] = true;
    ++state.packetsSubmitted;
    state.submittedBytes += applicationSocketPacketBytes;
    state.latestFeatureEventTimeNs = Simulator::Now().GetNanoSeconds();
}

void
PredictionTelemetryCollector::CaptureSnapshot(PredictionFrameKey key, uint64_t offsetUs)
{
    const auto iterator = m_frames.find(key);
    NS_ABORT_MSG_IF(iterator == m_frames.end(), "Snapshot references an unknown frame copy");
    const auto& state = iterator->second;
    const uint64_t sampleTimeNs = Simulator::Now().GetNanoSeconds();
    const uint64_t expectedTimeNs =
        state.plan.frame.generationTimeNs + offsetUs * static_cast<uint64_t>(1000);
    NS_ABORT_MSG_IF(sampleTimeNs != expectedTimeNs,
                    "Prediction snapshot did not execute at its configured offset");
    const uint64_t deadlineTimeNs =
        state.plan.frame.generationTimeNs +
        static_cast<uint64_t>(state.plan.frame.deadlineUs) * 1000;
    NS_ABORT_MSG_IF(sampleTimeNs >= deadlineTimeNs,
                    "Prediction snapshot executed at or after the frame deadline");
    NS_ABORT_MSG_IF(state.latestFeatureEventTimeNs > sampleTimeNs,
                    "Prediction snapshot contains a future feature event");

    PredictionSample sample;
    sample.runId = m_runId;
    sample.key = key;
    sample.sampleStage = MakeStageName(offsetUs);
    sample.sampleOffsetUs = offsetUs;
    sample.sampleTimeNs = sampleTimeNs;
    sample.latestFeatureEventTimeNs = state.latestFeatureEventTimeNs;
    sample.generationTimeNs = state.plan.frame.generationTimeNs;
    sample.deadlineTimeNs = deadlineTimeNs;
    sample.frameAgeUs = offsetUs;
    sample.deadlineSlackUs = state.plan.frame.deadlineUs - offsetUs;
    sample.senderMacComplete = state.senderMacComplete;
    sample.actionable = !state.senderMacComplete && sampleTimeNs < deadlineTimeNs;
    sample.frameSizeBytes = state.plan.frame.frameSizeBytes;
    sample.framePacketCount = state.plan.frame.packetCount;
    sample.frameType = state.plan.frame.frameType;
    sample.packetsSubmitted = state.packetsSubmitted;
    sample.applicationSocketPacketBytesSubmitted = state.submittedBytes;
    sample.packetsRemainingToSubmit =
        state.plan.frame.packetCount - state.packetsSubmitted;
    m_samples.push_back(std::move(sample));
}

const std::vector<PredictionSample>&
PredictionTelemetryCollector::GetSamples() const
{
    return m_samples;
}

std::size_t
PredictionTelemetryCollector::GetRegisteredFrameCount() const
{
    return m_frames.size();
}

std::string
PredictionTelemetryCollector::MakeStageName(uint64_t offsetUs)
{
    if (offsetUs % 1000 == 0)
    {
        return "T" + std::to_string(offsetUs / 1000);
    }
    return "offset_" + std::to_string(offsetUs) + "us";
}

void
PredictionTelemetryCollector::DoDispose()
{
    for (auto& event : m_snapshotEvents)
    {
        event.Cancel();
    }
    m_snapshotEvents.clear();
    Object::DoDispose();
}

} // namespace ns3
