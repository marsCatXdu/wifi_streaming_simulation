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

} // namespace

int
main(int argc, char* argv[])
{
    double durationSeconds = 1.0;
    double fps = 30.0;
    uint32_t frameSize = 12000;
    uint32_t payloadSize = 1200;
    uint32_t deadlineUs = 33333;
    double fixedRssDbm = -50.0;
    std::string emissionMode = "burst";
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

    CommandLine command(__FILE__);
    command.AddValue("duration", "Frame source duration in seconds", durationSeconds);
    command.AddValue("fps", "Synthetic frame rate", fps);
    command.AddValue("frameSize", "Synthetic frame size in bytes", frameSize);
    command.AddValue("payloadSize", "Streaming payload bytes per UDP datagram", payloadSize);
    command.AddValue("deadlineUs", "Frame deadline in microseconds", deadlineUs);
    command.AddValue("fixedRssDbm", "Fixed received signal strength in dBm", fixedRssDbm);
    command.AddValue("emissionMode", "burst or uniform_within_frame", emissionMode);
    command.AddValue("topology", "single_link or dual_interface", topology);
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
    command.Parse(argc, argv);
    NS_ABORT_MSG_IF(topology != "single_link" && topology != "dual_interface",
                    "Unknown topology " << topology);
    NS_ABORT_MSG_IF(policyName != "fixed_link_0" && policyName != "fixed_link_1" &&
                        policyName != "static_best" && policyName != "full_duplication",
                    "Unknown policy " << policyName);
    NS_ABORT_MSG_IF(topology == "single_link" && policyName != "fixed_link_0",
                    "single_link supports only fixed_link_0");
    NS_ABORT_MSG_IF(durationSeconds <= 0, "duration must be positive");
    NS_ABORT_MSG_IF(emissionMode != "burst" && emissionMode != "uniform_within_frame",
                    "Unknown emission mode " << emissionMode);
    ExperimentOutput::PrepareRunDirectory(outputDir);

    NodeContainer station;
    station.Create(1);
    NodeContainer accessPoint;
    accessPoint.Create(1);
    NodeContainer edge;
    edge.Create(1);

    Config::SetDefault("ns3::WifiMacQueue::MaxSize",
                       QueueSizeValue(QueueSize(std::to_string(queueMaxPackets) + "p")));
    Config::SetDefault("ns3::WifiMacQueue::MaxDelay",
                       TimeValue(MilliSeconds(queueMaxDelayMs)));

    WifiHelper wifi;
    wifi.SetStandard(WIFI_STANDARD_80211ax);
    wifi.ConfigHeOptions("GuardInterval", TimeValue(NanoSeconds(guardIntervalNs)));
    wifi.SetRemoteStationManager("ns3::ConstantRateWifiManager",
                                 "DataMode",
                                 StringValue("HeMcs5"),
                                 "ControlMode",
                                 StringValue("OfdmRate24Mbps"),
                                 "RtsCtsThreshold",
                                 UintegerValue(rtsCtsThreshold),
                                 "FragmentationThreshold",
                                 UintegerValue(fragmentationThreshold));
    NetDeviceContainer stationDevices;
    NetDeviceContainer apWifiDevices;
    const auto installWifiLink = [&](const std::string& ssidName,
                                     const std::string& channelSettings) {
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
        installWifiLink("wifi-streaming", "{36, 20, BAND_5GHZ, 0}");
    }
    else
    {
        installWifiLink("wifi-streaming-2g", "{1, 20, BAND_2_4GHZ, 0}");
        installWifiLink("wifi-streaming-5g", "{36, 20, BAND_5GHZ, 0}");
    }

    NodeContainer all(station, accessPoint, edge);
    MobilityHelper mobility;
    mobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
    mobility.Install(all);

    CsmaHelper csma;
    csma.SetChannelAttribute("DataRate", StringValue("1Gbps"));
    csma.SetChannelAttribute("Delay", TimeValue(MicroSeconds(10)));
    NetDeviceContainer wiredDevices = csma.Install(NodeContainer(accessPoint, edge));

    InternetStackHelper internet;
    internet.Install(all);
    Ipv4AddressHelper address;
    std::vector<Ipv4Address> stationAddresses;
    std::vector<Ipv4Address> apAddresses;
    for (uint32_t path = 0; path < stationDevices.GetN(); ++path)
    {
        NetDeviceContainer wifiPair(stationDevices.Get(path), apWifiDevices.Get(path));
        const std::string network = "10.1." + std::to_string(path) + ".0";
        address.SetBase(network.c_str(), "255.255.255.0");
        const Ipv4InterfaceContainer interfaces = address.Assign(wifiPair);
        stationAddresses.push_back(interfaces.GetAddress(0));
        apAddresses.push_back(interfaces.GetAddress(1));
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
    Ipv4GlobalRoutingHelper::PopulateRoutingTables();

    // One host route per radio lets a socket bound to that NetDevice select the
    // matching gateway even though both routes target the same edge address.
    Ipv4StaticRoutingHelper staticRoutingHelper;
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

    const Time warmup = Seconds(1);
    const Time measurementStop = warmup + Seconds(durationSeconds);

    StreamingRunConfig resolved;
    resolved.runId = runId;
    resolved.topology = topology;
    resolved.policy = policyName;
    resolved.emissionMode = emissionMode;
    resolved.durationSeconds = durationSeconds;
    resolved.warmupSeconds = warmup.GetSeconds();
    resolved.fps = fps;
    resolved.frameSizeBytes = frameSize;
    resolved.payloadSizeBytes = payloadSize;
    resolved.deadlineUs = deadlineUs;
    resolved.fixedRssDbm = fixedRssDbm;
    resolved.standard = "802.11ax";
    resolved.dataMode = "HeMcs5";
    resolved.controlMode = "OfdmRate24Mbps";
    resolved.guardInterval = std::to_string(guardIntervalNs) + "ns";
    resolved.channelSettings =
        topology == "single_link"
            ? std::vector<std::string>{"{36, 20, BAND_5GHZ, 0}"}
            : std::vector<std::string>{"{1, 20, BAND_2_4GHZ, 0}",
                                       "{36, 20, BAND_5GHZ, 0}"};
    resolved.queueMaxPackets = queueMaxPackets;
    resolved.queueMaxDelayMs = queueMaxDelayMs;
    resolved.maxAmpduSizeBytes = maxAmpduSize;
    resolved.maxAmsduSizeBytes = maxAmsduSize;
    resolved.blockAckEnabled = maxAmpduSize > 0;
    resolved.frameRetryLimit = frameRetryLimit;
    resolved.rtsCtsThresholdBytes = rtsCtsThreshold;
    resolved.fragmentationThresholdBytes = fragmentationThreshold;
    resolved.txopLimitUs = txopLimitUs;
    resolved.accessCategory = "AC_BE";
    resolved.staticLink0Score = staticLink0Score;
    resolved.staticLink1Score = staticLink1Score;
    resolved.packetEventLogsEnabled = false;
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

    Ptr<SyntheticFrameSource> source = CreateObject<SyntheticFrameSource>();
    source->SetFps(fps);
    source->SetDuration(Seconds(durationSeconds));
    source->SetConstantFrameSize(frameSize);
    source->SetDeadline(deadlineUs);
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

    WifiTxStatsHelper txStats(warmup, measurementStop);
    txStats.Enable(stationDevices);
    WifiCoTraceHelper occupancy(warmup, measurementStop);
    occupancy.Enable(stationDevices);

    Simulator::Schedule(Seconds(0.9), &PopulateNeighborCaches);
    Simulator::Stop(Seconds(durationSeconds + 3));
    Simulator::Run();
    metrics->FinalizeMissingFrames();

    const auto successes = txStats.GetSuccessesByNodeDevice();
    const auto failures = txStats.GetFailuresByNodeDevice();
    const auto retransmissions = txStats.GetRetransmissionsByNodeDevice();
    const auto retryDrops =
        txStats.GetFailuresByNodeDevice(WIFI_MAC_DROP_REACHED_RETRY_LIMIT);
    const auto successRecords = txStats.GetSuccessRecords();
    const auto& occupancyRecords = occupancy.GetDeviceRecords();
    std::vector<LinkIntervalRecord> linkIntervals;
    std::vector<MacSummaryRecord> macSummaries;
    for (uint32_t path = 0; path < stationDevices.GetN(); ++path)
    {
        const uint32_t nodeId = station.Get(0)->GetId();
        const uint32_t deviceId = stationDevices.Get(path)->GetIfIndex();
        const auto deviceKey = std::make_tuple(nodeId, deviceId);
        const auto linkKey = std::make_tuple(nodeId, deviceId, uint8_t{0});
        const auto lookup = [](const auto& values, const auto& key) {
            const auto value = values.find(key);
            return value == values.end() ? uint64_t{0} : value->second;
        };

        std::vector<double> serviceTimes;
        if (const auto records = successRecords.find(linkKey); records != successRecords.end())
        {
            for (const auto& record : records->second)
            {
                serviceTimes.push_back((record.m_ackTime - record.m_enqueueTime).GetMicroSeconds());
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
        interval.applicationBytesSent = sender->GetPathBytesSent(path);
        interval.applicationBytesReceived = receiver->GetPathBytesReceived(path);
        interval.redundantBytes = sender->GetPathRedundantBytesSent(path);
        interval.successfulMpdus = lookup(successes, deviceKey);
        interval.failedMpdus = lookup(failures, deviceKey);
        interval.retransmissions = lookup(retransmissions, deviceKey);
        interval.meanMpduServiceTimeUs = meanServiceTime;
        interval.p95MpduServiceTimeUs = p95ServiceTime;
        if (occupancyRecord)
        {
            interval.phyIdleTimeUs =
                GetStateDurationUs(*occupancyRecord, 0, WifiPhyState::IDLE);
            interval.phyCcaBusyTimeUs =
                GetStateDurationUs(*occupancyRecord, 0, WifiPhyState::CCA_BUSY);
            interval.phyTxTimeUs = GetStateDurationUs(*occupancyRecord, 0, WifiPhyState::TX);
            interval.phyRxTimeUs = GetStateDurationUs(*occupancyRecord, 0, WifiPhyState::RX);
        }
        linkIntervals.push_back(interval);

        MacSummaryRecord mac;
        mac.linkId = path;
        mac.nodeId = nodeId;
        mac.deviceId = deviceId;
        mac.successfulMpdus = interval.successfulMpdus;
        mac.failedMpdus = interval.failedMpdus;
        mac.retransmissions = interval.retransmissions;
        mac.retryLimitDrops = lookup(retryDrops, deviceKey);
        mac.meanMpduServiceTimeUs = meanServiceTime;
        mac.p95MpduServiceTimeUs = p95ServiceTime;
        macSummaries.push_back(mac);
    }
    ExperimentOutput::WriteLinkIntervals(outputDir, linkIntervals);
    ExperimentOutput::WriteMacSummary(outputDir, macSummaries);
    const auto summary = ExperimentOutput::ComputeSummary(metrics->GetFrameResults(),
                                                          durationSeconds,
                                                          sender->GetBytesSent(),
                                                          sender->GetRedundantBytesSent(),
                                                          linkIntervals);
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
              << " finalized_frames=" << receiver->GetFinalizedFrameCount() << std::endl;
    Simulator::Destroy();
    return 0;
}
