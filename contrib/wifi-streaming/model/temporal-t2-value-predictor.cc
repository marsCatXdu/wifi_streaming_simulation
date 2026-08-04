/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "temporal-t2-value-predictor.h"

#include "ns3/abort.h"

#include <algorithm>
#include <array>
#include <bit>
#include <charconv>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <system_error>
#include <utility>

namespace ns3
{
namespace
{

constexpr uint8_t PRIMARY_PATH_ID = 1;
constexpr uint8_t PRIMARY_COPY_ID = 0;
constexpr uint64_t T2_OFFSET_US = 2000;
constexpr uint64_t NANOS_PER_MICROSECOND = 1000;
constexpr uint64_t POLLING_INTERVAL_NS = 1000000;
constexpr uint64_t POLLING_DELAY_NS = 1000000;
constexpr uint64_t REQUIRED_SUPPORT_MASK = 0x3ffffffffdffffULL;
constexpr double PHY_FRACTION_SUM_TOLERANCE = 0.000002;
constexpr double LAST_EVENT_AGE_CAP_US = 1000000.0;

constexpr std::array<uint64_t, 3> HISTORY_WINDOWS_US{1000, 5000, 20000};

constexpr std::array<std::string_view, TemporalT2ValuePredictor::FEATURE_COUNT>
    g_featureNames{
        "x_f0_frame_age_us",
        "x_f0_deadline_slack_us",
        "x_f0_frame_size_bytes",
        "x_f0_frame_packet_count",
        "x_f0_packets_submitted",
        "x_f0_application_socket_packet_bytes_submitted",
        "x_f0_packets_remaining_to_submit",
        "x_primary_frame_packets_mac_enqueued",
        "x_primary_frame_packets_mac_dequeued",
        "x_primary_frame_packets_tx_succeeded",
        "x_primary_frame_mpdu_attempt_failures",
        "x_primary_frame_packets_terminally_dropped",
        "x_primary_frame_packets_currently_queued",
        "x_primary_frame_mac_service_bytes_currently_queued",
        "x_primary_mac_queue_packets",
        "x_primary_mac_queue_service_bytes",
        "x_primary_packets_ahead_of_frame",
        "x_primary_mac_service_bytes_ahead_of_frame",
        "x_primary_frame_packets_pending_primary",
        "x_primary_frame_mac_service_bytes_not_acknowledged",
        "x_primary_frame_mac_service_bytes_pending_primary",
        "x_primary_mpdu_attempts_1ms",
        "x_primary_mpdu_positive_acks_1ms",
        "x_primary_mpdu_attempt_failures_1ms",
        "x_primary_mpdu_retries_1ms",
        "x_primary_mpdu_retry_ratio_1ms",
        "x_primary_acknowledged_mac_service_bytes_1ms",
        "x_primary_mpdu_queue_to_ack_mean_1ms_us",
        "x_primary_mpdu_queue_to_ack_p95_1ms_us",
        "x_primary_mpdu_first_attempt_to_ack_mean_1ms_us",
        "x_primary_mpdu_first_attempt_to_ack_p95_1ms_us",
        "x_primary_phy_tx_fraction_1ms",
        "x_primary_phy_rx_fraction_1ms",
        "x_primary_phy_busy_fraction_1ms",
        "x_primary_phy_idle_fraction_1ms",
        "x_primary_phy_other_fraction_1ms",
        "x_primary_mpdu_attempts_5ms",
        "x_primary_mpdu_positive_acks_5ms",
        "x_primary_mpdu_attempt_failures_5ms",
        "x_primary_mpdu_retries_5ms",
        "x_primary_mpdu_retry_ratio_5ms",
        "x_primary_acknowledged_mac_service_bytes_5ms",
        "x_primary_mpdu_queue_to_ack_mean_5ms_us",
        "x_primary_mpdu_queue_to_ack_p95_5ms_us",
        "x_primary_mpdu_first_attempt_to_ack_mean_5ms_us",
        "x_primary_mpdu_first_attempt_to_ack_p95_5ms_us",
        "x_primary_phy_tx_fraction_5ms",
        "x_primary_phy_rx_fraction_5ms",
        "x_primary_phy_busy_fraction_5ms",
        "x_primary_phy_idle_fraction_5ms",
        "x_primary_phy_other_fraction_5ms",
        "x_primary_mpdu_attempts_20ms",
        "x_primary_mpdu_positive_acks_20ms",
        "x_primary_mpdu_attempt_failures_20ms",
        "x_primary_mpdu_retries_20ms",
        "x_primary_mpdu_retry_ratio_20ms",
        "x_primary_acknowledged_mac_service_bytes_20ms",
        "x_primary_mpdu_queue_to_ack_mean_20ms_us",
        "x_primary_mpdu_queue_to_ack_p95_20ms_us",
        "x_primary_mpdu_first_attempt_to_ack_mean_20ms_us",
        "x_primary_mpdu_first_attempt_to_ack_p95_20ms_us",
        "x_primary_phy_tx_fraction_20ms",
        "x_primary_phy_rx_fraction_20ms",
        "x_primary_phy_busy_fraction_20ms",
        "x_primary_phy_idle_fraction_20ms",
        "x_primary_phy_other_fraction_20ms",
        "x_f0_frame_type=I_FRAME",
        "x_f0_frame_type=P_FRAME",
        "x_compact_primary_working_rate_margin_1ms_bytes_per_us",
        "x_compact_primary_working_rate_margin_5ms_bytes_per_us",
        "x_compact_primary_working_rate_margin_20ms_bytes_per_us",
        "x_compact_primary_ahead_clearance_us_at_5ms_rate",
        "x_compact_primary_ahead_clearance_over_slack_at_5ms_rate",
        "x_compact_primary_ack_rate_trend_1ms_minus_20ms",
        "x_compact_primary_busy_trend_1ms_minus_20ms",
        "x_primary_delayed_current_mcs",
        "x_primary_delayed_current_mcs_missing",
        "x_primary_delayed_current_nss",
        "x_primary_delayed_current_nss_missing",
        "x_primary_delayed_current_channel_width_mhz",
        "x_primary_delayed_current_channel_width_mhz_missing",
        "x_primary_delayed_current_guard_interval_ns",
        "x_primary_delayed_current_guard_interval_ns_missing",
        "x_primary_delayed_current_last_tx_age_us",
        "x_primary_delayed_current_last_tx_age_us_missing",
        "x_primary_delayed_current_last_ack_age_us",
        "x_primary_delayed_current_last_ack_age_us_missing",
        "x_primary_lag1_mpdu_attempts_1ms",
        "x_primary_lag1_mpdu_positive_acks_1ms",
        "x_primary_lag1_mpdu_attempt_failures_1ms",
        "x_primary_lag1_mpdu_retries_1ms",
        "x_primary_lag1_acknowledged_mac_service_bytes_1ms",
        "x_primary_lag1_phy_tx_fraction_1ms",
        "x_primary_lag1_phy_rx_fraction_1ms",
        "x_primary_lag1_phy_busy_fraction_1ms",
        "x_primary_lag1_phy_idle_fraction_1ms",
        "x_primary_lag1_phy_other_fraction_1ms",
        "x_primary_lag1_mpdu_retry_ratio_1ms",
        "x_primary_lag1_mpdu_retry_structural_zero_1ms",
        "x_primary_lag1_mpdu_attempts_5ms",
        "x_primary_lag1_mpdu_positive_acks_5ms",
        "x_primary_lag1_mpdu_attempt_failures_5ms",
        "x_primary_lag1_mpdu_retries_5ms",
        "x_primary_lag1_acknowledged_mac_service_bytes_5ms",
        "x_primary_lag1_phy_tx_fraction_5ms",
        "x_primary_lag1_phy_rx_fraction_5ms",
        "x_primary_lag1_phy_busy_fraction_5ms",
        "x_primary_lag1_phy_idle_fraction_5ms",
        "x_primary_lag1_phy_other_fraction_5ms",
        "x_primary_lag1_mpdu_retry_ratio_5ms",
        "x_primary_lag1_mpdu_retry_structural_zero_5ms",
        "x_primary_lag1_mpdu_attempts_20ms",
        "x_primary_lag1_mpdu_positive_acks_20ms",
        "x_primary_lag1_mpdu_attempt_failures_20ms",
        "x_primary_lag1_mpdu_retries_20ms",
        "x_primary_lag1_acknowledged_mac_service_bytes_20ms",
        "x_primary_lag1_phy_tx_fraction_20ms",
        "x_primary_lag1_phy_rx_fraction_20ms",
        "x_primary_lag1_phy_busy_fraction_20ms",
        "x_primary_lag1_phy_idle_fraction_20ms",
        "x_primary_lag1_phy_other_fraction_20ms",
        "x_primary_lag1_mpdu_retry_ratio_20ms",
        "x_primary_lag1_mpdu_retry_structural_zero_20ms",
        "x_primary_lag1_mcs",
        "x_primary_lag1_mcs_missing",
        "x_primary_lag1_nss",
        "x_primary_lag1_nss_missing",
        "x_primary_lag1_channel_width_mhz",
        "x_primary_lag1_channel_width_mhz_missing",
        "x_primary_lag1_guard_interval_ns",
        "x_primary_lag1_guard_interval_ns_missing",
        "x_primary_lag1_last_tx_age_us",
        "x_primary_lag1_last_tx_age_us_missing",
        "x_primary_lag1_last_ack_age_us",
        "x_primary_lag1_last_ack_age_us_missing",
        "x_primary_since_lag1_mpdu_attempts_per_ms",
        "x_primary_since_lag1_mpdu_positive_acks_per_ms",
        "x_primary_since_lag1_mpdu_attempt_failures_per_ms",
        "x_primary_since_lag1_mpdu_retries_per_ms",
        "x_primary_since_lag1_ppdu_tx_count_per_ms",
        "x_primary_lag3_mpdu_attempts_1ms",
        "x_primary_lag3_mpdu_positive_acks_1ms",
        "x_primary_lag3_mpdu_attempt_failures_1ms",
        "x_primary_lag3_mpdu_retries_1ms",
        "x_primary_lag3_acknowledged_mac_service_bytes_1ms",
        "x_primary_lag3_phy_tx_fraction_1ms",
        "x_primary_lag3_phy_rx_fraction_1ms",
        "x_primary_lag3_phy_busy_fraction_1ms",
        "x_primary_lag3_phy_idle_fraction_1ms",
        "x_primary_lag3_phy_other_fraction_1ms",
        "x_primary_lag3_mpdu_retry_ratio_1ms",
        "x_primary_lag3_mpdu_retry_structural_zero_1ms",
        "x_primary_lag3_mpdu_attempts_5ms",
        "x_primary_lag3_mpdu_positive_acks_5ms",
        "x_primary_lag3_mpdu_attempt_failures_5ms",
        "x_primary_lag3_mpdu_retries_5ms",
        "x_primary_lag3_acknowledged_mac_service_bytes_5ms",
        "x_primary_lag3_phy_tx_fraction_5ms",
        "x_primary_lag3_phy_rx_fraction_5ms",
        "x_primary_lag3_phy_busy_fraction_5ms",
        "x_primary_lag3_phy_idle_fraction_5ms",
        "x_primary_lag3_phy_other_fraction_5ms",
        "x_primary_lag3_mpdu_retry_ratio_5ms",
        "x_primary_lag3_mpdu_retry_structural_zero_5ms",
        "x_primary_lag3_mpdu_attempts_20ms",
        "x_primary_lag3_mpdu_positive_acks_20ms",
        "x_primary_lag3_mpdu_attempt_failures_20ms",
        "x_primary_lag3_mpdu_retries_20ms",
        "x_primary_lag3_acknowledged_mac_service_bytes_20ms",
        "x_primary_lag3_phy_tx_fraction_20ms",
        "x_primary_lag3_phy_rx_fraction_20ms",
        "x_primary_lag3_phy_busy_fraction_20ms",
        "x_primary_lag3_phy_idle_fraction_20ms",
        "x_primary_lag3_phy_other_fraction_20ms",
        "x_primary_lag3_mpdu_retry_ratio_20ms",
        "x_primary_lag3_mpdu_retry_structural_zero_20ms",
        "x_primary_lag3_mcs",
        "x_primary_lag3_mcs_missing",
        "x_primary_lag3_nss",
        "x_primary_lag3_nss_missing",
        "x_primary_lag3_channel_width_mhz",
        "x_primary_lag3_channel_width_mhz_missing",
        "x_primary_lag3_guard_interval_ns",
        "x_primary_lag3_guard_interval_ns_missing",
        "x_primary_lag3_last_tx_age_us",
        "x_primary_lag3_last_tx_age_us_missing",
        "x_primary_lag3_last_ack_age_us",
        "x_primary_lag3_last_ack_age_us_missing",
        "x_primary_since_lag3_mpdu_attempts_per_ms",
        "x_primary_since_lag3_mpdu_positive_acks_per_ms",
        "x_primary_since_lag3_mpdu_attempt_failures_per_ms",
        "x_primary_since_lag3_mpdu_retries_per_ms",
        "x_primary_since_lag3_ppdu_tx_count_per_ms",
        "x_primary_lag8_mpdu_attempts_1ms",
        "x_primary_lag8_mpdu_positive_acks_1ms",
        "x_primary_lag8_mpdu_attempt_failures_1ms",
        "x_primary_lag8_mpdu_retries_1ms",
        "x_primary_lag8_acknowledged_mac_service_bytes_1ms",
        "x_primary_lag8_phy_tx_fraction_1ms",
        "x_primary_lag8_phy_rx_fraction_1ms",
        "x_primary_lag8_phy_busy_fraction_1ms",
        "x_primary_lag8_phy_idle_fraction_1ms",
        "x_primary_lag8_phy_other_fraction_1ms",
        "x_primary_lag8_mpdu_retry_ratio_1ms",
        "x_primary_lag8_mpdu_retry_structural_zero_1ms",
        "x_primary_lag8_mpdu_attempts_5ms",
        "x_primary_lag8_mpdu_positive_acks_5ms",
        "x_primary_lag8_mpdu_attempt_failures_5ms",
        "x_primary_lag8_mpdu_retries_5ms",
        "x_primary_lag8_acknowledged_mac_service_bytes_5ms",
        "x_primary_lag8_phy_tx_fraction_5ms",
        "x_primary_lag8_phy_rx_fraction_5ms",
        "x_primary_lag8_phy_busy_fraction_5ms",
        "x_primary_lag8_phy_idle_fraction_5ms",
        "x_primary_lag8_phy_other_fraction_5ms",
        "x_primary_lag8_mpdu_retry_ratio_5ms",
        "x_primary_lag8_mpdu_retry_structural_zero_5ms",
        "x_primary_lag8_mpdu_attempts_20ms",
        "x_primary_lag8_mpdu_positive_acks_20ms",
        "x_primary_lag8_mpdu_attempt_failures_20ms",
        "x_primary_lag8_mpdu_retries_20ms",
        "x_primary_lag8_acknowledged_mac_service_bytes_20ms",
        "x_primary_lag8_phy_tx_fraction_20ms",
        "x_primary_lag8_phy_rx_fraction_20ms",
        "x_primary_lag8_phy_busy_fraction_20ms",
        "x_primary_lag8_phy_idle_fraction_20ms",
        "x_primary_lag8_phy_other_fraction_20ms",
        "x_primary_lag8_mpdu_retry_ratio_20ms",
        "x_primary_lag8_mpdu_retry_structural_zero_20ms",
        "x_primary_lag8_mcs",
        "x_primary_lag8_mcs_missing",
        "x_primary_lag8_nss",
        "x_primary_lag8_nss_missing",
        "x_primary_lag8_channel_width_mhz",
        "x_primary_lag8_channel_width_mhz_missing",
        "x_primary_lag8_guard_interval_ns",
        "x_primary_lag8_guard_interval_ns_missing",
        "x_primary_lag8_last_tx_age_us",
        "x_primary_lag8_last_tx_age_us_missing",
        "x_primary_lag8_last_ack_age_us",
        "x_primary_lag8_last_ack_age_us_missing",
        "x_primary_since_lag8_mpdu_attempts_per_ms",
        "x_primary_since_lag8_mpdu_positive_acks_per_ms",
        "x_primary_since_lag8_mpdu_attempt_failures_per_ms",
        "x_primary_since_lag8_mpdu_retries_per_ms",
        "x_primary_since_lag8_ppdu_tx_count_per_ms",
    };

/** Add float32-quantized values to one fixed feature vector. */
class FeatureWriter
{
  public:
    /**
     * Create a writer for one output vector.
     *
     * @param values Destination feature vector.
     */
    explicit FeatureWriter(TemporalT2ValuePredictor::FeatureArray& values)
        : m_values(values)
    {
    }

