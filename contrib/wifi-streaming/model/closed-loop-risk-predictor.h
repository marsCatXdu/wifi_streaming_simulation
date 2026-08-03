/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef CLOSED_LOOP_RISK_PREDICTOR_H
#define CLOSED_LOOP_RISK_PREDICTOR_H

#include "prediction-telemetry-collector.h"

#include "ns3/object.h"

#include <array>
#include <cstdint>
#include <optional>
#include <string_view>

namespace ns3
{

class ClosedLoopRiskPredictorTestAccess;

/** Kind and interpretation of one closed-loop runtime score. */
enum class ClosedLoopRiskScoreKind : uint8_t
{
    CALIBRATED_PRIMARY_MISS_PROBABILITY,       ///< Probability of a primary deadline miss.
    WEIGHTED_HEAD_PROBABILITY_ADMISSION_SCORE, ///< Weighted policy score, not a probability.
};

/** Immutable identity of one stage-specific closed-loop model. */
struct ClosedLoopRiskModelIdentity
{
    uint64_t sampleOffsetUs;                    ///< Decision offset from frame generation.
    ClosedLoopRiskScoreKind scoreKind;          ///< Semantic kind of the returned score.
    std::string_view scoreName;                 ///< Stable score output name.
    std::string_view modelId;                   ///< Immutable compiled-model identifier.
    std::string_view primaryMissTargetId;       ///< Primary-miss target identifier.
    std::string_view completedTailTargetId;     ///< Tail target ID, or empty when absent.
    std::string_view sourceModelSha256;         ///< SHA-256 of the source model artifact.
    std::string_view targetProvenanceSha256;    ///< SHA-256 of target provenance.
    std::string_view featureContractSha256;     ///< SHA-256 of features, or empty if legacy.
    std::string_view combinerSha256;            ///< SHA-256 of combiner, or empty if absent.
    std::string_view primaryMissModelSha256;    ///< SHA-256 of compiled miss-head parameters.
    std::string_view completedTailModelSha256;  ///< SHA-256 of tail parameters, or empty.
    uint32_t featureCount;                      ///< Ordered raw model feature count.
};

/** Allocation-free output of one stage-specific closed-loop inference. */
struct ClosedLoopRiskScore
{
    double admissionScore;                    ///< Controller score in [0, 1].
    ClosedLoopRiskScoreKind scoreKind;        ///< Semantic kind of admissionScore.
    std::string_view scoreName;               ///< Stable score output name.
    std::string_view modelId;                 ///< Immutable compiled-model identifier.
    std::string_view sourceModelSha256;       ///< SHA-256 of the source model artifact.
    std::string_view targetProvenanceSha256;  ///< SHA-256 of target provenance.
    std::string_view featureContractSha256;   ///< SHA-256 of features, or empty if legacy.
    std::string_view combinerSha256;          ///< SHA-256 of combiner, or empty if absent.
    std::optional<double> primaryMissProbability;   ///< Calibrated primary-miss probability.
    std::optional<double> completedTailProbability; ///< Calibrated tail probability, if fitted.
};

/**
 * @ingroup wifi-streaming
 * Adapt causal sender snapshots to the frozen 1 ms commodity predictor.
 */
class ClosedLoopRiskPredictor : public Object
{
  public:
    /**
     * Return runtime type information.
     *
     * @return The object TypeId.
     */
    static TypeId GetTypeId();

    ClosedLoopRiskPredictor();
    ~ClosedLoopRiskPredictor() override;

    /**
     * Score one immutable snapshot using its selected polling report.
     *
     * @param sample Current primary-path snapshot.
     * @return Typed stage-specific model output.
     */
    ClosedLoopRiskScore Score(const PredictionSample& sample);

    /**
     * Score a sample whose output is exactly a calibrated miss probability.
     *
     * This compatibility adapter rejects non-probability stages rather than
     * silently passing a weighted admission score to a probability gate.
     *
     * @param sample Current primary-path snapshot.
     * @return Calibrated primary-miss probability.
     */
    double ScorePrimaryMissProbability(const PredictionSample& sample);

    /**
     * Get the immutable compiled-model identity for one supported offset.
     *
     * @param offsetUs Decision offset from frame generation.
     * @return Stage-specific model identity.
     */
    static const ClosedLoopRiskModelIdentity& GetModelIdentity(uint64_t offsetUs);

    /**
     * Check that the expected T0 and T4 compiled model contracts are present.
     *
     * @return True when exact staged inference is available.
     */
    static bool HasExactStagedModelContract();

    /**
     * Get the stable schema string for a score kind.
     *
     * @param kind Score kind.
     * @return Stable lowercase identifier.
     */
    static std::string_view GetScoreKindName(ClosedLoopRiskScoreKind kind);

  protected:
    void DoDispose() override;

  private:
    /**
     * @internal
     * Test-only access to the runtime feature adapter.
     * @endinternal
     */
    friend class ClosedLoopRiskPredictorTestAccess;

    /** Number of features in the widest supported model. */
    static constexpr std::size_t MAX_FEATURE_COUNT = 101;

    /** Stack-resident feature vector shared by the T0 and T4 adapters. */
    using FeatureArray = std::array<double, MAX_FEATURE_COUNT>;

    static double OptionalValue(const std::optional<uint64_t>& value);
    static double OptionalValue(const std::optional<uint32_t>& value);
    static double OptionalValue(const std::optional<uint16_t>& value);
    static double OptionalValue(const std::optional<uint8_t>& value);
    static double OptionalValue(const std::optional<double>& value);
    static double AgeUs(uint64_t sampleTimeNs, const std::optional<uint64_t>& eventTimeNs);
    static double EncodeFrameType(FrameType type);
    static double EncodeFrequencyBand(const std::optional<std::string>& band);
    static const PredictionRollingSample* FindWindow(const PredictionPollingReport& report,
                                                     uint64_t windowUs);
    static FeatureArray BuildFeatures(const PredictionSample& current,
                                      const PredictionPollingReport* report);
    static void ValidateFeatureContracts();
};

} // namespace ns3

#endif // CLOSED_LOOP_RISK_PREDICTOR_H
