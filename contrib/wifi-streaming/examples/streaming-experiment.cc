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
    command.AddValue("framesFile", "Per-frame CSV output", framesFile);
    command.AddValue("decisionsFile", "Policy decision CSV output", decisionsFile);
    command.AddValue("runId", "Run identifier stored in CSV output", runId);
    command.Parse(argc, argv);

    NodeContainer station;
    station.Create(1);
    NodeContainer accessPoint;
    accessPoint.Create(1);
    NodeContainer edge;
    edge.Create(1);

    Ptr<MultiModelSpectrumChannel> channel = CreateObject<MultiModelSpectrumChannel>();
    Ptr<FixedRssLossModel> loss = CreateObject<FixedRssLossModel>();
    loss->SetRss(fixedRssDbm);
    channel->AddPropagationLossModel(loss);
    channel->SetPropagationDelayModel(CreateObject<ConstantSpeedPropagationDelayModel>());

    SpectrumWifiPhyHelper phy;
    phy.SetChannel(channel);
    phy.Set("ChannelSettings", StringValue("{36, 20, BAND_5GHZ, 0}"));
    phy.Set("RxGain", DoubleValue(0));

    WifiHelper wifi;
    wifi.SetStandard(WIFI_STANDARD_80211ax);
    wifi.SetRemoteStationManager("ns3::ConstantRateWifiManager",
                                 "DataMode",
                                 StringValue("HeMcs5"),
                                 "ControlMode",
                                 StringValue("OfdmRate24Mbps"));
    WifiMacHelper mac;
    const Ssid ssid("wifi-streaming");
    mac.SetType("ns3::StaWifiMac",
                "Ssid",
                SsidValue(ssid),
                "ActiveProbing",
                BooleanValue(false));
    NetDeviceContainer stationDevice = wifi.Install(phy, mac, station);
    mac.SetType("ns3::ApWifiMac", "Ssid", SsidValue(ssid));
    NetDeviceContainer apWifiDevice = wifi.Install(phy, mac, accessPoint);

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
    NetDeviceContainer wifiDevices(stationDevice, apWifiDevice);
    address.SetBase("10.1.0.0", "255.255.255.0");
    Ipv4InterfaceContainer wifiInterfaces = address.Assign(wifiDevices);
    address.SetBase("10.2.0.0", "255.255.255.0");
    Ipv4InterfaceContainer wiredInterfaces = address.Assign(wiredDevices);

    Ptr<Ipv4> apIpv4 = accessPoint.Get(0)->GetObject<Ipv4>();
    for (uint32_t interface = 1; interface < apIpv4->GetNInterfaces(); ++interface)
    {
        apIpv4->SetForwarding(interface, true);
    }
    Ipv4GlobalRoutingHelper::PopulateRoutingTables();

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

    Ptr<Socket> socket = Socket::CreateSocket(station.Get(0), UdpSocketFactory::GetTypeId());
    NS_ABORT_MSG_IF(socket->Bind(InetSocketAddress(wifiInterfaces.GetAddress(0), 0)) < 0,
                    "Sender bind failed");
    Ptr<MultipathSender> sender = CreateObject<MultipathSender>();
    sender->SetFrameSource(source);
    sender->SetMetricsCollector(metrics);
    sender->SetPacketPayloadSize(payloadSize);
    sender->SetEmissionMode(emissionMode == "uniform_within_frame"
                                ? EmissionMode::UNIFORM_WITHIN_FRAME
                                : EmissionMode::BURST);
    sender->SetEmissionSpan(MilliSeconds(5));
    sender->AddPath(0, socket, stationDevice.Get(0));
    NS_ABORT_MSG_IF(socket->Connect(InetSocketAddress(wiredInterfaces.GetAddress(1), port)) < 0,
                    "Sender connect failed");
    station.Get(0)->AddApplication(sender);
    sender->SetStartTime(Seconds(1));
    sender->SetStopTime(Seconds(durationSeconds + 2));

    Simulator::Schedule(Seconds(0.9), &PopulateNeighborCaches);
    Simulator::Stop(Seconds(durationSeconds + 3));
    Simulator::Run();
    std::cout << "sent_packets=" << sender->GetPacketsSent()
              << " finalized_frames=" << receiver->GetFinalizedFrameCount() << std::endl;
    Simulator::Destroy();
    return 0;
}
