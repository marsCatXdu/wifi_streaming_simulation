/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef SECONDARY_AIRTIME_METER_H
#define SECONDARY_AIRTIME_METER_H

#include "ns3/callback.h"
#include "ns3/event-id.h"
#include "ns3/net-device.h"
#include "ns3/object.h"
#include "ns3/wifi-phy.h"
#include "ns3/wifi-tx-vector.h"

#include <cstdint>
#include <fstream>
#include <map>
#include <optional>
#include <set>
#include <string>
#include <vector>

namespace ns3
{

class WifiMpdu;
class WifiNetDevice;
enum WifiMacDropReason : uint8_t;

/**
 * State of one launched secondary copy tracked by the airtime meter.
 */
struct SecondaryAirtimeReservation
{
    uint64_t frameId{0};              ///< Application frame identifier.
    uint32_t packetCount{0};          ///< Secondary packet count.
    double reservedAirtimeUs{0};      ///< Remaining reserved estimate.
    double estimatedAirtimeUs{0};     ///< Original pre-launch estimate.
    double measuredAirtimeUs{0};      ///< Measured tagged PHY TX airtime.
    double nominalAirtimeUs{0};       ///< Estimate without retry inflation.
    uint64_t deadlineTimeNs{0};       ///< Absolute frame deadline.
    uint32_t packetsTerminal{0};      ///< Packets ACKed or terminally dropped.
    bool settled{false};              ///< Whether reservation was released.
    bool fallbackSettled{false};      ///< Whether settlement used the fallback timer.
    EventId settlementEvent;          ///< Fallback settlement timer.
};

/**
 * Passive secondary-path PHY TX airtime meter for tagged redundant copies.
 */
class SecondaryAirtimeMeter : public Object
{
  public:
    /**
     * Return runtime type information.
     *
     * @return The object TypeId.
     */
    static TypeId GetTypeId();

    SecondaryAirtimeMeter();
    ~SecondaryAirtimeMeter() override;

    /**
     * Bind the target-STA secondary path device that may transmit copies.
     *
     * @param pathId Application path identifier (must be 0 for V1).
     * @param device WifiNetDevice owning the secondary PHY.
     */
    void BindPath(uint8_t pathId, Ptr<NetDevice> device);

    /**
     * Configure CSV and JSON outputs.
     *
     * @param runId Stable run identifier.
     * @param eventsFile Per-PPDU event CSV path.
     * @param summaryFile End-of-run summary JSON path.
     */
    void SetOutputFiles(const std::string& runId,
                        const std::string& eventsFile,
                        const std::string& summaryFile);

    /**
     * Register a launched secondary copy for reservation settlement.
     *
     * @param reservation Initial reservation state.
     */
    void RegisterLaunchedCopy(SecondaryAirtimeReservation reservation);

    /**
     * Release unused reservation immediately (for launch rejection).
     *
     * @param frameId Application frame identifier.
     * @return Remaining reserved airtime that was released.
     */
    double ReleaseReservation(uint64_t frameId);

    /**
     * Return current outstanding reservation total.
     *
     * @return Reserved airtime in microseconds.
     */
    double GetReservedAirtimeUs() const;

    /**
     * Return cumulative measured tagged secondary TX airtime.
     *
     * @return Measured airtime in microseconds.
     */
    double GetMeasuredAirtimeTotalUs() const;

    /**
     * Return mixed tagged/untagged PPDU count.
     *
     * @return Mixed PPDU count.
     */
    uint64_t GetMixedPpduCount() const;

    /**
     * Return forced fallback settlement count.
     *
     * @return Fallback settlement count.
     */
    uint64_t GetForcedReservationSettlements() const;

    /**
     * Return maximum observed budget debt magnitude reported by the controller.
     *
     * @return Maximum debt in microseconds.
     */
    double GetMaximumBudgetDebtUs() const;

    /**
     * Record an observed negative available-airtime depth.
     *
     * @param debtUs Positive debt magnitude.
     */
    void ObserveBudgetDebt(double debtUs);

    /**
     * Return estimated action airtime sum across launched copies.
     *
     * @return Estimated airtime in microseconds.
     */
    double GetEstimatedActionAirtimeUs() const;

