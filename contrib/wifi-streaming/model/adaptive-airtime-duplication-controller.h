/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef ADAPTIVE_AIRTIME_DUPLICATION_CONTROLLER_H
#define ADAPTIVE_AIRTIME_DUPLICATION_CONTROLLER_H

#include "closed-loop-risk-predictor.h"
#include "secondary-airtime-meter.h"

#include "ns3/callback.h"
#include "ns3/object.h"

#include <cstdint>
#include <fstream>
#include <map>
#include <optional>
#include <set>
#include <string>
#include <vector>

namespace ns3
{

class MultipathSender;
struct DelayedCopyDescriptor;

/**
 * @internal Test-only access to deterministic bucket state.
 * @endinternal
 */
class AdaptiveAirtimeDuplicationControllerTestAccess;

/** Secondary packet set used after adaptive admission. */
enum class AdaptiveSecondaryPacketSelection
{
    FULL_COPY,              ///< Preserve the canonical forward full copy.
    PRIMARY_UNACKNOWLEDGED, ///< Send primary-unacknowledged indexes in reverse order.
};

/** Packet set used to price adaptive admission. */
enum class AdaptiveAdmissionPacketCost
{
    LAUNCHED_PACKET_SET, ///< Price the packet set that would actually launch.
    WHOLE_COPY,          ///< Price the canonical full secondary copy.
};

/**
 * Causal adaptive-airtime selective duplication controller.
 */
class AdaptiveAirtimeDuplicationController : public Object
{
  public:
    /**
     * Return runtime type information.
     *
     * @return The object TypeId.
     */
    static TypeId GetTypeId();

    AdaptiveAirtimeDuplicationController();
    ~AdaptiveAirtimeDuplicationController() override;

    /**
     * Set the sender that can launch a delayed secondary copy.
     *
     * @param sender Sender whose lifetime covers the controller callback.
     */
    void SetSender(MultipathSender* sender);

    /**
     * Set the causal stage-specific scorer.
     *
     * @param scorer Callback returning a typed bounded admission score.
     */
    void SetRiskScorer(Callback<ClosedLoopRiskScore, const PredictionSample&> scorer);

    /**
     * Attach the passive secondary airtime meter.
     *
     * @param meter Shared meter instance.
     */
    void SetAirtimeMeter(Ptr<SecondaryAirtimeMeter> meter);

    /**
     * Set the path whose frame-copy snapshots drive prediction.
     *
     * @param pathId Primary prediction path.
     */
    void SetPrimaryPath(uint8_t pathId);

    /**
     * Set how an admitted secondary copy is projected to packets.
     *
     * @param selection Full forward copy or reverse primary deficit.
     */
    void SetSecondaryPacketSelection(AdaptiveSecondaryPacketSelection selection);

    /**
     * Set the packet set used only for admission pricing.
     *
     * Reservation and settlement always describe the packet set that actually
     * launches. Whole-copy pricing therefore permits a controlled mechanism
     * ablation without pretending that a partial launch consumed full-copy
     * airtime.
     *
     * @param cost Packet-cost basis for the risk-density gate.
     */
    void SetAdmissionPacketCost(AdaptiveAdmissionPacketCost cost);

    /**
     * Set the long-run secondary-airtime budget fraction.
     *
     * @param fraction Fraction in (0, 1].
     */
    void SetBudgetFraction(double fraction);

    /**
     * Set the token-bucket horizon in microseconds.
     *
     * @param horizonUs Positive horizon.
     */
    void SetBucketHorizonUs(uint64_t horizonUs);

    /**
     * Set the initial token-credit horizon in microseconds.
     *
     * The initial horizon must be positive and no larger than the bucket
     * horizon. If it is not set explicitly, the bucket horizon is used so
     * existing configurations retain a full initial bucket. Configure the
     * maximum bucket horizon first when changing both values.
     *
     * @param horizonUs Initial token-credit horizon.
     */
    void SetInitialBucketHorizonUs(uint64_t horizonUs);

