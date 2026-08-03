/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef RANDOMIZED_INTERVENTION_CONTROLLER_H
#define RANDOMIZED_INTERVENTION_CONTROLLER_H

#include "multipath-sender.h"
#include "randomized-frame-assignment.h"
#include "secondary-airtime-meter.h"

#include "ns3/object.h"

#include <cstdint>
#include <fstream>
#include <map>
#include <optional>
#include <set>
#include <string>
#include <string_view>

namespace ns3
{

/**
 * @internal Test-only access to randomized intervention validation state.
 * @endinternal
 */
class RandomizedInterventionControllerTestAccess;

/**
 * @ingroup wifi-streaming
 * Execute frame-randomized delayed full-copy interventions.
 *
 * The controller consumes paired primary path 1/copy 0 and hypothetical
 * secondary path 0/copy 1 snapshots. Assignment occurs exactly once after
 * both T2 snapshots have been captured. A FULL_COPY_T2 intervention launches
 * immediately after that pair; a FULL_COPY_T4 intervention waits for both T4
 * snapshots. The randomized action is deliberately not token-gated.
 */
class RandomizedInterventionController : public Object
{
  public:
    /** Randomized assignment and execution CSV schema version. */
    static constexpr uint32_t CSV_SCHEMA_VERSION = 1;

    /** T2 snapshot offset in microseconds. */
    static constexpr uint64_t T2_OFFSET_US = 2000;

    /** T4 snapshot offset in microseconds. */
    static constexpr uint64_t T4_OFFSET_US = 4000;

    /** Fixed primary path for the paired experiment contract. */
    static constexpr uint8_t PRIMARY_PATH_ID = 1;

    /** Fixed delayed-secondary path for the paired experiment contract. */
    static constexpr uint8_t SECONDARY_PATH_ID = 0;

    /** Fixed primary copy identifier. */
    static constexpr uint8_t PRIMARY_COPY_ID = 0;

    /** Fixed delayed-secondary copy identifier. */
    static constexpr uint8_t SECONDARY_COPY_ID = 1;

    /**
     * Return runtime type information.
     *
     * @return The object TypeId.
     */
    static TypeId GetTypeId();

    RandomizedInterventionController();
    ~RandomizedInterventionController() override;

    /**
     * Set the sender that owns canonical delayed copies.
     *
     * @param sender Sender whose lifetime covers every snapshot callback.
     */
    void SetSender(MultipathSender* sender);

    /**
     * Set the passive meter used to reserve and settle launched-copy airtime.
     *
     * @param meter Shared secondary airtime meter.
     */
    void SetAirtimeMeter(Ptr<SecondaryAirtimeMeter> meter);

    /**
     * Set the immutable randomization tuple and arm probabilities.
     *
     * @param salt Explicit experiment salt.
     * @param seed ns-3 experiment seed.
     * @param run ns-3 experiment run number.
     * @param t2Probability Probability of FULL_COPY_T2.
     * @param t4Probability Probability of FULL_COPY_T4.
     */
    void SetAssignmentParameters(uint64_t salt,
                                 uint64_t seed,
                                 uint64_t run,
                                 double t2Probability,
                                 double t4Probability);

    /**
     * Set the half-open interval in which randomized intervention is allowed.
     *
     * Eligibility requires T2 to be at or after start and prospective T4 to
     * be strictly before stop. This common rule is evaluated before the arm
     * is interpreted.
     *
     * @param startTimeNs Inclusive assignment-window start.
     * @param stopTimeNs Exclusive assignment-window stop.
     */
    void SetAssignmentWindow(uint64_t startTimeNs, uint64_t stopTimeNs);

    /**
     * Configure deterministic assignment and execution CSV outputs.
     *
     * @param runId Stable run identifier expected in every snapshot.
     * @param assignmentsFile One-row-per-frame assignment CSV path.
     * @param executionsFile One-row-per-frame final execution CSV path.
     */
    void SetOutputFiles(const std::string& runId,
                        const std::string& assignmentsFile,
                        const std::string& executionsFile);