    /**
     * Callback invoked when measured PPDU airtime is allocated to a frame.
     */
    using MeasuredAirtimeCallback =
        Callback<void, uint64_t /*frameId*/, double /*allocatedUs*/, double /*ppduDurationUs*/>;

    /**
     * Callback invoked when a frame reservation is fully settled.
     */
    using SettlementCallback = Callback<void,
                                        uint64_t /*frameId*/,
                                        double /*releasedUs*/,
                                        double /*measuredUs*/,
                                        double /*nominalUs*/,
                                        bool /*fallback*/>;

    /**
     * Set the measured-airtime notification callback.
     *
     * @param callback Optional measured-airtime sink.
     */
    void SetMeasuredAirtimeCallback(MeasuredAirtimeCallback callback);

    /**
     * Set the reservation-settlement notification callback.
     *
     * @param callback Optional settlement sink.
     */
    void SetSettlementCallback(SettlementCallback callback);

    /**
     * Set the MAC queue max delay used for fallback settlement.
     *
     * @param delayMs Queue lifetime in milliseconds.
     */
    void SetQueueMaxDelayMs(uint32_t delayMs);

    /**
     * Write the end-of-run summary using the configured measurement window.
     *
     * @param measurementDurationUs Measurement interval duration.
     */
    void WriteSummary(uint64_t measurementDurationUs);

    /**
     * Apply a synthetic tagged PPDU allocation for deterministic unit tests.
     *
     * @param frameBytes Tagged MPDU bytes keyed by frame ID.
     * @param ppduDurationUs Full PPDU duration in microseconds.
     * @param otherDataBytes Untagged data bytes in the same PPDU.
     */
    void ApplyTestPpdu(const std::map<uint64_t, uint64_t>& frameBytes,
                       double ppduDurationUs,
                       uint64_t otherDataBytes);

  protected:
    void DoDispose() override;

  private:
    class TraceAdapter;

    void NotifyPhyTxPsduBegin(WifiConstPsduMap psduMap, WifiTxVector txVector, double /*txPower*/);
    void NotifyAckedMpdu(Ptr<const WifiMpdu> mpdu);
    void NotifyDroppedMpdu(WifiMacDropReason reason, Ptr<const WifiMpdu> mpdu);
    void MarkPacketTerminal(uint64_t frameId, uint32_t packetIndex);
    void SettleFrame(uint64_t frameId, bool fallback);
    void WriteEventHeader();
    void WriteEvent(uint64_t timeNs,
                    uint8_t pathId,
                    double ppduDurationUs,
                    uint64_t taggedMpduBytes,
                    const std::vector<uint64_t>& frameIds,
                    bool mixedPpdu);

    uint8_t m_pathId{0}; ///< Bound secondary path.
    Ptr<WifiNetDevice> m_device; ///< Bound STA WifiNetDevice.
    Ptr<WifiPhy> m_phy; ///< Bound PHY.
    std::string m_runId{"run"}; ///< Stable run identifier.
    std::ofstream m_events; ///< Per-PPDU CSV.
    std::string m_summaryFile; ///< Summary JSON path.
    MeasuredAirtimeCallback m_measuredCallback; ///< Optional measured sink.
    SettlementCallback m_settlementCallback; ///< Optional settlement sink.
    std::map<uint64_t, SecondaryAirtimeReservation> m_reservations; ///< Active frames.
    double m_reservedAirtimeUs{0}; ///< Outstanding reservations.
    double m_measuredAirtimeTotalUs{0}; ///< Cumulative measured tagged airtime.
    double m_estimatedActionAirtimeUs{0}; ///< Sum of original estimates.
    double m_maximumBudgetDebtUs{0}; ///< Observed controller debt depth.
    uint64_t m_taggedPpduCount{0}; ///< Tagged PPDU count.
    uint64_t m_mixedPpduCount{0}; ///< Mixed PPDU count.
    uint64_t m_forcedSettlements{0}; ///< Fallback settlements.
    uint32_t m_queueMaxDelayMs{500}; ///< MAC queue lifetime used for fallback.
};

} // namespace ns3

#endif // SECONDARY_AIRTIME_METER_H
