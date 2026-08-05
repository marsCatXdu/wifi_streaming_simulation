/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef DISTRIBUTIONAL_SHADOW_T2_CONTROLLER_H
#define DISTRIBUTIONAL_SHADOW_T2_CONTROLLER_H

#include "multipath-sender.h"
#include "permanent-airtime-credit-ledger.h"
#include "secondary-airtime-meter.h"
#include "temporal-t2-distribution-predictor.h"

#include "ns3/object.h"

#include <array>
#include <cstdint>
#include <fstream>
#include <map>
#include <optional>
#include <set>
#include <string>
#include <string_view>
#include <vector>

namespace ns3
{

/**
 * @internal Test-only access to distributional-shadow controller state.
 * @endinternal
 */
class DistributionalShadowT2ControllerTestAccess;

/**
 * @ingroup wifi-streaming
 * Execute the frozen distributional temporal-T2 shadow-price policy.
 *
 * The controller observes one primary path-1/copy-0 T2 endpoint immediately
 * followed by the matching untreated path-0/copy-1 endpoint. It predicts
 * deadline rescue with the fixed paired distribution model, prices the
 * current opportunity against the compiled finite-horizon reference, and
 * permanently debits every accepted canonical full-copy reservation.
 * Measured secondary airtime is retained as an independent evidence channel
 * and never refunds allocator credit.
 */
class DistributionalShadowT2Controller : public Object
{
  public:
    /** Decision CSV schema version. */
    static constexpr uint32_t CSV_SCHEMA_VERSION = 1;

    /** Controller summary schema version. */
    static constexpr uint32_t SUMMARY_SCHEMA_VERSION = 1;

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

    /** Exclusive measurement-window stop and repayment time in nanoseconds. */
    static constexpr uint64_t MEASUREMENT_STOP_NS = 61000000000;

    /** Inclusive decision-window start in nanoseconds. */
    static constexpr uint64_t DECISION_START_NS = 1000000000;

    /** Exclusive decision-window stop in nanoseconds. */
    static constexpr uint64_t DECISION_STOP_NS = 60466000000;

    /** Frozen causal credit-refill fraction. */
    static constexpr double BUDGET_FRACTION = 0.006;

    /** Frozen startup credit in microseconds. */
    static constexpr double INITIAL_CREDIT_US = 12000.0;

    /** Frozen maximum positive carry-over in microseconds. */
    static constexpr double POSITIVE_BALANCE_CAPACITY_US = 360000.0;

    /** Frozen canonical-cost safety factor. */
    static constexpr double COST_SAFETY_FACTOR = 1.25;

    /** Absolute accounting tolerance in microseconds. */
    static constexpr double ACCOUNTING_TOLERANCE_US = 1e-9;

    /** @return Runtime type information. */
    static TypeId GetTypeId();

    DistributionalShadowT2Controller();
    ~DistributionalShadowT2Controller() override;

    /**
     * Set the sender that owns canonical delayed copies.
     *
     * @param sender Sender whose lifetime covers every controller callback.
     */
    void SetSender(MultipathSender* sender);

    /**
     * Attach and freeze the passive secondary-airtime meter.
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
     * Consume one primary or hypothetical-secondary telemetry endpoint.
     *
     * @param sample Immutable telemetry endpoint.
     */
    void NotifySnapshot(const PredictionSample& sample);

    /**
     * Finalize repayment and write the controller summary.
     *
     * The caller supplies independent final-frame evidence so the controller
     * can prove row cardinality and launch/duplication agreement.
     *
     * @param generatedFrames Generated-frame count in the measurement window.
     * @param duplicatedFrameIds Frame IDs marked duplicated in final output.
     */
    void WriteSummary(uint64_t generatedFrames,
                      const std::set<uint64_t>& duplicatedFrameIds);

    /** @return Frozen policy identifier. */
    static std::string_view GetPolicyName();

    /** @return Frozen runtime-contract identifier. */
    static std::string_view GetRuntimeContractId();

    /** @return SHA-256 of the exact frozen runtime-contract bytes. */
    static std::string_view GetRuntimeContractSha256();

    /** @return Frozen canonical secondary-cost estimator identifier. */
    static std::string_view GetCostEstimatorId();

    /** @return Number of completed paired T2 frames. */
    uint64_t GetPairedFrameCount() const;

    /** @return Number of distribution-model evaluations. */
    uint64_t GetFeatureEvaluationCount() const;

    /** @return Number of accepted delayed-secondary launches. */
    uint64_t GetLaunchCount() const;

    /** @return Number of completed meter settlements. */
    uint64_t GetSettlementCount() const;

  protected:
    void DoDispose() override;

  private:
    friend class DistributionalShadowT2ControllerTestAccess;

