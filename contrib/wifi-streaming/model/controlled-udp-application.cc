/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "controlled-udp-application.h"

#include "ns3/abort.h"
#include "ns3/inet-socket-address.h"
#include "ns3/packet.h"
#include "ns3/simulator.h"
#include "ns3/udp-socket-factory.h"

namespace ns3
{

NS_OBJECT_ENSURE_REGISTERED(ControlledUdpApplication);

TypeId
ControlledUdpApplication::GetTypeId()
{
    static TypeId tid =
        TypeId("ns3::ControlledUdpApplication")
            .SetParent<Application>()
            .SetGroupName("WifiStreaming")
            .AddConstructor<ControlledUdpApplication>();
    return tid;
}

ControlledUdpApplication::ControlledUdpApplication() = default;
ControlledUdpApplication::~ControlledUdpApplication() = default;

void
ControlledUdpApplication::SetRemote(const Address& remote)
{
    m_remote = remote;
}

void
ControlledUdpApplication::SetLocal(const Address& local)
{
    m_local = local;
}

void
ControlledUdpApplication::SetDataRate(DataRate rate)
{
    NS_ABORT_MSG_IF(rate.GetBitRate() == 0, "Background UDP rate must be positive");
    m_rate = rate;
}

void
ControlledUdpApplication::SetPacketSize(uint32_t bytes)
{
    NS_ABORT_MSG_IF(bytes == 0, "Background UDP packet size must be positive");
    m_packetSize = bytes;
}

void
ControlledUdpApplication::SetActive(bool active)
{
    m_active = active;
    if (m_running && active && !m_sendEvent.IsPending())
    {
        m_sendEvent = Simulator::ScheduleNow(&ControlledUdpApplication::Send, this);
    }
}

bool
ControlledUdpApplication::IsActive() const
{
    return m_active;
}

uint64_t
ControlledUdpApplication::GetTotalTxBytes() const
{
    return m_totalTxBytes;
}

void
ControlledUdpApplication::StartApplication()
{
    m_running = true;
    m_socket = Socket::CreateSocket(GetNode(), UdpSocketFactory::GetTypeId());
    NS_ABORT_MSG_IF(!m_local.IsInvalid() && m_socket->Bind(m_local) < 0,
                    "Controlled UDP local bind failed");
    NS_ABORT_MSG_IF(m_local.IsInvalid() && m_socket->Bind() < 0,
                    "Controlled UDP wildcard bind failed");
    NS_ABORT_MSG_IF(m_socket->Connect(m_remote) < 0, "Controlled UDP connect failed");
    if (m_active)
    {
        m_sendEvent = Simulator::ScheduleNow(&ControlledUdpApplication::Send, this);
    }
}

void
ControlledUdpApplication::StopApplication()
{
    m_running = false;
    m_sendEvent.Cancel();
    if (m_socket)
    {
        m_socket->Close();
    }
}

void
ControlledUdpApplication::Send()
{
    if (!m_running || !m_active)
    {
        return;
    }
    const int sent = m_socket->Send(Create<Packet>(m_packetSize));
    if (sent > 0)
    {
        m_totalTxBytes += sent;
    }
    ScheduleNext();
}

void
ControlledUdpApplication::ScheduleNext()
{
    const double seconds = m_packetSize * 8.0 / m_rate.GetBitRate();
    m_sendEvent = Simulator::Schedule(Seconds(seconds), &ControlledUdpApplication::Send, this);
}

void
ControlledUdpApplication::DoDispose()
{
    m_socket = nullptr;
    Application::DoDispose();
}

} // namespace ns3
