/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef MULTIPATH_SENDER_H
#define MULTIPATH_SENDER_H

#include "frame-packetizer.h"
#include "frame-source.h"
#include "metrics-collector.h"

#include "ns3/application.h"
#include "ns3/event-id.h"
#include "ns3/net-device.h"
#include "ns3/socket.h"

#include <map>
#include <vector>

namespace ns3
{

using PathId = uint8_t;

/**
 * Frame-oriented sender. Its path map and per-frame selection boundary are
 * intentionally ready for later dual-interface policies.
 */
class MultipathSender : public Application
{
  public:
    static TypeId GetTypeId();
    MultipathSender();
    ~MultipathSender() override;

    void SetFrameSource(Ptr<FrameSource> source);
    void SetMetricsCollector(Ptr<MetricsCollector> collector);
    void SetPacketPayloadSize(uint32_t bytes);
    void SetEmissionMode(EmissionMode mode);
    void SetEmissionSpan(Time span);
    void SetRunIdHash(uint64_t hash);
    void SetPrimaryPath(PathId pathId);
    void AddPath(PathId pathId, Ptr<Socket> socket, Ptr<NetDevice> device = nullptr);

    uint64_t GetPacketsSent() const;

  protected:
    void StartApplication() override;
    void StopApplication() override;

  private:
    struct Path
    {
        Ptr<Socket> socket;
        Ptr<NetDevice> device;
    };

    void GenerateFrame(FrameDescriptor frame);
    void SendPacket(PathId pathId, Ptr<Packet> packet);

    Ptr<FrameSource> m_source;
    Ptr<MetricsCollector> m_collector;
    FramePacketizer m_packetizer;
    std::map<PathId, Path> m_paths;
    PathId m_primaryPath{0};
    uint64_t m_runIdHash{0};
    uint64_t m_packetsSent{0};
    std::vector<EventId> m_events;
};

} // namespace ns3

#endif // MULTIPATH_SENDER_H
