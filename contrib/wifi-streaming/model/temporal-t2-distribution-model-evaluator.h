/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef TEMPORAL_T2_DISTRIBUTION_MODEL_EVALUATOR_H
#define TEMPORAL_T2_DISTRIBUTION_MODEL_EVALUATOR_H

#include <array>
#include <cstdint>
#include <span>
#include <string_view>

namespace ns3
{

/**
 * @ingroup wifi-streaming
 * Immutable provenance for the compiled distributional temporal-T2 runtime.
 */
struct TemporalT2DistributionModelProvenance
{
    std::string_view evidenceStatus; ///< Scientific status of the deployment refit.
    std::string_view runtimeContractId; ///< Frozen runtime contract identifier.
    std::string_view runtimeContractSha256; ///< Frozen runtime contract SHA-256.
    std::string_view selectedVariant; ///< Cross-fitted selected predictor variant.
    std::string_view trainingGitCommit; ///< Clean commit used for the full-data refit.
    std::string_view sourceModelPickleSha256; ///< Canonical fitted-model pickle SHA-256.
    std::string_view sourceModelJsonSha256; ///< Portable model JSON SHA-256.
    std::string_view sourceReferenceJsonSha256; ///< Deployment reference JSON SHA-256.
    std::string_view sourceMetricsSha256; ///< Deployment-refit metrics SHA-256.
    std::string_view sourceManifestSha256; ///< Runtime artifact manifest SHA-256.
    std::string_view exporterSha256; ///< Committed exporter source SHA-256.
    std::string_view portableModelSha256; ///< Canonical portable-model component SHA-256.
    std::string_view deploymentReferenceSha256; ///< Canonical reference component SHA-256.
    std::string_view featureContractSha256; ///< Ordered 308-feature contract SHA-256.
};

/**
 * @ingroup wifi-streaming
 * Completion distributions and separate benefits predicted for one T2 frame.
 */
struct TemporalT2DistributionModelResult
{
    std::array<double, 6> controlLogits; ///< Raw CONTROL class scores.
    std::array<double, 6> controlProbabilities; ///< Smoothed CONTROL class probabilities.
    std::array<double, 5> controlCdf; ///< CONTROL completion CDF at fixed thresholds.
    std::array<double, 6> fullCopyLogits; ///< Raw FULL_COPY_T2 class scores.
    std::array<double, 6> fullCopyProbabilities; ///< Smoothed full-copy probabilities.
    std::array<double, 5> fullCopyCdf; ///< Full-copy completion CDF at fixed thresholds.
    double deadlineRescueReward; ///< Nonnegative completion gain at 33333 us.
    double tail18CdfGain; ///< Signed completion gain at 18000 us.
};

/**
 * @ingroup wifi-streaming
 * Evaluate the frozen completion-distribution model and shadow-price reference.
 *
 * Raw inputs follow GetFeatureNames() order. Finite values are rounded to
 * IEEE-754 binary32 and widened before preprocessing, exactly as in training.
 * NaN denotes a missing value; infinity and binary32 overflow are rejected.
 */
class TemporalT2DistributionModelEvaluator
{
  public:
    /** Number of raw model features. */
    static constexpr std::size_t RAW_FEATURE_COUNT = 308;

    /** Number of mutually exclusive completion classes. */
    static constexpr std::size_t CLASS_COUNT = 6;

    /** Number of finite completion thresholds. */
    static constexpr std::size_t CDF_COUNT = 5;

    /** Number of five-second shadow-reference bins. */
    static constexpr uint8_t TIME_BIN_COUNT = 12;

    /** Number of causal primary-congestion regimes. */
    static constexpr uint8_t REGIME_COUNT = 3;

    /**
     * Get immutable source provenance.
     *
     * @return Compiled provenance record.
     */
    static const TemporalT2DistributionModelProvenance& GetProvenance();

    /**
     * Get the exact raw feature order.
     *
     * @return Ordered 308-feature names.
     */
    static std::span<const std::string_view> GetFeatureNames();

    /** @return Selected feature-family identifier. */
    static std::string_view GetFeatureFamily();

    /** @return Frozen binary32-then-binary64 feature adapter. */
    static std::string_view GetFeatureAdapter();

    /** @return Selected six-class HGB specification identifier. */
    static std::string_view GetModelSpecId();

    /** @return Primary allocation objective identifier. */
    static std::string_view GetObjective();

    /** @return Caller-owned frame gate identifier. */
    static std::string_view GetFrameGate();

    /** @return Exact canonical P-frame reservation in microseconds. */
    static double GetCanonicalPFrameReservationUs();

    /** @return Maximum reachable repayable-credit state in microseconds. */
    static double GetMaximumRepayableCreditUs();

    /** @return Frozen shadow-reference time-bin width in microseconds. */
    static uint32_t GetTimeBinWidthUs();

    /**
     * Evaluate both completion-distribution heads.
     *
     * @param features Raw features in GetFeatureNames() order.
     * @return Raw scores, smoothed probabilities, CDFs, and separate benefits.
     */
    static TemporalT2DistributionModelResult Evaluate(std::span<const double> features);

    /**
     * Convert a relative decision time to its fixed reference bin.
     *
     * @param decisionTimeUs Time since measurement start in microseconds.
     * @return Integer time bin from zero through eleven.
     */
    static uint8_t GetTimeBin(double decisionTimeUs);

    /**
     * Assign the causal running busy mean with side-right cutpoint semantics.
     *
     * @param timeBin Fixed reference time bin.
     * @param runningPrimaryBusy20ms Causal running primary busy mean.
     * @return Congestion regime from zero through two.
     */
    static uint8_t GetCongestionRegime(uint8_t timeBin,
                                       double runningPrimaryBusy20ms);

    /**
     * Query the exact reachable shadow-price curve.
     *
     * @param timeBin Fixed reference time bin.
     * @param regime Congestion regime from zero through two.
     * @param repayableCreditUs Current balance plus deterministic remaining refill.
     * @return Marginal reward density, positive infinity, or zero.
     */
    static double GetOpportunityCost(uint8_t timeBin,
                                     uint8_t regime,
                                     double repayableCreditUs);

    /**
     * Check the compiled model, reference, and metadata structure.
     *
     * @return True after the complete generated artifact passes validation.
     */
    static bool HasExactRuntimeContract();
};

} // namespace ns3

#endif // TEMPORAL_T2_DISTRIBUTION_MODEL_EVALUATOR_H
