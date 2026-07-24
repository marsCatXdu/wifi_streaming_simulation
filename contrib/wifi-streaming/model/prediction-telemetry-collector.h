/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef PREDICTION_TELEMETRY_COLLECTOR_H
#define PREDICTION_TELEMETRY_COLLECTOR_H

#include "frame-packetizer.h"

#include "ns3/event-id.h"
#include "ns3/net-device.h"
#include "ns3/object.h"

#include <cstdint>
#include <deque>
#include <fstream>
#include <list>
#include <map>
#include <optional>
#include <string>
#include <vector>

namespace ns3
{

class WifiMacQueue;
class WifiMpdu;
class WifiNetDevice;
class PredictionTelemetryCollectorTestAccess;
class WifiPhy;
class WifiPhyStateHelper;
class WifiPsdu;
class WifiTxVector;
class ChannelAccessManager;
class Txop;
enum AcIndex : uint8_t;
enum WifiMacDropReason : uint8_t;
enum class WifiExpectedAccessReason : uint8_t;
enum class WifiPhyState;

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
    FRAME_PLAN = 0,                ///< Immutable frame and packetization-plan state.
    SOCKET_SUBMISSION_PROGRESS = 1, ///< Application socket submission progress.
    MPDU_OUTCOMES = 2,             ///< Exact tagged MPDU attempts and outcomes.
    MAC_QUEUE = 3,                 ///< Tagged target MAC queue state.
    PHY_OCCUPANCY = 4,             ///< Target PHY state history.
    TX_VECTOR = 5,                 ///< Most recent tagged target TX vector.
    CAUSAL_ORACLE = 6              ///< Exact current MAC/PHY oracle state.
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
 * Exact causal statistics for one rolling history window.
 */
struct PredictionRollingSample
{
    uint64_t windowUs{0};               ///< Rolling window duration.
    uint64_t mpduAttempts{0};            ///< MPDU attempts completed in the window.
    uint64_t mpduSuccesses{0};           ///< Positive MPDU acknowledgements.
    uint64_t mpduAttemptFailures{0};     ///< Unsuccessful nonterminal attempts.
    uint64_t mpduRetries{0};             ///< Attempts following a prior failed attempt.
    std::optional<double> mpduRetryRatio; ///< Retries divided by attempts.
    uint64_t acknowledgedMacServiceBytes{0}; ///< Newly acknowledged MAC service bytes.
    std::optional<double> mpduQueueToAckMeanUs; ///< Mean enqueue-to-ACK time.
    std::optional<double> mpduQueueToAckP95Us;  ///< Type-7 P95 enqueue-to-ACK time.
    std::optional<double> mpduFirstAttemptToAckMeanUs; ///< Mean first-attempt-to-ACK time.
    std::optional<double> mpduFirstAttemptToAckP95Us;  ///< Type-7 P95 first-attempt-to-ACK time.
    double phyTxTimeUs{0};          ///< PHY TX occupancy.
    double phyRxTimeUs{0};          ///< PHY RX occupancy.
    double phyBusyTimeUs{0};        ///< PHY CCA_BUSY occupancy.
    double phyIdleTimeUs{0};        ///< PHY IDLE occupancy.
    double phyOtherTimeUs{0};       ///< PHY SWITCHING, SLEEP, and OFF occupancy.
    std::optional<double> phyTxFraction;    ///< TX fraction of available coverage.
    std::optional<double> phyRxFraction;    ///< RX fraction of available coverage.
    std::optional<double> phyBusyFraction;  ///< CCA_BUSY fraction of available coverage.
    std::optional<double> phyIdleFraction;  ///< IDLE fraction of available coverage.
    std::optional<double> phyOtherFraction; ///< Other-state fraction of available coverage.
    double historyCoverageUs{0};             ///< Available history duration.
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