    /** Stable decision statuses in their frozen gate order. */
    enum class DecisionStatus : uint8_t
    {
        OUTSIDE_DECISION_WINDOW = 0, ///< Sample is outside [1 s, 60.466 s).
        HISTORY_WARMUP = 1,          ///< Exact paired lag-8 history is unavailable.
        NOT_ACTIONABLE = 2,          ///< Primary copy is already complete.
        FRAME_TYPE_RESTRICTED = 3,   ///< Frame is not a P frame.
        DESCRIPTOR_UNAVAILABLE = 4,  ///< Canonical delayed copy is absent.
        NONPOSITIVE_REWARD = 5,      ///< Predicted deadline rescue is not positive.
        OPPORTUNITY_PRICE_REJECTED = 6, ///< Reward density is below shadow price.
        HORIZON_CREDIT_REJECTED = 7, ///< Reservation cannot be repaid by stop.
        LAUNCH_REJECTED = 8,         ///< Sender synchronously rejected the request.
        ACTION = 9,                  ///< Full delayed-secondary copy launched.
    };

    /** Accounting state for one accepted launch. */
    struct LaunchedFrameState
    {
        double nominalAirtimeUs{0};  ///< Frozen nominal cost at launch.
        double reservedAirtimeUs{0}; ///< Frozen permanent ledger debit.
        double remainingReservedUs{0}; ///< Expected meter reservation remainder.
        double measuredAirtimeUs{0}; ///< Measured allocations observed so far.
        bool settled{false};          ///< Whether the meter settled the frame.
    };

    /** Complete evidence serialized into one decision row. */
    struct DecisionEvidence
    {
        const PredictionSample* primary{nullptr}; ///< Paired primary endpoint.
        const PredictionSample* secondary{nullptr}; ///< Paired secondary endpoint.
        TemporalT2DistributionPredictor::HistoryEvidence history; ///< Exact lag evidence.
        DecisionStatus status{DecisionStatus::OUTSIDE_DECISION_WINDOW}; ///< Final route.
        bool insideDecisionWindow{false}; ///< Whether the first gate passed.
        bool congestionUpdated{false}; ///< Whether the running state consumed this row.
        double currentPrimaryBusy20ms{0}; ///< Current causal primary busy fraction.
        double runningPrimaryBusy20ms{0}; ///< Causal running primary busy mean.
        uint64_t congestionObservationCount{0}; ///< Running-mean denominator.
        std::optional<uint8_t> timeBin; ///< Fixed five-second time bin.
        std::optional<uint8_t> congestionRegime; ///< Frozen congestion regime.
        bool descriptorChecked{false}; ///< Whether the descriptor gate was reached.
        std::optional<DelayedCopyDescriptor> descriptor; ///< Valid canonical descriptor.
        double canonicalNominalAirtimeUs{0}; ///< Descriptor cost before safety factor.
        double canonicalReservedAirtimeUs{0}; ///< Exact permanent debit.
        bool featureEvaluated{false}; ///< Whether model diagnostics are populated.
        std::optional<TemporalT2DistributionModelResult> model; ///< Model diagnostics.
        double rewardDensityPerUs{0}; ///< Deadline reward per reserved microsecond.
        double opportunityCostPerUs{0}; ///< Frozen marginal shadow price.
        bool passesOpportunityPrice{false}; ///< Whether density met the price.
        bool horizonAdmissionConsidered{false}; ///< Whether repayability was queried.
        bool horizonAdmitted{false}; ///< Whether permanent debit was repayable.
        bool launchAttempted{false}; ///< Whether the sender request was made.
        bool secondaryLaunched{false}; ///< Whether the sender accepted the request.
        uint64_t earlierUnsettledLaunches{0}; ///< Active prior reservations at scoring.
        bool secondaryStateActionDirty{false}; ///< Whether prior actions may affect features.
        double ledgerBalanceBeforeUs{0}; ///< Earned balance after causal refill.
        double ledgerDebtBeforeUs{0}; ///< Negative balance magnitude before action.
        double ledgerRemainingRefillBeforeUs{0}; ///< Deterministic remaining refill.
        double ledgerRepayableBeforeUs{0}; ///< Balance plus remaining refill.
        double ledgerDebitedBeforeUs{0}; ///< Permanent debits before action.
        double ledgerBalanceAfterUs{0}; ///< Balance after optional permanent debit.
        double ledgerDebtAfterUs{0}; ///< Debt after optional permanent debit.
        double ledgerDebitedAfterUs{0}; ///< Permanent debits after decision.
        double meterReservedBeforeUs{0}; ///< Outstanding measured-meter reservations.
        double meterReservedAfterUs{0}; ///< Outstanding reservations after decision.
    };

    /**
     * Find an intrinsic endpoint error safe to check before pairing.
     *
     * @param sample Prospective endpoint.
     * @param primary Whether the endpoint must be the primary member.
     * @return Stable error text, or empty when valid.
     */
    std::optional<std::string> FindEndpointError(const PredictionSample& sample,
                                                 bool primary) const;

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
     * Process one fully paired endpoint pair and emit one decision row.
     *
     * @param primary Valid primary endpoint.
     * @param secondary Valid hypothetical-secondary endpoint.
     */
    void ProcessPair(const PredictionSample& primary,
                     const PredictionSample& secondary);

