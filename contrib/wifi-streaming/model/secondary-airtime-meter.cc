/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "secondary-airtime-meter.h"

#include "streaming-frame-tag.h"

#include "ns3/abort.h"
#include "ns3/log.h"
#include "ns3/simulator.h"
#include "ns3/wifi-mac.h"
#include "ns3/wifi-mpdu.h"
#include "ns3/wifi-net-device.h"
#include "ns3/wifi-phy.h"
#include "ns3/wifi-psdu.h"

#include <algorithm>
#include <cmath>
#include <iomanip>
#include <limits>
#include <sstream>

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("SecondaryAirtimeMeter");
NS_OBJECT_ENSURE_REGISTERED(SecondaryAirtimeMeter);

class SecondaryAirtimeMeter::TraceAdapter
{
  public:
    static void PhyTxPsduBegin(SecondaryAirtimeMeter* meter,
                               WifiConstPsduMap psduMap,
                               WifiTxVector txVector,
                               double txPower)
    {
        meter->NotifyPhyTxPsduBegin(std::move(psduMap), txVector, txPower);
    }

    static void AckedMpdu(SecondaryAirtimeMeter* meter, Ptr<const WifiMpdu> mpdu)
    {
        meter->NotifyAckedMpdu(mpdu);
    }

    static void DroppedMpdu(SecondaryAirtimeMeter* meter,
                            WifiMacDropReason reason,
                            Ptr<const WifiMpdu> mpdu)
    {
        meter->NotifyDroppedMpdu(reason, mpdu);
    }
};

TypeId
SecondaryAirtimeMeter::GetTypeId()
{
    static TypeId tid = TypeId("ns3::SecondaryAirtimeMeter")
                            .SetParent<Object>()
                            .SetGroupName("WifiStreaming")
                            .AddConstructor<SecondaryAirtimeMeter>();
    return tid;
}

SecondaryAirtimeMeter::SecondaryAirtimeMeter() = default;

SecondaryAirtimeMeter::~SecondaryAirtimeMeter() = default;

void
SecondaryAirtimeMeter::BindPath(uint8_t pathId, Ptr<NetDevice> device)
{
    NS_ABORT_MSG_IF(pathId != 0, "V1 secondary airtime meter expects path 0");
    NS_ABORT_MSG_IF(!device, "Secondary airtime meter requires a device");
    NS_ABORT_MSG_IF(m_device, "Secondary airtime meter path already bound");
    auto wifi = DynamicCast<WifiNetDevice>(device);
    NS_ABORT_MSG_IF(!wifi, "Secondary airtime meter requires a WifiNetDevice");
    NS_ABORT_MSG_IF(!wifi->GetPhy() || !wifi->GetMac(),
                    "Secondary airtime meter requires PHY and MAC");
    m_pathId = pathId;
    m_device = wifi;
    m_phy = wifi->GetPhy();
    NS_ABORT_MSG_IF(
        !m_phy->TraceConnectWithoutContext(
            "PhyTxPsduBegin",
            MakeBoundCallback(&TraceAdapter::PhyTxPsduBegin, this)),
        "Cannot connect secondary airtime PhyTxPsduBegin trace");
    NS_ABORT_MSG_IF(
        !wifi->GetMac()->TraceConnectWithoutContext(
            "AckedMpdu",
            MakeBoundCallback(&TraceAdapter::AckedMpdu, this)),
        "Cannot connect secondary airtime AckedMpdu trace");
    NS_ABORT_MSG_IF(
        !wifi->GetMac()->TraceConnectWithoutContext(
            "DroppedMpdu",
            MakeBoundCallback(&TraceAdapter::DroppedMpdu, this)),
        "Cannot connect secondary airtime DroppedMpdu trace");
}

