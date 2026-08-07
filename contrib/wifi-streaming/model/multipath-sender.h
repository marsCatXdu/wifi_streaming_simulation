/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef MULTIPATH_SENDER_H
#define MULTIPATH_SENDER_H

#include "frame-packetizer.h"
#include "frame-source.h"
#include "metrics-collector.h"
#include "prediction-telemetry-collector.h"
#include "redundancy-policy.h"

#include "ns3/application.h"
#include "ns3/event-id.h"
#include "ns3/net-device.h"
#include "ns3/socket.h"

#include <map>
#include <optional>
#include <string>
#include <vector>

namespace ns3
{

/**
 * Causal descriptor for a delayed secondary copy that has not yet launched.
 */
struct DelayedCopyDescriptor
{
    uint64_t frameId{0};                 ///< Application frame identifier.
    uint32_t framePacketCount{0};        ///< Total packet count required by the frame.
    uint32_t packetCount{0};             ///< Selected secondary packet count.
    std::vector<uint32_t> packetIndices; ///< Original packet indexes in launch order.
    uint64_t expectedMacServiceBytes{0}; ///< Sum of expected MAC service bytes.
    uint64_t deadlineTimeNs{0};          ///< Absolute frame deadline.
};

/**
 * Frame-oriented sender making exactly one redundancy-policy decision per
 * frame. Initial policy paths carry complete copies; a delayed controller may
 * project the secondary copy to a packet subset at launch.
 */
class MultipathSender : public Application
{
  public:
    static TypeId GetTypeId();
    MultipathSender();
    ~MultipathSender() override;

    void SetFrameSource(Ptr<FrameSource> source);
    void SetMetricsCollector(Ptr<MetricsCollector> collector);

    /**
     * Attach the passive prediction telemetry collector.
     *
     * @param collector Collector that receives plans and submission progress.
     */
    void SetPredictionTelemetryCollector(Ptr<PredictionTelemetryCollector> collector);

    /**
     * Enable paired prediction telemetry for the canonical delayed copy.
     *
     * Disabled by default. When enabled, the delayed copy is registered at
     * frame generation and any subsequently launched packets are recorded as
     * submissions against that immutable full-copy plan. A prediction
     * telemetry collector must be attached before the application starts.
     *
     * @param enabled True to track delayed secondary frame copies.
     */
    void SetDelayedSecondaryPredictionTrackingEnabled(bool enabled);

    void SetPacketPayloadSize(uint32_t bytes);
    /**
     * Set exact lower-layer bytes added after socket submission.
     *
     * @param bytes UDP, IP, and LLC/SNAP bytes added before MAC service.
     */
    void SetExpectedMacServiceOverhead(uint32_t bytes);
    void SetEmissionMode(EmissionMode mode);
    void SetEmissionSpan(Time span);
    void SetRunIdHash(uint64_t hash);
    void SetPrimaryPath(PathId pathId);
    void SetPolicy(Ptr<RedundancyPolicy> policy);
    void AddPath(PathId pathId, Ptr<Socket> socket, Ptr<NetDevice> device = nullptr);

    /**
     * Configure a secondary path that a causal controller may launch later.
     *
     * @param pathId Secondary application path.
     */
    void SetDelayedSecondaryPath(PathId pathId);

    /**
     * Return the delayed secondary descriptor for one active frame.
     *
     * @param frameId Application frame identifier.
     * @return Descriptor when the frame is pending; empty otherwise.
     */
    std::optional<DelayedCopyDescriptor> GetDelayedSecondaryCopyDescriptor(
        uint64_t frameId) const;

    /**
     * Return a descriptor for a reverse-ordered tail of a delayed copy.
     *
     * @param frameId Application frame identifier.
     * @param packetCount Number of original tail packets to select.
     * @return Projected descriptor when the frame is pending; empty otherwise.
     */
    std::optional<DelayedCopyDescriptor> GetDelayedSecondaryReverseTailDescriptor(
        uint64_t frameId,
        uint32_t packetCount) const;

    /**
     * Return a descriptor for explicitly ordered delayed-copy packets.
     *
     * @param frameId Application frame identifier.
     * @param packetIndices Distinct original packet indexes in launch order.
     * @return Projected descriptor when the frame is pending; empty otherwise.
     */
    std::optional<DelayedCopyDescriptor> GetDelayedSecondaryPacketDescriptor(
        uint64_t frameId,
        const std::vector<uint32_t>& packetIndices) const;

    /**
     * Return a descriptor for ideal systematic coded-repair symbols.
     *
     * @param frameId Application frame identifier.
     * @param repairPacketCount Number of innovative repair symbols.
     * @return Projected descriptor when the frame is pending; empty otherwise.
     */
    std::optional<DelayedCopyDescriptor> GetDelayedSecondaryCodedRepairDescriptor(
        uint64_t frameId,
        uint32_t repairPacketCount) const;

    /**
     * Query primary packet indexes not yet positively acknowledged.
     *
     * @param key Registered primary frame-copy identity.
     * @return Ascending unacknowledged indexes when telemetry is available;
     * empty optional otherwise.
     */
    std::optional<std::vector<uint32_t>> GetUnacknowledgedPacketIndices(
        const PredictionFrameKey& key) const;

