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
    NS_ABORT_MSG("Wi-Fi standard must be vht, he, or eht: " << name);
    return WIFI_STANDARD_80211ax;
}

std::string
DataModeForStandard(const std::string& name)
{
    return name == "vht" ? "VhtMcs5" : (name == "eht" ? "EhtMcs5" : "HeMcs5");
}

std::string
StandardLabel(const std::string& name)
{
    return name == "vht" ? "802.11ac" : (name == "eht" ? "802.11be" : "802.11ax");
}

uint8_t
StandardRank(const std::string& name)
{
    return name == "vht" ? 0 : (name == "he" ? 1 : 2);
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
    std::string backgroundTraffic = "none";
    std::string backgroundDirection = "uplink";
    std::string correlationMode = "independent";
    std::string correlationTrace;
    uint32_t backgroundStations0 = 0;
    uint32_t backgroundStations1 = 0;
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

    CommandLine command(__FILE__);
    command.AddValue("seed", "ns-3 random seed", seed);
    command.AddValue("run", "ns-3 random run/substream", run);
    command.AddValue("duration", "Frame source duration in seconds", durationSeconds);
    command.AddValue("fps", "Synthetic frame rate", fps);
    command.AddValue("frameSize", "Synthetic frame size in bytes", frameSize);
    command.AddValue("payloadSize", "Streaming payload bytes per UDP datagram", payloadSize);
    command.AddValue("deadlineUs", "Frame deadline in microseconds", deadlineUs);
    command.AddValue("fixedRssDbm", "Fixed received signal strength in dBm", fixedRssDbm);
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
    command.AddValue("wifiStandard", "vht, he, or eht (fixed PHY rate)", wifiStandard);
    command.AddValue("backgroundStandard0",
                     "Link-0 background standard: inherit, vht, he, or eht",
                     backgroundStandard0);
    command.AddValue("backgroundStandard1",
                     "Link-1 background standard: inherit, vht, he, or eht",
                     backgroundStandard1);
    command.AddValue("backgroundTraffic",
                     "none, udp_constant, udp_bursty, or tcp_bulk",
                     backgroundTraffic);
    command.AddValue("backgroundDirection",
                     "uplink, downlink, or mixed (alternates stations)",
                     backgroundDirection);
    command.AddValue("backgroundStations0",
                     "Background stations on link 0",
                     backgroundStations0);
    command.AddValue("backgroundStations1",
                     "Background stations on link 1",
                     backgroundStations1);
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
    command.Parse(argc, argv);
    NS_ABORT_MSG_IF(seed == 0, "seed must be positive");
    NS_ABORT_MSG_IF(run == 0, "run must be positive");
    RngSeedManager::SetSeed(seed);
    RngSeedManager::SetRun(run);
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
    NS_ABORT_MSG_IF(sourceName != "synthetic" && sourceName != "trace",
                    "source must be synthetic or trace");
    NS_ABORT_MSG_IF(sourceName == "trace" && traceFile.empty(),
                    "source=trace requires --traceFile");
    NS_ABORT_MSG_IF(emissionMode != "burst" && emissionMode != "uniform_within_frame",
                    "Unknown emission mode " << emissionMode);
    NS_ABORT_MSG_IF(wifiStandard != "vht" && wifiStandard != "he" && wifiStandard != "eht",
                    "wifiStandard must be vht, he, or eht");
    NS_ABORT_MSG_IF(resolvedBackgroundStandard0 != "vht" &&
                        resolvedBackgroundStandard0 != "he" &&
                        resolvedBackgroundStandard0 != "eht",
                    "backgroundStandard0 must be inherit, vht, he, or eht");
    NS_ABORT_MSG_IF(resolvedBackgroundStandard1 != "vht" &&
                        resolvedBackgroundStandard1 != "he" &&
                        resolvedBackgroundStandard1 != "eht",
                    "backgroundStandard1 must be inherit, vht, he, or eht");
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
                        backgroundTraffic != "udp_bursty" && backgroundTraffic != "tcp_bulk",
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
        (backgroundStations0 > 0 && resolvedBackgroundStandard0 != wifiStandard) ||
        (backgroundStations1 > 0 && resolvedBackgroundStandard1 != wifiStandard);
    NS_ABORT_MSG_IF(heterogeneousBackground && backgroundDirection != "uplink",
                    "Heterogeneous background standards support uplink only; fixed-rate AP "
                    "downlink cannot safely select a legacy station MCS");
    NS_ABORT_MSG_IF(topology == "single_link" && backgroundStations1 != 0,
                    "single_link cannot have backgroundStations1");
    NS_ABORT_MSG_IF(topology == "mlo_str" &&
                        (backgroundTraffic != "none" || backgroundStations0 != 0 ||
                         backgroundStations1 != 0),
                    "Background traffic is not supported by mlo_str in this MVP");
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
    const uint32_t linkCount = topology == "single_link" ? 1 : 2;
    std::vector<NodeContainer> backgroundStations(linkCount);
    backgroundStations[0].Create(backgroundStations0);
    if (linkCount == 2)
    {
        backgroundStations[1].Create(backgroundStations1);
    }

    Config::SetDefault("ns3::WifiMacQueue::MaxSize",
                       QueueSizeValue(QueueSize(std::to_string(queueMaxPackets) + "p")));
    Config::SetDefault("ns3::WifiMacQueue::MaxDelay",
                       TimeValue(MilliSeconds(queueMaxDelayMs)));

    WifiHelper wifi;
    const WifiStandard standard = ParseWifiStandard(wifiStandard);
    const std::string dataMode = DataModeForStandard(wifiStandard);
    wifi.SetStandard(standard);
    if (wifiStandard != "vht")
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
    const auto installWifiLink = [&](const std::string& ssidName,
                                     const std::string& channelSettings,
                                     uint32_t link) {
        Ptr<MultiModelSpectrumChannel> channel = CreateObject<MultiModelSpectrumChannel>();
        Ptr<FixedRssLossModel> loss = CreateObject<FixedRssLossModel>();
        loss->SetRss(fixedRssDbm);
        channel->AddPropagationLossModel(loss);
        channel->SetPropagationDelayModel(CreateObject<ConstantSpeedPropagationDelayModel>());

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
            const std::string& backgroundStandard =
                link == 0 ? resolvedBackgroundStandard0 : resolvedBackgroundStandard1;
            wifi.SetStandard(ParseWifiStandard(backgroundStandard));
            if (backgroundStandard != "vht")
            {
                wifi.ConfigHeOptions("GuardInterval", TimeValue(NanoSeconds(guardIntervalNs)));
            }
            wifi.SetRemoteStationManager(
                "ns3::ConstantRateWifiManager",
                "DataMode",
                StringValue(DataModeForStandard(backgroundStandard)),
                "ControlMode",
                StringValue("OfdmRate24Mbps"),
                "RtsCtsThreshold",
                UintegerValue(rtsCtsThreshold),
                "FragmentationThreshold",
                UintegerValue(fragmentationThreshold));
            backgroundDevices[link] =
                wifi.Install(phy, mac, backgroundStations[link]);
        }
        wifi.SetStandard(standard);
        if (wifiStandard != "vht")
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
        installWifiLink("wifi-streaming", "{36, 20, BAND_5GHZ, 0}", 0);
    }
    else if (topology == "dual_interface")
    {
        installWifiLink("wifi-streaming-2g", "{1, 20, BAND_2_4GHZ, 0}", 0);
        installWifiLink("wifi-streaming-5g", "{36, 20, BAND_5GHZ, 0}", 1);
    }
    else
    {
        Ptr<MultiModelSpectrumChannel> channel2Ghz =
            CreateObject<MultiModelSpectrumChannel>();
        Ptr<FixedRssLossModel> loss2Ghz = CreateObject<FixedRssLossModel>();
        loss2Ghz->SetRss(fixedRssDbm);
        channel2Ghz->AddPropagationLossModel(loss2Ghz);
        channel2Ghz->SetPropagationDelayModel(
            CreateObject<ConstantSpeedPropagationDelayModel>());

        Ptr<MultiModelSpectrumChannel> channel5Ghz =
            CreateObject<MultiModelSpectrumChannel>();
        Ptr<FixedRssLossModel> loss5Ghz = CreateObject<FixedRssLossModel>();
        loss5Ghz->SetRss(fixedRssDbm);
        channel5Ghz->AddPropagationLossModel(loss5Ghz);
        channel5Ghz->SetPropagationDelayModel(
            CreateObject<ConstantSpeedPropagationDelayModel>());

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
        apWifiDevices = wifi.Install(phy, mac, accessPoint);

        Ptr<WifiNetDevice> staMld = DynamicCast<WifiNetDevice>(stationDevices.Get(0));
        Ptr<WifiNetDevice> apMld = DynamicCast<WifiNetDevice>(apWifiDevices.Get(0));
        WifiStaticSetupHelper::SetStaticAssociation(apMld, staMld);
        if (maxAmpduSize > 0)
        {
            WifiStaticSetupHelper::SetStaticBlockAck(apMld, staMld, 0);
            WifiStaticSetupHelper::SetStaticBlockAck(staMld, apMld, 0);
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
    MobilityHelper mobility;
    mobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
    mobility.Install(all);
    accessPoint.Get(0)->GetObject<MobilityModel>()->SetPosition(Vector(0, 0, 0));
    station.Get(0)->GetObject<MobilityModel>()->SetPosition(Vector(1, 0, 0));
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
    for (uint32_t path = 0; path < stationDevices.GetN(); ++path)
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
        for (uint32_t index = 0; index < backgroundStations[path].GetN(); ++index)
        {
            Ptr<Ipv4> backgroundIpv4 =
                backgroundStations[path].Get(index)->GetObject<Ipv4>();
            Ptr<Ipv4StaticRouting> backgroundRouting =
                staticRoutingHelper.GetStaticRouting(backgroundIpv4);
            const uint32_t backgroundInterface =
                backgroundIpv4->GetInterfaceForDevice(backgroundDevices[path].Get(index));
            backgroundRouting->AddHostRouteTo(edgeDestinations[path],
                                              apAddresses[path],
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
    resolved.fixedRssDbm = fixedRssDbm;
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
    resolved.backgroundTraffic = backgroundTraffic;
    resolved.backgroundDirection = backgroundDirection;
    resolved.correlationMode = correlationMode;
    resolved.correlationTrace = correlationTrace;
    resolved.backgroundStations =
        linkCount == 1 ? std::vector<uint32_t>{backgroundStations0}
                       : std::vector<uint32_t>{backgroundStations0, backgroundStations1};
    resolved.backgroundStandards =
        linkCount == 1
            ? std::vector<std::string>{StandardLabel(resolvedBackgroundStandard0)}
            : std::vector<std::string>{StandardLabel(resolvedBackgroundStandard0),
                                       StandardLabel(resolvedBackgroundStandard1)};
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
    ExperimentOutput::WriteResolvedConfig(outputDir, resolved);

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

    uint64_t backgroundBytesSent = 0;
    std::vector<Ptr<ControlledUdpApplication>> backgroundUdpSources;
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
                uplink ? edgeDestinations[link] : backgroundAddresses[link][index];
            const Ipv4Address sourceAddress =
                uplink ? backgroundAddresses[link][index] : edgeDestinations[link];
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
                    MakeBoundCallback(&CountBackgroundTx, &backgroundBytesSent));
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

    WifiTxStatsHelper txStats(warmup, measurementStop);
    txStats.Enable(stationDevices);
    WifiCoTraceHelper occupancy(warmup, measurementStop);
    occupancy.Enable(stationDevices);

    Simulator::Schedule(Seconds(0.9), &PopulateNeighborCaches);
    Simulator::Stop(Seconds(durationSeconds + 3));
    Simulator::Run();
    metrics->FinalizeMissingFrames();
    for (const auto& application : backgroundUdpSources)
    {
        backgroundBytesSent += application->GetTotalTxBytes();
    }
    uint64_t backgroundBytesReceived = 0;
    for (const auto& sink : backgroundSinks)
    {
        backgroundBytesReceived += sink->GetTotalRx();
    }

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