void
SecondaryAirtimeMeter::SetOutputFiles(const std::string& runId,
                                      const std::string& eventsFile,
                                      const std::string& settlementsFile,
                                      const std::string& summaryFile)
{
    NS_ABORT_MSG_IF(runId.empty(), "Secondary airtime run ID cannot be empty");
    NS_ABORT_MSG_IF(eventsFile.empty() || settlementsFile.empty() || summaryFile.empty(),
                    "Secondary airtime output paths cannot be empty");
    NS_ABORT_MSG_IF(m_events.is_open(), "Secondary airtime outputs configured twice");
    m_runId = runId;
    m_summaryFile = summaryFile;
    m_events.open(eventsFile, std::ios::out | std::ios::trunc);
    NS_ABORT_MSG_IF(!m_events, "Cannot open secondary airtime events " << eventsFile);
    m_events << std::setprecision(12);
    WriteEventHeader();
    m_settlements.open(settlementsFile, std::ios::out | std::ios::trunc);
    NS_ABORT_MSG_IF(!m_settlements,
                    "Cannot open secondary airtime settlements " << settlementsFile);
    m_settlements << std::setprecision(12);
    m_settlements << "run_id,frame_id,settlement_time_ns,released_airtime_us,"
                     "measured_airtime_us,nominal_airtime_us,fallback\n";
}

void
SecondaryAirtimeMeter::SetMeasurementWindow(uint64_t startTimeNs, uint64_t stopTimeNs)
{
    NS_ABORT_MSG_IF(stopTimeNs <= startTimeNs,
                    "Secondary airtime measurement stop must follow its start");
    NS_ABORT_MSG_IF(m_taggedPpduCount != 0,
                    "Cannot change the secondary airtime window after measurement starts");
    m_measurementStartNs = startTimeNs;
    m_measurementStopNs = stopTimeNs;
}

void
SecondaryAirtimeMeter::SetBudgetMetadata(double fraction, double initialCapacityUs)
{
    NS_ABORT_MSG_IF(!std::isfinite(fraction) || fraction <= 0 || fraction > 1,
                    "Secondary airtime budget fraction must be in (0, 1]");
    NS_ABORT_MSG_IF(!std::isfinite(initialCapacityUs) || initialCapacityUs <= 0,
                    "Secondary airtime initial capacity must be positive");
    NS_ABORT_MSG_IF(m_budgetFraction || m_initialCapacityUs,
                    "Secondary airtime budget metadata was configured twice");
    m_budgetFraction = fraction;
    m_initialCapacityUs = initialCapacityUs;
}

void
SecondaryAirtimeMeter::SetQueueMaxDelayMs(uint32_t delayMs)
{
    NS_ABORT_MSG_IF(delayMs == 0, "Secondary airtime queue delay must be positive");
    m_queueMaxDelayMs = delayMs;
}

void
SecondaryAirtimeMeter::SetMeasuredAirtimeCallback(MeasuredAirtimeCallback callback)
{
    m_measuredCallback = std::move(callback);
}

void
SecondaryAirtimeMeter::SetSettlementCallback(SettlementCallback callback)
{
    m_settlementCallback = std::move(callback);
}

void
SecondaryAirtimeMeter::RegisterLaunchedCopy(SecondaryAirtimeReservation reservation)
{
    NS_ABORT_MSG_IF(reservation.packetCount == 0,
                    "Secondary airtime reservation requires packets");
    NS_ABORT_MSG_IF(!std::isfinite(reservation.reservedAirtimeUs) ||
                        reservation.reservedAirtimeUs < 0 ||
                        !std::isfinite(reservation.estimatedAirtimeUs) ||
                        reservation.estimatedAirtimeUs <= 0 ||
                        !std::isfinite(reservation.nominalAirtimeUs) ||
                        reservation.nominalAirtimeUs <= 0,
                    "Secondary airtime reservation contains invalid costs");
    NS_ABORT_MSG_IF(reservation.reservedAirtimeUs > reservation.estimatedAirtimeUs,
                    "Secondary airtime reservation exceeds its original estimate");
    NS_ABORT_MSG_IF(m_reservations.contains(reservation.frameId),
                    "Duplicate secondary airtime reservation for frame "
                        << reservation.frameId);
    NS_ABORT_MSG_IF(!reservation.terminalPacketIndices.empty(),
                    "New secondary airtime reservation is already terminal");
    if (reservation.expectedPacketIndices.empty())
    {
        for (uint32_t index = 0; index < reservation.packetCount; ++index)
        {
            reservation.expectedPacketIndices.insert(index);
        }
    }
    NS_ABORT_MSG_IF(reservation.expectedPacketIndices.size() != reservation.packetCount,
                    "Secondary airtime expected packet set has the wrong size");
    m_reservedAirtimeUs += reservation.reservedAirtimeUs;
    m_estimatedActionAirtimeUs += reservation.estimatedAirtimeUs;
    const uint64_t fallbackDelayNs =
        static_cast<uint64_t>(m_queueMaxDelayMs) * 1000000ULL + 1000000ULL;
    NS_ABORT_MSG_IF(reservation.deadlineTimeNs >
                        std::numeric_limits<uint64_t>::max() - fallbackDelayNs,
                    "Secondary airtime fallback timestamp overflows");
    const uint64_t settleAtNs = reservation.deadlineTimeNs + fallbackDelayNs;
    const int64_t nowNs = Simulator::Now().GetNanoSeconds();
    const uint64_t delayNs =
        settleAtNs > static_cast<uint64_t>(std::max<int64_t>(0, nowNs))
            ? settleAtNs - static_cast<uint64_t>(std::max<int64_t>(0, nowNs))
            : 0;
    reservation.settlementEvent =
        Simulator::Schedule(NanoSeconds(delayNs),
                            &SecondaryAirtimeMeter::SettleFrame,
                            this,
                            reservation.frameId,
                            true);
    m_reservations.emplace(reservation.frameId, std::move(reservation));
}

