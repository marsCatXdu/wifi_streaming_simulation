/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "experiment-output.h"
#include "closed-loop-risk-predictor.h"
#include "distributional-shadow-t2-controller.h"
#include "paired-value-t2-controller.h"
#include "prediction-model-evaluator.h"
#include "prediction-telemetry-collector.h"
#include "randomized-intervention-controller.h"

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

std::string
DecisionStageName(uint64_t offsetUs)
{
    if (offsetUs % 1000 == 0)
    {
        return "T" + std::to_string(offsetUs / 1000);
    }
    return "offset_" + std::to_string(offsetUs) + "us";
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
           << "  \"seed\": " << config.rngSeed << ",\n"
           << "  \"run\": " << config.rngRun << ",\n"
           << "  \"topology\": \"" << JsonEscape(config.topology) << "\",\n"
           << "  \"policy\": \"" << JsonEscape(config.policy) << "\",\n";
    if (config.policy == "paired_value_duplication_t2" ||
        config.policy == "distributional_shadow_duplication_t2")
    {
        const bool generalizationProfile =
            config.pairedTemporalT2FrameProfile == "environment_generalization_v1";
        output << "  \"environment\": \""
               << (generalizationProfile ? "held_out_environment_generalization_v1"
                                         : "unchanged_neutral_mixed4x4")
               << "\",\n"
               << "  \"pairedTemporalT2FrameProfile\": \""
               << JsonEscape(config.pairedTemporalT2FrameProfile) << "\",\n";
    }
    output << "  \"duration_s\": " << config.durationSeconds << ",\n"
           << "  \"warmup_s\": " << config.warmupSeconds << ",\n"
           << "  \"measurement_start_s\": " << config.warmupSeconds << ",\n"
           << "  \"measurement_stop_s\": " << config.warmupSeconds + config.durationSeconds
           << ",\n"
           << "  \"stream\": {\n"
           << "    \"source\": \"" << JsonEscape(config.source) << "\",\n"
           << "    \"trace_file\": \"" << JsonEscape(config.traceFile) << "\",\n"
           << "    \"fps\": " << config.fps << ",\n"
           << "    \"frame_size_bytes\": " << config.frameSizeBytes << ",\n"
           << "    \"gop_length\": " << config.gopLength << ",\n"
           << "    \"keyframe_size_multiplier\": " << config.keyframeSizeMultiplier << ",\n"
           << "    \"payload_size_bytes\": " << config.payloadSizeBytes << ",\n"
           << "    \"deadline_us\": " << config.deadlineUs << ",\n"
           << "    \"emission_mode\": \"" << JsonEscape(config.emissionMode) << "\"\n"
           << "  },\n"
           << "  \"propagation\": {\n"
           << "    \"model\": \"" << JsonEscape(config.propagationModel) << "\",\n"
           << "    \"rss_dbm\": " << config.fixedRssDbm << ",\n"
           << "    \"station_distance_m\": " << config.stationDistanceM << ",\n"
           << "    \"path_loss_exponent\": " << config.pathLossExponent << ",\n"
           << "    \"reference_loss_2_4_ghz_db\": " << config.referenceLoss2GhzDb << ",\n"
           << "    \"reference_loss_5_ghz_db\": " << config.referenceLoss5GhzDb << ",\n"
           << "    \"nakagami_distance_1_m\": " << config.nakagamiDistance1M << ",\n"
           << "    \"nakagami_distance_2_m\": " << config.nakagamiDistance2M << ",\n"
           << "    \"nakagami_m0\": " << config.nakagamiM0 << ",\n"
           << "    \"nakagami_m1\": " << config.nakagamiM1 << ",\n"
           << "    \"nakagami_m2\": " << config.nakagamiM2 << ",\n"
           << "    \"random_stream_base\": " << config.propagationStreamBase << "\n"
           << "  },\n"
           << "  \"wifi\": {\n"
           << "    \"standard\": \"" << JsonEscape(config.standard) << "\",\n"
           << "    \"mcs_mode\": \"" << JsonEscape(config.mcsMode) << "\",\n"
           << "    \"station_manager\": \"" << JsonEscape(config.stationManager)
           << "\",\n"
           << "    \"adaptive_mcs_update_interval_ms\": "
           << config.adaptiveMcsUpdateIntervalMs << ",\n"
           << "    \"adaptive_mcs_use_latest_amendment_only\": " << std::boolalpha
           << config.adaptiveMcsUseLatestAmendmentOnly << ",\n"
           << "    \"adaptive_mcs_random_stream_base\": "
           << config.adaptiveMcsStreamBase << ",\n"
           << "    \"adaptive_mcs_random_stream_count\": "
           << config.adaptiveMcsStreamCount << ",\n"
           << "    \"data_mode\": \"" << JsonEscape(config.dataMode) << "\",\n"
           << "    \"control_mode\": \"" << JsonEscape(config.controlMode) << "\",\n"
           << "    \"guard_interval\": \"" << JsonEscape(config.guardInterval) << "\",\n"
           << "    \"channel_settings\": [";
    for (std::size_t i = 0; i < config.channelSettings.size(); ++i)
    {
        output << (i == 0 ? "" : ", ") << '"' << JsonEscape(config.channelSettings[i]) << '"';
    }
    output << "],\n"
           << "    \"frequency_ranges\": [";
    for (std::size_t i = 0; i < config.frequencyRanges.size(); ++i)
    {
        output << (i == 0 ? "" : ", ") << '"' << JsonEscape(config.frequencyRanges[i]) << '"';
    }
    output << "],\n"
           << "    \"data_modes_per_link\": [";
    for (std::size_t i = 0; i < config.perLinkDataModes.size(); ++i)
    {
        output << (i == 0 ? "" : ", ") << '"' << JsonEscape(config.perLinkDataModes[i]) << '"';
    }
    output << "],\n"
           << "    \"queue_max_packets\": " << config.queueMaxPackets << ",\n"
           << "    \"queue_max_delay_ms\": " << config.queueMaxDelayMs << ",\n"
           << "    \"max_ampdu_size_bytes\": " << config.maxAmpduSizeBytes << ",\n"
           << "    \"max_amsdu_size_bytes\": " << config.maxAmsduSizeBytes << ",\n"
           << "    \"sta_max_inflights\": " << config.mloStaMaxInflights << ",\n"
           << "    \"ul_ofdma_enabled\": " << std::boolalpha << config.ulOfdmaEnabled << ",\n"
           << "    \"ul_ofdma_scope\": \"" << JsonEscape(config.ulOfdmaScope) << "\",\n"
           << "    \"ul_ofdma_access_interval_ms\": " << config.ulOfdmaAccessIntervalMs << ",\n"
           << "    \"ul_ofdma_bsrp_enabled\": " << config.ulOfdmaBsrpEnabled << ",\n"
           << "    \"ul_ofdma_max_stations\": " << config.ulOfdmaMaxStations << ",\n"
           << "    \"ul_ofdma_psdu_size_bytes\": " << config.ulOfdmaPsduSizeBytes << ",\n"
           << "    \"block_ack_enabled\": " << std::boolalpha << config.blockAckEnabled << ",\n"
           << "    \"frame_retry_limit\": " << config.frameRetryLimit << ",\n"
           << "    \"rts_cts_threshold_bytes\": " << config.rtsCtsThresholdBytes << ",\n"
           << "    \"fragmentation_threshold_bytes\": "
           << config.fragmentationThresholdBytes << ",\n"
           << "    \"wmm_mode\": \"" << JsonEscape(config.wmmMode) << "\",\n"
           << "    \"stream_ip_tos\": " << +config.streamIpTos << ",\n"
           << "    \"stream_tid\": " << +config.streamTid << ",\n"
           << "    \"access_category\": \"" << JsonEscape(config.accessCategory) << "\",\n"
           << "    \"txop_limit_us\": " << config.txopLimitUs << ",\n"
           << "    \"static_association\": " << config.staticAssociation << ",\n"
           << "    \"tid_to_link_mapping_ul\": \"" << JsonEscape(config.tidToLinkMapping)
           << "\",\n"
           << "    \"str_mode\": \"" << JsonEscape(config.strMode) << "\",\n"
           << "    \"multi_link_mode\": \"" << JsonEscape(config.multiLinkMode) << "\",\n"
           << "    \"emlsr\": {\n"
           << "      \"activated\": " << config.emlsrActivated << ",\n"
           << "      \"profile\": \"" << JsonEscape(config.emlsrProfile) << "\",\n"
           << "      \"manager\": \"" << JsonEscape(config.emlsrManager) << "\",\n"
           << "      \"ap_manager\": \"" << JsonEscape(config.emlsrApManager) << "\",\n"
           << "      \"link_ids\": [";
    for (std::size_t i = 0; i < config.emlsrLinkIds.size(); ++i)
    {
        output << (i == 0 ? "" : ", ") << +config.emlsrLinkIds[i];
    }
    output << "],\n"
           << "      \"main_phy_id\": " << +config.emlsrMainPhyId << ",\n"
           << "      \"padding_delay_us\": " << config.emlsrPaddingDelayUs << ",\n"
           << "      \"transition_delay_us\": " << config.emlsrTransitionDelayUs << ",\n"
           << "      \"transition_timeout_us\": " << config.emlsrTransitionTimeoutUs << ",\n"
           << "      \"medium_sync_duration_us\": " << config.emlsrMediumSyncDurationUs
           << ",\n"
           << "      \"msd_ofdm_ed_threshold_dbm\": "
           << config.emlsrMsdOfdmEdThresholdDbm << ",\n"
           << "      \"msd_max_n_txops\": " << +config.emlsrMsdMaxNTxops << ",\n"
           << "      \"channel_switch_delay_us\": " << config.emlsrChannelSwitchDelayUs
           << ",\n"
           << "      \"switch_aux_phy\": " << config.emlsrSwitchAuxPhy << ",\n"
           << "      \"aux_phy_tx_capable\": " << config.emlsrAuxPhyTxCapable << ",\n"
           << "      \"aux_phy_channel_width_mhz\": "
           << config.emlsrAuxPhyChannelWidthMhz << ",\n"
           << "      \"aux_phy_max_modulation_class\": \""
           << JsonEscape(config.emlsrAuxPhyMaxModulationClass) << "\",\n"
           << "      \"put_aux_phy_to_sleep\": " << config.emlsrPutAuxPhyToSleep << ",\n"
           << "      \"in_device_interference\": "
           << config.emlsrInDeviceInterference << ",\n"
           << "      \"use_notified_mac_header\": " << config.emlsrUseNotifiedMacHeader
           << ",\n"
           << "      \"reset_cam_state\": " << config.emlsrResetCamState << ",\n"
           << "      \"allow_ul_txop_in_rx\": " << config.emlsrAllowUlTxopInRx << ",\n"
           << "      \"interrupt_switch\": " << config.emlsrInterruptSwitch << ",\n"
           << "      \"use_aux_phy_cca\": " << config.emlsrUseAuxPhyCca << ",\n"
           << "      \"switch_main_phy_back_delay_us\": "
           << config.emlsrSwitchMainPhyBackDelayUs << ",\n"
           << "      \"keep_main_phy_after_dl_txop\": "
           << config.emlsrKeepMainPhyAfterDlTxop << ",\n"
           << "      \"check_access_on_main_phy_link\": "
           << config.emlsrCheckAccessOnMainPhyLink << ",\n"
           << "      \"min_ac_to_skip_check_access\": \""
           << JsonEscape(config.emlsrMinAcToSkipCheckAccess) << "\",\n"
           << "      \"ap_use_notified_mac_header\": "
           << config.emlsrApUseNotifiedMacHeader << ",\n"
           << "      \"ap_early_switch_to_listening\": "
           << config.emlsrApEarlySwitchToListening << ",\n"
           << "      \"ap_wait_trans_delay_on_psdu_rx_error\": "
           << config.emlsrApWaitTransDelayOnPsduRxError << ",\n"
           << "      \"ap_update_cw_after_failed_icf\": "
           << config.emlsrApUpdateCwAfterFailedIcf << ",\n"
           << "      \"ap_report_failed_icf\": " << config.emlsrApReportFailedIcf << ",\n"
           << "      \"cam_generate_backoff_without_tx\": "
           << config.emlsrCamGenerateBackoffWithoutTx << ",\n"
           << "      \"cam_proactive_backoff\": " << config.emlsrCamProactiveBackoff
           << ",\n"
           << "      \"cam_reset_backoff_threshold_us\": "
           << config.emlsrCamResetBackoffThresholdUs << ",\n"
           << "      \"cam_n_slots_left\": " << +config.emlsrCamNSlotsLeft << ",\n"
           << "      \"cam_n_slots_left_min_delay_us\": "
           << config.emlsrCamNSlotsLeftMinDelayUs << ",\n"
           << "      \"notify_mac_header_rx_end\": "
           << config.emlsrNotifyMacHeaderRxEnd << ",\n"
           << "      \"main_phy_frequency_ranges\": [";
    for (std::size_t i = 0; i < config.emlsrMainPhyFrequencyRanges.size(); ++i)
    {
        output << (i == 0 ? "" : ", ") << '"'
               << JsonEscape(config.emlsrMainPhyFrequencyRanges[i]) << '"';
    }
    output << "]\n"
           << "    },\n"
           << "    \"application_socket_count\": " << config.applicationSocketCount << ",\n"
           << "    \"application_duplication\": " << config.applicationDuplication << "\n"
           << "  },\n"
           << "  \"policy_settings\": {\n"
           << "    \"static_link_0_score\": " << config.staticLink0Score << ",\n"
           << "    \"static_link_1_score\": " << config.staticLink1Score << ",\n"
           << "    \"mechanism_telemetry_enabled\": "
           << config.mechanismTelemetryEnabled << ",\n"
           << "    \"mechanism_action\": \""
           << JsonEscape(config.mechanismAction) << "\",\n"
           << "    \"mechanism_oracle_packet_outcome_file\": \""
           << JsonEscape(config.mechanismOraclePacketOutcomeFile) << "\",\n"
           << "    \"mechanism_systematic_repair_divisor\": "
           << config.mechanismSystematicRepairDivisor << "\n"
           << "  },\n"
           << "  \"background\": {\n"
           << "    \"profile\": \"" << JsonEscape(config.backgroundProfile) << "\",\n"
           << "    \"traffic\": \"" << JsonEscape(config.backgroundTraffic) << "\",\n"
           << "    \"direction\": \"" << JsonEscape(config.backgroundDirection) << "\",\n"
           << "    \"stations_per_link\": [";
    for (std::size_t i = 0; i < config.backgroundStations.size(); ++i)
    {
        output << (i == 0 ? "" : ", ") << config.backgroundStations[i];
    }
    output << "],\n"
           << "    \"standards_per_link\": [";
    for (std::size_t i = 0; i < config.backgroundStandards.size(); ++i)
    {
        output << (i == 0 ? "" : ", ") << '"'
               << JsonEscape(config.backgroundStandards[i]) << '"';
    }
    output << "],\n"
           << "    \"station_standards_per_link\": [";
    for (std::size_t link = 0; link < config.backgroundStationStandards.size(); ++link)
    {
        output << (link == 0 ? "" : ", ") << '[';
        for (std::size_t station = 0;
             station < config.backgroundStationStandards[link].size();
             ++station)
        {
            output << (station == 0 ? "" : ", ") << '"'
                   << JsonEscape(config.backgroundStationStandards[link][station]) << '"';
        }
        output << ']';
    }
    output << "],\n"
           << "    \"application_streams\": [";
    for (std::size_t i = 0; i < config.backgroundApplicationStreams.size(); ++i)
    {
        output << (i == 0 ? "" : ", ") << config.backgroundApplicationStreams[i];
    }
    output << "],\n"
           << "    \"association_mode\": \""
           << JsonEscape(config.backgroundAssociationMode) << "\",\n"
           << "    \"rate_mbps_per_station\": " << config.backgroundRateMbps << ",\n"
           << "    \"packet_size_bytes\": " << config.backgroundPacketSizeBytes << ",\n"
           << "    \"near_distance_m\": " << config.backgroundNearDistanceM << ",\n"
           << "    \"far_distance_m\": " << config.backgroundFarDistanceM << ",\n"
           << "    \"random_stream_base\": " << config.backgroundStreamBase << ",\n"
           << "    \"random_on_mean_ms\": " << config.randomOnMeanMs << ",\n"
           << "    \"random_off_mean_ms\": " << config.randomOffMeanMs << ",\n"
           << "    \"obss\": {\n"
           << "      \"profile\": \"" << JsonEscape(config.obssProfile) << "\",\n";
    if (config.obssWmmProfile != "legacy")
    {
        output << "      \"wmm_profile\": \""
               << JsonEscape(config.obssWmmProfile) << "\",\n"
               << "      \"vi_ip_tos\": " << +config.obssViIpTos << ",\n"
               << "      \"vi_tid\": " << +config.obssViTid << ",\n"
               << "      \"vi_access_category\": \""
               << JsonEscape(config.obssViAccessCategory) << "\",\n"
               << "      \"vi_flow_ordinals\": [";
        for (std::size_t index = 0; index < config.obssViFlowOrdinals.size(); ++index)
        {
            output << (index == 0 ? "" : ", ") << config.obssViFlowOrdinals[index];
        }
        output << "],\n";
    }
    output << "      \"stations_per_bss\": " << config.obssStationsPerBss << ",\n"
           << "      \"min_rate_mbps\": " << config.obssMinRateMbps << ",\n"
           << "      \"max_rate_mbps\": " << config.obssMaxRateMbps << ",\n"
           << "      \"ul_min_rate_mbps\": " << config.obssUlMinRateMbps << ",\n"
           << "      \"ul_max_rate_mbps\": " << config.obssUlMaxRateMbps << ",\n"
           << "      \"dl_min_rate_mbps\": " << config.obssDlMinRateMbps << ",\n"
           << "      \"dl_max_rate_mbps\": " << config.obssDlMaxRateMbps << ",\n"
           << "      \"on_mean_ms\": " << config.obssOnMeanMs << ",\n"
           << "      \"off_mean_ms\": " << config.obssOffMeanMs << ",\n"
           << "      \"station_manager\": \"" << JsonEscape(config.obssStationManager)
           << "\",\n"
           << "      \"manager_update_ms\": " << config.obssManagerUpdateMs << ",\n"
           << "      \"use_latest_amendment_only\": "
           << config.obssUseLatestAmendmentOnly << ",\n"
           << "      \"packet_size_bytes\": " << config.obssPacketSizeBytes << ",\n"
           << "      \"area_min_x_m\": " << config.obssAreaMinXM << ",\n"
           << "      \"area_max_x_m\": " << config.obssAreaMaxXM << ",\n"
           << "      \"area_min_y_m\": " << config.obssAreaMinYM << ",\n"
           << "      \"area_max_y_m\": " << config.obssAreaMaxYM << ",\n"
           << "      \"sta_min_distance_m\": " << config.obssStaMinDistanceM << ",\n"
           << "      \"sta_max_distance_m\": " << config.obssStaMaxDistanceM << ",\n"
           << "      \"placement_stream_base\": " << config.obssPlacementStreamBase << ",\n"
           << "      \"application_stream_base\": " << config.obssApplicationStreamBase
           << ",\n"
           << "      \"wifi_stream_base\": " << config.obssWifiStreamBase << ",\n"
           << "      \"bsses\": [";
    for (std::size_t bss = 0; bss < config.obssBsses.size(); ++bss)
    {
        const auto& descriptor = config.obssBsses[bss];
        output << (bss == 0 ? "" : ", ") << "{\"bss_id\": " << descriptor.bssId
               << ", \"link_id\": " << +descriptor.linkId << ", \"ssid\": \""
               << JsonEscape(descriptor.ssid) << "\", \"standard\": \""
               << JsonEscape(descriptor.standard) << "\", \"ap\": ["
               << descriptor.apX << ", " << descriptor.apY << "], \"stas\": [";
        NS_ABORT_MSG_IF(descriptor.staX.size() != descriptor.staY.size(),
                        "OBSS STA coordinate vectors differ in size");
        for (std::size_t station = 0; station < descriptor.staX.size(); ++station)
        {
            output << (station == 0 ? "" : ", ") << '[' << descriptor.staX[station] << ", "
                   << descriptor.staY[station] << ']';
        }
        output << "]}";
    }
    output << "]\n"
           << "    },\n"
           << "    \"correlation\": {\n"
           << "      \"mode\": \"" << JsonEscape(config.correlationMode) << "\",\n"
           << "      \"trace\": \"" << JsonEscape(config.correlationTrace) << "\",\n"
           << "      \"common_on_mean_ms\": " << config.commonOnMeanMs << ",\n"
           << "      \"common_off_mean_ms\": " << config.commonOffMeanMs << ",\n"
           << "      \"local_on_mean_ms\": " << config.localOnMeanMs << ",\n"
           << "      \"local_off_mean_ms\": " << config.localOffMeanMs << ",\n"
           << "      \"common_on_duration_ms\": " << config.commonOnDurationMs << ",\n"
           << "      \"common_off_duration_ms\": " << config.commonOffDurationMs << ",\n"
           << "      \"local_on_duration_ms\": " << config.localOnDurationMs << ",\n"
           << "      \"local_off_duration_ms\": " << config.localOffDurationMs << "\n"
           << "    }\n"
           << "  },\n";
    if (config.predictionTelemetryEnabled)
    {
        output << "  \"predictionTelemetry\": {\n"
               << "    \"enabled\": true,\n"
               << "    \"sample_offsets_us\": [";
        for (std::size_t index = 0; index < config.predictionSampleOffsetsUs.size(); ++index)
        {
            output << (index == 0 ? "" : ", ") << config.predictionSampleOffsetsUs[index];
        }
        output << "],\n"
               << "    \"history_windows_us\": [";
        for (std::size_t index = 0; index < config.predictionHistoryWindowsUs.size(); ++index)
        {
            output << (index == 0 ? "" : ", ") << config.predictionHistoryWindowsUs[index];
        }
        output << "],\n"
               << "    \"polling_interval_us\": " << config.predictionPollingIntervalUs << ",\n"
               << "    \"polling_report_delay_us\": "
               << config.predictionPollingReportDelayUs << ",\n"
               << "    \"polling_schema_version\": " << PREDICTION_POLLING_SCHEMA_VERSION
               << ",\n"
               << "    \"event_log_enabled\": " << config.predictionEventLogEnabled << ",\n"
               << "    \"oracle_features_enabled\": "
               << config.predictionOracleFeaturesEnabled << ",\n"
               << "    \"telemetry_schema_version\": "
               << PREDICTION_TELEMETRY_SCHEMA_VERSION << ",\n"
               << "    \"event_schema_version\": " << PREDICTION_EVENT_SCHEMA_VERSION << ",\n"
               << "    \"feature_support_mask_version\": " << FEATURE_SUPPORT_MASK_VERSION
               << "\n"
               << "  },\n";
    }
    if (config.policy == "paired_value_duplication_t2")
    {
        constexpr uint64_t nanosPerMicrosecond = 1000;
        constexpr uint64_t decisionStopGuardUs =
            (PairedValueT2Controller::MEASUREMENT_STOP_NS -
             PairedValueT2Controller::DECISION_STOP_NS) /
            nanosPerMicrosecond;
        const auto admissionProfile =
            PairedValueT2Controller::ParseAdmissionProfile(
                config.pairedValueT2AdmissionProfile);
        NS_ABORT_MSG_IF(!admissionProfile,
                        "Unknown paired-value admission profile "
                            << config.pairedValueT2AdmissionProfile);
        output << "  \"pairedValueDuplicationT2\": {\n"
               << "    \"csv_schema_version\": "
               << PairedValueT2Controller::GetCsvSchemaVersion(*admissionProfile) << ",\n"
               << "    \"summary_schema_version\": "
               << PairedValueT2Controller::GetSummarySchemaVersion(*admissionProfile)
               << ",\n"
               << "    \"runtime_contract_id\": \""
               << PairedValueT2Controller::GetRuntimeContractId(*admissionProfile)
               << "\",\n"
               << "    \"runtime_contract_sha256\": \""
               << PairedValueT2Controller::GetRuntimeContractSha256(*admissionProfile)
               << "\",\n"
               << "    \"primary_path\": " << +PairedValueT2Controller::PRIMARY_PATH_ID
               << ",\n"
               << "    \"primary_copy_id\": "
               << +PairedValueT2Controller::PRIMARY_COPY_ID << ",\n"
               << "    \"secondary_path\": "
               << +PairedValueT2Controller::SECONDARY_PATH_ID << ",\n"
               << "    \"secondary_copy_id\": "
               << +PairedValueT2Controller::SECONDARY_COPY_ID << ",\n"
               << "    \"stage\": \"T2\",\n"
               << "    \"sample_offset_us\": " << PairedValueT2Controller::T2_OFFSET_US
               << ",\n"
               << "    \"measurement_start_ns\": "
               << PairedValueT2Controller::MEASUREMENT_START_NS << ",\n"
               << "    \"measurement_stop_ns\": "
               << PairedValueT2Controller::MEASUREMENT_STOP_NS << ",\n"
               << "    \"decision_start_ns\": "
               << PairedValueT2Controller::DECISION_START_NS << ",\n"
               << "    \"decision_stop_ns\": "
               << PairedValueT2Controller::DECISION_STOP_NS << ",\n"
               << "    \"decision_stop_guard_us\": " << decisionStopGuardUs << ",\n"
               << "    \"delayed_secondary_prediction_tracking_enabled\": true,\n"
               << "    \"receiver_hold_for_delayed_secondary\": true,\n"
               << "    \"action\": \"canonical_full_secondary_copy\",\n"
               << "    \"cost_estimator_id\": \""
               << PairedValueT2Controller::GetCostEstimatorId() << "\",\n"
               << "    \"cost_safety_factor\": "
               << PairedValueT2Controller::COST_SAFETY_FACTOR << ",\n"
               << "    \"budget_fraction\": " << PairedValueT2Controller::BUDGET_FRACTION
               << ",\n"
               << "    \"budget_max_horizon_us\": "
               << PairedValueT2Controller::GetBudgetMaxHorizonUs(*admissionProfile)
               << ",\n"
               << "    \"budget_initial_horizon_us\": "
               << PairedValueT2Controller::BUDGET_INITIAL_HORIZON_US;
        if (PairedValueT2Controller::UsesScoreAwareEmergency(*admissionProfile))
        {
            output
                << ",\n"
                << "    \"admission_profile_id\": \""
                << PairedValueT2Controller::AdmissionProfileName(*admissionProfile)
                << "\",\n"
                << "    \"emergency_score_threshold_float32\": "
                << PairedValueT2Controller::GetEmergencyScoreThreshold(
                       *admissionProfile)
                << ",\n"
                << "    \"emergency_score_threshold_float32_bits_hex\": "
                << (PairedValueT2Controller::UsesCostFreeScore(*admissionProfile)
                        ? "\"0x3e9d2ac5\""
                        : "\"0x391d4952\"")
                << ",\n"
                << "    \"emergency_maximum_debt_us\": "
                << PairedValueT2Controller::EMERGENCY_MAXIMUM_DEBT_US;
            if (PairedValueT2Controller::UsesRemainingRefillBorrowing(
                    *admissionProfile))
            {
                output
                    << ",\n"
                    << "    \"remaining_refill_borrowing_enabled\": true,\n"
                    << "    \"remaining_refill_repayment_stop_ns\": "
                    << PairedValueT2Controller::MEASUREMENT_STOP_NS << "\n";
            }
            else
            {
                output << "\n";
            }
        }
        else
        {
            output << "\n";
        }
        output << "  },\n";
    }
    if (config.policy == "distributional_shadow_duplication_t2")
    {
        constexpr uint64_t nanosPerMicrosecond = 1000;
        constexpr uint64_t decisionStopGuardUs =
            (DistributionalShadowT2Controller::MEASUREMENT_STOP_NS -
             DistributionalShadowT2Controller::DECISION_STOP_NS) /
            nanosPerMicrosecond;
        output
            << "  \"distributionalShadowDuplicationT2\": {\n"
            << "    \"csv_schema_version\": "
            << DistributionalShadowT2Controller::CSV_SCHEMA_VERSION << ",\n"
            << "    \"summary_schema_version\": "
            << DistributionalShadowT2Controller::SUMMARY_SCHEMA_VERSION << ",\n"
            << "    \"runtime_contract_id\": \""
            << DistributionalShadowT2Controller::GetRuntimeContractId() << "\",\n"
            << "    \"runtime_contract_sha256\": \""
            << DistributionalShadowT2Controller::GetRuntimeContractSha256()
            << "\",\n"
            << "    \"primary_path\": "
            << +DistributionalShadowT2Controller::PRIMARY_PATH_ID << ",\n"
            << "    \"primary_copy_id\": "
            << +DistributionalShadowT2Controller::PRIMARY_COPY_ID << ",\n"
            << "    \"secondary_path\": "
            << +DistributionalShadowT2Controller::SECONDARY_PATH_ID << ",\n"
            << "    \"secondary_copy_id\": "
            << +DistributionalShadowT2Controller::SECONDARY_COPY_ID << ",\n"
            << "    \"stage\": \"T2\",\n"
            << "    \"sample_offset_us\": "
            << DistributionalShadowT2Controller::T2_OFFSET_US << ",\n"
            << "    \"measurement_start_ns\": "
            << DistributionalShadowT2Controller::MEASUREMENT_START_NS << ",\n"
            << "    \"measurement_stop_ns\": "
            << DistributionalShadowT2Controller::MEASUREMENT_STOP_NS << ",\n"
            << "    \"decision_start_ns\": "
            << DistributionalShadowT2Controller::DECISION_START_NS << ",\n"
            << "    \"decision_stop_ns\": "
            << DistributionalShadowT2Controller::DECISION_STOP_NS << ",\n"
            << "    \"decision_stop_guard_us\": " << decisionStopGuardUs << ",\n"
            << "    \"delayed_secondary_prediction_tracking_enabled\": true,\n"
            << "    \"receiver_hold_for_delayed_secondary\": true,\n"
            << "    \"action\": \"canonical_full_secondary_copy\",\n"
            << "    \"predictor_variant\": \""
            << TemporalT2DistributionModelEvaluator::GetProvenance().selectedVariant
            << "\",\n"
            << "    \"predictor_model_spec_id\": \""
            << TemporalT2DistributionModelEvaluator::GetModelSpecId() << "\",\n"
            << "    \"predictor_feature_count\": "
            << TemporalT2DistributionModelEvaluator::RAW_FEATURE_COUNT << ",\n"
            << "    \"deadline_reward_and_tail_gain_are_separate\": true,\n"
            << "    \"shadow_reference\": \"congestion_tertile_5s\",\n"
            << "    \"cost_estimator_id\": \""
            << DistributionalShadowT2Controller::GetCostEstimatorId() << "\",\n"
            << "    \"cost_safety_factor\": "
            << DistributionalShadowT2Controller::COST_SAFETY_FACTOR << ",\n"
            << "    \"canonical_p_frame_reservation_us\": "
            << TemporalT2DistributionModelEvaluator::GetCanonicalPFrameReservationUs()
            << ",\n"
            << "    \"budget_fraction\": "
            << DistributionalShadowT2Controller::BUDGET_FRACTION << ",\n"
            << "    \"positive_balance_capacity_us\": "
            << DistributionalShadowT2Controller::POSITIVE_BALANCE_CAPACITY_US
            << ",\n"
            << "    \"initial_credit_us\": "
            << DistributionalShadowT2Controller::INITIAL_CREDIT_US << ",\n"
            << "    \"negative_balance_allowed_when_repayable\": true,\n"
            << "    \"accepted_reservation_is_permanent\": true,\n"
            << "    \"measured_settlement_refunds_ledger\": false\n"
            << "  },\n";
    }
    if (config.policy == "selective_duplication")
    {
        output << "  \"selectiveDuplication\": {\n"
               << "    \"model_id\": \"" << PredictionModelEvaluator::GetModelId() << "\",\n"
               << "    \"target_id\": \"" << PredictionModelEvaluator::GetTargetId() << "\",\n"
               << "    \"target_provenance_sha256\": \""
               << PredictionModelEvaluator::GetTargetProvenanceSha256() << "\",\n"
               << "    \"source_model_sha256\": \""
               << PredictionModelEvaluator::GetSourceModelSha256() << "\",\n"
               << "    \"feature_set\": \"F0+F1-degraded\",\n"
               << "    \"degradation_profile\": \"polling_1ms\",\n"
               << "    \"calibration\": \"platt\",\n"
               << "    \"stages\": [";
        for (std::size_t index = 0; index < config.selectiveDuplicationDecisionOffsetsUs.size();
             ++index)
        {
            output << (index == 0 ? "" : ", ") << '"'
                   << DecisionStageName(config.selectiveDuplicationDecisionOffsetsUs[index])
                   << '"';
        }
        output << "],\n"
               << "    \"primary_path\": 1,\n"
               << "    \"secondary_path\": 0,\n"
               << "    \"probability_threshold\": "
               << config.selectiveDuplicationThreshold << ",\n"
               << "    \"frame_budget\": " << config.selectiveDuplicationFrameBudget << ",\n"
               << "    \"burst_horizon_frames\": "
               << config.selectiveDuplicationBurstHorizonFrames << ",\n"
               << "    \"decision_offsets_us\": [";
        for (std::size_t index = 0;
             index < config.selectiveDuplicationDecisionOffsetsUs.size();
             ++index)
        {
            output << (index == 0 ? "" : ", ")
                   << config.selectiveDuplicationDecisionOffsetsUs[index];
        }
        output << "]\n"
               << "  },\n";
    }
    if (config.policy == "adaptive_airtime_duplication" ||
        config.policy == "adaptive_deficit_duplication")
    {
        const bool primaryDeficit = config.policy == "adaptive_deficit_duplication";
        NS_ABORT_MSG_IF(config.adaptiveAirtimeAdmissionPacketCost != "launched_packet_set" &&
                            config.adaptiveAirtimeAdmissionPacketCost != "whole_copy",
                        "Unknown adaptive admission packet-cost basis");
        const bool wholeCopyAdmission =
            !primaryDeficit || config.adaptiveAirtimeAdmissionPacketCost == "whole_copy";
        const std::string effectiveAdmissionPacketCost =
            wholeCopyAdmission ? "whole_copy" : "primary_unacknowledged_packet_set";
        const std::string operatingProfile =
            primaryDeficit
                ? (wholeCopyAdmission
                       ? "primary_unacknowledged+whole_copy_priced"
                       : "primary_unacknowledged+selected_packet_set_priced")
                : "full_forward+whole_copy_priced";
        const std::string admissionInflation =
            config.adaptiveAirtimeAdmissionUsesRetryInflation ? "retry_inflated"
                                                              : "nominal";
        output << "  \""
               << (primaryDeficit ? "adaptiveDeficitDuplication"
                                  : "adaptiveAirtimeDuplication")
               << "\": {\n"
               << "    \"score_contract\": \"stage_specific\",\n"
               << "    \"stage_scorers\": {\n";
        for (std::size_t index = 0; index < config.adaptiveAirtimeDecisionOffsetsUs.size();
             ++index)
        {
            const auto offsetUs = config.adaptiveAirtimeDecisionOffsetsUs[index];
            const auto& identity = ClosedLoopRiskPredictor::GetModelIdentity(offsetUs);
            output << "      \"" << DecisionStageName(offsetUs) << "\": {\n"
                   << "        \"sample_offset_us\": " << offsetUs << ",\n"
                   << "        \"score_name\": \"" << identity.scoreName << "\",\n"
                   << "        \"score_kind\": \""
                   << ClosedLoopRiskPredictor::GetScoreKindName(identity.scoreKind)
                   << "\",\n"
                   << "        \"model_id\": \"" << identity.modelId << "\",\n"
                   << "        \"primary_miss_target_id\": \""
                   << identity.primaryMissTargetId << "\",\n"
                   << "        \"completed_tail_target_id\": \""
                   << identity.completedTailTargetId << "\",\n"
                   << "        \"source_model_sha256\": \""
                   << identity.sourceModelSha256 << "\",\n"
                   << "        \"target_provenance_sha256\": \""
                   << identity.targetProvenanceSha256 << "\",\n"
                   << "        \"feature_contract_sha256\": \""
                   << identity.featureContractSha256 << "\",\n"
                   << "        \"combiner_sha256\": \"" << identity.combinerSha256
                   << "\",\n"
                   << "        \"primary_miss_model_sha256\": \""
                   << identity.primaryMissModelSha256 << "\",\n"
                   << "        \"completed_tail_model_sha256\": \""
                   << identity.completedTailModelSha256 << "\",\n"
                   << "        \"feature_count\": " << identity.featureCount << "\n"
                   << "      }"
                   << (index + 1 == config.adaptiveAirtimeDecisionOffsetsUs.size() ? "\n"
                                                                                   : ",\n");
        }
        output << "    },\n"
               << "    \"admission_feature_set\": \"stage_specific_compiled\",\n"
               << "    \"packet_selection_feature_set\": \""
               << (primaryDeficit ? "F2-primary-frame-ack-state" : "none") << "\",\n"
               << "    \"packet_selection\": \""
               << (primaryDeficit ? "primary_unacknowledged_reverse" : "full_forward")
               << "\",\n"
               << "    \"degradation_profile\": \"polling_1ms\",\n"
               << "    \"configured_admission_packet_cost\": \""
               << config.adaptiveAirtimeAdmissionPacketCost << "\",\n"
               << "    \"effective_admission_packet_cost\": \""
               << effectiveAdmissionPacketCost << "\",\n"
               << "    \"operating_profile\": \"" << operatingProfile << "\",\n"
               << "    \"stages\": [";
        for (std::size_t index = 0; index < config.adaptiveAirtimeDecisionOffsetsUs.size();
             ++index)
        {
            output << (index == 0 ? "" : ", ") << '"'
                   << DecisionStageName(config.adaptiveAirtimeDecisionOffsetsUs[index]) << '"';
        }
        output << "],\n"
               << "    \"primary_path\": 1,\n"
               << "    \"secondary_path\": 0,\n"
               << "    \"budget_definition\": \"secondary_sender_phy_tx_airtime\",\n"
               << "    \"budget_fraction\": " << config.adaptiveAirtimeBudgetFraction << ",\n"
               << "    \"bucket_horizon_us\": " << config.adaptiveAirtimeBucketHorizonUs
               << ",\n"
               << "    \"initial_bucket_horizon_us\": "
               << config.adaptiveAirtimeInitialBucketHorizonUs << ",\n"
               << "    \"initial_bucket_capacity_us\": "
               << config.adaptiveAirtimeBudgetFraction *
                      static_cast<double>(config.adaptiveAirtimeInitialBucketHorizonUs)
               << ",\n"
               << "    \"initial_shadow_price\": "
               << config.adaptiveAirtimeInitialShadowPrice << ",\n"
               << "    \"dual_step\": " << config.adaptiveAirtimeDualStep << ",\n"
               << "    \"admission_uses_retry_inflation\": "
               << config.adaptiveAirtimeAdmissionUsesRetryInflation << ",\n"
               << "    \"admission_cost_definition\": \""
               << admissionInflation << "_estimated_" << effectiveAdmissionPacketCost
               << "_secondary_sender_phy_tx_airtime"
               << "\",\n"
               << "    \"reservation_cost_definition\": "
                  "\"retry_inflated_estimated_launched_packet_set_"
                  "secondary_sender_phy_tx_airtime\",\n"
               << "    \"cost_safety_factor\": " << config.adaptiveAirtimeCostSafetyFactor
               << ",\n"
               << "    \"cost_ewma_alpha\": " << config.adaptiveAirtimeCostEwmaAlpha << ",\n"
               << "    \"decision_offsets_us\": [";
        for (std::size_t index = 0; index < config.adaptiveAirtimeDecisionOffsetsUs.size();
             ++index)
        {
            output << (index == 0 ? "" : ", ")
                   << config.adaptiveAirtimeDecisionOffsetsUs[index];
        }
        output << "],\n"
               << "    \"shadow_price_mode\": \""
               << (config.adaptiveAirtimeDecisionOffsetShadowPrices.empty()
                       ? "global_dual"
                       : "offset_override_with_global_dual_fallback")
               << "\",\n"
               << "    \"decision_offset_shadow_prices\": {";
        std::size_t priceIndex = 0;
        for (const auto& [offsetUs, price] :
             config.adaptiveAirtimeDecisionOffsetShadowPrices)
        {
            output << (priceIndex++ == 0 ? "" : ", ") << '"' << offsetUs << "\": "
                   << price;
        }
        output << "},\n"
               << "    \"i_frame_only_decision_offsets_us\": [";
        for (std::size_t index = 0;
             index < config.adaptiveAirtimeIFrameOnlyDecisionOffsetsUs.size();
             ++index)
        {
            output << (index == 0 ? "" : ", ")
                   << config.adaptiveAirtimeIFrameOnlyDecisionOffsetsUs[index];
        }
        output << "]\n"
               << "  },\n";
    }
    if (config.policy == "randomized_full_copy_exploration")
    {
        output << "  \"randomizedIntervention\": {\n"
               << "    \"csv_schema_version\": "
               << RandomizedInterventionController::CSV_SCHEMA_VERSION << ",\n"
               << "    \"assignment_algorithm\": \""
               << JsonEscape(config.randomizedAssignmentAlgorithm) << "\",\n"
               << "    \"assignment_salt\": " << config.randomizedAssignmentSalt << ",\n"
               << "    \"randomization_consumes_ns3_rng\": false,\n"
               << "    \"arm_probabilities\": {\"FULL_COPY_T2\": "
               << config.randomizedT2Probability << ", \"FULL_COPY_T4\": "
               << config.randomizedT4Probability << ", \"CONTROL\": "
               << (1.0 - config.randomizedT2Probability -
                   config.randomizedT4Probability)
               << "},\n"
               << "    \"stages\": [\"T2\", \"T4\"],\n"
               << "    \"stage_offsets_us\": [2000, 4000],\n"
               << "    \"primary_path\": 1,\n"
               << "    \"primary_copy_id\": 0,\n"
               << "    \"secondary_path\": 0,\n"
               << "    \"secondary_copy_id\": 1,\n"
               << "    \"assignment_window_start_ns\": "
               << config.randomizedAssignmentWindowStartNs << ",\n"
               << "    \"assignment_window_stop_ns\": "
               << config.randomizedAssignmentWindowStopNs << ",\n"
               << "    \"assignment_stop_guard_us\": "
               << config.randomizedAssignmentStopGuardUs << ",\n"
               << "    \"common_eligibility_rule\": "
                  "\"T2_at_or_after_start_and_prospective_T4_before_stop_and_"
                  "primary_actionable_and_canonical_secondary_descriptor_available\",\n"
               << "    \"intervention\": \"canonical_full_secondary_copy\",\n"
               << "    \"token_gate_enabled\": false,\n"
               << "    \"cost_estimator_id\": \""
               << JsonEscape(config.randomizedCostEstimator) << "\"\n"
               << "  },\n";
    }
    if (config.secondaryAirtimeMeterEnabled)
    {
        output << "  \"secondaryAirtimeMeter\": {\n"
               << "    \"enabled\": true,\n"
               << "    \"event_schema_version\": "
               << config.secondaryAirtimeEventSchemaVersion << ",\n"
               << "    \"path_id\": 0,\n"
               << "    \"copy_id\": 1,\n"
               << "    \"definition\": \"secondary_sender_phy_tx_airtime\",\n"
               << "    \"measurement_start_ns\": "
               << static_cast<uint64_t>(std::llround(config.warmupSeconds * 1e9)) << ",\n"
               << "    \"measurement_stop_ns\": "
               << static_cast<uint64_t>(
                      std::llround((config.warmupSeconds + config.durationSeconds) * 1e9))
               << "\n"
               << "  },\n";
    }
    output << "  \"packet_event_logs_enabled\": " << config.packetEventLogsEnabled << "\n"
           << "}\n";
}