    std::optional<uint64_t> mpduTxAttemptsTotal;       ///< Cumulative MPDU attempts.
    std::optional<uint64_t> mpduTxSuccessesTotal;      ///< Cumulative positive acknowledgements.
    std::optional<uint64_t> mpduTxAttemptFailuresTotal; ///< Cumulative failed attempts.
    std::optional<uint64_t> mpduRetriesTotal;          ///< Cumulative retry attempts.
    std::optional<uint64_t> mpduTerminalDropsTotal;    ///< Cumulative terminal MPDU drops.
    std::optional<uint64_t> mpduRetryLimitDropsTotal;  ///< Retry-limit terminal drops.
    std::optional<uint64_t> mpduLifetimeDropsTotal;    ///< Lifetime terminal drops.
    std::optional<uint64_t> mpduQueueDropsTotal;       ///< Queue-related terminal drops.
    std::optional<uint64_t> ppduTxCountTotal;          ///< Cumulative target PHY PPDUs.
    std::optional<uint64_t> lastTxAttemptTimeNs;       ///< Most recent tagged attempt.
    std::optional<uint64_t> lastTxSuccessTimeNs;       ///< Most recent tagged success.
    std::optional<uint8_t> currentMcs;                 ///< Most recent tagged MCS.
    std::optional<uint8_t> currentNss;                 ///< Most recent tagged spatial streams.
    std::optional<uint16_t> currentChannelWidthMhz;    ///< Most recent tagged channel width.
    std::optional<uint64_t> currentGuardIntervalNs;    ///< Most recent tagged guard interval.
    std::optional<std::string> frequencyBand;          ///< Bound target PHY frequency band.
    std::optional<double> centerFrequencyMhz;          ///< Bound target PHY center frequency.
    std::optional<double> currentAckSignalDbm;         ///< Most recent ACK signal, if supported.
    std::vector<PredictionRollingSample> rolling;      ///< Configured rolling-window statistics.

    std::optional<uint32_t> framePacketsMacEnqueued; ///< Distinct frame packets first enqueued.
    std::optional<uint32_t> framePacketsMacDequeued; ///< Distinct frame packets first dequeued.
    std::optional<uint32_t> framePacketsTxSucceeded; ///< Distinct positively acknowledged packets.
    std::optional<uint32_t> frameMpduAttemptFailures; ///< Failed attempts attributed to the frame.
    std::optional<uint32_t> framePacketsTerminallyDropped; ///< Distinct terminally dropped packets.
    std::optional<uint32_t> framePacketsCurrentlyQueued;   ///< Frame packets currently queued.
    std::optional<uint64_t> frameMacServiceBytesCurrentlyQueued; ///< Queued frame service bytes.
    std::optional<uint32_t> macQueuePackets;                    ///< Target queue packet count.
    std::optional<uint64_t> macQueueServiceBytes;               ///< Target queue service bytes.
    std::optional<uint64_t> macQueueOldestEnqueueTimeNs;        ///< Oldest target enqueue time.
    std::optional<uint32_t> packetsAheadOfFrame;                ///< Queue packets ahead of frame.
    std::optional<uint64_t> macServiceBytesAheadOfFrame;        ///< Service bytes ahead of frame.
    std::optional<uint32_t> framePacketsPendingPrimary;         ///< Primary-path packets pending.
    std::optional<uint64_t> frameMacServiceBytesNotAcknowledged; ///< Unacknowledged frame work.
    std::optional<uint64_t> frameMacServiceBytesPendingPrimary;  ///< Primary service work pending.

    std::optional<uint32_t> currentCw;                 ///< Current contention window oracle.
    std::optional<uint32_t> remainingBackoffSlots;     ///< Remaining backoff slots oracle.
    std::optional<double> navRemainingUs;              ///< Remaining NAV oracle.
    std::optional<std::string> currentPhyState;        ///< Exact current PHY state.
    std::optional<std::string> channelAccessStatus;    ///< Exact TXOP access state oracle.
    std::optional<bool> mediumBusyNow;                 ///< Channel manager busy state oracle.
    std::optional<std::string> expectedAccessReasonWithinSlack; ///< Access-within-slack reason.

