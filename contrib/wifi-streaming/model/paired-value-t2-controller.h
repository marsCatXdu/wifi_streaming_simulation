/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef PAIRED_VALUE_T2_CONTROLLER_H
#define PAIRED_VALUE_T2_CONTROLLER_H

#include "multipath-sender.h"
#include "secondary-airtime-budget-guard.h"
#include "secondary-airtime-meter.h"
#include "temporal-t2-value-predictor.h"

#include "ns3/object.h"

#include <array>
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
 * @internal Test-only access to paired-value controller validation state.
 * @endinternal
 */
class PairedValueT2ControllerTestAccess;

/**
 * @ingroup wifi-streaming
 * Execute the frozen paired primary-only temporal T2 value policy.
 *
 * The controller accepts exactly one primary path-1/copy-0 snapshot followed
 * immediately by its hypothetical path-0/copy-1 snapshot.  It stores every
 * validated primary report before applying the fixed policy gates, evaluates
 * the frozen model only after the first five gates, and accounts accepted full
 * copies against measured secondary airtime.
 */
class PairedValueT2Controller : public Object
{
  public:
    /** Frozen admission profiles. */
    enum class AdmissionProfile : uint8_t
    {
        BASELINE_V1 = 0,              ///< Strict measured-airtime admission.
        SCORE_AWARE_EMERGENCY_V2 = 1, ///< Bounded high-score future credit.
    };

    /** Baseline decision CSV schema version. */
    static constexpr uint32_t CSV_SCHEMA_VERSION = 1;

    /** Baseline controller summary schema version. */
    static constexpr uint32_t SUMMARY_SCHEMA_VERSION = 1;

    /** Score-aware decision CSV schema version. */
    static constexpr uint32_t SCORE_AWARE_CSV_SCHEMA_VERSION = 2;

    /** Score-aware controller summary schema version. */
    static constexpr uint32_t SCORE_AWARE_SUMMARY_SCHEMA_VERSION = 2;

    /** Frozen primary path identifier. */
    static constexpr uint8_t PRIMARY_PATH_ID = 1;

    /** Frozen hypothetical secondary path identifier. */
    static constexpr uint8_t SECONDARY_PATH_ID = 0;

    /** Frozen primary copy identifier. */
    static constexpr uint8_t PRIMARY_COPY_ID = 0;

    /** Frozen delayed-secondary copy identifier. */
    static constexpr uint8_t SECONDARY_COPY_ID = 1;

    /** Frozen T2 sample offset in microseconds. */
    static constexpr uint64_t T2_OFFSET_US = 2000;

    /** Inclusive measurement-window start in nanoseconds. */
    static constexpr uint64_t MEASUREMENT_START_NS = 1000000000;

    /** Exclusive measurement-window stop in nanoseconds. */
    static constexpr uint64_t MEASUREMENT_STOP_NS = 61000000000;

    /** Inclusive decision-window start in nanoseconds. */
    static constexpr uint64_t DECISION_START_NS = 1000000000;

    /** Exclusive decision-window stop in nanoseconds. */
    static constexpr uint64_t DECISION_STOP_NS = 60466000000;

    /** Frozen measured-airtime budget fraction. */
    static constexpr double BUDGET_FRACTION = 0.006;

    /** Frozen maximum budget horizon in microseconds. */
    static constexpr uint64_t BUDGET_MAX_HORIZON_US = 10000000;

    /** Frozen startup-credit horizon in microseconds. */
    static constexpr uint64_t BUDGET_INITIAL_HORIZON_US = 2000000;

    /** Frozen canonical-cost safety factor. */
    static constexpr double COST_SAFETY_FACTOR = 1.25;

    /** Frozen float32 emergency-tier score threshold. */
    static constexpr float EMERGENCY_SCORE_THRESHOLD = 0.0001500000071246177F;

    /** Frozen emergency-tier maximum negative available balance. */
    static constexpr double EMERGENCY_MAXIMUM_DEBT_US = 60000.0;

