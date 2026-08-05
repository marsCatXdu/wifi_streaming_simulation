/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "temporal-t2-distribution-predictor.h"

#include "ns3/abort.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <utility>

namespace ns3
{
namespace
{

constexpr uint8_t PRIMARY_PATH_ID = 1;
constexpr uint8_t PRIMARY_COPY_ID = 0;
constexpr uint8_t SECONDARY_PATH_ID = 0;
constexpr uint8_t SECONDARY_COPY_ID = 1;
constexpr uint64_t T2_OFFSET_US = 2000;
constexpr uint64_t NANOS_PER_MICROSECOND = 1000;
constexpr uint64_t POLLING_INTERVAL_NS = 1000000;
constexpr uint64_t POLLING_DELAY_NS = 1000000;
constexpr uint64_t MAXIMUM_POLL_STALENESS_NS = 2000000;
constexpr double PHY_FRACTION_SUM_TOLERANCE = 0.000002;
constexpr std::string_view REQUIRED_SUPPORT_MASK = "0x3ffffffffdffff";
constexpr std::array<uint64_t, 3> HISTORY_WINDOWS_US{1000, 5000, 20000};

constexpr std::array<std::string_view,
                     TemporalT2DistributionPredictor::SECONDARY_FEATURE_COUNT>
    g_secondaryFeatureNames{
        "x_secondary_mac_queue_packets",
        "x_secondary_mac_queue_service_bytes",
        "x_secondary_phy_tx_fraction_1ms",
        "x_secondary_phy_rx_fraction_1ms",
        "x_secondary_phy_busy_fraction_1ms",
        "x_secondary_phy_idle_fraction_1ms",
        "x_secondary_phy_other_fraction_1ms",
        "x_secondary_phy_tx_fraction_5ms",
        "x_secondary_phy_rx_fraction_5ms",
        "x_secondary_phy_busy_fraction_5ms",
        "x_secondary_phy_idle_fraction_5ms",
        "x_secondary_phy_other_fraction_5ms",
        "x_secondary_phy_tx_fraction_20ms",
        "x_secondary_phy_rx_fraction_20ms",
        "x_secondary_phy_busy_fraction_20ms",
        "x_secondary_phy_idle_fraction_20ms",
        "x_secondary_phy_other_fraction_20ms",
        "x_secondary_lag1_phy_tx_fraction_1ms",
        "x_secondary_lag1_phy_rx_fraction_1ms",
        "x_secondary_lag1_phy_busy_fraction_1ms",
        "x_secondary_lag1_phy_idle_fraction_1ms",
        "x_secondary_lag1_phy_other_fraction_1ms",
        "x_secondary_lag1_phy_tx_fraction_5ms",
        "x_secondary_lag1_phy_rx_fraction_5ms",
        "x_secondary_lag1_phy_busy_fraction_5ms",
        "x_secondary_lag1_phy_idle_fraction_5ms",
        "x_secondary_lag1_phy_other_fraction_5ms",
        "x_secondary_lag1_phy_tx_fraction_20ms",
        "x_secondary_lag1_phy_rx_fraction_20ms",
        "x_secondary_lag1_phy_busy_fraction_20ms",
        "x_secondary_lag1_phy_idle_fraction_20ms",
        "x_secondary_lag1_phy_other_fraction_20ms",
        "x_secondary_lag3_phy_tx_fraction_1ms",
        "x_secondary_lag3_phy_rx_fraction_1ms",
        "x_secondary_lag3_phy_busy_fraction_1ms",
        "x_secondary_lag3_phy_idle_fraction_1ms",
        "x_secondary_lag3_phy_other_fraction_1ms",
        "x_secondary_lag3_phy_tx_fraction_5ms",
        "x_secondary_lag3_phy_rx_fraction_5ms",
        "x_secondary_lag3_phy_busy_fraction_5ms",
        "x_secondary_lag3_phy_idle_fraction_5ms",
        "x_secondary_lag3_phy_other_fraction_5ms",
        "x_secondary_lag3_phy_tx_fraction_20ms",
        "x_secondary_lag3_phy_rx_fraction_20ms",
        "x_secondary_lag3_phy_busy_fraction_20ms",
        "x_secondary_lag3_phy_idle_fraction_20ms",
        "x_secondary_lag3_phy_other_fraction_20ms",
        "x_secondary_lag8_phy_tx_fraction_1ms",
        "x_secondary_lag8_phy_rx_fraction_1ms",
        "x_secondary_lag8_phy_busy_fraction_1ms",
        "x_secondary_lag8_phy_idle_fraction_1ms",
        "x_secondary_lag8_phy_other_fraction_1ms",
        "x_secondary_lag8_phy_tx_fraction_5ms",
        "x_secondary_lag8_phy_rx_fraction_5ms",
        "x_secondary_lag8_phy_busy_fraction_5ms",
        "x_secondary_lag8_phy_idle_fraction_5ms",
        "x_secondary_lag8_phy_other_fraction_5ms",
        "x_secondary_lag8_phy_tx_fraction_20ms",
        "x_secondary_lag8_phy_rx_fraction_20ms",
        "x_secondary_lag8_phy_busy_fraction_20ms",
        "x_secondary_lag8_phy_idle_fraction_20ms",
        "x_secondary_lag8_phy_other_fraction_20ms",
    };

/** Add binary32-quantized values to one fixed feature vector. */
class FeatureWriter
{
  public:
    /**
     * Create a writer at an existing feature offset.
     *
     * @param values Destination vector.
     * @param index First destination index.
     */
    FeatureWriter(TemporalT2DistributionPredictor::FeatureArray& values, std::size_t index)
        : m_values(values),
          m_index(index)
    {
    }

