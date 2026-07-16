/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef RANDOM_RATE_ON_OFF_APPLICATION_H
#define RANDOM_RATE_ON_OFF_APPLICATION_H

#include "ns3/address.h"
#include "ns3/application.h"
#include "ns3/data-rate.h"
#include "ns3/event-id.h"
#include "ns3/nstime.h"
#include "ns3/ptr.h"

#include <cstdint>
#include <vector>

namespace ns3
{

class ExponentialRandomVariable;
class Socket;
class UniformRandomVariable;

/**
 * @ingroup wifi-streaming
 *
 * @brief A UDP ON/OFF source that selects a new random rate for every ON period.
 *
 * The application begins in the OFF state.  ON and OFF durations are
 * independent exponential random variables.  Upon every transition to ON, a
 * continuous uniform rate is sampled from the configured interval and held
 * constant until the end of that ON period.
 */
class RandomRateOnOffApplication : public Application
{
  public:
    /**
     * Description of one ON period.
     *
     * A period that is active when the application stops is closed at the
     * application stop time.
     */
    struct PeriodRecord
    {
        uint64_t index; //!< Zero-based ON-period index.
        Time start;     //!< Time at which the ON period began.
        Time end;       //!< Time at which the ON period ended.
        double rateBps; //!< Rate selected for this period, in bits per second.
    };

    /**
     * @brief Get the object TypeId.
     * @return The object TypeId.
     */
    static TypeId GetTypeId();

    RandomRateOnOffApplication();
    ~RandomRateOnOffApplication() override;

    /**
     * @brief Set the destination address.
     * @param remote UDP destination address.
     */
    void SetRemote(const Address& remote);

    /**
     * @brief Set the local bind address.
     * @param local Local address, or an invalid Address for a wildcard bind.
     */
    void SetLocal(const Address& local);

    /**
     * @brief Set the packet payload size.
     * @param bytes Number of bytes per UDP packet.
     */
    void SetPacketSize(uint32_t bytes);

    /**
     * @brief Set the interval from which ON-period rates are sampled.
     * @param minimum Minimum data rate.
     * @param maximum Maximum data rate.
     */
    void SetRateRange(DataRate minimum, DataRate maximum);

    /**
     * @brief Set the exponential ON and OFF duration means.
     * @param onMean Mean ON duration.
     * @param offMean Mean OFF duration.
     */
    void SetMeans(Time onMean, Time offMean);

    /**
     * @brief Assign independent streams to the rate, ON-time, and OFF-time RNGs.
     * @param stream First stream number.
     * @return Always three, the number of streams assigned.
     */
    int64_t AssignStreams(int64_t stream) override;

    /**
     * @brief Get the number of successfully transmitted bytes.
     * @return Total transmitted bytes.
     */
    uint64_t GetTotalTxBytes() const;

    /**
     * @brief Get all completed or stopped ON-period records.
     * @return Constant reference to the period records.
     */
    const std::vector<PeriodRecord>& GetPeriodRecords() const;

  private:
    void StartApplication() override;
    void StopApplication() override;
    void DoDispose() override;

    void StartOnPeriod();
    void EndOnPeriod();
    void SendPacket();
    void ScheduleNextPacket();
    void CancelEvents();
    void CloseActivePeriod();

    Address m_remote; //!< UDP destination.
    Address m_local;  //!< Local bind address.
    uint32_t m_packetSize{1200}; //!< UDP payload size in bytes.
    DataRate m_minRate{"1Mbps"}; //!< Minimum ON-period rate.
    DataRate m_maxRate{"10Mbps"}; //!< Maximum ON-period rate.
    Time m_onMean{Seconds(1)}; //!< Mean ON duration.
    Time m_offMean{Seconds(1)}; //!< Mean OFF duration.

    Ptr<UniformRandomVariable> m_rateVariable; //!< Rate random variable.
    Ptr<ExponentialRandomVariable> m_onVariable; //!< ON-duration random variable.
    Ptr<ExponentialRandomVariable> m_offVariable; //!< OFF-duration random variable.
    Ptr<Socket> m_socket; //!< Connected UDP socket.

    EventId m_stateEvent; //!< Next ON/OFF transition.
    EventId m_sendEvent;  //!< Next packet transmission.
    double m_currentRateBps{0}; //!< Rate held during the current ON period.
    bool m_running{false}; //!< Whether the application is running.
    bool m_isOn{false}; //!< Whether the application is in an ON period.
    uint64_t m_totalTxBytes{0}; //!< Successfully transmitted bytes.
    std::vector<PeriodRecord> m_periodRecords; //!< ON-period history.
};

} // namespace ns3

#endif // RANDOM_RATE_ON_OFF_APPLICATION_H