    /** Absolute tolerance for floating-point accounting in microseconds. */
    static constexpr double ACCOUNTING_TOLERANCE_US = 1e-9;

    /** @return Runtime type information. */
    static TypeId GetTypeId();

    PairedValueT2Controller();
    ~PairedValueT2Controller() override;

    /**
     * Select one frozen admission profile before any output or runtime setup.
     *
     * @param profile Admission profile to execute and identify in evidence.
     */
    void SetAdmissionProfile(AdmissionProfile profile);

    /** @return Selected admission profile. */
    AdmissionProfile GetAdmissionProfile() const;

    /**
     * Return the stable profile identifier.
     *
     * @param profile Admission profile.
     * @return Stable profile identifier.
     */
    static std::string_view AdmissionProfileName(AdmissionProfile profile);

    /**
     * Parse one stable admission-profile identifier.
     *
     * @param name Prospective profile identifier.
     * @return Matching profile, or empty for an unsupported name.
     */
    static std::optional<AdmissionProfile> ParseAdmissionProfile(
        std::string_view name);

    /**
     * Return the decision schema for one admission profile.
     *
     * @param profile Admission profile.
     * @return Frozen decision CSV schema version.
     */
    static uint32_t GetCsvSchemaVersion(AdmissionProfile profile);

    /**
     * Return the summary schema for one admission profile.
     *
     * @param profile Admission profile.
     * @return Frozen controller summary schema version.
     */
    static uint32_t GetSummarySchemaVersion(AdmissionProfile profile);

    /**
     * Set the sender that owns canonical delayed copies.
     *
     * @param sender Sender whose lifetime covers every controller callback.
     */
    void SetSender(MultipathSender* sender);

    /**
     * Attach and freeze the passive secondary-airtime meter.
     *
     * This also installs the fixed measurement window, 500 ms queue lifetime,
     * budget metadata, measured-airtime callback, and settlement callback.
     *
     * @param meter Shared meter instance.
     */
    void SetAirtimeMeter(Ptr<SecondaryAirtimeMeter> meter);

    /**
     * Configure the one-row-per-frame decision CSV and final summary JSON.
     *
     * @param runId Stable telemetry run identifier.
     * @param decisionsFile Decision CSV output path.
     * @param summaryFile Controller summary JSON output path.
     */
    void SetOutputFiles(const std::string& runId,
                        const std::string& decisionsFile,
                        const std::string& summaryFile);

    /**
     * Consume one paired-telemetry snapshot.
     *
     * T0 is ignored.  T2 must arrive as a primary snapshot immediately
     * followed by its matching untreated hypothetical secondary snapshot.
     *
     * @param sample Immutable telemetry endpoint.
     */
    void NotifySnapshot(const PredictionSample& sample);

    /**
     * Write and validate the frozen controller summary after meter summary.
     *
     * The caller supplies independent final frame evidence so the controller
     * can prove row cardinality and exact launch/duplication agreement.
     *
     * @param generatedFrames Generated-frame count in the measurement window.
     * @param duplicatedFrameIds Frame IDs marked duplicated in final frame output.
     */
    void WriteSummary(uint64_t generatedFrames,
                      const std::set<uint64_t>& duplicatedFrameIds);

    /** @return Frozen runtime-contract identifier. */
    static std::string_view GetRuntimeContractId();

    /**
     * Return the frozen runtime contract for one admission profile.
     *
     * @param profile Admission profile.
     * @return Frozen runtime-contract identifier.
     */
    static std::string_view GetRuntimeContractId(AdmissionProfile profile);

    /** @return SHA-256 of the exact frozen runtime-contract file bytes. */
    static std::string_view GetRuntimeContractSha256();

    /**
     * Return the runtime-contract digest for one admission profile.
     *
     * @param profile Admission profile.
     * @return SHA-256 of the exact runtime-contract file bytes.
     */
    static std::string_view GetRuntimeContractSha256(AdmissionProfile profile);