    /**
     * Consume one immutable prediction snapshot.
     *
     * Irrelevant offsets are ignored. Relevant snapshots must arrive primary
     * before secondary within each frame and stage.
     *
     * @param sample Snapshot emitted by paired prediction telemetry.
     */
    void NotifySnapshot(const PredictionSample& sample);

    /**
     * Estimate the reserved airtime of a canonical full delayed copy.
     *
     * @param packetCount Number of application packets.
     * @param expectedMacServiceBytes Sum of expected MAC service bytes.
     * @return Safety-adjusted airtime estimate in microseconds.
     */
    double EstimateFullCopyAirtimeUs(uint32_t packetCount,
                                     uint64_t expectedMacServiceBytes) const;

    /**
     * Return the frozen cost-estimator identifier.
     *
     * @return Cost-estimator identifier.
     */
    static std::string_view GetCostEstimatorId();

    /** @return Number of paired T2 frames assigned. */
    uint64_t GetAssignmentCount() const;

    /** @return Number of assignments satisfying common T2 eligibility. */
    uint64_t GetEligibleT2Count() const;

    /** @return Number of frames assigned FULL_COPY_T2. */
    uint64_t GetT2ArmCount() const;

    /** @return Number of frames assigned FULL_COPY_T4. */
    uint64_t GetT4ArmCount() const;

    /** @return Number of frames assigned CONTROL. */
    uint64_t GetControlArmCount() const;

    /** @return Number of final execution rows emitted. */
    uint64_t GetExecutionCount() const;

    /** @return Number of sender launch calls attempted. */
    uint64_t GetLaunchAttemptCount() const;

    /** @return Number of accepted delayed-copy launches. */
    uint64_t GetLaunchCount() const;

    /** @return Number of assigned exposures not delivered despite actionability. */
    uint64_t GetNoncomplianceCount() const;

    /** @return Number of distinct launched copies settled by the airtime meter. */
    uint64_t GetSettlementCount() const;

  protected:
    void DoDispose() override;

  private:
    friend class RandomizedInterventionControllerTestAccess;

    /** Exact samples retained for one paired stage. */
    struct StagePair
    {
        std::optional<PredictionSample> primary;   ///< Primary path 1/copy 0 sample.
        std::optional<PredictionSample> secondary; ///< Secondary path 0/copy 1 sample.
        bool completed{false};                     ///< Whether both samples were processed.
    };

    /** Complete causal state retained for one frame. */
    struct FrameState
    {
        StagePair t2; ///< Paired T2 snapshots.
        StagePair t4; ///< Paired T4 snapshots.
        std::optional<RandomizedExplorationAssignment> assignment; ///< Immutable draw.
        bool eligibleT2{false};              ///< Common pre-assignment eligibility.
        std::string eligibilityReason;       ///< Stable eligibility outcome.
        std::optional<DelayedCopyDescriptor> descriptor; ///< Canonical full-copy descriptor.
        double nominalAirtimeUs{0};          ///< Estimate before safety adjustment.
        double estimatedAirtimeUs{0};        ///< Reserved safety-adjusted estimate.
        bool executionLogged{false};         ///< Whether the one final row was emitted.
    };

    /**
     * Return a stable label for one assignment arm.
     *
     * @param arm Arm to label.
     * @return Stable uppercase arm label.
     */
    static std::string_view ArmName(RandomizedExplorationArm arm);

    /**
     * Return the expected stage name for one relevant offset.
     *
     * @param offsetUs Snapshot offset.
     * @return T2 or T4.
     */
    static std::string_view StageName(uint64_t offsetUs);

    /**
     * Return whether one sample has the fixed primary identity.
     *
     * @param sample Sample to inspect.
     * @return True for path 1/copy 0.
     */
    static bool IsPrimary(const PredictionSample& sample);

    /**
     * Return whether one sample has the fixed secondary identity.
     *
     * @param sample Sample to inspect.
     * @return True for path 0/copy 1.
     */
    static bool IsSecondary(const PredictionSample& sample);