    /**
     * Set the initial shadow price.
     *
     * @param price Price in [0, 1].
     */
    void SetInitialShadowPrice(double price);

    /**
     * Set the dual-update step size.
     *
     * A zero step freezes the shadow price at its initial value, yielding a
     * fixed risk-density gate while retaining token-bucket enforcement.
     *
     * @param step Nonnegative step.
     */
    void SetDualStep(double step);

    /**
     * Select whether admission pricing includes retry inflation.
     *
     * Reservation and token-availability accounting always use the
     * retry-inflated estimate. Disabling this option uses nominal airtime only
     * for normalized admission cost and utility.
     *
     * @param enabled True to use retry-inflated admission cost.
     */
    void SetAdmissionUsesRetryInflation(bool enabled);

    /**
     * Set the pre-launch cost safety factor.
     *
     * @param factor Factor >= 1.
     */
    void SetCostSafetyFactor(double factor);

    /**
     * Set the EWMA alpha for retry inflation.
     *
     * @param alpha Alpha in (0, 1].
     */
    void SetCostEwmaAlpha(double alpha);

    /**
     * Set the snapshot offsets eligible for decisions.
     *
     * @param offsetsUs Distinct offsets from frame generation.
     */
    void SetDecisionOffsetsUs(const std::vector<uint64_t>& offsetsUs);

    /**
     * Override the admission shadow price at selected decision offsets.
     *
     * Offsets without an override continue to use the global dual variable.
     * Overrides are fixed while the global dual update and token meter continue
     * unchanged.
     *
     * @param prices Fixed shadow price indexed by decision offset in microseconds.
     */
    void SetDecisionOffsetShadowPrices(const std::map<uint64_t, double>& prices);

    /**
     * Restrict selected decision offsets to I-frames.
     *
     * @param offsetsUs Decision offsets at which only I-frames may launch.
     */
    void SetIFrameOnlyDecisionOffsetsUs(const std::vector<uint64_t>& offsetsUs);

    /**
     * Configure the copy used to normalize admission costs.
     *
     * @param packetCount Number of packets in the reference frame copy.
     * @param expectedMacServiceBytes Sum of expected MAC service bytes.
     */
    void SetReferenceCopyDescriptor(uint32_t packetCount,
                                    uint64_t expectedMacServiceBytes);

    /**
     * Configure the decision CSV output.
     *
     * @param runId Stable run identifier.
     * @param fileName Output CSV path.
     */
    void SetOutputFile(const std::string& runId, const std::string& fileName);

    /**
     * Consume one immutable causal telemetry snapshot.
     *
     * @param sample Snapshot delivered after collector validation.
     */
    void NotifySnapshot(const PredictionSample& sample);

    /**
     * Estimate secondary sender PHY TX airtime.
     *
     * @param packetCount Number of packets in the copy.
     * @param expectedMacServiceBytes Sum of expected MAC service bytes.
     * @param inflation Retry and aggregation inflation multiplier.
     * @return Estimated airtime in microseconds.
     */
    double EstimateSecondaryAirtimeUs(uint32_t packetCount,
                                      uint64_t expectedMacServiceBytes,
                                      double inflation) const;

    /**
     * Return the configured reference-copy airtime.
     *
     * @return Reference airtime in microseconds.
     */
    double GetReferenceAirtimeUs() const;

    /**
     * Return launched secondary copies.
     *
     * @return Action count.
     */
    uint64_t GetActionCount() const;

    /**
     * Return the current shadow price.
     *
     * @return Shadow price.
     */
    double GetShadowPrice() const;

    /**
     * Return the current bucket balance.
     *
     * @return Balance in microseconds.
     */
    double GetBucketBalanceUs() const;

    /**
     * Return the startup token capacity recorded when control began.
     *
     * @return Initial capacity in microseconds.
     */
    double GetInitialCapacityUs() const;