double
SecondaryAirtimeMeter::ReleaseReservation(uint64_t frameId)
{
    auto iterator = m_reservations.find(frameId);
    if (iterator == m_reservations.end())
    {
        return 0;
    }
    auto& reservation = iterator->second;
    reservation.settlementEvent.Cancel();
    const double released = reservation.reservedAirtimeUs;
    m_reservedAirtimeUs = std::max(0.0, m_reservedAirtimeUs - released);
    reservation.reservedAirtimeUs = 0;
    reservation.settled = true;
    m_reservations.erase(iterator);
    return released;
}

double
SecondaryAirtimeMeter::GetReservedAirtimeUs() const
{
    return m_reservedAirtimeUs;
}

double
SecondaryAirtimeMeter::GetMeasuredAirtimeTotalUs() const
{
    return m_measuredAirtimeTotalUs;
}

uint64_t
SecondaryAirtimeMeter::GetMixedPpduCount() const
{
    return m_mixedPpduCount;
}

uint64_t
SecondaryAirtimeMeter::GetTaggedPpduCount() const
{
    return m_taggedPpduCount;
}

uint64_t
SecondaryAirtimeMeter::GetForcedReservationSettlements() const
{
    return m_forcedSettlements;
}

double
SecondaryAirtimeMeter::GetMaximumBudgetDebtUs() const
{
    return m_maximumBudgetDebtUs;
}

void
SecondaryAirtimeMeter::ObserveBudgetDebt(double debtUs)
{
    if (std::isfinite(debtUs) && debtUs > m_maximumBudgetDebtUs)
    {
        m_maximumBudgetDebtUs = debtUs;
    }
}

double
SecondaryAirtimeMeter::GetEstimatedActionAirtimeUs() const
{
    return m_estimatedActionAirtimeUs;
}