    /**
     * Find an intrinsic sample-contract violation.
     *
     * @param sample Sample to validate.
     * @return Stable error text, or empty when valid.
     */
    std::optional<std::string> FindSampleError(const PredictionSample& sample) const;

    /**
     * Find a primary-secondary immutable-metadata mismatch.
     *
     * @param primary Primary snapshot.
     * @param secondary Secondary snapshot.
     * @return Stable error text, or empty when the pair matches.
     */
    static std::optional<std::string> FindPairError(const PredictionSample& primary,
                                                    const PredictionSample& secondary);

    /**
     * Find an immutable-metadata change between T2 and T4.
     *
     * @param t2 T2 snapshot for one copy.
     * @param t4 T4 snapshot for the same copy.
     * @return Stable error text, or empty when immutable fields agree.
     */
    static std::optional<std::string> FindCrossStageError(const PredictionSample& t2,
                                                          const PredictionSample& t4);

    /**
     * Find an ordering or duplication violation without mutating state.
     *
     * @param state Existing frame state.
     * @param sample Incoming relevant snapshot.
     * @return Stable error text, or empty when the transition is legal.
     */
    static std::optional<std::string> FindTransitionError(const FrameState& state,
                                                          const PredictionSample& sample);

    /**
     * Find a malformed canonical full-copy descriptor.
     *
     * @param sample Paired primary sample.
     * @param descriptor Delayed-copy descriptor.
     * @return Stable error text, or empty when the descriptor is canonical.
     */
    static std::optional<std::string> FindDescriptorError(
        const PredictionSample& sample,
        const DelayedCopyDescriptor& descriptor);

    /**
     * Find a difference between two canonical descriptor observations.
     *
     * @param expected Descriptor captured at T2.
     * @param actual Descriptor queried at execution.
     * @return Stable error text, or empty when exactly equal.
     */
    static std::optional<std::string> FindDescriptorMismatch(
        const DelayedCopyDescriptor& expected,
        const DelayedCopyDescriptor& actual);

    /**
     * Validate that a hypothetical secondary snapshot remains untreated.
     *
     * @param sample Secondary snapshot.
     * @return Stable error text, or empty when no progress is visible.
     */
    static std::optional<std::string> FindUntreatedSecondaryError(
        const PredictionSample& sample);

    /**
     * Process a newly completed paired stage.
     *
     * @param frame Frame state being advanced.
     * @param offsetUs Completed stage offset.
     */
    void ProcessCompletedPair(FrameState& frame, uint64_t offsetUs);

    /**
     * Assign and resolve immediate T2 outcomes.
     *
     * @param frame Frame with a completed T2 pair.
     */
    void ProcessT2(FrameState& frame);

    /**
     * Resolve a pending FULL_COPY_T4 assignment.
     *
     * @param frame Frame with completed T2 and T4 pairs.
     */
    void ProcessT4(FrameState& frame);

    /**
     * Attempt one assigned full-copy launch and emit its final outcome.
     *
     * @param frame Assigned frame state.
     * @param pair Paired stage immediately preceding launch.
     * @param stage Stable T2 or T4 stage label.
     * @param descriptor Descriptor observed immediately before launch.
     */
    void AttemptLaunch(FrameState& frame,
                       const StagePair& pair,
                       std::string_view stage,
                       const DelayedCopyDescriptor& descriptor);

    /**
     * Build the meter reservation for one accepted full-copy launch.
     *
     * @param frame Assigned frame state.
     * @param descriptor Canonical delayed-copy descriptor.
     * @return Complete reservation with exact packet indexes.
     */
    SecondaryAirtimeReservation BuildReservation(
        const FrameState& frame,
        const DelayedCopyDescriptor& descriptor) const;

    /**
     * Compute the nominal full-copy airtime before safety adjustment.
     *
     * @param packetCount Number of packets.
     * @param expectedMacServiceBytes Sum of expected MAC service bytes.
     * @return Nominal airtime in microseconds.
     */
    static double EstimateNominalAirtimeUs(uint32_t packetCount,
                                           uint64_t expectedMacServiceBytes);

