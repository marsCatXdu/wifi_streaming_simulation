/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "prediction-telemetry-collector.h"

#include "ns3/abort.h"
#include "ns3/channel-access-manager.h"
#include "ns3/qos-utils.h"
#include "ns3/simulator.h"
#include "ns3/txop.h"
#include "ns3/wifi-mac-queue.h"
#include "ns3/wifi-mac.h"
#include "ns3/wifi-mode.h"
#include "ns3/wifi-mpdu.h"
#include "ns3/wifi-net-device.h"
#include "ns3/wifi-phy-state-helper.h"
#include "ns3/wifi-phy.h"
#include "ns3/wifi-psdu.h"
#include "ns3/wifi-tx-vector.h"

#include <algorithm>
#include <cmath>
#include <iomanip>
#include <limits>
#include <numeric>
#include <sstream>
#include <tuple>
#include <utility>

namespace ns3
{

NS_OBJECT_ENSURE_REGISTERED(PredictionTelemetryCollector);

namespace
{

uint64_t
NowNs()
{
    const int64_t nowNs = Simulator::Now().GetNanoSeconds();
    NS_ABORT_MSG_IF(nowNs < 0, "Prediction telemetry does not support negative simulation time");
    return static_cast<uint64_t>(nowNs);
}

void
WriteCsvString(std::ostream& output, const std::string& value)
{
    if (value.find_first_of(",\"\r\n") == std::string::npos)
    {
        output << value;
        return;
    }
    output << '"';
    for (const char character : value)
    {
        if (character == '"')
        {
            output << "\"\"";
        }
        else
        {
            output << character;
        }
    }
    output << '"';
}

class CsvRow
{
  public:
    explicit CsvRow(std::ostream& output)
        : m_output(output)
    {
    }

    template <typename T>
    void Add(const T& value)
    {
        Separate();
        Write(value);
    }

    template <typename T>
    void Add(const std::optional<T>& value)
    {
        Separate();
        if (value)
        {
            Write(*value);
        }
    }

    void End()
    {
        m_output << '\n';
    }

  private:
    void Separate()
    {
        if (!m_first)
        {
            m_output << ',';
        }
        m_first = false;
    }

    template <typename T>
    void Write(const T& value)
    {
        m_output << value;
    }

    void Write(const uint8_t& value)
    {
        m_output << +value;
    }

    void Write(const std::string& value)
    {
        WriteCsvString(m_output, value);
    }

    std::ostream& m_output;
    bool m_first{true};
};

} // namespace

/**
 * Adapts ns-3 trace callback signatures to path-aware collector methods.
 */
class PredictionTelemetryTraceAdapter
{
  public:
    static void QueueEnqueue(PredictionTelemetryCollector* collector,
                             uint8_t pathId,
                             Ptr<const WifiMpdu> mpdu)
    {
        collector->NotifyQueueEnqueue(pathId, mpdu);
    }

    static void QueueDequeue(PredictionTelemetryCollector* collector,
                             uint8_t pathId,
                             Ptr<const WifiMpdu> mpdu)
    {
        collector->NotifyQueueDequeue(pathId, mpdu);
    }

    static void AckedMpdu(PredictionTelemetryCollector* collector,
                          uint8_t pathId,
                          Ptr<const WifiMpdu> mpdu)
    {
        collector->NotifyAckedMpdu(pathId, mpdu);
    }

    static void NackedMpdu(PredictionTelemetryCollector* collector,
                           uint8_t pathId,
                           Ptr<const WifiMpdu> mpdu)
    {
        collector->NotifyNackedMpdu(pathId, mpdu);
    }

    static void MpduResponseTimeout(PredictionTelemetryCollector* collector,
                                    uint8_t pathId,
                                    uint8_t,
                                    Ptr<const WifiMpdu> mpdu,
                                    const WifiTxVector&)
    {
        collector->NotifyResponseTimeout(pathId, mpdu);
    }

    static void PsduResponseTimeout(PredictionTelemetryCollector* collector,
                                    uint8_t pathId,
                                    uint8_t,
                                    Ptr<const WifiPsdu> psdu,
                                    const WifiTxVector&)
    {
        for (const auto& mpdu : *psdu)
        {
            collector->NotifyResponseTimeout(pathId, mpdu);
        }
    }

    static void DroppedMpdu(PredictionTelemetryCollector* collector,
                            uint8_t pathId,
                            WifiMacDropReason reason,
                            Ptr<const WifiMpdu> mpdu)
    {
        collector->NotifyDroppedMpdu(pathId, reason, mpdu);
    }

    static void PhyTxBegin(PredictionTelemetryCollector* collector,
                           uint8_t pathId,
                           Ptr<const Packet> packet,
                           double)
    {
        collector->NotifyPhyTxBegin(pathId, packet);
    }

    static void PhyTxPsduBegin(PredictionTelemetryCollector* collector,
                               uint8_t pathId,
                               WifiConstPsduMap psduMap,
                               WifiTxVector txVector,
                               double)
    {
        bool taggedTarget = false;
        for (const auto& [staId, psdu] : psduMap)
        {
            (void)staId;
            for (const auto& mpdu : *psdu)
            {
                StreamingFrameTag tag;
                if (PredictionTelemetryCollector::GetTag(mpdu, tag) && tag.pathId == pathId)
                {
                    taggedTarget = true;
                    break;
                }
            }
            if (taggedTarget)
            {
                break;
            }
        }
        collector->NotifyPpduTx(pathId, txVector, taggedTarget);
    }

