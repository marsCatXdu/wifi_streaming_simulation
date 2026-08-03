/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef EXPERIMENT_OUTPUT_H
#define EXPERIMENT_OUTPUT_H

#include "metrics-collector.h"

#include "ns3/nstime.h"

#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <vector>

namespace ns3
{

/**
 * Resolved configuration for one overlapping infrastructure BSS.
 */
struct ObssBssConfig
{
    uint32_t bssId{0};
    uint8_t linkId{0};
    std::string ssid;
    std::string standard;
    double apX{0};
    double apY{0};
    std::vector<double> staX;
    std::vector<double> staY;
};

/**
 * Resolved settings written with every streaming experiment.
 */
struct StreamingRunConfig
{
    std::string runId;
    uint32_t rngSeed{1};
    uint64_t rngRun{1};
    std::string topology;
    std::string policy;
    std::string source{"synthetic"};
    std::string traceFile;
    std::string emissionMode;
    double durationSeconds{0};
    double warmupSeconds{0};
    double fps{0};
    uint32_t frameSizeBytes{0};
    uint32_t gopLength{0};
    double keyframeSizeMultiplier{1};
    uint32_t payloadSizeBytes{0};
    uint32_t deadlineUs{0};
    std::string propagationModel{"fixed_rss"};
    double fixedRssDbm{0};
    double stationDistanceM{10};
    double pathLossExponent{3};
    double referenceLoss2GhzDb{40.046};
    double referenceLoss5GhzDb{46.678};
    double nakagamiDistance1M{80};
    double nakagamiDistance2M{200};
    double nakagamiM0{1.5};
    double nakagamiM1{0.75};
    double nakagamiM2{0.75};
    int64_t propagationStreamBase{5000};
    std::string standard;
    std::string stationManager{"ConstantRateWifiManager"};
    std::string dataMode;
    std::string controlMode;
    std::vector<std::string> channelSettings;
    std::vector<std::string> frequencyRanges;
    std::vector<std::string> perLinkDataModes;
    std::string guardInterval;
    uint32_t queueMaxPackets{0};
    uint32_t queueMaxDelayMs{0};
    uint32_t maxAmpduSizeBytes{0};
    uint32_t maxAmsduSizeBytes{0};
    uint32_t mloStaMaxInflights{1};
    bool ulOfdmaEnabled{false};
    std::string ulOfdmaScope{"all_he_eht_aps"};
    uint32_t ulOfdmaAccessIntervalMs{0};
    bool ulOfdmaBsrpEnabled{false};
    uint32_t ulOfdmaMaxStations{0};
    uint32_t ulOfdmaPsduSizeBytes{0};
    uint32_t frameRetryLimit{0};
    uint32_t rtsCtsThresholdBytes{0};
    uint32_t fragmentationThresholdBytes{0};
    uint32_t txopLimitUs{0};
    std::string accessCategory;
    bool blockAckEnabled{false};
    bool staticAssociation{false};
    std::string tidToLinkMapping;
    std::string strMode{"not_applicable"};
    std::string multiLinkMode{"not_applicable"}; ///< Active native multi-link mode
    bool emlsrActivated{false};                  ///< Whether EMLSR support is activated
    std::string emlsrProfile{"not_applicable"}; ///< Predeclared EMLSR profile identifier
    std::string emlsrManager{"not_applicable"}; ///< EMLSR manager TypeId name
    std::string emlsrApManager{"not_applicable"}; ///< AP EMLSR manager TypeId name
    std::vector<uint8_t> emlsrLinkIds;           ///< Links configured for EMLSR mode
    uint8_t emlsrMainPhyId{0};                   ///< Preferred main PHY identifier
    uint32_t emlsrPaddingDelayUs{0};             ///< Advertised padding delay in microseconds
    uint32_t emlsrTransitionDelayUs{0};          ///< Advertised transition delay in microseconds
    uint32_t emlsrTransitionTimeoutUs{0};         ///< AP transition timeout in microseconds
    uint32_t emlsrMediumSyncDurationUs{0};        ///< Medium sync duration in microseconds
    int32_t emlsrMsdOfdmEdThresholdDbm{0};        ///< Medium sync OFDM threshold in dBm
    uint8_t emlsrMsdMaxNTxops{0};                 ///< Medium sync TXOP-attempt limit
    uint32_t emlsrChannelSwitchDelayUs{0};       ///< PHY channel switch delay in microseconds
    bool emlsrSwitchAuxPhy{false};               ///< Whether the auxiliary PHY switches bands
    bool emlsrAuxPhyTxCapable{false};            ///< Whether the auxiliary PHY can transmit
    uint32_t emlsrAuxPhyChannelWidthMhz{0};      ///< Auxiliary PHY channel width in MHz
    std::string emlsrAuxPhyMaxModulationClass{"not_applicable"}; ///< Auxiliary modulation limit
    bool emlsrPutAuxPhyToSleep{false};           ///< Whether the auxiliary PHY sleeps in TXOPs
    bool emlsrInDeviceInterference{false};       ///< Whether in-device interference is modeled
    bool emlsrUseNotifiedMacHeader{false};        ///< Whether the STA manager uses MAC headers
    bool emlsrResetCamState{false};               ///< Whether switches reset channel access state
    bool emlsrAllowUlTxopInRx{false};             ///< Whether UL TXOPs may start during reception
    bool emlsrInterruptSwitch{false};             ///< Whether a main-PHY switch may be interrupted
    bool emlsrUseAuxPhyCca{false};                ///< Whether switching reuses auxiliary PHY CCA
    uint32_t emlsrSwitchMainPhyBackDelayUs{0};    ///< Main-PHY return delay in microseconds
    bool emlsrKeepMainPhyAfterDlTxop{false};      ///< Whether the main PHY stays after DL TXOPs
    bool emlsrCheckAccessOnMainPhyLink{false};    ///< Whether main-link access is compared
    std::string emlsrMinAcToSkipCheckAccess{"not_applicable"}; ///< Minimum AC allowed to skip
    bool emlsrApUseNotifiedMacHeader{false};      ///< Whether the AP manager uses MAC headers
    bool emlsrApEarlySwitchToListening{false};    ///< Whether the AP switches early to listening
    bool emlsrApWaitTransDelayOnPsduRxError{false}; ///< Whether AP waits after PSDU RX errors
    bool emlsrApUpdateCwAfterFailedIcf{false};    ///< Whether AP updates CW after failed ICFs
    bool emlsrApReportFailedIcf{false};           ///< Whether AP reports failed ICFs
    bool emlsrCamGenerateBackoffWithoutTx{false}; ///< Whether an unused TXOP generates backoff
    bool emlsrCamProactiveBackoff{false};         ///< Whether channel access backs off proactively
    uint32_t emlsrCamResetBackoffThresholdUs{0}; ///< Channel-access reset threshold
    uint8_t emlsrCamNSlotsLeft{0};                ///< Early channel-access alert threshold
    uint32_t emlsrCamNSlotsLeftMinDelayUs{0};     ///< Early-alert minimum delay
    bool emlsrNotifyMacHeaderRxEnd{false};       ///< Whether PHY MAC-header notifications are on
    std::vector<std::string> emlsrMainPhyFrequencyRanges; ///< Main PHY frequency interfaces
    uint32_t applicationSocketCount{0};
    bool applicationDuplication{false};
    bool packetEventLogsEnabled{false};
    bool predictionTelemetryEnabled{false};
    std::vector<uint64_t> predictionSampleOffsetsUs;
    std::vector<uint64_t> predictionHistoryWindowsUs;
    uint64_t predictionPollingIntervalUs{1000};
    uint64_t predictionPollingReportDelayUs{1000};
    bool predictionEventLogEnabled{false};
    bool predictionOracleFeaturesEnabled{false};
    double selectiveDuplicationThreshold{0};
    double selectiveDuplicationFrameBudget{0};
    uint32_t selectiveDuplicationBurstHorizonFrames{0};
    std::vector<uint64_t> selectiveDuplicationDecisionOffsetsUs;
    bool secondaryAirtimeMeterEnabled{false};
    double adaptiveAirtimeBudgetFraction{0};
    uint64_t adaptiveAirtimeBucketHorizonUs{0};
    uint64_t adaptiveAirtimeInitialBucketHorizonUs{0}; ///< Startup-credit horizon.
    double adaptiveAirtimeInitialShadowPrice{0};
    double adaptiveAirtimeDualStep{0};
    bool adaptiveAirtimeAdmissionUsesRetryInflation{true}; ///< Admission cost mode.
    std::string adaptiveAirtimeAdmissionPacketCost{
        "launched_packet_set"}; ///< Packet set priced by admission.
    double adaptiveAirtimeCostSafetyFactor{0};
    double adaptiveAirtimeCostEwmaAlpha{0};
    std::vector<uint64_t> adaptiveAirtimeDecisionOffsetsUs;
    std::map<uint64_t, double> adaptiveAirtimeDecisionOffsetShadowPrices;
    std::vector<uint64_t> adaptiveAirtimeIFrameOnlyDecisionOffsetsUs;
    uint32_t fullDuplicationPrimaryPath{0};
    double staticLink0Score{0};
    double staticLink1Score{0};
    std::string backgroundTraffic{"none"};
    std::string backgroundProfile{"none"};
    std::string backgroundDirection{"uplink"};
    std::string correlationMode{"independent"};
    std::string correlationTrace;
    std::vector<uint32_t> backgroundStations;
    std::vector<std::string> backgroundStandards;
    std::vector<std::vector<std::string>> backgroundStationStandards;
    std::vector<int64_t> backgroundApplicationStreams;
    std::string backgroundAssociationMode{"not_applicable"};
    double backgroundRateMbps{0};
    uint32_t backgroundPacketSizeBytes{0};
    double backgroundNearDistanceM{0};
    double backgroundFarDistanceM{0};
    int64_t backgroundStreamBase{0};
    double commonOnMeanMs{0};
    double commonOffMeanMs{0};
    double localOnMeanMs{0};
    double localOffMeanMs{0};
    double commonOnDurationMs{0};
    double commonOffDurationMs{0};
    double localOnDurationMs{0};
    double localOffDurationMs{0};
    double randomOnMeanMs{0};
    double randomOffMeanMs{0};
    std::string obssProfile{"none"};
    uint32_t obssStationsPerBss{0};
    double obssMinRateMbps{0};
    double obssMaxRateMbps{0};
    double obssUlMinRateMbps{0};
    double obssUlMaxRateMbps{0};
    double obssDlMinRateMbps{0};
    double obssDlMaxRateMbps{0};
    double obssOnMeanMs{0};
    double obssOffMeanMs{0};
    std::string obssStationManager{"constant"};
    double obssManagerUpdateMs{0};
    bool obssUseLatestAmendmentOnly{false};
    uint32_t obssPacketSizeBytes{0};
    double obssAreaMinXM{0};
    double obssAreaMaxXM{0};
    double obssAreaMinYM{0};
    double obssAreaMaxYM{0};
    double obssStaMinDistanceM{0};
    double obssStaMaxDistanceM{0};
    int64_t obssPlacementStreamBase{0};
    int64_t obssApplicationStreamBase{0};
    int64_t obssWifiStreamBase{0};
    std::vector<ObssBssConfig> obssBsses;
};

/**
 * Runtime state proving that the requested EMLSR profile was activated.
 */
struct MloRuntimeInfo
{
    std::string mode{"not_applicable"};          ///< Observed native multi-link mode
    std::string profile{"not_applicable"};       ///< Predeclared EMLSR profile identifier
    bool stationEmlsrActivated{false};            ///< STA EMLSR activation state
    bool apEmlsrActivated{false};                 ///< AP EMLSR activation state
    std::string emlsrManager{"not_applicable"};  ///< Observed EMLSR manager TypeId name
    std::string apEmlsrManager{"not_applicable"}; ///< Observed AP EMLSR manager TypeId name
    std::vector<uint8_t> emlsrLinkIds;            ///< Observed EMLSR link identifiers
    std::vector<bool> apEmlsrEnabledPerLink;      ///< AP view of EMLSR per setup link
    uint8_t mainPhyId{0};                         ///< Observed main PHY identifier
    uint8_t initialMainPhyLinkId{0};              ///< Initial link carrying the main PHY
    std::string initialMainPhyBand{"not_applicable"}; ///< Initial main PHY frequency band
    uint32_t paddingDelayUs{0};                   ///< Observed padding delay in microseconds
    uint32_t transitionDelayUs{0};                ///< Observed transition delay in microseconds
    uint32_t transitionTimeoutUs{0};              ///< Observed transition timeout in microseconds
    uint32_t mediumSyncDurationUs{0};             ///< Observed medium sync duration in microseconds
    int32_t msdOfdmEdThresholdDbm{0};             ///< Observed medium sync threshold in dBm
    uint8_t msdMaxNTxops{0};                      ///< Observed medium sync TXOP-attempt limit
    uint32_t channelSwitchDelayUs{0};             ///< Observed switch delay in microseconds
    bool switchAuxPhy{false};                     ///< Observed auxiliary PHY switching mode
    bool auxPhyTxCapable{false};                  ///< Observed auxiliary transmit capability
    uint32_t auxPhyChannelWidthMhz{0};            ///< Observed auxiliary channel width in MHz
    std::string auxPhyMaxModulationClass{"not_applicable"}; ///< Auxiliary modulation limit
    bool putAuxPhyToSleep{false};                 ///< Observed auxiliary PHY sleep setting
    bool inDeviceInterference{false};             ///< Observed in-device interference setting
    bool useNotifiedMacHeader{false};              ///< Observed STA MAC-header-use setting
    bool resetCamState{false};                     ///< Observed channel-access reset setting
    bool allowUlTxopInRx{false};                   ///< Observed UL-during-RX setting
    bool interruptSwitch{false};                   ///< Observed switch-interruption setting
    bool useAuxPhyCca{false};                      ///< Observed auxiliary CCA setting
    uint32_t switchMainPhyBackDelayUs{0};          ///< Observed main-PHY return delay
    bool keepMainPhyAfterDlTxop{false};            ///< Observed post-DL main-PHY setting
    bool checkAccessOnMainPhyLink{false};          ///< Observed main-link access check
    std::string minAcToSkipCheckAccess{"not_applicable"}; ///< Observed skip-check AC
    bool apUseNotifiedMacHeader{false};            ///< Observed AP MAC-header-use setting
    bool apEarlySwitchToListening{false};          ///< Observed AP early-listening setting
    bool apWaitTransDelayOnPsduRxError{false};     ///< Observed AP RX-error delay setting
    bool apUpdateCwAfterFailedIcf{false};          ///< Observed AP failed-ICF CW setting
    bool apReportFailedIcf{false};                 ///< Observed AP failed-ICF report setting
    bool allPhySettingsMatchProfile{false};        ///< Whether every STA/AP PHY matches the profile
    bool allCamSettingsMatchProfile{false};        ///< Whether every STA/AP CAM matches the profile
    bool notifyMacHeaderRxEnd{false};             ///< Observed PHY MAC-header notification setting
    std::vector<std::string> mainPhyFrequencyRanges; ///< Main PHY frequency interfaces
    std::vector<uint64_t> successfulMpdusPerLink; ///< Successful MPDUs ordered by link ID
    std::vector<uint64_t> phyTxTimeUsPerLink;     ///< Sender PHY TX time ordered by link ID
};

/**
 * Build identity written with every streaming experiment.
 */
struct StreamingBuildInfo
{
    std::string ns3Version;
    std::string ns3UpstreamCommit;
    std::string projectGitCommit;
    std::string compiler;
    std::string buildProfile;
    std::string executionTimestampUtc;
    std::string host;
};

/**
 * One application/MAC/PHY measurement-window record.
 */
struct LinkIntervalRecord
{
    uint64_t timestampUs{0};
    uint8_t linkId{0};
    uint64_t applicationBytesSent{0};
    uint64_t applicationBytesReceived{0};
    uint64_t redundantBytes{0};
    uint64_t probeBytes{0};
    uint64_t successfulMpdus{0};
    uint64_t failedMpdus{0};
    uint64_t retransmissions{0};
    std::optional<double> meanMpduServiceTimeUs;
    std::optional<double> p95MpduServiceTimeUs;
    std::optional<uint64_t> queueBytes;
    std::optional<double> estimatedRateMbps;
    uint64_t phyIdleTimeUs{0};
    uint64_t phyCcaBusyTimeUs{0};
    uint64_t phyTxTimeUs{0};
    uint64_t phyRxTimeUs{0};
};

/**
 * Aggregate MAC record for one sender radio.
 */
struct MacSummaryRecord
{
    uint8_t linkId{0};
    uint32_t nodeId{0};
    uint32_t deviceId{0};
    uint64_t successfulMpdus{0};
    uint64_t failedMpdus{0};
    uint64_t retransmissions{0};
    uint64_t retryLimitDrops{0};
    std::optional<double> meanMpduServiceTimeUs;
    std::optional<double> p95MpduServiceTimeUs;
};

/**
 * Aggregate record for one independently randomized OBSS flow.
 */
struct BackgroundFlowRecord
{
    std::string runId;
    uint32_t bssId{0};
    uint8_t linkId{0};
    std::string standard;
    uint32_t staIndex{0};
    std::string direction;
    uint32_t sourceNodeId{0};
    uint32_t destinationNodeId{0};
    uint16_t port{0};
    int64_t rateStream{0};
    int64_t onStream{0};
    int64_t offStream{0};
    uint32_t periodCount{0};
    uint64_t bytesSent{0};
    uint64_t bytesReceived{0};
};

/**
 * One sampled offered rate for an OBSS flow ON period.
 */
struct BackgroundRatePeriodRecord
{
    std::string runId;
    uint32_t bssId{0};
    uint32_t staIndex{0};
    std::string direction;
    uint32_t periodIndex{0};
    uint64_t startUs{0};
    uint64_t endUs{0};
    double rateMbps{0};
};

/**
 * Stable run-level statistics.
 */
struct StreamingRunSummary
{
    uint64_t frameCount{0};
    uint64_t completeFrameCount{0};
    uint64_t incompleteFrameCount{0};
    uint64_t deadlineMissCount{0};
    double completeRatio{0};
    double incompleteRatio{0};
    double deadlineMissRatio{0};
    std::optional<double> latencyP50Us;
    std::optional<double> latencyP90Us;
    std::optional<double> latencyP95Us;
    std::optional<double> latencyP99Us;
    std::optional<double> latencyP999Us;
    double applicationGoodputMbps{0};
    double redundantByteRatio{0};
    uint64_t applicationBytesSent{0};
    uint64_t applicationBytesDelivered{0};
    uint64_t redundantBytesSent{0};
    uint64_t duplicateFrameCount{0};
    uint64_t duplicateRecoveryCount{0};
    double duplicateRecoveryRate{0};
    uint64_t duplicateNoBenefitCount{0};
    double duplicateNoBenefitRatio{0};
    uint64_t successfulMpdus{0};
    uint64_t failedMpdus{0};
    uint64_t retransmissions{0};
    uint64_t phyTxTimeUs{0};
    uint64_t phyRxTimeUs{0};
    uint64_t phyCcaBusyTimeUs{0};
    uint64_t backgroundBytesSent{0};
    uint64_t backgroundBytesReceived{0};
    std::vector<uint64_t> backgroundBytesSentPerLink;
    std::vector<uint64_t> backgroundBytesReceivedPerLink;
    std::vector<uint64_t> backgroundBytesSentPerStation;
    std::vector<uint64_t> backgroundBytesReceivedPerStation;
    double backgroundThroughputMbps{0};
};

/**
 * Creates stable, machine-readable output artifacts for an experiment run.
 */
class ExperimentOutput
{
  public:
    static constexpr const char* NS3_UPSTREAM_COMMIT =
        "d2add90b452d600cfb4859baed8e9ea633519447";