    /**
     * Emit the immutable assignment row.
     *
     * @param frame Assigned frame state.
     */
    void WriteAssignment(const FrameState& frame);

    /**
     * Emit exactly one final execution row.
     *
     * @param frame Assigned frame state.
     * @param pair Pair defining the execution outcome.
     * @param descriptorAvailableAtExecution Whether a descriptor was queryable.
     * @param primaryActionable Whether exposure remained actionable.
     * @param attempted Whether RequestSecondaryCopy was called.
     * @param launched Whether the sender accepted the request.
     * @param noncompliance Whether an actionable assigned exposure was missed.
     * @param status Stable execution status.
     */
    void WriteExecution(FrameState& frame,
                        const StagePair& pair,
                        bool descriptorAvailableAtExecution,
                        bool primaryActionable,
                        bool attempted,
                        bool launched,
                        bool noncompliance,
                        std::string_view status);

    /** Write the assignment CSV header. */
    void WriteAssignmentHeader();

    /** Write the execution CSV header. */
    void WriteExecutionHeader();

    /**
     * Count one meter settlement and reject duplicate callbacks.
     *
     * @param frameId Settled frame identifier.
     * @param releasedUs Released reserved airtime.
     * @param measuredUs Measured tagged airtime.
     * @param nominalUs Nominal estimated airtime.
     * @param fallback Whether fallback timing caused settlement.
     */
    void NotifySettlement(uint64_t frameId,
                          double releasedUs,
                          double measuredUs,
                          double nominalUs,
                          bool fallback);

    /**
     * Assert that immutable configuration is ready and freeze it.
     */
    void StartControl();

    MultipathSender* m_sender{nullptr}; ///< Non-owning delayed-copy sender.
    Ptr<SecondaryAirtimeMeter> m_meter; ///< Airtime reservation and settlement meter.
    uint64_t m_salt{0};                 ///< Explicit assignment salt.
    uint64_t m_seed{0};                 ///< Explicit ns-3 seed.
    uint64_t m_run{0};                  ///< Explicit ns-3 run number.
    double m_t2Probability{0};          ///< FULL_COPY_T2 probability.
    double m_t4Probability{0};          ///< FULL_COPY_T4 probability.
    bool m_assignmentConfigured{false}; ///< Whether assignment parameters were set.
    uint64_t m_windowStartNs{0};        ///< Inclusive intervention-window start.
    uint64_t m_windowStopNs{0};         ///< Exclusive intervention-window stop.
    bool m_windowConfigured{false};     ///< Whether the intervention window was set.
    bool m_started{false};              ///< Whether the first relevant snapshot arrived.
    std::string m_runId;                ///< Expected telemetry run identifier.
    std::ofstream m_assignments;        ///< Immutable assignment CSV.
    std::ofstream m_executions;         ///< Final execution CSV.
    std::map<uint64_t, FrameState> m_frames; ///< Per-frame paired causal state.
    std::set<uint64_t> m_launchedFrames;     ///< Frames with accepted launch requests.
    std::set<uint64_t> m_settledFrames;      ///< Frames with observed settlements.
    uint64_t m_assignmentCount{0};            ///< Paired T2 assignment count.
    uint64_t m_eligibleT2Count{0};            ///< Common eligible T2 count.
    uint64_t m_t2ArmCount{0};                 ///< FULL_COPY_T2 draw count.
    uint64_t m_t4ArmCount{0};                 ///< FULL_COPY_T4 draw count.
    uint64_t m_controlArmCount{0};            ///< CONTROL draw count.
    uint64_t m_executionCount{0};             ///< Final execution row count.
    uint64_t m_launchAttemptCount{0};         ///< Sender launch call count.
    uint64_t m_launchCount{0};                ///< Accepted sender launch count.
    uint64_t m_noncomplianceCount{0};         ///< Missed actionable exposure count.
    uint64_t m_settlementCount{0};            ///< Distinct meter settlement count.
};

} // namespace ns3

#endif // RANDOMIZED_INTERVENTION_CONTROLLER_H