    /**
     * Add a finite value or a missing NaN using the frozen numeric adapter.
     *
     * @param value Value to add.
     */
    void Add(double value)
    {
        if (!std::isfinite(value) && !std::isnan(value))
        {
            throw std::invalid_argument("temporal T2 feature is infinite");
        }
        if (std::isfinite(value) &&
            std::abs(value) > static_cast<double>(std::numeric_limits<float>::max()))
        {
            throw std::invalid_argument("temporal T2 feature overflows float32");
        }
        const float quantized = static_cast<float>(value);
        if (m_index >= m_values.size())
        {
            throw std::logic_error("temporal T2 feature adapter exceeded its width");
        }
        m_values[m_index++] = static_cast<double>(quantized);
    }

    /**
     * Add one optional numeric value.
     *
     * @tparam T Numeric optional type.
     * @param value Optional value.
     */
    template <typename T>
    void AddOptional(const std::optional<T>& value)
    {
        Add(value ? static_cast<double>(*value) : std::numeric_limits<double>::quiet_NaN());
    }

    /**
     * Return the number of values written.
     *
     * @return Current output index.
     */
    std::size_t Size() const
    {
        return m_index;
    }

  private:
    TemporalT2ValuePredictor::FeatureArray& m_values; ///< Destination vector.
    std::size_t m_index{0}; ///< Next output position.
};

std::optional<uint64_t>
ParseHexMask(std::string_view text)
{
    if (text.size() <= 2 || text[0] != '0' || (text[1] != 'x' && text[1] != 'X'))
    {
        return std::nullopt;
    }
    uint64_t value = 0;
    const char* first = text.data() + 2;
    const char* last = text.data() + text.size();
    const auto [end, error] = std::from_chars(first, last, value, 16);
    if (error != std::errc() || end != last)
    {
        return std::nullopt;
    }
    return value;
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

bool
IsFiniteNonnegativeFloat32(const std::optional<double>& value)
{
    return value && std::isfinite(*value) && *value >= 0.0 &&
           std::abs(*value) <= static_cast<double>(std::numeric_limits<float>::max());
}

std::array<uint64_t, 9>
GetCounters(const PredictionPollingReport& report)
{
    return {*report.mpduTxAttemptsTotal,
            *report.mpduPositiveAcksTotal,
            *report.mpduTxAttemptFailuresTotal,
            *report.mpduRetriesTotal,
            *report.mpduTerminalDropsTotal,
            *report.mpduRetryLimitDropsTotal,
            *report.mpduLifetimeDropsTotal,
            *report.mpduQueueDropsTotal,
            *report.ppduTxCountTotal};
}

bool
HasAllCounters(const PredictionPollingReport& report)
{
    return report.mpduTxAttemptsTotal && report.mpduPositiveAcksTotal &&
           report.mpduTxAttemptFailuresTotal && report.mpduRetriesTotal &&
           report.mpduTerminalDropsTotal && report.mpduRetryLimitDropsTotal &&
           report.mpduLifetimeDropsTotal && report.mpduQueueDropsTotal &&
           report.ppduTxCountTotal;
}

double
Missing()
{
    return std::numeric_limits<double>::quiet_NaN();
}

} // namespace

TemporalT2ValuePredictor::TemporalT2ValuePredictor() = default;

std::optional<std::string>
TemporalT2ValuePredictor::FindReportError(const PredictionPollingReport& report,
                                          uint64_t sampleTimeNs)
{
    const auto supportMask = ParseHexMask(report.featureSupportMask);
    if (!supportMask || *supportMask != REQUIRED_SUPPORT_MASK)
    {
        return "primary polling support mask differs from the frozen exact mask";
    }
    if (report.captureTimeNs % POLLING_INTERVAL_NS != 0)
    {
        return "primary polling capture is off the frozen cadence";
    }
    if (report.captureTimeNs >
        std::numeric_limits<uint64_t>::max() - POLLING_DELAY_NS)
    {
        return "primary polling availability calculation overflows";
    }
    if (report.availableTimeNs != report.captureTimeNs + POLLING_DELAY_NS)
    {
        return "primary polling availability delay differs";
    }
    if (report.availableTimeNs > sampleTimeNs || report.captureTimeNs > sampleTimeNs)
    {
        return "primary polling report is unavailable at the sample";
    }
    const uint64_t stalenessNs = sampleTimeNs - report.captureTimeNs;
    if (stalenessNs < POLLING_DELAY_NS ||
        stalenessNs >= POLLING_DELAY_NS + POLLING_INTERVAL_NS)
    {
        return "primary polling staleness is outside [1 ms, 2 ms)";
    }
    if (!HasConsistentWatermark(report.latestFeatureEventTimeNs,
                                report.latestFeatureEventSequence))
    {
        return "primary polling watermark has inconsistent absence and sequence";
    }
    if (report.latestFeatureEventTimeNs &&
        *report.latestFeatureEventTimeNs > report.captureTimeNs)
    {
        return "primary polling watermark is in the future";
    }
    if ((report.lastTxAttemptTimeNs &&
         *report.lastTxAttemptTimeNs > report.captureTimeNs) ||
        (report.lastPositiveAckTimeNs &&
         *report.lastPositiveAckTimeNs > report.captureTimeNs))
    {
        return "primary polling radio event is in the future";
    }
    if (!HasAllCounters(report))
    {
        return "primary polling report lacks a required cumulative counter";
    }
    if (report.rolling.size() != HISTORY_WINDOWS_US.size())
    {
        return "primary polling report has the wrong rolling-window count";
    }
    for (std::size_t index = 0; index < HISTORY_WINDOWS_US.size(); ++index)
    {
        const auto& rolling = report.rolling[index];
        const uint64_t windowUs = HISTORY_WINDOWS_US[index];
        if (rolling.windowUs != windowUs)
        {
            return "primary polling rolling windows are out of order";
        }
        if (rolling.historyCoverageUs != static_cast<double>(windowUs))
        {
            return "primary polling rolling history is not fully covered";
        }
        if (rolling.mpduRetries > rolling.mpduAttempts)
        {
            return "primary polling retries exceed attempts";
        }
        if (rolling.mpduAttempts == 0)
        {
            if (rolling.mpduRetryRatio)
            {
                return "zero-attempt primary polling retry ratio is present";
            }
        }
        else
        {
            const double expected =
                static_cast<double>(rolling.mpduRetries) / rolling.mpduAttempts;
            if (!rolling.mpduRetryRatio || !std::isfinite(*rolling.mpduRetryRatio) ||
                *rolling.mpduRetryRatio != expected)
            {
                return "primary polling retry ratio differs from exact counters";
            }
        }

        const std::array<std::optional<double>, 4> acknowledgementLatencies{
            rolling.mpduQueueToAckMeanUs,
            rolling.mpduQueueToAckP95Us,
            rolling.mpduFirstAttemptToAckMeanUs,
            rolling.mpduFirstAttemptToAckP95Us,
        };
        const bool allAckLatenciesMissing =
            std::all_of(acknowledgementLatencies.begin(),
                        acknowledgementLatencies.end(),
                        [](const auto& value) { return !value; });
        if (allAckLatenciesMissing != (rolling.mpduPositiveAcks == 0))
        {
            return "primary polling ACK-latency missingness differs from ACK count";
        }
        if (rolling.mpduPositiveAcks > 0 &&
            !std::all_of(acknowledgementLatencies.begin(),
                         acknowledgementLatencies.end(),
                         IsFiniteNonnegativeFloat32))
        {
            return "primary polling ACK latency is missing or outside float32";
        }

        const std::array<std::optional<double>, 5> fractions{
            rolling.phyTxFraction,
            rolling.phyRxFraction,
            rolling.phyBusyFraction,
            rolling.phyIdleFraction,
            rolling.phyOtherFraction,
        };
        double sum = 0.0;
        for (const auto& fraction : fractions)
        {
            if (!fraction || !std::isfinite(*fraction) || *fraction < 0.0 ||
                *fraction > 1.0)
            {
                return "primary polling PHY fraction is missing or outside [0, 1]";
            }
            sum += *fraction;
        }
        if (std::abs(sum - 1.0) > PHY_FRACTION_SUM_TOLERANCE)
        {
            return "primary polling PHY fractions do not sum to one";
        }
    }
    return std::nullopt;
}

std::optional<std::string>
TemporalT2ValuePredictor::FindPrimaryError(const PredictionSample& sample) const
{
    if (sample.telemetrySchemaVersion != PREDICTION_TELEMETRY_SCHEMA_VERSION)
    {
        return "primary sample telemetry schema differs";
    }
    if (PREDICTION_POLLING_SCHEMA_VERSION != 1 || FEATURE_SUPPORT_MASK_VERSION != 2)
    {
        return "compiled polling or support-mask schema differs";
    }
    if (sample.runId.empty())
    {
        return "primary sample run ID is empty";
    }
    if (sample.key.pathId != PRIMARY_PATH_ID || sample.key.copyId != PRIMARY_COPY_ID)
    {
        return "sample is not primary path 1/copy 0";
    }
    if (sample.sampleStage != "T2" || sample.sampleOffsetUs != T2_OFFSET_US)
    {
        return "primary sample is not the frozen T2 endpoint";
    }
    if (sample.generationTimeNs >
        std::numeric_limits<uint64_t>::max() - T2_OFFSET_US * NANOS_PER_MICROSECOND)
    {
        return "primary sample timestamp calculation overflows";
    }
    if (sample.sampleTimeNs !=
        sample.generationTimeNs + T2_OFFSET_US * NANOS_PER_MICROSECOND)
    {
        return "primary sample time differs from generation plus T2 offset";
    }
    if (sample.deadlineTimeNs <= sample.sampleTimeNs)
    {
        return "primary sample is at or after its deadline";
    }
    const uint64_t slackNs = sample.deadlineTimeNs - sample.sampleTimeNs;
    if (slackNs % NANOS_PER_MICROSECOND != 0 ||
        sample.deadlineSlackUs != slackNs / NANOS_PER_MICROSECOND)
    {
        return "primary sample deadline slack differs";
    }
    if (sample.frameAgeUs != T2_OFFSET_US)
    {
        return "primary sample frame age differs from T2 offset";
    }
    if (sample.frameSizeBytes == 0 || sample.framePacketCount == 0)
    {
        return "primary sample frame metadata is empty";
    }
    if (sample.frameType != FrameType::I_FRAME && sample.frameType != FrameType::P_FRAME)
    {
        return "primary sample frame type is unsupported";
    }
    if (sample.packetsSubmitted > sample.framePacketCount ||
        sample.packetsRemainingToSubmit > sample.framePacketCount ||
        static_cast<uint64_t>(sample.packetsSubmitted) + sample.packetsRemainingToSubmit !=
            sample.framePacketCount)
    {
        return "primary sample application packet progress does not conserve the frame";
    }
    if (sample.senderMacComplete &&
        (sample.packetsSubmitted != sample.framePacketCount ||
         sample.packetsRemainingToSubmit != 0))
    {
        return "MAC-complete primary sample retains unsubmitted packets";
    }
    if (sample.actionable == sample.senderMacComplete)
    {
        return "primary sample actionability differs from MAC completion";
    }
    if (!HasConsistentWatermark(sample.latestFeatureEventTimeNs,
                                sample.latestFeatureEventSequence))
    {
        return "primary sample watermark has inconsistent absence and sequence";
    }
    if (sample.latestFeatureEventTimeNs &&
        *sample.latestFeatureEventTimeNs > sample.sampleTimeNs)
    {
        return "primary sample watermark is in the future";
    }
    const auto sampleSupportMask = ParseHexMask(sample.featureSupportMask);
    if (!sampleSupportMask || *sampleSupportMask != REQUIRED_SUPPORT_MASK)
    {
        return "primary sample support mask differs from the frozen exact mask";
    }
    if (!sample.pollingReport)
    {
        return "primary sample has no available polling report";
    }
    if (const auto error = FindReportError(*sample.pollingReport, sample.sampleTimeNs))
    {
        return error;
    }

    if (!m_lastFrameId)
    {
        if (sample.key.frameId != 0)
        {
            return "primary history does not begin at frame zero";
        }
        return std::nullopt;
    }
    if (*m_lastFrameId == std::numeric_limits<uint64_t>::max() ||
        sample.key.frameId != *m_lastFrameId + 1)
    {
        return "primary frame identifiers are not contiguous";
    }
    if (sample.runId != m_runId)
    {
        return "primary sample run ID changed within one history";
    }
    const auto* prior = FindSlot(*m_lastFrameId);
    if (!prior)
    {
        return "primary history lost its preceding exact frame";
    }
    if (sample.sampleTimeNs <= prior->sample.sampleTimeNs ||
        sample.pollingReport->captureTimeNs <= prior->report.captureTimeNs)
    {
        return "primary sample or polling capture time did not increase";
    }
    if (sample.latestFeatureEventSequence < prior->sample.latestFeatureEventSequence ||
        !IsNondecreasing(prior->sample.latestFeatureEventTimeNs,
                         sample.latestFeatureEventTimeNs))
    {
        return "primary sample watermark decreased";
    }
    if (sample.pollingReport->latestFeatureEventSequence <
            prior->report.latestFeatureEventSequence ||
        !IsNondecreasing(prior->report.latestFeatureEventTimeNs,
                         sample.pollingReport->latestFeatureEventTimeNs))
    {
        return "primary polling watermark decreased";
    }
    const auto priorCounters = GetCounters(prior->report);
    const auto currentCounters = GetCounters(*sample.pollingReport);
    for (std::size_t index = 0; index < currentCounters.size(); ++index)
    {
        if (currentCounters[index] < priorCounters[index])
        {
            return "primary polling cumulative counter decreased";
        }
    }
    for (const uint64_t lag : EXACT_LAGS)
    {
        if (sample.key.frameId >= lag && !FindSlot(sample.key.frameId - lag))
        {
            return "primary history lacks an exact required lag frame";
        }
    }
    return std::nullopt;
}

const TemporalT2ValuePredictor::HistorySlot*
TemporalT2ValuePredictor::FindSlot(uint64_t frameId) const
{
    const auto& slot = m_history[frameId % HISTORY_SLOT_COUNT];
    return slot && slot->frameId == frameId ? &*slot : nullptr;
}

TemporalT2ValuePredictor::HistoryEvidence
TemporalT2ValuePredictor::ObservePrimary(const PredictionSample& sample)
{
    ValidateModelContract();
    const auto error = FindPrimaryError(sample);
    NS_ABORT_MSG_IF(error, "Invalid temporal T2 primary endpoint: " << *error);

    HistorySlot stored;
    stored.frameId = sample.key.frameId;
    stored.sample = sample;
    stored.sample.pollingReport.reset();
    stored.report = *sample.pollingReport;
    m_history[sample.key.frameId % HISTORY_SLOT_COUNT] = std::move(stored);
    m_lastFrameId = sample.key.frameId;
    if (sample.key.frameId == 0)
    {
        m_runId = sample.runId;
    }

    const auto* current = FindSlot(sample.key.frameId);
    NS_ABORT_MSG_IF(!current, "Temporal T2 current report was not stored exactly");
    HistoryEvidence evidence;
    evidence.ready = sample.key.frameId >= EXACT_LAGS.back();
    evidence.currentPollCaptureTimeNs = current->report.captureTimeNs;
    evidence.currentPollAvailableTimeNs = current->report.availableTimeNs;
    for (std::size_t index = 0; index < EXACT_LAGS.size(); ++index)
    {
        evidence.lags[index].lagFrames = EXACT_LAGS[index];
        const uint64_t lag = EXACT_LAGS[index];
        if (sample.key.frameId >= lag)
        {
            const auto* lagged = FindSlot(sample.key.frameId - lag);
            NS_ABORT_MSG_IF(!lagged, "Temporal T2 exact lag disappeared after storage");
            evidence.lags[index].frameId = lagged->frameId;
            evidence.lags[index].pollCaptureTimeNs = lagged->report.captureTimeNs;
        }
    }
    return evidence;
}

TemporalT2ValuePredictor::FeatureArray
TemporalT2ValuePredictor::BuildFeatures(
    const PredictionSample& current,
    const PredictionPollingReport& currentReport,
    const std::array<LaggedReportInput, LAG_COUNT>& lagged)
{
    FeatureArray values;
    values.fill(Missing());
    FeatureWriter writer(values);

    const auto appendCurrentRolling = [&writer](const PredictionRollingSample& rolling) {
        writer.Add(static_cast<double>(rolling.mpduAttempts));
        writer.Add(static_cast<double>(rolling.mpduPositiveAcks));
        writer.Add(static_cast<double>(rolling.mpduAttemptFailures));
        writer.Add(static_cast<double>(rolling.mpduRetries));
        writer.AddOptional(rolling.mpduRetryRatio);
        writer.Add(static_cast<double>(rolling.acknowledgedMacServiceBytes));
        writer.AddOptional(rolling.mpduQueueToAckMeanUs);
        writer.AddOptional(rolling.mpduQueueToAckP95Us);
        writer.AddOptional(rolling.mpduFirstAttemptToAckMeanUs);
        writer.AddOptional(rolling.mpduFirstAttemptToAckP95Us);
        writer.AddOptional(rolling.phyTxFraction);
        writer.AddOptional(rolling.phyRxFraction);
        writer.AddOptional(rolling.phyBusyFraction);
        writer.AddOptional(rolling.phyIdleFraction);
        writer.AddOptional(rolling.phyOtherFraction);
    };
    const auto appendValueMissing = [&writer](const auto& value) {
        writer.AddOptional(value);
        writer.Add(value ? 0.0 : 1.0);
    };
    const auto appendAgeMissing = [&writer](uint64_t captureTimeNs,
                                            const std::optional<uint64_t>& eventTimeNs) {
        if (!eventTimeNs)
        {
            writer.Add(Missing());
            writer.Add(1.0);
            return;
        }
        if (*eventTimeNs > captureTimeNs)
        {
            throw std::invalid_argument("temporal T2 radio age references a future event");
        }
        const double ageUs = std::min(
            static_cast<double>(captureTimeNs - *eventTimeNs) / NANOS_PER_MICROSECOND,
            LAST_EVENT_AGE_CAP_US);
        writer.Add(ageUs);
        writer.Add(0.0);
    };
    const auto appendRadio = [&appendValueMissing,
                              &appendAgeMissing](const PredictionPollingReport& report) {
        appendValueMissing(report.currentMcs);
        appendValueMissing(report.currentNss);
        appendValueMissing(report.currentChannelWidthMhz);
        appendValueMissing(report.currentGuardIntervalNs);
        appendAgeMissing(report.captureTimeNs, report.lastTxAttemptTimeNs);
        appendAgeMissing(report.captureTimeNs, report.lastPositiveAckTimeNs);
    };

    writer.Add(static_cast<double>(current.frameAgeUs));
    writer.Add(static_cast<double>(current.deadlineSlackUs));
    writer.Add(static_cast<double>(current.frameSizeBytes));
    writer.Add(static_cast<double>(current.framePacketCount));
    writer.Add(static_cast<double>(current.packetsSubmitted));
    writer.Add(static_cast<double>(current.applicationSocketPacketBytesSubmitted));
    writer.Add(static_cast<double>(current.packetsRemainingToSubmit));
    writer.AddOptional(current.framePacketsMacEnqueued);
    writer.AddOptional(current.framePacketsMacDequeued);
    writer.AddOptional(current.framePacketsTxSucceeded);
    writer.AddOptional(current.frameMpduAttemptFailures);
    writer.AddOptional(current.framePacketsTerminallyDropped);
    writer.AddOptional(current.framePacketsCurrentlyQueued);
    writer.AddOptional(current.frameMacServiceBytesCurrentlyQueued);
    writer.AddOptional(current.macQueuePackets);
    writer.AddOptional(current.macQueueServiceBytes);
    writer.AddOptional(current.packetsAheadOfFrame);
    writer.AddOptional(current.macServiceBytesAheadOfFrame);
    writer.AddOptional(current.framePacketsPendingPrimary);
    writer.AddOptional(current.frameMacServiceBytesNotAcknowledged);
    writer.AddOptional(current.frameMacServiceBytesPendingPrimary);

    if (currentReport.rolling.size() != HISTORY_WINDOWS_US.size())
    {
        throw std::invalid_argument("temporal T2 current report has the wrong window count");
    }
    for (std::size_t index = 0; index < HISTORY_WINDOWS_US.size(); ++index)
    {
        if (currentReport.rolling[index].windowUs != HISTORY_WINDOWS_US[index])
        {
            throw std::invalid_argument("temporal T2 current rolling-window order differs");
        }
        appendCurrentRolling(currentReport.rolling[index]);
    }
    writer.Add(current.frameType == FrameType::I_FRAME ? 1.0 : 0.0);
    writer.Add(current.frameType == FrameType::P_FRAME ? 1.0 : 0.0);
    if (writer.Size() != 68)
    {
        throw std::logic_error("temporal T2 primary-base feature width differs");
    }

    // The compact formulas consume the already float32-quantized and widened
    // primary-base values, exactly as the training adapter does.
    const double rawSlack = values[1];
    const double slack = std::isnan(rawSlack) ? Missing() : std::max(rawSlack, 1.0);
    const double pendingBytes = values[20];
    const double aheadBytes = values[17];
    const std::array<double, 3> acknowledgedBytes{values[26], values[41], values[56]};
    const std::array<double, 3> coverageUs{1000.0, 5000.0, 20000.0};
    std::array<double, 3> rates;
    for (std::size_t index = 0; index < rates.size(); ++index)
    {
        rates[index] = acknowledgedBytes[index] / coverageUs[index];
    }
    const double requiredRate = pendingBytes / slack;
    writer.Add(rates[0] - requiredRate);
    writer.Add(rates[1] - requiredRate);
    writer.Add(rates[2] - requiredRate);

    double clearance = Missing();
    if (std::isfinite(aheadBytes) && std::isfinite(acknowledgedBytes[1]))
    {
        if (aheadBytes <= 0.0)
        {
            clearance = 0.0;
        }
        else if (acknowledgedBytes[1] > 0.0)
        {
            clearance = std::min(aheadBytes * 5000.0 / acknowledgedBytes[1],
                                 LAST_EVENT_AGE_CAP_US);
        }
        else
        {
            clearance = LAST_EVENT_AGE_CAP_US;
        }
    }
    writer.Add(clearance);
    writer.Add(std::isfinite(clearance) && std::isfinite(slack) && slack > 0.0
                   ? std::clamp(clearance / slack, -100.0, 100.0)
                   : Missing());
    writer.Add(rates[0] - rates[2]);
    writer.Add(values[33] - values[63]);
    if (writer.Size() != 75)
    {
        throw std::logic_error("temporal T2 compact-physics feature width differs");
    }

    appendRadio(currentReport);
    if (writer.Size() != 87)
    {
        throw std::logic_error("temporal T2 current-radio feature width differs");
    }
    if (!HasAllCounters(currentReport))
    {
        throw std::invalid_argument("temporal T2 current report lacks cumulative counters");
    }

    for (std::size_t lagIndex = 0; lagIndex < lagged.size(); ++lagIndex)
    {
        const auto& input = lagged[lagIndex];
        const uint64_t expectedLag = EXACT_LAGS[lagIndex];
        if (input.lagFrames != expectedLag || !input.report ||
            current.key.frameId < expectedLag ||
            input.sourceFrameId != current.key.frameId - expectedLag)
        {
            throw std::invalid_argument("temporal T2 exact lag identity differs");
        }
        const auto& report = *input.report;
        if (report.rolling.size() != HISTORY_WINDOWS_US.size())
        {
            throw std::invalid_argument("temporal T2 lag report has the wrong window count");
        }
        for (std::size_t windowIndex = 0; windowIndex < HISTORY_WINDOWS_US.size();
             ++windowIndex)
        {
            const auto& rolling = report.rolling[windowIndex];
            if (rolling.windowUs != HISTORY_WINDOWS_US[windowIndex])
            {
                throw std::invalid_argument("temporal T2 lag rolling-window order differs");
            }
            writer.Add(static_cast<double>(rolling.mpduAttempts));
            writer.Add(static_cast<double>(rolling.mpduPositiveAcks));
            writer.Add(static_cast<double>(rolling.mpduAttemptFailures));
            writer.Add(static_cast<double>(rolling.mpduRetries));
            writer.Add(static_cast<double>(rolling.acknowledgedMacServiceBytes));
            writer.AddOptional(rolling.phyTxFraction);
            writer.AddOptional(rolling.phyRxFraction);
            writer.AddOptional(rolling.phyBusyFraction);
            writer.AddOptional(rolling.phyIdleFraction);
            writer.AddOptional(rolling.phyOtherFraction);
            if (rolling.mpduAttempts == 0)
            {
                writer.Add(0.0);
                writer.Add(1.0);
            }
            else
            {
                writer.Add(static_cast<double>(rolling.mpduRetries) /
                           rolling.mpduAttempts);
                writer.Add(0.0);
            }
        }
        appendRadio(report);
        if (!HasAllCounters(report))
        {
            throw std::invalid_argument("temporal T2 lag report lacks cumulative counters");
        }
        if (report.captureTimeNs >= currentReport.captureTimeNs)
        {
            throw std::invalid_argument("temporal T2 exact lag capture is not earlier");
        }
        const uint64_t spanNs = currentReport.captureTimeNs - report.captureTimeNs;
        const double spanMs = static_cast<double>(spanNs) / 1000000.0;
        const auto currentCounters = GetCounters(currentReport);
        const auto lagCounters = GetCounters(report);
        constexpr std::array<std::size_t, 5> RATE_COUNTER_INDICES{0, 1, 2, 3, 8};
        for (const std::size_t counterIndex : RATE_COUNTER_INDICES)
        {
            if (currentCounters[counterIndex] < lagCounters[counterIndex])
            {
                throw std::invalid_argument("temporal T2 cumulative counter reset at a lag");
            }
            const uint64_t delta = currentCounters[counterIndex] - lagCounters[counterIndex];
            writer.Add(static_cast<double>(delta) / spanMs);
        }
        const std::size_t expectedSize = 87 + (lagIndex + 1) * 53;
        if (writer.Size() != expectedSize)
        {
            throw std::logic_error("temporal T2 per-lag feature width differs");
        }
    }
    if (writer.Size() != FEATURE_COUNT)
    {
        throw std::logic_error("temporal T2 feature adapter width differs");
    }
    return values;
}

TemporalT2ValuePredictor::FeatureArray
TemporalT2ValuePredictor::BuildStoredFeatures(uint64_t frameId) const
{
    if (!m_lastFrameId || frameId != *m_lastFrameId)
    {
        throw std::invalid_argument("temporal T2 evaluation is not for the latest stored frame");
    }
    const auto* currentSlot = FindSlot(frameId);
    if (!currentSlot)
    {
        throw std::logic_error("temporal T2 latest owned sample is missing");
    }
    if (frameId < EXACT_LAGS.back())
    {
        throw std::invalid_argument("temporal T2 evaluation lacks exact lag-8 history");
    }
    std::array<LaggedReportInput, LAG_COUNT> lagged;
    for (std::size_t index = 0; index < EXACT_LAGS.size(); ++index)
    {
        const uint64_t lag = EXACT_LAGS[index];
        const auto* slot = FindSlot(frameId - lag);
        if (!slot)
        {
            throw std::logic_error("temporal T2 stored exact lag is missing");
        }
        lagged[index] = {lag, slot->frameId, &slot->report};
    }
    return BuildFeatures(currentSlot->sample, currentSlot->report, lagged);
}

TemporalT2ValueModelResult
TemporalT2ValuePredictor::Evaluate(uint64_t frameId) const
{
    ValidateModelContract();
    const auto result = TemporalT2ValueModelEvaluator::Evaluate(BuildStoredFeatures(frameId));
    const std::array<double, 7> diagnostics{
        result.primaryBad12Logit,
        result.primaryBad12Probability,
        result.treatedBad12Logit,
        result.treatedBad12Probability,
        result.predictedLogAirtime,
        result.predictedSecondaryAirtimeUs,
        result.nonnegativeBad12Value,
    };
    NS_ABORT_MSG_IF(!std::all_of(diagnostics.begin(), diagnostics.end(), [](double value) {
                        return std::isfinite(value);
                    }) ||
                        !std::isfinite(result.valuePerCostScore),
                    "Temporal T2 model returned a nonfinite diagnostic");
    return result;
}

bool
TemporalT2ValuePredictor::PassesFrameGate(FrameType frameType)
{
    return frameType == FrameType::P_FRAME;
}

std::span<const std::string_view>
TemporalT2ValuePredictor::GetFeatureNames()
{
    return g_featureNames;
}

bool
TemporalT2ValuePredictor::HasExactModelContract()
{
    if (PREDICTION_TELEMETRY_SCHEMA_VERSION != 3 || PREDICTION_POLLING_SCHEMA_VERSION != 1 ||
        FEATURE_SUPPORT_MASK_VERSION != 2 ||
        TemporalT2ValueModelEvaluator::GetFeatureFamily() !=
            "primary_compact_physics_temporal" ||
        TemporalT2ValueModelEvaluator::GetFeatureAdapter() !=
            "finite_numeric_float32_then_float64_one_hot_v1" ||
        TemporalT2ValueModelEvaluator::GetRanker() != "legacy_bad12_value_per_cost" ||
        TemporalT2ValueModelEvaluator::GetFrameGate() != "p_frames_only" ||
        TemporalT2ValueModelEvaluator::GetScoreAdapter() !=
            "final_candidate_float32_threshold_ge_v1" ||
        std::bit_cast<uint32_t>(TemporalT2ValueModelEvaluator::GetScoreThreshold()) !=
            0x38bbc0e5U)
    {
        return false;
    }
    const auto modelNames = TemporalT2ValueModelEvaluator::GetFeatureNames();
    if (modelNames.size() != g_featureNames.size() ||
        !std::equal(g_featureNames.begin(), g_featureNames.end(), modelNames.begin()))
    {
        return false;
    }
    if (std::any_of(g_featureNames.begin(), g_featureNames.end(), [](std::string_view name) {
            return name.find("secondary") != std::string_view::npos;
        }))
    {
        return false;
    }

    const auto& provenance = TemporalT2ValueModelEvaluator::GetProvenance();
    return provenance.evidenceStatus ==
               "previously_opened_run_group_test_engineering_evidence_not_confirmation" &&
           provenance.featureContractId ==
               "randomized_intervention_t2_temporal_action_clean_v1" &&
           provenance.modelSpecId == "hgb64_depth3_7leaf_two_head_ridge_log_cost_v1" &&
           provenance.selectionId == "calibration_two_objective_50pct_maximin_v1" &&
           provenance.trainingGitCommit ==
               "9b9ee02edc0b289b0ba4187c3f5567087c1d977f" &&
           provenance.sourceModelSha256 ==
               "dff01b0f8319320489709c4039d97011f35439aa92adedbe167fe61b9de7bcb8" &&
           provenance.sourceMetricsSha256 ==
               "35929f0638b03ec79f2f3967dd947265c3d73b7fa51f487299cc1d96a555a014" &&
           provenance.sourceManifestSha256 ==
               "b3af02b647c7671a631f3d43ebece75781989889358c845335d4003610a8208f" &&
           provenance.frozenSelectionSha256 ==
               "c7f886a4ca1a29b9fbd2e25d19d78f994d7136ecdea4f6a16db77eacacf5ce9f" &&
           provenance.datasetManifestSha256 ==
               "87d630a66f460b46a31245f56da2a8110091c42dc5fd499416e2d82d697d0314" &&
           provenance.datasetMetadataSha256 ==
               "03a3fc35dac1afa126653703855f243a70cb40d93f713002e7ae0b9d7cea20e8" &&
           provenance.datasetCsvSha256 ==
               "9376face6806929318c92e74fc2c47da740e187c7b0570910a37acdd1f3be0bc" &&
           provenance.trainerSha256 ==
               "ffe024b88dd7b70bab34873ac59ba7abb748db5af564be8526fb205ec94ddfa9" &&
           provenance.exporterSha256 ==
               "0558b1d671bbb836eb6d50db843ce6430fd1f501b7046587e37f0de5c38837fb" &&
           provenance.plainModelSha256 ==
               "e0da9390d9d8c30975ca90d4d0d33b7f64b865ed0b35087c223a9ee8341280ee" &&
           provenance.featureContractSha256 ==
               "caedef73eddbdca3f855f2fa6ad538da9270f5c5c9b9467f64a35aa96da3d186" &&
           provenance.selectedPolicySha256 ==
               "00ad2f3063983b87564112cdff3c8abf241f9ee22656b31395c12871bde2d8d2" &&
           provenance.primaryHeadSha256 ==
               "80a03574fe478fd00868044c67644bda44364987b4eba84625827aef86a072b2" &&
           provenance.treatedHeadSha256 ==
               "0251eee3c5e02d346a0571e1d9d56812bdc8697883ab516a83929e91298f987f" &&
           provenance.costModelSha256 ==
               "e41e279d4abbe966fadbf583b70a9ba26a2c14207628ce855a15f9d282d85c6d";
}

void
TemporalT2ValuePredictor::ValidateModelContract()
{
    static const bool exact = HasExactModelContract();
    NS_ABORT_MSG_IF(!exact, "Compiled temporal T2 predictor contract differs");
}

} // namespace ns3
