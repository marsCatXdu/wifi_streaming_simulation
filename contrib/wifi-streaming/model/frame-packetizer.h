/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef FRAME_PACKETIZER_H
#define FRAME_PACKETIZER_H

#include "streaming-header.h"

#include "ns3/nstime.h"
#include "ns3/packet.h"

#include <vector>

namespace ns3
{

enum class EmissionMode
{
    BURST,
    UNIFORM_WITHIN_FRAME,
    TRACE_DEFINED
};

struct PacketEmission
{
    Ptr<Packet> packet;
    Time offset;
};

/**
 * Splits a frame into MTU-safe application packets and emission offsets.
 */
class FramePacketizer
{
  public:
    void SetPayloadSize(uint32_t bytes);
    uint32_t GetPayloadSize() const;
    void SetEmissionMode(EmissionMode mode);
    void SetEmissionSpan(Time span);

    std::vector<PacketEmission> Packetize(const FrameDescriptor& frame,
                                          uint64_t runIdHash,
                                          uint8_t copyId,
                                          uint8_t linkId,
                                          uint16_t flags = 0) const;

  private:
    uint32_t m_payloadSize{1200};
    EmissionMode m_mode{EmissionMode::BURST};
    Time m_emissionSpan{MilliSeconds(1)};
};

} // namespace ns3

#endif // FRAME_PACKETIZER_H