void
SecondaryAirtimeMeter::NotifyPhyTxPsduBegin(WifiConstPsduMap psduMap,
                                            WifiTxVector txVector,
                                            double txPower)
{
    (void)txPower;
    NS_ABORT_MSG_IF(!m_phy, "Secondary airtime meter PHY is unbound");
    const uint64_t nowNs = static_cast<uint64_t>(
        std::max<int64_t>(0, Simulator::Now().GetNanoSeconds()));
    if (!IsWithinMeasurementWindow(nowNs))
    {
        return;
    }
    const Time duration = WifiPhy::CalculateTxDuration(psduMap, txVector, m_phy->GetPhyBand());
    const double durationUs = duration.GetSeconds() * 1e6;
    uint64_t taggedBytes = 0;
    uint64_t otherDataBytes = 0;
    std::map<uint64_t, uint64_t> frameBytes;
    for (const auto& [staId, psdu] : psduMap)
    {
        (void)staId;
        if (!psdu)
        {
            continue;
        }
        for (const auto& mpdu : *psdu)
        {
            if (!mpdu)
            {
                continue;
            }
            const auto& header = mpdu->GetHeader();
            if (!(header.IsData() && header.HasData()))
            {
                continue;
            }
            StreamingFrameTag tag;
            const bool tagged =
                mpdu->GetOriginal()->GetPacket()->PeekPacketTag(tag);
            const uint32_t bytes = mpdu->GetSize();
            if (tagged && tag.pathId == 0 && tag.copyId == 1)
            {
                taggedBytes += bytes;
                frameBytes[tag.frameId] += bytes;
            }
            else
            {
                otherDataBytes += bytes;
            }
        }
    }

    if (taggedBytes == 0)
    {
        return;
    }

    const bool mixed = otherDataBytes > 0;
    if (mixed)
    {
        ++m_mixedPpduCount;
    }
    ++m_taggedPpduCount;
    m_measuredAirtimeTotalUs += durationUs;

    std::vector<uint64_t> frameIds;
    double allocatedSum = 0;
    std::size_t index = 0;
    const std::size_t count = frameBytes.size();
    for (const auto& [frameId, bytes] : frameBytes)
    {
        frameIds.push_back(frameId);
        double allocated = durationUs * static_cast<double>(bytes) /
                           static_cast<double>(taggedBytes);
        ++index;
        if (index == count)
        {
            allocated = durationUs - allocatedSum;
        }
        allocatedSum += allocated;
        auto reservation = m_reservations.find(frameId);
        if (reservation != m_reservations.end() && !reservation->second.settled)
        {
            reservation->second.measuredAirtimeUs += allocated;
            const double reduce =
                std::min(reservation->second.reservedAirtimeUs, allocated);
            reservation->second.reservedAirtimeUs -= reduce;
            m_reservedAirtimeUs = std::max(0.0, m_reservedAirtimeUs - reduce);
        }
        if (!m_measuredCallback.IsNull())
        {
            m_measuredCallback(frameId, allocated, durationUs);
        }
    }

    WriteEvent(Simulator::Now().GetNanoSeconds(),
               m_pathId,
               durationUs,
               taggedBytes,
               frameIds,
               mixed);
}

void
SecondaryAirtimeMeter::NotifyAckedMpdu(Ptr<const WifiMpdu> mpdu)
{
    if (!mpdu)
    {
        return;
    }
    StreamingFrameTag tag;
    if (!mpdu->GetOriginal()->GetPacket()->PeekPacketTag(tag))
    {
        return;
    }
    if (!(tag.pathId == 0 && tag.copyId == 1))
    {
        return;
    }
    MarkPacketTerminal(tag.frameId, tag.packetIndex);
}

void
SecondaryAirtimeMeter::NotifyDroppedMpdu(WifiMacDropReason, Ptr<const WifiMpdu> mpdu)
{
    if (!mpdu)
    {
        return;
    }
    StreamingFrameTag tag;
    if (!mpdu->GetOriginal()->GetPacket()->PeekPacketTag(tag))
    {
        return;
    }
    if (!(tag.pathId == 0 && tag.copyId == 1))
    {
        return;
    }
    MarkPacketTerminal(tag.frameId, tag.packetIndex);
}

void
SecondaryAirtimeMeter::MarkPacketTerminal(uint64_t frameId, uint32_t packetIndex)
{
    auto iterator = m_reservations.find(frameId);
    if (iterator == m_reservations.end() || iterator->second.settled)
    {
        return;
    }
    auto& reservation = iterator->second;
    NS_ABORT_MSG_IF(!reservation.expectedPacketIndices.contains(packetIndex),
                    "Secondary airtime terminal packet was not part of the launch");
    const bool inserted = reservation.terminalPacketIndices.insert(packetIndex).second;
    if (!inserted)
    {
        return;
    }
    if (reservation.terminalPacketIndices.size() == reservation.packetCount)
    {
        SettleFrame(frameId, false);
    }
}

void
SecondaryAirtimeMeter::SettleFrame(uint64_t frameId, bool fallback)
{
    auto iterator = m_reservations.find(frameId);
    if (iterator == m_reservations.end() || iterator->second.settled)
    {
        return;
    }
    auto& reservation = iterator->second;
    reservation.settlementEvent.Cancel();
    reservation.settled = true;
    reservation.fallbackSettled = fallback;
    if (fallback)
    {
        ++m_forcedSettlements;
    }
    const double released = reservation.reservedAirtimeUs;
    m_reservedAirtimeUs = std::max(0.0, m_reservedAirtimeUs - released);
    reservation.reservedAirtimeUs = 0;
    WriteSettlement(frameId,
                    released,
                    reservation.measuredAirtimeUs,
                    reservation.nominalAirtimeUs,
                    fallback);
    if (!m_settlementCallback.IsNull())
    {
        m_settlementCallback(frameId,
                             released,
                             reservation.measuredAirtimeUs,
                             reservation.nominalAirtimeUs,
                             fallback);
    }
    m_reservations.erase(iterator);
}

