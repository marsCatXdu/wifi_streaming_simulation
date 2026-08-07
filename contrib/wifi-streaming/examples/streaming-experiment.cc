/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "ns3/applications-module.h"
#include "ns3/core-module.h"
#include "ns3/csma-module.h"
#include "ns3/internet-module.h"
#include "ns3/mobility-module.h"
#include "ns3/multi-model-spectrum-channel.h"
#include "ns3/propagation-delay-model.h"
#include "ns3/propagation-loss-model.h"
#include "ns3/spectrum-wifi-helper.h"
#include "ns3/wifi-module.h"
#include "ns3/wifi-streaming-module.h"

#include <charconv>
#include <chrono>
#include <cmath>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <numeric>
#include <set>
#include <string_view>
#include <unistd.h>

using namespace ns3;

namespace
{

#ifndef WIFI_STREAMING_PROJECT_COMMIT
#define WIFI_STREAMING_PROJECT_COMMIT "unknown"
#endif

bool
IsPrimaryRiskT0Model()
{
    return PredictionModelEvaluator::GetModelId() ==
               "commodity_polling_1ms_obss_primary_t0_v1" &&
           PredictionModelEvaluator::GetTargetId() == "primary_copy_deadline_miss";
}

bool
SupportsStagedAdaptivePolicy(const std::string& policyName)
{
    if (policyName != "adaptive_airtime_duplication" &&
        policyName != "adaptive_deficit_duplication")
    {
        return false;
    }
    return ClosedLoopRiskPredictor::HasExactStagedModelContract();
}

bool
IsMechanismT2Policy(const std::string& policyName)
{
    return policyName == "mechanism_full_copy_t2" ||
           policyName == "mechanism_oracle_repair_t2" ||
           policyName == "mechanism_systematic_fec_t2";
}

void
PopulateNeighborCaches()
{
    NeighborCacheHelper neighborCache;
    neighborCache.PopulateNeighborCache();
}

std::string
UtcTimestamp()
{
    const auto now = std::chrono::system_clock::now();
    const std::time_t time = std::chrono::system_clock::to_time_t(now);
    std::tm utc{};
    gmtime_r(&time, &utc);
    char buffer[32];
    std::strftime(buffer, sizeof(buffer), "%Y-%m-%dT%H:%M:%SZ", &utc);
    return buffer;
}

std::string
HostName()
{
    char buffer[256] = {};
    return gethostname(buffer, sizeof(buffer) - 1) == 0 ? buffer : "unknown";
}

std::string
BuildProfile()
{
#if defined(NS3_BUILD_PROFILE_OPTIMIZED)
    return "optimized";
#elif defined(NS3_BUILD_PROFILE_RELEASE)
    return "release";
#elif defined(NS3_BUILD_PROFILE_DEBUG)
    return "debug";
#else
    return "unknown";
#endif
}

std::vector<uint64_t>
ParseStrictUintList(const std::string& value,
                    const std::string& name,
                    bool allowZero)
{
    NS_ABORT_MSG_IF(value.empty(), name << " cannot be empty");
    std::vector<uint64_t> result;
    std::string_view remaining(value);
    while (!remaining.empty())
    {
        const auto comma = remaining.find(',');
        const auto token = remaining.substr(0, comma);
        NS_ABORT_MSG_IF(token.empty(), name << " contains an empty item");
        uint64_t parsed = 0;
        const auto conversion =
            std::from_chars(token.data(), token.data() + token.size(), parsed);
        NS_ABORT_MSG_IF(conversion.ec != std::errc() ||
                            conversion.ptr != token.data() + token.size(),
                        name << " contains a non-integer item: " << token);
        NS_ABORT_MSG_IF(!allowZero && parsed == 0, name << " values must be positive");
        NS_ABORT_MSG_IF(!result.empty() && parsed <= result.back(),
                        name << " must be strictly increasing and unique");
        result.push_back(parsed);
        if (comma == std::string_view::npos)
        {
            break;
        }
        NS_ABORT_MSG_IF(comma + 1 == remaining.size(),
                        name << " contains an empty item");
        remaining.remove_prefix(comma + 1);
    }
    return result;
}

std::map<uint64_t, double>
ParseStrictOffsetPriceMap(const std::string& value, const std::string& name)
{
    std::map<uint64_t, double> result;
    if (value.empty())
    {
        return result;
    }
    std::string_view remaining(value);
    while (!remaining.empty())
    {
        const auto comma = remaining.find(',');
        const auto token = remaining.substr(0, comma);
        const auto colon = token.find(':');
        NS_ABORT_MSG_IF(token.empty() || colon == std::string_view::npos || colon == 0 ||
                            colon + 1 == token.size() ||
                            token.find(':', colon + 1) != std::string_view::npos,
                        name << " items must have offset:price form");

        const auto offsetToken = token.substr(0, colon);
        const auto priceToken = token.substr(colon + 1);
        uint64_t offsetUs = 0;
        double price = 0;
        const auto offsetConversion = std::from_chars(offsetToken.data(),
                                                      offsetToken.data() + offsetToken.size(),
                                                      offsetUs);
        const auto priceConversion = std::from_chars(priceToken.data(),
                                                     priceToken.data() + priceToken.size(),
                                                     price);
        NS_ABORT_MSG_IF(offsetConversion.ec != std::errc() ||
                            offsetConversion.ptr != offsetToken.data() + offsetToken.size(),
                        name << " contains a non-integer offset: " << offsetToken);
        NS_ABORT_MSG_IF(priceConversion.ec != std::errc() ||
                            priceConversion.ptr != priceToken.data() + priceToken.size() ||
                            !std::isfinite(price) || price < 0 || price > 1,
                        name << " prices must be finite and in [0,1]");
        NS_ABORT_MSG_IF(!result.empty() && offsetUs <= result.rbegin()->first,
                        name << " offsets must be strictly increasing and unique");
        result.emplace(offsetUs, price);
        if (comma == std::string_view::npos)
        {
            break;
        }
        NS_ABORT_MSG_IF(comma + 1 == remaining.size(),
                        name << " contains an empty item");
        remaining.remove_prefix(comma + 1);
    }
    return result;
}

uint64_t
GetStateDurationUs(const WifiCoTraceHelper::DeviceRecord& record,
                   uint8_t phyLinkId,
                   WifiPhyState state)
{
    const auto link = record.m_linkStateDurations.find(phyLinkId);
    if (link == record.m_linkStateDurations.end())
    {
        return 0;
    }
    const auto duration = link->second.find(state);
    return duration == link->second.end() ? 0 : duration->second.GetMicroSeconds();
}

void
CountBackgroundTx(uint64_t* bytes, Ptr<const Packet> packet)
{
    *bytes += packet->GetSize();
}

WifiStandard
ParseWifiStandard(const std::string& name)
{
    if (name == "ht")
    {
        return WIFI_STANDARD_80211n;
    }
    if (name == "vht")
    {
        return WIFI_STANDARD_80211ac;
    }
    if (name == "he")
    {
        return WIFI_STANDARD_80211ax;
    }
    if (name == "eht")
    {
        return WIFI_STANDARD_80211be;
    }
    NS_ABORT_MSG("Wi-Fi standard must be ht, vht, he, or eht: " << name);
    return WIFI_STANDARD_80211ax;
}

std::string
DataModeForStandard(const std::string& name)
{
    return name == "ht"    ? "HtMcs5"
           : name == "vht" ? "VhtMcs5"
           : name == "eht" ? "EhtMcs5"
                           : "HeMcs5";
}

std::string
StandardLabel(const std::string& name)
{
    return name == "ht"    ? "802.11n"
           : name == "vht" ? "802.11ac"
           : name == "eht" ? "802.11be"
                           : "802.11ax";
}

uint8_t
StandardRank(const std::string& name)
{
    return name == "ht" ? 0 : (name == "vht" ? 1 : (name == "he" ? 2 : 3));
}

std::vector<std::string>
LegacyMixed8Standards(uint32_t link, bool singleLink)
{
    if (!singleLink && link == 0)
    {
        return {"ht", "he", "eht", "ht", "he", "eht", "ht", "he"};
    }
    return {"ht", "vht", "he", "eht", "ht", "vht", "he", "eht"};
}

void
ConfigureUlOfdmaScheduler(WifiMacHelper& mac,
                          bool enabled,
                          uint32_t accessIntervalMs,
                          bool bsrpEnabled,
                          uint32_t maxStations,
                          uint32_t psduSize)
{
    if (!enabled)
    {
        return;
    }
    mac.SetMultiUserScheduler("ns3::RrMultiUserScheduler",
                              "EnableUlOfdma",
                              BooleanValue(true),
                              "EnableBsrp",
                              BooleanValue(bsrpEnabled),
                              "AccessReqInterval",
                              TimeValue(MilliSeconds(accessIntervalMs)),
                              "NStations",
                              UintegerValue(maxStations),
                              "UlPsduSize",
                              UintegerValue(psduSize));
}

struct OfdmaTelemetry
{
    uint64_t triggerFrames{0};
    uint64_t basicTriggerFrames{0};
    uint64_t bsrpTriggerFrames{0};
    uint64_t ruGrants{0};
    uint64_t tbPpdusTransmitted{0};
    uint64_t tbBytesTransmitted{0};
    uint64_t tbMpdusReceived{0};
    uint64_t tbBytesReceived{0};
};

bool
IsTbPreamble(WifiPreamble preamble)
{
    return preamble == WIFI_PREAMBLE_HE_TB || preamble == WIFI_PREAMBLE_EHT_TB;
}

void
CountOfdmaPhyTx(OfdmaTelemetry* telemetry,
                WifiConstPsduMap psdus,
                WifiTxVector txVector,
                double)
{
    if (IsTbPreamble(txVector.GetPreambleType()))
    {
        ++telemetry->tbPpdusTransmitted;
        for (const auto& [staId, psdu] : psdus)
        {
            (void)staId;
            telemetry->tbBytesTransmitted += psdu->GetSize();
        }
    }
    for (const auto& [staId, psdu] : psdus)
    {
        (void)staId;
        if (psdu->GetNMpdus() != 1 || !psdu->GetHeader(0).IsTrigger())
        {
            continue;
        }
        CtrlTriggerHeader trigger;
        psdu->GetPayload(0)->PeekHeader(trigger);
        ++telemetry->triggerFrames;
        telemetry->ruGrants += trigger.GetNUserInfoFields();
        if (trigger.GetType() == TriggerFrameType::BASIC_TRIGGER)
        {
            ++telemetry->basicTriggerFrames;
        }
        else if (trigger.GetType() == TriggerFrameType::BSRP_TRIGGER)
        {
            ++telemetry->bsrpTriggerFrames;
        }
    }
}

void
CountOfdmaPhyRx(OfdmaTelemetry* telemetry,
                Ptr<const Packet> packet,
                uint16_t,
                WifiTxVector txVector,
                MpduInfo,
                SignalNoiseDbm,
                uint16_t)
{
    if (IsTbPreamble(txVector.GetPreambleType()))
    {
        ++telemetry->tbMpdusReceived;
        telemetry->tbBytesReceived += packet->GetSize();
    }
}

void
ConnectOfdmaTelemetry(const NetDeviceContainer& devices, OfdmaTelemetry* telemetry)
{
    for (uint32_t index = 0; index < devices.GetN(); ++index)
    {
        Ptr<WifiNetDevice> wifiDevice = DynamicCast<WifiNetDevice>(devices.Get(index));
        for (uint8_t phyId = 0; phyId < wifiDevice->GetNPhys(); ++phyId)
        {
            wifiDevice->GetPhy(phyId)->TraceConnectWithoutContext(
                "PhyTxPsduBegin",
                MakeBoundCallback(&CountOfdmaPhyTx, telemetry));
            wifiDevice->GetPhy(phyId)->TraceConnectWithoutContext(
                "MonitorSnifferRx",
                MakeBoundCallback(&CountOfdmaPhyRx, telemetry));
        }
    }
}

void
WriteOfdmaTelemetry(const std::string& outputDir,
                    const OfdmaTelemetry& target,
                    const OfdmaTelemetry& sameBss,
                    const OfdmaTelemetry& obss)
{
    std::ofstream output(std::filesystem::path(outputDir) / "ofdma_summary.csv",
                         std::ios::out | std::ios::trunc);
    NS_ABORT_MSG_IF(!output, "Cannot write OFDMA telemetry");
    output << "device_group,trigger_frames,basic_trigger_frames,bsrp_trigger_frames,"
              "ru_grants,tb_ppdus_transmitted,tb_bytes_transmitted,tb_mpdus_received,"
              "tb_bytes_received\n";
    const auto write = [&output](const std::string& name, const OfdmaTelemetry& record) {
        output << name << ',' << record.triggerFrames << ',' << record.basicTriggerFrames << ','
               << record.bsrpTriggerFrames << ',' << record.ruGrants << ','
               << record.tbPpdusTransmitted << ',' << record.tbBytesTransmitted << ','
               << record.tbMpdusReceived << ',' << record.tbBytesReceived << '\n';
    };
    write("target", target);
    write("same_bss_background", sameBss);
    write("obss", obss);
}

} // namespace