  protected:
    void DoDispose() override;

  private:
    /**
     * @internal Unit-test access to deterministic bucket state.
     * @endinternal
     */
    friend class AdaptiveAirtimeDuplicationControllerTestAccess;

    /** Per-frame launch state retained across decision stages. */
    struct FrameState
    {
        bool launched{false}; ///< Whether a secondary copy was launched.
    };

    /**
     * Initialize the token bucket at the first causal snapshot.
     *
     * @param nowNs Current simulation time in nanoseconds.
     */
    void InitializeBucket(uint64_t nowNs);

    /**
     * Return whether a maximum and initial bucket horizon form a valid pair.
     *
     * @param bucketHorizonUs Maximum-balance horizon in microseconds.
     * @param initialHorizonUs Initial-credit horizon in microseconds.
     * @return True when both are positive and the initial horizon is no larger.
     */
    static bool AreBucketHorizonsValid(uint64_t bucketHorizonUs,
                                       uint64_t initialHorizonUs);

    /**
     * Refill the token bucket through a causal event timestamp.
     *
     * @param nowNs Current simulation time in nanoseconds.
     */
    void RefillBucket(uint64_t nowNs);

    /**
     * Apply one projected dual-variable update.
     *
     * @param nowNs Current T0 timestamp in nanoseconds.
     */
    void UpdateShadowPrice(uint64_t nowNs);

    /**
     * Resolve the admission price for a decision offset.
     *
     * @param offsetUs Decision offset in microseconds.
     * @return Fixed offset override, or the current global dual variable.
     */
    double ResolveDecisionShadowPrice(uint64_t offsetUs) const;

    /**
     * Return whether the frame type is eligible at a decision offset.
     *
     * @param sample Current causal prediction sample.
     * @return True when this stage permits the sample's frame type.
     */
    bool IsFrameTypeEligible(const PredictionSample& sample) const;

    /**
     * Charge measured PPDU airtime to the controller balance.
     *
     * @param frameId Frame receiving the allocated airtime.
     * @param allocatedUs Airtime allocated to this frame in microseconds.
     * @param ppduDurationUs Full PPDU duration in microseconds.
     */
    void NotifyMeasuredAirtime(uint64_t frameId, double allocatedUs, double ppduDurationUs);

    /**
     * Update retry inflation after a reservation settles.
     *
     * @param frameId Settled frame identifier.
     * @param releasedUs Unused reservation released in microseconds.
     * @param measuredUs Measured frame airtime in microseconds.
     * @param nominalUs Nominal frame estimate in microseconds.
     * @param fallback Whether fallback timing caused settlement.
     */
    void NotifySettlement(uint64_t frameId,
                          double releasedUs,
                          double measuredUs,
                          double nominalUs,
                          bool fallback);

    /**
     * Resolve ordered secondary packet indexes for one snapshot.
     *
     * A null optional denotes the canonical full copy. An empty vector denotes
     * an observed zero deficit.
     *
     * @param sample Current causal primary snapshot.
     * @return Packet selection for this stage.
     */
    std::optional<std::vector<uint32_t>> ResolveSecondaryPacketIndices(
        const PredictionSample& sample) const;

    /** Write the adaptive decision CSV header. */
    void WriteHeader();