void
SecondaryAirtimeMeter::WriteSettlement(uint64_t frameId,
                                       double releasedUs,
                                       double measuredUs,
                                       double nominalUs,
                                       bool fallback)
{
    if (!m_settlements)
    {
        return;
    }
    m_settlements << m_runId << ',' << frameId << ',' << Simulator::Now().GetNanoSeconds() << ','
                  << releasedUs << ',' << measuredUs << ',' << nominalUs << ',' << fallback
                  << '\n';
    m_settlements.flush();
}

bool
SecondaryAirtimeMeter::IsWithinMeasurementWindow(uint64_t timeNs) const
{
    if (!m_measurementStartNs || !m_measurementStopNs)
    {
        return true;
    }
    return timeNs >= *m_measurementStartNs && timeNs < *m_measurementStopNs;
}

void
SecondaryAirtimeMeter::WriteEventHeader()
{
    m_events << "run_id,time_ns,path_id,ppdu_duration_us,tagged_mpdu_bytes,frame_ids,"
                "mixed_ppdu,cumulative_tagged_airtime_us\n";
}

void
SecondaryAirtimeMeter::WriteEvent(uint64_t timeNs,
                                  uint8_t pathId,
                                  double ppduDurationUs,
                                  uint64_t taggedMpduBytes,
                                  const std::vector<uint64_t>& frameIds,
                                  bool mixedPpdu)
{
    if (!m_events)
    {
        return;
    }
    std::ostringstream frames;
    for (std::size_t index = 0; index < frameIds.size(); ++index)
    {
        frames << (index == 0 ? "" : ";") << frameIds[index];
    }
    m_events << m_runId << ',' << timeNs << ',' << +pathId << ',' << ppduDurationUs << ','
             << taggedMpduBytes << ',' << frames.str() << ',' << mixedPpdu << ','
             << m_measuredAirtimeTotalUs << '\n';
    m_events.flush();
}

void
SecondaryAirtimeMeter::ApplyTestPpdu(const std::map<uint64_t, uint64_t>& frameBytes,
                                     double ppduDurationUs,
                                     uint64_t otherDataBytes)
{
    const uint64_t nowNs = static_cast<uint64_t>(
        std::max<int64_t>(0, Simulator::Now().GetNanoSeconds()));
    if (!IsWithinMeasurementWindow(nowNs))
    {
        return;
    }
    uint64_t taggedBytes = 0;
    for (const auto& [frameId, bytes] : frameBytes)
    {
        (void)frameId;
        taggedBytes += bytes;
    }
    if (taggedBytes == 0)
    {
        return;
    }
    const bool mixed = otherDataBytes > 0;
    if (mixed)
    {
        ++m_mixedPpduCount;
    }
    ++m_taggedPpduCount;
    m_measuredAirtimeTotalUs += ppduDurationUs;

    std::vector<uint64_t> frameIds;
    double allocatedSum = 0;
    std::size_t index = 0;
    const std::size_t count = frameBytes.size();
    for (const auto& [frameId, bytes] : frameBytes)
    {
        frameIds.push_back(frameId);
        double allocated = ppduDurationUs * static_cast<double>(bytes) /
                           static_cast<double>(taggedBytes);
        ++index;
        if (index == count)
        {
            allocated = ppduDurationUs - allocatedSum;
        }
        allocatedSum += allocated;
        auto reservation = m_reservations.find(frameId);
        if (reservation != m_reservations.end() && !reservation->second.settled)
        {
            reservation->second.measuredAirtimeUs += allocated;
            const double reduce =
                std::min(reservation->second.reservedAirtimeUs, allocated);
            reservation->second.reservedAirtimeUs -= reduce;
            m_reservedAirtimeUs = std::max(0.0, m_reservedAirtimeUs - reduce);
        }
        if (!m_measuredCallback.IsNull())
        {
            m_measuredCallback(frameId, allocated, ppduDurationUs);
        }
    }
    WriteEvent(Simulator::Now().GetNanoSeconds(),
               m_pathId,
               ppduDurationUs,
               taggedBytes,
               frameIds,
               mixed);
}

