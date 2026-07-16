/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef CONTROLLED_UDP_APPLICATION_H
#define CONTROLLED_UDP_APPLICATION_H

#include "ns3/address.h"
#include "ns3/application.h"
#include "ns3/data-rate.h"
#include "ns3/event-id.h"
#include "ns3/socket.h"

namespace ns3
{

/**
 * Fixed-rate UDP source whose transmission gate is controlled externally.
 */
class ControlledUdpApplication : public Application
{
  public:
    static TypeId GetTypeId();

    ControlledUdpApplication();
    ~ControlledUdpApplication() override;

    void SetRemote(const Address& remote);
    void SetLocal(const Address& local);
    void SetDataRate(DataRate rate);
    void SetPacketSize(uint32_t bytes);
    void SetActive(bool active);
    bool IsActive() const;
    uint64_t GetTotalTxBytes() const;

  private:
    void StartApplication() override;
    void StopApplication() override;
    void Send();
    void ScheduleNext();
    void DoDispose() override;

    Address m_remote;
    Address m_local;
    DataRate m_rate{"1Mbps"};
    uint32_t m_packetSize{1200};
    Ptr<Socket> m_socket;
    EventId m_sendEvent;
    bool m_running{false};
    bool m_active{false};
    uint64_t m_totalTxBytes{0};
};

} // namespace ns3

#endif // CONTROLLED_UDP_APPLICATION_H
