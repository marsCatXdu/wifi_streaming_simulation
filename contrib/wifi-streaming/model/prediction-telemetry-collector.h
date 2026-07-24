/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef PREDICTION_TELEMETRY_COLLECTOR_H
#define PREDICTION_TELEMETRY_COLLECTOR_H

#include "frame-packetizer.h"

#include "ns3/event-id.h"
#include "ns3/object.h"

#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace ns3
{

/**
 * Source-owned prediction telemetry schema version.
 */
constexpr uint32_t PREDICTION_TELEMETRY_SCHEMA_VERSION = 1;

/**
 * Source-owned prediction event schema version.
 */
constexpr uint32_t PREDICTION_EVENT_SCHEMA_VERSION = 1;

/**
 * Source-owned feature support-mask mapping version.
 */
constexpr uint32_t FEATURE_SUPPORT_MASK_VERSION = 1;

/**
 * Version-1 feature-family support-mask bit assignments.
 */
enum class PredictionFeatureSupportBit : uint32_t
{
    FRAME_PLAN = 0,               ///< Immutable frame and packetization-plan state.
    SOCKET_SUBMISSION_PROGRESS = 1 ///< Application socket submission progress.
};

/**
 * Stable key for one application frame copy on one path.
 */
struct PredictionFrameKey
{
    uint64_t frameId{0}; ///< Application frame identifier.
    uint8_t pathId{0};   ///< Application path identifier.
    uint8_t copyId{0};   ///< Application copy identifier.

    /**
     * Compare keys for deterministic ordered containers.
     *
     * @param other Key to compare.
     * @return True when this key sorts before the other key.
     */
    bool operator<(const PredictionFrameKey& other) const;
};

/**
 * Immutable sender-side observation captured at a configured frame offset.
 */
struct PredictionSample
{
    uint32_t telemetrySchemaVersion{PREDICTION_TELEMETRY_SCHEMA_VERSION};
    std::string runId;                     ///< Stable experiment run identifier.
    PredictionFrameKey key;                ///< Frame-copy identity.
    std::string sampleStage;                ///< Human-readable stage name.
    uint64_t sampleOffsetUs{0};             ///< Offset from frame generation.
    uint64_t sampleTimeNs{0};               ///< Absolute sample time.
    uint64_t latestFeatureEventTimeNs{0};   ///< Latest included feature event.
    uint64_t generationTimeNs{0};           ///< Absolute frame generation time.
    uint64_t deadlineTimeNs{0};             ///< Absolute frame deadline.
    uint64_t frameAgeUs{0};                 ///< Frame age at sampling.
    uint64_t deadlineSlackUs{0};             ///< Time remaining until deadline.
    bool senderMacComplete{false};           ///< Whether every packet is MAC-acknowledged.
    bool actionable{true};                   ///< Whether an adaptive action could still apply.
    uint32_t frameSizeBytes{0};              ///< Encoded-video frame size.
    uint32_t framePacketCount{0};            ///< Planned application packet count.
    FrameType frameType{FrameType::UNKNOWN}; ///< Application frame type.
    uint32_t packetsSubmitted{0};            ///< Packets accepted by the selected socket.
    uint64_t applicationSocketPacketBytesSubmitted{0}; ///< Submitted socket packet bytes.
    uint32_t packetsRemainingToSubmit{0};              ///< Planned packets not yet submitted.
    std::string featureSupportMask{"0x3"};              ///< Canonical supported-family mask.
};

/**
 * Passive frame-aligned sender telemetry collector.
 *
 * This foundation records immutable packetization state and socket-submission
 * progress. MAC and PHY observers extend the same state without changing
 * sender policy or protocol behavior.
 */
class PredictionTelemetryCollector : public Object
{
  public:
    /**
     * Return the runtime type information.
     *
     * @return The object TypeId.
     */
    static TypeId GetTypeId();

    PredictionTelemetryCollector();
    ~PredictionTelemetryCollector() override;

    /**
     * Set the stable run identifier emitted with every sample.
     *
     * @param runId Stable run identifier.
     */
    void SetRunId(const std::string& runId);

    /**
     * Configure strictly increasing pre-deadline sample offsets.
     *
     * The first offset must be zero so T0 can be captured synchronously.
     *
     * @param offsetsUs Sample offsets in microseconds.
     */
    void SetSampleOffsetsUs(const std::vector<uint64_t>& offsetsUs);

    /**
     * Return resolved sample offsets.
     *
     * @return Configured offsets in microseconds.
     */
    const std::vector<uint64_t>& GetSampleOffsetsUs() const;

    /**
     * Register an immutable frame plan and synchronously capture T0.
     *
     * @param plan Packetization plan for one path and copy.
     */
    void RegisterFrame(const PacketizationPlan& plan);

    /**
     * Record successful submission of one tagged application packet.
     *
     * @param tag Stable frame packet identity.
     * @param applicationSocketPacketBytes Packet size immediately before socket submission.
     */
    void RecordPacketSubmitted(const StreamingFrameTag& tag,
                               uint32_t applicationSocketPacketBytes);

    /**
     * Return all captured samples in callback order.
     *
     * @return Immutable sample vector.
     */
    const std::vector<PredictionSample>& GetSamples() const;

    /**
     * Return the number of registered frame copies.
     *
     * @return Registered frame-copy count.
     */
    std::size_t GetRegisteredFrameCount() const;

    /**
     * Convert a configured offset to a stable stage name.
     *
     * @param offsetUs Sample offset in microseconds.
     * @return Stage name.
     */
    static std::string MakeStageName(uint64_t offsetUs);

  protected:
    void DoDispose() override;

  private:
    struct FrameState
    {
        PacketizationPlan plan;        ///< Immutable packetization plan.
        std::vector<bool> submitted;   ///< Per-packet socket submission state.
        uint32_t packetsSubmitted{0};  ///< Number of submitted packets.
        uint64_t submittedBytes{0};    ///< Application socket packet bytes submitted.
        bool senderMacComplete{false}; ///< Whether all packets are positively acknowledged.
        uint64_t latestFeatureEventTimeNs{0}; ///< Latest included feature event.
    };

    void CaptureSnapshot(PredictionFrameKey key, uint64_t offsetUs);
    static PredictionFrameKey MakeKey(const PacketizationPlan& plan);
    static PredictionFrameKey MakeKey(const StreamingFrameTag& tag);

    std::string m_runId{"run"};
    std::vector<uint64_t> m_sampleOffsetsUs{0, 1000, 2000, 4000};
    std::map<PredictionFrameKey, FrameState> m_frames;
    std::vector<PredictionSample> m_samples;
    std::vector<EventId> m_snapshotEvents;
};

} // namespace ns3

#endif // PREDICTION_TELEMETRY_COLLECTOR_H
