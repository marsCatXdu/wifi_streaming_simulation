/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "random-rate-on-off-application.h"

#include "ns3/abort.h"
#include "ns3/address.h"
#include "ns3/data-rate.h"
#include "ns3/log.h"
#include "ns3/packet.h"
#include "ns3/random-variable-stream.h"
#include "ns3/simulator.h"
#include "ns3/socket.h"
#include "ns3/uinteger.h"
#include "ns3/udp-socket-factory.h"

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("RandomRateOnOffApplication");
NS_OBJECT_ENSURE_REGISTERED(RandomRateOnOffApplication);

TypeId
RandomRateOnOffApplication::GetTypeId()
{
    static TypeId tid =
        TypeId("ns3::RandomRateOnOffApplication")
            .SetParent<Application>()
            .SetGroupName("WifiStreaming")
            .AddConstructor<RandomRateOnOffApplication>()
            .AddAttribute("Remote",
                          "UDP destination address.",
                          AddressValue(),
                          MakeAddressAccessor(&RandomRateOnOffApplication::m_remote),
                          MakeAddressChecker())
            .AddAttribute("Local",
                          "Local bind address; an invalid address selects a wildcard bind.",
                          AddressValue(),
                          MakeAddressAccessor(&RandomRateOnOffApplication::m_local),
                          MakeAddressChecker())
            .AddAttribute("PacketSize",
                          "UDP payload size in bytes.",
                          UintegerValue(1200),
                          MakeUintegerAccessor(&RandomRateOnOffApplication::m_packetSize),
                          MakeUintegerChecker<uint32_t>(1))
            .AddAttribute("MinDataRate",
                          "Minimum data rate selected for an ON period.",
                          DataRateValue(DataRate("1Mbps")),
                          MakeDataRateAccessor(&RandomRateOnOffApplication::m_minRate),
                          MakeDataRateChecker())
            .AddAttribute("MaxDataRate",
                          "Maximum data rate selected for an ON period.",
                          DataRateValue(DataRate("10Mbps")),
                          MakeDataRateAccessor(&RandomRateOnOffApplication::m_maxRate),
                          MakeDataRateChecker())
            .AddAttribute("OnMean",
                          "Mean of the exponential ON-period duration.",
                          TimeValue(Seconds(1)),
                          MakeTimeAccessor(&RandomRateOnOffApplication::m_onMean),
                          MakeTimeChecker())
            .AddAttribute("OffMean",
                          "Mean of the exponential OFF-period duration.",
                          TimeValue(Seconds(1)),
                          MakeTimeAccessor(&RandomRateOnOffApplication::m_offMean),
                          MakeTimeChecker());
    return tid;
}

RandomRateOnOffApplication::RandomRateOnOffApplication()
    : m_rateVariable(CreateObject<UniformRandomVariable>()),
      m_onVariable(CreateObject<ExponentialRandomVariable>()),
      m_offVariable(CreateObject<ExponentialRandomVariable>())
{
}

RandomRateOnOffApplication::~RandomRateOnOffApplication() = default;

void
RandomRateOnOffApplication::SetRemote(const Address& remote)
{
    NS_ABORT_MSG_IF(m_running, "Cannot change the remote address while the application is running");
    m_remote = remote;
}

void
RandomRateOnOffApplication::SetLocal(const Address& local)
{
    NS_ABORT_MSG_IF(m_running, "Cannot change the local address while the application is running");
    m_local = local;
}

void
RandomRateOnOffApplication::SetPacketSize(uint32_t bytes)
{
    NS_ABORT_MSG_IF(bytes == 0, "Packet size must be positive");
    NS_ABORT_MSG_IF(m_running, "Cannot change packet size while the application is running");
    m_packetSize = bytes;
}

void
RandomRateOnOffApplication::SetRateRange(DataRate minimum, DataRate maximum)
{
    NS_ABORT_MSG_IF(minimum.GetBitRate() == 0, "Minimum data rate must be positive");
    NS_ABORT_MSG_IF(maximum < minimum, "Maximum data rate must not be below minimum data rate");
    NS_ABORT_MSG_IF(m_running, "Cannot change the rate range while the application is running");
    m_minRate = minimum;
    m_maxRate = maximum;
}

void
RandomRateOnOffApplication::SetMeans(Time onMean, Time offMean)
{
    NS_ABORT_MSG_IF(onMean <= Time(0) || offMean <= Time(0),
                    "ON and OFF means must be positive");
    NS_ABORT_MSG_IF(m_running, "Cannot change ON/OFF means while the application is running");
    m_onMean = onMean;
    m_offMean = offMean;
}