    std::string featureSupportMask{"0x3"}; ///< Canonical supported-family mask.
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
     * Configure strictly increasing positive rolling history windows.
     *
     * @param windowsUs History windows in microseconds.
     */
    void SetHistoryWindowsUs(const std::vector<uint64_t>& windowsUs);

    /**
     * Return resolved rolling history windows.
     *
     * @return Configured windows in microseconds.
     */
    const std::vector<uint64_t>& GetHistoryWindowsUs() const;

    /**
     * Enable or disable causal ns-3 oracle fields.
     *
     * @param enabled True to populate F3 current-state fields.
     */
    void SetOracleFeaturesEnabled(bool enabled);

    /**
     * Bind one application path to its selected sender Wi-Fi device and PHY.
     *
     * @param pathId Application path identifier.
     * @param device Selected sender Wi-Fi device.
     * @param phyId Internal PHY identifier on the device.
     * @param accessCategory Target traffic access category.
     */
    void BindWifiPath(uint8_t pathId,
                      Ptr<NetDevice> device,
                      uint8_t phyId,
                      AcIndex accessCategory);

    /**
     * Configure prediction sample and optional raw-event outputs.
     *
     * An empty event file disables raw event logging.
     *
     * @param samplesFile Prediction sample CSV path.
     * @param eventsFile Optional prediction event CSV path.
     */
    void SetOutputFiles(const std::string& samplesFile, const std::string& eventsFile = "");

    /**
     * Write deterministically sorted prediction samples.
     */
    void WriteOutputs();

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

    /**
     * Calculate a Hyndman-Fan type-7 percentile.
     *
     * @param values Observation vector.
     * @param probability Quantile probability in the closed interval [0, 1].
     * @return Interpolated percentile.
     */
    static double Percentile(std::vector<double> values, double probability);

  protected:
    void DoDispose() override;

  private:
    friend class PredictionTelemetryCollectorTestAccess;

    friend class PredictionTelemetryTraceAdapter;

    struct PacketState
    {
        bool submitted{false};        ///< Socket submission has completed.
        bool enqueued{false};         ///< First MAC enqueue has occurred.
        bool dequeued{false};         ///< First MAC dequeue has occurred.
        bool queued{false};           ///< Packet is currently in the target queue.
        bool acknowledged{false};     ///< Positive MAC acknowledgement was observed.
        bool terminallyDropped{false}; ///< Packet was removed permanently from this path.
        bool attemptPending{false};   ///< Most recent attempt has no outcome yet.
        uint32_t attemptCount{0};     ///< Number of observed PHY attempts.
        uint32_t attemptFailures{0};  ///< Number of unsuccessful attempts.
        std::optional<uint32_t> macServiceBytes; ///< WifiMpdu::GetPacketSize().
        std::optional<uint64_t> enqueueTimeNs;   ///< First MAC enqueue time.
        std::optional<uint64_t> firstAttemptTimeNs; ///< First PHY attempt time.
        const WifiMpdu* stableMpdu{nullptr};        ///< Stable original MPDU identity.
    };

    struct FrameState
    {
        PacketizationPlan plan;             ///< Immutable packetization plan.
        std::vector<PacketState> packets;    ///< Per-packet sender and MAC state.
        uint32_t packetsSubmitted{0};        ///< Number of submitted packets.
        uint64_t submittedBytes{0};          ///< Application socket packet bytes submitted.
        uint32_t packetsMacEnqueued{0};      ///< Distinct packets first enqueued.
        uint32_t packetsMacDequeued{0};      ///< Distinct packets first dequeued.
        uint32_t packetsTxSucceeded{0};      ///< Distinct positively acknowledged packets.
        uint32_t mpduAttemptFailures{0};     ///< Failed attempts attributed to this frame.
        uint32_t packetsTerminallyDropped{0}; ///< Distinct terminally dropped packets.
        bool senderMacComplete{false};       ///< Whether all packets are positively acknowledged.
        uint64_t latestFeatureEventTimeNs{0}; ///< Latest included feature event.
    };