void
ExperimentOutput::WriteMloRuntime(const std::string& outputDir, const MloRuntimeInfo& info)
{
    auto output = OpenOutput(outputDir, "mlo_runtime.json");
    output << "{\n"
           << "  \"mode\": \"" << JsonEscape(info.mode) << "\",\n"
           << "  \"profile\": \"" << JsonEscape(info.profile) << "\",\n"
           << "  \"station_emlsr_activated\": " << std::boolalpha
           << info.stationEmlsrActivated << ",\n"
           << "  \"ap_emlsr_activated\": " << info.apEmlsrActivated << ",\n"
           << "  \"emlsr_manager\": \"" << JsonEscape(info.emlsrManager) << "\",\n"
           << "  \"ap_emlsr_manager\": \"" << JsonEscape(info.apEmlsrManager) << "\",\n"
           << "  \"emlsr_link_ids\": [";
    for (std::size_t i = 0; i < info.emlsrLinkIds.size(); ++i)
    {
        output << (i == 0 ? "" : ", ") << +info.emlsrLinkIds[i];
    }
    output << "],\n"
           << "  \"ap_emlsr_enabled_per_link\": [";
    for (std::size_t i = 0; i < info.apEmlsrEnabledPerLink.size(); ++i)
    {
        output << (i == 0 ? "" : ", ") << info.apEmlsrEnabledPerLink[i];
    }
    output << "],\n"
           << "  \"main_phy_id\": " << +info.mainPhyId << ",\n"
           << "  \"initial_main_phy_link_id\": " << +info.initialMainPhyLinkId << ",\n"
           << "  \"initial_main_phy_band\": \"" << JsonEscape(info.initialMainPhyBand)
           << "\",\n"
           << "  \"padding_delay_us\": " << info.paddingDelayUs << ",\n"
           << "  \"transition_delay_us\": " << info.transitionDelayUs << ",\n"
           << "  \"transition_timeout_us\": " << info.transitionTimeoutUs << ",\n"
           << "  \"medium_sync_duration_us\": " << info.mediumSyncDurationUs << ",\n"
           << "  \"msd_ofdm_ed_threshold_dbm\": " << info.msdOfdmEdThresholdDbm << ",\n"
           << "  \"msd_max_n_txops\": " << +info.msdMaxNTxops << ",\n"
           << "  \"channel_switch_delay_us\": " << info.channelSwitchDelayUs << ",\n"
           << "  \"switch_aux_phy\": " << info.switchAuxPhy << ",\n"
           << "  \"aux_phy_tx_capable\": " << info.auxPhyTxCapable << ",\n"
           << "  \"aux_phy_channel_width_mhz\": " << info.auxPhyChannelWidthMhz << ",\n"
           << "  \"aux_phy_max_modulation_class\": \""
           << JsonEscape(info.auxPhyMaxModulationClass) << "\",\n"
           << "  \"put_aux_phy_to_sleep\": " << info.putAuxPhyToSleep << ",\n"
           << "  \"in_device_interference\": " << info.inDeviceInterference << ",\n"
           << "  \"use_notified_mac_header\": " << info.useNotifiedMacHeader << ",\n"
           << "  \"reset_cam_state\": " << info.resetCamState << ",\n"
           << "  \"allow_ul_txop_in_rx\": " << info.allowUlTxopInRx << ",\n"
           << "  \"interrupt_switch\": " << info.interruptSwitch << ",\n"
           << "  \"use_aux_phy_cca\": " << info.useAuxPhyCca << ",\n"
           << "  \"switch_main_phy_back_delay_us\": " << info.switchMainPhyBackDelayUs
           << ",\n"
           << "  \"keep_main_phy_after_dl_txop\": " << info.keepMainPhyAfterDlTxop << ",\n"
           << "  \"check_access_on_main_phy_link\": " << info.checkAccessOnMainPhyLink
           << ",\n"
           << "  \"min_ac_to_skip_check_access\": \""
           << JsonEscape(info.minAcToSkipCheckAccess) << "\",\n"
           << "  \"ap_use_notified_mac_header\": " << info.apUseNotifiedMacHeader << ",\n"
           << "  \"ap_early_switch_to_listening\": " << info.apEarlySwitchToListening
           << ",\n"
           << "  \"ap_wait_trans_delay_on_psdu_rx_error\": "
           << info.apWaitTransDelayOnPsduRxError << ",\n"
           << "  \"ap_update_cw_after_failed_icf\": " << info.apUpdateCwAfterFailedIcf
           << ",\n"
           << "  \"ap_report_failed_icf\": " << info.apReportFailedIcf << ",\n"
           << "  \"all_phy_settings_match_profile\": "
           << info.allPhySettingsMatchProfile << ",\n"
           << "  \"all_cam_settings_match_profile\": "
           << info.allCamSettingsMatchProfile << ",\n"
           << "  \"notify_mac_header_rx_end\": " << info.notifyMacHeaderRxEnd << ",\n"
           << "  \"main_phy_frequency_ranges\": [";
    for (std::size_t i = 0; i < info.mainPhyFrequencyRanges.size(); ++i)
    {
        output << (i == 0 ? "" : ", ") << '"'
               << JsonEscape(info.mainPhyFrequencyRanges[i]) << '"';
    }
    output << "],\n"
           << "  \"successful_mpdus_per_link\": [";
    for (std::size_t i = 0; i < info.successfulMpdusPerLink.size(); ++i)
    {
        output << (i == 0 ? "" : ", ") << info.successfulMpdusPerLink[i];
    }
    output << "],\n"
           << "  \"phy_tx_time_us_per_link\": [";
    for (std::size_t i = 0; i < info.phyTxTimeUsPerLink.size(); ++i)
    {
        output << (i == 0 ? "" : ", ") << info.phyTxTimeUsPerLink[i];
    }
    output << "]\n"
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
ExperimentOutput::WriteBackgroundFlows(const std::string& outputDir,
                                       const std::vector<BackgroundFlowRecord>& records)
{
    auto output = OpenOutput(outputDir, "background_flows.csv");
    output << "run_id,bss_id,link_id,standard,sta_index,direction,source_node_id,"
              "destination_node_id,port,rate_stream,on_stream,off_stream,period_count,"
              "bytes_sent,bytes_received\n";
    for (const auto& record : records)
    {
        output << record.runId << ',' << record.bssId << ',' << +record.linkId << ','
               << record.standard << ',' << record.staIndex << ',' << record.direction << ','
               << record.sourceNodeId << ',' << record.destinationNodeId << ',' << record.port
               << ',' << record.rateStream << ',' << record.onStream << ',' << record.offStream
               << ',' << record.periodCount << ',' << record.bytesSent << ','
               << record.bytesReceived << '\n';
    }
}

void
ExperimentOutput::WriteBackgroundRatePeriods(
    const std::string& outputDir,
    const std::vector<BackgroundRatePeriodRecord>& records)
{
    auto output = OpenOutput(outputDir, "background_rate_periods.csv");
    output << "run_id,bss_id,sta_index,direction,period_index,start_us,end_us,rate_mbps\n";
    for (const auto& record : records)
    {
        output << record.runId << ',' << record.bssId << ',' << record.staIndex << ','
               << record.direction << ',' << record.periodIndex << ',' << record.startUs << ','
               << record.endUs << ',' << record.rateMbps << '\n';
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
           << "  \"phy_cca_busy_time_us\": " << summary.phyCcaBusyTimeUs << ",\n"
           << "  \"background_bytes_sent\": " << summary.backgroundBytesSent << ",\n"
           << "  \"background_bytes_received\": " << summary.backgroundBytesReceived << ",\n"
           << "  \"background_bytes_sent_per_link\": [";
    for (std::size_t i = 0; i < summary.backgroundBytesSentPerLink.size(); ++i)
    {
        output << (i == 0 ? "" : ", ") << summary.backgroundBytesSentPerLink[i];
    }
    output << "],\n"
           << "  \"background_bytes_received_per_link\": [";
    for (std::size_t i = 0; i < summary.backgroundBytesReceivedPerLink.size(); ++i)
    {
        output << (i == 0 ? "" : ", ") << summary.backgroundBytesReceivedPerLink[i];
    }
    output << "],\n"
           << "  \"background_bytes_sent_per_station\": [";
    for (std::size_t i = 0; i < summary.backgroundBytesSentPerStation.size(); ++i)
    {
        output << (i == 0 ? "" : ", ") << summary.backgroundBytesSentPerStation[i];
    }
    output << "],\n"
           << "  \"background_bytes_received_per_station\": [";
    for (std::size_t i = 0; i < summary.backgroundBytesReceivedPerStation.size(); ++i)
    {
        output << (i == 0 ? "" : ", ") << summary.backgroundBytesReceivedPerStation[i];
    }
    output << "],\n"
           << "  \"background_throughput_mbps\": " << summary.backgroundThroughputMbps << "\n"
           << "}\n";
}

} // namespace ns3