int64_t
RandomRateOnOffApplication::AssignStreams(int64_t stream)
{
    m_rateVariable->SetStream(stream);
    m_onVariable->SetStream(stream + 1);
    m_offVariable->SetStream(stream + 2);
    return 3;
}

uint64_t
RandomRateOnOffApplication::GetTotalTxBytes() const
{
    return m_totalTxBytes;
}

const std::vector<RandomRateOnOffApplication::PeriodRecord>&
RandomRateOnOffApplication::GetPeriodRecords() const
{
    return m_periodRecords;
}

void
RandomRateOnOffApplication::StartApplication()
{
    NS_ABORT_MSG_IF(m_remote.IsInvalid(), "A remote UDP address is required");
    NS_ABORT_MSG_IF(m_minRate.GetBitRate() == 0, "Minimum data rate must be positive");
    NS_ABORT_MSG_IF(m_maxRate < m_minRate,
                    "Maximum data rate must not be below minimum data rate");
    NS_ABORT_MSG_IF(m_onMean <= Time(0) || m_offMean <= Time(0),
                    "ON and OFF means must be positive");

    CancelEvents();
    m_socket = Socket::CreateSocket(GetNode(), UdpSocketFactory::GetTypeId());
    const int bindResult = m_local.IsInvalid() ? m_socket->Bind() : m_socket->Bind(m_local);
    NS_ABORT_MSG_IF(bindResult < 0, "Random-rate ON/OFF UDP bind failed");
    NS_ABORT_MSG_IF(m_socket->Connect(m_remote) < 0,
                    "Random-rate ON/OFF UDP connect failed");
    m_socket->SetAllowBroadcast(true);
    m_socket->ShutdownRecv();

    m_running = true;
    m_isOn = false;
    const Time offDuration = Seconds(m_offVariable->GetValue(m_offMean.GetSeconds(), 0));
    m_stateEvent =
        Simulator::Schedule(offDuration, &RandomRateOnOffApplication::StartOnPeriod, this);
}

void
RandomRateOnOffApplication::StopApplication()
{
    m_running = false;
    CancelEvents();
    CloseActivePeriod();
    if (m_socket)
    {
        m_socket->Close();
        m_socket = nullptr;
    }
}

void
RandomRateOnOffApplication::StartOnPeriod()
{
    if (!m_running)
    {
        return;
    }

    m_currentRateBps =
        m_rateVariable->GetValue(static_cast<double>(m_minRate.GetBitRate()),
                                 static_cast<double>(m_maxRate.GetBitRate()));
    m_isOn = true;
    m_periodRecords.push_back(
        {m_periodRecords.size(), Simulator::Now(), Simulator::Now(), m_currentRateBps});

    ScheduleNextPacket();
    const Time onDuration = Seconds(m_onVariable->GetValue(m_onMean.GetSeconds(), 0));
    m_stateEvent =
        Simulator::Schedule(onDuration, &RandomRateOnOffApplication::EndOnPeriod, this);
}

void
RandomRateOnOffApplication::EndOnPeriod()
{
    if (!m_running || !m_isOn)
    {
        return;
    }

    m_sendEvent.Cancel();
    CloseActivePeriod();
    const Time offDuration = Seconds(m_offVariable->GetValue(m_offMean.GetSeconds(), 0));
    m_stateEvent =
        Simulator::Schedule(offDuration, &RandomRateOnOffApplication::StartOnPeriod, this);
}

void
RandomRateOnOffApplication::SendPacket()
{
    if (!m_running || !m_isOn)
    {
        return;
    }

    const int sent = m_socket->Send(Create<Packet>(m_packetSize));
    if (sent > 0)
    {
        m_totalTxBytes += static_cast<uint64_t>(sent);
    }
    ScheduleNextPacket();
}

void
RandomRateOnOffApplication::ScheduleNextPacket()
{
    const double interval = (static_cast<double>(m_packetSize) * 8) / m_currentRateBps;
    m_sendEvent =
        Simulator::Schedule(Seconds(interval), &RandomRateOnOffApplication::SendPacket, this);
}

void
RandomRateOnOffApplication::CancelEvents()
{
    m_stateEvent.Cancel();
    m_sendEvent.Cancel();
}

void
RandomRateOnOffApplication::CloseActivePeriod()
{
    if (m_isOn)
    {
        m_periodRecords.back().end = Simulator::Now();
        m_isOn = false;
    }
}

void
RandomRateOnOffApplication::DoDispose()
{
    StopApplication();
    m_rateVariable = nullptr;
    m_onVariable = nullptr;
    m_offVariable = nullptr;
    Application::DoDispose();
}

} // namespace ns3