    enum class MacEventKind
    {
        ATTEMPT,
        SUCCESS,
        ATTEMPT_FAILURE
    };

    struct MacEvent
    {
        uint64_t timeNs{0};             ///< Event timestamp.
        MacEventKind kind{MacEventKind::ATTEMPT}; ///< Event type.
        bool retry{false};              ///< Whether an attempt is a retry.
        uint32_t acknowledgedBytes{0};  ///< Service bytes acknowledged by a success.
        std::optional<double> queueToAckUs; ///< Queue-to-ACK observation.
        std::optional<double> firstAttemptToAckUs; ///< First-attempt-to-ACK observation.
    };

    struct PhyInterval
    {
        int64_t startNs{0};          ///< Interval start.
        int64_t endNs{0};            ///< Interval end, possibly beyond the current time.
        WifiPhyState state;          ///< PHY state.
        uint64_t reportedAtNs{0};    ///< Time at which the trace callback reported the interval.
        uint64_t serial{0};          ///< Deterministic ordering for equal report times.
    };

    struct QueueEntry
    {
        const WifiMpdu* stableMpdu{nullptr}; ///< Stable original MPDU identity.
        std::optional<StreamingFrameTag> tag; ///< Target frame tag, when present.
        uint32_t macServiceBytes{0};           ///< WifiMpdu::GetPacketSize().
        uint64_t enqueueTimeNs{0};             ///< Queue insertion time.
    };

    struct PathState
    {
        Ptr<WifiNetDevice> device;        ///< Bound sender Wi-Fi device.
        Ptr<WifiPhy> phy;                 ///< Bound internal PHY.
        Ptr<WifiPhyStateHelper> phyState; ///< Bound PHY state helper.
        Ptr<WifiMacQueue> queue;          ///< Bound target access-category queue.
        Ptr<Txop> txop;                   ///< Bound target access-category TXOP.
        Ptr<ChannelAccessManager> channelAccessManager; ///< Bound channel manager.
        uint8_t phyId{0};                 ///< Internal PHY identifier.
        int64_t telemetryStartNs{0};      ///< Start of available history.
        uint64_t latestFeatureEventTimeNs{0}; ///< Latest path-level feature event.
        uint64_t mpduAttempts{0};         ///< Cumulative tagged attempts.
        uint64_t mpduSuccesses{0};        ///< Cumulative tagged successes.
        uint64_t mpduAttemptFailures{0};  ///< Cumulative tagged failed attempts.
        uint64_t mpduRetries{0};          ///< Cumulative tagged retries.
        uint64_t mpduTerminalDrops{0};    ///< Cumulative tagged terminal drops.
        uint64_t mpduRetryLimitDrops{0};  ///< Retry-limit drops.
        uint64_t mpduLifetimeDrops{0};    ///< Lifetime drops.
        uint64_t mpduQueueDrops{0};       ///< Queue-related drops.
        uint64_t ppduTxCount{0};          ///< Cumulative target-PHY PPDU transmissions.
        std::optional<uint64_t> lastTxAttemptTimeNs; ///< Most recent tagged attempt.
        std::optional<uint64_t> lastTxSuccessTimeNs; ///< Most recent tagged success.
        std::optional<uint8_t> currentMcs;            ///< Most recent tagged MCS.
        std::optional<uint8_t> currentNss;            ///< Most recent tagged NSS.
        std::optional<uint16_t> currentChannelWidthMhz; ///< Most recent tagged width.
        std::optional<uint64_t> currentGuardIntervalNs; ///< Most recent tagged GI.
        std::string frequencyBand;                     ///< Bound PHY band token.
        double centerFrequencyMhz{0};                  ///< Bound PHY center frequency.
        std::deque<MacEvent> macEvents;                ///< Bounded recent MAC history.
        std::deque<PhyInterval> phyIntervals;          ///< Bounded recent PHY history.
        std::list<QueueEntry> queueEntries;            ///< Current access-category queue order.
        uint64_t phyIntervalSerial{0};                 ///< Next interval ordering serial.
    };