int
main(int argc, char* argv[])
{
    uint32_t seed = 1;
    uint64_t run = 1;
    double durationSeconds = 1.0;
    double fps = 30.0;
    uint32_t frameSize = 12000;
    uint32_t gopLength = 60;
    double keyframeSizeMultiplier = 4.0;
    uint32_t payloadSize = 1200;
    uint32_t deadlineUs = 33333;
    double fixedRssDbm = -50.0;
    double stationDistanceM = 10.0;
    std::string propagationModel = "fixed_rss";
    double pathLossExponent = 3.0;
    double referenceLoss2GhzDb = 40.046;
    double referenceLoss5GhzDb = 46.678;
    double nakagamiDistance1M = 80.0;
    double nakagamiDistance2M = 200.0;
    double nakagamiM0 = 1.5;
    double nakagamiM1 = 0.75;
    double nakagamiM2 = 0.75;
    int64_t propagationStreamBase = 5000;
    std::string emissionMode = "burst";
    std::string sourceName = "synthetic";
    std::string traceFile;
    std::string topology = "single_link";
    std::string policyName = "fixed_link_0";
    double staticLink0Score = 0.0;
    double staticLink1Score = 1.0;
    std::string outputDir;
    std::string framesFile;
    std::string decisionsFile;
    std::string projectGitCommit;
    std::string runId = "single-link";
    bool configurationCheckOnly = false;
    bool predictionTelemetryEnabled = false;
    std::string predictionSampleOffsetsUs = "0,1000,2000,4000";
    std::string predictionHistoryWindowsUs = "1000,5000,20000";
    uint64_t predictionPollingIntervalUs = 1000;
    uint64_t predictionPollingReportDelayUs = 1000;
    bool predictionEventLogEnabled = false;
    bool predictionOracleFeaturesEnabled = false;
    std::string pairedValueT2AdmissionProfile = "baseline_v1";
    std::string pairedTemporalT2FrameProfile = "canonical_v1";
    double selectiveDuplicationThreshold = 0.2;
    double selectiveDuplicationFrameBudget = 0.3;
    uint32_t selectiveDuplicationBurstHorizonFrames = 30;
    std::string selectiveDuplicationDecisionOffsetsUs = "0";
    bool secondaryAirtimeMeterEnabled = false;
    double adaptiveAirtimeBudgetFraction = 0.02;
    uint64_t adaptiveAirtimeBucketHorizonUs = 1000000;
    uint64_t adaptiveAirtimeInitialBucketHorizonUs = 0;
    double adaptiveAirtimeInitialShadowPrice = 0.20;
    double adaptiveAirtimeDualStep = 0.01;
    bool adaptiveAirtimeAdmissionUsesRetryInflation = true;
    std::string adaptiveAirtimeAdmissionPacketCost = "launched_packet_set";
    double adaptiveAirtimeCostSafetyFactor = 1.25;
    double adaptiveAirtimeCostEwmaAlpha = 0.10;
    std::string adaptiveAirtimeDecisionOffsetsUs = "0,4000";
    std::string adaptiveAirtimeDecisionOffsetShadowPrices;
    std::string adaptiveAirtimeIFrameOnlyDecisionOffsetsUs;
    uint64_t randomizedAssignmentSalt{0};
    double randomizedT2Probability{0.08};
    double randomizedT4Probability{0.12};
    uint64_t randomizedAssignmentStopGuardUs{534000};
    bool mechanismTelemetryEnabled{false};
    std::string mechanismOraclePacketOutcomeFile;
    uint32_t mechanismSystematicRepairDivisor{8};
    uint32_t fullDuplicationPrimaryPath = 0;
    uint32_t queueMaxPackets = 500;
    uint32_t queueMaxDelayMs = 500;
    uint32_t maxAmpduSize = 65535;
    uint32_t maxAmsduSize = 0;
    uint32_t frameRetryLimit = 7;
    uint32_t txopLimitUs = 0;
    uint32_t rtsCtsThreshold = 4692480;
    uint32_t fragmentationThreshold = 65535;
    uint32_t guardIntervalNs = 800;
    constexpr uint32_t expectedMacServiceOverheadBytes = 36;
    uint32_t mloStaMaxInflights = 1;
    bool ulOfdmaEnabled = false;
    std::string ulOfdmaScope = "all_he_eht_aps";
    uint32_t ulOfdmaAccessIntervalMs = 20;
    bool ulOfdmaBsrpEnabled = true;
    uint32_t ulOfdmaMaxStations = 4;
    uint32_t ulOfdmaPsduSize = 1200;
    std::string wifiStandard = "eht";
    std::string backgroundStandard0 = "inherit";
    std::string backgroundStandard1 = "inherit";
    std::string backgroundProfile = "none";
    std::string backgroundTraffic = "none";
    std::string backgroundDirection = "uplink";
    std::string correlationMode = "independent";
    std::string correlationTrace;
    int32_t backgroundStations0Option = -1;
    int32_t backgroundStations1Option = -1;
    double backgroundRateMbps = 20.0;
    uint32_t backgroundPacketSize = 1200;
    double backgroundNearDistanceM = 2.0;
    double backgroundFarDistanceM = 15.0;
    int64_t backgroundStreamBase = 1000;
    double commonOnMeanMs = 100;
    double commonOffMeanMs = 100;
    double localOnMeanMs = 100;
    double localOffMeanMs = 100;
    double commonOnDurationMs = 0;
    double commonOffDurationMs = 0;
    double localOnDurationMs = 0;
    double localOffDurationMs = 0;
    double randomOnMeanMs = 100;
    double randomOffMeanMs = 100;
    std::string obssProfile = "none";
    uint32_t obssStationsPerBss = 4;
    double obssUlMinRateMbps = 0.5;
    double obssUlMaxRateMbps = 3.0;
    double obssDlMinRateMbps = 2.0;
    double obssDlMaxRateMbps = 8.0;
    double obssOnMeanMs = 100.0;
    double obssOffMeanMs = 300.0;
    std::string obssStationManager = "minstrel_ht";
    double obssManagerUpdateMs = 50.0;
    uint32_t obssPacketSize = 1200;
    double obssAreaMinXM = -15.0;
    double obssAreaMaxXM = 15.0;
    double obssAreaMinYM = -10.0;
    double obssAreaMaxYM = 10.0;
    double obssStaMinDistanceM = 2.0;
    double obssStaMaxDistanceM = 6.0;
    int64_t obssPlacementStreamBase = 6000;
    int64_t obssApplicationStreamBase = 7000;
    int64_t obssWifiStreamBase = 8000;

    CommandLine command(__FILE__);
    command.AddValue("seed", "ns-3 random seed", seed);
    command.AddValue("run", "ns-3 random run/substream", run);
    command.AddValue("duration", "Frame source duration in seconds", durationSeconds);
    command.AddValue("fps", "Synthetic frame rate", fps);
    command.AddValue("frameSize", "Synthetic interframe size in bytes", frameSize);
    command.AddValue("gopLength", "Synthetic frames per GOP", gopLength);
    command.AddValue("keyframeSizeMultiplier",
                     "Synthetic I-frame size relative to interframes",
                     keyframeSizeMultiplier);
    command.AddValue("payloadSize", "Streaming payload bytes per UDP datagram", payloadSize);
    command.AddValue("deadlineUs", "Frame deadline in microseconds", deadlineUs);
    command.AddValue("fixedRssDbm", "Fixed received signal strength in dBm", fixedRssDbm);
    command.AddValue("stationDistanceM", "Streaming STA distance from AP", stationDistanceM);
    command.AddValue("propagationModel",
                     "fixed_rss or log_distance_nakagami",
                     propagationModel);
    command.AddValue("pathLossExponent", "Log-distance path-loss exponent", pathLossExponent);
    command.AddValue("referenceLoss2GhzDb",
                     "Log-distance reference loss at 1 m for 2.4 GHz",
                     referenceLoss2GhzDb);
    command.AddValue("referenceLoss5GhzDb",
                     "Log-distance reference loss at 1 m for 5 GHz",
                     referenceLoss5GhzDb);
    command.AddValue("nakagamiDistance1M", "First Nakagami distance boundary", nakagamiDistance1M);
    command.AddValue("nakagamiDistance2M", "Second Nakagami distance boundary", nakagamiDistance2M);
    command.AddValue("nakagamiM0", "Near-field Nakagami m", nakagamiM0);
    command.AddValue("nakagamiM1", "Middle-field Nakagami m", nakagamiM1);
    command.AddValue("nakagamiM2", "Far-field Nakagami m", nakagamiM2);
    command.AddValue("propagationStreamBase",
                     "First deterministic stream for propagation fading",
                     propagationStreamBase);
    command.AddValue("emissionMode", "burst or uniform_within_frame", emissionMode);
    command.AddValue("source", "Frame source: synthetic or trace", sourceName);
    command.AddValue("traceFile", "Frame trace CSV required when source=trace", traceFile);
    command.AddValue("topology",
                     "single_link, dual_interface, mlo_str, or mlo_emlsr",
                     topology);
    command.AddValue("policy",
                     "fixed_link_0, fixed_link_1, static_best, full_duplication, "
                     "selective_duplication, adaptive_airtime_duplication, "
                     "adaptive_deficit_duplication, randomized_full_copy_exploration, "
                     "paired_value_duplication_t2, or "
                     "distributional_shadow_duplication_t2, mechanism_full_copy_t2, "
                     "mechanism_oracle_repair_t2, or mechanism_systematic_fec_t2",
                     policyName);
    command.AddValue("staticLink0Score",
                     "Static link 0 score (lower is better)",
                     staticLink0Score);
    command.AddValue("staticLink1Score",
                     "Static link 1 score (lower is better)",
                     staticLink1Score);
    command.AddValue("outputDir", "Required empty/new run output directory", outputDir);
    command.AddValue("framesFile", "Optional legacy copy of frames.csv", framesFile);
    command.AddValue("decisionsFile", "Optional legacy copy of policy_decisions.csv", decisionsFile);
    command.AddValue("projectGitCommit",
                     "Project commit (defaults to build-time repository commit)",
                     projectGitCommit);
    command.AddValue("runId", "Run identifier stored in CSV output", runId);
    command.AddValue("configurationCheckOnly",
                     "Validate the complete configuration and exit before creating output",
                     configurationCheckOnly);
    command.AddValue("predictionTelemetryEnabled",
                     "Enable passive frame-aligned prediction telemetry",
                     predictionTelemetryEnabled);
    command.AddValue("predictionSampleOffsetsUs",
                     "Strict comma-separated prediction sample offsets",
                     predictionSampleOffsetsUs);
    command.AddValue("predictionHistoryWindowsUs",
                     "Strict comma-separated prediction history windows",
                     predictionHistoryWindowsUs);
    command.AddValue("predictionPollingIntervalUs",
                     "Frame-independent prediction polling interval",
                     predictionPollingIntervalUs);
    command.AddValue("predictionPollingReportDelayUs",
                     "Prediction polling report availability delay",
                     predictionPollingReportDelayUs);
    command.AddValue("predictionEventLogEnabled",
                     "Write optional raw prediction event CSV",
                     predictionEventLogEnabled);
    command.AddValue("predictionOracleFeaturesEnabled",
                     "Populate causal ns-3 F3 current-state fields",
                     predictionOracleFeaturesEnabled);
    command.AddValue("pairedValueT2AdmissionProfile",
                     "Paired-value admission profile: baseline_v1 or "
                     "score_aware_emergency_v2 or score_aware_full_horizon_v3",
                     pairedValueT2AdmissionProfile);
    command.AddValue("pairedTemporalT2FrameProfile",
                     "Paired temporal-T2 frame profile: canonical_v1 or "
                     "environment_generalization_v1",
                     pairedTemporalT2FrameProfile);
    command.AddValue("selectiveDuplicationThreshold",
                     "Calibrated miss-probability action threshold",
                     selectiveDuplicationThreshold);
    command.AddValue("selectiveDuplicationFrameBudget",
                     "Frame tokens refilled per generated frame",
                     selectiveDuplicationFrameBudget);
    command.AddValue("selectiveDuplicationBurstHorizonFrames",
                     "Frame horizon used to size the action token bucket",
                     selectiveDuplicationBurstHorizonFrames);
    command.AddValue("selectiveDuplicationDecisionOffsetsUs",
                     "Strict comma-separated offsets eligible for selective action",
                     selectiveDuplicationDecisionOffsetsUs);
    command.AddValue("secondaryAirtimeMeterEnabled",
                     "Enable passive tagged secondary PHY TX airtime metering",
                     secondaryAirtimeMeterEnabled);
    command.AddValue("adaptiveAirtimeBudgetFraction",
                     "Long-run secondary PHY TX airtime budget fraction",
                     adaptiveAirtimeBudgetFraction);
    command.AddValue("adaptiveAirtimeBucketHorizonUs",
                     "Adaptive airtime token-bucket horizon in microseconds",
                     adaptiveAirtimeBucketHorizonUs);
    command.AddValue("adaptiveAirtimeInitialBucketHorizonUs",
                     "Initial credit horizon in microseconds; zero uses the bucket horizon",
                     adaptiveAirtimeInitialBucketHorizonUs);
    command.AddValue("adaptiveAirtimeInitialShadowPrice",
                     "Initial adaptive airtime shadow price",
                     adaptiveAirtimeInitialShadowPrice);
    command.AddValue("adaptiveAirtimeDualStep",
                     "Adaptive airtime dual-update step size",
                     adaptiveAirtimeDualStep);
    command.AddValue("adaptiveAirtimeAdmissionUsesRetryInflation",
                     "Use retry-inflated airtime for admission pricing",
                     adaptiveAirtimeAdmissionUsesRetryInflation);
    command.AddValue("adaptiveAirtimeAdmissionPacketCost",
                     "Admission packet-cost basis: launched_packet_set or whole_copy",
                     adaptiveAirtimeAdmissionPacketCost);
    command.AddValue("adaptiveAirtimeCostSafetyFactor",
                     "Pre-launch secondary airtime cost safety factor",
                     adaptiveAirtimeCostSafetyFactor);
    command.AddValue("adaptiveAirtimeCostEwmaAlpha",
                     "EWMA alpha for secondary airtime retry inflation",
                     adaptiveAirtimeCostEwmaAlpha);
    command.AddValue("adaptiveAirtimeDecisionOffsetsUs",
                     "Strict comma-separated offsets eligible for adaptive action",
                     adaptiveAirtimeDecisionOffsetsUs);
    command.AddValue("adaptiveAirtimeDecisionOffsetShadowPrices",
                     "Strict offset:price overrides for adaptive admission",
                     adaptiveAirtimeDecisionOffsetShadowPrices);
    command.AddValue("adaptiveAirtimeIFrameOnlyDecisionOffsetsUs",
                     "Strict offsets at which adaptive action is limited to I-frames",
                     adaptiveAirtimeIFrameOnlyDecisionOffsetsUs);
    command.AddValue("randomizedAssignmentSalt",
                     "Explicit deterministic randomized-intervention salt",
                     randomizedAssignmentSalt);
    command.AddValue("randomizedT2Probability",
                     "Frame probability for randomized full-copy T2",
                     randomizedT2Probability);
    command.AddValue("randomizedT4Probability",
                     "Frame probability for randomized full-copy T4",
                     randomizedT4Probability);
    command.AddValue("randomizedAssignmentStopGuardUs",
                     "Guard before measurement stop for randomized assignment",
                     randomizedAssignmentStopGuardUs);
    command.AddValue("mechanismTelemetryEnabled",
                     "Record paired primary/secondary T2 mechanism telemetry",
                     mechanismTelemetryEnabled);
    command.AddValue("mechanismOraclePacketOutcomeFile",
                     "Paired primary-only packet outcomes for privileged oracle replay",
                     mechanismOraclePacketOutcomeFile);
    command.AddValue("mechanismSystematicRepairDivisor",
                     "Send ceil(source packets / divisor) ideal T2 repair symbols",
                     mechanismSystematicRepairDivisor);
    command.AddValue("fullDuplicationPrimaryPath",
                     "Primary path for unconditional application duplication",
                     fullDuplicationPrimaryPath);
    command.AddValue("queueMaxPackets", "MAC queue maximum packets", queueMaxPackets);
    command.AddValue("queueMaxDelayMs", "MAC queue maximum delay", queueMaxDelayMs);
    command.AddValue("maxAmpduSize", "BE A-MPDU maximum bytes (0 disables)", maxAmpduSize);
    command.AddValue("maxAmsduSize", "BE A-MSDU maximum bytes (0 disables)", maxAmsduSize);
    command.AddValue("frameRetryLimit", "MAC frame transmission attempt limit", frameRetryLimit);
    command.AddValue("txopLimitUs", "BE TXOP limit in microseconds", txopLimitUs);
    command.AddValue("rtsCtsThreshold", "RTS/CTS PSDU threshold bytes", rtsCtsThreshold);
    command.AddValue("fragmentationThreshold",
                     "Fragmentation PSDU threshold bytes",
                     fragmentationThreshold);
    command.AddValue("guardIntervalNs", "HE guard interval in nanoseconds", guardIntervalNs);
    command.AddValue("mloStaMaxInflights",
                     "Maximum links carrying one uplink MPDU concurrently",
                     mloStaMaxInflights);
    command.AddValue("ulOfdmaEnabled", "Enable AP-triggered uplink OFDMA", ulOfdmaEnabled);
    command.AddValue("ulOfdmaScope",
                     "UL OFDMA AP scope: target_aps or all_he_eht_aps",
                     ulOfdmaScope);
    command.AddValue("ulOfdmaAccessIntervalMs",
                     "Periodic UL OFDMA channel-access request interval",
                     ulOfdmaAccessIntervalMs);
    command.AddValue("ulOfdmaBsrpEnabled",
                     "Send buffer-status-report poll triggers before UL OFDMA",
                     ulOfdmaBsrpEnabled);
    command.AddValue("ulOfdmaMaxStations",
                     "Maximum stations allocated resource units",
                     ulOfdmaMaxStations);
    command.AddValue("ulOfdmaPsduSize",
                     "Fallback solicited UL PSDU size in bytes",
                     ulOfdmaPsduSize);
    command.AddValue("wifiStandard", "ht, vht, he, or eht (fixed PHY rate)", wifiStandard);
    command.AddValue("backgroundProfile",
                     "none or legacy_mixed8 deterministic contention profile",
                     backgroundProfile);
    command.AddValue("backgroundStandard0",
                     "Link-0 background standard: inherit, ht, vht, he, or eht",
                     backgroundStandard0);
    command.AddValue("backgroundStandard1",
                     "Link-1 background standard: inherit, ht, vht, he, or eht",
                     backgroundStandard1);
    command.AddValue("backgroundTraffic",
                     "none, udp_constant, udp_bursty, udp_random_onoff, or tcp_bulk",
                     backgroundTraffic);
    command.AddValue("backgroundDirection",
                     "uplink, downlink, or mixed (alternates stations)",
                     backgroundDirection);
    command.AddValue("backgroundStations0",
                     "Background stations on link 0",
                     backgroundStations0Option);
    command.AddValue("backgroundStations1",
                     "Background stations on link 1",
                     backgroundStations1Option);
    command.AddValue("backgroundRateMbps",
                     "UDP offered rate per background station",
                     backgroundRateMbps);
    command.AddValue("backgroundPacketSize",
                     "Background UDP payload bytes",
                     backgroundPacketSize);
    command.AddValue("backgroundNearDistanceM",
                     "Near background-station placement distance",
                     backgroundNearDistanceM);
    command.AddValue("backgroundFarDistanceM",
                     "Far background-station placement distance",
                     backgroundFarDistanceM);
    command.AddValue("correlationMode",
                     "independent, common_bursts, mixed_common_and_independent, or trace_replay",
                     correlationMode);
    command.AddValue("correlationTrace",
                     "timestamp_s,link,on CSV for trace_replay",
                     correlationTrace);
    command.AddValue("backgroundStreamBase",
                     "First deterministic random stream for load controller",
                     backgroundStreamBase);
    command.AddValue("commonOnMeanMs", "Common exponential ON mean", commonOnMeanMs);
    command.AddValue("commonOffMeanMs", "Common exponential OFF mean", commonOffMeanMs);
    command.AddValue("localOnMeanMs", "Per-link exponential ON mean", localOnMeanMs);
    command.AddValue("localOffMeanMs", "Per-link exponential OFF mean", localOffMeanMs);
    command.AddValue("commonOnDurationMs",
                     "Common deterministic ON duration; zero selects exponential",
                     commonOnDurationMs);
    command.AddValue("commonOffDurationMs",
                     "Common deterministic OFF duration; zero selects exponential",
                     commonOffDurationMs);
    command.AddValue("localOnDurationMs",
                     "Per-link deterministic ON duration; zero selects exponential",
                     localOnDurationMs);
    command.AddValue("localOffDurationMs",
                     "Per-link deterministic OFF duration; zero selects exponential",
                     localOffDurationMs);
    command.AddValue("randomOnMeanMs",
                     "Per-station exponential ON mean for udp_random_onoff",
                     randomOnMeanMs);
    command.AddValue("randomOffMeanMs",
                     "Per-station exponential OFF mean for udp_random_onoff",
                     randomOffMeanMs);
    command.AddValue("obssProfile", "none or mixed4x4 overlapping-BSS profile", obssProfile);
    command.AddValue("obssStationsPerBss", "Stations in each overlapping BSS", obssStationsPerBss);
    command.AddValue("obssUlMinRateMbps", "Minimum OBSS uplink ON-period rate", obssUlMinRateMbps);
    command.AddValue("obssUlMaxRateMbps", "Maximum OBSS uplink ON-period rate", obssUlMaxRateMbps);
    command.AddValue("obssDlMinRateMbps",
                     "Minimum OBSS downlink ON-period rate",
                     obssDlMinRateMbps);
    command.AddValue("obssDlMaxRateMbps",
                     "Maximum OBSS downlink ON-period rate",
                     obssDlMaxRateMbps);
    command.AddValue("obssOnMeanMs", "Mean OBSS ON duration", obssOnMeanMs);
    command.AddValue("obssOffMeanMs", "Mean OBSS OFF duration", obssOffMeanMs);
    command.AddValue("obssStationManager",
                     "OBSS station manager: minstrel_ht, ideal, or constant",
                     obssStationManager);
    command.AddValue("obssManagerUpdateMs",
                     "Minstrel-HT statistics update interval",
                     obssManagerUpdateMs);
    command.AddValue("obssPacketSize", "OBSS UDP payload bytes", obssPacketSize);
    command.AddValue("obssAreaMinXM", "Minimum OBSS AP x coordinate", obssAreaMinXM);
    command.AddValue("obssAreaMaxXM", "Maximum OBSS AP x coordinate", obssAreaMaxXM);
    command.AddValue("obssAreaMinYM", "Minimum OBSS AP y coordinate", obssAreaMinYM);
    command.AddValue("obssAreaMaxYM", "Maximum OBSS AP y coordinate", obssAreaMaxYM);
    command.AddValue("obssStaMinDistanceM",
                     "Minimum OBSS STA radius from its AP",
                     obssStaMinDistanceM);
    command.AddValue("obssStaMaxDistanceM",
                     "Maximum OBSS STA radius from its AP",
                     obssStaMaxDistanceM);
    command.AddValue("obssPlacementStreamBase",
                     "First deterministic OBSS placement stream",
                     obssPlacementStreamBase);
    command.AddValue("obssApplicationStreamBase",
                     "First deterministic OBSS application stream",
                     obssApplicationStreamBase);
    command.AddValue("obssWifiStreamBase",
                     "First deterministic OBSS Wi-Fi stream",
                     obssWifiStreamBase);
    command.Parse(argc, argv);
    NS_ABORT_MSG_IF(seed == 0, "seed must be positive");
    NS_ABORT_MSG_IF(run == 0, "run must be positive");
    NS_ABORT_MSG_IF((predictionEventLogEnabled || predictionOracleFeaturesEnabled) &&
                        !predictionTelemetryEnabled,
                    "Prediction event and oracle options require prediction telemetry");
    std::vector<uint64_t> resolvedPredictionSampleOffsetsUs;
    std::vector<uint64_t> resolvedPredictionHistoryWindowsUs;
    std::vector<uint64_t> resolvedSelectiveDecisionOffsetsUs;
    std::vector<uint64_t> resolvedAdaptiveDecisionOffsetsUs;
    std::map<uint64_t, double> resolvedAdaptiveDecisionOffsetShadowPrices;
    std::vector<uint64_t> resolvedAdaptiveIFrameOnlyDecisionOffsetsUs;
    AdaptiveAdmissionPacketCost resolvedAdaptiveAdmissionPacketCost =
        AdaptiveAdmissionPacketCost::LAUNCHED_PACKET_SET;
    const auto resolvedPairedValueT2AdmissionProfile =
        PairedValueT2Controller::ParseAdmissionProfile(
            pairedValueT2AdmissionProfile);
    NS_ABORT_MSG_IF(!resolvedPairedValueT2AdmissionProfile,
                    "Unknown pairedValueT2AdmissionProfile "
                        << pairedValueT2AdmissionProfile);
    const bool pairedValueT2Control =
        policyName == "paired_value_duplication_t2";
    const bool distributionalShadowT2Control =
        policyName == "distributional_shadow_duplication_t2";
    const bool pairedTemporalT2Control =
        pairedValueT2Control || distributionalShadowT2Control;
    const bool mechanismT2Control = IsMechanismT2Policy(policyName);
    const bool mechanismObservation = mechanismTelemetryEnabled || mechanismT2Control;
    const bool environmentGeneralizationT2FrameProfile =
        pairedTemporalT2FrameProfile == "environment_generalization_v1";
    NS_ABORT_MSG_IF(pairedTemporalT2FrameProfile != "canonical_v1" &&
                        !environmentGeneralizationT2FrameProfile,
                    "Unknown pairedTemporalT2FrameProfile "
                        << pairedTemporalT2FrameProfile);
    NS_ABORT_MSG_IF(!pairedTemporalT2Control &&
                        pairedTemporalT2FrameProfile != "canonical_v1",
                    "A noncanonical paired temporal-T2 frame profile requires a paired "
                    "temporal-T2 policy");
    const uint64_t resolvedAdaptiveAirtimeInitialBucketHorizonUs =
        adaptiveAirtimeInitialBucketHorizonUs == 0
            ? adaptiveAirtimeBucketHorizonUs
            : adaptiveAirtimeInitialBucketHorizonUs;
    if (predictionTelemetryEnabled)
    {
        NS_ABORT_MSG_IF(predictionPollingIntervalUs == 0,
                        "predictionPollingIntervalUs must be positive");
        resolvedPredictionSampleOffsetsUs =
            ParseStrictUintList(predictionSampleOffsetsUs,
                                "predictionSampleOffsetsUs",
                                true);
        resolvedPredictionHistoryWindowsUs =
            ParseStrictUintList(predictionHistoryWindowsUs,
                                "predictionHistoryWindowsUs",
                                false);
        NS_ABORT_MSG_IF(resolvedPredictionSampleOffsetsUs.front() != 0,
                        "predictionSampleOffsetsUs must start with zero");
        NS_ABORT_MSG_IF(resolvedPredictionSampleOffsetsUs.back() >= deadlineUs,
                        "Every prediction sample offset must precede the frame deadline");
        NS_ABORT_MSG_IF(topology != "dual_interface" ||
                            (policyName != "fixed_link_0" && policyName != "fixed_link_1" &&
                             policyName != "selective_duplication" &&
                             policyName != "adaptive_airtime_duplication" &&
                             policyName != "adaptive_deficit_duplication" &&
                             policyName != "randomized_full_copy_exploration" &&
                             policyName != "full_duplication" &&
                             !pairedTemporalT2Control && !mechanismT2Control) ||
                            wifiStandard != "eht" || ulOfdmaEnabled || maxAmsduSize != 0 ||
                            fragmentationThreshold != 65535,
                        "Prediction telemetry requires dual_interface, a fixed-link or "
                        "selective/adaptive/randomized/mechanism policy, EHT, disabled UL "
                        "OFDMA/A-MSDU, and "
                        "disabled fragmentation");
    }
    if (policyName == "selective_duplication")
    {
        NS_ABORT_MSG_IF(!predictionTelemetryEnabled,
                        "Selective duplication requires prediction telemetry");
        NS_ABORT_MSG_IF(emissionMode != "burst",
                        "Selective duplication currently requires burst emission");
        NS_ABORT_MSG_IF(!std::isfinite(selectiveDuplicationThreshold) ||
                            selectiveDuplicationThreshold < 0 ||
                            selectiveDuplicationThreshold > 1,
                        "Selective duplication threshold must be in [0,1]");
        NS_ABORT_MSG_IF(!std::isfinite(selectiveDuplicationFrameBudget) ||
                            selectiveDuplicationFrameBudget <= 0 ||
                            selectiveDuplicationFrameBudget > 1 ||
                            selectiveDuplicationBurstHorizonFrames == 0,
                        "Selective duplication requires budget in (0,1] and positive horizon");
        resolvedSelectiveDecisionOffsetsUs =
            ParseStrictUintList(selectiveDuplicationDecisionOffsetsUs,
                                "selectiveDuplicationDecisionOffsetsUs",
                                true);
        NS_ABORT_MSG_IF(resolvedSelectiveDecisionOffsetsUs.front() != 0,
                        "Selective duplication decision offsets must start with zero");
        NS_ABORT_MSG_IF(IsPrimaryRiskT0Model() &&
                            resolvedSelectiveDecisionOffsetsUs.size() != 1,
                        "Primary-risk T0 model only supports decision offset 0");
        for (const auto offset : resolvedSelectiveDecisionOffsetsUs)
        {
            NS_ABORT_MSG_IF(std::find(resolvedPredictionSampleOffsetsUs.begin(),
                                      resolvedPredictionSampleOffsetsUs.end(),
                                      offset) == resolvedPredictionSampleOffsetsUs.end(),
                            "Every selective decision offset must be a prediction sample offset");
            NS_ABORT_MSG_IF(offset != 0 && offset != 1000 && offset != 2000 && offset != 4000,
                            "Frozen selective predictor supports only T0, T1, T2, and T4");
        }
    }
    if (policyName == "adaptive_airtime_duplication" ||
        policyName == "adaptive_deficit_duplication")
    {
        NS_ABORT_MSG_IF(!predictionTelemetryEnabled,
                        "Adaptive airtime duplication requires prediction telemetry");
        NS_ABORT_MSG_IF(emissionMode != "burst",
                        "Adaptive airtime duplication currently requires burst emission");
        NS_ABORT_MSG_IF(!secondaryAirtimeMeterEnabled,
                        "Adaptive airtime duplication requires the secondary airtime meter");
        NS_ABORT_MSG_IF(!std::isfinite(adaptiveAirtimeBudgetFraction) ||
                            adaptiveAirtimeBudgetFraction <= 0 ||
                            adaptiveAirtimeBudgetFraction > 1,
                        "Adaptive airtime budget fraction must be in (0,1]");
        NS_ABORT_MSG_IF(adaptiveAirtimeBucketHorizonUs == 0,
                        "Adaptive airtime bucket horizon must be positive");
        NS_ABORT_MSG_IF(resolvedAdaptiveAirtimeInitialBucketHorizonUs == 0 ||
                            resolvedAdaptiveAirtimeInitialBucketHorizonUs >
                                adaptiveAirtimeBucketHorizonUs,
                        "Adaptive airtime initial bucket horizon must be positive and no "
                        "larger than the bucket horizon");
        NS_ABORT_MSG_IF(!std::isfinite(adaptiveAirtimeInitialShadowPrice) ||
                            adaptiveAirtimeInitialShadowPrice < 0 ||
                            adaptiveAirtimeInitialShadowPrice > 1,
                        "Adaptive airtime initial shadow price must be in [0,1]");
        NS_ABORT_MSG_IF(!std::isfinite(adaptiveAirtimeDualStep) ||
                            adaptiveAirtimeDualStep < 0,
                        "Adaptive airtime dual step must be nonnegative");
        NS_ABORT_MSG_IF(!std::isfinite(adaptiveAirtimeCostSafetyFactor) ||
                            adaptiveAirtimeCostSafetyFactor < 1,
                        "Adaptive airtime cost safety factor must be >= 1");
        NS_ABORT_MSG_IF(!std::isfinite(adaptiveAirtimeCostEwmaAlpha) ||
                            adaptiveAirtimeCostEwmaAlpha <= 0 ||
                            adaptiveAirtimeCostEwmaAlpha > 1,
                        "Adaptive airtime EWMA alpha must be in (0,1]");
        resolvedAdaptiveDecisionOffsetsUs =
            ParseStrictUintList(adaptiveAirtimeDecisionOffsetsUs,
                                "adaptiveAirtimeDecisionOffsetsUs",
                                true);
        NS_ABORT_MSG_IF(resolvedAdaptiveDecisionOffsetsUs.front() != 0,
                        "Adaptive airtime decision offsets must start with zero");
        const bool stagedAdaptive = resolvedAdaptiveDecisionOffsetsUs.size() != 1;
        NS_ABORT_MSG_IF(stagedAdaptive &&
                            resolvedAdaptiveDecisionOffsetsUs !=
                                std::vector<uint64_t>({0, 4000}),
                        "Staged adaptive control requires exactly the T0 and T4 offsets");
        NS_ABORT_MSG_IF(stagedAdaptive && !SupportsStagedAdaptivePolicy(policyName),
                        "Staged adaptive control requires the exact audited T0/T4 "
                        "compiled-model identities");
        if (adaptiveAirtimeAdmissionPacketCost == "whole_copy")
        {
            resolvedAdaptiveAdmissionPacketCost = AdaptiveAdmissionPacketCost::WHOLE_COPY;
        }
        else
        {
            NS_ABORT_MSG_IF(adaptiveAirtimeAdmissionPacketCost != "launched_packet_set",
                            "adaptiveAirtimeAdmissionPacketCost must be "
                            "launched_packet_set or whole_copy");
        }
        resolvedAdaptiveDecisionOffsetShadowPrices = ParseStrictOffsetPriceMap(
            adaptiveAirtimeDecisionOffsetShadowPrices,
            "adaptiveAirtimeDecisionOffsetShadowPrices");
        if (!adaptiveAirtimeIFrameOnlyDecisionOffsetsUs.empty())
        {
            resolvedAdaptiveIFrameOnlyDecisionOffsetsUs = ParseStrictUintList(
                adaptiveAirtimeIFrameOnlyDecisionOffsetsUs,
                "adaptiveAirtimeIFrameOnlyDecisionOffsetsUs",
                true);
        }
        for (const auto offset : resolvedAdaptiveDecisionOffsetsUs)
        {
            NS_ABORT_MSG_IF(std::find(resolvedPredictionSampleOffsetsUs.begin(),
                                      resolvedPredictionSampleOffsetsUs.end(),
                                      offset) == resolvedPredictionSampleOffsetsUs.end(),
                            "Every adaptive decision offset must be a prediction sample offset");
            NS_ABORT_MSG_IF(offset != 0 && offset != 1000 && offset != 2000 && offset != 4000,
                            "Frozen adaptive predictor supports only T0, T1, T2, and T4");
        }
        for (const auto& [offset, price] : resolvedAdaptiveDecisionOffsetShadowPrices)
        {
            (void)price;
            NS_ABORT_MSG_IF(std::find(resolvedAdaptiveDecisionOffsetsUs.begin(),
                                      resolvedAdaptiveDecisionOffsetsUs.end(),
                                      offset) == resolvedAdaptiveDecisionOffsetsUs.end(),
                            "Every adaptive shadow-price override must be a decision offset");
        }
        for (const auto offset : resolvedAdaptiveIFrameOnlyDecisionOffsetsUs)
        {
            NS_ABORT_MSG_IF(std::find(resolvedAdaptiveDecisionOffsetsUs.begin(),
                                      resolvedAdaptiveDecisionOffsetsUs.end(),
                                      offset) == resolvedAdaptiveDecisionOffsetsUs.end(),
                            "Every adaptive I-frame restriction must be a decision offset");
        }
    }
    if (policyName == "randomized_full_copy_exploration")
    {
        const uint64_t minimumStopGuardUs =
            static_cast<uint64_t>(queueMaxDelayMs) * 1000 + 1000 + deadlineUs -
            RandomizedInterventionController::T4_OFFSET_US;
        const double largestFrameSize = frameSize * keyframeSizeMultiplier;
        NS_ABORT_MSG_IF(!predictionTelemetryEnabled,
                        "Randomized intervention requires prediction telemetry");
        NS_ABORT_MSG_IF(!secondaryAirtimeMeterEnabled,
                        "Randomized intervention requires the secondary airtime meter");
        NS_ABORT_MSG_IF(emissionMode != "burst",
                        "Randomized intervention currently requires burst emission");
        NS_ABORT_MSG_IF(predictionEventLogEnabled || predictionOracleFeaturesEnabled,
                        "Randomized intervention requires commodity telemetry without raw "
                        "events or oracle fields");
        NS_ABORT_MSG_IF(predictionPollingIntervalUs != 1000 ||
                            predictionPollingReportDelayUs != 1000,
                        "Randomized intervention requires genuine delayed 1 ms polling");
        NS_ABORT_MSG_IF(guardIntervalNs != 800,
                        "Randomized intervention cost provenance requires an 800 ns guard "
                        "interval");
        NS_ABORT_MSG_IF(maxAmpduSize != 65535 || txopLimitUs != 0,
                        "Randomized intervention cost provenance requires the audited 65535-byte "
                        "A-MPDU limit and zero TXOP limit");
        NS_ABORT_MSG_IF(rtsCtsThreshold != 4692480,
                        "Randomized intervention cost provenance requires disabled RTS/CTS");
        NS_ABORT_MSG_IF(sourceName != "synthetic" || payloadSize == 0 ||
                            !std::isfinite(largestFrameSize) || largestFrameSize < 1 ||
                            largestFrameSize > std::numeric_limits<uint32_t>::max(),
                        "Randomized intervention requires a bounded synthetic frame profile");
        const uint64_t largestFrameBytes = std::llround(largestFrameSize);
        const uint64_t largestFramePackets =
            1 + (largestFrameBytes - 1) / payloadSize;
        const uint64_t largestEstimatedAggregateBytes =
            largestFrameBytes +
            largestFramePackets *
                (StreamingHeader::SERIALIZED_SIZE + expectedMacServiceOverheadBytes + 38);
        NS_ABORT_MSG_IF(largestEstimatedAggregateBytes > maxAmpduSize,
                        "Randomized nominal one-PPDU estimator requires every synthetic frame "
                        "to fit the configured A-MPDU limit");
        NS_ABORT_MSG_IF(resolvedPredictionSampleOffsetsUs !=
                            std::vector<uint64_t>({0, 2000, 4000}),
                        "Randomized intervention requires exactly the T0, T2, and T4 samples");
        NS_ABORT_MSG_IF(!std::isfinite(randomizedT2Probability) ||
                            !std::isfinite(randomizedT4Probability) ||
                            randomizedT2Probability <= 0 ||
                            randomizedT4Probability <= 0 ||
                            randomizedT2Probability + randomizedT4Probability >= 1,
                        "Randomized T2 and T4 probabilities must be positive and leave a "
                        "positive control probability");
        NS_ABORT_MSG_IF(
            randomizedAssignmentStopGuardUs == 0 || !std::isfinite(durationSeconds) ||
                randomizedAssignmentStopGuardUs < minimumStopGuardUs ||
                randomizedAssignmentStopGuardUs >
                    static_cast<uint64_t>(std::numeric_limits<int64_t>::max()) / 1000 ||
                durationSeconds * 1e6 <=
                    static_cast<double>(randomizedAssignmentStopGuardUs) +
                        RandomizedInterventionController::T4_OFFSET_US,
            "Randomized assignment stop guard must cover deadline/queue settlement and leave "
            "a common intervention window");
    }
    if (mechanismObservation)
    {
        NS_ABORT_MSG_IF(topology != "dual_interface",
                        "Mechanism telemetry requires dual_interface topology");
        NS_ABORT_MSG_IF(!predictionTelemetryEnabled,
                        "Mechanism telemetry requires prediction telemetry");
        NS_ABORT_MSG_IF(policyName != "fixed_link_1" &&
                            policyName != "full_duplication" &&
                            !mechanismT2Control,
                        "Mechanism telemetry supports fixed_link_1, full_duplication, or a "
                        "mechanism T2 policy");
        NS_ABORT_MSG_IF(emissionMode != "burst",
                        "Mechanism telemetry requires burst emission");
        NS_ABORT_MSG_IF(resolvedPredictionSampleOffsetsUs !=
                            std::vector<uint64_t>({0, 2000}),
                        "Mechanism telemetry requires exactly the T0 and T2 samples");
        NS_ABORT_MSG_IF(mechanismSystematicRepairDivisor != 8,
                        "The frozen mechanism experiment requires repair divisor 8");
        NS_ABORT_MSG_IF(policyName == "full_duplication" &&
                            fullDuplicationPrimaryPath != 1,
                        "Mechanism full-copy T0 requires primary path 1");
        NS_ABORT_MSG_IF(mechanismT2Control && !secondaryAirtimeMeterEnabled,
                        "Mechanism T2 actions require the secondary airtime meter");
        NS_ABORT_MSG_IF(policyName == "mechanism_oracle_repair_t2" &&
                            mechanismOraclePacketOutcomeFile.empty(),
                        "Mechanism oracle repair requires paired packet outcomes");
        NS_ABORT_MSG_IF(policyName != "mechanism_oracle_repair_t2" &&
                            !mechanismOraclePacketOutcomeFile.empty(),
                        "Only mechanism oracle repair accepts paired packet outcomes");
    }
    if (pairedTemporalT2Control)
    {
        NS_ABORT_MSG_IF(topology != "dual_interface",
                        "Paired-value T2 control requires dual_interface topology");
        NS_ABORT_MSG_IF(!predictionTelemetryEnabled,
                        "Paired-value T2 control requires prediction telemetry");
        NS_ABORT_MSG_IF(!secondaryAirtimeMeterEnabled,
                        "Paired-value T2 control requires the secondary airtime meter");
        NS_ABORT_MSG_IF(emissionMode != "burst",
                        "Paired-value T2 control requires burst emission");
        NS_ABORT_MSG_IF(predictionEventLogEnabled || predictionOracleFeaturesEnabled,
                        "Paired-value T2 control requires commodity telemetry without raw "
                        "events or oracle fields");
        NS_ABORT_MSG_IF(resolvedPredictionSampleOffsetsUs !=
                            std::vector<uint64_t>({0, 2000}),
                        "Paired-value T2 control requires exactly the T0 and T2 samples");
        NS_ABORT_MSG_IF(resolvedPredictionHistoryWindowsUs !=
                            std::vector<uint64_t>({1000, 5000, 20000}),
                        "Paired-value T2 control requires exact 1, 5, and 20 ms histories");
        NS_ABORT_MSG_IF(predictionPollingIntervalUs != 1000 ||
                            predictionPollingReportDelayUs != 1000,
                        "Paired-value T2 control requires genuine delayed 1 ms polling");
        NS_ABORT_MSG_IF(durationSeconds != 60.0,
                        "Paired-value T2 control requires the frozen 60 s duration");
        if (!environmentGeneralizationT2FrameProfile)
        {
            NS_ABORT_MSG_IF(sourceName != "synthetic" || fps != 30.0 ||
                                frameSize != 12000 || keyframeSizeMultiplier != 4.0 ||
                                gopLength != 60 || payloadSize != 1200 ||
                                deadlineUs != 33333,
                            "Paired temporal-T2 canonical_v1 requires the frozen synthetic "
                            "frame profile");
        }
        else
        {
            const bool cadenceAndDeadlineSupported =
                (fps == 24.0 && deadlineUs == 41667) ||
                (fps == 30.0 && deadlineUs == 33333) ||
                (fps == 45.0 && deadlineUs == 22222) ||
                (fps == 60.0 && deadlineUs == 16667);
            const bool gopSupported =
                gopLength == 30 || gopLength == 60 || gopLength == 90 ||
                gopLength == 120;
            NS_ABORT_MSG_IF(sourceName != "synthetic" || !cadenceAndDeadlineSupported ||
                                frameSize < 6000 || frameSize > 14000 ||
                                frameSize % 100 != 0 || !gopSupported ||
                                !std::isfinite(keyframeSizeMultiplier) ||
                                keyframeSizeMultiplier < 2.0 ||
                                keyframeSizeMultiplier > 4.0 || payloadSize != 1200,
                            "Paired temporal-T2 environment_generalization_v1 frame profile "
                            "is outside the frozen workload domain");
            const double largestFrameSize = frameSize * keyframeSizeMultiplier;
            NS_ABORT_MSG_IF(!std::isfinite(largestFrameSize) || largestFrameSize < 1 ||
                                largestFrameSize > std::numeric_limits<uint32_t>::max(),
                            "Paired temporal-T2 environment_generalization_v1 frame size is "
                            "not bounded");
            const uint64_t largestFrameBytes = std::llround(largestFrameSize);
            const uint64_t largestFramePackets =
                1 + (largestFrameBytes - 1) / payloadSize;
            const uint64_t largestEstimatedAggregateBytes =
                largestFrameBytes +
                largestFramePackets *
                    (StreamingHeader::SERIALIZED_SIZE + expectedMacServiceOverheadBytes + 38);
            NS_ABORT_MSG_IF(largestEstimatedAggregateBytes > maxAmpduSize,
                            "Paired temporal-T2 environment_generalization_v1 requires every "
                            "synthetic frame to fit the configured A-MPDU limit");
        }
        NS_ABORT_MSG_IF(queueMaxDelayMs != 500 || maxAmpduSize != 65535 ||
                            maxAmsduSize != 0 || txopLimitUs != 0 ||
                            rtsCtsThreshold != 4692480 || fragmentationThreshold != 65535 ||
                            guardIntervalNs != 800,
                        "Paired-value T2 control requires the frozen Wi-Fi cost profile");
    }
    if (!pairedValueT2Control)
    {
        NS_ABORT_MSG_IF(
            *resolvedPairedValueT2AdmissionProfile !=
                PairedValueT2Controller::AdmissionProfile::BASELINE_V1,
            "A nonbaseline paired-value admission profile requires paired-value control");
    }
    RngSeedManager::SetSeed(seed);
    RngSeedManager::SetRun(run);
    NS_ABORT_MSG_IF(ulOfdmaScope != "target_aps" && ulOfdmaScope != "all_he_eht_aps",
                    "ulOfdmaScope must be target_aps or all_he_eht_aps");
    NS_ABORT_MSG_IF(ulOfdmaEnabled &&
                        (ulOfdmaAccessIntervalMs == 0 || ulOfdmaMaxStations == 0 ||
                         ulOfdmaMaxStations > 74 || ulOfdmaPsduSize == 0),
                    "UL OFDMA requires a positive interval and PSDU size and 1-74 stations");
    NS_ABORT_MSG_IF(backgroundProfile != "none" && backgroundProfile != "legacy_mixed8",
                    "backgroundProfile must be none or legacy_mixed8");
    NS_ABORT_MSG_IF(propagationModel != "fixed_rss" &&
                        propagationModel != "log_distance_nakagami",
                    "propagationModel must be fixed_rss or log_distance_nakagami");
    NS_ABORT_MSG_IF(pathLossExponent <= 0 || referenceLoss2GhzDb <= 0 ||
                        referenceLoss5GhzDb <= 0,
                    "Path-loss exponent and reference losses must be positive");
    NS_ABORT_MSG_IF(nakagamiDistance1M <= 0 ||
                        nakagamiDistance2M <= nakagamiDistance1M || nakagamiM0 <= 0 ||
                        nakagamiM1 <= 0 || nakagamiM2 <= 0,
                    "Nakagami distances must be ordered and m parameters positive");
    NS_ABORT_MSG_IF(propagationStreamBase < 0,
                    "propagationStreamBase cannot be negative");
    NS_ABORT_MSG_IF(obssProfile != "none" && obssProfile != "mixed4x4",
                    "obssProfile must be none or mixed4x4");
    const bool obssEnabled = obssProfile == "mixed4x4";
    NS_ABORT_MSG_IF(obssEnabled && obssStationsPerBss != 4,
                    "mixed4x4 requires obssStationsPerBss=4");
    NS_ABORT_MSG_IF(obssEnabled && propagationModel == "fixed_rss",
                    "OBSS requires log_distance_nakagami propagation");
    NS_ABORT_MSG_IF(obssEnabled && topology == "single_link",
                    "OBSS requires dual_interface or mlo_str topology");
    NS_ABORT_MSG_IF(obssUlMinRateMbps <= 0 || obssUlMaxRateMbps < obssUlMinRateMbps ||
                        obssDlMinRateMbps <= 0 || obssDlMaxRateMbps < obssDlMinRateMbps ||
                        obssOnMeanMs <= 0 || obssOffMeanMs <= 0 || obssPacketSize == 0,
                    "OBSS rates, means, and packet size must be positive");
    NS_ABORT_MSG_IF(obssStationManager != "minstrel_ht" && obssStationManager != "ideal" &&
                        obssStationManager != "constant",
                    "obssStationManager must be minstrel_ht, ideal, or constant");
    NS_ABORT_MSG_IF(obssManagerUpdateMs <= 0,
                    "obssManagerUpdateMs must be positive");
    NS_ABORT_MSG_IF(obssAreaMaxXM < obssAreaMinXM || obssAreaMaxYM < obssAreaMinYM,
                    "OBSS placement rectangle bounds are invalid");
    NS_ABORT_MSG_IF(obssStaMinDistanceM <= 0 ||
                        obssStaMaxDistanceM < obssStaMinDistanceM,
                    "OBSS STA distances must satisfy 0 < min <= max");
    NS_ABORT_MSG_IF(obssPlacementStreamBase < 0 || obssApplicationStreamBase < 0 ||
                        obssWifiStreamBase < 0,
                    "OBSS stream bases cannot be negative");
    const uint32_t linkCount = topology == "single_link" ? 1 : 2;
    if (backgroundProfile == "legacy_mixed8")
    {
        NS_ABORT_MSG_IF(backgroundStations0Option >= 0 && backgroundStations0Option != 8,
                        "legacy_mixed8 requires backgroundStations0=8 when explicitly set");
        NS_ABORT_MSG_IF(linkCount == 2 && backgroundStations1Option >= 0 &&
                            backgroundStations1Option != 8,
                        "legacy_mixed8 requires backgroundStations1=8 when explicitly set");
        NS_ABORT_MSG_IF(linkCount == 1 && backgroundStations1Option > 0,
                        "single_link cannot have backgroundStations1");
        backgroundStations0Option = 8;
        backgroundStations1Option = linkCount == 2 ? 8 : 0;
        NS_ABORT_MSG_IF(wifiStandard != "eht",
                        "legacy_mixed8 requires an EHT streaming station and AP");
        NS_ABORT_MSG_IF(backgroundStandard0 != "inherit" ||
                            backgroundStandard1 != "inherit",
                        "legacy_mixed8 defines per-station standards; backgroundStandard0/1 "
                        "must remain inherit");
        NS_ABORT_MSG_IF(backgroundDirection != "uplink",
                        "legacy_mixed8 supports uplink background traffic only");
        NS_ABORT_MSG_IF(backgroundTraffic != "none" &&
                            backgroundTraffic != "udp_random_onoff" &&
                            backgroundTraffic != "udp_bursty",
                        "legacy_mixed8 requires backgroundTraffic=udp_random_onoff or "
                        "udp_bursty");
        if (backgroundTraffic == "none")
        {
            backgroundTraffic = "udp_random_onoff";
        }
    }
    const uint32_t backgroundStations0 =
        backgroundStations0Option < 0 ? 0 : static_cast<uint32_t>(backgroundStations0Option);
    const uint32_t backgroundStations1 =
        backgroundStations1Option < 0 ? 0 : static_cast<uint32_t>(backgroundStations1Option);
    const std::string resolvedBackgroundStandard0 =
        backgroundStandard0 == "inherit" ? wifiStandard : backgroundStandard0;
    const std::string resolvedBackgroundStandard1 =
        backgroundStandard1 == "inherit" ? wifiStandard : backgroundStandard1;
    NS_ABORT_MSG_IF(topology != "single_link" && topology != "dual_interface" &&
                        topology != "mlo_str" && topology != "mlo_emlsr",
                    "Unknown topology " << topology);
    NS_ABORT_MSG_IF(policyName != "fixed_link_0" && policyName != "fixed_link_1" &&
                        policyName != "static_best" && policyName != "full_duplication" &&
                        policyName != "selective_duplication" &&
                        policyName != "adaptive_airtime_duplication" &&
                        policyName != "adaptive_deficit_duplication" &&
                        policyName != "randomized_full_copy_exploration" &&
                        policyName != "paired_value_duplication_t2" &&
                        policyName != "distributional_shadow_duplication_t2" &&
                        !IsMechanismT2Policy(policyName),
                    "Unknown policy " << policyName);
    NS_ABORT_MSG_IF(fullDuplicationPrimaryPath > 1,
                    "fullDuplicationPrimaryPath must be 0 or 1");
    NS_ABORT_MSG_IF(topology == "single_link" && policyName != "fixed_link_0",
                    "single_link supports only fixed_link_0");
    const bool nativeMloTopology = topology == "mlo_str" || topology == "mlo_emlsr";
    NS_ABORT_MSG_IF(nativeMloTopology && policyName != "fixed_link_0",
                    topology << " uses one native MLO path and supports only fixed_link_0");
    NS_ABORT_MSG_IF(nativeMloTopology && wifiStandard != "eht",
                    topology << " requires --wifiStandard=eht");
    NS_ABORT_MSG_IF(topology == "mlo_emlsr" && mloStaMaxInflights != 1,
                    "mlo_emlsr reference profile requires mloStaMaxInflights=1");
    NS_ABORT_MSG_IF(secondaryAirtimeMeterEnabled &&
                        policyName != "selective_duplication" &&
                        policyName != "adaptive_airtime_duplication" &&
                        policyName != "adaptive_deficit_duplication" &&
                        policyName != "randomized_full_copy_exploration" &&
                        policyName != "paired_value_duplication_t2" &&
                        policyName != "distributional_shadow_duplication_t2" &&
                        !IsMechanismT2Policy(policyName) &&
                        policyName != "full_duplication",
                    "Secondary airtime metering supports only selective, adaptive, randomized, "
                    "or full duplication policies");
    NS_ABORT_MSG_IF(secondaryAirtimeMeterEnabled &&
                        policyName == "full_duplication" &&
                        fullDuplicationPrimaryPath != 1,
                    "V1 full-duplication airtime metering requires primary path 1 so copy 1 "
                    "uses secondary path 0");
    NS_ABORT_MSG_IF(secondaryAirtimeMeterEnabled &&
                        (maxAmsduSize != 0 || fragmentationThreshold != 65535),
                    "Secondary airtime metering requires disabled A-MSDU and fragmentation "
                    "so packet-terminal settlement remains unambiguous");
    NS_ABORT_MSG_IF(mloStaMaxInflights < 1 || mloStaMaxInflights > 15,
                    "mloStaMaxInflights must be in [1,15]");
    NS_ABORT_MSG_IF(topology == "dual_interface" && wifiStandard != "eht",
                    "dual_interface requires --wifiStandard=eht for comparison with STR MLO");
    NS_ABORT_MSG_IF(durationSeconds <= 0, "duration must be positive");
    NS_ABORT_MSG_IF(stationDistanceM <= 0, "stationDistanceM must be positive");
    NS_ABORT_MSG_IF(sourceName != "synthetic" && sourceName != "trace",
                    "source must be synthetic or trace");
    NS_ABORT_MSG_IF(sourceName == "trace" && traceFile.empty(),
                    "source=trace requires --traceFile");
    NS_ABORT_MSG_IF(emissionMode != "burst" && emissionMode != "uniform_within_frame",
                    "Unknown emission mode " << emissionMode);
    NS_ABORT_MSG_IF(wifiStandard != "ht" && wifiStandard != "vht" && wifiStandard != "he" &&
                        wifiStandard != "eht",
                    "wifiStandard must be ht, vht, he, or eht");
    NS_ABORT_MSG_IF(resolvedBackgroundStandard0 != "ht" &&
                        resolvedBackgroundStandard0 != "vht" &&
                        resolvedBackgroundStandard0 != "he" &&
                        resolvedBackgroundStandard0 != "eht",
                    "backgroundStandard0 must be inherit, ht, vht, he, or eht");
    NS_ABORT_MSG_IF(resolvedBackgroundStandard1 != "ht" &&
                        resolvedBackgroundStandard1 != "vht" &&
                        resolvedBackgroundStandard1 != "he" &&
                        resolvedBackgroundStandard1 != "eht",
                    "backgroundStandard1 must be inherit, ht, vht, he, or eht");
    NS_ABORT_MSG_IF(backgroundStations0 > 0 && resolvedBackgroundStandard0 == "vht" &&
                        topology == "dual_interface",
                    "VHT backgroundStandard0 is invalid on the 2.4 GHz link");
    NS_ABORT_MSG_IF(backgroundStations0 > 0 &&
                        StandardRank(resolvedBackgroundStandard0) > StandardRank(wifiStandard),
                    "backgroundStandard0 cannot be newer than the AP standard");
    NS_ABORT_MSG_IF(backgroundStations1 > 0 &&
                        StandardRank(resolvedBackgroundStandard1) > StandardRank(wifiStandard),
                    "backgroundStandard1 cannot be newer than the AP standard");
    NS_ABORT_MSG_IF(backgroundTraffic != "none" && backgroundTraffic != "udp_constant" &&
                        backgroundTraffic != "udp_bursty" &&
                        backgroundTraffic != "udp_random_onoff" &&
                        backgroundTraffic != "tcp_bulk",
                    "Unknown backgroundTraffic " << backgroundTraffic);
    NS_ABORT_MSG_IF(correlationMode != "independent" &&
                        correlationMode != "common_bursts" &&
                        correlationMode != "mixed_common_and_independent" &&
                        correlationMode != "trace_replay",
                    "Unknown correlationMode " << correlationMode);
    NS_ABORT_MSG_IF(backgroundDirection != "uplink" && backgroundDirection != "downlink" &&
                        backgroundDirection != "mixed",
                    "Unknown backgroundDirection " << backgroundDirection);
    const bool heterogeneousBackground =
        backgroundProfile == "legacy_mixed8" ||
        (backgroundStations0 > 0 && resolvedBackgroundStandard0 != wifiStandard) ||
        (backgroundStations1 > 0 && resolvedBackgroundStandard1 != wifiStandard);
    NS_ABORT_MSG_IF(heterogeneousBackground && backgroundDirection != "uplink",
                    "Heterogeneous background standards support uplink only; fixed-rate AP "
                    "downlink cannot safely select a legacy station MCS");
    NS_ABORT_MSG_IF(topology == "single_link" && backgroundStations1 != 0,
                    "single_link cannot have backgroundStations1");
    NS_ABORT_MSG_IF(nativeMloTopology &&
                        backgroundProfile != "legacy_mixed8" &&
                        (backgroundTraffic != "none" || backgroundStations0 != 0 ||
                         backgroundStations1 != 0),
                    topology << " supports background traffic only through legacy_mixed8");
    NS_ABORT_MSG_IF(backgroundTraffic == "none" &&
                        (backgroundStations0 != 0 || backgroundStations1 != 0),
                    "Background station counts require non-none backgroundTraffic");
    NS_ABORT_MSG_IF(backgroundTraffic != "none" &&
                        backgroundStations0 + backgroundStations1 == 0,
                    "Background traffic requires at least one station");
    NS_ABORT_MSG_IF(backgroundRateMbps <= 0 || backgroundPacketSize == 0,
                    "Background UDP rate and packet size must be positive");
    NS_ABORT_MSG_IF(backgroundNearDistanceM <= 0 ||
                        backgroundFarDistanceM < backgroundNearDistanceM,
                    "Background distances must satisfy 0 < near <= far");
    NS_ABORT_MSG_IF(commonOnMeanMs <= 0 || commonOffMeanMs <= 0 ||
                        localOnMeanMs <= 0 || localOffMeanMs <= 0,
                    "ON/OFF means must be positive");
    NS_ABORT_MSG_IF(randomOnMeanMs <= 0 || randomOffMeanMs <= 0,
                    "Random per-station ON/OFF means must be positive");
    NS_ABORT_MSG_IF(commonOnDurationMs < 0 || commonOffDurationMs < 0 ||
                        localOnDurationMs < 0 || localOffDurationMs < 0,
                    "Deterministic ON/OFF durations cannot be negative");
    NS_ABORT_MSG_IF(backgroundTraffic != "udp_bursty" &&
                        correlationMode != "independent",
                    "Correlation modes apply only to udp_bursty background traffic");
    NS_ABORT_MSG_IF(correlationMode == "trace_replay" && correlationTrace.empty(),
                    "trace_replay requires correlationTrace");
    if (configurationCheckOnly)
    {
        return 0;
    }
    ExperimentOutput::PrepareRunDirectory(outputDir);

    NodeContainer station;
    station.Create(1);
    NodeContainer accessPoint;
    accessPoint.Create(1);
    NodeContainer edge;
    edge.Create(1);
    const bool nativeMlo = nativeMloTopology;
    const bool emlsrMlo = topology == "mlo_emlsr";
    constexpr uint8_t emlsrMainPhyId = 1;
    constexpr uint32_t emlsrPaddingDelayUs = 128;
    constexpr uint32_t emlsrTransitionDelayUs = 128;
    constexpr uint32_t emlsrTransitionTimeoutUs = 0;
    constexpr uint32_t emlsrMediumSyncDurationUs = 5472;
    constexpr int32_t emlsrMsdOfdmEdThresholdDbm = -72;
    constexpr uint8_t emlsrMsdMaxNTxops = 1;
    constexpr uint32_t emlsrChannelSwitchDelayUs = 100;
    constexpr uint32_t emlsrAuxPhyChannelWidthMhz = 20;
    constexpr uint32_t emlsrSwitchMainPhyBackDelayUs = 5000;
    constexpr uint32_t emlsrCamResetBackoffThresholdUs = 0;
    constexpr uint8_t emlsrCamNSlotsLeft = 0;
    constexpr uint32_t emlsrCamNSlotsLeftMinDelayUs = 25;
    Ptr<WifiNetDevice> targetStaMld;
    Ptr<WifiNetDevice> targetApMld;
    MloRuntimeInfo mloRuntime;
    bool mloRuntimeCaptured = false;
    std::vector<NodeContainer> backgroundStations(linkCount);
    backgroundStations[0].Create(backgroundStations0);
    if (linkCount == 2)
    {
        backgroundStations[1].Create(backgroundStations1);
    }
    std::vector<std::vector<std::string>> backgroundStationStandards(linkCount);
    for (uint32_t link = 0; link < linkCount; ++link)
    {
        if (backgroundProfile == "legacy_mixed8")
        {
            backgroundStationStandards[link] =
                LegacyMixed8Standards(link, topology == "single_link");
        }
        else
        {
            backgroundStationStandards[link].assign(
                backgroundStations[link].GetN(),
                link == 0 ? resolvedBackgroundStandard0 : resolvedBackgroundStandard1);
        }
    }

    Config::SetDefault("ns3::WifiMacQueue::MaxSize",
                       QueueSizeValue(QueueSize(std::to_string(queueMaxPackets) + "p")));
    Config::SetDefault("ns3::WifiMacQueue::MaxDelay",
                       TimeValue(MilliSeconds(queueMaxDelayMs)));

    WifiHelper wifi;
    const WifiStandard standard = ParseWifiStandard(wifiStandard);
    const std::string dataMode = DataModeForStandard(wifiStandard);
    const auto controlModeForLink = [topology](uint32_t link) {
        return topology != "single_link" && link == 0 ? "ErpOfdmRate24Mbps"
                                                       : "OfdmRate24Mbps";
    };
    wifi.SetStandard(standard);
    if (wifiStandard == "he" || wifiStandard == "eht")
    {
        wifi.ConfigHeOptions("GuardInterval", TimeValue(NanoSeconds(guardIntervalNs)));
    }
    NetDeviceContainer stationDevices;
    NetDeviceContainer apWifiDevices;
    std::vector<NetDeviceContainer> backgroundDevices(linkCount);
    std::vector<Ptr<MultiModelSpectrumChannel>> wifiChannels(linkCount);
    const auto makeWifiChannel = [&](double referenceLossDb, uint32_t link) {
        Ptr<MultiModelSpectrumChannel> channel = CreateObject<MultiModelSpectrumChannel>();
        if (propagationModel == "fixed_rss")
        {
            Ptr<FixedRssLossModel> loss = CreateObject<FixedRssLossModel>();
            loss->SetRss(fixedRssDbm);
            channel->AddPropagationLossModel(loss);
        }
        else
        {
            Ptr<LogDistancePropagationLossModel> pathLoss =
                CreateObject<LogDistancePropagationLossModel>();
            pathLoss->SetAttribute("Exponent", DoubleValue(pathLossExponent));
            pathLoss->SetAttribute("ReferenceDistance", DoubleValue(1.0));
            pathLoss->SetAttribute("ReferenceLoss", DoubleValue(referenceLossDb));
            Ptr<NakagamiPropagationLossModel> fading =
                CreateObject<NakagamiPropagationLossModel>();
            fading->SetAttribute("Distance1", DoubleValue(nakagamiDistance1M));
            fading->SetAttribute("Distance2", DoubleValue(nakagamiDistance2M));
            fading->SetAttribute("m0", DoubleValue(nakagamiM0));
            fading->SetAttribute("m1", DoubleValue(nakagamiM1));
            fading->SetAttribute("m2", DoubleValue(nakagamiM2));
            pathLoss->SetNext(fading);
            channel->AddPropagationLossModel(pathLoss);
        }
        channel->SetPropagationDelayModel(CreateObject<ConstantSpeedPropagationDelayModel>());
        channel->AssignStreams(propagationStreamBase + static_cast<int64_t>(4 * link));
        return channel;
    };
    const auto installBackgroundLink = [&](const std::string& ssidName,
                                           const std::string& channelSettings,
                                           uint32_t link) {
        for (uint32_t index = 0; index < backgroundStations[link].GetN(); ++index)
        {
            const std::string& stationStandard = backgroundStationStandards[link][index];
            WifiHelper backgroundWifi;
            backgroundWifi.SetStandard(ParseWifiStandard(stationStandard));
            if (stationStandard == "he" || stationStandard == "eht")
            {
                backgroundWifi.ConfigHeOptions("GuardInterval",
                                               TimeValue(NanoSeconds(guardIntervalNs)));
            }
            backgroundWifi.SetRemoteStationManager(
                "ns3::ConstantRateWifiManager",
                "DataMode",
                StringValue(DataModeForStandard(stationStandard)),
                "ControlMode",
                StringValue(controlModeForLink(link)),
                "RtsCtsThreshold",
                UintegerValue(rtsCtsThreshold),
                "FragmentationThreshold",
                UintegerValue(fragmentationThreshold));
            SpectrumWifiPhyHelper backgroundPhy;
            backgroundPhy.SetChannel(wifiChannels[link]);
            backgroundPhy.Set("ChannelSettings", StringValue(channelSettings));
            backgroundPhy.Set("RxGain", DoubleValue(0));
            WifiMacHelper backgroundMac;
            backgroundMac.SetType("ns3::StaWifiMac",
                                  "Ssid",
                                  SsidValue(Ssid(ssidName)),
                                  "ActiveProbing",
                                  BooleanValue(false),
                                  "FrameRetryLimit",
                                  UintegerValue(frameRetryLimit),
                                  "BE_MaxAmpduSize",
                                  UintegerValue(maxAmpduSize),
                                  "BE_MaxAmsduSize",
                                  UintegerValue(maxAmsduSize));
            backgroundMac.SetEdca(AC_BE,
                                  "TxopLimits",
                                  StringValue(std::to_string(txopLimitUs) + "us"));
            backgroundDevices[link].Add(
                backgroundWifi.Install(backgroundPhy,
                                       backgroundMac,
                                       backgroundStations[link].Get(index)));
        }
    };
    const auto installWifiLink = [&](const std::string& ssidName,
                                     const std::string& channelSettings,
                                     uint32_t link,
                                     double referenceLossDb) {
        Ptr<MultiModelSpectrumChannel> channel = makeWifiChannel(referenceLossDb, link);
        wifiChannels[link] = channel;

        wifi.SetRemoteStationManager("ns3::ConstantRateWifiManager",
                                     "DataMode",
                                     StringValue(dataMode),
                                     "ControlMode",
                                     StringValue(controlModeForLink(link)),
                                     "RtsCtsThreshold",
                                     UintegerValue(rtsCtsThreshold),
                                     "FragmentationThreshold",
                                     UintegerValue(fragmentationThreshold));

        SpectrumWifiPhyHelper phy;
        phy.SetChannel(channel);
        phy.Set("ChannelSettings", StringValue(channelSettings));
        phy.Set("RxGain", DoubleValue(0));
        WifiMacHelper mac;
        const Ssid ssid(ssidName);
        mac.SetType("ns3::StaWifiMac",
                    "Ssid",
                    SsidValue(ssid),
                    "ActiveProbing",
                    BooleanValue(false),
                    "FrameRetryLimit",
                    UintegerValue(frameRetryLimit),
                    "BE_MaxAmpduSize",
                    UintegerValue(maxAmpduSize),
                    "BE_MaxAmsduSize",
                    UintegerValue(maxAmsduSize));
        mac.SetEdca(AC_BE,
                    "TxopLimits",
                    StringValue(std::to_string(txopLimitUs) + "us"));
        stationDevices.Add(wifi.Install(phy, mac, station));
        if (backgroundStations[link].GetN() > 0)
        {
            installBackgroundLink(ssidName, channelSettings, link);
        }
        ConfigureUlOfdmaScheduler(mac,
                                  ulOfdmaEnabled,
                                  ulOfdmaAccessIntervalMs,
                                  ulOfdmaBsrpEnabled,
                                  ulOfdmaMaxStations,
                                  ulOfdmaPsduSize);
        mac.SetType("ns3::ApWifiMac",
                    "Ssid",
                    SsidValue(ssid),
                    "FrameRetryLimit",
                    UintegerValue(frameRetryLimit),
                    "BE_MaxAmpduSize",
                    UintegerValue(maxAmpduSize),
                    "BE_MaxAmsduSize",
                    UintegerValue(maxAmsduSize));
        mac.SetEdca(AC_BE,
                    "TxopLimits",
                    StringValue(std::to_string(txopLimitUs) + "us"));
        apWifiDevices.Add(wifi.Install(phy, mac, accessPoint));
    };
    if (topology == "single_link")
    {
        installWifiLink("wifi-streaming",
                        "{36, 20, BAND_5GHZ, 0}",
                        0,
                        referenceLoss5GhzDb);
    }
    else if (topology == "dual_interface")
    {
        installWifiLink("wifi-streaming-2g",
                        "{1, 20, BAND_2_4GHZ, 0}",
                        0,
                        referenceLoss2GhzDb);
        installWifiLink("wifi-streaming-5g",
                        "{36, 20, BAND_5GHZ, 0}",
                        1,
                        referenceLoss5GhzDb);
    }
    else
    {
        if (emlsrMlo)
        {
            wifi.ConfigEhtOptions(
                "EmlsrActivated",
                BooleanValue(true),
                "TransitionTimeout",
                TimeValue(MicroSeconds(emlsrTransitionTimeoutUs)),
                "MediumSyncDuration",
                TimeValue(MicroSeconds(emlsrMediumSyncDurationUs)),
                "MsdOfdmEdThreshold",
                IntegerValue(emlsrMsdOfdmEdThresholdDbm),
                "MsdMaxNTxops",
                UintegerValue(emlsrMsdMaxNTxops));
        }
        Ptr<MultiModelSpectrumChannel> channel2Ghz = makeWifiChannel(referenceLoss2GhzDb, 0);
        wifiChannels[0] = channel2Ghz;

        Ptr<MultiModelSpectrumChannel> channel5Ghz = makeWifiChannel(referenceLoss5GhzDb, 1);
        wifiChannels[1] = channel5Ghz;

        SpectrumWifiPhyHelper phy(2);
        phy.AddPhyToFreqRangeMapping(0, WIFI_SPECTRUM_2_4_GHZ);
        if (emlsrMlo)
        {
            phy.AddPhyToFreqRangeMapping(emlsrMainPhyId, WIFI_SPECTRUM_2_4_GHZ);
        }
        phy.AddPhyToFreqRangeMapping(1, WIFI_SPECTRUM_5_GHZ);
        phy.AddChannel(channel2Ghz, WIFI_SPECTRUM_2_4_GHZ);
        phy.AddChannel(channel5Ghz, WIFI_SPECTRUM_5_GHZ);
        phy.Set(0, "ChannelSettings", StringValue("{1, 20, BAND_2_4GHZ, 0}"));
        phy.Set(1, "ChannelSettings", StringValue("{36, 20, BAND_5GHZ, 0}"));
        phy.Set("RxGain", DoubleValue(0));
        if (emlsrMlo)
        {
            phy.Set("ChannelSwitchDelay",
                    TimeValue(MicroSeconds(emlsrChannelSwitchDelayUs)));
            phy.Set("NotifyMacHdrRxEnd", BooleanValue(true));
        }

        uint8_t link0 = 0;
        wifi.SetRemoteStationManager(link0,
                                     "ns3::ConstantRateWifiManager",
                                     "DataMode",
                                     StringValue("EhtMcs5"),
                                     "ControlMode",
                                     StringValue(controlModeForLink(0)),
                                     "RtsCtsThreshold",
                                     UintegerValue(rtsCtsThreshold),
                                     "FragmentationThreshold",
                                     UintegerValue(fragmentationThreshold));
        wifi.SetRemoteStationManager(1,
                                     "ns3::ConstantRateWifiManager",
                                     "DataMode",
                                     StringValue("EhtMcs5"),
                                     "ControlMode",
                                     StringValue(controlModeForLink(1)),
                                     "RtsCtsThreshold",
                                     UintegerValue(rtsCtsThreshold),
                                     "FragmentationThreshold",
                                     UintegerValue(fragmentationThreshold));
        wifi.ConfigEhtOptions(
            "TidToLinkMappingNegSupport",
            EnumValue(WifiTidToLinkMappingNegSupport::ANY_LINK_SET),
            "TidToLinkMappingUl",
            StringValue("0 0,1"));

        WifiMacHelper mac;
        if (emlsrMlo)
        {
            mac.SetChannelAccessManager(
                "GenerateBackoffIfTxopWithoutTx",
                BooleanValue(false),
                "ProactiveBackoff",
                BooleanValue(false),
                "ResetBackoffThreshold",
                TimeValue(MicroSeconds(emlsrCamResetBackoffThresholdUs)),
                "NSlotsLeft",
                UintegerValue(emlsrCamNSlotsLeft),
                "NSlotsLeftMinDelay",
                TimeValue(MicroSeconds(emlsrCamNSlotsLeftMinDelayUs)));
        }
        const Ssid ssid("wifi-streaming-mld");
        mac.SetType("ns3::StaWifiMac",
                    "Ssid",
                    SsidValue(ssid),
                    "ActiveProbing",
                    BooleanValue(false),
                    "FrameRetryLimit",
                    UintegerValue(frameRetryLimit),
                    "BE_MaxAmpduSize",
                    UintegerValue(maxAmpduSize),
                    "BE_MaxAmsduSize",
                    UintegerValue(maxAmsduSize));
        if (emlsrMlo)
        {
            mac.SetEmlsrManager("ns3::AdvancedEmlsrManager",
                                "EmlsrLinkSet",
                                StringValue("0,1"),
                                "MainPhyId",
                                UintegerValue(emlsrMainPhyId),
                                "EmlsrPaddingDelay",
                                TimeValue(MicroSeconds(emlsrPaddingDelayUs)),
                                "EmlsrTransitionDelay",
                                TimeValue(MicroSeconds(emlsrTransitionDelayUs)),
                                "SwitchAuxPhy",
                                BooleanValue(false),
                                "AuxPhyTxCapable",
                                BooleanValue(false),
                                "AuxPhyChannelWidth",
                                UintegerValue(emlsrAuxPhyChannelWidthMhz),
                                "AuxPhyMaxModClass",
                                EnumValue(WIFI_MOD_CLASS_OFDM),
                                "PutAuxPhyToSleep",
                                BooleanValue(false),
                                "InDeviceInterference",
                                BooleanValue(false),
                                "UseNotifiedMacHdr",
                                BooleanValue(true),
                                "ResetCamState",
                                BooleanValue(false),
                                "AllowUlTxopInRx",
                                BooleanValue(false),
                                "InterruptSwitch",
                                BooleanValue(false),
                                "UseAuxPhyCca",
                                BooleanValue(false),
                                "SwitchMainPhyBackDelay",
                                TimeValue(MicroSeconds(emlsrSwitchMainPhyBackDelayUs)),
                                "KeepMainPhyAfterDlTxop",
                                BooleanValue(false),
                                "CheckAccessOnMainPhyLink",
                                BooleanValue(true),
                                "MinAcToSkipCheckAccess",
                                EnumValue(AcIndex::AC_BK));
        }
        mac.SetEdca(AC_BE,
                    "NMaxInflights",
                    UintegerValue(mloStaMaxInflights),
                    "TxopLimits",
                    StringValue(std::to_string(txopLimitUs) + "us," +
                                std::to_string(txopLimitUs) + "us"));
        stationDevices = wifi.Install(phy, mac, station);

        ConfigureUlOfdmaScheduler(mac,
                                  ulOfdmaEnabled,
                                  ulOfdmaAccessIntervalMs,
                                  ulOfdmaBsrpEnabled,
                                  ulOfdmaMaxStations,
                                  ulOfdmaPsduSize);
        mac.SetType("ns3::ApWifiMac",
                    "Ssid",
                    SsidValue(ssid),
                    "BeaconGeneration",
                    BooleanValue(backgroundProfile == "legacy_mixed8"),
                    "FrameRetryLimit",
                    UintegerValue(frameRetryLimit),
                    "BE_MaxAmpduSize",
                    UintegerValue(maxAmpduSize),
                    "BE_MaxAmsduSize",
                    UintegerValue(maxAmsduSize));
        if (emlsrMlo)
        {
            mac.SetApEmlsrManager("ns3::AdvancedApEmlsrManager",
                                  "UseNotifiedMacHdr",
                                  BooleanValue(true),
                                  "EarlySwitchToListening",
                                  BooleanValue(false),
                                  "WaitTransDelayOnPsduRxError",
                                  BooleanValue(true),
                                  "UpdateCwAfterFailedIcf",
                                  BooleanValue(true),
                                  "ReportFailedIcf",
                                  BooleanValue(true));
        }
        mac.SetEdca(AC_BE,
                    "TxopLimits",
                    StringValue(std::to_string(txopLimitUs) + "us," +
                                std::to_string(txopLimitUs) + "us"));
        apWifiDevices = wifi.Install(phy, mac, accessPoint);

        targetStaMld = DynamicCast<WifiNetDevice>(stationDevices.Get(0));
        targetApMld = DynamicCast<WifiNetDevice>(apWifiDevices.Get(0));
        WifiStaticSetupHelper::SetStaticAssociation(targetApMld, targetStaMld);
        if (emlsrMlo)
        {
            WifiStaticSetupHelper::SetStaticEmlsr(targetApMld, targetStaMld);
        }
        if (backgroundProfile == "legacy_mixed8")
        {
            installBackgroundLink("wifi-streaming-mld",
                                  "{1, 20, BAND_2_4GHZ, 0}",
                                  0);
            installBackgroundLink("wifi-streaming-mld",
                                  "{36, 20, BAND_5GHZ, 0}",
                                  1);
        }
        if (maxAmpduSize > 0)
        {
            WifiStaticSetupHelper::SetStaticBlockAck(targetApMld, targetStaMld, 0);
            WifiStaticSetupHelper::SetStaticBlockAck(targetStaMld, targetApMld, 0);
        }
    }

    std::string observedStationManager;
    std::string observedControlModes;
    for (uint32_t deviceIndex = 0; deviceIndex < stationDevices.GetN(); ++deviceIndex)
    {
        const auto wifiDevice = DynamicCast<WifiNetDevice>(stationDevices.Get(deviceIndex));
        NS_ABORT_MSG_IF(!wifiDevice, "Target station device is not a WifiNetDevice");
        for (const auto& manager : wifiDevice->GetRemoteStationManagers())
        {
            const std::string managerName = manager->GetInstanceTypeId().GetName();
            NS_ABORT_MSG_IF(managerName != "ns3::ConstantRateWifiManager",
                            "Unexpected target station manager: " << managerName);
            observedStationManager = "ConstantRateWifiManager";
            WifiModeValue controlMode;
            manager->GetAttribute("ControlMode", controlMode);
            if (!observedControlModes.empty())
            {
                observedControlModes += ",";
            }
            observedControlModes += controlMode.Get().GetUniqueName();
        }
    }
    const std::string expectedControlModes =
        topology == "single_link" ? "OfdmRate24Mbps"
                                  : "ErpOfdmRate24Mbps,OfdmRate24Mbps";
    NS_ABORT_MSG_IF(observedControlModes != expectedControlModes,
                    "Target station control modes are " << observedControlModes
                                                         << ", expected "
                                                         << expectedControlModes);
    if (emlsrMlo)
    {
        Simulator::Schedule(NanoSeconds(1),
                            [targetStaMld,
                             targetApMld,
                             &mloRuntime,
                             &mloRuntimeCaptured]() {
                                auto staMac = DynamicCast<StaWifiMac>(targetStaMld->GetMac());
                                NS_ABORT_MSG_IF(!staMac || !staMac->IsAssociated(),
                                                "EMLSR runtime check requires an associated STA "
                                                "MLD");
                                auto apMac = DynamicCast<ApWifiMac>(targetApMld->GetMac());
                                NS_ABORT_MSG_IF(!apMac, "EMLSR runtime check requires an AP MLD");
                                auto manager = staMac->GetEmlsrManager();
                                NS_ABORT_MSG_IF(!manager,
                                                "EMLSR runtime check found no EMLSR manager");
                                auto apManager = apMac->GetApEmlsrManager();
                                NS_ABORT_MSG_IF(!apManager,
                                                "EMLSR runtime check found no AP EMLSR manager");
                                const std::set<uint8_t> expectedLinks{0, 1};
                                NS_ABORT_MSG_IF(manager->GetEmlsrLinks() != expectedLinks,
                                                "EMLSR runtime link set is not {0,1}");

                                mloRuntime.mode = "EMLSR";
                                mloRuntime.profile = "advanced_sta_ap_fixed_aux_v4";
                                mloRuntime.stationEmlsrActivated =
                                    targetStaMld->IsEmlsrActivated();
                                mloRuntime.apEmlsrActivated = targetApMld->IsEmlsrActivated();
                                mloRuntime.emlsrManager = manager->GetInstanceTypeId().GetName();
                                mloRuntime.apEmlsrManager =
                                    apManager->GetInstanceTypeId().GetName();
                                mloRuntime.emlsrLinkIds.assign(expectedLinks.begin(),
                                                               expectedLinks.end());
                                mloRuntime.mainPhyId = manager->GetMainPhyId();
                                const auto mainPhyLink =
                                    staMac->GetLinkForPhy(mloRuntime.mainPhyId);
                                NS_ABORT_MSG_IF(!mainPhyLink,
                                                "EMLSR main PHY is not mapped to a setup link");
                                mloRuntime.initialMainPhyLinkId = *mainPhyLink;
                                const auto mainPhy = targetStaMld->GetPhy(mloRuntime.mainPhyId);
                                const auto mainPhyBand = mainPhy->GetPhyBand();
                                mloRuntime.initialMainPhyBand =
                                    mainPhyBand == WIFI_PHY_BAND_5GHZ ? "5 GHz" : "unexpected";
                                auto spectrumMainPhy = DynamicCast<SpectrumWifiPhy>(mainPhy);
                                NS_ABORT_MSG_IF(!spectrumMainPhy,
                                                "EMLSR main PHY is not a SpectrumWifiPhy");
                                const auto& interfaces =
                                    spectrumMainPhy->GetSpectrumPhyInterfaces();
                                NS_ABORT_MSG_IF(
                                    !interfaces.contains(WIFI_SPECTRUM_2_4_GHZ) ||
                                        !interfaces.contains(WIFI_SPECTRUM_5_GHZ),
                                    "EMLSR main PHY does not cover both configured bands");
                                mloRuntime.mainPhyFrequencyRanges = {
                                    "WIFI_SPECTRUM_2_4_GHZ", "WIFI_SPECTRUM_5_GHZ"};

                                TimeValue paddingDelay;
                                TimeValue transitionDelay;
                                BooleanValue switchAuxPhy;
                                BooleanValue auxPhyTxCapable;
                                UintegerValue auxPhyChannelWidth;
                                EnumValue<WifiModulationClass> auxPhyMaxModClass;
                                BooleanValue putAuxPhyToSleep;
                                BooleanValue inDeviceInterference;
                                BooleanValue useNotifiedMacHdr;
                                BooleanValue resetCamState;
                                BooleanValue allowUlTxopInRx;
                                BooleanValue interruptSwitch;
                                BooleanValue useAuxPhyCca;
                                TimeValue switchMainPhyBackDelay;
                                BooleanValue keepMainPhyAfterDlTxop;
                                BooleanValue checkAccessOnMainPhyLink;
                                EnumValue<AcIndex> minAcToSkipCheckAccess;
                                BooleanValue notifyMacHeaderRxEnd;
                                manager->GetAttribute("EmlsrPaddingDelay", paddingDelay);
                                manager->GetAttribute("EmlsrTransitionDelay", transitionDelay);
                                manager->GetAttribute("SwitchAuxPhy", switchAuxPhy);
                                manager->GetAttribute("AuxPhyTxCapable", auxPhyTxCapable);
                                manager->GetAttribute("AuxPhyChannelWidth", auxPhyChannelWidth);
                                manager->GetAttribute("AuxPhyMaxModClass", auxPhyMaxModClass);
                                manager->GetAttribute("PutAuxPhyToSleep", putAuxPhyToSleep);
                                manager->GetAttribute("InDeviceInterference",
                                                      inDeviceInterference);
                                manager->GetAttribute("UseNotifiedMacHdr", useNotifiedMacHdr);
                                manager->GetAttribute("ResetCamState", resetCamState);
                                manager->GetAttribute("AllowUlTxopInRx", allowUlTxopInRx);
                                manager->GetAttribute("InterruptSwitch", interruptSwitch);
                                manager->GetAttribute("UseAuxPhyCca", useAuxPhyCca);
                                manager->GetAttribute("SwitchMainPhyBackDelay",
                                                      switchMainPhyBackDelay);
                                manager->GetAttribute("KeepMainPhyAfterDlTxop",
                                                      keepMainPhyAfterDlTxop);
                                manager->GetAttribute("CheckAccessOnMainPhyLink",
                                                      checkAccessOnMainPhyLink);
                                manager->GetAttribute("MinAcToSkipCheckAccess",
                                                      minAcToSkipCheckAccess);
                                mainPhy->GetAttribute("NotifyMacHdrRxEnd",
                                                      notifyMacHeaderRxEnd);
                                const auto transitionTimeout = manager->GetTransitionTimeout();
                                const auto msdMaxNTxops = manager->GetMediumSyncMaxNTxops();
                                mloRuntime.paddingDelayUs =
                                    paddingDelay.Get().GetMicroSeconds();
                                mloRuntime.transitionDelayUs =
                                    transitionDelay.Get().GetMicroSeconds();
                                mloRuntime.transitionTimeoutUs =
                                    transitionTimeout
                                        ? transitionTimeout->GetMicroSeconds()
                                        : 0;
                                mloRuntime.mediumSyncDurationUs =
                                    manager->GetMediumSyncDuration().GetMicroSeconds();
                                mloRuntime.msdOfdmEdThresholdDbm =
                                    manager->GetMediumSyncOfdmEdThreshold();
                                mloRuntime.msdMaxNTxops =
                                    msdMaxNTxops ? *msdMaxNTxops : 0;
                                mloRuntime.channelSwitchDelayUs =
                                    targetStaMld->GetPhy(mloRuntime.mainPhyId)
                                        ->GetChannelSwitchDelay()
                                        .GetMicroSeconds();
                                mloRuntime.switchAuxPhy = switchAuxPhy.Get();
                                mloRuntime.auxPhyTxCapable = auxPhyTxCapable.Get();
                                mloRuntime.auxPhyChannelWidthMhz = auxPhyChannelWidth.Get();
                                mloRuntime.auxPhyMaxModulationClass =
                                    auxPhyMaxModClass.Get() == WIFI_MOD_CLASS_OFDM
                                        ? "OFDM"
                                        : "unexpected";
                                mloRuntime.putAuxPhyToSleep = putAuxPhyToSleep.Get();
                                mloRuntime.inDeviceInterference =
                                    inDeviceInterference.Get();
                                mloRuntime.useNotifiedMacHeader = useNotifiedMacHdr.Get();
                                mloRuntime.resetCamState = resetCamState.Get();
                                mloRuntime.allowUlTxopInRx = allowUlTxopInRx.Get();
                                mloRuntime.interruptSwitch = interruptSwitch.Get();
                                mloRuntime.useAuxPhyCca = useAuxPhyCca.Get();
                                mloRuntime.switchMainPhyBackDelayUs =
                                    switchMainPhyBackDelay.Get().GetMicroSeconds();
                                mloRuntime.keepMainPhyAfterDlTxop =
                                    keepMainPhyAfterDlTxop.Get();
                                mloRuntime.checkAccessOnMainPhyLink =
                                    checkAccessOnMainPhyLink.Get();
                                mloRuntime.minAcToSkipCheckAccess =
                                    minAcToSkipCheckAccess.Get() == AcIndex::AC_BK
                                        ? "AC_BK"
                                        : "unexpected";
                                mloRuntime.notifyMacHeaderRxEnd =
                                    notifyMacHeaderRxEnd.Get();

                                BooleanValue apUseNotifiedMacHdr;
                                BooleanValue apEarlySwitchToListening;
                                BooleanValue apWaitTransDelayOnPsduRxError;
                                BooleanValue apUpdateCwAfterFailedIcf;
                                BooleanValue apReportFailedIcf;
                                apManager->GetAttribute("UseNotifiedMacHdr",
                                                        apUseNotifiedMacHdr);
                                apManager->GetAttribute("EarlySwitchToListening",
                                                        apEarlySwitchToListening);
                                apManager->GetAttribute("WaitTransDelayOnPsduRxError",
                                                        apWaitTransDelayOnPsduRxError);
                                apManager->GetAttribute("UpdateCwAfterFailedIcf",
                                                        apUpdateCwAfterFailedIcf);
                                apManager->GetAttribute("ReportFailedIcf",
                                                        apReportFailedIcf);
                                mloRuntime.apUseNotifiedMacHeader =
                                    apUseNotifiedMacHdr.Get();
                                mloRuntime.apEarlySwitchToListening =
                                    apEarlySwitchToListening.Get();
                                mloRuntime.apWaitTransDelayOnPsduRxError =
                                    apWaitTransDelayOnPsduRxError.Get();
                                mloRuntime.apUpdateCwAfterFailedIcf =
                                    apUpdateCwAfterFailedIcf.Get();
                                mloRuntime.apReportFailedIcf = apReportFailedIcf.Get();

                                for (const auto linkId : expectedLinks)
                                {
                                    const auto clientLinkAddress =
                                        staMac->GetFrameExchangeManager(linkId)->GetAddress();
                                    mloRuntime.apEmlsrEnabledPerLink.push_back(
                                        apMac->GetWifiRemoteStationManager(linkId)
                                            ->GetEmlsrEnabled(clientLinkAddress));
                                }

                                const auto phySettingsMatch = [](Ptr<WifiNetDevice> device) {
                                    if (device->GetNPhys() != 2)
                                    {
                                        return false;
                                    }
                                    for (const auto& phy : device->GetPhys())
                                    {
                                        BooleanValue notifyMacHeader;
                                        phy->GetAttribute("NotifyMacHdrRxEnd", notifyMacHeader);
                                        if (!notifyMacHeader.Get() ||
                                            phy->GetChannelSwitchDelay() != MicroSeconds(100))
                                        {
                                            return false;
                                        }
                                    }
                                    return true;
                                };
                                mloRuntime.allPhySettingsMatchProfile =
                                    phySettingsMatch(targetStaMld) &&
                                    phySettingsMatch(targetApMld);

                                const auto camSettingsMatch = [](Ptr<WifiMac> wifiMac) {
                                    if (wifiMac->GetNLinks() != 2)
                                    {
                                        return false;
                                    }
                                    for (uint8_t linkId = 0; linkId < wifiMac->GetNLinks(); ++linkId)
                                    {
                                        const auto cam =
                                            wifiMac->GetChannelAccessManager(linkId);
                                        BooleanValue generateBackoffWithoutTx;
                                        BooleanValue proactiveBackoff;
                                        TimeValue resetBackoffThreshold;
                                        UintegerValue nSlotsLeft;
                                        TimeValue nSlotsLeftMinDelay;
                                        cam->GetAttribute("GenerateBackoffIfTxopWithoutTx",
                                                          generateBackoffWithoutTx);
                                        cam->GetAttribute("ProactiveBackoff", proactiveBackoff);
                                        cam->GetAttribute("ResetBackoffThreshold",
                                                          resetBackoffThreshold);
                                        cam->GetAttribute("NSlotsLeft", nSlotsLeft);
                                        cam->GetAttribute("NSlotsLeftMinDelay",
                                                          nSlotsLeftMinDelay);
                                        if (generateBackoffWithoutTx.Get() ||
                                            proactiveBackoff.Get() ||
                                            resetBackoffThreshold.Get() != MicroSeconds(0) ||
                                            nSlotsLeft.Get() != 0 ||
                                            nSlotsLeftMinDelay.Get() != MicroSeconds(25))
                                        {
                                            return false;
                                        }
                                    }
                                    return true;
                                };
                                mloRuntime.allCamSettingsMatchProfile =
                                    camSettingsMatch(staMac) && camSettingsMatch(apMac);

                                const bool profileMatches =
                                    mloRuntime.stationEmlsrActivated &&
                                    mloRuntime.apEmlsrActivated &&
                                    mloRuntime.emlsrManager ==
                                        "ns3::AdvancedEmlsrManager" &&
                                    mloRuntime.apEmlsrManager ==
                                        "ns3::AdvancedApEmlsrManager" &&
                                    mloRuntime.mainPhyId == 1 &&
                                    mloRuntime.initialMainPhyLinkId == 1 &&
                                    mainPhyBand == WIFI_PHY_BAND_5GHZ &&
                                    mloRuntime.paddingDelayUs == 128 &&
                                    mloRuntime.transitionDelayUs == 128 &&
                                    transitionTimeout &&
                                    *transitionTimeout == MicroSeconds(0) &&
                                    mloRuntime.mediumSyncDurationUs == 5472 &&
                                    mloRuntime.msdOfdmEdThresholdDbm == -72 &&
                                    msdMaxNTxops && *msdMaxNTxops == 1 &&
                                    mloRuntime.channelSwitchDelayUs == 100 &&
                                    !mloRuntime.switchAuxPhy &&
                                    !mloRuntime.auxPhyTxCapable &&
                                    mloRuntime.auxPhyChannelWidthMhz == 20 &&
                                    mloRuntime.auxPhyMaxModulationClass == "OFDM" &&
                                    !mloRuntime.putAuxPhyToSleep &&
                                    !mloRuntime.inDeviceInterference &&
                                    mloRuntime.useNotifiedMacHeader &&
                                    !mloRuntime.resetCamState &&
                                    !mloRuntime.allowUlTxopInRx &&
                                    !mloRuntime.interruptSwitch &&
                                    !mloRuntime.useAuxPhyCca &&
                                    mloRuntime.switchMainPhyBackDelayUs == 5000 &&
                                    !mloRuntime.keepMainPhyAfterDlTxop &&
                                    mloRuntime.checkAccessOnMainPhyLink &&
                                    mloRuntime.minAcToSkipCheckAccess == "AC_BK" &&
                                    mloRuntime.apUseNotifiedMacHeader &&
                                    !mloRuntime.apEarlySwitchToListening &&
                                    mloRuntime.apWaitTransDelayOnPsduRxError &&
                                    mloRuntime.apUpdateCwAfterFailedIcf &&
                                    mloRuntime.apReportFailedIcf &&
                                    mloRuntime.notifyMacHeaderRxEnd &&
                                    mloRuntime.allPhySettingsMatchProfile &&
                                    mloRuntime.allCamSettingsMatchProfile;
                                NS_ABORT_MSG_IF(!profileMatches,
                                                "Resolved EMLSR runtime state differs from the "
                                                "practical profile");
                                NS_ABORT_MSG_IF(
                                    mloRuntime.apEmlsrEnabledPerLink.size() != 2 ||
                                        !std::all_of(mloRuntime.apEmlsrEnabledPerLink.begin(),
                                                 mloRuntime.apEmlsrEnabledPerLink.end(),
                                                 [](bool enabled) { return enabled; }),
                                    "AP did not enable EMLSR on both setup links");
                                mloRuntimeCaptured = true;
                            });
    }
    constexpr uint32_t obssBssCount = 4;
    std::vector<NodeContainer> obssAccessPoints(obssBssCount);
    std::vector<NodeContainer> obssStations(obssBssCount);
    std::vector<NetDeviceContainer> obssApDevices(obssBssCount);
    std::vector<NetDeviceContainer> obssStaDevices(obssBssCount);
    std::vector<ObssBssConfig> obssBssConfigs;
    const std::vector<std::string> obssStandards{"ht", "he", "vht", "eht"};
    const std::vector<uint8_t> obssLinks{0, 0, 1, 1};
    if (obssEnabled)
    {
        for (uint32_t bss = 0; bss < obssBssCount; ++bss)
        {
            obssAccessPoints[bss].Create(1);
            obssStations[bss].Create(obssStationsPerBss);
            WifiHelper obssWifi;
            obssWifi.SetStandard(ParseWifiStandard(obssStandards[bss]));
            if (obssStandards[bss] == "he" || obssStandards[bss] == "eht")
            {
                obssWifi.ConfigHeOptions("GuardInterval", TimeValue(NanoSeconds(guardIntervalNs)));
            }
            if (obssStationManager == "minstrel_ht")
            {
                obssWifi.SetRemoteStationManager(
                    "ns3::MinstrelHtWifiManager",
                    "UpdateStatistics",
                    TimeValue(MilliSeconds(obssManagerUpdateMs)),
                    "UseLatestAmendmentOnly",
                    BooleanValue(true),
                    "RtsCtsThreshold",
                    UintegerValue(rtsCtsThreshold),
                    "FragmentationThreshold",
                    UintegerValue(fragmentationThreshold));
            }
            else if (obssStationManager == "ideal")
            {
                obssWifi.SetRemoteStationManager(
                    "ns3::IdealWifiManager",
                    "BerThreshold",
                    DoubleValue(1e-6),
                    "RtsCtsThreshold",
                    UintegerValue(rtsCtsThreshold),
                    "FragmentationThreshold",
                    UintegerValue(fragmentationThreshold));
            }
            else
            {
                obssWifi.SetRemoteStationManager(
                    "ns3::ConstantRateWifiManager",
                    "DataMode",
                    StringValue(DataModeForStandard(obssStandards[bss])),
                    "ControlMode",
                    StringValue(obssLinks[bss] == 0 ? "ErpOfdmRate24Mbps"
                                                   : "OfdmRate24Mbps"),
                    "RtsCtsThreshold",
                    UintegerValue(rtsCtsThreshold),
                    "FragmentationThreshold",
                    UintegerValue(fragmentationThreshold));
            }
            SpectrumWifiPhyHelper obssPhy;
            obssPhy.SetChannel(wifiChannels[obssLinks[bss]]);
            obssPhy.Set("ChannelSettings",
                        StringValue(obssLinks[bss] == 0
                                        ? "{1, 20, BAND_2_4GHZ, 0}"
                                        : "{36, 20, BAND_5GHZ, 0}"));
            obssPhy.Set("RxGain", DoubleValue(0));
            WifiMacHelper obssMac;
            const Ssid obssSsid("wifi-streaming-obss-" + std::to_string(bss));
            obssMac.SetType("ns3::StaWifiMac",
                            "Ssid",
                            SsidValue(obssSsid),
                            "ActiveProbing",
                            BooleanValue(false),
                            "FrameRetryLimit",
                            UintegerValue(frameRetryLimit),
                            "BE_MaxAmpduSize",
                            UintegerValue(maxAmpduSize),
                            "BE_MaxAmsduSize",
                            UintegerValue(maxAmsduSize));
            obssMac.SetEdca(AC_BE,
                            "TxopLimits",
                            StringValue(std::to_string(txopLimitUs) + "us"));
            obssStaDevices[bss] = obssWifi.Install(obssPhy, obssMac, obssStations[bss]);
            ConfigureUlOfdmaScheduler(
                obssMac,
                ulOfdmaEnabled && ulOfdmaScope == "all_he_eht_aps" &&
                    (obssStandards[bss] == "he" || obssStandards[bss] == "eht"),
                ulOfdmaAccessIntervalMs,
                ulOfdmaBsrpEnabled,
                ulOfdmaMaxStations,
                ulOfdmaPsduSize);
            obssMac.SetType("ns3::ApWifiMac",
                            "Ssid",
                            SsidValue(obssSsid),
                            "FrameRetryLimit",
                            UintegerValue(frameRetryLimit),
                            "BE_MaxAmpduSize",
                            UintegerValue(maxAmpduSize),
                            "BE_MaxAmsduSize",
                            UintegerValue(maxAmsduSize));
            obssMac.SetEdca(AC_BE,
                            "TxopLimits",
                            StringValue(std::to_string(txopLimitUs) + "us"));
            obssApDevices[bss] =
                obssWifi.Install(obssPhy, obssMac, obssAccessPoints[bss]);
            Ptr<WifiNetDevice> obssAp =
                DynamicCast<WifiNetDevice>(obssApDevices[bss].Get(0));
            for (uint32_t index = 0; index < obssStationsPerBss; ++index)
            {
                WifiStaticSetupHelper::SetStaticAssociation(
                    obssAp,
                    DynamicCast<WifiNetDevice>(obssStaDevices[bss].Get(index)));
            }
        }
        int64_t obssWifiStream = obssWifiStreamBase;
        for (uint32_t bss = 0; bss < obssBssCount; ++bss)
        {
            obssWifiStream +=
                WifiHelper::AssignStreams(obssStaDevices[bss], obssWifiStream);
            obssWifiStream +=
                WifiHelper::AssignStreams(obssApDevices[bss], obssWifiStream);
        }
    }
    int64_t wifiStream = 2000;
    wifiStream += WifiHelper::AssignStreams(stationDevices, wifiStream);
    wifiStream += WifiHelper::AssignStreams(apWifiDevices, wifiStream);
    for (const auto& devices : backgroundDevices)
    {
        wifiStream += WifiHelper::AssignStreams(devices, wifiStream);
    }

    NodeContainer all(station, accessPoint, edge);
    for (const auto& nodes : backgroundStations)
    {
        all.Add(nodes);
    }
    for (uint32_t bss = 0; bss < obssBssCount; ++bss)
    {
        all.Add(obssAccessPoints[bss]);
        all.Add(obssStations[bss]);
    }
    MobilityHelper mobility;
    mobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
    mobility.Install(all);
    accessPoint.Get(0)->GetObject<MobilityModel>()->SetPosition(Vector(0, 0, 0));
    station.Get(0)->GetObject<MobilityModel>()->SetPosition(Vector(stationDistanceM, 0, 0));
    for (uint32_t link = 0; link < linkCount; ++link)
    {
        for (uint32_t index = 0; index < backgroundStations[link].GetN(); ++index)
        {
            const double distance =
                index % 2 == 0 ? backgroundNearDistanceM : backgroundFarDistanceM;
            backgroundStations[link].Get(index)->GetObject<MobilityModel>()->SetPosition(
                Vector(distance, 2.0 + link, 0));
        }
    }
    const auto samplePlacement = [](double minimum, double maximum, int64_t stream) {
        Ptr<UniformRandomVariable> variable = CreateObject<UniformRandomVariable>();
        variable->SetStream(stream);
        return variable->GetValue(minimum, maximum);
    };
    constexpr double pi = 3.14159265358979323846;
    for (uint32_t bss = 0; bss < (obssEnabled ? obssBssCount : 0); ++bss)
    {
        int64_t placementStream =
            obssPlacementStreamBase + static_cast<int64_t>(bss * (2 + 2 * obssStationsPerBss));
        ObssBssConfig descriptor;
        descriptor.bssId = bss;
        descriptor.linkId = obssLinks[bss];
        descriptor.ssid = "wifi-streaming-obss-" + std::to_string(bss);
        descriptor.standard = StandardLabel(obssStandards[bss]);
        descriptor.apX = samplePlacement(obssAreaMinXM, obssAreaMaxXM, placementStream++);
        descriptor.apY = samplePlacement(obssAreaMinYM, obssAreaMaxYM, placementStream++);
        obssAccessPoints[bss].Get(0)->GetObject<MobilityModel>()->SetPosition(
            Vector(descriptor.apX, descriptor.apY, 0));
        for (uint32_t index = 0; index < obssStationsPerBss; ++index)
        {
            const double angle = samplePlacement(0, 2 * pi, placementStream++);
            const double radius = samplePlacement(obssStaMinDistanceM,
                                                  obssStaMaxDistanceM,
                                                  placementStream++);
            const double x = descriptor.apX + radius * std::cos(angle);
            const double y = descriptor.apY + radius * std::sin(angle);
            descriptor.staX.push_back(x);
            descriptor.staY.push_back(y);
            obssStations[bss].Get(index)->GetObject<MobilityModel>()->SetPosition(Vector(x, y, 0));
        }
        obssBssConfigs.push_back(descriptor);
    }

    CsmaHelper csma;
    csma.SetChannelAttribute("DataRate", StringValue("1Gbps"));
    csma.SetChannelAttribute("Delay", TimeValue(MicroSeconds(10)));
    NetDeviceContainer wiredDevices = csma.Install(NodeContainer(accessPoint, edge));

    InternetStackHelper internet;
    internet.Install(all);
    Ipv4AddressHelper address;
    std::vector<Ipv4Address> stationAddresses;
    std::vector<Ipv4Address> apAddresses;
    std::vector<std::vector<Ipv4Address>> backgroundAddresses(linkCount);
    std::vector<Ipv4Address> obssApAddresses(obssBssCount);
    std::vector<std::vector<Ipv4Address>> obssStaAddresses(obssBssCount);
    if (nativeMlo)
    {
        NetDeviceContainer wifiNetwork(stationDevices.Get(0), apWifiDevices.Get(0));
        for (const auto& devices : backgroundDevices)
        {
            wifiNetwork.Add(devices);
        }
        address.SetBase("10.1.0.0", "255.255.255.0");
        const Ipv4InterfaceContainer interfaces = address.Assign(wifiNetwork);
        stationAddresses.push_back(interfaces.GetAddress(0));
        apAddresses.push_back(interfaces.GetAddress(1));
        uint32_t interfaceIndex = 2;
        for (uint32_t link = 0; link < linkCount; ++link)
        {
            for (uint32_t index = 0; index < backgroundDevices[link].GetN(); ++index)
            {
                backgroundAddresses[link].push_back(interfaces.GetAddress(interfaceIndex++));
            }
        }
    }
    else for (uint32_t path = 0; path < stationDevices.GetN(); ++path)
    {
        NetDeviceContainer wifiPair(stationDevices.Get(path), apWifiDevices.Get(path));
        wifiPair.Add(backgroundDevices[path]);
        const std::string network = "10.1." + std::to_string(path) + ".0";
        address.SetBase(network.c_str(), "255.255.255.0");
        const Ipv4InterfaceContainer interfaces = address.Assign(wifiPair);
        stationAddresses.push_back(interfaces.GetAddress(0));
        apAddresses.push_back(interfaces.GetAddress(1));
        for (uint32_t index = 0; index < backgroundDevices[path].GetN(); ++index)
        {
            backgroundAddresses[path].push_back(interfaces.GetAddress(index + 2));
        }
    }
    address.SetBase("10.2.0.0", "255.255.255.0");
    Ipv4InterfaceContainer wiredInterfaces = address.Assign(wiredDevices);
    std::vector<Ipv4Address> edgeDestinations{wiredInterfaces.GetAddress(1)};

    Ptr<Ipv4> apIpv4 = accessPoint.Get(0)->GetObject<Ipv4>();
    Ptr<Ipv4> edgeIpv4 = edge.Get(0)->GetObject<Ipv4>();
    const uint32_t apWiredInterface = apIpv4->GetInterfaceForDevice(wiredDevices.Get(0));
    const uint32_t edgeWiredInterface = edgeIpv4->GetInterfaceForDevice(wiredDevices.Get(1));
    for (uint32_t path = 1; path < stationDevices.GetN(); ++path)
    {
        const std::string prefix = "10.2." + std::to_string(path);
        const Ipv4Mask mask("255.255.255.0");
        apIpv4->AddAddress(apWiredInterface,
                           Ipv4InterfaceAddress(Ipv4Address((prefix + ".1").c_str()), mask));
        const Ipv4Address destination((prefix + ".2").c_str());
        edgeIpv4->AddAddress(edgeWiredInterface, Ipv4InterfaceAddress(destination, mask));
        edgeDestinations.push_back(destination);
    }
    for (uint32_t interface = 1; interface < apIpv4->GetNInterfaces(); ++interface)
    {
        apIpv4->SetForwarding(interface, true);
    }
    Ipv4StaticRoutingHelper staticRoutingHelper;
    if (nativeMlo)
    {
        // Ipv4GlobalRouting calls WifiNetDevice::GetChannel(), which is
        // intentionally undefined for a multi-channel MLD. Install the one
        // required reverse route explicitly instead.
        Ptr<Ipv4StaticRouting> edgeRouting = staticRoutingHelper.GetStaticRouting(edgeIpv4);
        edgeRouting->AddNetworkRouteTo(Ipv4Address("10.1.0.0"),
                                       Ipv4Mask("255.255.255.0"),
                                       wiredInterfaces.GetAddress(0),
                                       edgeWiredInterface);
    }
    else
    {
        Ipv4GlobalRoutingHelper::PopulateRoutingTables();
    }
    // Assign isolated OBSS subnets after global route discovery.  Their radios
    // share the spectrum channels with the target BSS but not its IP network.
    for (uint32_t bss = 0; bss < (obssEnabled ? obssBssCount : 0); ++bss)
    {
        NetDeviceContainer obssNetwork(obssApDevices[bss].Get(0));
        obssNetwork.Add(obssStaDevices[bss]);
        const std::string network = "10.3." + std::to_string(bss) + ".0";
        address.SetBase(network.c_str(), "255.255.255.0");
        const Ipv4InterfaceContainer interfaces = address.Assign(obssNetwork);
        obssApAddresses[bss] = interfaces.GetAddress(0);
        for (uint32_t index = 0; index < obssStationsPerBss; ++index)
        {
            obssStaAddresses[bss].push_back(interfaces.GetAddress(index + 1));
        }
    }

    // One host route per radio lets a socket bound to that NetDevice select the
    // matching gateway even though both routes target the same edge address.
    Ptr<Ipv4> stationIpv4 = station.Get(0)->GetObject<Ipv4>();
    Ptr<Ipv4StaticRouting> stationRouting = staticRoutingHelper.GetStaticRouting(stationIpv4);
    for (uint32_t path = 0; path < stationDevices.GetN(); ++path)
    {
        const uint32_t interface = stationIpv4->GetInterfaceForDevice(stationDevices.Get(path));
        stationRouting->AddHostRouteTo(edgeDestinations[path],
                                       apAddresses[path],
                                       interface,
                                       0);
    }
    for (uint32_t link = 0; link < linkCount; ++link)
    {
        for (uint32_t index = 0; index < backgroundStations[link].GetN(); ++index)
        {
            Ptr<Ipv4> backgroundIpv4 =
                backgroundStations[link].Get(index)->GetObject<Ipv4>();
            Ptr<Ipv4StaticRouting> backgroundRouting =
                staticRoutingHelper.GetStaticRouting(backgroundIpv4);
            const uint32_t backgroundInterface =
                backgroundIpv4->GetInterfaceForDevice(backgroundDevices[link].Get(index));
            const uint32_t destinationPath = nativeMlo ? 0 : link;
            backgroundRouting->AddHostRouteTo(edgeDestinations[destinationPath],
                                              apAddresses[destinationPath],
                                              backgroundInterface,
                                              0);
        }
    }

    const Time warmup = Seconds(1);
    const Time measurementStop = warmup + Seconds(durationSeconds);
    uint64_t randomizedAssignmentWindowStartNs = 0;
    uint64_t randomizedAssignmentWindowStopNs = 0;
    if (policyName == "randomized_full_copy_exploration")
    {
        const Time assignmentStop =
            measurementStop - MicroSeconds(randomizedAssignmentStopGuardUs);
        NS_ABORT_MSG_IF(assignmentStop <=
                            warmup +
                                MicroSeconds(RandomizedInterventionController::T4_OFFSET_US),
                        "Randomized assignment window cannot contain a paired T2/T4 frame");
        randomizedAssignmentWindowStartNs = warmup.GetNanoSeconds();
        randomizedAssignmentWindowStopNs = assignmentStop.GetNanoSeconds();
    }

    StreamingRunConfig resolved;
    resolved.runId = runId;
    resolved.rngSeed = seed;
    resolved.rngRun = run;
    resolved.topology = topology;
    resolved.policy = policyName;
    resolved.source = sourceName;
    resolved.traceFile = traceFile;
    resolved.emissionMode = emissionMode;
    resolved.durationSeconds = durationSeconds;
    resolved.warmupSeconds = warmup.GetSeconds();
    resolved.fps = fps;
    resolved.frameSizeBytes = frameSize;
    resolved.gopLength = gopLength;
    resolved.keyframeSizeMultiplier = keyframeSizeMultiplier;
    resolved.payloadSizeBytes = payloadSize;
    resolved.deadlineUs = deadlineUs;
    resolved.propagationModel = propagationModel;
    resolved.fixedRssDbm = fixedRssDbm;
    resolved.stationDistanceM = stationDistanceM;
    resolved.pathLossExponent = pathLossExponent;
    resolved.referenceLoss2GhzDb = referenceLoss2GhzDb;
    resolved.referenceLoss5GhzDb = referenceLoss5GhzDb;
    resolved.nakagamiDistance1M = nakagamiDistance1M;
    resolved.nakagamiDistance2M = nakagamiDistance2M;
    resolved.nakagamiM0 = nakagamiM0;
    resolved.nakagamiM1 = nakagamiM1;
    resolved.nakagamiM2 = nakagamiM2;
    resolved.propagationStreamBase = propagationStreamBase;
    resolved.standard = StandardLabel(wifiStandard);
    resolved.dataMode = dataMode;
    resolved.stationManager = observedStationManager;
    resolved.controlMode = observedControlModes;
    resolved.guardInterval = std::to_string(guardIntervalNs) + "ns";
    resolved.channelSettings =
        topology == "single_link"
            ? std::vector<std::string>{"{36, 20, BAND_5GHZ, 0}"}
            : std::vector<std::string>{"{1, 20, BAND_2_4GHZ, 0}",
                                       "{36, 20, BAND_5GHZ, 0}"};
    resolved.frequencyRanges =
        topology == "single_link"
            ? std::vector<std::string>{"WIFI_SPECTRUM_5_GHZ"}
            : std::vector<std::string>{"WIFI_SPECTRUM_2_4_GHZ",
                                       "WIFI_SPECTRUM_5_GHZ"};
    resolved.perLinkDataModes =
        linkCount == 1 ? std::vector<std::string>{dataMode}
                       : std::vector<std::string>{dataMode, dataMode};
    resolved.queueMaxPackets = queueMaxPackets;
    resolved.queueMaxDelayMs = queueMaxDelayMs;
    resolved.maxAmpduSizeBytes = maxAmpduSize;
    resolved.maxAmsduSizeBytes = maxAmsduSize;
    resolved.mloStaMaxInflights = nativeMlo ? mloStaMaxInflights : 1;
    resolved.ulOfdmaEnabled = ulOfdmaEnabled;
    resolved.ulOfdmaScope = ulOfdmaScope;
    resolved.ulOfdmaAccessIntervalMs = ulOfdmaAccessIntervalMs;
    resolved.ulOfdmaBsrpEnabled = ulOfdmaBsrpEnabled;
    resolved.ulOfdmaMaxStations = ulOfdmaMaxStations;
    resolved.ulOfdmaPsduSizeBytes = ulOfdmaPsduSize;
    resolved.blockAckEnabled = maxAmpduSize > 0;
    resolved.staticAssociation = nativeMlo;
    resolved.tidToLinkMapping = nativeMlo ? "0 0,1" : "not_applicable";
    resolved.strMode = topology == "mlo_str" ? "STR" : "not_applicable";
    resolved.multiLinkMode =
        nativeMlo ? (emlsrMlo ? "EMLSR" : "STR") : "not_applicable";
    resolved.emlsrActivated = emlsrMlo;
    resolved.emlsrProfile =
        emlsrMlo ? "advanced_sta_ap_fixed_aux_v4" : "not_applicable";
    resolved.emlsrManager = emlsrMlo ? "ns3::AdvancedEmlsrManager" : "not_applicable";
    resolved.emlsrApManager =
        emlsrMlo ? "ns3::AdvancedApEmlsrManager" : "not_applicable";
    resolved.emlsrLinkIds = emlsrMlo ? std::vector<uint8_t>{0, 1}
                                     : std::vector<uint8_t>{};
    resolved.emlsrMainPhyId = emlsrMlo ? emlsrMainPhyId : 0;
    resolved.emlsrPaddingDelayUs = emlsrMlo ? emlsrPaddingDelayUs : 0;
    resolved.emlsrTransitionDelayUs = emlsrMlo ? emlsrTransitionDelayUs : 0;
    resolved.emlsrTransitionTimeoutUs = emlsrMlo ? emlsrTransitionTimeoutUs : 0;
    resolved.emlsrMediumSyncDurationUs = emlsrMlo ? emlsrMediumSyncDurationUs : 0;
    resolved.emlsrMsdOfdmEdThresholdDbm =
        emlsrMlo ? emlsrMsdOfdmEdThresholdDbm : 0;
    resolved.emlsrMsdMaxNTxops = emlsrMlo ? emlsrMsdMaxNTxops : 0;
    resolved.emlsrChannelSwitchDelayUs = emlsrMlo ? emlsrChannelSwitchDelayUs : 0;
    resolved.emlsrSwitchAuxPhy = false;
    resolved.emlsrAuxPhyTxCapable = false;
    resolved.emlsrAuxPhyChannelWidthMhz =
        emlsrMlo ? emlsrAuxPhyChannelWidthMhz : 0;
    resolved.emlsrAuxPhyMaxModulationClass = emlsrMlo ? "OFDM" : "not_applicable";
    resolved.emlsrPutAuxPhyToSleep = false;
    resolved.emlsrInDeviceInterference = false;
    resolved.emlsrUseNotifiedMacHeader = emlsrMlo;
    resolved.emlsrResetCamState = false;
    resolved.emlsrAllowUlTxopInRx = false;
    resolved.emlsrInterruptSwitch = false;
    resolved.emlsrUseAuxPhyCca = false;
    resolved.emlsrSwitchMainPhyBackDelayUs =
        emlsrMlo ? emlsrSwitchMainPhyBackDelayUs : 0;
    resolved.emlsrKeepMainPhyAfterDlTxop = false;
    resolved.emlsrCheckAccessOnMainPhyLink = emlsrMlo;
    resolved.emlsrMinAcToSkipCheckAccess = emlsrMlo ? "AC_BK" : "not_applicable";
    resolved.emlsrApUseNotifiedMacHeader = emlsrMlo;
    resolved.emlsrApEarlySwitchToListening = false;
    resolved.emlsrApWaitTransDelayOnPsduRxError = emlsrMlo;
    resolved.emlsrApUpdateCwAfterFailedIcf = emlsrMlo;
    resolved.emlsrApReportFailedIcf = emlsrMlo;
    resolved.emlsrCamGenerateBackoffWithoutTx = false;
    resolved.emlsrCamProactiveBackoff = false;
    resolved.emlsrCamResetBackoffThresholdUs =
        emlsrMlo ? emlsrCamResetBackoffThresholdUs : 0;
    resolved.emlsrCamNSlotsLeft = emlsrMlo ? emlsrCamNSlotsLeft : 0;
    resolved.emlsrCamNSlotsLeftMinDelayUs =
        emlsrMlo ? emlsrCamNSlotsLeftMinDelayUs : 0;
    resolved.emlsrNotifyMacHeaderRxEnd = emlsrMlo;
    resolved.emlsrMainPhyFrequencyRanges =
        emlsrMlo ? std::vector<std::string>{"WIFI_SPECTRUM_2_4_GHZ",
                                            "WIFI_SPECTRUM_5_GHZ"}
                 : std::vector<std::string>{};
    resolved.applicationSocketCount = stationDevices.GetN();
    resolved.applicationDuplication = policyName == "full_duplication";
    resolved.frameRetryLimit = frameRetryLimit;
    resolved.rtsCtsThresholdBytes = rtsCtsThreshold;
    resolved.fragmentationThresholdBytes = fragmentationThreshold;
    resolved.txopLimitUs = txopLimitUs;
    resolved.accessCategory = "AC_BE";
    resolved.staticLink0Score = staticLink0Score;
    resolved.staticLink1Score = staticLink1Score;
    resolved.packetEventLogsEnabled = false;
    resolved.predictionTelemetryEnabled = predictionTelemetryEnabled;
    resolved.predictionSampleOffsetsUs = resolvedPredictionSampleOffsetsUs;
    resolved.predictionHistoryWindowsUs = resolvedPredictionHistoryWindowsUs;
    resolved.predictionPollingIntervalUs = predictionPollingIntervalUs;
    resolved.predictionPollingReportDelayUs = predictionPollingReportDelayUs;
    resolved.predictionEventLogEnabled = predictionEventLogEnabled;
    resolved.predictionOracleFeaturesEnabled = predictionOracleFeaturesEnabled;
    resolved.pairedValueT2AdmissionProfile = pairedValueT2AdmissionProfile;
    resolved.pairedTemporalT2FrameProfile = pairedTemporalT2FrameProfile;
    resolved.selectiveDuplicationThreshold = selectiveDuplicationThreshold;
    resolved.selectiveDuplicationFrameBudget = selectiveDuplicationFrameBudget;
    resolved.selectiveDuplicationBurstHorizonFrames =
        selectiveDuplicationBurstHorizonFrames;
    resolved.selectiveDuplicationDecisionOffsetsUs = resolvedSelectiveDecisionOffsetsUs;
    resolved.secondaryAirtimeMeterEnabled = secondaryAirtimeMeterEnabled;
    resolved.secondaryAirtimeEventSchemaVersion =
        SecondaryAirtimeMeter::EVENT_SCHEMA_VERSION;
    resolved.adaptiveAirtimeBudgetFraction = adaptiveAirtimeBudgetFraction;
    resolved.adaptiveAirtimeBucketHorizonUs = adaptiveAirtimeBucketHorizonUs;
    resolved.adaptiveAirtimeInitialBucketHorizonUs =
        resolvedAdaptiveAirtimeInitialBucketHorizonUs;
    resolved.adaptiveAirtimeInitialShadowPrice = adaptiveAirtimeInitialShadowPrice;
    resolved.adaptiveAirtimeDualStep = adaptiveAirtimeDualStep;
    resolved.adaptiveAirtimeAdmissionUsesRetryInflation =
        adaptiveAirtimeAdmissionUsesRetryInflation;
    resolved.adaptiveAirtimeAdmissionPacketCost = adaptiveAirtimeAdmissionPacketCost;
    resolved.adaptiveAirtimeCostSafetyFactor = adaptiveAirtimeCostSafetyFactor;
    resolved.adaptiveAirtimeCostEwmaAlpha = adaptiveAirtimeCostEwmaAlpha;
    resolved.adaptiveAirtimeDecisionOffsetsUs = resolvedAdaptiveDecisionOffsetsUs;
    resolved.adaptiveAirtimeDecisionOffsetShadowPrices =
        resolvedAdaptiveDecisionOffsetShadowPrices;
    resolved.adaptiveAirtimeIFrameOnlyDecisionOffsetsUs =
        resolvedAdaptiveIFrameOnlyDecisionOffsetsUs;
    if (policyName == "randomized_full_copy_exploration")
    {
        resolved.randomizedAssignmentAlgorithm =
            std::string(RandomizedFrameAssignment::GetAlgorithmId());
        resolved.randomizedAssignmentSalt = randomizedAssignmentSalt;
        resolved.randomizedT2Probability = randomizedT2Probability;
        resolved.randomizedT4Probability = randomizedT4Probability;
        resolved.randomizedAssignmentStopGuardUs = randomizedAssignmentStopGuardUs;
        resolved.randomizedAssignmentWindowStartNs = randomizedAssignmentWindowStartNs;
        resolved.randomizedAssignmentWindowStopNs = randomizedAssignmentWindowStopNs;
        resolved.randomizedCostEstimator =
            std::string(RandomizedInterventionController::GetCostEstimatorId());
    }
    resolved.mechanismTelemetryEnabled = mechanismObservation;
    resolved.mechanismAction =
        policyName == "mechanism_full_copy_t2"
            ? "FULL_COPY_T2"
        : policyName == "mechanism_oracle_repair_t2"
            ? "ORACLE_EVENTUAL_MISSING_REPAIR_T2"
        : policyName == "mechanism_systematic_fec_t2"
            ? "IDEAL_SYSTEMATIC_REPAIR_T2"
            : "OBSERVE";
    resolved.mechanismOraclePacketOutcomeFile = mechanismOraclePacketOutcomeFile;
    resolved.mechanismSystematicRepairDivisor = mechanismSystematicRepairDivisor;
    resolved.fullDuplicationPrimaryPath = fullDuplicationPrimaryPath;
    resolved.backgroundProfile = backgroundProfile;
    resolved.backgroundTraffic = backgroundTraffic;
    resolved.backgroundDirection = backgroundDirection;
    resolved.correlationMode = correlationMode;
    resolved.correlationTrace = correlationTrace;
    resolved.backgroundStations =
        linkCount == 1 ? std::vector<uint32_t>{backgroundStations0}
                       : std::vector<uint32_t>{backgroundStations0, backgroundStations1};
    resolved.backgroundStandards =
        backgroundProfile == "legacy_mixed8"
            ? std::vector<std::string>(linkCount, "mixed")
        : linkCount == 1
            ? std::vector<std::string>{StandardLabel(resolvedBackgroundStandard0)}
            : std::vector<std::string>{StandardLabel(resolvedBackgroundStandard0),
                                       StandardLabel(resolvedBackgroundStandard1)};
    resolved.backgroundStationStandards.resize(linkCount);
    for (uint32_t link = 0; link < linkCount; ++link)
    {
        for (const auto& stationStandard : backgroundStationStandards[link])
        {
            resolved.backgroundStationStandards[link].push_back(
                StandardLabel(stationStandard));
        }
    }
    resolved.backgroundAssociationMode =
        backgroundStations0 + backgroundStations1 == 0
            ? "not_applicable"
            : (nativeMlo ? "passive_scan_to_ap_mld_link" : "passive_scan");
    resolved.backgroundRateMbps = backgroundRateMbps;
    resolved.backgroundPacketSizeBytes = backgroundPacketSize;
    resolved.backgroundNearDistanceM = backgroundNearDistanceM;
    resolved.backgroundFarDistanceM = backgroundFarDistanceM;
    resolved.backgroundStreamBase = backgroundStreamBase;
    resolved.commonOnMeanMs = commonOnMeanMs;
    resolved.commonOffMeanMs = commonOffMeanMs;
    resolved.localOnMeanMs = localOnMeanMs;
    resolved.localOffMeanMs = localOffMeanMs;
    resolved.commonOnDurationMs = commonOnDurationMs;
    resolved.commonOffDurationMs = commonOffDurationMs;
    resolved.localOnDurationMs = localOnDurationMs;
    resolved.localOffDurationMs = localOffDurationMs;
    resolved.randomOnMeanMs = randomOnMeanMs;
    resolved.randomOffMeanMs = randomOffMeanMs;
    resolved.obssProfile = obssProfile;
    resolved.obssStationsPerBss = obssEnabled ? obssStationsPerBss : 0;
    resolved.obssMinRateMbps = std::min(obssUlMinRateMbps, obssDlMinRateMbps);
    resolved.obssMaxRateMbps = std::max(obssUlMaxRateMbps, obssDlMaxRateMbps);
    resolved.obssUlMinRateMbps = obssUlMinRateMbps;
    resolved.obssUlMaxRateMbps = obssUlMaxRateMbps;
    resolved.obssDlMinRateMbps = obssDlMinRateMbps;
    resolved.obssDlMaxRateMbps = obssDlMaxRateMbps;
    resolved.obssOnMeanMs = obssOnMeanMs;
    resolved.obssOffMeanMs = obssOffMeanMs;
    resolved.obssStationManager = obssStationManager;
    resolved.obssManagerUpdateMs = obssManagerUpdateMs;
    resolved.obssUseLatestAmendmentOnly = obssStationManager == "minstrel_ht";
    resolved.obssPacketSizeBytes = obssPacketSize;
    resolved.obssAreaMinXM = obssAreaMinXM;
    resolved.obssAreaMaxXM = obssAreaMaxXM;
    resolved.obssAreaMinYM = obssAreaMinYM;
    resolved.obssAreaMaxYM = obssAreaMaxYM;
    resolved.obssStaMinDistanceM = obssStaMinDistanceM;
    resolved.obssStaMaxDistanceM = obssStaMaxDistanceM;
    resolved.obssPlacementStreamBase = obssPlacementStreamBase;
    resolved.obssApplicationStreamBase = obssApplicationStreamBase;
    resolved.obssWifiStreamBase = obssWifiStreamBase;
    resolved.obssBsses = obssBssConfigs;

    if (projectGitCommit.empty())
    {
        projectGitCommit = WIFI_STREAMING_PROJECT_COMMIT;
    }
    StreamingBuildInfo buildInfo;
    buildInfo.ns3Version = "ns-3.48";
    buildInfo.ns3UpstreamCommit = ExperimentOutput::NS3_UPSTREAM_COMMIT;
    buildInfo.projectGitCommit = projectGitCommit;
    buildInfo.compiler = __VERSION__;
    buildInfo.buildProfile = BuildProfile();
    buildInfo.executionTimestampUtc = UtcTimestamp();
    buildInfo.host = HostName();
    ExperimentOutput::WriteBuildInfo(outputDir, buildInfo);

    Ptr<MetricsCollector> metrics = CreateObject<MetricsCollector>();
    metrics->SetRunId(runId);
    metrics->SetOutputFiles((std::filesystem::path(outputDir) / "frames.csv").string(),
                            (std::filesystem::path(outputDir) / "policy_decisions.csv").string());
    if (mechanismObservation)
    {
        metrics->SetPacketOutcomesFile(
            (std::filesystem::path(outputDir) / "frame_packet_outcomes.csv").string());
    }

    Ptr<PredictionTelemetryCollector> predictionTelemetry;
    if (predictionTelemetryEnabled)
    {
        predictionTelemetry = CreateObject<PredictionTelemetryCollector>();
        predictionTelemetry->SetRunId(runId);
        predictionTelemetry->SetSampleOffsetsUs(resolvedPredictionSampleOffsetsUs);
        predictionTelemetry->SetHistoryWindowsUs(resolvedPredictionHistoryWindowsUs);
        predictionTelemetry->SetPollingIntervalUs(predictionPollingIntervalUs);
        predictionTelemetry->SetPollingReportDelayUs(predictionPollingReportDelayUs);
        predictionTelemetry->SetOracleFeaturesEnabled(predictionOracleFeaturesEnabled);
        predictionTelemetry->SetOutputFiles(
            (std::filesystem::path(outputDir) / "prediction_samples.csv").string(),
            predictionEventLogEnabled
                ? (std::filesystem::path(outputDir) / "prediction_events.csv").string()
                : "",
            (std::filesystem::path(outputDir) / "prediction_polling_samples.csv").string());
        const uint8_t selectedPath = policyName == "fixed_link_0" ? 0 : 1;
        predictionTelemetry->BindWifiPath(selectedPath,
                                          stationDevices.Get(selectedPath),
                                          0,
                                          AC_BE);
        if (policyName == "randomized_full_copy_exploration" ||
            pairedTemporalT2Control || mechanismObservation)
        {
            // Bind the hypothetical secondary after the primary so T0 path
            // history is initialized in the same explicit causal order used
            // for frame-copy registration and snapshot callbacks.
            predictionTelemetry->BindWifiPath(0, stationDevices.Get(0), 0, AC_BE);
        }
    }

    constexpr uint16_t port = 5000;
    Ptr<FrameReceiver> receiver = CreateObject<FrameReceiver>();
    receiver->SetLocal(InetSocketAddress(Ipv4Address::GetAny(), port));
    receiver->SetMetricsCollector(metrics);
    receiver->SetCleanupTimeout(Seconds(1));
    if (policyName == "selective_duplication" ||
        policyName == "adaptive_airtime_duplication" ||
        policyName == "adaptive_deficit_duplication" ||
        policyName == "randomized_full_copy_exploration" ||
        pairedTemporalT2Control || mechanismObservation)
    {
        // Keep primary-only frames open so a causal delayed secondary launch
        // can still be accepted before the deadline.
        receiver->SetHoldForDelayedSecondary(true);
    }
    edge.Get(0)->AddApplication(receiver);
    receiver->SetStartTime(Seconds(0.5));
    receiver->SetStopTime(Seconds(durationSeconds + 2.9));

    Ptr<FrameSource> source;
    if (sourceName == "trace")
    {
        auto traceSource = CreateObject<TraceFrameSource>();
        traceSource->SetFileName(traceFile);
        source = traceSource;
    }
    else
    {
        auto syntheticSource = CreateObject<SyntheticFrameSource>();
        syntheticSource->SetFps(fps);
        syntheticSource->SetDuration(Seconds(durationSeconds));
        syntheticSource->SetConstantFrameSize(frameSize);
        syntheticSource->SetGopLength(gopLength);
        syntheticSource->SetKeyframeSizeMultiplier(keyframeSizeMultiplier);
        syntheticSource->SetDeadline(deadlineUs);
        source = syntheticSource;
    }
    source->AssignStreams(1);

    Ptr<MultipathSender> sender = CreateObject<MultipathSender>();
    sender->SetFrameSource(source);
    sender->SetMetricsCollector(metrics);
    if (predictionTelemetry)
    {
        sender->SetPredictionTelemetryCollector(predictionTelemetry);
    }
    sender->SetPacketPayloadSize(payloadSize);
    if (predictionTelemetry)
    {
        // IPv4 (20) + UDP (8) + LLC/SNAP (8). Fragmentation and
        // A-MSDU are disabled by the prediction telemetry contract.
        sender->SetExpectedMacServiceOverhead(expectedMacServiceOverheadBytes);
    }
    sender->SetEmissionMode(emissionMode == "uniform_within_frame"
                                ? EmissionMode::UNIFORM_WITHIN_FRAME
                                : EmissionMode::BURST);
    sender->SetEmissionSpan(MilliSeconds(5));
    for (uint32_t path = 0; path < stationDevices.GetN(); ++path)
    {
        Ptr<Socket> socket = Socket::CreateSocket(station.Get(0), UdpSocketFactory::GetTypeId());
        NS_ABORT_MSG_IF(socket->Bind(InetSocketAddress(stationAddresses[path], 0)) < 0,
                        "Sender bind failed for path " << path);
        sender->AddPath(path, socket, stationDevices.Get(path));
        NS_ABORT_MSG_IF(
            socket->Connect(InetSocketAddress(edgeDestinations[path], port)) < 0,
            "Sender connect failed for path " << path);
    }
    if (policyName == "fixed_link_0" || policyName == "fixed_link_1")
    {
        auto policy = CreateObject<FixedLinkPolicy>();
        policy->SetPath(policyName == "fixed_link_0" ? 0 : 1);
        sender->SetPolicy(policy);
        if (mechanismObservation)
        {
            sender->SetDelayedSecondaryPath(0);
            sender->SetDelayedSecondaryPredictionTrackingEnabled(true);
        }
    }
    else if (policyName == "static_best")
    {
        auto policy = CreateObject<StaticBestLinkPolicy>();
        policy->SetPathScores(staticLink0Score, staticLink1Score);
        sender->SetPolicy(policy);
    }
    else if (policyName == "selective_duplication")
    {
        auto policy = CreateObject<SelectiveDuplicationPolicy>();
        policy->SetPrimaryPath(1);
        sender->SetPolicy(policy);
        sender->SetDelayedSecondaryPath(0);
    }
    else if (policyName == "adaptive_airtime_duplication")
    {
        auto policy = CreateObject<AdaptiveAirtimeDuplicationPolicy>();
        policy->SetPrimaryPath(1);
        sender->SetPolicy(policy);
        sender->SetDelayedSecondaryPath(0);
    }
    else if (policyName == "adaptive_deficit_duplication")
    {
        auto policy = CreateObject<AdaptiveDeficitDuplicationPolicy>();
        policy->SetPrimaryPath(1);
        sender->SetPolicy(policy);
        sender->SetDelayedSecondaryPath(0);
    }
    else if (policyName == "randomized_full_copy_exploration")
    {
        auto policy = CreateObject<RandomizedFullCopyExplorationPolicy>();
        policy->SetPrimaryPath(1);
        sender->SetPolicy(policy);
        sender->SetDelayedSecondaryPath(0);
        sender->SetDelayedSecondaryPredictionTrackingEnabled(true);
    }
    else if (policyName == "paired_value_duplication_t2")
    {
        auto policy = CreateObject<PairedValueT2Policy>();
        sender->SetPolicy(policy);
        sender->SetDelayedSecondaryPath(PairedValueT2Controller::SECONDARY_PATH_ID);
        sender->SetDelayedSecondaryPredictionTrackingEnabled(true);
    }
    else if (policyName == "distributional_shadow_duplication_t2")
    {
        auto policy = CreateObject<DistributionalShadowT2Policy>();
        sender->SetPolicy(policy);
        sender->SetDelayedSecondaryPath(
            DistributionalShadowT2Controller::SECONDARY_PATH_ID);
        sender->SetDelayedSecondaryPredictionTrackingEnabled(true);
    }
    else if (mechanismT2Control)
    {
        auto policy = CreateObject<MechanismT2Policy>();
        if (policyName == "mechanism_full_copy_t2")
        {
            policy->SetKind(MechanismT2PolicyKind::FULL_COPY);
        }
        else if (policyName == "mechanism_oracle_repair_t2")
        {
            policy->SetKind(MechanismT2PolicyKind::ORACLE_REPAIR);
        }
        else
        {
            policy->SetKind(MechanismT2PolicyKind::SYSTEMATIC_REPAIR);
        }
        sender->SetPolicy(policy);
        sender->SetDelayedSecondaryPath(MechanismExperimentController::SECONDARY_PATH_ID);
        sender->SetDelayedSecondaryPredictionTrackingEnabled(true);
    }
    else
    {
        auto policy = CreateObject<FullDuplicationPolicy>();
        policy->SetPaths(fullDuplicationPrimaryPath, 1 - fullDuplicationPrimaryPath);
        sender->SetPolicy(policy);
    }
    Ptr<ClosedLoopRiskPredictor> closedLoopPredictor;
    Ptr<SelectiveDuplicationController> selectiveController;
    Ptr<AdaptiveAirtimeDuplicationController> adaptiveController;
    Ptr<RandomizedInterventionController> randomizedController;
    Ptr<PairedValueT2Controller> pairedValueController;
    Ptr<DistributionalShadowT2Controller> distributionalShadowController;
    Ptr<MechanismExperimentController> mechanismController;
    Ptr<SecondaryAirtimeMeter> secondaryAirtimeMeter;
    const bool meterWanted =
        secondaryAirtimeMeterEnabled &&
        (policyName == "selective_duplication" ||
         policyName == "adaptive_airtime_duplication" ||
         policyName == "adaptive_deficit_duplication" ||
         policyName == "randomized_full_copy_exploration" ||
         policyName == "paired_value_duplication_t2" ||
         policyName == "distributional_shadow_duplication_t2" ||
         mechanismT2Control ||
         policyName == "full_duplication");
    if (meterWanted)
    {
        NS_ABORT_MSG_IF(stationDevices.GetN() < 1,
                        "Secondary airtime meter requires a secondary path device");
        secondaryAirtimeMeter = CreateObject<SecondaryAirtimeMeter>();
        secondaryAirtimeMeter->SetQueueMaxDelayMs(queueMaxDelayMs);
        secondaryAirtimeMeter->SetMeasurementWindow(warmup.GetNanoSeconds(),
                                                    measurementStop.GetNanoSeconds());
        secondaryAirtimeMeter->BindPath(0, stationDevices.Get(0));
        secondaryAirtimeMeter->SetOutputFiles(
            runId,
            (std::filesystem::path(outputDir) / "secondary_airtime_events.csv").string(),
            (std::filesystem::path(outputDir) / "secondary_airtime_settlements.csv").string(),
            (std::filesystem::path(outputDir) / "secondary_airtime_summary.json").string());
    }
    if (mechanismObservation)
    {
        mechanismController = CreateObject<MechanismExperimentController>();
        mechanismController->SetSender(PeekPointer(sender));
        if (secondaryAirtimeMeter)
        {
            mechanismController->SetAirtimeMeter(secondaryAirtimeMeter);
        }
        if (policyName == "mechanism_full_copy_t2")
        {
            mechanismController->SetAction(MechanismT2Action::FULL_COPY);
        }
        else if (policyName == "mechanism_oracle_repair_t2")
        {
            mechanismController->SetAction(MechanismT2Action::ORACLE_REPAIR);
            mechanismController->SetOraclePacketOutcomeFile(
                mechanismOraclePacketOutcomeFile);
        }
        else if (policyName == "mechanism_systematic_fec_t2")
        {
            mechanismController->SetAction(MechanismT2Action::SYSTEMATIC_REPAIR);
        }
        mechanismController->SetSystematicRepairDivisor(
            mechanismSystematicRepairDivisor);
        mechanismController->SetOutputFiles(
            runId,
            (std::filesystem::path(outputDir) / "mechanism_t2_snapshots.csv").string(),
            (std::filesystem::path(outputDir) / "mechanism_t2_actions.csv").string());
        predictionTelemetry->SetSnapshotCallback(
            MakeCallback(&MechanismExperimentController::NotifySnapshot,
                         PeekPointer(mechanismController)));
    }
    if (policyName == "randomized_full_copy_exploration")
    {
        randomizedController = CreateObject<RandomizedInterventionController>();
        randomizedController->SetSender(PeekPointer(sender));
        randomizedController->SetAirtimeMeter(secondaryAirtimeMeter);
        randomizedController->SetAssignmentParameters(randomizedAssignmentSalt,
                                                       seed,
                                                       run,
                                                       randomizedT2Probability,
                                                       randomizedT4Probability);
        randomizedController->SetAssignmentWindow(randomizedAssignmentWindowStartNs,
                                                  randomizedAssignmentWindowStopNs);
        randomizedController->SetOutputFiles(
            runId,
            (std::filesystem::path(outputDir) / "randomized_intervention_assignments.csv")
                .string(),
            (std::filesystem::path(outputDir) / "randomized_intervention_executions.csv")
                .string());
        predictionTelemetry->SetSnapshotCallback(
            MakeCallback(&RandomizedInterventionController::NotifySnapshot,
                         PeekPointer(randomizedController)));
    }
    if (policyName == "paired_value_duplication_t2")
    {
        pairedValueController = CreateObject<PairedValueT2Controller>();
        pairedValueController->SetAdmissionProfile(
            *resolvedPairedValueT2AdmissionProfile);
        pairedValueController->SetSender(PeekPointer(sender));
        pairedValueController->SetAirtimeMeter(secondaryAirtimeMeter);
        pairedValueController->SetFrameContract(
            deadlineUs,
            frameSize,
            std::max<uint32_t>(
                1,
                static_cast<uint32_t>(std::llround(frameSize * keyframeSizeMultiplier))),
            payloadSize);
        pairedValueController->SetOutputFiles(
            runId,
            (std::filesystem::path(outputDir) / "paired_value_t2_decisions.csv").string(),
            (std::filesystem::path(outputDir) / "paired_value_t2_summary.json").string());
        predictionTelemetry->SetSnapshotCallback(
            MakeCallback(&PairedValueT2Controller::NotifySnapshot,
                         PeekPointer(pairedValueController)));
    }
    if (policyName == "distributional_shadow_duplication_t2")
    {
        distributionalShadowController =
            CreateObject<DistributionalShadowT2Controller>();
        distributionalShadowController->SetSender(PeekPointer(sender));
        distributionalShadowController->SetAirtimeMeter(secondaryAirtimeMeter);
        distributionalShadowController->SetFrameContract(
            deadlineUs,
            frameSize,
            std::max<uint32_t>(
                1,
                static_cast<uint32_t>(std::llround(frameSize * keyframeSizeMultiplier))),
            payloadSize);
        distributionalShadowController->SetOutputFiles(
            runId,
            (std::filesystem::path(outputDir) /
             "distributional_shadow_t2_decisions.csv")
                .string(),
            (std::filesystem::path(outputDir) /
             "distributional_shadow_t2_summary.json")
                .string());
        predictionTelemetry->SetSnapshotCallback(
            MakeCallback(&DistributionalShadowT2Controller::NotifySnapshot,
                         PeekPointer(distributionalShadowController)));
    }
    if (policyName == "selective_duplication")
    {
        closedLoopPredictor = CreateObject<ClosedLoopRiskPredictor>();
        selectiveController = CreateObject<SelectiveDuplicationController>();
        selectiveController->SetSender(PeekPointer(sender));
        selectiveController->SetRiskScorer(
            MakeCallback(&ClosedLoopRiskPredictor::ScorePrimaryMissProbability,
                         PeekPointer(closedLoopPredictor)));
        selectiveController->SetPrimaryPath(1);
        selectiveController->SetProbabilityThreshold(selectiveDuplicationThreshold);
        selectiveController->SetFrameBudget(selectiveDuplicationFrameBudget);
        selectiveController->SetBurstHorizonFrames(
            selectiveDuplicationBurstHorizonFrames);
        selectiveController->SetDecisionOffsetsUs(resolvedSelectiveDecisionOffsetsUs);
        selectiveController->SetOutputFile(
            runId,
            (std::filesystem::path(outputDir) / "selective_duplication_decisions.csv").string());
        predictionTelemetry->SetSnapshotCallback(
            MakeCallback(&SelectiveDuplicationController::NotifySnapshot,
                         PeekPointer(selectiveController)));
    }
    if (policyName == "adaptive_airtime_duplication" ||
        policyName == "adaptive_deficit_duplication")
    {
        closedLoopPredictor = CreateObject<ClosedLoopRiskPredictor>();
        adaptiveController = CreateObject<AdaptiveAirtimeDuplicationController>();
        adaptiveController->SetSender(PeekPointer(sender));
        adaptiveController->SetRiskScorer(
            MakeCallback(&ClosedLoopRiskPredictor::Score, PeekPointer(closedLoopPredictor)));
        adaptiveController->SetAirtimeMeter(secondaryAirtimeMeter);
        adaptiveController->SetPrimaryPath(1);
        if (policyName == "adaptive_deficit_duplication")
        {
            adaptiveController->SetSecondaryPacketSelection(
                AdaptiveSecondaryPacketSelection::PRIMARY_UNACKNOWLEDGED);
        }
        adaptiveController->SetAdmissionPacketCost(resolvedAdaptiveAdmissionPacketCost);
        adaptiveController->SetBudgetFraction(adaptiveAirtimeBudgetFraction);
        adaptiveController->SetBucketHorizonUs(adaptiveAirtimeBucketHorizonUs);
        adaptiveController->SetInitialBucketHorizonUs(
            resolvedAdaptiveAirtimeInitialBucketHorizonUs);
        adaptiveController->SetInitialShadowPrice(adaptiveAirtimeInitialShadowPrice);
        adaptiveController->SetDualStep(adaptiveAirtimeDualStep);
        adaptiveController->SetAdmissionUsesRetryInflation(
            adaptiveAirtimeAdmissionUsesRetryInflation);
        adaptiveController->SetCostSafetyFactor(adaptiveAirtimeCostSafetyFactor);
        adaptiveController->SetCostEwmaAlpha(adaptiveAirtimeCostEwmaAlpha);
        adaptiveController->SetDecisionOffsetsUs(resolvedAdaptiveDecisionOffsetsUs);
        adaptiveController->SetDecisionOffsetShadowPrices(
            resolvedAdaptiveDecisionOffsetShadowPrices);
        adaptiveController->SetIFrameOnlyDecisionOffsetsUs(
            resolvedAdaptiveIFrameOnlyDecisionOffsetsUs);
        const uint32_t referencePacketCount = 1 + (frameSize - 1) / payloadSize;
        const uint64_t referenceExpectedMacServiceBytes =
            static_cast<uint64_t>(frameSize) +
            static_cast<uint64_t>(referencePacketCount) *
                (StreamingHeader::SERIALIZED_SIZE + expectedMacServiceOverheadBytes);
        adaptiveController->SetReferenceCopyDescriptor(referencePacketCount,
                                                       referenceExpectedMacServiceBytes);
        adaptiveController->SetOutputFile(
            runId,
            (std::filesystem::path(outputDir) / "adaptive_airtime_decisions.csv").string());
        predictionTelemetry->SetSnapshotCallback(
            MakeCallback(&AdaptiveAirtimeDuplicationController::NotifySnapshot,
                         PeekPointer(adaptiveController)));
    }
    station.Get(0)->AddApplication(sender);
    sender->SetStartTime(warmup);
    sender->SetStopTime(Seconds(durationSeconds + 2));

    const uint32_t backgroundStationCount = backgroundStations0 + backgroundStations1;
    std::vector<uint64_t> backgroundBytesSentPerStation(backgroundStationCount, 0);
    std::vector<uint64_t> backgroundBytesReceivedPerStation(backgroundStationCount, 0);
    std::vector<Ptr<ControlledUdpApplication>> backgroundUdpSources;
    std::vector<uint32_t> backgroundUdpOrdinals;
    std::vector<Ptr<PacketSink>> backgroundSinks;
    Ptr<CorrelatedLoadController> loadController;
    if (backgroundTraffic == "udp_bursty")
    {
        loadController = CreateObject<CorrelatedLoadController>();
        loadController->SetLinkCount(linkCount);
        loadController->SetMode(correlationMode);
        loadController->SetTraceFile(correlationTrace);
        loadController->SetCommonMeans(MilliSeconds(commonOnMeanMs),
                                       MilliSeconds(commonOffMeanMs));
        loadController->SetLocalMeans(MilliSeconds(localOnMeanMs), MilliSeconds(localOffMeanMs));
        loadController->SetCommonDeterministicDurations(MilliSeconds(commonOnDurationMs),
                                                        MilliSeconds(commonOffDurationMs));
        loadController->SetLocalDeterministicDurations(MilliSeconds(localOnDurationMs),
                                                       MilliSeconds(localOffDurationMs));
        loadController->AssignStreams(backgroundStreamBase);
    }
    uint16_t backgroundPort = 10000;
    uint32_t backgroundOrdinal = 0;
    for (uint32_t link = 0; link < linkCount; ++link)
    {
        for (uint32_t index = 0; index < backgroundStations[link].GetN();
             ++index, ++backgroundOrdinal)
        {
            const bool uplink =
                backgroundDirection == "uplink" ||
                (backgroundDirection == "mixed" && backgroundOrdinal % 2 == 0);
            const Ptr<Node> sourceNode =
                uplink ? backgroundStations[link].Get(index) : edge.Get(0);
            const Ptr<Node> sinkNode =
                uplink ? edge.Get(0) : backgroundStations[link].Get(index);
            const Ipv4Address destination =
                uplink ? edgeDestinations[nativeMlo ? 0 : link]
                       : backgroundAddresses[link][index];
            const Ipv4Address sourceAddress =
                uplink ? backgroundAddresses[link][index]
                       : edgeDestinations[nativeMlo ? 0 : link];
            const std::string protocol = backgroundTraffic == "tcp_bulk"
                                             ? "ns3::TcpSocketFactory"
                                             : "ns3::UdpSocketFactory";
            PacketSinkHelper sinkHelper(protocol,
                                        InetSocketAddress(Ipv4Address::GetAny(),
                                                          backgroundPort));
            auto sinkApplication = sinkHelper.Install(sinkNode).Get(0);
            auto sink = DynamicCast<PacketSink>(sinkApplication);
            sink->SetStartTime(Seconds(0.5));
            sink->SetStopTime(measurementStop + Seconds(1));
            backgroundSinks.push_back(sink);

            if (backgroundTraffic == "tcp_bulk")
            {
                BulkSendHelper bulk("ns3::TcpSocketFactory",
                                    InetSocketAddress(destination, backgroundPort));
                bulk.SetAttribute("MaxBytes", UintegerValue(0));
                bulk.SetAttribute("SendSize", UintegerValue(backgroundPacketSize));
                auto application = bulk.Install(sourceNode).Get(0);
                application->SetStartTime(warmup);
                application->SetStopTime(measurementStop);
                application->TraceConnectWithoutContext(
                    "Tx",
                    MakeBoundCallback(&CountBackgroundTx,
                                      &backgroundBytesSentPerStation[backgroundOrdinal]));
            }
            else if (backgroundTraffic == "udp_random_onoff")
            {
                OnOffHelper onoff("ns3::UdpSocketFactory",
                                  InetSocketAddress(destination, backgroundPort));
                onoff.SetAttribute(
                    "Local",
                    AddressValue(InetSocketAddress(sourceAddress, 0)));
                onoff.SetAttribute(
                    "OnTime",
                    StringValue("ns3::ExponentialRandomVariable[Mean=" +
                                std::to_string(randomOnMeanMs / 1000.0) + "]"));
                onoff.SetAttribute(
                    "OffTime",
                    StringValue("ns3::ExponentialRandomVariable[Mean=" +
                                std::to_string(randomOffMeanMs / 1000.0) + "]"));
                onoff.SetAttribute(
                    "DataRate",
                    DataRateValue(DataRate(static_cast<uint64_t>(backgroundRateMbps * 1e6))));
                onoff.SetAttribute("PacketSize", UintegerValue(backgroundPacketSize));
                auto application =
                    DynamicCast<OnOffApplication>(onoff.Install(sourceNode).Get(0));
                const int64_t applicationStream =
                    backgroundStreamBase +
                    static_cast<int64_t>(resolved.backgroundApplicationStreams.size()) * 2;
                const int64_t consumed = application->AssignStreams(applicationStream);
                NS_ABORT_MSG_IF(consumed != 2,
                                "OnOffApplication random stream count changed; expected 2, got "
                                    << consumed);
                resolved.backgroundApplicationStreams.push_back(applicationStream);
                application->SetStartTime(warmup);
                application->SetStopTime(measurementStop);
                application->TraceConnectWithoutContext(
                    "Tx",
                    MakeBoundCallback(&CountBackgroundTx,
                                      &backgroundBytesSentPerStation[backgroundOrdinal]));
            }
            else
            {
                auto application = CreateObject<ControlledUdpApplication>();
                application->SetRemote(InetSocketAddress(destination, backgroundPort));
                application->SetLocal(InetSocketAddress(sourceAddress, 0));
                application->SetDataRate(
                    DataRate(static_cast<uint64_t>(backgroundRateMbps * 1e6)));
                application->SetPacketSize(backgroundPacketSize);
                application->SetActive(backgroundTraffic == "udp_constant");
                sourceNode->AddApplication(application);
                application->SetStartTime(warmup);
                application->SetStopTime(measurementStop);
                backgroundUdpSources.push_back(application);
                backgroundUdpOrdinals.push_back(backgroundOrdinal);
                if (loadController)
                {
                    loadController->AddApplication(link, application);
                }
            }
            ++backgroundPort;
        }
    }
    if (loadController)
    {
        loadController->Start(warmup, measurementStop);
    }
    std::vector<BackgroundFlowRecord> obssFlowRecords;
    std::vector<BackgroundRatePeriodRecord> obssRatePeriodRecords;
    std::vector<Ptr<RandomRateOnOffApplication>> obssSources;
    std::vector<Ptr<PacketSink>> obssSinks;
    uint16_t obssPort = 20000;
    uint32_t obssFlowOrdinal = 0;
    for (uint32_t bss = 0; bss < (obssEnabled ? obssBssCount : 0); ++bss)
    {
        for (uint32_t index = 0; index < obssStationsPerBss; ++index)
        {
            for (uint32_t direction = 0; direction < 2; ++direction, ++obssFlowOrdinal)
            {
                const bool uplink = direction == 0;
                const Ptr<Node> sourceNode =
                    uplink ? obssStations[bss].Get(index) : obssAccessPoints[bss].Get(0);
                const Ptr<Node> sinkNode =
                    uplink ? obssAccessPoints[bss].Get(0) : obssStations[bss].Get(index);
                const Ipv4Address sourceAddress =
                    uplink ? obssStaAddresses[bss][index] : obssApAddresses[bss];
                const Ipv4Address destination =
                    uplink ? obssApAddresses[bss] : obssStaAddresses[bss][index];

                PacketSinkHelper sinkHelper(
                    "ns3::UdpSocketFactory",
                    InetSocketAddress(Ipv4Address::GetAny(), obssPort));
                Ptr<PacketSink> sink =
                    DynamicCast<PacketSink>(sinkHelper.Install(sinkNode).Get(0));
                sink->SetStartTime(Seconds(0.5));
                sink->SetStopTime(measurementStop + Seconds(1));
                obssSinks.push_back(sink);

                Ptr<RandomRateOnOffApplication> source =
                    CreateObject<RandomRateOnOffApplication>();
                source->SetRemote(InetSocketAddress(destination, obssPort));
                source->SetLocal(InetSocketAddress(sourceAddress, 0));
                source->SetPacketSize(obssPacketSize);
                const double minimumRateMbps =
                    uplink ? obssUlMinRateMbps : obssDlMinRateMbps;
                const double maximumRateMbps =
                    uplink ? obssUlMaxRateMbps : obssDlMaxRateMbps;
                source->SetRateRange(
                    DataRate(static_cast<uint64_t>(minimumRateMbps * 1e6)),
                    DataRate(static_cast<uint64_t>(maximumRateMbps * 1e6)));
                source->SetMeans(MilliSeconds(obssOnMeanMs), MilliSeconds(obssOffMeanMs));
                const int64_t rateStream =
                    obssApplicationStreamBase + static_cast<int64_t>(3 * obssFlowOrdinal);
                const int64_t consumed = source->AssignStreams(rateStream);
                NS_ABORT_MSG_IF(consumed != 3,
                                "RandomRateOnOffApplication stream count changed; expected 3, got "
                                    << consumed);
                sourceNode->AddApplication(source);
                source->SetStartTime(warmup);
                source->SetStopTime(measurementStop);
                obssSources.push_back(source);

                BackgroundFlowRecord record;
                record.runId = runId;
                record.bssId = bss;
                record.linkId = obssLinks[bss];
                record.standard = StandardLabel(obssStandards[bss]);
                record.staIndex = index;
                record.direction = uplink ? "uplink" : "downlink";
                record.sourceNodeId = sourceNode->GetId();
                record.destinationNodeId = sinkNode->GetId();
                record.port = obssPort;
                record.rateStream = rateStream;
                record.onStream = rateStream + 1;
                record.offStream = rateStream + 2;
                obssFlowRecords.push_back(record);
                ++obssPort;
            }
        }
    }
    ExperimentOutput::WriteResolvedConfig(outputDir, resolved);

    OfdmaTelemetry targetOfdma;
    OfdmaTelemetry sameBssOfdma;
    OfdmaTelemetry obssOfdma;
    ConnectOfdmaTelemetry(stationDevices, &targetOfdma);
    ConnectOfdmaTelemetry(apWifiDevices, &targetOfdma);
    for (const auto& devices : backgroundDevices)
    {
        ConnectOfdmaTelemetry(devices, &sameBssOfdma);
    }
    for (uint32_t bss = 0; bss < obssBssCount; ++bss)
    {
        ConnectOfdmaTelemetry(obssStaDevices[bss], &obssOfdma);
        ConnectOfdmaTelemetry(obssApDevices[bss], &obssOfdma);
    }

    WifiTxStatsHelper txStats(warmup, measurementStop);
    txStats.Enable(stationDevices);
    WifiCoTraceHelper occupancy(warmup, measurementStop);
    occupancy.Enable(stationDevices);

    Simulator::Schedule(Seconds(0.9), &PopulateNeighborCaches);
    Simulator::Stop(Seconds(durationSeconds + 3));
    Simulator::Run();
    if (predictionTelemetry)
    {
        // Prediction rows are sender-side artifacts and must be finalized
        // independently of receiver outcome bookkeeping.
        predictionTelemetry->WriteOutputs();
    }
    if (secondaryAirtimeMeter)
    {
        secondaryAirtimeMeter->WriteSummary();
    }
    if (randomizedController)
    {
        NS_ABORT_MSG_IF(
            randomizedController->GetAssignmentCount() !=
                randomizedController->GetExecutionCount(),
            "Randomized intervention did not emit one execution for every assignment");
        NS_ABORT_MSG_IF(
            randomizedController->GetAssignmentCount() !=
                randomizedController->GetT2ArmCount() +
                    randomizedController->GetT4ArmCount() +
                    randomizedController->GetControlArmCount(),
            "Randomized intervention arm counts do not reconcile");
        NS_ABORT_MSG_IF(randomizedController->GetLaunchCount() !=
                            randomizedController->GetSettlementCount(),
                        "Randomized intervention launch/settlement counts do not reconcile");
        NS_ABORT_MSG_IF(randomizedController->GetLaunchCount() >
                            randomizedController->GetLaunchAttemptCount(),
                        "Randomized intervention accepted more launches than it attempted");
    }
    metrics->FinalizeMissingFrames();
    if (mechanismController)
    {
        NS_ABORT_MSG_IF(mechanismController->GetPairedFrameCount() !=
                            metrics->GetFrameResults().size(),
                        "Mechanism T2 pair count differs from generated frame count");
    }
    if (pairedValueController || distributionalShadowController)
    {
        std::set<uint64_t> generatedFrameIds;
        std::set<uint64_t> duplicatedFrameIds;
        for (const auto& frame : metrics->GetFrameResults())
        {
            const bool inserted = generatedFrameIds.insert(frame.frame.frameId).second;
            NS_ABORT_MSG_IF(!inserted,
                            "Paired temporal T2 final output contains duplicate frame ID "
                                << frame.frame.frameId);
            if (frame.duplicated)
            {
                duplicatedFrameIds.insert(frame.frame.frameId);
            }
        }
        if (pairedValueController)
        {
            pairedValueController->WriteSummary(generatedFrameIds.size(),
                                                duplicatedFrameIds);
        }
        else
        {
            distributionalShadowController->WriteSummary(generatedFrameIds.size(),
                                                         duplicatedFrameIds);
        }
    }
    for (std::size_t i = 0; i < backgroundUdpSources.size(); ++i)
    {
        backgroundBytesSentPerStation[backgroundUdpOrdinals[i]] =
            backgroundUdpSources[i]->GetTotalTxBytes();
    }
    for (std::size_t i = 0; i < backgroundSinks.size(); ++i)
    {
        backgroundBytesReceivedPerStation[i] = backgroundSinks[i]->GetTotalRx();
    }
    std::vector<uint64_t> obssBytesSentPerLink(linkCount, 0);
    std::vector<uint64_t> obssBytesReceivedPerLink(linkCount, 0);
    const uint32_t obssStationCount =
        obssEnabled ? obssBssCount * obssStationsPerBss : 0;
    std::vector<uint64_t> obssBytesSentPerStation(obssStationCount, 0);
    std::vector<uint64_t> obssBytesReceivedPerStation(obssStationCount, 0);
    for (std::size_t flow = 0; flow < obssFlowRecords.size(); ++flow)
    {
        auto& record = obssFlowRecords[flow];
        record.bytesSent = obssSources[flow]->GetTotalTxBytes();
        record.bytesReceived = obssSinks[flow]->GetTotalRx();
        const auto& periods = obssSources[flow]->GetPeriodRecords();
        record.periodCount = periods.size();
        obssBytesSentPerLink[record.linkId] += record.bytesSent;
        obssBytesReceivedPerLink[record.linkId] += record.bytesReceived;
        const uint32_t stationIndex = record.bssId * obssStationsPerBss + record.staIndex;
        obssBytesSentPerStation[stationIndex] += record.bytesSent;
        obssBytesReceivedPerStation[stationIndex] += record.bytesReceived;
        for (const auto& period : periods)
        {
            BackgroundRatePeriodRecord periodRecord;
            periodRecord.runId = runId;
            periodRecord.bssId = record.bssId;
            periodRecord.staIndex = record.staIndex;
            periodRecord.direction = record.direction;
            periodRecord.periodIndex = static_cast<uint32_t>(period.index);
            periodRecord.startUs = period.start.GetMicroSeconds();
            periodRecord.endUs = period.end.GetMicroSeconds();
            periodRecord.rateMbps = period.rateBps / 1e6;
            obssRatePeriodRecords.push_back(periodRecord);
        }
    }
    backgroundBytesSentPerStation.insert(backgroundBytesSentPerStation.end(),
                                         obssBytesSentPerStation.begin(),
                                         obssBytesSentPerStation.end());
    backgroundBytesReceivedPerStation.insert(backgroundBytesReceivedPerStation.end(),
                                             obssBytesReceivedPerStation.begin(),
                                             obssBytesReceivedPerStation.end());
    ExperimentOutput::WriteBackgroundFlows(outputDir, obssFlowRecords);
    ExperimentOutput::WriteBackgroundRatePeriods(outputDir, obssRatePeriodRecords);
    const uint64_t backgroundBytesSent =
        std::accumulate(backgroundBytesSentPerStation.begin(),
                        backgroundBytesSentPerStation.end(),
                        uint64_t{0});
    const uint64_t backgroundBytesReceived =
        std::accumulate(backgroundBytesReceivedPerStation.begin(),
                        backgroundBytesReceivedPerStation.end(),
                        uint64_t{0});

    const auto successes = txStats.GetSuccessesByNodeDevice();
    const auto successesByLink = txStats.GetSuccessesByNodeDeviceLink();
    const auto failures = txStats.GetFailuresByNodeDevice();
    const auto retransmissions = txStats.GetRetransmissionsByNodeDevice();
    const auto retryDrops =
        txStats.GetFailuresByNodeDevice(WIFI_MAC_DROP_REACHED_RETRY_LIMIT);
    const auto successRecords = txStats.GetSuccessRecords();
    const auto& occupancyRecords = occupancy.GetDeviceRecords();
    std::vector<LinkIntervalRecord> linkIntervals;
    std::vector<MacSummaryRecord> macSummaries;
    const uint32_t reportedLinkCount = nativeMlo ? 2 : stationDevices.GetN();
    for (uint32_t path = 0; path < reportedLinkCount; ++path)
    {
        const uint32_t nodeId = station.Get(0)->GetId();
        const uint32_t deviceId =
            stationDevices.Get(nativeMlo ? 0 : path)->GetIfIndex();
        const auto deviceKey = std::make_tuple(nodeId, deviceId);
        const auto linkKey =
            std::make_tuple(nodeId, deviceId, static_cast<uint8_t>(nativeMlo ? path : 0));
        const auto lookup = [](const auto& values, const auto& key) {
            const auto value = values.find(key);
            return value == values.end() ? uint64_t{0} : value->second;
        };

        std::vector<double> serviceTimes;
        uint64_t linkRetransmissions = 0;
        if (const auto records = successRecords.find(linkKey); records != successRecords.end())
        {
            for (const auto& record : records->second)
            {
                serviceTimes.push_back((record.m_ackTime - record.m_enqueueTime).GetMicroSeconds());
                linkRetransmissions += record.m_retransmissions;
            }
        }
        std::optional<double> meanServiceTime;
        std::optional<double> p95ServiceTime;
        if (!serviceTimes.empty())
        {
            meanServiceTime =
                std::accumulate(serviceTimes.begin(), serviceTimes.end(), 0.0) /
                serviceTimes.size();
            p95ServiceTime = ExperimentOutput::Percentile(serviceTimes, 0.95);
        }

        const WifiCoTraceHelper::DeviceRecord* occupancyRecord = nullptr;
        for (const auto& record : occupancyRecords)
        {
            if (record.m_nodeId == nodeId && record.m_ifIndex == deviceId)
            {
                occupancyRecord = &record;
                break;
            }
        }
        LinkIntervalRecord interval;
        interval.timestampUs = measurementStop.GetMicroSeconds();
        interval.linkId = path;
        const uint8_t applicationPath = nativeMlo ? 0 : path;
        interval.applicationBytesSent =
            (!nativeMlo || path == 0) ? sender->GetPathBytesSent(applicationPath) : 0;
        interval.applicationBytesReceived =
            (!nativeMlo || path == 0) ? receiver->GetPathBytesReceived(applicationPath) : 0;
        interval.redundantBytes =
            (!nativeMlo || path == 0) ? sender->GetPathRedundantBytesSent(applicationPath) : 0;
        interval.successfulMpdus =
            nativeMlo ? lookup(successesByLink, linkKey) : lookup(successes, deviceKey);
        // ns-3.48 exposes failures only per device; place the device total in
        // link 0 so run-level totals are not double counted.
        interval.failedMpdus = (!nativeMlo || path == 0) ? lookup(failures, deviceKey) : 0;
        interval.retransmissions =
            nativeMlo ? linkRetransmissions : lookup(retransmissions, deviceKey);
        interval.meanMpduServiceTimeUs = meanServiceTime;
        interval.p95MpduServiceTimeUs = p95ServiceTime;
        if (occupancyRecord)
        {
            interval.phyIdleTimeUs =
                GetStateDurationUs(*occupancyRecord, nativeMlo ? path : 0, WifiPhyState::IDLE);
            interval.phyCcaBusyTimeUs =
                GetStateDurationUs(*occupancyRecord,
                                   nativeMlo ? path : 0,
                                   WifiPhyState::CCA_BUSY);
            interval.phyTxTimeUs =
                GetStateDurationUs(*occupancyRecord, nativeMlo ? path : 0, WifiPhyState::TX);
            interval.phyRxTimeUs =
                GetStateDurationUs(*occupancyRecord, nativeMlo ? path : 0, WifiPhyState::RX);
        }
        linkIntervals.push_back(interval);

        MacSummaryRecord mac;
        mac.linkId = path;
        mac.nodeId = nodeId;
        mac.deviceId = deviceId;
        mac.successfulMpdus = interval.successfulMpdus;
        mac.failedMpdus = interval.failedMpdus;
        mac.retransmissions = interval.retransmissions;
        mac.retryLimitDrops =
            (!nativeMlo || path == 0) ? lookup(retryDrops, deviceKey) : 0;
        mac.meanMpduServiceTimeUs = meanServiceTime;
        mac.p95MpduServiceTimeUs = p95ServiceTime;
        macSummaries.push_back(mac);
    }
    if (emlsrMlo)
    {
        NS_ABORT_MSG_IF(!mloRuntimeCaptured,
                        "EMLSR runtime profile was not captured after static setup");
        for (const auto& interval : linkIntervals)
        {
            mloRuntime.successfulMpdusPerLink.push_back(interval.successfulMpdus);
            mloRuntime.phyTxTimeUsPerLink.push_back(interval.phyTxTimeUs);
        }
        ExperimentOutput::WriteMloRuntime(outputDir, mloRuntime);
        NS_ABORT_MSG_IF(mloRuntime.successfulMpdusPerLink.size() != 2 ||
                            mloRuntime.phyTxTimeUsPerLink.size() != 2,
                        "EMLSR runtime activity arrays must contain both setup links");
    }
    ExperimentOutput::WriteLinkIntervals(outputDir, linkIntervals);
    ExperimentOutput::WriteMacSummary(outputDir, macSummaries);
    auto summary = ExperimentOutput::ComputeSummary(metrics->GetFrameResults(),
                                                    durationSeconds,
                                                    sender->GetBytesSent(),
                                                    sender->GetRedundantBytesSent(),
                                                    linkIntervals);
    summary.backgroundBytesSent = backgroundBytesSent;
    summary.backgroundBytesReceived = backgroundBytesReceived;
    summary.backgroundBytesSentPerStation = backgroundBytesSentPerStation;
    summary.backgroundBytesReceivedPerStation = backgroundBytesReceivedPerStation;
    summary.backgroundBytesSentPerLink.assign(linkCount, 0);
    summary.backgroundBytesReceivedPerLink.assign(linkCount, 0);
    uint32_t stationOrdinal = 0;
    for (uint32_t link = 0; link < linkCount; ++link)
    {
        for (uint32_t index = 0; index < backgroundStations[link].GetN();
             ++index, ++stationOrdinal)
        {
            summary.backgroundBytesSentPerLink[link] +=
                backgroundBytesSentPerStation[stationOrdinal];
            summary.backgroundBytesReceivedPerLink[link] +=
                backgroundBytesReceivedPerStation[stationOrdinal];
        }
        summary.backgroundBytesSentPerLink[link] += obssBytesSentPerLink[link];
        summary.backgroundBytesReceivedPerLink[link] += obssBytesReceivedPerLink[link];
    }
    summary.backgroundThroughputMbps =
        backgroundBytesReceived * 8.0 / durationSeconds / 1e6;
    ExperimentOutput::WriteSummary(outputDir, summary);
    WriteOfdmaTelemetry(outputDir, targetOfdma, sameBssOfdma, obssOfdma);
    if (!framesFile.empty())
    {
        std::filesystem::copy_file(std::filesystem::path(outputDir) / "frames.csv",
                                   framesFile,
                                   std::filesystem::copy_options::overwrite_existing);
    }
    if (!decisionsFile.empty())
    {
        std::filesystem::copy_file(std::filesystem::path(outputDir) /
                                       "policy_decisions.csv",
                                   decisionsFile,
                                   std::filesystem::copy_options::overwrite_existing);
    }
    std::cout << "sent_packets=" << sender->GetPacketsSent()
              << " sent_bytes=" << sender->GetBytesSent()
              << " redundant_bytes=" << sender->GetRedundantBytesSent()
              << " link_0_bytes=" << sender->GetPathBytesSent(0)
              << " link_1_bytes=" << sender->GetPathBytesSent(1)
              << " background_tx_bytes=" << backgroundBytesSent
              << " background_rx_bytes=" << backgroundBytesReceived
              << " finalized_frames=" << receiver->GetFinalizedFrameCount() << std::endl;
    Simulator::Destroy();
    return 0;
}
