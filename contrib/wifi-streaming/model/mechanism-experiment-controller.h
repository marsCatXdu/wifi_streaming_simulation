/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef MECHANISM_EXPERIMENT_CONTROLLER_H
#define MECHANISM_EXPERIMENT_CONTROLLER_H

#include "multipath-sender.h"
#include "secondary-airtime-meter.h"

#include "ns3/object.h"

#include <cstdint>
#include <fstream>
#include <map>
#include <optional>
#include <string>
#include <vector>

namespace ns3
{

/**
 * T2 action used by the packet-repair mechanism experiment.
 */
enum class MechanismT2Action
{
    OBSERVE,            ///< Record paired T2 state without a delayed action.
    FULL_COPY,          ///< Launch the canonical full secondary copy.
    ORACLE_REPAIR,      ///< Launch hindsight-selected original source packets.
    SYSTEMATIC_REPAIR,  ///< Launch ideal innovative coded-repair symbols.
};

/**
 * @ingroup wifi-streaming
 * Record paired T2 queue/ACK state and execute one frozen mechanism action.
 *
 * The oracle action reads packet indexes from the exact packet-outcome
 * sidecar of a paired primary-only run. It is deliberately privileged
 * hindsight replay, not a causal policy. The systematic action uses the
 * simulator's ideal MDS-style repair abstraction.
 */
class MechanismExperimentController : public Object
{
  public:
    /** CSV schema shared by snapshot and action outputs. */
    static constexpr uint32_t CSV_SCHEMA_VERSION = 1;

    /** Frozen action offset in microseconds. */
    static constexpr uint64_t T2_OFFSET_US = 2000;

    /** Fixed primary application path. */
    static constexpr uint8_t PRIMARY_PATH_ID = 1;

    /** Fixed secondary application path. */
    static constexpr uint8_t SECONDARY_PATH_ID = 0;

    /** Fixed primary copy identifier. */
    static constexpr uint8_t PRIMARY_COPY_ID = 0;

    /** Fixed secondary copy identifier. */
    static constexpr uint8_t SECONDARY_COPY_ID = 1;

    /**
     * Return runtime type information.
     *
     * @return Object TypeId.
     */
    static TypeId GetTypeId();

    MechanismExperimentController();
    ~MechanismExperimentController() override;

    /**
     * Set the sender owning delayed secondary plans.
     *
     * @param sender Sender whose lifetime covers all callbacks.
     */
    void SetSender(MultipathSender* sender);

    /**
     * Set the passive secondary airtime meter.
     *
     * @param meter Meter used for exact per-frame action accounting.
     */
    void SetAirtimeMeter(Ptr<SecondaryAirtimeMeter> meter);

    /**
     * Select the frozen mechanism action.
     *
     * @param action Action executed after the paired T2 snapshots.
     */
    void SetAction(MechanismT2Action action);

    /**
     * Set the systematic repair denominator.
     *
     * The action sends ceil(source packet count / divisor) repair symbols.
     *
     * @param divisor Positive repair denominator.
     */
    void SetSystematicRepairDivisor(uint32_t divisor);

    /**
     * Load a packet-outcome sidecar for privileged oracle replay.
     *
     * @param path Exact CSV emitted by the paired primary-only run.
     */
    void SetOraclePacketOutcomeFile(const std::string& path);

    /**
     * Configure deterministic controller outputs.
     *
     * @param runId Stable run identifier.
     * @param snapshotsFile One-row-per-path T2 state CSV.
     * @param actionsFile One-row-per-frame action CSV.
     */
    void SetOutputFiles(const std::string& runId,
                        const std::string& snapshotsFile,
                        const std::string& actionsFile);

    /**
     * Consume one immutable prediction snapshot.
     *
     * @param sample Snapshot emitted by paired prediction telemetry.
     */
    void NotifySnapshot(const PredictionSample& sample);

    /** @return Number of complete paired T2 observations. */
    uint64_t GetPairedFrameCount() const;

    /** @return Number of delayed actions accepted by the sender. */
    uint64_t GetLaunchCount() const;

    /** @return Stable action label used in output. */
    std::string GetActionName() const;

  protected:
    void DoDispose() override;

