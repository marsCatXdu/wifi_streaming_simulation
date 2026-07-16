/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "experiment-output.h"

#include "ns3/abort.h"

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <numeric>
#include <sstream>

namespace ns3
{

namespace
{

std::string
JsonEscape(const std::string& value)
{
    std::ostringstream output;
    for (const unsigned char character : value)
    {
        switch (character)
        {
        case '"':
            output << "\\\"";
            break;
        case '\\':
            output << "\\\\";
            break;
        case '\b':
            output << "\\b";
            break;
        case '\f':
            output << "\\f";
            break;
        case '\n':
            output << "\\n";
            break;
        case '\r':
            output << "\\r";
            break;
        case '\t':
            output << "\\t";
            break;
        default:
            if (character < 0x20)
            {
                output << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                       << static_cast<unsigned>(character) << std::dec;
            }
            else
            {
                output << character;
            }
        }
    }
    return output.str();
}

std::ofstream
OpenOutput(const std::string& outputDir, const std::string& fileName)
{
    const auto path = std::filesystem::path(outputDir) / fileName;
    std::ofstream output(path, std::ios::out | std::ios::trunc);
    NS_ABORT_MSG_IF(!output, "Cannot open output file " << path.string());
    output << std::setprecision(12);
    return output;
}

template <typename T>
void
WriteCsvOptional(std::ostream& output, const std::optional<T>& value)
{
    if (value)
    {
        output << *value;
    }
}

void
WriteJsonOptional(std::ostream& output, const std::optional<double>& value)
{
    if (value)
    {
        output << *value;
    }
    else
    {
        output << "null";
    }
}

} // namespace

double
ExperimentOutput::Percentile(std::vector<double> values, double quantile)
{
    NS_ABORT_MSG_IF(values.empty(), "Cannot compute a percentile of an empty sample");
    NS_ABORT_MSG_IF(quantile < 0 || quantile > 1, "Percentile quantile must be in [0, 1]");
    std::sort(values.begin(), values.end());
    const double index = quantile * (values.size() - 1);
    const auto lower = static_cast<std::size_t>(std::floor(index));
    const auto upper = static_cast<std::size_t>(std::ceil(index));
    if (lower == upper)
    {
        return values[lower];
    }
    const double weight = index - lower;
    return values[lower] * (1 - weight) + values[upper] * weight;
}

StreamingRunSummary
ExperimentOutput::ComputeSummary(const std::vector<FrameResult>& frames,
                                 double measurementDurationSeconds,
                                 uint64_t applicationBytesSent,
                                 uint64_t redundantBytesSent,
                                 const std::vector<LinkIntervalRecord>& links)
{
    NS_ABORT_MSG_IF(measurementDurationSeconds <= 0, "Measurement duration must be positive");
    StreamingRunSummary summary;
    summary.frameCount = frames.size();
    summary.applicationBytesSent = applicationBytesSent;
    summary.redundantBytesSent = redundantBytesSent;
    std::vector<double> latencies;
    for (const auto& frame : frames)
    {
        if (frame.incomplete)
        {
            ++summary.incompleteFrameCount;
        }
        else
        {
            ++summary.completeFrameCount;
            summary.applicationBytesDelivered += frame.frame.frameSizeBytes;
            if (frame.unionCompletionUs)
            {
                latencies.push_back(*frame.unionCompletionUs -
                                    frame.frame.generationTimeNs / 1000.0);
            }
        }
        summary.deadlineMissCount += frame.deadlineMiss;
        if (frame.duplicated)
        {
            ++summary.duplicateFrameCount;
            if (frame.unionCompletionUs &&
                (!frame.copy0CompletionUs ||
                 *frame.unionCompletionUs < *frame.copy0CompletionUs))
            {
                ++summary.duplicateRecoveryCount;
            }
            if (frame.unionCompletionUs && frame.copy0CompletionUs &&
                *frame.unionCompletionUs == *frame.copy0CompletionUs)
            {
                ++summary.duplicateNoBenefitCount;
            }
        }
    }
    if (summary.frameCount > 0)
    {
        summary.completeRatio =
            static_cast<double>(summary.completeFrameCount) / summary.frameCount;
        summary.incompleteRatio =
            static_cast<double>(summary.incompleteFrameCount) / summary.frameCount;
        summary.deadlineMissRatio =
            static_cast<double>(summary.deadlineMissCount) / summary.frameCount;
    }
    if (!latencies.empty())
    {
        summary.latencyP50Us = Percentile(latencies, 0.50);
        summary.latencyP90Us = Percentile(latencies, 0.90);
        summary.latencyP95Us = Percentile(latencies, 0.95);
        summary.latencyP99Us = Percentile(latencies, 0.99);
        // This leaves at least about 100 observations above the requested quantile.
        if (latencies.size() >= 100000)
        {
            summary.latencyP999Us = Percentile(latencies, 0.999);
        }
    }
    summary.applicationGoodputMbps =
        summary.applicationBytesDelivered * 8.0 / measurementDurationSeconds / 1e6;
    if (applicationBytesSent > 0)
    {
        summary.redundantByteRatio =
            static_cast<double>(redundantBytesSent) / applicationBytesSent;
    }
    if (summary.duplicateFrameCount > 0)
    {
        summary.duplicateRecoveryRate =
            static_cast<double>(summary.duplicateRecoveryCount) / summary.duplicateFrameCount;
        summary.duplicateNoBenefitRatio =
            static_cast<double>(summary.duplicateNoBenefitCount) / summary.duplicateFrameCount;
    }
    for (const auto& link : links)
    {
        summary.successfulMpdus += link.successfulMpdus;
        summary.failedMpdus += link.failedMpdus;
        summary.retransmissions += link.retransmissions;
        summary.phyTxTimeUs += link.phyTxTimeUs;
        summary.phyRxTimeUs += link.phyRxTimeUs;
        summary.phyCcaBusyTimeUs += link.phyCcaBusyTimeUs;
    }
    return summary;
}

void
ExperimentOutput::PrepareRunDirectory(const std::string& outputDir)
{
    NS_ABORT_MSG_IF(outputDir.empty(), "--outputDir is required");
    std::error_code error;
    const std::filesystem::path path(outputDir);
    if (std::filesystem::exists(path, error))
    {
        NS_ABORT_MSG_IF(error || !std::filesystem::is_directory(path),
                        "Output path is not a directory: " << outputDir);
        NS_ABORT_MSG_IF(!std::filesystem::is_empty(path),
                        "Output directory must be empty: " << outputDir);
        return;
    }
    NS_ABORT_MSG_IF(!std::filesystem::create_directories(path, error) || error,
                    "Cannot create output directory " << outputDir << ": " << error.message());
}

void
ExperimentOutput::WriteResolvedConfig(const std::string& outputDir,
                                      const StreamingRunConfig& config)
{
    auto output = OpenOutput(outputDir, "resolved_config.json");
    output << "{\n"
           << "  \"run_id\": \"" << JsonEscape(config.runId) << "\",\n"
           << "  \"topology\": \"" << JsonEscape(config.topology) << "\",\n"
           << "  \"policy\": \"" << JsonEscape(config.policy) << "\",\n"
           << "  \"duration_s\": " << config.durationSeconds << ",\n"
           << "  \"warmup_s\": " << config.warmupSeconds << ",\n"
           << "  \"measurement_start_s\": " << config.warmupSeconds << ",\n"
           << "  \"measurement_stop_s\": " << config.warmupSeconds + config.durationSeconds
           << ",\n"
           << "  \"stream\": {\n"
           << "    \"fps\": " << config.fps << ",\n"
           << "    \"frame_size_bytes\": " << config.frameSizeBytes << ",\n"
           << "    \"payload_size_bytes\": " << config.payloadSizeBytes << ",\n"
           << "    \"deadline_us\": " << config.deadlineUs << ",\n"
           << "    \"emission_mode\": \"" << JsonEscape(config.emissionMode) << "\"\n"
           << "  },\n"
           << "  \"propagation\": {\"model\": \"fixed_rss\", \"rss_dbm\": "
           << config.fixedRssDbm << "},\n"
           << "  \"wifi\": {\n"
           << "    \"standard\": \"" << JsonEscape(config.standard) << "\",\n"
           << "    \"station_manager\": \"ConstantRateWifiManager\",\n"
           << "    \"data_mode\": \"" << JsonEscape(config.dataMode) << "\",\n"
           << "    \"control_mode\": \"" << JsonEscape(config.controlMode) << "\",\n"
           << "    \"guard_interval\": \"" << JsonEscape(config.guardInterval) << "\",\n"
           << "    \"channel_settings\": [";
    for (std::size_t i = 0; i < config.channelSettings.size(); ++i)
    {
        output << (i == 0 ? "" : ", ") << '"' << JsonEscape(config.channelSettings[i]) << '"';
    }
    output << "],\n"
           << "    \"queue_max_packets\": " << config.queueMaxPackets << ",\n"
           << "    \"queue_max_delay_ms\": " << config.queueMaxDelayMs << ",\n"
           << "    \"max_ampdu_size_bytes\": " << config.maxAmpduSizeBytes << ",\n"
           << "    \"max_amsdu_size_bytes\": " << config.maxAmsduSizeBytes << ",\n"
           << "    \"block_ack_enabled\": " << std::boolalpha << config.blockAckEnabled << ",\n"
           << "    \"frame_retry_limit\": " << config.frameRetryLimit << ",\n"
           << "    \"rts_cts_threshold_bytes\": " << config.rtsCtsThresholdBytes << ",\n"
           << "    \"fragmentation_threshold_bytes\": "
           << config.fragmentationThresholdBytes << ",\n"
           << "    \"access_category\": \"" << JsonEscape(config.accessCategory) << "\",\n"
           << "    \"txop_limit_us\": " << config.txopLimitUs << "\n"
           << "  },\n"
           << "  \"policy_settings\": {\"static_link_0_score\": " << config.staticLink0Score
           << ", \"static_link_1_score\": " << config.staticLink1Score << "},\n"
           << "  \"packet_event_logs_enabled\": " << config.packetEventLogsEnabled << "\n"
           << "}\n";
}

void
ExperimentOutput::WriteBuildInfo(const std::string& outputDir, const StreamingBuildInfo& info)
{
    auto output = OpenOutput(outputDir, "build_info.json");
    output << "{\n"
           << "  \"ns3_version\": \"" << JsonEscape(info.ns3Version) << "\",\n"
           << "  \"ns3_upstream_commit\": \"" << JsonEscape(info.ns3UpstreamCommit) << "\",\n"
           << "  \"project_git_commit\": \"" << JsonEscape(info.projectGitCommit) << "\",\n"
           << "  \"compiler\": \"" << JsonEscape(info.compiler) << "\",\n"
           << "  \"build_profile\": \"" << JsonEscape(info.buildProfile) << "\",\n"
           << "  \"execution_timestamp_utc\": \"" << JsonEscape(info.executionTimestampUtc)
           << "\",\n"
           << "  \"host\": \"" << JsonEscape(info.host) << "\"\n"
           << "}\n";
}

void
ExperimentOutput::WriteLinkIntervals(const std::string& outputDir,
                                     const std::vector<LinkIntervalRecord>& records)
{
    auto output = OpenOutput(outputDir, "link_intervals.csv");
    output << "timestamp_us,link_id,application_bytes_sent,application_bytes_received,"
              "redundant_bytes,probe_bytes,successful_mpdus,failed_mpdus,retransmissions,"
              "mean_mpdu_service_time_us,p95_mpdu_service_time_us,queue_bytes,"
              "estimated_rate_mbps,phy_idle_time_us,phy_cca_busy_time_us,phy_tx_time_us,"
              "phy_rx_time_us\n";
    for (const auto& record : records)
    {
        output << record.timestampUs << ',' << +record.linkId << ','
               << record.applicationBytesSent << ',' << record.applicationBytesReceived << ','
               << record.redundantBytes << ',' << record.probeBytes << ','
               << record.successfulMpdus << ',' << record.failedMpdus << ','
               << record.retransmissions << ',';
        WriteCsvOptional(output, record.meanMpduServiceTimeUs);
        output << ',';
        WriteCsvOptional(output, record.p95MpduServiceTimeUs);
        output << ',';
        WriteCsvOptional(output, record.queueBytes);
        output << ',';
        WriteCsvOptional(output, record.estimatedRateMbps);
        output << ',' << record.phyIdleTimeUs << ',' << record.phyCcaBusyTimeUs << ','
               << record.phyTxTimeUs << ',' << record.phyRxTimeUs << '\n';
    }
}

void
ExperimentOutput::WriteMacSummary(const std::string& outputDir,
                                  const std::vector<MacSummaryRecord>& records)
{
    auto output = OpenOutput(outputDir, "mac_summary.csv");
    output << "link_id,node_id,device_id,successful_mpdus,failed_mpdus,retransmissions,"
              "retry_limit_drops,mean_mpdu_service_time_us,p95_mpdu_service_time_us\n";
    for (const auto& record : records)
    {
        output << +record.linkId << ',' << record.nodeId << ',' << record.deviceId << ','
               << record.successfulMpdus << ',' << record.failedMpdus << ','
               << record.retransmissions << ',' << record.retryLimitDrops << ',';
        WriteCsvOptional(output, record.meanMpduServiceTimeUs);
        output << ',';
        WriteCsvOptional(output, record.p95MpduServiceTimeUs);
        output << '\n';
    }
}

void
ExperimentOutput::WriteSummary(const std::string& outputDir,
                               const StreamingRunSummary& summary)
{
    auto output = OpenOutput(outputDir, "summary.json");
    output << "{\n"
           << "  \"frame_count\": " << summary.frameCount << ",\n"
           << "  \"complete_frame_count\": " << summary.completeFrameCount << ",\n"
           << "  \"incomplete_frame_count\": " << summary.incompleteFrameCount << ",\n"
           << "  \"deadline_miss_count\": " << summary.deadlineMissCount << ",\n"
           << "  \"complete_ratio\": " << summary.completeRatio << ",\n"
           << "  \"incomplete_ratio\": " << summary.incompleteRatio << ",\n"
           << "  \"deadline_miss_ratio\": " << summary.deadlineMissRatio << ",\n"
           << "  \"latency_p50_us\": ";
    WriteJsonOptional(output, summary.latencyP50Us);
    output << ",\n  \"latency_p90_us\": ";
    WriteJsonOptional(output, summary.latencyP90Us);
    output << ",\n  \"latency_p95_us\": ";
    WriteJsonOptional(output, summary.latencyP95Us);
    output << ",\n  \"latency_p99_us\": ";
    WriteJsonOptional(output, summary.latencyP99Us);
    output << ",\n  \"latency_p99_9_us\": ";
    WriteJsonOptional(output, summary.latencyP999Us);
    output << ",\n"
           << "  \"application_goodput_mbps\": " << summary.applicationGoodputMbps << ",\n"
           << "  \"application_bytes_sent\": " << summary.applicationBytesSent << ",\n"
           << "  \"application_bytes_delivered\": " << summary.applicationBytesDelivered << ",\n"
           << "  \"redundant_bytes_sent\": " << summary.redundantBytesSent << ",\n"
           << "  \"redundant_byte_ratio\": " << summary.redundantByteRatio << ",\n"
           << "  \"duplicate_frame_count\": " << summary.duplicateFrameCount << ",\n"
           << "  \"duplicate_recovery_count\": " << summary.duplicateRecoveryCount << ",\n"
           << "  \"duplicate_recovery_rate\": " << summary.duplicateRecoveryRate << ",\n"
           << "  \"duplicate_no_benefit_count\": " << summary.duplicateNoBenefitCount << ",\n"
           << "  \"duplicate_no_benefit_ratio\": " << summary.duplicateNoBenefitRatio << ",\n"
           << "  \"successful_mpdus\": " << summary.successfulMpdus << ",\n"
           << "  \"failed_mpdus\": " << summary.failedMpdus << ",\n"
           << "  \"retransmissions\": " << summary.retransmissions << ",\n"
           << "  \"phy_tx_time_us\": " << summary.phyTxTimeUs << ",\n"
           << "  \"phy_rx_time_us\": " << summary.phyRxTimeUs << ",\n"
           << "  \"phy_cca_busy_time_us\": " << summary.phyCcaBusyTimeUs << "\n"
           << "}\n";
}

} // namespace ns3