    /** @return Frozen canonical secondary-cost estimator identifier. */
    static std::string_view GetCostEstimatorId();

    /** @return Number of completed paired T2 frames. */
    uint64_t GetPairedFrameCount() const;

    /** @return Number of model evaluations. */
    uint64_t GetFeatureEvaluationCount() const;

    /** @return Number of accepted delayed-secondary launches. */
    uint64_t GetLaunchCount() const;

    /** @return Number of completed meter settlements. */
    uint64_t GetSettlementCount() const;

  protected:
    void DoDispose() override;

  private:
    friend class PairedValueT2ControllerTestAccess;

    /** Stable decision statuses in their frozen gate order. */
    enum class DecisionStatus : uint8_t
    {
        OUTSIDE_DECISION_WINDOW = 0, ///< Sample is outside [1 s, 60.466 s).
        HISTORY_WARMUP = 1,          ///< Exact lag-8 history is unavailable.
        FRAME_TYPE_RESTRICTED = 2,   ///< Frame is not a P frame.
        NOT_ACTIONABLE = 3,          ///< Primary copy is already complete.
        DESCRIPTOR_UNAVAILABLE = 4,  ///< Canonical delayed copy is absent.
        BELOW_SCORE_THRESHOLD = 5,   ///< Frozen learned score gate failed.
        AIRTIME_GUARD_REJECTED = 6,  ///< Measured-airtime credit is insufficient.
        LAUNCH_REJECTED = 7,         ///< Sender synchronously rejected the request.
        ACTION = 8,                  ///< Full delayed-secondary copy launched.
    };

    /** Accounting state for one accepted launch. */
    struct LaunchedFrameState
    {
        double nominalAirtimeUs{0};  ///< Frozen nominal cost at launch.
        double reservedAirtimeUs{0}; ///< Frozen safety-adjusted reservation.
        double remainingReservedUs{0}; ///< Expected meter reservation remainder.
        double measuredAirtimeUs{0}; ///< Measured allocations observed so far.
        bool settled{false};          ///< Whether the meter settled the frame.
    };

    /** Complete evidence serialized into one decision row. */
    struct DecisionEvidence
    {
        const PredictionSample* primary{nullptr}; ///< Paired primary endpoint.
        const PredictionSample* secondary{nullptr}; ///< Paired secondary endpoint.
        TemporalT2ValuePredictor::HistoryEvidence history; ///< Stored exact-history evidence.
        DecisionStatus status{DecisionStatus::OUTSIDE_DECISION_WINDOW}; ///< Final status.
        bool insideDecisionWindow{false}; ///< First gate result.
        bool featureEvaluated{false};      ///< Whether model diagnostics are populated.
        std::optional<TemporalT2ValueModelResult> model; ///< Frozen model diagnostics.
        bool descriptorChecked{false};   ///< Whether the descriptor gate was reached.
        std::optional<DelayedCopyDescriptor> descriptor; ///< Valid canonical descriptor.
        double canonicalNominalAirtimeUs{0}; ///< Descriptor cost before safety factor.
        double canonicalReservedAirtimeUs{0}; ///< Frozen reservation cost.
        double guardBalanceBeforeUs{0};  ///< Earned balance after causal refill.
        double meterReservedBeforeUs{0}; ///< Outstanding meter reservations before action.
        double guardAvailableBeforeUs{0}; ///< Balance minus reservations before action.
        double guardDebtBeforeUs{0};     ///< Negative-balance depth before action.
        bool guardAdmissionConsidered{false}; ///< Whether score reached admission.
        bool guardAdmitted{false};       ///< Whether canonical reservation fit.
        bool strictGuardAdmitted{false}; ///< Whether strict credit admitted the copy.
        bool passesEmergencyScore{false}; ///< Whether the high-score tier passed.
        bool emergencyAdmissionConsidered{false}; ///< Whether bounded debt was queried.
        bool emergencyAdmitted{false}; ///< Whether bounded future credit admitted.
        bool launchAttempted{false};     ///< Whether sender request was made.
        bool secondaryLaunched{false};   ///< Whether sender accepted the request.
        double guardBalanceAfterUs{0};   ///< Earned balance after the decision.
        double meterReservedAfterUs{0};  ///< Outstanding meter reservations after decision.
        double guardAvailableAfterUs{0}; ///< Balance minus reservations after decision.
        double guardDebtAfterUs{0};      ///< Negative-balance depth after decision.
    };