  private:
    /** Exact privileged oracle plan for one frame. */
    struct OraclePlan
    {
        uint32_t sourcePacketCount{0};       ///< Source packet cardinality.
        std::vector<uint32_t> packetIndices; ///< Deadline-missing source indexes.
    };

    /** Paired T2 samples retained until both paths are observed. */
    struct FrameState
    {
        std::optional<PredictionSample> primary;   ///< Path 1/copy 0 sample.
        std::optional<PredictionSample> secondary; ///< Path 0/copy 1 sample.
        bool processed{false};                     ///< Whether output/action is final.
    };

    /** Start control after all required dependencies are configured. */
    void StartControl();

    /**
     * Return whether a sample is the fixed primary identity.
     *
     * @param sample Sample to inspect.
     * @return True for path 1/copy 0.
     */
    static bool IsPrimary(const PredictionSample& sample);

    /**
     * Return whether a sample is the fixed secondary identity.
     *
     * @param sample Sample to inspect.
     * @return True for path 0/copy 1.
     */
    static bool IsSecondary(const PredictionSample& sample);

    /**
     * Process one newly complete primary/secondary T2 pair.
     *
     * @param frameId Application frame identifier.
     * @param state Complete paired state.
     */
    void ProcessPair(uint64_t frameId, FrameState& state);

    /**
     * Build the exact descriptor selected by the configured action.
     *
     * @param primary Primary T2 sample.
     * @param selectedIndices Selected original or repair-symbol indexes.
     * @param reason Stable action reason to update.
     * @return Descriptor, or empty when the action intentionally sends nothing.
     */
    std::optional<DelayedCopyDescriptor> SelectActionDescriptor(
        const PredictionSample& primary,
        std::vector<uint32_t>& selectedIndices,
        std::string& reason) const;

    /**
     * Launch the configured action after its meter reservation is installed.
     *
     * @param frameId Application frame identifier.
     * @param selectedIndices Exact action packet indexes.
     * @param reason Stable policy reason.
     * @return True when the sender accepted the action.
     */
    bool LaunchAction(uint64_t frameId,
                      const std::vector<uint32_t>& selectedIndices,
                      const std::string& reason);

    /**
     * Write one T2 sample row.
     *
     * @param sample Path-specific T2 sample.
     * @param ackDeficit Exact primary ACK-deficit indexes.
     */
    void WriteSnapshot(const PredictionSample& sample,
                       const std::vector<uint32_t>& ackDeficit);

    /**
     * Write the final action row for one frame.
     *
     * @param primary Primary T2 sample.
     * @param requested Whether an action descriptor was selected.
     * @param launched Whether the sender accepted the action.
     * @param reason Stable outcome reason.
     * @param descriptor Selected descriptor, if any.
     * @param nominalAirtimeUs Nominal pre-launch airtime estimate.
     */
    void WriteAction(const PredictionSample& primary,
                     bool requested,
                     bool launched,
                     const std::string& reason,
                     const std::optional<DelayedCopyDescriptor>& descriptor,
                     double nominalAirtimeUs);

    /** Write the snapshot CSV header. */
    void WriteSnapshotHeader();

    /** Write the action CSV header. */
    void WriteActionHeader();

    MultipathSender* m_sender{nullptr}; ///< Non-owning sender pointer.
    Ptr<SecondaryAirtimeMeter> m_meter; ///< Passive action airtime meter.
    MechanismT2Action m_action{MechanismT2Action::OBSERVE}; ///< Frozen action.
    uint32_t m_repairDivisor{8}; ///< Systematic repair denominator.
    std::string m_oraclePacketOutcomeFile; ///< Privileged source artifact path.
    std::map<uint64_t, OraclePlan> m_oraclePlans; ///< Plans keyed by frame ID.
    std::map<uint64_t, FrameState> m_frames; ///< Incomplete paired T2 state.
    std::string m_runId; ///< Expected snapshot run identifier.
    std::ofstream m_snapshots; ///< Path-specific T2 state output.
    std::ofstream m_actions; ///< Per-frame action output.
    bool m_started{false}; ///< Whether dependency validation completed.
    uint64_t m_pairedFrames{0}; ///< Completed T2 pair count.
    uint64_t m_launches{0}; ///< Accepted delayed action count.
};

} // namespace ns3

#endif // MECHANISM_EXPERIMENT_CONTROLLER_H
