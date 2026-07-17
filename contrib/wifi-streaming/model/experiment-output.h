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
    uint32_t frameRetryLimit{0};
    uint32_t rtsCtsThresholdBytes{0};
    uint32_t fragmentationThresholdBytes{0};
    uint32_t txopLimitUs{0};
    std::string accessCategory;
    bool blockAckEnabled{false};
    bool staticAssociation{false};
    std::string tidToLinkMapping;
    std::string strMode{"not_applicable"};
    uint32_t applicationSocketCount{0};
    bool applicationDuplication{false};
    bool packetEventLogsEnabled{false};
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