    void CaptureSnapshot(PredictionFrameKey key, uint64_t offsetUs);
    void NotifyQueueEnqueue(uint8_t pathId, Ptr<const WifiMpdu> mpdu);
    void NotifyQueueDequeue(uint8_t pathId, Ptr<const WifiMpdu> mpdu);
    void NotifyAckedMpdu(uint8_t pathId, Ptr<const WifiMpdu> mpdu);
    void NotifyNackedMpdu(uint8_t pathId, Ptr<const WifiMpdu> mpdu);
    void NotifyResponseTimeout(uint8_t pathId, Ptr<const WifiMpdu> mpdu);
    void NotifyDroppedMpdu(uint8_t pathId,
                           WifiMacDropReason reason,
                           Ptr<const WifiMpdu> mpdu);
    void NotifyPhyTxBegin(uint8_t pathId, Ptr<const Packet> packet);
    void NotifyPpduTx(uint8_t pathId, const WifiTxVector& txVector, bool taggedTarget);
    void NotifyPhyState(uint8_t pathId, Time start, Time duration, WifiPhyState state);
    void FinalizeAttemptFailure(PathState& path,
                                FrameState& frame,
                                PacketState& packet,
                                const StreamingFrameTag& tag,
                                uint64_t nowNs);
    PredictionRollingSample BuildRollingSample(const PathState& path,
                                               uint64_t nowNs,
                                               uint64_t windowUs) const;
    void PopulateLinkSample(PredictionSample& sample,
                            const PredictionFrameKey& key,
                            const FrameState& frame,
                            PathState& path);
    void PruneHistories(PathState& path, uint64_t nowNs);
    void WriteEvent(const std::string& eventType,
                    uint8_t pathId,
                    const std::optional<StreamingFrameTag>& tag,
                    std::optional<uint32_t> attemptNumber,
                    std::optional<uint32_t> macServiceBytes);
    void WriteEventHeader();
    void WriteSampleHeader(std::ostream& output) const;
    void WriteSample(std::ostream& output, const PredictionSample& sample) const;
    static bool GetTag(Ptr<const WifiMpdu> mpdu, StreamingFrameTag& tag);
    static const WifiMpdu* GetStableMpdu(Ptr<const WifiMpdu> mpdu);
    static std::string MakeSupportMask(bool wifiBound,
                                       bool txVectorSupported,
                                       bool oracleSupported);
    static std::string PhyStateToString(WifiPhyState state);
    static std::string AccessStatusToString(uint8_t status);
    static std::string ExpectedAccessReasonToString(WifiExpectedAccessReason reason);
    static std::string WindowLabel(uint64_t windowUs);
    static PredictionFrameKey MakeKey(const PacketizationPlan& plan);
    static PredictionFrameKey MakeKey(const StreamingFrameTag& tag);

    std::string m_runId{"run"};
    std::vector<uint64_t> m_sampleOffsetsUs{0, 1000, 2000, 4000};
    std::vector<uint64_t> m_historyWindowsUs{1000, 5000, 20000};
    std::map<PredictionFrameKey, FrameState> m_frames;
    std::map<uint8_t, PathState> m_paths;
    std::vector<PredictionSample> m_samples;
    std::vector<EventId> m_snapshotEvents;
    std::string m_samplesFile;
    std::ofstream m_eventsOutput;
    bool m_outputsWritten{false};
    bool m_oracleFeaturesEnabled{false};
};

} // namespace ns3

#endif // PREDICTION_TELEMETRY_COLLECTOR_H