    /**
     * Find an intrinsic primary endpoint error safe to check before pairing.
     *
     * @param sample Prospective pending primary endpoint.
     * @return Stable error text, or empty when valid.
     */
    std::optional<std::string> FindPendingPrimaryError(
        const PredictionSample& sample) const;

    /**
     * Find an intrinsic hypothetical-secondary endpoint error.
     *
     * @param sample Prospective hypothetical-secondary endpoint.
     * @return Stable error text, or empty when valid.
     */
    std::optional<std::string> FindSecondaryError(const PredictionSample& sample) const;

    /**
     * Find an immutable mismatch in one primary-secondary pair.
     *
     * @param primary Pending primary endpoint.
     * @param secondary Following hypothetical-secondary endpoint.
     * @return Stable error text, or empty when the pair agrees.
     */
    static std::optional<std::string> FindPairError(const PredictionSample& primary,
                                                    const PredictionSample& secondary);

    /**
     * Validate the secondary endpoint's allowed untreated-progress fields.
     *
     * @param secondary Hypothetical-secondary endpoint.
     * @return Stable error text, or empty when the copy remains untreated.
     */
    static std::optional<std::string> FindUntreatedSecondaryError(
        const PredictionSample& secondary);

    /**
     * Validate and recompute a canonical full delayed-copy descriptor.
     *
     * @param primary Paired primary endpoint.
     * @param descriptor Available delayed-copy descriptor.
     * @return Stable error text, or empty when the descriptor is canonical.
     */
    static std::optional<std::string> FindDescriptorError(
        const PredictionSample& primary,
        const DelayedCopyDescriptor& descriptor);

    /**
     * Process one fully validated pair and emit exactly one decision row.
     *
     * @param primary Valid paired primary endpoint.
     * @param secondary Valid paired hypothetical-secondary endpoint.
     */
    void ProcessPair(const PredictionSample& primary,
                     const PredictionSample& secondary);

    /**
     * Refill and snapshot guard/meter state before the ordered gates.
     *
     * @param evidence Decision evidence to populate.
     */
    void CaptureAccountingBefore(DecisionEvidence& evidence);

    /**
     * Snapshot and validate guard/meter state after the final decision.
     *
     * @param evidence Decision evidence to populate and validate.
     */
    void CaptureAccountingAfter(DecisionEvidence& evidence);

    /**
     * Build the exact meter reservation for one accepted full copy.
     *
     * @param descriptor Canonical full delayed-copy descriptor.
     * @param nominalAirtimeUs Nominal canonical cost.
     * @param reservedAirtimeUs Safety-adjusted canonical reservation.
     * @return Complete meter reservation.
     */
    static SecondaryAirtimeReservation BuildReservation(
        const DelayedCopyDescriptor& descriptor,
        double nominalAirtimeUs,
        double reservedAirtimeUs);

    /**
     * Consume one measured-airtime allocation after meter reservation reduction.
     *
     * @param frameId Launched frame receiving the allocation.
     * @param allocatedUs Airtime allocated to the frame.
     * @param ppduDurationUs Full tagged PPDU duration.
     */
    void NotifyMeasuredAirtime(uint64_t frameId,
                               double allocatedUs,
                               double ppduDurationUs);

    /**
     * Consume one final meter settlement after reservation release.
     *
     * @param frameId Settled launched frame.
     * @param releasedUs Released reservation remainder.
     * @param measuredUs Total measured allocation for the frame.
     * @param nominalUs Frozen nominal launch cost.
     * @param fallback Whether the meter used fallback settlement.
     */
    void NotifySettlement(uint64_t frameId,
                          double releasedUs,
                          double measuredUs,
                          double nominalUs,
                          bool fallback);

