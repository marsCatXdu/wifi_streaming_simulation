/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef TEMPORAL_T2_DISTRIBUTION_PREDICTOR_H
#define TEMPORAL_T2_DISTRIBUTION_PREDICTOR_H

#include "prediction-telemetry-collector.h"
#include "temporal-t2-distribution-model-evaluator.h"
#include "temporal-t2-value-predictor.h"

#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <string>
#include <string_view>

namespace ns3
{

class TemporalT2DistributionPredictorTestAccess;

/**
 * @ingroup wifi-streaming
 * Own paired T2 telemetry and evaluate the frozen 308-feature distributional
 * predictor.
 *
 * The first 246 features are delegated to TemporalT2ValuePredictor without
 * reinterpretation. The final 62 features contain only passive secondary
 * queue size and current or exact-lag delayed PHY fractions.
 */
class TemporalT2DistributionPredictor
{
  public:
    /** Number of ordered raw model features. */
    static constexpr std::size_t FEATURE_COUNT = 308;

    /** Number of primary-only features reused without change. */
    static constexpr std::size_t PRIMARY_FEATURE_COUNT = 246;

    /** Number of appended passive secondary features. */
    static constexpr std::size_t SECONDARY_FEATURE_COUNT = 62;

    /** Number of exact frame lags used by both paths. */
    static constexpr std::size_t LAG_COUNT = 3;

    /** Number of owned endpoint slots needed for exact lag 8. */
    static constexpr std::size_t HISTORY_SLOT_COUNT = 9;

    /** Stack-resident feature vector in frozen model order. */
    using FeatureArray = std::array<double, FEATURE_COUNT>;

    /** Evidence for one exact secondary lag. */
    struct LagEvidence
    {
        uint64_t lagFrames{0}; ///< Exact frame lag.
        std::optional<uint64_t> frameId; ///< Exact source frame, if available.
        std::optional<uint64_t> pollCaptureTimeNs; ///< Source polling capture, if available.
    };

    /** Paired history state immediately after storing one endpoint pair. */
    struct HistoryEvidence
    {
        bool ready{false}; ///< Whether exact paired lags 1, 3, and 8 are available.
        TemporalT2ValuePredictor::HistoryEvidence primary; ///< Primary history evidence.
        uint64_t currentSecondaryPollCaptureTimeNs{0}; ///< Current secondary poll capture.
        uint64_t currentSecondaryPollAvailableTimeNs{0}; ///< Current secondary availability.
        std::array<LagEvidence, LAG_COUNT> secondaryLags; ///< Secondary lag evidence.
    };

    TemporalT2DistributionPredictor();

    /**
     * Validate and store one primary/hypothetical-secondary T2 pair.
     *
     * Both endpoints are validated before secondary history changes. Every
     * valid pair is stored before any caller-owned policy gate.
     *
     * @param primary Primary path-1/copy-0 endpoint.
     * @param secondary Hypothetical secondary path-0/copy-1 endpoint.
     * @return Exact paired history evidence after storage.
     */
    HistoryEvidence ObservePair(const PredictionSample& primary,
                                const PredictionSample& secondary);

    /**
     * Build the raw 308-feature vector for the latest stored pair.
     *
     * @param frameId Exact identity of the latest owned pair.
     * @return Binary32-quantized and widened features in model order.
     */
    FeatureArray GetFeatureArray(uint64_t frameId) const;

    /**
     * Evaluate the most recently stored, history-ready pair.
     *
     * @param frameId Exact identity of the latest owned pair.
     * @return Completion distributions and separate predicted benefits.
     */
    TemporalT2DistributionModelResult Evaluate(uint64_t frameId) const;

    /**
     * Apply the frozen caller-owned frame gate.
     *
     * @param frameType Frame type to inspect.
     * @return True only for a P frame.
     */
    static bool PassesFrameGate(FrameType frameType);

    /** @return Ordered 308-feature names. */
    static std::span<const std::string_view> GetFeatureNames();

    /** @return True only when the composed adapter and evaluator are exact. */
    static bool HasExactModelContract();

  private:
    friend class TemporalT2DistributionPredictorTestAccess;

    /** One exact secondary endpoint retained in the frame-keyed ring. */
    struct SecondaryHistorySlot
    {
        uint64_t frameId{0}; ///< Exact source frame identifier.
        PredictionSample sample; ///< Owned sample with its report detached.
        PredictionPollingReport report; ///< Owned delayed secondary report.
    };

    /** One secondary report selected for an exact-lag feature build. */
    struct LaggedSecondaryInput
    {
        uint64_t lagFrames{0}; ///< Required lag distance.
        uint64_t sourceFrameId{0}; ///< Exact source frame identifier.
        const PredictionPollingReport* report{nullptr}; ///< Validated source report.
    };

    /** Fixed exact frame lags in model order. */
    static constexpr std::array<uint64_t, LAG_COUNT> EXACT_LAGS{1, 3, 8};

    /**
     * Find a passive-secondary polling-report contract error.
     *
     * @param report Report to validate.
     * @param sampleTimeNs Associated endpoint sample time.
     * @return Stable error text, or empty when valid.
     */
    static std::optional<std::string> FindSecondaryReportError(
        const PredictionPollingReport& report,
        uint64_t sampleTimeNs);

    /**
     * Find an intrinsic or stateful secondary-endpoint contract error.
     *
     * @param sample Endpoint to validate without mutation.
     * @return Stable error text, or empty when valid.
     */
    std::optional<std::string> FindSecondaryError(const PredictionSample& sample) const;

    /**
     * Find immutable pair or untreated-current-frame drift.
     *
     * @param primary Primary endpoint.
     * @param secondary Hypothetical secondary endpoint.
     * @return Stable error text, or empty when valid.
     */
    static std::optional<std::string> FindPairError(const PredictionSample& primary,
                                                    const PredictionSample& secondary);

    /**
     * Compose frozen primary features with passive secondary features.
     *
     * @param primaryFeatures Exact 246-feature primary adapter output.
     * @param current Current secondary endpoint.
     * @param currentReport Current delayed secondary report.
     * @param lagged Exact lag-1, lag-3, and lag-8 secondary reports.
     * @return Frozen 308-feature adapter output.
     */
    static FeatureArray BuildFeatures(
        const TemporalT2ValuePredictor::FeatureArray& primaryFeatures,
        const PredictionSample& current,
        const PredictionPollingReport& currentReport,
        const std::array<LaggedSecondaryInput, LAG_COUNT>& lagged);

    /**
     * Find one exact secondary history entry.
     *
     * @param frameId Exact frame identifier.
     * @return Owned slot, or null if the modulo slot has another identity.
     */
    const SecondaryHistorySlot* FindSecondarySlot(uint64_t frameId) const;

    /** Abort unless the compiled model and composed adapter are exact. */
    static void ValidateModelContract();

    TemporalT2ValuePredictor m_primary; ///< Proven primary feature/history adapter.
    std::array<std::optional<SecondaryHistorySlot>, HISTORY_SLOT_COUNT>
        m_secondaryHistory; ///< Owned passive-secondary endpoint ring.
    std::optional<uint64_t> m_lastFrameId; ///< Most recently observed pair.
    std::string m_runId; ///< Stable run identity pinned by frame zero.
};

} // namespace ns3

#endif // TEMPORAL_T2_DISTRIBUTION_PREDICTOR_H