    static double Percentile(std::vector<double> values, double quantile);
    static StreamingRunSummary ComputeSummary(const std::vector<FrameResult>& frames,
                                              double measurementDurationSeconds,
                                              uint64_t applicationBytesSent,
                                              uint64_t redundantBytesSent,
                                              const std::vector<LinkIntervalRecord>& links);
    static void PrepareRunDirectory(const std::string& outputDir);
    static void WriteResolvedConfig(const std::string& outputDir,
                                    const StreamingRunConfig& config);
    /**
     * Write the observed EMLSR profile and per-link activity.
     *
     * @param outputDir Run output directory.
     * @param info Observed multi-link runtime state.
     */
    static void WriteMloRuntime(const std::string& outputDir, const MloRuntimeInfo& info);
    static void WriteBuildInfo(const std::string& outputDir, const StreamingBuildInfo& info);
    static void WriteLinkIntervals(const std::string& outputDir,
                                   const std::vector<LinkIntervalRecord>& records);
    static void WriteMacSummary(const std::string& outputDir,
                                const std::vector<MacSummaryRecord>& records);
    static void WriteBackgroundFlows(const std::string& outputDir,
                                     const std::vector<BackgroundFlowRecord>& records);
    static void WriteBackgroundRatePeriods(
        const std::string& outputDir,
        const std::vector<BackgroundRatePeriodRecord>& records);
    static void WriteSummary(const std::string& outputDir, const StreamingRunSummary& summary);
};

} // namespace ns3

#endif // EXPERIMENT_OUTPUT_H