    /**
     * Capture causally advanced ledger and meter state.
     *
     * @param sampleTimeNs Current decision timestamp.
     * @param evidence Decision evidence to populate.
     */
    void CaptureAccountingBefore(uint64_t sampleTimeNs,
                                 DecisionEvidence& evidence);

    /**
     * Capture and reconcile accounting after the final route.
     *
     * @param evidence Decision evidence to populate.
     */
    void CaptureAccountingAfter(DecisionEvidence& evidence);

    /**
     * Update the frozen causal running congestion state.
     *
     * @param primary History-ready, actionable primary endpoint.
     * @param evidence Decision evidence to populate.
     */
    void UpdateCongestion(const PredictionSample& primary,
                          DecisionEvidence& evidence);

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
     * Consume one measured-airtime allocation after meter reduction.
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

    /** Write the exact decision CSV header. */
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
     * Count launches whose meter reservation is not settled.
     *
     * @return Number of active launched reservations.
     */
    uint64_t CountUnsettledLaunches() const;

    /**
     * Return finite meter reservation and compare it with tracked state.
     *
     * @return Reconciled outstanding reservation in microseconds.
     */
    double GetReconciledMeterReservedUs() const;

    MultipathSender* m_sender{nullptr}; ///< Non-owning delayed-copy sender.
    Ptr<SecondaryAirtimeMeter> m_meter; ///< Shared measured-airtime meter.
    TemporalT2DistributionPredictor m_predictor; ///< Paired distribution adapter.
    PermanentAirtimeCreditLedger m_ledger; ///< Permanent finite-horizon credit.
    std::optional<PredictionSample> m_pendingPrimary; ///< Sole unmatched primary endpoint.
    std::string m_runId; ///< Exact run identity.
    std::ofstream m_decisions; ///< One-row-per-generated-frame evidence.
    std::string m_summaryFile; ///< Final controller summary JSON path.
    bool m_started{false}; ///< Whether immutable setup has been frozen.
    bool m_summaryWritten{false}; ///< Whether final summary was emitted.
    std::array<uint64_t, 10> m_statusCounts{}; ///< Counts in frozen status order.
    std::set<uint64_t> m_decidedFrameIds; ///< Frames with one decision row.
    std::set<uint64_t> m_launchedFrameIds; ///< Frames accepted by sender.
    std::set<uint64_t> m_settledFrameIds; ///< Frames settled by meter.
    std::map<uint64_t, LaunchedFrameState> m_launchedFrames; ///< Callback accounting.
    std::array<uint64_t, 12> m_actionsByTimeBin{}; ///< Accepted actions by time bin.
    std::array<double, 12> m_reservationUsByTimeBin{}; ///< Debits by time bin.
    std::array<uint64_t, 3> m_actionsByRegime{}; ///< Accepted actions by regime.
    std::array<double, 3> m_reservationUsByRegime{}; ///< Debits by regime.
    std::vector<double> m_positiveRewards; ///< Scored positive deadline rewards.
    std::vector<double> m_finiteOpportunityCosts; ///< Finite shadow prices queried.
    uint64_t m_infiniteOpportunityCosts{0}; ///< Infinite shadow-price queries.
    uint64_t m_pairedFrames{0}; ///< Fully validated paired T2 frames.
    uint64_t m_featureEvaluated{0}; ///< Distribution-model evaluation count.
    uint64_t m_positiveRewardCount{0}; ///< Strictly positive predicted rewards.
    uint64_t m_opportunityPassed{0}; ///< Candidates clearing shadow price.
    uint64_t m_horizonAdmissionConsidered{0}; ///< Repayability queries.
    uint64_t m_horizonAdmitted{0}; ///< Repayable candidates.
    uint64_t m_launchAttempted{0}; ///< Sender request count.
    uint64_t m_secondarySettled{0}; ///< Meter settlement count.
    uint64_t m_actionDirtyScored{0}; ///< Scored decisions with prior unsettled action.
    uint64_t m_congestionObservationCount{0}; ///< Running busy sample count.
    double m_primaryBusySum{0}; ///< Running busy numerator.
    double m_expectedMeterReservedUs{0}; ///< Independently tracked meter reservations.
    double m_canonicalNominalLaunchedSumUs{0}; ///< Nominal launch-cost sum.
    double m_canonicalReservedLaunchedSumUs{0}; ///< Permanent debit sum.
    double m_predictedRewardLaunchedSum{0}; ///< Predicted rescue sum for actions.
    double m_tail18GainLaunchedSum{0}; ///< Predicted tail-gain sum for actions.
};

} // namespace ns3

#endif // DISTRIBUTIONAL_SHADOW_T2_CONTROLLER_H
