/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef TEMPORAL_T2_VALUE_PREDICTOR_H
#define TEMPORAL_T2_VALUE_PREDICTOR_H

#include "prediction-telemetry-collector.h"
#include "temporal-t2-value-model-evaluator.h"

#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <string>
#include <string_view>

namespace ns3
{

class TemporalT2ValuePredictorTestAccess;

/**
 * @ingroup wifi-streaming
 * Validate primary T2 telemetry, retain exact frame-keyed history, and run the
 * frozen primary-only temporal value model.
 *
 * ObservePrimary() stores every valid primary endpoint before the caller
 * applies any policy gate. Evaluate() is deliberately separate so the caller
 * can preserve the frozen window, history, frame-type, actionability, and
 * descriptor gate order.
 */
class TemporalT2ValuePredictor
{
  public:
    /** Number of ordered raw model features. */
    static constexpr std::size_t FEATURE_COUNT = 246;

    /** Number of exact frame lags used by the model. */
    static constexpr std::size_t LAG_COUNT = 3;

    /** Number of owned report slots needed for the largest exact lag. */
    static constexpr std::size_t HISTORY_SLOT_COUNT = 9;

    /** Stack-resident feature vector in the frozen model order. */
    using FeatureArray = std::array<double, FEATURE_COUNT>;

    /** Evidence for one exact lag slot. */
    struct LagEvidence
    {
        uint64_t lagFrames{0};                    ///< Exact frame lag.
        std::optional<uint64_t> frameId;           ///< Exact source frame, if available.
        std::optional<uint64_t> pollCaptureTimeNs; ///< Source poll capture, if available.
    };

    /** History state immediately after storing one primary endpoint. */
    struct HistoryEvidence
    {
        bool ready{false}; ///< Whether exact lags 1, 3, and 8 are all available.
        uint64_t currentPollCaptureTimeNs{0};   ///< Current owned-report capture time.
        uint64_t currentPollAvailableTimeNs{0}; ///< Current owned-report availability time.
        std::array<LagEvidence, LAG_COUNT> lags; ///< Exact lag evidence in 1, 3, 8 order.
    };

    TemporalT2ValuePredictor();

    /**
     * Validate and store one primary path-1/copy-0 T2 endpoint.
     *
     * Validation completes before any history state is changed. The report is
     * then copied into the fixed ring before this method returns.
     *
     * @param sample Primary T2 sample with an available polling report.
     * @return History evidence after storing the report.
     */
    HistoryEvidence ObservePrimary(const PredictionSample& sample);

    /**
     * Evaluate the most recently stored, history-ready primary endpoint.
     *
     * This method does not apply the caller-owned P-frame or policy gates.
     *
     * @param frameId Exact identity of the latest owned primary sample.
     * @return Frozen model diagnostics and learned score decision.
     */
    TemporalT2ValueModelResult Evaluate(uint64_t frameId) const;

    /**
     * Build the raw primary-only feature vector for the latest stored frame.
     *
     * This exposes the already-validated adapter for composition with a
     * separately frozen predictor. It does not evaluate a model or apply a
     * policy gate.
     *
     * @param frameId Exact identity of the latest owned primary sample.
     * @return Frozen 246-feature primary adapter output.
     */
    FeatureArray GetFeatureArray(uint64_t frameId) const;

    /**
     * Apply the frozen caller-owned frame gate.
     *
     * @param frameType Frame type to inspect.
     * @return True only for a P frame.
     */
    static bool PassesFrameGate(FrameType frameType);

    /**
     * Return the independent runtime feature-name contract.
     *
     * @return Ordered primary-only feature names.
     */
    static std::span<const std::string_view> GetFeatureNames();

    /**
     * Check the complete frozen evaluator and feature-adapter identity.
     *
     * @return True only when every pinned identifier, digest, name, and the
     *         float32 threshold matches.
     */
    static bool HasExactModelContract();

  private:
    friend class TemporalT2ValuePredictorTestAccess;

    /** One exact primary endpoint retained in the frame-keyed ring. */
    struct HistorySlot
    {
        uint64_t frameId{0}; ///< Exact source frame identifier.
        PredictionSample sample; ///< Owned exact live sample with its report detached.
        PredictionPollingReport report; ///< Owned exact delayed primary report.
    };

    /** One report selected for a stateless exact-lag feature build. */
    struct LaggedReportInput
    {
        uint64_t lagFrames{0}; ///< Required lag distance.
        uint64_t sourceFrameId{0}; ///< Exact source frame identifier.
        const PredictionPollingReport* report{nullptr}; ///< Owned validated source report.
    };

    /** Fixed exact frame lags in model order. */
    static constexpr std::array<uint64_t, LAG_COUNT> EXACT_LAGS{1, 3, 8};

    /**
     * Find a standalone delayed-report contract error.
     *
     * @param report Report to validate.
     * @param sampleTimeNs Associated endpoint sample time.
     * @return Stable error text, or empty when valid.
     */
    static std::optional<std::string> FindReportError(const PredictionPollingReport& report,
                                                      uint64_t sampleTimeNs);

    /**
     * Find an intrinsic or stateful primary-endpoint contract error.
     *
     * @param sample Endpoint to validate without mutation.
     * @return Stable error text, or empty when valid.
     */
    std::optional<std::string> FindPrimaryError(const PredictionSample& sample) const;

    /**
     * Build the exact raw feature vector from a current sample and four reports.
     *
     * @param current Current primary sample.
     * @param currentReport Current delayed primary report.
     * @param lagged Exact lag-1, lag-3, and lag-8 reports.
     * @return Float32-quantized and exactly widened feature vector.
     */
    static FeatureArray BuildFeatures(
        const PredictionSample& current,
        const PredictionPollingReport& currentReport,
        const std::array<LaggedReportInput, LAG_COUNT>& lagged);

    /**
     * Build features through the owned exact-history path.
     *
     * @param frameId Exact identity of the latest owned primary sample.
     * @return Frozen raw feature vector.
     */
    FeatureArray BuildStoredFeatures(uint64_t frameId) const;

    /**
     * Find one exact report-ring entry.
     *
     * @param frameId Exact frame identifier.
     * @return Owned slot, or null if the modulo slot has another identity.
     */
    const HistorySlot* FindSlot(uint64_t frameId) const;

    /** Abort unless the compiled model and independent adapter are exact. */
    static void ValidateModelContract();

    std::array<std::optional<HistorySlot>, HISTORY_SLOT_COUNT> m_history; ///< Owned report ring.
    std::optional<uint64_t> m_lastFrameId; ///< Most recently observed frame.
    std::string m_runId; ///< Stable run identity pinned by frame zero.
};

} // namespace ns3

#endif // TEMPORAL_T2_VALUE_PREDICTOR_H