void
SecondaryAirtimeMeter::WriteSummary()
{
    NS_ABORT_MSG_IF(m_summaryFile.empty(), "Secondary airtime summary path is unset");
    NS_ABORT_MSG_IF(!m_measurementStartNs || !m_measurementStopNs,
                    "Secondary airtime measurement window is unset");
    // Settle any remaining reservations at end of run for deterministic output.
    std::vector<uint64_t> open;
    for (const auto& [frameId, reservation] : m_reservations)
    {
        if (!reservation.settled)
        {
            open.push_back(frameId);
        }
    }
    for (uint64_t frameId : open)
    {
        SettleFrame(frameId, true);
    }

    const double ratio =
        m_estimatedActionAirtimeUs > 0
            ? m_measuredAirtimeTotalUs / m_estimatedActionAirtimeUs
            : 0.0;
    const double measurementDurationUs =
        static_cast<double>(*m_measurementStopNs - *m_measurementStartNs) / 1000.0;
    const double fraction = m_measuredAirtimeTotalUs / measurementDurationUs;
    const std::optional<double> finiteRunBudgetUs =
        m_budgetFraction && m_initialCapacityUs
            ? std::optional<double>(*m_budgetFraction * measurementDurationUs +
                                    *m_initialCapacityUs)
            : std::nullopt;
    const std::optional<double> budgetExcessUs =
        finiteRunBudgetUs
            ? std::optional<double>(std::max(0.0,
                                             m_measuredAirtimeTotalUs - *finiteRunBudgetUs))
            : std::nullopt;
    std::ofstream summary(m_summaryFile, std::ios::out | std::ios::trunc);
    NS_ABORT_MSG_IF(!summary, "Cannot open secondary airtime summary " << m_summaryFile);
    summary << std::setprecision(12);
    summary << "{\n"
            << "  \"tagged_ppdu_count\": " << m_taggedPpduCount << ",\n"
            << "  \"mixed_ppdu_count\": " << m_mixedPpduCount << ",\n"
            << "  \"tagged_secondary_tx_airtime_us\": " << m_measuredAirtimeTotalUs << ",\n"
            << "  \"measurement_start_ns\": " << *m_measurementStartNs << ",\n"
            << "  \"measurement_stop_ns\": " << *m_measurementStopNs << ",\n"
            << "  \"measurement_duration_us\": " << measurementDurationUs << ",\n"
            << "  \"tagged_secondary_tx_airtime_fraction\": " << fraction << ",\n"
            << "  \"maximum_budget_debt_us\": " << m_maximumBudgetDebtUs << ",\n"
            << "  \"estimated_action_airtime_us\": " << m_estimatedActionAirtimeUs << ",\n"
            << "  \"actual_to_estimated_airtime_ratio\": " << ratio << ",\n"
            << "  \"forced_reservation_settlements\": " << m_forcedSettlements << ",\n"
            << "  \"budget_fraction\": ";
    if (m_budgetFraction)
    {
        summary << *m_budgetFraction;
    }
    else
    {
        summary << "null";
    }
    summary << ",\n  \"initial_bucket_capacity_us\": ";
    if (m_initialCapacityUs)
    {
        summary << *m_initialCapacityUs;
    }
    else
    {
        summary << "null";
    }
    summary << ",\n  \"finite_run_budget_us\": ";
    if (finiteRunBudgetUs)
    {
        summary << *finiteRunBudgetUs;
    }
    else
    {
        summary << "null";
    }
    summary << ",\n  \"budget_excess_us\": ";
    if (budgetExcessUs)
    {
        summary << *budgetExcessUs;
    }
    else
    {
        summary << "null";
    }
    summary << "\n"
            << "}\n";
}

void
SecondaryAirtimeMeter::DoDispose()
{
    for (auto& [frameId, reservation] : m_reservations)
    {
        (void)frameId;
        reservation.settlementEvent.Cancel();
    }
    m_reservations.clear();
    m_measuredCallback = MeasuredAirtimeCallback();
    m_settlementCallback = SettlementCallback();
    m_phy = nullptr;
    m_device = nullptr;
    if (m_events.is_open())
    {
        m_events.close();
    }
    if (m_settlements.is_open())
    {
        m_settlements.close();
    }
    Object::DoDispose();
}

} // namespace ns3