    static void PhyState(PredictionTelemetryCollector* collector,
                         uint8_t pathId,
                         Time start,
                         Time duration,
                         WifiPhyState state)
    {
        collector->NotifyPhyState(pathId, start, duration, state);
    }
};

bool
PredictionFrameKey::operator<(const PredictionFrameKey& other) const
{
    return std::tie(frameId, pathId, copyId) <
           std::tie(other.frameId, other.pathId, other.copyId);
}

TypeId
PredictionTelemetryCollector::GetTypeId()
{
    static TypeId tid = TypeId("ns3::PredictionTelemetryCollector")
                            .SetParent<Object>()
                            .SetGroupName("WifiStreaming")
                            .AddConstructor<PredictionTelemetryCollector>();
    return tid;
}

PredictionTelemetryCollector::PredictionTelemetryCollector() = default;

PredictionTelemetryCollector::~PredictionTelemetryCollector() = default;

void
PredictionTelemetryCollector::SetRunId(const std::string& runId)
{
    NS_ABORT_MSG_IF(runId.empty(), "Prediction telemetry run ID cannot be empty");
    m_runId = runId;
}

void
PredictionTelemetryCollector::SetSampleOffsetsUs(const std::vector<uint64_t>& offsetsUs)
{
    NS_ABORT_MSG_IF(!m_frames.empty(), "Cannot change sample offsets after frame registration");
    NS_ABORT_MSG_IF(offsetsUs.empty(), "Prediction sample offsets cannot be empty");
    NS_ABORT_MSG_IF(offsetsUs.front() != 0, "Prediction sample offsets must begin with T0");
    for (std::size_t index = 1; index < offsetsUs.size(); ++index)
    {
        NS_ABORT_MSG_IF(offsetsUs[index] <= offsetsUs[index - 1],
                        "Prediction sample offsets must be strictly increasing");
    }
    m_sampleOffsetsUs = offsetsUs;
}

const std::vector<uint64_t>&
PredictionTelemetryCollector::GetSampleOffsetsUs() const
{
    return m_sampleOffsetsUs;
}

void
PredictionTelemetryCollector::SetHistoryWindowsUs(const std::vector<uint64_t>& windowsUs)
{
    NS_ABORT_MSG_IF(!m_paths.empty() || !m_frames.empty(),
                    "Cannot change history windows after path binding or frame registration");
    NS_ABORT_MSG_IF(windowsUs.empty(), "Prediction history windows cannot be empty");
    NS_ABORT_MSG_IF(windowsUs.front() == 0, "Prediction history windows must be positive");
    NS_ABORT_MSG_IF(windowsUs.back() >
                        static_cast<uint64_t>(std::numeric_limits<int64_t>::max()) / 2000,
                    "Prediction history window exceeds supported nanosecond range");
    for (std::size_t index = 1; index < windowsUs.size(); ++index)
    {
        NS_ABORT_MSG_IF(windowsUs[index] <= windowsUs[index - 1],
                        "Prediction history windows must be strictly increasing");
    }
    m_historyWindowsUs = windowsUs;
}

const std::vector<uint64_t>&
PredictionTelemetryCollector::GetHistoryWindowsUs() const
{
    return m_historyWindowsUs;
}

void
PredictionTelemetryCollector::SetOracleFeaturesEnabled(bool enabled)
{
    NS_ABORT_MSG_IF(!m_frames.empty(),
                    "Cannot change oracle collection after frame registration");
    m_oracleFeaturesEnabled = enabled;
}

void
PredictionTelemetryCollector::BindWifiPath(uint8_t pathId,
                                           Ptr<NetDevice> device,
                                           uint8_t phyId,
                                           AcIndex accessCategory)
{
    NS_ABORT_MSG_IF(!device, "Prediction telemetry path requires a net device");
    NS_ABORT_MSG_IF(m_paths.contains(pathId), "Prediction telemetry path was bound twice");
    auto wifiDevice = DynamicCast<WifiNetDevice>(device);
    NS_ABORT_MSG_IF(!wifiDevice, "Prediction telemetry requires a WifiNetDevice");
    NS_ABORT_MSG_IF(phyId >= wifiDevice->GetNPhys(),
                    "Prediction telemetry PHY ID is not present on the Wi-Fi device");
    auto queue = wifiDevice->GetMac()->GetTxopQueue(accessCategory);
    NS_ABORT_MSG_IF(!queue, "Prediction telemetry target access category has no MAC queue");
    NS_ABORT_MSG_IF(queue->GetNPackets() != 0,
                    "Prediction telemetry path must be bound before target queue traffic");

    PathState state;
    state.device = wifiDevice;
    state.phy = wifiDevice->GetPhy(phyId);
    state.phyState = state.phy->GetState();
    state.queue = queue;
    state.txop = wifiDevice->GetMac()->GetTxopFor(accessCategory);
    NS_ABORT_MSG_IF(!state.txop, "Prediction telemetry target access category has no TXOP");
    state.channelAccessManager = wifiDevice->GetMac()->GetChannelAccessManager(phyId);
    NS_ABORT_MSG_IF(!state.channelAccessManager,
                    "Prediction telemetry target PHY has no channel access manager");
    state.phyId = phyId;
    state.telemetryStartNs = Simulator::Now().GetNanoSeconds();
    state.latestFeatureEventTimeNs =
        static_cast<uint64_t>(std::max<int64_t>(state.telemetryStartNs, 0));
    state.centerFrequencyMhz = state.phy->GetFrequency();
    if (state.centerFrequencyMhz < 3000)
    {
        state.frequencyBand = "2.4GHz";
    }
    else if (state.centerFrequencyMhz < 5925)
    {
        state.frequencyBand = "5GHz";
    }
    else
    {
        state.frequencyBand = "6GHz";
    }
    state.phyIntervals.push_back(
        {state.telemetryStartNs,
         std::numeric_limits<int64_t>::max(),
         state.phyState->GetState(),
         state.latestFeatureEventTimeNs,
         state.phyIntervalSerial++});
    m_paths.emplace(pathId, std::move(state));

    auto& path = m_paths.at(pathId);
    NS_ABORT_MSG_IF(
        !path.queue->TraceConnectWithoutContext(
            "Enqueue",
            MakeBoundCallback(&PredictionTelemetryTraceAdapter::QueueEnqueue, this, pathId)),
        "Cannot connect prediction telemetry MAC enqueue trace");
    NS_ABORT_MSG_IF(
        !path.queue->TraceConnectWithoutContext(
            "Dequeue",
            MakeBoundCallback(&PredictionTelemetryTraceAdapter::QueueDequeue, this, pathId)),
        "Cannot connect prediction telemetry MAC dequeue trace");
    NS_ABORT_MSG_IF(
        !path.device->GetMac()->TraceConnectWithoutContext(
            "AckedMpdu",
            MakeBoundCallback(&PredictionTelemetryTraceAdapter::AckedMpdu, this, pathId)),
        "Cannot connect prediction telemetry AckedMpdu trace");
    NS_ABORT_MSG_IF(
        !path.device->GetMac()->TraceConnectWithoutContext(
            "NAckedMpdu",
            MakeBoundCallback(&PredictionTelemetryTraceAdapter::NackedMpdu, this, pathId)),
        "Cannot connect prediction telemetry NAckedMpdu trace");
    NS_ABORT_MSG_IF(
        !path.device->GetMac()->TraceConnectWithoutContext(
            "MpduResponseTimeout",
            MakeBoundCallback(&PredictionTelemetryTraceAdapter::MpduResponseTimeout,
                              this,
                              pathId)),
        "Cannot connect prediction telemetry MPDU timeout trace");
    NS_ABORT_MSG_IF(
        !path.device->GetMac()->TraceConnectWithoutContext(
            "PsduResponseTimeout",
            MakeBoundCallback(&PredictionTelemetryTraceAdapter::PsduResponseTimeout,
                              this,
                              pathId)),
        "Cannot connect prediction telemetry PSDU timeout trace");
    NS_ABORT_MSG_IF(
        !path.device->GetMac()->TraceConnectWithoutContext(
            "DroppedMpdu",
            MakeBoundCallback(&PredictionTelemetryTraceAdapter::DroppedMpdu, this, pathId)),
        "Cannot connect prediction telemetry DroppedMpdu trace");
    NS_ABORT_MSG_IF(
        !path.phy->TraceConnectWithoutContext(
            "PhyTxBegin",
            MakeBoundCallback(&PredictionTelemetryTraceAdapter::PhyTxBegin, this, pathId)),
        "Cannot connect prediction telemetry PhyTxBegin trace");
    NS_ABORT_MSG_IF(
        !path.phy->TraceConnectWithoutContext(
            "PhyTxPsduBegin",
            MakeBoundCallback(&PredictionTelemetryTraceAdapter::PhyTxPsduBegin, this, pathId)),
        "Cannot connect prediction telemetry PhyTxPsduBegin trace");
    NS_ABORT_MSG_IF(
        !path.phyState->TraceConnectWithoutContext(
            "State",
            MakeBoundCallback(&PredictionTelemetryTraceAdapter::PhyState, this, pathId)),
        "Cannot connect prediction telemetry PHY state trace");
}

void
PredictionTelemetryCollector::SetOutputFiles(const std::string& samplesFile,
                                             const std::string& eventsFile)
{
    NS_ABORT_MSG_IF(samplesFile.empty(), "Prediction sample output path cannot be empty");
    NS_ABORT_MSG_IF(!m_samplesFile.empty(), "Prediction output files were configured twice");
    m_samplesFile = samplesFile;
    if (!eventsFile.empty())
    {
        m_eventsOutput.open(eventsFile, std::ios::out | std::ios::trunc);
        NS_ABORT_MSG_IF(!m_eventsOutput, "Cannot open prediction event output " << eventsFile);
        m_eventsOutput << std::setprecision(12);
        WriteEventHeader();
    }
}

void
PredictionTelemetryCollector::WriteEventHeader()
{
    m_eventsOutput
        << "event_schema_version,run_id,event_time_ns,event_type,path_id,copy_id,frame_id,"
           "packet_index,attempt_number,mac_service_bytes,mac_queue_packets,"
           "mac_queue_service_bytes,current_mcs,current_nss,current_channel_width_mhz,"
           "current_guard_interval_ns,current_phy_state\n";
}

std::string
PredictionTelemetryCollector::WindowLabel(uint64_t windowUs)
{
    if (windowUs % 1000 == 0)
    {
        return std::to_string(windowUs / 1000) + "ms";
    }
    return std::to_string(windowUs) + "us";
}

void
PredictionTelemetryCollector::WriteSampleHeader(std::ostream& output) const
{
    output
        << "telemetry_schema_version,run_id,frame_id,path_id,copy_id,sample_stage,"
           "sample_offset_us,sample_time_ns,latest_feature_event_time_ns,generation_time_ns,"
           "deadline_time_ns,frame_age_us,deadline_slack_us,sender_mac_complete,actionable,"
           "frame_size_bytes,frame_packet_count,frame_type,packets_submitted,"
           "application_socket_packet_bytes_submitted,packets_remaining_to_submit,"
           "mpdu_tx_attempts_total,mpdu_tx_successes_total,mpdu_tx_attempt_failures_total,"
           "mpdu_retries_total,mpdu_terminal_drops_total,mpdu_retry_limit_drops_total,"
           "mpdu_lifetime_drops_total,mpdu_queue_drops_total,ppdu_tx_count_total,"
           "last_tx_attempt_time_ns,last_tx_success_time_ns,current_mcs,current_nss,"
           "current_channel_width_mhz,current_guard_interval_ns,frequency_band,"
           "center_frequency_mhz,current_ack_signal_dbm";
    for (const auto windowUs : m_historyWindowsUs)
    {
        const auto label = WindowLabel(windowUs);
        output << ",mpdu_attempts_" << label << ",mpdu_successes_" << label
               << ",mpdu_attempt_failures_" << label << ",mpdu_retries_" << label
               << ",mpdu_retry_ratio_" << label << ",acknowledged_mac_service_bytes_" << label
               << ",mpdu_queue_to_ack_mean_" << label << "_us"
               << ",mpdu_queue_to_ack_p95_" << label << "_us"
               << ",mpdu_first_attempt_to_ack_mean_" << label << "_us"
               << ",mpdu_first_attempt_to_ack_p95_" << label << "_us"
               << ",phy_tx_time_" << label << "_us"
               << ",phy_rx_time_" << label << "_us"
               << ",phy_busy_time_" << label << "_us"
               << ",phy_idle_time_" << label << "_us"
               << ",phy_other_time_" << label << "_us"
               << ",phy_tx_fraction_" << label << ",phy_rx_fraction_" << label
               << ",phy_busy_fraction_" << label << ",phy_idle_fraction_" << label
               << ",phy_other_fraction_" << label << ",history_coverage_" << label << "_us";
    }
    output
        << ",frame_packets_mac_enqueued,frame_packets_mac_dequeued,"
           "frame_packets_tx_succeeded,frame_mpdu_attempt_failures,"
           "frame_packets_terminally_dropped,frame_packets_currently_queued,"
           "frame_mac_service_bytes_currently_queued,mac_queue_packets,"
           "mac_queue_service_bytes,mac_queue_oldest_enqueue_time_ns,packets_ahead_of_frame,"
           "mac_service_bytes_ahead_of_frame,frame_packets_pending_primary,"
           "frame_mac_service_bytes_not_acknowledged,"
           "frame_mac_service_bytes_pending_primary,current_cw,remaining_backoff_slots,"
           "nav_remaining_us,current_phy_state,channel_access_status,medium_busy_now,"
           "expected_access_reason_within_slack,feature_support_mask\n";
}

void
PredictionTelemetryCollector::WriteSample(std::ostream& output,
                                          const PredictionSample& sample) const
{
    CsvRow row(output);
    row.Add(sample.telemetrySchemaVersion);
    row.Add(sample.runId);
    row.Add(sample.key.frameId);
    row.Add(sample.key.pathId);
    row.Add(sample.key.copyId);
    row.Add(sample.sampleStage);
    row.Add(sample.sampleOffsetUs);
    row.Add(sample.sampleTimeNs);
    row.Add(sample.latestFeatureEventTimeNs);
    row.Add(sample.generationTimeNs);
    row.Add(sample.deadlineTimeNs);
    row.Add(sample.frameAgeUs);
    row.Add(sample.deadlineSlackUs);
    row.Add(sample.senderMacComplete);
    row.Add(sample.actionable);
    row.Add(sample.frameSizeBytes);
    row.Add(sample.framePacketCount);
    row.Add(FrameTypeToString(sample.frameType));
    row.Add(sample.packetsSubmitted);
    row.Add(sample.applicationSocketPacketBytesSubmitted);
    row.Add(sample.packetsRemainingToSubmit);
    row.Add(sample.mpduTxAttemptsTotal);
    row.Add(sample.mpduTxSuccessesTotal);
    row.Add(sample.mpduTxAttemptFailuresTotal);
    row.Add(sample.mpduRetriesTotal);
    row.Add(sample.mpduTerminalDropsTotal);
    row.Add(sample.mpduRetryLimitDropsTotal);
    row.Add(sample.mpduLifetimeDropsTotal);
    row.Add(sample.mpduQueueDropsTotal);
    row.Add(sample.ppduTxCountTotal);
    row.Add(sample.lastTxAttemptTimeNs);
    row.Add(sample.lastTxSuccessTimeNs);
    row.Add(sample.currentMcs);
    row.Add(sample.currentNss);
    row.Add(sample.currentChannelWidthMhz);
    row.Add(sample.currentGuardIntervalNs);
    row.Add(sample.frequencyBand);
    row.Add(sample.centerFrequencyMhz);
    row.Add(sample.currentAckSignalDbm);
    NS_ABORT_MSG_IF(!sample.rolling.empty() &&
                        sample.rolling.size() != m_historyWindowsUs.size(),
                    "Prediction sample rolling-window count does not match configuration");
    for (std::size_t index = 0; index < m_historyWindowsUs.size(); ++index)
    {
        if (sample.rolling.empty())
        {
            row.Add(std::optional<uint64_t>{});
            row.Add(std::optional<uint64_t>{});
            row.Add(std::optional<uint64_t>{});
            row.Add(std::optional<uint64_t>{});
            row.Add(std::optional<double>{});
            row.Add(std::optional<uint64_t>{});
            row.Add(std::optional<double>{});
            row.Add(std::optional<double>{});
            row.Add(std::optional<double>{});
            row.Add(std::optional<double>{});
            row.Add(std::optional<double>{});
            row.Add(std::optional<double>{});
            row.Add(std::optional<double>{});
            row.Add(std::optional<double>{});
            row.Add(std::optional<double>{});
            row.Add(std::optional<double>{});
            row.Add(std::optional<double>{});
            row.Add(std::optional<double>{});
            row.Add(std::optional<double>{});
            row.Add(std::optional<double>{});
            row.Add(std::optional<double>{});
            continue;
        }
        const auto& rolling = sample.rolling[index];
        NS_ABORT_MSG_IF(rolling.windowUs != m_historyWindowsUs[index],
                        "Prediction sample rolling windows are out of order");
        row.Add(rolling.mpduAttempts);
        row.Add(rolling.mpduSuccesses);
        row.Add(rolling.mpduAttemptFailures);
        row.Add(rolling.mpduRetries);
        row.Add(rolling.mpduRetryRatio);
        row.Add(rolling.acknowledgedMacServiceBytes);
        row.Add(rolling.mpduQueueToAckMeanUs);
        row.Add(rolling.mpduQueueToAckP95Us);
        row.Add(rolling.mpduFirstAttemptToAckMeanUs);
        row.Add(rolling.mpduFirstAttemptToAckP95Us);
        row.Add(rolling.phyTxTimeUs);
        row.Add(rolling.phyRxTimeUs);
        row.Add(rolling.phyBusyTimeUs);
        row.Add(rolling.phyIdleTimeUs);
        row.Add(rolling.phyOtherTimeUs);
        row.Add(rolling.phyTxFraction);
        row.Add(rolling.phyRxFraction);
        row.Add(rolling.phyBusyFraction);
        row.Add(rolling.phyIdleFraction);
        row.Add(rolling.phyOtherFraction);
        row.Add(rolling.historyCoverageUs);
    }
    row.Add(sample.framePacketsMacEnqueued);
    row.Add(sample.framePacketsMacDequeued);
    row.Add(sample.framePacketsTxSucceeded);
    row.Add(sample.frameMpduAttemptFailures);
    row.Add(sample.framePacketsTerminallyDropped);
    row.Add(sample.framePacketsCurrentlyQueued);
    row.Add(sample.frameMacServiceBytesCurrentlyQueued);
    row.Add(sample.macQueuePackets);
    row.Add(sample.macQueueServiceBytes);
    row.Add(sample.macQueueOldestEnqueueTimeNs);
    row.Add(sample.packetsAheadOfFrame);
    row.Add(sample.macServiceBytesAheadOfFrame);
    row.Add(sample.framePacketsPendingPrimary);
    row.Add(sample.frameMacServiceBytesNotAcknowledged);
    row.Add(sample.frameMacServiceBytesPendingPrimary);
    row.Add(sample.currentCw);
    row.Add(sample.remainingBackoffSlots);
    row.Add(sample.navRemainingUs);
    row.Add(sample.currentPhyState);
    row.Add(sample.channelAccessStatus);
    row.Add(sample.mediumBusyNow);
    row.Add(sample.expectedAccessReasonWithinSlack);
    row.Add(sample.featureSupportMask);
    row.End();
}

void
PredictionTelemetryCollector::WriteOutputs()
{
    NS_ABORT_MSG_IF(m_samplesFile.empty(), "Prediction sample output path was not configured");
    NS_ABORT_MSG_IF(m_outputsWritten, "Prediction outputs were written more than once");
    std::sort(m_samples.begin(),
              m_samples.end(),
              [](const PredictionSample& left, const PredictionSample& right) {
                  return std::tie(left.sampleTimeNs,
                                  left.key.frameId,
                                  left.key.pathId,
                                  left.sampleOffsetUs,
                                  left.key.copyId) <
                         std::tie(right.sampleTimeNs,
                                  right.key.frameId,
                                  right.key.pathId,
                                  right.sampleOffsetUs,
                                  right.key.copyId);
              });
    std::ofstream output(m_samplesFile, std::ios::out | std::ios::trunc);
    NS_ABORT_MSG_IF(!output, "Cannot open prediction sample output " << m_samplesFile);
    output << std::setprecision(12);
    WriteSampleHeader(output);
    for (const auto& sample : m_samples)
    {
        WriteSample(output, sample);
    }
    output.close();
    if (m_eventsOutput.is_open())
    {
        m_eventsOutput.flush();
    }
    m_outputsWritten = true;
}

void
PredictionTelemetryCollector::WriteEvent(
    const std::string& eventType,
    uint8_t pathId,
    const std::optional<StreamingFrameTag>& tag,
    std::optional<uint32_t> attemptNumber,
    std::optional<uint32_t> macServiceBytes)
{
    if (!m_eventsOutput.is_open())
    {
        return;
    }
    auto pathIterator = m_paths.find(pathId);
    NS_ABORT_MSG_IF(pathIterator == m_paths.end(), "Prediction event references an unbound path");
    const auto& path = pathIterator->second;
    uint64_t queueServiceBytes = 0;
    for (const auto& entry : path.queueEntries)
    {
        queueServiceBytes += entry.macServiceBytes;
    }

    CsvRow row(m_eventsOutput);
    row.Add(PREDICTION_EVENT_SCHEMA_VERSION);
    row.Add(m_runId);
    row.Add(NowNs());
    row.Add(eventType);
    row.Add(pathId);
    if (tag)
    {
        row.Add(tag->copyId);
        row.Add(tag->frameId);
        row.Add(tag->packetIndex);
    }
    else
    {
        row.Add(std::optional<uint8_t>{});
        row.Add(std::optional<uint64_t>{});
        row.Add(std::optional<uint32_t>{});
    }
    row.Add(attemptNumber);
    row.Add(macServiceBytes);
    row.Add(path.queueEntries.size());
    row.Add(queueServiceBytes);
    row.Add(path.currentMcs);
    row.Add(path.currentNss);
    row.Add(path.currentChannelWidthMhz);
    row.Add(path.currentGuardIntervalNs);
    row.Add(PhyStateToString(path.phyState->GetState()));
    row.End();
}

PredictionFrameKey
PredictionTelemetryCollector::MakeKey(const PacketizationPlan& plan)
{
    return {plan.frame.frameId, plan.pathId, plan.copyId};
}

PredictionFrameKey
PredictionTelemetryCollector::MakeKey(const StreamingFrameTag& tag)
{
    return {tag.frameId, tag.pathId, tag.copyId};
}

void
PredictionTelemetryCollector::RegisterFrame(const PacketizationPlan& plan)
{
    const int64_t nowNs = Simulator::Now().GetNanoSeconds();
    NS_ABORT_MSG_IF(nowNs < 0 || plan.frame.generationTimeNs != static_cast<uint64_t>(nowNs),
                    "Prediction frame registration must occur at generation time");
    NS_ABORT_MSG_IF(plan.frame.packetCount == 0 ||
                        plan.frame.packetCount != plan.packets.size(),
                    "Packetization plan has inconsistent packet count");
    NS_ABORT_MSG_IF(plan.frame.deadlineUs == 0,
                    "Prediction telemetry requires a positive frame deadline");

    uint64_t payloadBytes = 0;
    for (std::size_t index = 0; index < plan.packets.size(); ++index)
    {
        NS_ABORT_MSG_IF(plan.packets[index].packetIndex != index,
                        "Packetization plan indexes must be contiguous");
        payloadBytes += plan.packets[index].applicationPayloadBytes;
    }
    NS_ABORT_MSG_IF(payloadBytes != plan.frame.frameSizeBytes,
                    "Packetization plan payload bytes do not equal frame size");
    for (const auto offsetUs : m_sampleOffsetsUs)
    {
        NS_ABORT_MSG_IF(offsetUs >= plan.frame.deadlineUs,
                        "Prediction sample offset must precede the frame deadline");
    }

    const auto key = MakeKey(plan);
    FrameState state;
    state.plan = plan;
    state.packets.resize(plan.frame.packetCount);
    for (std::size_t index = 0; index < plan.packets.size(); ++index)
    {
        state.packets[index].macServiceBytes = plan.packets[index].expectedMacServiceBytes;
    }
    state.latestFeatureEventTimeNs = plan.frame.generationTimeNs;
    const auto [iterator, inserted] = m_frames.emplace(key, std::move(state));
    NS_ABORT_MSG_IF(!inserted,
                    "Prediction frame copy was registered more than once: frame "
                        << key.frameId << " path " << +key.pathId << " copy " << +key.copyId);
    (void)iterator;

    // T0 is deliberately synchronous. No packet has been materialized,
    // submitted, or queued when this call executes in MultipathSender.
    CaptureSnapshot(key, 0);
    StreamingFrameTag frameTag;
    frameTag.frameId = plan.frame.frameId;
    frameTag.pathId = plan.pathId;
    frameTag.copyId = plan.copyId;
    frameTag.packetCount = plan.frame.packetCount;
    frameTag.generationTimeNs = plan.frame.generationTimeNs;
    frameTag.deadlineTimeNs =
        plan.frame.generationTimeNs + static_cast<uint64_t>(plan.frame.deadlineUs) * 1000;
    frameTag.frameSizeBytes = plan.frame.frameSizeBytes;
    frameTag.frameType = plan.frame.frameType;
    WriteEvent("FRAME_REGISTERED", plan.pathId, frameTag, std::nullopt, std::nullopt);
    for (std::size_t index = 1; index < m_sampleOffsetsUs.size(); ++index)
    {
        const auto offsetUs = m_sampleOffsetsUs[index];
        m_snapshotEvents.push_back(
            Simulator::Schedule(MicroSeconds(offsetUs),
                                &PredictionTelemetryCollector::CaptureSnapshot,
                                this,
                                key,
                                offsetUs));
    }
}

void
PredictionTelemetryCollector::RecordPacketSubmitted(
    const StreamingFrameTag& tag,
    uint32_t applicationSocketPacketBytes)
{
    NS_ABORT_MSG_IF(!tag.IsValid(), "Cannot record an invalid streaming frame tag");
    const auto key = MakeKey(tag);
    auto iterator = m_frames.find(key);
    NS_ABORT_MSG_IF(iterator == m_frames.end(),
                    "Submitted packet belongs to an unregistered prediction frame");
    auto& state = iterator->second;
    NS_ABORT_MSG_IF(tag.packetCount != state.plan.frame.packetCount ||
                        tag.generationTimeNs != state.plan.frame.generationTimeNs ||
                        tag.deadlineTimeNs != state.plan.frame.generationTimeNs +
                                                  static_cast<uint64_t>(
                                                      state.plan.frame.deadlineUs) *
                                                      1000 ||
                        tag.frameSizeBytes != state.plan.frame.frameSizeBytes ||
                        tag.frameType != state.plan.frame.frameType,
                    "Submitted packet tag disagrees with its immutable frame plan");
    NS_ABORT_MSG_IF(tag.packetIndex >= state.packets.size(),
                    "Submitted packet index exceeds the frame plan");
    auto& packet = state.packets[tag.packetIndex];
    NS_ABORT_MSG_IF(packet.submitted,
                    "Application packet was submitted more than once on one frame copy");

    packet.submitted = true;
    ++state.packetsSubmitted;
    state.submittedBytes += applicationSocketPacketBytes;
    state.latestFeatureEventTimeNs = NowNs();
    WriteEvent("PACKET_SUBMITTED", tag.pathId, tag, std::nullopt, std::nullopt);
}

bool
PredictionTelemetryCollector::GetTag(Ptr<const WifiMpdu> mpdu, StreamingFrameTag& tag)
{
    if (!mpdu)
    {
        return false;
    }
    return mpdu->GetOriginal()->GetPacket()->PeekPacketTag(tag);
}

const WifiMpdu*
PredictionTelemetryCollector::GetStableMpdu(Ptr<const WifiMpdu> mpdu)
{
    return mpdu ? PeekPointer(mpdu->GetOriginal()) : nullptr;
}

void
PredictionTelemetryCollector::NotifyQueueEnqueue(uint8_t pathId, Ptr<const WifiMpdu> mpdu)
{
    auto pathIterator = m_paths.find(pathId);
    NS_ABORT_MSG_IF(pathIterator == m_paths.end(), "MAC enqueue references an unbound path");
    StreamingFrameTag tag;
    if (!GetTag(mpdu, tag))
    {
        return;
    }
    NS_ABORT_MSG_IF(tag.pathId != pathId,
                    "Tagged MPDU appeared in the queue for a different application path");
    auto& path = pathIterator->second;
    const uint64_t nowNs = NowNs();
    path.latestFeatureEventTimeNs = nowNs;

    QueueEntry entry;
    entry.stableMpdu = GetStableMpdu(mpdu);
    entry.macServiceBytes = mpdu->GetPacketSize();
    entry.enqueueTimeNs = nowNs;
    entry.tag = tag;
    const auto duplicate =
        std::find_if(path.queueEntries.begin(),
                     path.queueEntries.end(),
                     [&entry](const QueueEntry& queued) {
                         return queued.stableMpdu == entry.stableMpdu;
                     });
    NS_ABORT_MSG_IF(duplicate != path.queueEntries.end(),
                    "A stable MPDU identity was enqueued more than once");
    path.queueEntries.push_back(entry);

    const auto key = MakeKey(*entry.tag);
    auto frameIterator = m_frames.find(key);
    NS_ABORT_MSG_IF(frameIterator == m_frames.end(),
                    "Tagged MAC enqueue references an unregistered frame");
    auto& frame = frameIterator->second;
    auto& packet = frame.packets.at(entry.tag->packetIndex);
    NS_ABORT_MSG_IF(packet.enqueued, "One logical streaming packet mapped to multiple MPDUs");
    const auto& planned = frame.plan.packets.at(entry.tag->packetIndex);
    if (planned.expectedMacServiceBytes)
    {
        NS_ABORT_MSG_IF(*planned.expectedMacServiceBytes != entry.macServiceBytes,
                        "Planned and observed MAC service-byte sizes disagree");
    }
    packet.enqueued = true;
    packet.queued = true;
    packet.macServiceBytes = entry.macServiceBytes;
    packet.enqueueTimeNs = nowNs;
    packet.stableMpdu = entry.stableMpdu;
    ++frame.packetsMacEnqueued;
    frame.latestFeatureEventTimeNs = nowNs;
    WriteEvent("MAC_ENQUEUE",
               pathId,
               *entry.tag,
               std::nullopt,
               entry.macServiceBytes);
}

void
PredictionTelemetryCollector::NotifyQueueDequeue(uint8_t pathId, Ptr<const WifiMpdu> mpdu)
{
    auto pathIterator = m_paths.find(pathId);
    NS_ABORT_MSG_IF(pathIterator == m_paths.end(), "MAC dequeue references an unbound path");
    StreamingFrameTag observedTag;
    if (!GetTag(mpdu, observedTag))
    {
        return;
    }
    NS_ABORT_MSG_IF(observedTag.pathId != pathId,
                    "Tagged MPDU dequeued on a different application path");
    auto& path = pathIterator->second;
    const uint64_t nowNs = NowNs();
    path.latestFeatureEventTimeNs = nowNs;
    const auto stableMpdu = GetStableMpdu(mpdu);
    const auto queued =
        std::find_if(path.queueEntries.begin(),
                     path.queueEntries.end(),
                     [stableMpdu](const QueueEntry& entry) {
                         return entry.stableMpdu == stableMpdu;
                     });
    NS_ABORT_MSG_IF(queued == path.queueEntries.end(),
                    "MAC dequeue has no matching observed enqueue");
    const auto tag = queued->tag;
    path.queueEntries.erase(queued);
    NS_ABORT_MSG_IF(!tag, "Tagged MAC dequeue matched an untagged queue entry");

    auto frameIterator = m_frames.find(MakeKey(*tag));
    NS_ABORT_MSG_IF(frameIterator == m_frames.end(),
                    "Tagged MAC dequeue references an unregistered frame");
    auto& frame = frameIterator->second;
    auto& packet = frame.packets.at(tag->packetIndex);
    NS_ABORT_MSG_IF(packet.stableMpdu != stableMpdu,
                    "MAC dequeue changed the stable MPDU identity");
    if (!packet.dequeued)
    {
        packet.dequeued = true;
        ++frame.packetsMacDequeued;
    }
    packet.queued = false;
    frame.latestFeatureEventTimeNs = nowNs;
    WriteEvent("MAC_DEQUEUE", pathId, *tag, std::nullopt, mpdu->GetPacketSize());
}

void
PredictionTelemetryCollector::NotifyPhyTxBegin(uint8_t pathId, Ptr<const Packet> packet)
{
    StreamingFrameTag tag;
    if (!packet->PeekPacketTag(tag))
    {
        return;
    }
    NS_ABORT_MSG_IF(tag.pathId != pathId,
                    "Tagged PHY transmission appeared on a different application path");
    auto pathIterator = m_paths.find(pathId);
    NS_ABORT_MSG_IF(pathIterator == m_paths.end(), "PHY attempt references an unbound path");
    auto frameIterator = m_frames.find(MakeKey(tag));
    NS_ABORT_MSG_IF(frameIterator == m_frames.end(),
                    "Tagged PHY attempt references an unregistered frame");
    auto& path = pathIterator->second;
    auto& frame = frameIterator->second;
    auto& packetState = frame.packets.at(tag.packetIndex);
    NS_ABORT_MSG_IF(!packetState.enqueued,
                    "PHY attempt occurred before the tagged packet's MAC enqueue");
    NS_ABORT_MSG_IF(packetState.acknowledged || packetState.terminallyDropped,
                    "PHY attempted a packet after its terminal outcome");
    NS_ABORT_MSG_IF(packetState.attemptPending,
                    "A new PHY attempt began before the previous attempt was finalized");

    const uint64_t nowNs = NowNs();
    const bool retry = packetState.attemptCount > 0;
    ++packetState.attemptCount;
    packetState.attemptPending = true;
    if (!packetState.firstAttemptTimeNs)
    {
        packetState.firstAttemptTimeNs = nowNs;
    }
    ++path.mpduAttempts;
    path.mpduRetries += retry;
    path.lastTxAttemptTimeNs = nowNs;
    path.latestFeatureEventTimeNs = nowNs;
    frame.latestFeatureEventTimeNs = nowNs;
    path.macEvents.push_back({nowNs,
                              MacEventKind::ATTEMPT,
                              retry,
                              0,
                              std::nullopt,
                              std::nullopt});
    WriteEvent("MPDU_TX_ATTEMPT",
               pathId,
               tag,
               packetState.attemptCount,
               packetState.macServiceBytes);
    if (retry)
    {
        WriteEvent("MPDU_RETRY",
                   pathId,
                   tag,
                   packetState.attemptCount,
                   packetState.macServiceBytes);
    }
    PruneHistories(path, nowNs);
}

void
PredictionTelemetryCollector::FinalizeAttemptFailure(PathState& path,
                                                      FrameState& frame,
                                                      PacketState& packet,
                                                      const StreamingFrameTag& tag,
                                                      uint64_t nowNs)
{
    if (!packet.attemptPending)
    {
        return;
    }
    packet.attemptPending = false;
    ++packet.attemptFailures;
    ++frame.mpduAttemptFailures;
    ++path.mpduAttemptFailures;
    frame.latestFeatureEventTimeNs = nowNs;
    path.latestFeatureEventTimeNs = nowNs;
    path.macEvents.push_back({nowNs,
                              MacEventKind::ATTEMPT_FAILURE,
                              false,
                              0,
                              std::nullopt,
                              std::nullopt});
    WriteEvent("MPDU_TX_ATTEMPT_FAILURE",
               tag.pathId,
               tag,
               packet.attemptCount,
               packet.macServiceBytes);
    PruneHistories(path, nowNs);
}

void
PredictionTelemetryCollector::NotifyNackedMpdu(uint8_t pathId, Ptr<const WifiMpdu> mpdu)
{
    NotifyResponseTimeout(pathId, mpdu);
}

void
PredictionTelemetryCollector::NotifyResponseTimeout(uint8_t pathId, Ptr<const WifiMpdu> mpdu)
{
    StreamingFrameTag tag;
    if (!GetTag(mpdu, tag))
    {
        return;
    }
    NS_ABORT_MSG_IF(tag.pathId != pathId,
                    "Tagged failed attempt appeared on a different application path");
    auto& path = m_paths.at(pathId);
    auto& frame = m_frames.at(MakeKey(tag));
    auto& packet = frame.packets.at(tag.packetIndex);
    FinalizeAttemptFailure(path, frame, packet, tag, NowNs());
}

void
PredictionTelemetryCollector::NotifyAckedMpdu(uint8_t pathId, Ptr<const WifiMpdu> mpdu)
{
    StreamingFrameTag tag;
    if (!GetTag(mpdu, tag))
    {
        return;
    }
    NS_ABORT_MSG_IF(tag.pathId != pathId,
                    "Tagged acknowledgement appeared on a different application path");
    auto& path = m_paths.at(pathId);
    auto& frame = m_frames.at(MakeKey(tag));
    auto& packet = frame.packets.at(tag.packetIndex);
    if (packet.acknowledged)
    {
        return;
    }
    NS_ABORT_MSG_IF(packet.terminallyDropped,
                    "A packet was acknowledged after a terminal drop");
    NS_ABORT_MSG_IF(!packet.attemptPending,
                    "Positive acknowledgement has no pending PHY attempt");
    NS_ABORT_MSG_IF(!packet.macServiceBytes || !packet.enqueueTimeNs ||
                        !packet.firstAttemptTimeNs,
                    "Positive acknowledgement lacks required MPDU timing state");

    const uint64_t nowNs = NowNs();
    packet.attemptPending = false;
    packet.acknowledged = true;
    ++frame.packetsTxSucceeded;
    ++path.mpduSuccesses;
    path.lastTxSuccessTimeNs = nowNs;
    path.latestFeatureEventTimeNs = nowNs;
    frame.latestFeatureEventTimeNs = nowNs;
    const double queueToAckUs = (nowNs - *packet.enqueueTimeNs) / 1000.0;
    const double firstAttemptToAckUs = (nowNs - *packet.firstAttemptTimeNs) / 1000.0;
    path.macEvents.push_back({nowNs,
                              MacEventKind::SUCCESS,
                              false,
                              *packet.macServiceBytes,
                              queueToAckUs,
                              firstAttemptToAckUs});
    WriteEvent("MPDU_TX_SUCCESS",
               pathId,
               tag,
               packet.attemptCount,
               packet.macServiceBytes);
    frame.senderMacComplete =
        frame.packetsTxSucceeded == frame.plan.frame.packetCount;
    PruneHistories(path, nowNs);
}

void
PredictionTelemetryCollector::NotifyDroppedMpdu(uint8_t pathId,
                                                 WifiMacDropReason reason,
                                                 Ptr<const WifiMpdu> mpdu)
{
    StreamingFrameTag tag;
    if (!GetTag(mpdu, tag))
    {
        return;
    }
    NS_ABORT_MSG_IF(tag.pathId != pathId,
                    "Tagged terminal drop appeared on a different application path");
    auto& path = m_paths.at(pathId);
    auto& frame = m_frames.at(MakeKey(tag));
    auto& packet = frame.packets.at(tag.packetIndex);
    if (packet.terminallyDropped)
    {
        return;
    }
    NS_ABORT_MSG_IF(packet.acknowledged, "Acknowledged MPDU was reported as terminally dropped");
    const uint64_t nowNs = NowNs();
    FinalizeAttemptFailure(path, frame, packet, tag, nowNs);
    const auto stableMpdu = GetStableMpdu(mpdu);
    const auto queued =
        std::find_if(path.queueEntries.begin(),
                     path.queueEntries.end(),
                     [stableMpdu](const QueueEntry& entry) {
                         return entry.stableMpdu == stableMpdu;
                     });
    if (queued != path.queueEntries.end())
    {
        path.queueEntries.erase(queued);
    }
    if (!packet.macServiceBytes)
    {
        packet.macServiceBytes = mpdu->GetPacketSize();
    }
    if (!packet.stableMpdu)
    {
        packet.stableMpdu = stableMpdu;
    }
    packet.terminallyDropped = true;
    packet.queued = false;
    ++frame.packetsTerminallyDropped;
    ++path.mpduTerminalDrops;
    switch (reason)
    {
    case WIFI_MAC_DROP_REACHED_RETRY_LIMIT:
        ++path.mpduRetryLimitDrops;
        break;
    case WIFI_MAC_DROP_EXPIRED_LIFETIME:
        ++path.mpduLifetimeDrops;
        break;
    case WIFI_MAC_DROP_FAILED_ENQUEUE:
    case WIFI_MAC_DROP_QOS_OLD_PACKET:
        ++path.mpduQueueDrops;
        break;
    }
    frame.latestFeatureEventTimeNs = nowNs;
    path.latestFeatureEventTimeNs = nowNs;
    WriteEvent("MAC_DROP",
               pathId,
               tag,
               packet.attemptCount,
               packet.macServiceBytes);
    WriteEvent("MPDU_TERMINAL_DROP",
               pathId,
               tag,
               packet.attemptCount,
               packet.macServiceBytes);
}

void
PredictionTelemetryCollector::NotifyPpduTx(uint8_t pathId,
                                            const WifiTxVector& txVector,
                                            bool taggedTarget)
{
    auto& path = m_paths.at(pathId);
    ++path.ppduTxCount;
    path.latestFeatureEventTimeNs = NowNs();
    if (taggedTarget)
    {
        const auto mode = txVector.GetMode();
        switch (mode.GetModulationClass())
        {
        case WIFI_MOD_CLASS_HT:
        case WIFI_MOD_CLASS_VHT:
        case WIFI_MOD_CLASS_HE:
        case WIFI_MOD_CLASS_EHT:
            path.currentMcs = mode.GetMcsValue();
            break;
        default:
            path.currentMcs.reset();
            break;
        }
        path.currentNss = txVector.GetNss();
        path.currentChannelWidthMhz =
            static_cast<uint16_t>(std::llround(txVector.GetChannelWidth()));
        path.currentGuardIntervalNs = txVector.GetGuardInterval().GetNanoSeconds();
    }
    WriteEvent("PPDU_TX", pathId, std::nullopt, std::nullopt, std::nullopt);
}

void
PredictionTelemetryCollector::NotifyPhyState(uint8_t pathId,
                                              Time start,
                                              Time duration,
                                              WifiPhyState state)
{
    if (!duration.IsStrictlyPositive())
    {
        return;
    }
    auto& path = m_paths.at(pathId);
    const uint64_t nowNs = NowNs();
    const int64_t startNs = start.GetNanoSeconds();
    const int64_t endNs = (start + duration).GetNanoSeconds();
    NS_ABORT_MSG_IF(startNs < path.telemetryStartNs || endNs <= startNs,
                    "Invalid PHY state interval for prediction telemetry");
    auto existing =
        std::find_if(path.phyIntervals.begin(),
                     path.phyIntervals.end(),
                     [startNs, state](const PhyInterval& interval) {
                         return interval.serial != 0 && interval.startNs == startNs &&
                                interval.state == state;
                     });
    PhyInterval interval{
        startNs, endNs, state, nowNs, path.phyIntervalSerial++};
    if (existing == path.phyIntervals.end())
    {
        path.phyIntervals.push_back(interval);
    }
    else
    {
        *existing = interval;
    }
    path.latestFeatureEventTimeNs = nowNs;
    WriteEvent("PHY_STATE_CHANGE", pathId, std::nullopt, std::nullopt, std::nullopt);
    PruneHistories(path, nowNs);
}

void
PredictionTelemetryCollector::PruneHistories(PathState& path, uint64_t nowNs)
{
    const uint64_t maximumWindowNs = m_historyWindowsUs.back() * 1000;
    const uint64_t guardNs = std::max<uint64_t>(1000000, maximumWindowNs / 10);
    const uint64_t cutoffNs =
        nowNs > maximumWindowNs + guardNs ? nowNs - maximumWindowNs - guardNs : 0;
    while (!path.macEvents.empty() && path.macEvents.front().timeNs < cutoffNs)
    {
        path.macEvents.pop_front();
    }
    path.phyIntervals.erase(
        std::remove_if(path.phyIntervals.begin(),
                       path.phyIntervals.end(),
                       [cutoffNs](const PhyInterval& interval) {
                           return interval.serial != 0 && interval.endNs >= 0 &&
                                  static_cast<uint64_t>(interval.endNs) < cutoffNs;
                       }),
        path.phyIntervals.end());
}

double
PredictionTelemetryCollector::Percentile(std::vector<double> values, double probability)
{
    NS_ABORT_MSG_IF(values.empty(), "Cannot calculate a percentile of an empty vector");
    NS_ABORT_MSG_IF(probability < 0 || probability > 1,
                    "Percentile probability must be in [0, 1]");
    std::sort(values.begin(), values.end());
    const double index = (values.size() - 1) * probability;
    const auto lower = static_cast<std::size_t>(std::floor(index));
    const auto upper = static_cast<std::size_t>(std::ceil(index));
    if (lower == upper)
    {
        return values[lower];
    }
    const double weight = index - lower;
    return values[lower] * (1 - weight) + values[upper] * weight;
}

PredictionRollingSample
PredictionTelemetryCollector::BuildRollingSample(const PathState& path,
                                                  uint64_t nowNs,
                                                  uint64_t windowUs) const
{
    PredictionRollingSample result;
    result.windowUs = windowUs;
    const uint64_t windowNs = windowUs * 1000;
    const uint64_t lowerNs = nowNs > windowNs ? nowNs - windowNs : 0;
    std::vector<double> queueToAck;
    std::vector<double> firstAttemptToAck;
    for (const auto& event : path.macEvents)
    {
        if (event.timeNs <= lowerNs || event.timeNs > nowNs)
        {
            continue;
        }
        switch (event.kind)
        {
        case MacEventKind::ATTEMPT:
            ++result.mpduAttempts;
            result.mpduRetries += event.retry;
            break;
        case MacEventKind::SUCCESS:
            ++result.mpduSuccesses;
            result.acknowledgedMacServiceBytes += event.acknowledgedBytes;
            if (event.queueToAckUs)
            {
                queueToAck.push_back(*event.queueToAckUs);
            }
            if (event.firstAttemptToAckUs)
            {
                firstAttemptToAck.push_back(*event.firstAttemptToAckUs);
            }
            break;
        case MacEventKind::ATTEMPT_FAILURE:
            ++result.mpduAttemptFailures;
            break;
        }
    }
    if (result.mpduAttempts > 0)
    {
        result.mpduRetryRatio =
            static_cast<double>(result.mpduRetries) / result.mpduAttempts;
    }
    if (!queueToAck.empty())
    {
        result.mpduQueueToAckMeanUs =
            std::accumulate(queueToAck.begin(), queueToAck.end(), 0.0) / queueToAck.size();
        result.mpduQueueToAckP95Us = Percentile(queueToAck, 0.95);
    }
    if (!firstAttemptToAck.empty())
    {
        result.mpduFirstAttemptToAckMeanUs =
            std::accumulate(firstAttemptToAck.begin(), firstAttemptToAck.end(), 0.0) /
            firstAttemptToAck.size();
        result.mpduFirstAttemptToAckP95Us = Percentile(firstAttemptToAck, 0.95);
    }

    const int64_t coverageStartNs =
        std::max<int64_t>(path.telemetryStartNs, static_cast<int64_t>(lowerNs));
    const int64_t coverageEndNs = static_cast<int64_t>(nowNs);
    if (coverageEndNs > coverageStartNs)
    {
        result.historyCoverageUs = (coverageEndNs - coverageStartNs) / 1000.0;
        std::vector<int64_t> boundaries{coverageStartNs, coverageEndNs};
        for (const auto& interval : path.phyIntervals)
        {
            if (interval.reportedAtNs > nowNs || interval.endNs <= coverageStartNs ||
                interval.startNs >= coverageEndNs)
            {
                continue;
            }
            boundaries.push_back(std::max(interval.startNs, coverageStartNs));
            boundaries.push_back(std::min(interval.endNs, coverageEndNs));
        }
        std::sort(boundaries.begin(), boundaries.end());
        boundaries.erase(std::unique(boundaries.begin(), boundaries.end()), boundaries.end());
        int64_t currentTailStartNs = coverageStartNs;
        for (const auto& interval : path.phyIntervals)
        {
            if (interval.serial != 0 && interval.reportedAtNs <= nowNs &&
                interval.startNs < coverageEndNs)
            {
                currentTailStartNs =
                    std::max(currentTailStartNs, std::min(interval.endNs, coverageEndNs));
            }
        }
        boundaries.push_back(currentTailStartNs);
        std::sort(boundaries.begin(), boundaries.end());
        boundaries.erase(std::unique(boundaries.begin(), boundaries.end()), boundaries.end());
        for (std::size_t index = 1; index < boundaries.size(); ++index)
        {
            const int64_t segmentStart = boundaries[index - 1];
            const int64_t segmentEnd = boundaries[index];
            if (segmentEnd <= segmentStart)
            {
                continue;
            }
            const int64_t midpoint = segmentStart + (segmentEnd - segmentStart) / 2;
            const PhyInterval* selected = nullptr;
            for (const auto& interval : path.phyIntervals)
            {
                if (interval.reportedAtNs <= nowNs && interval.startNs <= midpoint &&
                    midpoint < interval.endNs &&
                    (!selected ||
                     std::tie(interval.reportedAtNs, interval.serial) >
                         std::tie(selected->reportedAtNs, selected->serial)))
                {
                    selected = &interval;
                }
            }
            NS_ABORT_MSG_IF(!selected,
                            "PHY history has an uncovered interval within available coverage");
            const double durationUs = (segmentEnd - segmentStart) / 1000.0;
            const WifiPhyState selectedState =
                selected->serial == 0 && midpoint >= currentTailStartNs
                    ? path.phyState->GetState()
                    : selected->state;
            switch (selectedState)
            {
            case WifiPhyState::TX:
                result.phyTxTimeUs += durationUs;
                break;
            case WifiPhyState::RX:
                result.phyRxTimeUs += durationUs;
                break;
            case WifiPhyState::CCA_BUSY:
                result.phyBusyTimeUs += durationUs;
                break;
            case WifiPhyState::IDLE:
                result.phyIdleTimeUs += durationUs;
                break;
            case WifiPhyState::SWITCHING:
            case WifiPhyState::SLEEP:
            case WifiPhyState::OFF:
                result.phyOtherTimeUs += durationUs;
                break;
            }
        }
        const double total = result.phyTxTimeUs + result.phyRxTimeUs +
                             result.phyBusyTimeUs + result.phyIdleTimeUs +
                             result.phyOtherTimeUs;
        NS_ABORT_MSG_IF(std::abs(total - result.historyCoverageUs) > 0.001,
                        "PHY state durations do not sum to available history coverage");
        result.phyTxFraction = result.phyTxTimeUs / result.historyCoverageUs;
        result.phyRxFraction = result.phyRxTimeUs / result.historyCoverageUs;
        result.phyBusyFraction = result.phyBusyTimeUs / result.historyCoverageUs;
        result.phyIdleFraction = result.phyIdleTimeUs / result.historyCoverageUs;
        result.phyOtherFraction = result.phyOtherTimeUs / result.historyCoverageUs;
    }
    return result;
}

std::string
PredictionTelemetryCollector::MakeSupportMask(bool wifiBound,
                                              bool txVectorSupported,
                                              bool oracleSupported)
{
    uint64_t mask = (1ULL << static_cast<uint32_t>(PredictionFeatureSupportBit::FRAME_PLAN)) |
                    (1ULL << static_cast<uint32_t>(
                         PredictionFeatureSupportBit::SOCKET_SUBMISSION_PROGRESS));
    if (wifiBound)
    {
        mask |= (1ULL << static_cast<uint32_t>(PredictionFeatureSupportBit::MPDU_OUTCOMES)) |
                (1ULL << static_cast<uint32_t>(PredictionFeatureSupportBit::MAC_QUEUE)) |
                (1ULL << static_cast<uint32_t>(PredictionFeatureSupportBit::PHY_OCCUPANCY));
    }
    if (txVectorSupported)
    {
        mask |= 1ULL << static_cast<uint32_t>(PredictionFeatureSupportBit::TX_VECTOR);
    }
    if (oracleSupported)
    {
        mask |= 1ULL << static_cast<uint32_t>(PredictionFeatureSupportBit::CAUSAL_ORACLE);
    }
    std::ostringstream output;
    output << "0x" << std::hex << mask;
    return output.str();
}

std::string
PredictionTelemetryCollector::PhyStateToString(WifiPhyState state)
{
    std::ostringstream output;
    output << state;
    return output.str();
}

std::string
PredictionTelemetryCollector::AccessStatusToString(uint8_t status)
{
    switch (static_cast<Txop::ChannelAccessStatus>(status))
    {
    case Txop::NOT_REQUESTED:
        return "NOT_REQUESTED";
    case Txop::REQUESTED:
        return "REQUESTED";
    case Txop::GRANTED:
        return "GRANTED";
    }
    NS_ABORT_MSG("Unknown Txop channel access status");
}

std::string
PredictionTelemetryCollector::ExpectedAccessReasonToString(
    WifiExpectedAccessReason reason)
{
    switch (reason)
    {
    case WifiExpectedAccessReason::ACCESS_EXPECTED:
        return "ACCESS_EXPECTED";
    case WifiExpectedAccessReason::NOT_REQUESTED:
        return "NOT_REQUESTED";
    case WifiExpectedAccessReason::NOTHING_TO_TX:
        return "NOTHING_TO_TX";
    case WifiExpectedAccessReason::RX_END:
        return "RX_END";
    case WifiExpectedAccessReason::BUSY_END:
        return "BUSY_END";
    case WifiExpectedAccessReason::TX_END:
        return "TX_END";
    case WifiExpectedAccessReason::NAV_END:
        return "NAV_END";
    case WifiExpectedAccessReason::ACK_TIMER_END:
        return "ACK_TIMER_END";
    case WifiExpectedAccessReason::CTS_TIMER_END:
        return "CTS_TIMER_END";
    case WifiExpectedAccessReason::SWITCHING_END:
        return "SWITCHING_END";
    case WifiExpectedAccessReason::NO_PHY_END:
        return "NO_PHY_END";
    case WifiExpectedAccessReason::SLEEP_END:
        return "SLEEP_END";
    case WifiExpectedAccessReason::OFF_END:
        return "OFF_END";
    case WifiExpectedAccessReason::BACKOFF_END:
        return "BACKOFF_END";
    }
    NS_ABORT_MSG("Unknown WifiExpectedAccessReason");
}

void
PredictionTelemetryCollector::PopulateLinkSample(PredictionSample& sample,
                                                 const PredictionFrameKey& key,
                                                 const FrameState& frame,
                                                 PathState& path)
{
    const uint64_t nowNs = sample.sampleTimeNs;
    PruneHistories(path, nowNs);
    sample.latestFeatureEventTimeNs =
        std::max(sample.latestFeatureEventTimeNs, path.latestFeatureEventTimeNs);
    sample.mpduTxAttemptsTotal = path.mpduAttempts;
    sample.mpduTxSuccessesTotal = path.mpduSuccesses;
    sample.mpduTxAttemptFailuresTotal = path.mpduAttemptFailures;
    sample.mpduRetriesTotal = path.mpduRetries;
    sample.mpduTerminalDropsTotal = path.mpduTerminalDrops;
    sample.mpduRetryLimitDropsTotal = path.mpduRetryLimitDrops;
    sample.mpduLifetimeDropsTotal = path.mpduLifetimeDrops;
    sample.mpduQueueDropsTotal = path.mpduQueueDrops;
    sample.ppduTxCountTotal = path.ppduTxCount;
    sample.lastTxAttemptTimeNs = path.lastTxAttemptTimeNs;
    sample.lastTxSuccessTimeNs = path.lastTxSuccessTimeNs;
    sample.currentMcs = path.currentMcs;
    sample.currentNss = path.currentNss;
    sample.currentChannelWidthMhz = path.currentChannelWidthMhz;
    sample.currentGuardIntervalNs = path.currentGuardIntervalNs;
    sample.frequencyBand = path.frequencyBand;
    sample.centerFrequencyMhz = path.centerFrequencyMhz;
    sample.rolling.reserve(m_historyWindowsUs.size());
    for (const auto windowUs : m_historyWindowsUs)
    {
        sample.rolling.push_back(BuildRollingSample(path, nowNs, windowUs));
    }

    sample.framePacketsMacEnqueued = frame.packetsMacEnqueued;
    sample.framePacketsMacDequeued = frame.packetsMacDequeued;
    sample.framePacketsTxSucceeded = frame.packetsTxSucceeded;
    sample.frameMpduAttemptFailures = frame.mpduAttemptFailures;
    sample.framePacketsTerminallyDropped = frame.packetsTerminallyDropped;
    uint32_t frameQueuedPackets = 0;
    uint64_t frameQueuedBytes = 0;
    uint32_t pendingPackets = 0;
    bool allServiceBytesKnown = true;
    uint64_t unacknowledgedBytes = 0;
    uint64_t pendingBytes = 0;
    for (const auto& packet : frame.packets)
    {
        if (packet.queued)
        {
            ++frameQueuedPackets;
            NS_ABORT_MSG_IF(!packet.macServiceBytes,
                            "Queued tagged packet has no MAC service-byte size");
            frameQueuedBytes += *packet.macServiceBytes;
        }
        if (!packet.acknowledged && !packet.terminallyDropped)
        {
            ++pendingPackets;
        }
        if (!packet.macServiceBytes)
        {
            allServiceBytesKnown = false;
            continue;
        }
        if (!packet.acknowledged)
        {
            unacknowledgedBytes += *packet.macServiceBytes;
        }
        if (!packet.acknowledged && !packet.terminallyDropped)
        {
            pendingBytes += *packet.macServiceBytes;
        }
    }
    sample.framePacketsCurrentlyQueued = frameQueuedPackets;
    sample.frameMacServiceBytesCurrentlyQueued = frameQueuedBytes;
    sample.framePacketsPendingPrimary = pendingPackets;
    if (allServiceBytesKnown)
    {
        sample.frameMacServiceBytesNotAcknowledged = unacknowledgedBytes;
        sample.frameMacServiceBytesPendingPrimary = pendingBytes;
    }

    sample.macQueuePackets = path.queueEntries.size();
    uint64_t queueBytes = 0;
    std::optional<uint64_t> oldestEnqueue;
    for (const auto& entry : path.queueEntries)
    {
        queueBytes += entry.macServiceBytes;
        oldestEnqueue =
            oldestEnqueue ? std::min(*oldestEnqueue, entry.enqueueTimeNs) : entry.enqueueTimeNs;
    }
    sample.macQueueServiceBytes = queueBytes;
    sample.macQueueOldestEnqueueTimeNs = oldestEnqueue;

    uint32_t packetsAhead = 0;
    uint64_t bytesAhead = 0;
    bool foundFrame = false;
    for (const auto& entry : path.queueEntries)
    {
        if (entry.tag && MakeKey(*entry.tag).frameId == key.frameId &&
            entry.tag->pathId == key.pathId && entry.tag->copyId == key.copyId)
        {
            foundFrame = true;
            break;
        }
        ++packetsAhead;
        bytesAhead += entry.macServiceBytes;
    }
    if (foundFrame || frame.packetsSubmitted == 0)
    {
        sample.packetsAheadOfFrame = packetsAhead;
        sample.macServiceBytesAheadOfFrame = bytesAhead;
    }

    if (m_oracleFeaturesEnabled)
    {
        sample.currentCw = path.txop->GetCw(path.phyId);
        sample.remainingBackoffSlots = path.txop->GetBackoffSlots(path.phyId);
        const Time navRemaining =
            std::max(path.channelAccessManager->GetNavEnd() - Simulator::Now(), Time());
        sample.navRemainingUs = navRemaining.GetNanoSeconds() / 1000.0;
        sample.currentPhyState = PhyStateToString(path.phyState->GetState());
        sample.channelAccessStatus = AccessStatusToString(
            static_cast<uint8_t>(path.txop->GetAccessStatus(path.phyId)));
        sample.mediumBusyNow = path.channelAccessManager->IsBusy();
        sample.expectedAccessReasonWithinSlack = ExpectedAccessReasonToString(
            path.channelAccessManager->GetExpectedAccessWithin(
                MicroSeconds(sample.deadlineSlackUs)));
    }
    sample.featureSupportMask =
        MakeSupportMask(true, true, m_oracleFeaturesEnabled);
}

void
PredictionTelemetryCollector::CaptureSnapshot(PredictionFrameKey key, uint64_t offsetUs)
{
    const auto iterator = m_frames.find(key);
    NS_ABORT_MSG_IF(iterator == m_frames.end(), "Snapshot references an unknown frame copy");
    const auto& state = iterator->second;
    const uint64_t sampleTimeNs = NowNs();
    const uint64_t expectedTimeNs =
        state.plan.frame.generationTimeNs + offsetUs * static_cast<uint64_t>(1000);
    NS_ABORT_MSG_IF(sampleTimeNs != expectedTimeNs,
                    "Prediction snapshot did not execute at its configured offset");
    const uint64_t deadlineTimeNs =
        state.plan.frame.generationTimeNs +
        static_cast<uint64_t>(state.plan.frame.deadlineUs) * 1000;
    NS_ABORT_MSG_IF(sampleTimeNs >= deadlineTimeNs,
                    "Prediction snapshot executed at or after the frame deadline");
    NS_ABORT_MSG_IF(state.latestFeatureEventTimeNs > sampleTimeNs,
                    "Prediction snapshot contains a future feature event");

    PredictionSample sample;
    sample.runId = m_runId;
    sample.key = key;
    sample.sampleStage = MakeStageName(offsetUs);
    sample.sampleOffsetUs = offsetUs;
    sample.sampleTimeNs = sampleTimeNs;
    sample.latestFeatureEventTimeNs = state.latestFeatureEventTimeNs;
    sample.generationTimeNs = state.plan.frame.generationTimeNs;
    sample.deadlineTimeNs = deadlineTimeNs;
    sample.frameAgeUs = offsetUs;
    sample.deadlineSlackUs = state.plan.frame.deadlineUs - offsetUs;
    sample.senderMacComplete = state.senderMacComplete;
    sample.actionable = !state.senderMacComplete && sampleTimeNs < deadlineTimeNs;
    sample.frameSizeBytes = state.plan.frame.frameSizeBytes;
    sample.framePacketCount = state.plan.frame.packetCount;
    sample.frameType = state.plan.frame.frameType;
    sample.packetsSubmitted = state.packetsSubmitted;
    sample.applicationSocketPacketBytesSubmitted = state.submittedBytes;
    sample.packetsRemainingToSubmit =
        state.plan.frame.packetCount - state.packetsSubmitted;
    if (auto path = m_paths.find(key.pathId); path != m_paths.end())
    {
        PopulateLinkSample(sample, key, state, path->second);
    }
    else
    {
        sample.featureSupportMask = MakeSupportMask(false, false, false);
    }
    NS_ABORT_MSG_IF(sample.latestFeatureEventTimeNs > sample.sampleTimeNs,
                    "Prediction snapshot contains a future path feature event");
    m_samples.push_back(std::move(sample));
}

const std::vector<PredictionSample>&
PredictionTelemetryCollector::GetSamples() const
{
    return m_samples;
}

std::size_t
PredictionTelemetryCollector::GetRegisteredFrameCount() const
{
    return m_frames.size();
}

std::string
PredictionTelemetryCollector::MakeStageName(uint64_t offsetUs)
{
    if (offsetUs % 1000 == 0)
    {
        return "T" + std::to_string(offsetUs / 1000);
    }
    return "offset_" + std::to_string(offsetUs) + "us";
}

void
PredictionTelemetryCollector::DoDispose()
{
    for (auto& event : m_snapshotEvents)
    {
        event.Cancel();
    }
    m_snapshotEvents.clear();
    Object::DoDispose();
}

} // namespace ns3