    /**
     * Launch the preplanned secondary copy for one active frame.
     *
     * @param frameId Application frame identifier.
     * @return True when the copy was launched; false when the request was
     * rejected because it was unknown, repeated, or expired.
     */
    bool RequestSecondaryCopy(uint64_t frameId);

    /**
     * Launch the preplanned secondary copy with an explicit policy reason.
     *
     * @param frameId Application frame identifier.
     * @param reason Policy decision reason recorded in metrics.
     * @return True when the copy was launched.
     */
    bool RequestSecondaryCopy(uint64_t frameId, const std::string& reason);

    /**
     * Launch a reverse-ordered tail of the preplanned secondary copy.
     *
     * @param frameId Application frame identifier.
     * @param packetCount Number of original tail packets to launch.
     * @param reason Policy decision reason recorded in metrics.
     * @return True when the partial copy was launched.
     */
    bool RequestSecondaryReverseTail(uint64_t frameId,
                                     uint32_t packetCount,
                                     const std::string& reason);

    /**
     * Launch explicitly ordered packets from the preplanned secondary copy.
     *
     * @param frameId Application frame identifier.
     * @param packetIndices Distinct original packet indexes in launch order.
     * @param reason Policy decision reason recorded in metrics.
     * @return True when the partial copy was launched.
     */
    bool RequestSecondaryPackets(uint64_t frameId,
                                 const std::vector<uint32_t>& packetIndices,
                                 const std::string& reason);

    /**
     * Launch ideal systematic coded-repair symbols on the delayed path.
     *
     * @param frameId Application frame identifier.
     * @param repairPacketCount Number of innovative repair symbols.
     * @param reason Policy decision reason recorded in metrics.
     * @return True when the repair action was launched.
     */
    bool RequestSecondaryCodedRepair(uint64_t frameId,
                                     uint32_t repairPacketCount,
                                     const std::string& reason);

    uint64_t GetPacketsSent() const;
    uint64_t GetBytesSent() const;
    uint64_t GetRedundantBytesSent() const;
    uint64_t GetPathBytesSent(PathId pathId) const;
    uint64_t GetPathRedundantBytesSent(PathId pathId) const;

  protected:
    void StartApplication() override;
    void StopApplication() override;

  private:
    struct Path
    {
        Ptr<Socket> socket;
        Ptr<NetDevice> device;
    };

    struct DelayedFrameState
    {
        PacketizationPlan secondaryPlan; ///< Candidate delayed copy.
        bool launched{false};            ///< Whether the copy was launched.
    };

    void GenerateFrame(FrameDescriptor frame);
    /**
     * Describe one full or projected secondary plan.
     *
     * @param plan Secondary plan to describe.
     * @return Causal descriptor used for admission and accounting.
     */
    static DelayedCopyDescriptor DescribeDelayedPlan(const PacketizationPlan& plan);

    /**
     * Launch one resolved delayed plan.
     *
     * @param frameId Application frame identifier.
     * @param packetIndices Ordered packet subset; empty optional for full copy.
     * @param reason Policy reason recorded in metrics.
     * @return True when the copy was launched.
     */
    bool RequestSecondaryCopyInternal(uint64_t frameId,
                                      const std::optional<std::vector<uint32_t>>& packetIndices,
                                      const std::string& reason);

    /**
     * Launch a fully resolved delayed plan.
     *
     * @param frame Delayed-frame state being consumed.
     * @param launchPlan Exact plan to schedule.
     * @param reason Policy reason recorded in metrics.
     * @param predictionTracked Whether packet progress belongs to a registered plan.
     * @return True after the launch is recorded.
     */
    bool LaunchDelayedPlan(DelayedFrameState& frame,
                           const PacketizationPlan& launchPlan,
                           const std::string& reason,
                           bool predictionTracked);
    void ScheduleCopy(const PacketizationPlan& plan,
                      bool redundant,
                      bool predictionTracked = true);
    void SendPacket(PathId pathId,
                    Ptr<Packet> packet,
                    StreamingFrameTag frameTag,
                    bool redundant,
                    bool predictionTracked);

    Ptr<FrameSource> m_source;
    Ptr<MetricsCollector> m_collector;
    Ptr<PredictionTelemetryCollector> m_predictionCollector;
    FramePacketizer m_packetizer;
    std::map<PathId, Path> m_paths;
    std::map<uint64_t, DelayedFrameState> m_delayedFrames;
    std::optional<PathId> m_delayedSecondaryPath;
    bool m_delayedSecondaryPredictionTrackingEnabled{false}; ///< Track delayed-copy telemetry.
    PathId m_primaryPath{0};
    Ptr<RedundancyPolicy> m_policy;
    LinkTelemetrySnapshot m_telemetry;
    uint64_t m_runIdHash{0};
    uint64_t m_packetsSent{0};
    uint64_t m_bytesSent{0};
    uint64_t m_redundantBytesSent{0};
    std::map<PathId, uint64_t> m_pathBytesSent;
    std::map<PathId, uint64_t> m_pathRedundantBytesSent;
    std::vector<EventId> m_events;
};

} // namespace ns3

#endif // MULTIPATH_SENDER_H
