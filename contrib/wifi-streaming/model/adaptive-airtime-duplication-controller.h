/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef ADAPTIVE_AIRTIME_DUPLICATION_CONTROLLER_H
#define ADAPTIVE_AIRTIME_DUPLICATION_CONTROLLER_H

#include "prediction-telemetry-collector.h"
#include "secondary-airtime-meter.h"

#include "ns3/callback.h"
#include "ns3/object.h"

#include <cstdint>
#include <fstream>
#include <map>
#include <set>
#include <string>
#include <vector>

namespace ns3
{

class MultipathSender;

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
     * Set the causal calibrated-risk scorer.
     *
     * @param scorer Callback returning a probability in [0, 1].
     */
    void SetRiskScorer(Callback<double, const PredictionSample&> scorer);

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
     * Set the initial shadow price.
     *
     * @param price Price in [0, 1].
     */
    void SetInitialShadowPrice(double price);

    /**
     * Set the dual-update step size.
     *
     * @param step Positive step.
     */
    void SetDualStep(double step);

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

  protected:
    void DoDispose() override;

  private:
    struct FrameState
    {
        bool launched{false}; ///< Whether a secondary copy was launched.
    };

    void InitializeBucket(uint64_t nowNs);
    void RefillBucket(uint64_t nowNs);
    void UpdateShadowPrice(uint64_t nowNs);
    double EstimateSecondaryAirtimeUs(uint32_t packetCount,
                                      uint64_t expectedMacServiceBytes,
                                      double inflation) const;
    double ReferenceAirtimeUs() const;
    void NotifyMeasuredAirtime(uint64_t frameId, double allocatedUs, double ppduDurationUs);
    void NotifySettlement(uint64_t frameId,
                          double releasedUs,
                          double measuredUs,
                          double nominalUs,
                          bool fallback);
    void WriteHeader();
    void WriteDecision(const PredictionSample& sample,
                       double probability,
                       double estimatedUs,
                       double referenceUs,
                       double normalizedCost,
                       double utility,
                       double balanceUs,
                       double reservedUs,
                       double availableUs,
                       const std::string& decision,
                       bool launched);

    MultipathSender* m_sender{nullptr}; ///< Non-owning sender pointer.
    Callback<double, const PredictionSample&> m_scorer; ///< Frozen causal scorer.
    Ptr<SecondaryAirtimeMeter> m_meter; ///< Passive secondary airtime meter.
    uint8_t m_primaryPath{1}; ///< Prediction path (5 GHz by default).
    double m_budgetFraction{0.02}; ///< Long-run airtime fraction.
    uint64_t m_bucketHorizonUs{1000000}; ///< Bucket horizon.
    double m_initialShadowPrice{0.20}; ///< Initial dual variable.
    double m_dualStep{0.01}; ///< Dual update step.
    double m_costSafetyFactor{1.25}; ///< Pre-launch safety factor.
    double m_costEwmaAlpha{0.10}; ///< Retry-inflation EWMA alpha.
    double m_retryInflation{1.0}; ///< Current k_t.
    double m_shadowPrice{0.20}; ///< Current lambda.
    double m_bucketCapacityUs{0}; ///< Maximum tokens.
    double m_bucketBalanceUs{0}; ///< Earned tokens minus measured airtime.
    double m_initialCapacityUs{0}; ///< Recorded startup fill.
    uint64_t m_lastRefillTimeNs{0}; ///< Last refill timestamp.
    uint64_t m_lastPriceUpdateNs{0}; ///< Last T0 shadow-price update.
    double m_measuredSinceLastT0Us{0}; ///< Measured airtime since previous T0.
    bool m_bucketInitialized{false}; ///< Whether the bucket is live.
    std::set<uint64_t> m_decisionOffsetsUs{0, 1000, 2000, 4000}; ///< Enabled stages.
    std::map<uint64_t, FrameState> m_frames; ///< Per-frame launch state.
    std::string m_runId{"run"}; ///< Stable output run identifier.
    std::ofstream m_output; ///< Decision CSV.
    uint64_t m_actions{0}; ///< Successful delayed launches.
};

} // namespace ns3

#endif // ADAPTIVE_AIRTIME_DUPLICATION_CONTROLLER_H
