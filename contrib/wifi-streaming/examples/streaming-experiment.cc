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

#include <iostream>

using namespace ns3;

namespace
{

void
PopulateNeighborCaches()
{
    NeighborCacheHelper neighborCache;
    neighborCache.PopulateNeighborCache();
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
    std::string framesFile = "frames.csv";
    std::string decisionsFile = "policy_decisions.csv";
    std::string runId = "single-link";

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
    command.AddValue("framesFile", "Per-frame CSV output", framesFile);
    command.AddValue("decisionsFile", "Policy decision CSV output", decisionsFile);
    command.AddValue("runId", "Run identifier stored in CSV output", runId);
    command.Parse(argc, argv);
    NS_ABORT_MSG_IF(topology != "single_link" && topology != "dual_interface",
                    "Unknown topology " << topology);
    NS_ABORT_MSG_IF(policyName != "fixed_link_0" && policyName != "fixed_link_1" &&
                        policyName != "static_best" && policyName != "full_duplication",
                    "Unknown policy " << policyName);
    NS_ABORT_MSG_IF(topology == "single_link" && policyName != "fixed_link_0",
                    "single_link supports only fixed_link_0");

    NodeContainer station;
    station.Create(1);
    NodeContainer accessPoint;
    accessPoint.Create(1);
    NodeContainer edge;
    edge.Create(1);

    WifiHelper wifi;
    wifi.SetStandard(WIFI_STANDARD_80211ax);
    wifi.SetRemoteStationManager("ns3::ConstantRateWifiManager",
                                 "DataMode",
                                 StringValue("HeMcs5"),
                                 "ControlMode",
                                 StringValue("OfdmRate24Mbps"));
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
                    BooleanValue(false));
        stationDevices.Add(wifi.Install(phy, mac, station));
        mac.SetType("ns3::ApWifiMac", "Ssid", SsidValue(ssid));
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

    Ptr<Ipv4> apIpv4 = accessPoint.Get(0)->GetObject<Ipv4>();
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
        stationRouting->AddHostRouteTo(wiredInterfaces.GetAddress(1),
                                       apAddresses[path],
                                       interface,
                                       path);
    }

    Ptr<MetricsCollector> metrics = CreateObject<MetricsCollector>();
    metrics->SetRunId(runId);
    metrics->SetOutputFiles(framesFile, decisionsFile);

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
            socket->Connect(InetSocketAddress(wiredInterfaces.GetAddress(1), port)) < 0,
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
    sender->SetStartTime(Seconds(1));
    sender->SetStopTime(Seconds(durationSeconds + 2));

    Simulator::Schedule(Seconds(0.9), &PopulateNeighborCaches);
    Simulator::Stop(Seconds(durationSeconds + 3));
    Simulator::Run();
    std::cout << "sent_packets=" << sender->GetPacketsSent()
              << " sent_bytes=" << sender->GetBytesSent()
              << " redundant_bytes=" << sender->GetRedundantBytesSent()
              << " link_0_bytes=" << sender->GetPathBytesSent(0)
              << " link_1_bytes=" << sender->GetPathBytesSent(1)
              << " finalized_frames=" << receiver->GetFinalizedFrameCount() << std::endl;
    Simulator::Destroy();
    return 0;
}
