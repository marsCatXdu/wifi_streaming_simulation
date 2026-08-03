/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef PRIMARY_TAIL_T4_MODEL_EVALUATOR_H
#define PRIMARY_TAIL_T4_MODEL_EVALUATOR_H

#include <cstdint>
#include <span>
#include <string_view>

namespace ns3
{

/**
 * @ingroup wifi-streaming
 * Immutable provenance for the compiled two-head T4 predictor.
 */
struct PrimaryTailT4ModelProvenance
{
    std::string_view artifactId;                  ///< Training artifact identifier.
    std::string_view modelId;                     ///< Compiled model identifier.
    std::string_view evidenceStatus;              ///< Scientific status of the training data.
    std::string_view pipelineId;                  ///< Fitted preprocessing/model pipeline.
    std::string_view stage;                       ///< Decision stage, always T4.
    std::string_view featureSet;                  ///< Logical feature-set identifier.
    std::string_view degradationProfile;          ///< F1 observation profile identifier.
    std::string_view primaryMissTargetId;         ///< Primary-miss head target identifier.
    std::string_view completedTailTargetId;       ///< Completed-tail head target identifier.
    std::string_view sourceModelSha256;           ///< SHA-256 of the source Python bundle.
    std::string_view datasetSha256;               ///< SHA-256 of the training Parquet dataset.
    std::string_view datasetManifestSha256;       ///< SHA-256 of its dataset manifest.
    std::string_view datasetValidationSha256;     ///< SHA-256 of its validation report.
    std::string_view exportSha256;                ///< SHA-256 of the canonical plain export.
    std::string_view targetProvenanceSha256;      ///< SHA-256 of target provenance.
    std::string_view featureContractSha256;       ///< SHA-256 of the ordered feature contract.
    std::string_view combinerSha256;              ///< SHA-256 of the combiner contract.
    std::string_view primaryMissModelSha256;      ///< SHA-256 of exported miss-head parameters.
    std::string_view completedTailModelSha256;    ///< SHA-256 of exported tail-head parameters.
};

/**
 * @ingroup wifi-streaming
 * Output of the compiled two-head T4 predictor.
 */
struct PrimaryTailT4ModelResult
{
    double primaryMissRankingScore;          ///< Uncalibrated primary-miss HGB score.
    double primaryMissProbability;           ///< Calibrated primary-miss probability.
    double completedTailRankingScore;        ///< Uncalibrated completed-tail HGB score.
    double completedTailProbability;         ///< Calibrated completed-tail probability.
    double admissionScore;                    ///< Bounded admission score, not a probability.
};

/**
 * @ingroup wifi-streaming
 * Evaluate the frozen two-head primary-copy predictor at T4.
 *
 * Input values follow GetFeatureNames() order. A quiet NaN denotes a missing
 * value. Categorical values use the numeric encoding from the prediction
 * feature contract. The two heads are independently Platt calibrated. Their
 * bounded admission score is `(p_miss + 0.2 * p_tail) / 1.2`; it is a policy
 * score and must not be interpreted or logged as a probability.
 */
class PrimaryTailT4ModelEvaluator
{
  public:
    /**
     * Get immutable model and scientific provenance.
     *
     * @return Compiled provenance record.
     */
    static const PrimaryTailT4ModelProvenance& GetProvenance();

    /**
     * Get the ordered logical feature contract consumed by Evaluate().
     *
     * @return Ordered logical feature names.
     */
    static std::span<const std::string_view> GetFeatureNames();

    /**
     * Get the corresponding recorded dataset column names.
     *
     * @return Ordered physical feature names.
     */
    static std::span<const std::string_view> GetPhysicalFeatureNames();

    /**
     * Get the completed-primary latency threshold used to fit the tail head.
     *
     * @return Tail threshold in microseconds.
     */
    static uint32_t GetTailThresholdUs();

    /**
     * Get the admission-score output name.
     *
     * @return Output name.
     */
    static std::string_view GetScoreName();

    /**
     * Get the admission-score kind.
     *
     * @return Score-kind identifier.
     */
    static std::string_view GetScoreKind();

    /**
     * Get the admission-score combiner identifier.
     *
     * @return Combiner identifier.
     */
    static std::string_view GetCombiner();

    /**
     * Get the primary-miss head weight.
     *
     * @return Primary-miss weight.
     */
    static double GetPrimaryMissWeight();

    /**
     * Get the completed-tail head weight.
     *
     * @return Completed-tail weight.
     */
    static double GetCompletedTailWeight();

    /**
     * Get the combiner normalization.
     *
     * @return Positive score normalization.
     */
    static double GetScoreNormalization();

    /**
     * Evaluate both T4 heads and their frozen admission-score combiner.
     *
     * @param features Raw feature values in GetFeatureNames() order.
     * @return Ranking scores, calibrated head probabilities, and admission score.
     */
    static PrimaryTailT4ModelResult Evaluate(std::span<const double> features);
};

} // namespace ns3

#endif // PRIMARY_TAIL_T4_MODEL_EVALUATOR_H
