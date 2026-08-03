/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef FRAME_RECEIVER_H
#define FRAME_RECEIVER_H

#include "metrics-collector.h"

#include "ns3/application.h"
#include "ns3/event-id.h"
#include "ns3/socket.h"

#include <map>
#include <set>

namespace ns3
{

/**
 * UDP frame reassembler with union, copy, and link packet state.
 */
class FrameReceiver : public Application
{
  public:
    static TypeId GetTypeId();
    FrameReceiver();
    ~FrameReceiver() override;

    void SetLocal(const Address& local);
    void SetMetricsCollector(Ptr<MetricsCollector> collector);
    void SetCleanupTimeout(Time timeout);

    /**
     * Hold primary-only frames open until a delayed secondary arrives or the
     * deadline/cleanup timer expires.
     *
     * Required for selective/adaptive delayed duplication so a late secondary
     * launch is not discarded after the primary alone has already completed.
     *
     * @param enable Whether to wait for a possible delayed secondary copy.
     */
    void SetHoldForDelayedSecondary(bool enable);

    void ProcessPacket(Ptr<Packet> packet);
    uint32_t GetPendingFrameCount() const;
    uint32_t GetFinalizedFrameCount() const;
    uint64_t GetPathBytesReceived(uint8_t pathId) const;

  protected:
    void StartApplication() override;
    void StopApplication() override;
    void DoDispose() override;

  private:
    struct ReassemblyState
    {
        FrameDescriptor frame;
        std::set<uint32_t> unionPackets;
        std::map<uint8_t, std::set<uint32_t>> copyPackets;
        std::map<uint8_t, std::set<uint32_t>> linkPackets;
        std::map<uint32_t, uint8_t> firstLinkForPacket;
        bool duplicatedFrame{false};
        uint32_t duplicates{0};
        std::optional<uint64_t> firstArrivalUs;
        std::optional<uint64_t> completionUs;
        std::optional<Time> completionTime; ///< Exact completion time for deadline comparison.
        std::map<uint8_t, uint64_t> copyCompletionUs;
        EventId deadlineEvent;
        EventId cleanupEvent;
    };

    void HandleRead(Ptr<Socket> socket);
    void Finalize(uint64_t frameId, bool expired);
    void FinalizeAll();

    Address m_local;
    Ptr<Socket> m_socket;
    Ptr<MetricsCollector> m_collector;
    Time m_cleanupTimeout{Seconds(1)};
    bool m_holdForDelayedSecondary{false}; ///< Wait for delayed copy 1 / deadline.
    std::map<uint64_t, ReassemblyState> m_frames;
    std::set<uint64_t> m_finalizedFrameIds;
    std::map<uint8_t, uint64_t> m_pathBytesReceived;
    uint32_t m_finalized{0};
};

} // namespace ns3

#endif // FRAME_RECEIVER_H
