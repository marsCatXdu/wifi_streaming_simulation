/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef PREDICTION_MODEL_EVALUATOR_H
#define PREDICTION_MODEL_EVALUATOR_H

#include <cstdint>
#include <span>
#include <string_view>

namespace ns3
{

/**
 * @ingroup wifi-streaming
 * Stage at which an online deadline-risk prediction is evaluated.
 */
enum class PredictionStage : uint8_t
{
    T0, ///< Frame generation.
    T1, ///< One millisecond after frame generation.
    T2, ///< Two milliseconds after frame generation.
    T4  ///< Four milliseconds after frame generation.
};

/**
 * @ingroup wifi-streaming
 * Output of one compiled deadline-risk predictor.
 */
struct PredictionModelResult
{
    double rankingScore;          ///< Uncalibrated histogram-gradient-boosting score.
    double calibratedProbability; ///< Platt-calibrated deadline-miss probability.
};

/**
 * @ingroup wifi-streaming
 * Evaluate the version-1 commodity-polling deadline-risk predictors.
 *
 * Input values must follow the order returned by GetFeatureNames(). A quiet
 * NaN denotes a missing feature. Categorical values use the numeric encoding
 * from the Python prediction feature contract.
 */
class PredictionModelEvaluator
{
  public:
    /**
     * Get the immutable compiled-model identifier.
     *
     * @return Model identifier.
     */
    static std::string_view GetModelId();

    /**
     * Get the identifier of the outcome predicted by the compiled model.
     *
     * @return Target identifier.
     */
    static std::string_view GetTargetId();

    /**
     * Get the SHA-256 digest of the canonical target-provenance object.
     *
     * @return Lowercase hexadecimal SHA-256 digest.
     */
    static std::string_view GetTargetProvenanceSha256();

    /**
     * Get the SHA-256 digest of the source Python model bundle.
     *
     * @return Lowercase hexadecimal SHA-256 digest.
     */
    static std::string_view GetSourceModelSha256();

    /**
     * Get the ordered raw feature contract shared by all exported stages.
     *
     * @return Ordered feature names.
     */
    static std::span<const std::string_view> GetFeatureNames();

    /**
     * Evaluate one stage-specific predictor.
     *
     * @param stage Prediction stage.
     * @param features Raw feature values in GetFeatureNames() order.
     * @return Raw ranking score and calibrated probability.
     */
    static PredictionModelResult Evaluate(PredictionStage stage,
                                          std::span<const double> features);
};

} // namespace ns3

#endif // PREDICTION_MODEL_EVALUATOR_H