    /** Write the exact 82-column decision CSV header. */
    void WriteDecisionHeader();

    /**
     * Write and flush one exact decision row.
     *
     * @param evidence Complete evidence after all ordered gates.
     */
    void WriteDecision(const DecisionEvidence& evidence);

    /** Validate immutable setup before the first relevant endpoint. */
    void StartControl();

    /**
     * Return one stable status label.
     *
     * @param status Frozen decision status.
     * @return Stable lowercase status label.
     */
    static std::string_view StatusName(DecisionStatus status);

    /**
     * Return the status-array index after range validation.
     *
     * @param status Frozen decision status.
     * @return Zero-based status-array index.
     */
    static std::size_t StatusIndex(DecisionStatus status);

    /**
     * Return a finite meter reservation and compare it with tracked state.
     *
     * @return Reconciled outstanding reservation in microseconds.
     */
    double GetReconciledMeterReservedUs() const;

    MultipathSender* m_sender{nullptr}; ///< Non-owning canonical delayed-copy sender.
    Ptr<SecondaryAirtimeMeter> m_meter; ///< Shared measured-airtime meter.
    TemporalT2ValuePredictor m_predictor; ///< Frozen history and model adapter.
    SecondaryAirtimeBudgetGuard m_guard; ///< Frozen measured-airtime guard.
    AdmissionProfile m_admissionProfile{AdmissionProfile::BASELINE_V1}; ///< Admission semantics.
    std::optional<PredictionSample> m_pendingPrimary; ///< Sole unmatched primary endpoint.
    std::string m_runId; ///< Exact run identity.
    std::ofstream m_decisions; ///< One-row-per-generated-frame decision CSV.
    std::string m_summaryFile; ///< Final controller summary JSON path.
    bool m_started{false}; ///< Whether immutable setup has been frozen.
    bool m_summaryWritten{false}; ///< Whether final summary was emitted.
    std::array<uint64_t, 9> m_statusCounts{}; ///< Counts in frozen status order.
    std::set<uint64_t> m_decidedFrameIds; ///< Frames with one emitted decision row.
    std::set<uint64_t> m_launchedFrameIds; ///< Frames accepted by the sender.
    std::set<uint64_t> m_settledFrameIds; ///< Frames settled by the meter.
    std::map<uint64_t, LaunchedFrameState> m_launchedFrames; ///< Callback accounting state.
    uint64_t m_pairedFrames{0}; ///< Fully validated paired T2 frames.
    uint64_t m_featureEvaluated{0}; ///< Model evaluation count.
    uint64_t m_scoreThresholdPassed{0}; ///< Frozen learned-score pass count.
    uint64_t m_strictGuardAdmitted{0}; ///< Strict-credit admissions.
    uint64_t m_emergencyScorePassed{0}; ///< Score-aware emergency threshold passes.
    uint64_t m_emergencyAdmissionConsidered{0}; ///< Bounded-debt admission queries.
    uint64_t m_emergencyAdmitted{0}; ///< Bounded future-credit admissions.
    uint64_t m_launchAttempted{0}; ///< RequestSecondaryCopy call count.
    uint64_t m_secondarySettled{0}; ///< Meter settlement count.
    double m_expectedMeterReservedUs{0}; ///< Independently tracked meter reservation total.
    double m_learnedCostSumEvaluatedUs{0}; ///< Learned cost over evaluated frames.
    double m_learnedCostSumLaunchedUs{0}; ///< Learned cost over accepted launches.
    double m_canonicalNominalLaunchedSumUs{0}; ///< Nominal canonical launch cost.
    double m_canonicalReservedLaunchedSumUs{0}; ///< Reserved canonical launch cost.
};

} // namespace ns3

#endif // PAIRED_VALUE_T2_CONTROLLER_H
