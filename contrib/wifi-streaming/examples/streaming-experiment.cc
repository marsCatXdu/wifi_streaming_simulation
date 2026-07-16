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

#include <chrono>
#include <cmath>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <numeric>
#include <unistd.h>

using namespace ns3;

namespace
{

#ifndef WIFI_STREAMING_PROJECT_COMMIT
#define WIFI_STREAMING_PROJECT_COMMIT "unknown"
#endif

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

} // namespace

int
main(int argc, char* argv[])
{
    uint32_t seed = 1;
    uint64_t run = 1;
    double durationSeconds = 1.0;
    double fps = 30.0;
    uint32_t frameSize = 12000;
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
    uint32_t queueMaxPackets = 500;
    uint32_t queueMaxDelayMs = 500;
    uint32_t maxAmpduSize = 65535;
    uint32_t maxAmsduSize = 0;
    uint32_t frameRetryLimit = 7;
    uint32_t txopLimitUs = 0;
    uint32_t rtsCtsThreshold = 4692480;
    uint32_t fragmentationThreshold = 65535;
    uint32_t guardIntervalNs = 800;
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
    command.AddValue("frameSize", "Synthetic frame size in bytes", frameSize);
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
    command.AddValue("topology", "single_link, dual_interface, or mlo_str", topology);
    command.AddValue("policy",
                     "fixed_link_0, fixed_link_1, static_best, or full_duplication",
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
    RngSeedManager::SetSeed(seed);
    RngSeedManager::SetRun(run);
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
                            backgroundTraffic != "udp_random_onoff",
                        "legacy_mixed8 requires backgroundTraffic=udp_random_onoff");
        backgroundTraffic = "udp_random_onoff";
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
                        topology != "mlo_str",
                    "Unknown topology " << topology);
    NS_ABORT_MSG_IF(policyName != "fixed_link_0" && policyName != "fixed_link_1" &&
                        policyName != "static_best" && policyName != "full_duplication",
                    "Unknown policy " << policyName);
    NS_ABORT_MSG_IF(topology == "single_link" && policyName != "fixed_link_0",
                    "single_link supports only fixed_link_0");
    NS_ABORT_MSG_IF(topology == "mlo_str" && policyName != "fixed_link_0",
                    "mlo_str uses one native MLO path and supports only fixed_link_0");
    NS_ABORT_MSG_IF(topology == "mlo_str" && wifiStandard != "eht",
                    "mlo_str requires --wifiStandard=eht");
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
    NS_ABORT_MSG_IF(topology == "mlo_str" &&
                        backgroundProfile != "legacy_mixed8" &&
                        (backgroundTraffic != "none" || backgroundStations0 != 0 ||
                         backgroundStations1 != 0),
                    "mlo_str supports background traffic only through legacy_mixed8");
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
    ExperimentOutput::PrepareRunDirectory(outputDir);

    NodeContainer station;
    station.Create(1);
    NodeContainer accessPoint;
    accessPoint.Create(1);
    NodeContainer edge;
    edge.Create(1);
    const bool nativeMlo = topology == "mlo_str";
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
    wifi.SetStandard(standard);
    if (wifiStandard == "he" || wifiStandard == "eht")
    {
        wifi.ConfigHeOptions("GuardInterval", TimeValue(NanoSeconds(guardIntervalNs)));
    }
    wifi.SetRemoteStationManager("ns3::ConstantRateWifiManager",
                                 "DataMode",
                                 StringValue(dataMode),
                                 "ControlMode",
                                 StringValue("OfdmRate24Mbps"),
                                 "RtsCtsThreshold",
                                 UintegerValue(rtsCtsThreshold),
                                 "FragmentationThreshold",
                                 UintegerValue(fragmentationThreshold));
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
                StringValue("OfdmRate24Mbps"),
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
        wifi.SetStandard(standard);
        if (wifiStandard == "he" || wifiStandard == "eht")
        {
            wifi.ConfigHeOptions("GuardInterval", TimeValue(NanoSeconds(guardIntervalNs)));
        }
        wifi.SetRemoteStationManager("ns3::ConstantRateWifiManager",
                                     "DataMode",
                                     StringValue(dataMode),
                                     "ControlMode",
                                     StringValue("OfdmRate24Mbps"),
                                     "RtsCtsThreshold",
                                     UintegerValue(rtsCtsThreshold),
                                     "FragmentationThreshold",
                                     UintegerValue(fragmentationThreshold));
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
        Ptr<MultiModelSpectrumChannel> channel2Ghz = makeWifiChannel(referenceLoss2GhzDb, 0);
        wifiChannels[0] = channel2Ghz;

        Ptr<MultiModelSpectrumChannel> channel5Ghz = makeWifiChannel(referenceLoss5GhzDb, 1);
        wifiChannels[1] = channel5Ghz;

        SpectrumWifiPhyHelper phy(2);
        phy.AddPhyToFreqRangeMapping(0, WIFI_SPECTRUM_2_4_GHZ);
        phy.AddPhyToFreqRangeMapping(1, WIFI_SPECTRUM_5_GHZ);
        phy.AddChannel(channel2Ghz, WIFI_SPECTRUM_2_4_GHZ);
        phy.AddChannel(channel5Ghz, WIFI_SPECTRUM_5_GHZ);
        phy.Set(0, "ChannelSettings", StringValue("{1, 20, BAND_2_4GHZ, 0}"));
        phy.Set(1, "ChannelSettings", StringValue("{36, 20, BAND_5GHZ, 0}"));
        phy.Set("RxGain", DoubleValue(0));

        uint8_t link0 = 0;
        wifi.SetRemoteStationManager(link0,
                                     "ns3::ConstantRateWifiManager",
                                     "DataMode",
                                     StringValue("EhtMcs5"),
                                     "ControlMode",
                                     StringValue("OfdmRate24Mbps"),
                                     "RtsCtsThreshold",
                                     UintegerValue(rtsCtsThreshold),
                                     "FragmentationThreshold",
                                     UintegerValue(fragmentationThreshold));
        wifi.SetRemoteStationManager(1,
                                     "ns3::ConstantRateWifiManager",
                                     "DataMode",
                                     StringValue("EhtMcs5"),
                                     "ControlMode",
                                     StringValue("OfdmRate24Mbps"),
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
        mac.SetEdca(AC_BE,
                    "TxopLimits",
                    StringValue(std::to_string(txopLimitUs) + "us," +
                                std::to_string(txopLimitUs) + "us"));
        stationDevices = wifi.Install(phy, mac, station);

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
        mac.SetEdca(AC_BE,
                    "TxopLimits",
                    StringValue(std::to_string(txopLimitUs) + "us," +
                                std::to_string(txopLimitUs) + "us"));
        apWifiDevices = wifi.Install(phy, mac, accessPoint);

        Ptr<WifiNetDevice> staMld = DynamicCast<WifiNetDevice>(stationDevices.Get(0));
        Ptr<WifiNetDevice> apMld = DynamicCast<WifiNetDevice>(apWifiDevices.Get(0));
        WifiStaticSetupHelper::SetStaticAssociation(apMld, staMld);
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
            WifiStaticSetupHelper::SetStaticBlockAck(apMld, staMld, 0);
            WifiStaticSetupHelper::SetStaticBlockAck(staMld, apMld, 0);
        }
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
                    StringValue("OfdmRate24Mbps"),
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
    resolved.controlMode = "OfdmRate24Mbps";
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
    resolved.blockAckEnabled = maxAmpduSize > 0;
    resolved.staticAssociation = nativeMlo;
    resolved.tidToLinkMapping = nativeMlo ? "0 0,1" : "not_applicable";
    resolved.strMode = nativeMlo ? "STR" : "not_applicable";
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

    constexpr uint16_t port = 5000;
    Ptr<FrameReceiver> receiver = CreateObject<FrameReceiver>();
    receiver->SetLocal(InetSocketAddress(Ipv4Address::GetAny(), port));
    receiver->SetMetricsCollector(metrics);
    receiver->SetCleanupTimeout(Seconds(1));
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
        syntheticSource->SetDeadline(deadlineUs);
        source = syntheticSource;
    }
    source->AssignStreams(1);

    Ptr<MultipathSender> sender = CreateObject<MultipathSender>();
    sender->SetFrameSource(source);
    sender->SetMetricsCollector(metrics);
    sender->SetPacketPayloadSize(payloadSize);
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
    }
    else if (policyName == "static_best")
    {
        auto policy = CreateObject<StaticBestLinkPolicy>();
        policy->SetPathScores(staticLink0Score, staticLink1Score);
        sender->SetPolicy(policy);
    }
    else
    {
        sender->SetPolicy(CreateObject<FullDuplicationPolicy>());
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

    WifiTxStatsHelper txStats(warmup, measurementStop);
    txStats.Enable(stationDevices);
    WifiCoTraceHelper occupancy(warmup, measurementStop);
    occupancy.Enable(stationDevices);

    Simulator::Schedule(Seconds(0.9), &PopulateNeighborCaches);
    Simulator::Stop(Seconds(durationSeconds + 3));
    Simulator::Run();
    metrics->FinalizeMissingFrames();
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
