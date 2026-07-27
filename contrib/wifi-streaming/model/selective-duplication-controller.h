/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef SELECTIVE_DUPLICATION_CONTROLLER_H
#define SELECTIVE_DUPLICATION_CONTROLLER_H

#include "prediction-telemetry-collector.h"

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
 * Apply a causal risk threshold and frame-token budget to delayed duplication.
 */
class SelectiveDuplicationController : public Object
{
  public:
    /**
     * Return runtime type information.
     *
     * @return The object TypeId.
     */
    static TypeId GetTypeId();

    SelectiveDuplicationController();
    ~SelectiveDuplicationController() override;

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
     * Set the path whose frame-copy snapshots drive prediction.
     *
     * @param pathId Primary prediction path.
     */
    void SetPrimaryPath(uint8_t pathId);

    /**
     * Set the calibrated probability threshold.
     *
     * @param threshold Probability threshold in [0, 1].
     */
    void SetProbabilityThreshold(double threshold);

    /**
     * Set the long-run frame action budget.
     *
     * @param budget Fractional token refill in (0, 1].
     */
    void SetFrameBudget(double budget);

    /**
     * Set the token-bucket burst horizon.
     *
     * @param frames Positive horizon in generated frames.
     */
    void SetBurstHorizonFrames(uint32_t frames);

    /**
     * Set the snapshot offsets eligible for decisions.
     *
     * @param offsetsUs Distinct offsets from frame generation.
     */
    void SetDecisionOffsetsUs(const std::vector<uint64_t>& offsetsUs);

    /**
     * Configure the append-free decision output.
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
     * Return the number of launched secondary copies.
     *
     * @return Successful action count.
     */
    uint64_t GetActionCount() const;

    /**
     * Return the number of threshold crossings suppressed by the budget.
     *
     * @return Budget-suppression count.
     */
    uint64_t GetBudgetSuppressionCount() const;

    /**
     * Return the current frame-token balance.
     *
     * @return Current token balance.
     */
    double GetTokenBalance() const;

  protected:
    void DoDispose() override;

  private:
    struct FrameState
    {
        bool resolved{false}; ///< Whether a threshold crossing has resolved the frame.
    };

    void InitializeBucket();
    void WriteHeader();
    void WriteDecision(const PredictionSample& sample,
                       double probability,
                       double tokensBefore,
                       const std::string& decision,
                       bool launched);

    MultipathSender* m_sender{nullptr}; ///< Non-owning sender pointer.
    Callback<double, const PredictionSample&> m_scorer; ///< Frozen causal scorer.
    uint8_t m_primaryPath{1};             ///< Prediction path (5 GHz by default).
    double m_probabilityThreshold{0.2}; ///< Calibrated action threshold.
    double m_frameBudget{0.3};          ///< Tokens refilled per generated frame.
    uint32_t m_burstHorizonFrames{30};  ///< Bucket capacity horizon.
    double m_tokenCapacity{0};          ///< Maximum frame tokens.
    double m_tokenBalance{0};           ///< Available frame tokens.
    bool m_bucketInitialized{false};    ///< Whether capacity has been resolved.
    std::set<uint64_t> m_decisionOffsetsUs{0, 1000, 2000, 4000}; ///< Enabled stages.
    std::map<uint64_t, FrameState> m_frames; ///< Per-frame decisions.
    std::string m_runId{"run"};         ///< Stable output run identifier.
    std::ofstream m_output;             ///< Decision CSV.
    uint64_t m_actions{0};              ///< Successful delayed launches.
    uint64_t m_budgetSuppressions{0};   ///< Suppressed threshold crossings.
};

} // namespace ns3

#endif // SELECTIVE_DUPLICATION_CONTROLLER_H
