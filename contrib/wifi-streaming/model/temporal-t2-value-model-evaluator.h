/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef TEMPORAL_T2_VALUE_MODEL_EVALUATOR_H
#define TEMPORAL_T2_VALUE_MODEL_EVALUATOR_H

#include <span>
#include <string_view>

namespace ns3
{

/**
 * @ingroup wifi-streaming
 * Immutable provenance for the compiled primary-only temporal T2 policy.
 */
struct TemporalT2ValueModelProvenance
{
    std::string_view evidenceStatus;        ///< Scientific status of source evidence.
    std::string_view featureContractId;     ///< Temporal dataset feature contract.
    std::string_view modelSpecId;           ///< Frozen training model specification.
    std::string_view selectionId;           ///< Calibration selection procedure.
    std::string_view trainingGitCommit;     ///< Clean commit used for canonical fitting.
    std::string_view sourceModelSha256;     ///< Canonical pickle SHA-256.
    std::string_view sourceMetricsSha256;   ///< Training metrics SHA-256.
    std::string_view sourceManifestSha256;  ///< Training artifact manifest SHA-256.
    std::string_view frozenSelectionSha256; ///< Pre-fit selection document SHA-256.
    std::string_view datasetManifestSha256; ///< Temporal dataset manifest SHA-256.
    std::string_view datasetMetadataSha256; ///< Temporal dataset metadata SHA-256.
    std::string_view datasetCsvSha256;      ///< Temporal dataset CSV SHA-256.
    std::string_view trainerSha256;         ///< Committed trainer source SHA-256.
    std::string_view exporterSha256;        ///< Exporter source SHA-256.
    std::string_view plainModelSha256;      ///< Canonical selected-model payload SHA-256.
    std::string_view featureContractSha256; ///< Compiled ordered-feature contract SHA-256.
    std::string_view selectedPolicySha256;  ///< Compiled policy contract SHA-256.
    std::string_view primaryHeadSha256;     ///< Primary bad12 head SHA-256.
    std::string_view treatedHeadSha256;     ///< Treated bad12 head SHA-256.
    std::string_view costModelSha256;       ///< Learned airtime-cost model SHA-256.
};

/**
 * @ingroup wifi-streaming
 * Result of the selected temporal T2 value model.
 */
struct TemporalT2ValueModelResult
{
    double primaryBad12Logit;            ///< Primary bad12 HGB raw logit.
    double primaryBad12Probability;      ///< Predicted primary bad12 probability.
    double treatedBad12Logit;            ///< Treated bad12 HGB raw logit.
    double treatedBad12Probability;      ///< Predicted treated bad12 probability.
    double predictedLogAirtime;          ///< Raw Ridge prediction of log1p airtime.
    double predictedSecondaryAirtimeUs;  ///< Learned secondary-airtime prediction.
    double nonnegativeBad12Value;        ///< Nonnegative probability improvement.
    float valuePerCostScore;             ///< Final float32 value-per-cost score.
    bool passesScoreThreshold;           ///< Whether score is at least the threshold.
};

/**
 * @ingroup wifi-streaming
 * Evaluate the frozen primary-only temporal T2 value model.
 *
 * Inputs follow GetFeatureNames() order. Finite values are rounded to IEEE-754
 * float32 and then widened before preprocessing, exactly as in training. NaN
 * denotes a missing value. Infinity and float32 overflow are rejected.
 *
 * passesScoreThreshold represents only the learned score gate. The caller is
 * responsible for the frozen P-frame, exact-history, actionability, and
 * airtime-budget gates.
 */
class TemporalT2ValueModelEvaluator
{
  public:
    /**
     * Get immutable training and export provenance.
     *
     * @return Compiled provenance record.
     */
    static const TemporalT2ValueModelProvenance& GetProvenance();

    /**
     * Get the ordered primary-only feature contract.
     *
     * @return Ordered feature names.
     */
    static std::span<const std::string_view> GetFeatureNames();

    /**
     * Get the selected primary-only feature-family identifier.
     *
     * @return Feature-family identifier.
     */
    static std::string_view GetFeatureFamily();

    /**
     * Get the frozen input feature-adapter identifier.
     *
     * @return Feature-adapter identifier.
     */
    static std::string_view GetFeatureAdapter();

    /**
     * Get the selected value-per-cost ranker identifier.
     *
     * @return Ranker identifier.
     */
    static std::string_view GetRanker();

    /**
     * Get the caller-owned frame gate identifier.
     *
     * @return Frame gate identifier.
     */
    static std::string_view GetFrameGate();

    /**
     * Get the frozen final-score adapter identifier.
     *
     * @return Score-adapter identifier.
     */
    static std::string_view GetScoreAdapter();

    /**
     * Get the frozen float32 calibration threshold.
     *
     * @return Positive score threshold.
     */
    static float GetScoreThreshold();

    /**
     * Evaluate the selected bad12 value and learned airtime cost.
     *
     * @param features Raw features in GetFeatureNames() order.
     * @return Fitted head diagnostics, cost, score, and threshold result.
     */
    static TemporalT2ValueModelResult Evaluate(std::span<const double> features);
};

} // namespace ns3

#endif // TEMPORAL_T2_VALUE_MODEL_EVALUATOR_H