    /**
     * Add a finite value or missing NaN.
     *
     * @param value Value to quantize and append.
     */
    void
    Add(double value)
    {
        if (!std::isfinite(value) && !std::isnan(value))
        {
            throw std::invalid_argument("distributional T2 feature is infinite");
        }
        if (std::isfinite(value) &&
            std::abs(value) > static_cast<double>(std::numeric_limits<float>::max()))
        {
            throw std::invalid_argument("distributional T2 feature overflows binary32");
        }
        if (m_index >= m_values.size())
        {
            throw std::logic_error("distributional T2 feature adapter exceeded its width");
        }
        m_values[m_index++] = static_cast<double>(static_cast<float>(value));
    }

    /**
     * Add one optional numeric value.
     *
     * @tparam T Numeric optional type.
     * @param value Optional value.
     */
    template <typename T>
    void
    AddOptional(const std::optional<T>& value)
    {
        Add(value ? static_cast<double>(*value) : std::numeric_limits<double>::quiet_NaN());
    }

    /** @return Next destination index. */
    std::size_t
    Size() const
    {
        return m_index;
    }

  private:
    TemporalT2DistributionPredictor::FeatureArray& m_values; ///< Destination vector.
    std::size_t m_index; ///< Next destination index.
};

const PredictionRollingSample*
FindRolling(const PredictionPollingReport& report, uint64_t windowUs)
{
    const auto match = std::find_if(report.rolling.begin(),
                                    report.rolling.end(),
                                    [windowUs](const auto& row) {
                                        return row.windowUs == windowUs;
                                    });
    return match == report.rolling.end() ? nullptr : &*match;
}

bool
HasFiniteFraction(const std::optional<double>& value)
{
    return value && std::isfinite(*value) && *value >= 0.0 && *value <= 1.0;
}

bool
HasConsistentWatermark(const std::optional<uint64_t>& timeNs, uint64_t sequence)
{
    return (sequence == 0) == !timeNs;
}

bool
IsNondecreasing(const std::optional<uint64_t>& prior,
                const std::optional<uint64_t>& current)
{
    if (!prior)
    {
        return true;
    }
    return current && *current >= *prior;
}

void
AppendPhyFractions(FeatureWriter& writer, const PredictionPollingReport& report)
{
    for (const uint64_t window : HISTORY_WINDOWS_US)
    {
        const auto* rolling = FindRolling(report, window);
        if (!rolling)
        {
            throw std::logic_error("distributional T2 validated rolling window disappeared");
        }
        writer.AddOptional(rolling->phyTxFraction);
        writer.AddOptional(rolling->phyRxFraction);
        writer.AddOptional(rolling->phyBusyFraction);
        writer.AddOptional(rolling->phyIdleFraction);
        writer.AddOptional(rolling->phyOtherFraction);
    }
}

} // namespace

TemporalT2DistributionPredictor::TemporalT2DistributionPredictor()
{
    ValidateModelContract();
}

std::optional<std::string>
TemporalT2DistributionPredictor::FindSecondaryReportError(
    const PredictionPollingReport& report,
    uint64_t sampleTimeNs)
{
    if (report.captureTimeNs % POLLING_INTERVAL_NS != 0)
    {
        return "secondary polling capture is off the frozen cadence";
    }
    if (report.captureTimeNs > std::numeric_limits<uint64_t>::max() - POLLING_DELAY_NS ||
        report.availableTimeNs != report.captureTimeNs + POLLING_DELAY_NS)
    {
        return "secondary polling availability differs from capture plus delay";
    }
    if (report.availableTimeNs > sampleTimeNs || sampleTimeNs < report.captureTimeNs ||
        sampleTimeNs - report.captureTimeNs < POLLING_DELAY_NS ||
        sampleTimeNs - report.captureTimeNs >= MAXIMUM_POLL_STALENESS_NS)
    {
        return "secondary polling report is unavailable or has invalid staleness";
    }
    if (!HasConsistentWatermark(report.latestFeatureEventTimeNs,
                                report.latestFeatureEventSequence) ||
        (report.latestFeatureEventTimeNs &&
         *report.latestFeatureEventTimeNs > report.captureTimeNs))
    {
        return "secondary polling watermark is invalid";
    }
    if (report.featureSupportMask != REQUIRED_SUPPORT_MASK)
    {
        return "secondary polling feature support differs";
    }
    if (report.rolling.size() != HISTORY_WINDOWS_US.size())
    {
        return "secondary polling rolling-window count differs";
    }
    for (std::size_t index = 0; index < HISTORY_WINDOWS_US.size(); ++index)
    {
        const auto& rolling = report.rolling[index];
        if (rolling.windowUs != HISTORY_WINDOWS_US[index] ||
            std::abs(rolling.historyCoverageUs - rolling.windowUs) > 1e-9 ||
            !HasFiniteFraction(rolling.phyTxFraction) ||
            !HasFiniteFraction(rolling.phyRxFraction) ||
            !HasFiniteFraction(rolling.phyBusyFraction) ||
            !HasFiniteFraction(rolling.phyIdleFraction) ||
            !HasFiniteFraction(rolling.phyOtherFraction))
        {
            return "secondary polling PHY fraction window is invalid";
        }
        const double sum = *rolling.phyTxFraction + *rolling.phyRxFraction +
                           *rolling.phyBusyFraction + *rolling.phyIdleFraction +
                           *rolling.phyOtherFraction;
        if (std::abs(sum - 1.0) > PHY_FRACTION_SUM_TOLERANCE)
        {
            return "secondary polling PHY fractions do not sum to one";
        }
    }
    return std::nullopt;
}

std::optional<std::string>
TemporalT2DistributionPredictor::FindSecondaryError(const PredictionSample& sample) const
{
    if (sample.telemetrySchemaVersion != PREDICTION_TELEMETRY_SCHEMA_VERSION ||
        PREDICTION_POLLING_SCHEMA_VERSION != 1 || FEATURE_SUPPORT_MASK_VERSION != 2 ||
        sample.key.pathId != SECONDARY_PATH_ID || sample.key.copyId != SECONDARY_COPY_ID)
    {
        return "endpoint is not a supported hypothetical secondary copy";
    }
    if (sample.runId.empty() || sample.sampleStage != "T2" ||
        sample.sampleOffsetUs != T2_OFFSET_US)
    {
        return "secondary endpoint identity or stage differs";
    }
    if (sample.generationTimeNs >
            std::numeric_limits<uint64_t>::max() -
                T2_OFFSET_US * NANOS_PER_MICROSECOND ||
        sample.sampleTimeNs !=
            sample.generationTimeNs + T2_OFFSET_US * NANOS_PER_MICROSECOND)
    {
        return "secondary endpoint timestamp differs";
    }
    if (!sample.pollingReport)
    {
        return "secondary endpoint lacks a delayed polling report";
    }
    if (const auto error = FindSecondaryReportError(*sample.pollingReport, sample.sampleTimeNs))
    {
        return error;
    }
    if (!HasConsistentWatermark(sample.latestFeatureEventTimeNs,
                                sample.latestFeatureEventSequence) ||
        (sample.latestFeatureEventTimeNs &&
         *sample.latestFeatureEventTimeNs > sample.sampleTimeNs))
    {
        return "secondary live watermark is invalid";
    }
    if (sample.featureSupportMask != REQUIRED_SUPPORT_MASK)
    {
        return "secondary live feature support differs";
    }
    if (!m_lastFrameId)
    {
        if (sample.key.frameId != 0)
        {
            return "secondary history does not begin at frame zero";
        }
    }
    else
    {
        if (*m_lastFrameId == std::numeric_limits<uint64_t>::max() ||
            sample.key.frameId != *m_lastFrameId + 1 || sample.runId != m_runId)
        {
            return "secondary history frame sequence or run identity differs";
        }
        const auto* prior = FindSecondarySlot(*m_lastFrameId);
        if (!prior || sample.sampleTimeNs <= prior->sample.sampleTimeNs ||
            sample.pollingReport->captureTimeNs <= prior->report.captureTimeNs)
        {
            return "secondary sample or polling capture time did not increase";
        }
        if (sample.latestFeatureEventSequence < prior->sample.latestFeatureEventSequence ||
            !IsNondecreasing(prior->sample.latestFeatureEventTimeNs,
                             sample.latestFeatureEventTimeNs))
        {
            return "secondary live watermark decreased";
        }
        if (sample.pollingReport->latestFeatureEventSequence <
                prior->report.latestFeatureEventSequence ||
            !IsNondecreasing(prior->report.latestFeatureEventTimeNs,
                             sample.pollingReport->latestFeatureEventTimeNs))
        {
            return "secondary polling watermark decreased";
        }
    }
    for (const uint64_t lag : EXACT_LAGS)
    {
        if (sample.key.frameId >= lag && !FindSecondarySlot(sample.key.frameId - lag))
        {
            return "secondary history lacks an exact required lag frame";
        }
    }
    return std::nullopt;
}

std::optional<std::string>
TemporalT2DistributionPredictor::FindPairError(const PredictionSample& primary,
                                               const PredictionSample& secondary)
{
    if (primary.key.pathId != PRIMARY_PATH_ID || primary.key.copyId != PRIMARY_COPY_ID ||
        primary.telemetrySchemaVersion != secondary.telemetrySchemaVersion ||
        primary.runId != secondary.runId || primary.key.frameId != secondary.key.frameId ||
        primary.sampleStage != secondary.sampleStage ||
        primary.sampleOffsetUs != secondary.sampleOffsetUs ||
        primary.sampleTimeNs != secondary.sampleTimeNs ||
        primary.generationTimeNs != secondary.generationTimeNs ||
        primary.deadlineTimeNs != secondary.deadlineTimeNs ||
        primary.frameAgeUs != secondary.frameAgeUs ||
        primary.deadlineSlackUs != secondary.deadlineSlackUs ||
        primary.frameSizeBytes != secondary.frameSizeBytes ||
        primary.framePacketCount != secondary.framePacketCount ||
        primary.frameType != secondary.frameType || !primary.pollingReport ||
        !secondary.pollingReport ||
        primary.pollingReport->captureTimeNs != secondary.pollingReport->captureTimeNs ||
        primary.pollingReport->availableTimeNs != secondary.pollingReport->availableTimeNs)
    {
        return "paired T2 endpoints disagree on immutable frame metadata";
    }
    const auto nonzero = [](const auto& value) { return value && *value != 0; };
    if (secondary.packetsSubmitted != 0 ||
        secondary.applicationSocketPacketBytesSubmitted != 0 ||
        secondary.packetsRemainingToSubmit != secondary.framePacketCount ||
        secondary.senderMacComplete || !secondary.actionable ||
        nonzero(secondary.framePacketsMacEnqueued) ||
        nonzero(secondary.framePacketsMacDequeued) ||
        nonzero(secondary.framePacketsTxSucceeded) ||
        nonzero(secondary.frameMpduAttemptFailures) ||
        nonzero(secondary.framePacketsTerminallyDropped) ||
        nonzero(secondary.framePacketsCurrentlyQueued) ||
        nonzero(secondary.frameMacServiceBytesCurrentlyQueued))
    {
        return "hypothetical secondary endpoint contains current-frame progress";
    }
    return std::nullopt;
}

const TemporalT2DistributionPredictor::SecondaryHistorySlot*
TemporalT2DistributionPredictor::FindSecondarySlot(uint64_t frameId) const
{
    const auto& slot = m_secondaryHistory[frameId % HISTORY_SLOT_COUNT];
    return slot && slot->frameId == frameId ? &*slot : nullptr;
}

TemporalT2DistributionPredictor::HistoryEvidence
TemporalT2DistributionPredictor::ObservePair(const PredictionSample& primary,
                                             const PredictionSample& secondary)
{
    ValidateModelContract();
    const auto secondaryError = FindSecondaryError(secondary);
    const auto pairError = FindPairError(primary, secondary);
    NS_ABORT_MSG_IF(secondaryError,
                    "Invalid distributional T2 secondary endpoint: " << *secondaryError);
    NS_ABORT_MSG_IF(pairError, "Invalid distributional T2 endpoint pair: " << *pairError);

    const auto primaryEvidence = m_primary.ObservePrimary(primary);
    SecondaryHistorySlot stored;
    stored.frameId = secondary.key.frameId;
    stored.sample = secondary;
    stored.sample.pollingReport.reset();
    stored.report = *secondary.pollingReport;
    m_secondaryHistory[secondary.key.frameId % HISTORY_SLOT_COUNT] = std::move(stored);
    m_lastFrameId = secondary.key.frameId;
    if (secondary.key.frameId == 0)
    {
        m_runId = secondary.runId;
    }

    const auto* current = FindSecondarySlot(secondary.key.frameId);
    NS_ABORT_MSG_IF(!current, "Distributional T2 secondary report was not stored exactly");
    HistoryEvidence evidence;
    evidence.ready = primaryEvidence.ready && secondary.key.frameId >= EXACT_LAGS.back();
    evidence.primary = primaryEvidence;
    evidence.currentSecondaryPollCaptureTimeNs = current->report.captureTimeNs;
    evidence.currentSecondaryPollAvailableTimeNs = current->report.availableTimeNs;
    for (std::size_t index = 0; index < EXACT_LAGS.size(); ++index)
    {
        const uint64_t lag = EXACT_LAGS[index];
        evidence.secondaryLags[index].lagFrames = lag;
        if (secondary.key.frameId >= lag)
        {
            const auto* lagged = FindSecondarySlot(secondary.key.frameId - lag);
            NS_ABORT_MSG_IF(!lagged,
                            "Distributional T2 exact secondary lag disappeared after storage");
            evidence.secondaryLags[index].frameId = lagged->frameId;
            evidence.secondaryLags[index].pollCaptureTimeNs = lagged->report.captureTimeNs;
        }
    }
    return evidence;
}

TemporalT2DistributionPredictor::FeatureArray
TemporalT2DistributionPredictor::BuildFeatures(
    const TemporalT2ValuePredictor::FeatureArray& primaryFeatures,
    const PredictionSample& current,
    const PredictionPollingReport& currentReport,
    const std::array<LaggedSecondaryInput, LAG_COUNT>& lagged)
{
    FeatureArray values;
    std::copy(primaryFeatures.begin(), primaryFeatures.end(), values.begin());
    FeatureWriter writer(values, PRIMARY_FEATURE_COUNT);
    writer.AddOptional(current.macQueuePackets);
    writer.AddOptional(current.macQueueServiceBytes);
    AppendPhyFractions(writer, currentReport);
    for (std::size_t index = 0; index < EXACT_LAGS.size(); ++index)
    {
        if (lagged[index].lagFrames != EXACT_LAGS[index] ||
            current.key.frameId < EXACT_LAGS[index] ||
            lagged[index].sourceFrameId != current.key.frameId - EXACT_LAGS[index] ||
            !lagged[index].report)
        {
            throw std::invalid_argument("distributional T2 exact secondary lag differs");
        }
        AppendPhyFractions(writer, *lagged[index].report);
    }
    if (writer.Size() != FEATURE_COUNT)
    {
        throw std::logic_error("distributional T2 feature adapter width differs");
    }
    return values;
}

TemporalT2DistributionPredictor::FeatureArray
TemporalT2DistributionPredictor::GetFeatureArray(uint64_t frameId) const
{
    ValidateModelContract();
    if (!m_lastFrameId || frameId != *m_lastFrameId)
    {
        throw std::invalid_argument("distributional T2 evaluation is not for latest pair");
    }
    const auto* current = FindSecondarySlot(frameId);
    if (!current)
    {
        throw std::logic_error("distributional T2 latest secondary sample is missing");
    }
    if (frameId < EXACT_LAGS.back())
    {
        throw std::invalid_argument("distributional T2 evaluation lacks exact lag-8 history");
    }
    std::array<LaggedSecondaryInput, LAG_COUNT> lagged;
    for (std::size_t index = 0; index < EXACT_LAGS.size(); ++index)
    {
        const uint64_t lag = EXACT_LAGS[index];
        const auto* slot = FindSecondarySlot(frameId - lag);
        if (!slot)
        {
            throw std::logic_error("distributional T2 stored secondary lag is missing");
        }
        lagged[index] = {lag, slot->frameId, &slot->report};
    }
    return BuildFeatures(m_primary.GetFeatureArray(frameId),
                         current->sample,
                         current->report,
                         lagged);
}

TemporalT2DistributionModelResult
TemporalT2DistributionPredictor::Evaluate(uint64_t frameId) const
{
    const auto result = TemporalT2DistributionModelEvaluator::Evaluate(
        GetFeatureArray(frameId));
    const auto finite = [](const auto& values) {
        return std::all_of(values.begin(), values.end(), [](double value) {
            return std::isfinite(value);
        });
    };
    NS_ABORT_MSG_IF(!finite(result.controlLogits) || !finite(result.controlProbabilities) ||
                        !finite(result.controlCdf) || !finite(result.fullCopyLogits) ||
                        !finite(result.fullCopyProbabilities) || !finite(result.fullCopyCdf) ||
                        !std::isfinite(result.deadlineRescueReward) ||
                        !std::isfinite(result.tail18CdfGain),
                    "Distributional T2 model returned a nonfinite diagnostic");
    return result;
}

bool
TemporalT2DistributionPredictor::PassesFrameGate(FrameType frameType)
{
    return TemporalT2ValuePredictor::PassesFrameGate(frameType);
}

std::span<const std::string_view>
TemporalT2DistributionPredictor::GetFeatureNames()
{
    return TemporalT2DistributionModelEvaluator::GetFeatureNames();
}

bool
TemporalT2DistributionPredictor::HasExactModelContract()
{
    if (!TemporalT2ValuePredictor::HasExactModelContract() ||
        !TemporalT2DistributionModelEvaluator::HasExactRuntimeContract())
    {
        return false;
    }
    const auto primary = TemporalT2ValuePredictor::GetFeatureNames();
    const auto combined = TemporalT2DistributionModelEvaluator::GetFeatureNames();
    if (primary.size() != PRIMARY_FEATURE_COUNT || combined.size() != FEATURE_COUNT)
    {
        return false;
    }
    return std::equal(primary.begin(), primary.end(), combined.begin()) &&
           std::equal(g_secondaryFeatureNames.begin(),
                      g_secondaryFeatureNames.end(),
                      combined.begin() + PRIMARY_FEATURE_COUNT);
}

void
TemporalT2DistributionPredictor::ValidateModelContract()
{
    NS_ABORT_MSG_IF(!HasExactModelContract(),
                    "Distributional T2 compiled model or feature contract differs");
}

} // namespace ns3