    /**
     * Write one adaptive decision row.
     *
     * @param sample Causal prediction sample.
     * @param score Typed stage-specific model output.
     * @param admissionUs Airtime used to price admission in microseconds.
     * @param estimatedUs Retry-inflated reservation airtime in microseconds.
     * @param admissionPacketCount Packet count used for admission pricing.
     * @param referenceUs Reference airtime in microseconds.
     * @param shadowPrice Effective admission shadow price.
     * @param normalizedCost Estimated cost divided by reference cost.
     * @param utility Net admission utility.
     * @param balanceUs Pre-decision bucket balance.
     * @param reservedUs Pre-decision outstanding reservations.
     * @param availableUs Pre-decision unreserved balance.
     * @param decision Stable decision name.
     * @param launched Whether this row launched the secondary copy.
     * @param descriptor Candidate secondary descriptor, if one exists.
     * @param primaryAcknowledgedPacketIndices Exact acknowledged primary indexes, if queried.
     */
    void WriteDecision(const PredictionSample& sample,
                       const ClosedLoopRiskScore& score,
                       double admissionUs,
                       double estimatedUs,
                       uint32_t admissionPacketCount,
                       double referenceUs,
                       double shadowPrice,
                       double normalizedCost,
                       double utility,
                       double balanceUs,
                       double reservedUs,
                       double availableUs,
                       const std::string& decision,
                       bool launched,
                       const DelayedCopyDescriptor* descriptor,
                       const std::vector<uint32_t>* primaryAcknowledgedPacketIndices);

    MultipathSender* m_sender{nullptr}; ///< Non-owning sender pointer.
    Callback<ClosedLoopRiskScore, const PredictionSample&> m_scorer; ///< Frozen causal scorer.
    Ptr<SecondaryAirtimeMeter> m_meter; ///< Passive secondary airtime meter.
    uint8_t m_primaryPath{1}; ///< Prediction path (5 GHz by default).
    AdaptiveSecondaryPacketSelection m_secondaryPacketSelection{
        AdaptiveSecondaryPacketSelection::FULL_COPY}; ///< Admitted packet projection.
    AdaptiveAdmissionPacketCost m_admissionPacketCost{
        AdaptiveAdmissionPacketCost::LAUNCHED_PACKET_SET}; ///< Admission packet-cost basis.
    double m_budgetFraction{0.02}; ///< Long-run airtime fraction.
    uint64_t m_bucketHorizonUs{1000000}; ///< Maximum-balance horizon.
    std::optional<uint64_t> m_initialBucketHorizonUs; ///< Explicit startup-credit horizon.
    double m_initialShadowPrice{0.20}; ///< Initial dual variable.
    double m_dualStep{0.01}; ///< Dual update step.
    bool m_admissionUsesRetryInflation{true}; ///< Whether admission includes retry inflation.
    double m_costSafetyFactor{1.25}; ///< Pre-launch safety factor.
    double m_costEwmaAlpha{0.10}; ///< Retry-inflation EWMA alpha.
    double m_retryInflation{1.0}; ///< Current k_t.
    double m_shadowPrice{0.20}; ///< Current lambda.
    double m_bucketCapacityUs{0}; ///< Maximum tokens.
    double m_bucketBalanceUs{0}; ///< Earned tokens minus measured airtime.
    double m_initialCapacityUs{0}; ///< Recorded startup fill.
    uint32_t m_referencePacketCount{0}; ///< Packet count used for cost normalization.
    uint64_t m_referenceExpectedMacServiceBytes{0}; ///< Reference MAC service bytes.
    uint64_t m_lastRefillTimeNs{0}; ///< Last refill timestamp.
    uint64_t m_lastPriceUpdateNs{0}; ///< Last T0 shadow-price update.
    double m_measuredSinceLastT0Us{0}; ///< Measured airtime since previous T0.
    bool m_bucketInitialized{false}; ///< Whether the bucket is live.
    std::set<uint64_t> m_decisionOffsetsUs{0, 4000}; ///< Enabled stages.
    std::map<uint64_t, double> m_decisionOffsetShadowPrices; ///< Fixed stage prices.
    std::set<uint64_t> m_iFrameOnlyDecisionOffsetsUs; ///< I-frame-only stages.
    std::map<uint64_t, FrameState> m_frames; ///< Per-frame launch state.
    std::string m_runId{"run"}; ///< Stable output run identifier.
    std::ofstream m_output; ///< Decision CSV.
    uint64_t m_actions{0}; ///< Successful delayed launches.
};

} // namespace ns3

#endif // ADAPTIVE_AIRTIME_DUPLICATION_CONTROLLER_H
