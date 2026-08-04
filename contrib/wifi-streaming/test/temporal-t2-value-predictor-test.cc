/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "ns3/temporal-t2-value-predictor.h"
#include "ns3/test.h"

#include "temporal-t2-feature-adapter-golden-v1.h"

#include <array>
#include <bit>
#include <cmath>
#include <cstdint>
#include <limits>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace ns3
{

/**
 * @ingroup tests
 * Test-only access to pure validation and feature construction.
 */
class TemporalT2ValuePredictorTestAccess
{
  public:
    /**
     * Return the production validation result without mutating history.
     *
     * @param predictor Predictor whose current state is used.
     * @param sample Candidate primary endpoint.
     * @return Error text, or empty when valid.
     */
    static std::optional<std::string> FindPrimaryError(
        const TemporalT2ValuePredictor& predictor,
        const PredictionSample& sample)
    {
        return predictor.FindPrimaryError(sample);
    }

    /**
     * Return the production delayed-report validation result.
     *
     * @param report Report to validate.
     * @param sampleTimeNs Associated sample time.
     * @return Error text, or empty when valid.
     */
    static std::optional<std::string> FindReportError(const PredictionPollingReport& report,
                                                      uint64_t sampleTimeNs)
    {
        return TemporalT2ValuePredictor::FindReportError(report, sampleTimeNs);
    }

    /**
     * Build features directly from four exact reports for frozen parity tests.
     *
     * @param current Current primary sample.
     * @param sourceFrameIds Exact source frame IDs in lag 1, 3, 8 order.
     * @param reports Exact source reports in lag 1, 3, 8 order.
     * @return Frozen 246-value adapter output.
     */
    static TemporalT2ValuePredictor::FeatureArray BuildDirect(
        const PredictionSample& current,
        const std::array<uint64_t, 3>& sourceFrameIds,
        const std::array<PredictionPollingReport, 3>& reports)
    {
        std::array<TemporalT2ValuePredictor::LaggedReportInput, 3> inputs;
        for (std::size_t index = 0; index < inputs.size(); ++index)
        {
            inputs[index] = {TemporalT2ValuePredictor::EXACT_LAGS[index],
                             sourceFrameIds[index],
                             &reports[index]};
        }
        return TemporalT2ValuePredictor::BuildFeatures(current,
                                                       *current.pollingReport,
                                                       inputs);
    }

    /**
     * Exercise the production missing-lag rejection path.
     *
     * @param current Current primary sample.
     * @param sourceFrameIds Exact source frame IDs.
     * @param reports Exact source reports.
     * @return Adapter output if the malformed input is accepted.
     */
    static TemporalT2ValuePredictor::FeatureArray BuildWithMissingLag(
        const PredictionSample& current,
        const std::array<uint64_t, 3>& sourceFrameIds,
        const std::array<PredictionPollingReport, 3>& reports)
    {
        std::array<TemporalT2ValuePredictor::LaggedReportInput, 3> inputs;
        for (std::size_t index = 0; index < inputs.size(); ++index)
        {
            inputs[index] = {TemporalT2ValuePredictor::EXACT_LAGS[index],
                             sourceFrameIds[index],
                             index == 1 ? nullptr : &reports[index]};
        }
        return TemporalT2ValuePredictor::BuildFeatures(current,
                                                       *current.pollingReport,
                                                       inputs);
    }

    /**
     * Build features through the production owned-history path.
     *
     * @param predictor History owner.
     * @param frameId Exact latest owned frame identity.
     * @return Frozen adapter output.
     */
    static TemporalT2ValuePredictor::FeatureArray BuildStored(
        const TemporalT2ValuePredictor& predictor,
        uint64_t frameId)
    {
        return predictor.BuildStoredFeatures(frameId);
    }
};

namespace
{

constexpr uint64_t START_NS = 900000000;
constexpr uint64_t FRAME_SPACING_NS = 34000000;

PredictionRollingSample
MakeRolling(uint64_t frameId, uint64_t windowUs, std::size_t windowIndex)
{
    PredictionRollingSample rolling;
    rolling.windowUs = windowUs;
    rolling.mpduAttempts = frameId + windowIndex + 1;
    rolling.mpduPositiveAcks = 1;
    rolling.mpduAttemptFailures = frameId + windowIndex;
    rolling.mpduRetries = rolling.mpduAttempts / 2;
    rolling.mpduRetryRatio =
        static_cast<double>(rolling.mpduRetries) / rolling.mpduAttempts;
    rolling.acknowledgedMacServiceBytes = 1000 + frameId * 10 + windowIndex;
    rolling.mpduQueueToAckMeanUs = 100.0 + frameId;
    rolling.mpduQueueToAckP95Us = 120.0 + frameId;
    rolling.mpduFirstAttemptToAckMeanUs = 80.0 + frameId;
    rolling.mpduFirstAttemptToAckP95Us = 90.0 + frameId;
    rolling.phyTxFraction = 0.1;
    rolling.phyRxFraction = 0.2;
    rolling.phyBusyFraction = 0.3;
    rolling.phyIdleFraction = 0.3;
    rolling.phyOtherFraction = 0.1;
    rolling.historyCoverageUs = windowUs;
    return rolling;
}

PredictionPollingReport
MakeReport(uint64_t frameId)
{
    const uint64_t generationNs = START_NS + frameId * FRAME_SPACING_NS;
    PredictionPollingReport report;
    report.captureTimeNs = generationNs + 1000000;
    report.availableTimeNs = report.captureTimeNs + 1000000;
    report.latestFeatureEventTimeNs = report.captureTimeNs - 500;
    report.latestFeatureEventSequence = 100 + frameId;
    const uint64_t counter = 10 + frameId * 10;
    report.mpduTxAttemptsTotal = counter;
    report.mpduPositiveAcksTotal = counter - 1;
    report.mpduTxAttemptFailuresTotal = counter - 2;
    report.mpduRetriesTotal = counter - 3;
    report.mpduTerminalDropsTotal = frameId;
    report.mpduRetryLimitDropsTotal = frameId;
    report.mpduLifetimeDropsTotal = frameId;
    report.mpduQueueDropsTotal = frameId;
    report.ppduTxCountTotal = counter - 4;
    report.lastTxAttemptTimeNs = report.captureTimeNs - 1000;
    report.lastPositiveAckTimeNs = report.captureTimeNs - 2000;
    report.currentMcs = 5;
    report.currentNss = 1;
    report.currentChannelWidthMhz = 20;
    report.currentGuardIntervalNs = 800;
    report.frequencyBand = "5GHz";
    report.centerFrequencyMhz = 5180.0;
    report.rolling = {MakeRolling(frameId, 1000, 0),
                      MakeRolling(frameId, 5000, 1),
                      MakeRolling(frameId, 20000, 2)};
    report.featureSupportMask = "0x3ffffffffdffff";
    return report;
}

PredictionSample
MakeSample(uint64_t frameId,
           bool actionable = true,
           FrameType frameType = FrameType::P_FRAME)
{
    constexpr uint32_t packetCount = 4;
    constexpr uint32_t frameSize = 4000;
    const uint64_t generationNs = START_NS + frameId * FRAME_SPACING_NS;
    PredictionSample sample;
    sample.runId = "temporal-t2-predictor-test";
    sample.key = {frameId, 1, 0};
    sample.sampleStage = "T2";
    sample.sampleOffsetUs = 2000;
    sample.sampleTimeNs = generationNs + 2000000;
    sample.latestFeatureEventTimeNs = sample.sampleTimeNs - 100;
    sample.latestFeatureEventSequence = 200 + frameId;
    sample.generationTimeNs = generationNs;
    sample.deadlineTimeNs = generationNs + 33333000;
    sample.frameAgeUs = 2000;
    sample.deadlineSlackUs = 31333;
    sample.senderMacComplete = !actionable;
    sample.actionable = actionable;
    sample.frameSizeBytes = frameSize;
    sample.framePacketCount = packetCount;
    sample.frameType = frameType;
    sample.packetsSubmitted = packetCount;
    sample.applicationSocketPacketBytesSubmitted = frameSize + packetCount * 50;
    sample.packetsRemainingToSubmit = 0;
    sample.framePacketsMacEnqueued = packetCount;
    sample.framePacketsMacDequeued = packetCount;
    sample.framePacketsTxSucceeded = actionable ? frameId % packetCount : packetCount;
    sample.frameMpduAttemptFailures = frameId;
    sample.framePacketsTerminallyDropped = 0;
    sample.framePacketsCurrentlyQueued = 0;
    sample.frameMacServiceBytesCurrentlyQueued = 0;
    sample.macQueuePackets = frameId;
    sample.macQueueServiceBytes = frameId * 100;
    sample.packetsAheadOfFrame = frameId % 3;
    sample.macServiceBytesAheadOfFrame = (frameId % 3) * 100;
    sample.framePacketsPendingPrimary =
        actionable ? packetCount - *sample.framePacketsTxSucceeded : 0;
    sample.frameMacServiceBytesNotAcknowledged =
        static_cast<uint64_t>(*sample.framePacketsPendingPrimary) * 1086;
    sample.frameMacServiceBytesPendingPrimary = sample.frameMacServiceBytesNotAcknowledged;
    sample.rolling = {MakeRolling(frameId + 1000, 1000, 0),
                      MakeRolling(frameId + 1000, 5000, 1),
                      MakeRolling(frameId + 1000, 20000, 2)};
    sample.featureSupportMask = "0x3ffffffffdffff";
    sample.pollingReport = MakeReport(frameId);
    return sample;
}

bool
HasError(const TemporalT2ValuePredictor& predictor, const PredictionSample& sample)
{
    return TemporalT2ValuePredictorTestAccess::FindPrimaryError(predictor, sample).has_value();
}

template <typename Callback>
bool
ThrowsInvalidArgument(Callback&& callback)
{
    try
    {
        callback();
    }
    catch (const std::invalid_argument&)
    {
        return true;
    }
    return false;
}

} // namespace

/**
 * @ingroup tests
 * Verify the fixed ring, exact lags, store-before-gates, and owned reports.
 */
class TemporalT2ValuePredictorHistoryTestCase : public TestCase
{
  public:
    /** Constructor. */
    TemporalT2ValuePredictorHistoryTestCase()
        : TestCase("Temporal T2 predictor retains exact owned history before policy gates")
    {
    }

  private:
    void DoRun() override
    {
        NS_TEST_ASSERT_MSG_EQ(TemporalT2ValuePredictor::HasExactModelContract(),
                              true,
                              "Frozen temporal T2 model contract differs");
        NS_TEST_ASSERT_MSG_EQ(TemporalT2ValuePredictor::PassesFrameGate(FrameType::P_FRAME),
                              true,
                              "P frame did not pass the caller-owned gate");
        NS_TEST_ASSERT_MSG_EQ(TemporalT2ValuePredictor::PassesFrameGate(FrameType::I_FRAME),
                              false,
                              "I frame passed the caller-owned P-frame gate");

        TemporalT2ValuePredictor predictor;
        std::vector<PredictionSample> samples;
        samples.reserve(13);
        for (uint64_t frameId = 0; frameId <= 12; ++frameId)
        {
            const bool actionable = frameId != 10;
            const FrameType type = frameId == 9 ? FrameType::I_FRAME : FrameType::P_FRAME;
            samples.push_back(MakeSample(frameId, actionable, type));
            const auto evidence = predictor.ObservePrimary(samples.back());
            NS_TEST_ASSERT_MSG_EQ(evidence.ready,
                                  frameId >= 8,
                                  "History readiness differs at frame " << frameId);
            for (std::size_t index = 0; index < evidence.lags.size(); ++index)
            {
                const uint64_t lag = std::array<uint64_t, 3>{1, 3, 8}[index];
                NS_TEST_ASSERT_MSG_EQ(evidence.lags[index].lagFrames,
                                      lag,
                                      "Lag evidence order differs");
                NS_TEST_ASSERT_MSG_EQ(evidence.lags[index].frameId.has_value(),
                                      frameId >= lag,
                                      "Lag evidence presence differs");
                NS_TEST_ASSERT_MSG_EQ(evidence.lags[index].pollCaptureTimeNs.has_value(),
                                      frameId >= lag,
                                      "Lag evidence capture presence differs");
                if (frameId >= lag)
                {
                    NS_TEST_ASSERT_MSG_EQ(*evidence.lags[index].frameId,
                                          frameId - lag,
                                          "Lag evidence frame differs");
                    NS_TEST_ASSERT_MSG_EQ(
                        *evidence.lags[index].pollCaptureTimeNs,
                        MakeReport(frameId - lag).captureTimeNs,
                        "Lag evidence capture time differs");
                }
            }

            if (frameId == 0)
            {
                NS_TEST_ASSERT_MSG_LT(samples[0].sampleTimeNs,
                                      1000000000,
                                      "Frame zero is not before the decision window");
            }
            else if (frameId == 8)
            {
                NS_TEST_ASSERT_MSG_EQ(evidence.ready,
                                      true,
                                      "Frame 8 is not the first history-ready frame");
                NS_TEST_ASSERT_MSG_EQ(*evidence.lags[2].frameId,
                                      0,
                                      "Frame 8 does not use frame 0 as exact lag 8");
                samples[0].pollingReport->rolling[0].mpduAttempts = 999999;
                const auto features8 =
                    TemporalT2ValuePredictorTestAccess::BuildStored(predictor, 8);
                NS_TEST_ASSERT_MSG_EQ(features8[193],
                                      1.0,
                                      "Owned frame-0 lag-8 report changed with its caller copy");
            }
            else if (frameId == 11)
            {
                NS_TEST_ASSERT_MSG_EQ(samples[10].actionable,
                                      false,
                                      "Frame 10 is not the nonactionable history sentinel");
                samples[10].pollingReport->rolling[0].mpduAttempts = 999999;
                const auto features11 =
                    TemporalT2ValuePredictorTestAccess::BuildStored(predictor, 11);
                NS_TEST_ASSERT_MSG_EQ(features11[87],
                                      11.0,
                                      "Owned nonactionable frame 10 was not used as lag 1");
            }
            else if (frameId == 12)
            {
                NS_TEST_ASSERT_MSG_EQ(static_cast<uint8_t>(samples[9].frameType),
                                      static_cast<uint8_t>(FrameType::I_FRAME),
                                      "Frame 9 is not the I-frame history sentinel");
                samples[9].pollingReport->rolling[0].mpduAttempts = 999999;
                samples[12].pollingReport->rolling[0].mpduAttempts = 999999;
                samples[12].frameMacServiceBytesPendingPrimary = 999999;
                samples[12].actionable = false;
                samples[12].senderMacComplete = true;

                const auto features12 =
                    TemporalT2ValuePredictorTestAccess::BuildStored(predictor, 12);
                NS_TEST_ASSERT_MSG_EQ(features12[140],
                                      10.0,
                                      "Owned I-frame 9 was not used as exact lag 3");
                NS_TEST_ASSERT_MSG_EQ(features12[21],
                                      13.0,
                                      "Owned current delayed report changed with its caller copy");
                NS_TEST_ASSERT_MSG_EQ(features12[20],
                                      4344.0,
                                      "Owned current live feature changed with its caller copy");
                NS_TEST_ASSERT_MSG_EQ(features12[21] ==
                                          samples[12].rolling[0].mpduAttempts,
                                      false,
                                      "Adapter read live rolling instead of the owned "
                                      "delayed report");
                const auto result = predictor.Evaluate(12);
                NS_TEST_ASSERT_MSG_EQ(std::isfinite(result.valuePerCostScore),
                                      true,
                                      "History-ready model result is nonfinite");
            }
        }
        NS_TEST_ASSERT_MSG_EQ(
            ThrowsInvalidArgument([&]() {
                TemporalT2ValuePredictorTestAccess::BuildStored(predictor, 11);
            }),
            true,
            "Owned evaluation accepted a non-latest frame identity");
    }
};

/**
 * @ingroup tests
 * Verify all five frozen real-ledger adapter and evaluator parity cases.
 */
class TemporalT2ValuePredictorGoldenParityTestCase : public TestCase
{
  public:
    /** Constructor. */
    TemporalT2ValuePredictorGoldenParityTestCase()
        : TestCase("Temporal T2 runtime adapter matches five frozen ledger fixtures")
    {
    }

  private:
    void DoRun() override
    {
        using namespace temporal_t2_feature_adapter_golden_v1;

        NS_TEST_ASSERT_MSG_EQ(CONTRACT_SHA256,
                              "d2bd9b1277a84e51d72579573a7b50891445ec08cdfa0f09e049eb89fdad53b0",
                              "Frozen adapter-fixture contract digest differs");
        NS_TEST_ASSERT_MSG_EQ(ORDERED_FEATURE_NAMES_SHA256,
                              "a00ebbb9807f99972f2cd009d1b2a20bf0b001cee123ac60d5121b2b1c07209e",
                              "Frozen feature-name digest differs");
        NS_TEST_ASSERT_MSG_EQ(SOURCE_MODEL_SHA256,
                              "dff01b0f8319320489709c4039d97011f35439aa92adedbe167fe61b9de7bcb8",
                              "Golden source-model digest differs");
        NS_TEST_ASSERT_MSG_EQ(SOURCE_SELECTION_SHA256,
                              "c7f886a4ca1a29b9fbd2e25d19d78f994d7136ecdea4f6a16db77eacacf5ce9f",
                              "Golden source-selection digest differs");
        NS_TEST_ASSERT_MSG_EQ(SOURCE_DATASET_SHA256,
                              "9376face6806929318c92e74fc2c47da740e187c7b0570910a37acdd1f3be0bc",
                              "Golden source-dataset digest differs");

        const auto cases = GetCases();
        constexpr std::array<std::string_view, 5> expectedIds{
            "threshold_equal_delayed_not_live",
            "nearest_calibration_below_threshold",
            "nearest_calibration_above_threshold",
            "i_frame_above_threshold_gate_probe",
            "first_history_ready_lag8_last_ack_missing",
        };
        constexpr std::array<uint64_t, 3> exactLags{1, 3, 8};
        constexpr std::array<std::array<uint64_t, 3>, 5> expectedSourceSampleTimes{{
            {{29668666667ULL, 29602000000ULL, 29435333333ULL}},
            {{11635333333ULL, 11568666667ULL, 11402000000ULL}},
            {{17402000000ULL, 17335333333ULL, 17168666667ULL}},
            {{4968666667ULL, 4902000000ULL, 4735333333ULL}},
            {{1235333333ULL, 1168666667ULL, 1002000000ULL}},
        }};
        constexpr std::array<std::array<uint64_t, 3>, 5> expectedPollCaptures{{
            {{29667000000ULL, 29601000000ULL, 29434000000ULL}},
            {{11634000000ULL, 11567000000ULL, 11401000000ULL}},
            {{17401000000ULL, 17334000000ULL, 17167000000ULL}},
            {{4967000000ULL, 4901000000ULL, 4734000000ULL}},
            {{1234000000ULL, 1167000000ULL, 1001000000ULL}},
        }};
        constexpr std::array<std::array<uint64_t, 3>, 5> expectedPollAvailability{{
            {{29668000000ULL, 29602000000ULL, 29435000000ULL}},
            {{11635000000ULL, 11568000000ULL, 11402000000ULL}},
            {{17402000000ULL, 17335000000ULL, 17168000000ULL}},
            {{4968000000ULL, 4902000000ULL, 4735000000ULL}},
            {{1235000000ULL, 1168000000ULL, 1002000000ULL}},
        }};
        constexpr std::array<std::array<FrameType, 3>, 5> expectedSourceTypes{{
            {{FrameType::P_FRAME, FrameType::P_FRAME, FrameType::P_FRAME}},
            {{FrameType::P_FRAME, FrameType::P_FRAME, FrameType::P_FRAME}},
            {{FrameType::P_FRAME, FrameType::P_FRAME, FrameType::P_FRAME}},
            {{FrameType::P_FRAME, FrameType::P_FRAME, FrameType::P_FRAME}},
            {{FrameType::P_FRAME, FrameType::P_FRAME, FrameType::I_FRAME}},
        }};
        constexpr std::array<std::array<bool, 3>, 5> expectedSourceActionability{{
            {{true, true, true}},
            {{true, false, true}},
            {{true, false, false}},
            {{true, true, true}},
            {{true, true, true}},
        }};
        NS_TEST_ASSERT_MSG_EQ(cases.size(), expectedIds.size(), "Golden fixture count differs");

        for (std::size_t caseIndex = 0; caseIndex < cases.size(); ++caseIndex)
        {
            const auto& golden = cases[caseIndex];
            NS_TEST_ASSERT_MSG_EQ(golden.fixtureId,
                                  expectedIds[caseIndex],
                                  "Golden fixture order differs");

            std::array<uint64_t, 3> sourceFrameIds;
            std::array<PredictionPollingReport, 3> reports;
            for (std::size_t lagIndex = 0; lagIndex < golden.lags.size(); ++lagIndex)
            {
                const auto& lag = golden.lags[lagIndex];
                NS_TEST_ASSERT_MSG_EQ(lag.lagFrames,
                                      exactLags[lagIndex],
                                      golden.fixtureId << ": exact-lag order differs");
                NS_TEST_ASSERT_MSG_EQ(lag.sourceFrameId,
                                      golden.currentSample.key.frameId - exactLags[lagIndex],
                                      golden.fixtureId << ": exact-lag frame differs");
                NS_TEST_ASSERT_MSG_EQ(lag.sourcePathId,
                                      1,
                                      golden.fixtureId << ": lag path differs");
                NS_TEST_ASSERT_MSG_EQ(lag.sourceCopyId,
                                      0,
                                      golden.fixtureId << ": lag copy differs");
                NS_TEST_ASSERT_MSG_EQ(lag.sourceStage,
                                      "T2",
                                      golden.fixtureId << ": lag stage differs");
                NS_TEST_ASSERT_MSG_EQ(
                    lag.sourceSampleTimeNs,
                    expectedSourceSampleTimes[caseIndex][lagIndex],
                    golden.fixtureId << ": lag source-sample time provenance differs");
                NS_TEST_ASSERT_MSG_EQ(
                    static_cast<uint8_t>(lag.sourceFrameType),
                    static_cast<uint8_t>(expectedSourceTypes[caseIndex][lagIndex]),
                    golden.fixtureId << ": lag source-frame type differs");
                NS_TEST_ASSERT_MSG_EQ(
                    lag.sourceSampleActionable,
                    expectedSourceActionability[caseIndex][lagIndex],
                    golden.fixtureId << ": lag source actionability differs");
                NS_TEST_ASSERT_MSG_EQ(lag.pollCaptureTimeNs,
                                      expectedPollCaptures[caseIndex][lagIndex],
                                      golden.fixtureId << ": lag capture provenance differs");
                NS_TEST_ASSERT_MSG_EQ(lag.pollAvailableTimeNs,
                                      expectedPollAvailability[caseIndex][lagIndex],
                                      golden.fixtureId
                                          << ": lag availability provenance differs");
                NS_TEST_ASSERT_MSG_EQ(lag.pollCaptureTimeNs,
                                      lag.report.captureTimeNs,
                                      golden.fixtureId << ": lag capture metadata differs");
                NS_TEST_ASSERT_MSG_EQ(lag.pollAvailableTimeNs,
                                      lag.report.availableTimeNs,
                                      golden.fixtureId << ": lag availability metadata differs");
                sourceFrameIds[lagIndex] = lag.sourceFrameId;
                reports[lagIndex] = lag.report;
            }

            const auto features = TemporalT2ValuePredictorTestAccess::BuildDirect(
                golden.currentSample,
                sourceFrameIds,
                reports);
            std::vector<uint16_t> nanIndices;
            for (std::size_t featureIndex = 0; featureIndex < features.size(); ++featureIndex)
            {
                const uint32_t expectedWord = golden.expectedFeatureWords[featureIndex];
                if (expectedWord == 0x7fc00000U)
                {
                    NS_TEST_ASSERT_MSG_EQ(std::isnan(features[featureIndex]),
                                          true,
                                          golden.fixtureId
                                              << ": expected NaN at feature " << featureIndex);
                    nanIndices.push_back(static_cast<uint16_t>(featureIndex));
                    continue;
                }
                NS_TEST_ASSERT_MSG_EQ(std::isfinite(features[featureIndex]),
                                      true,
                                      golden.fixtureId
                                          << ": unexpected nonfinite feature " << featureIndex);
                NS_TEST_ASSERT_MSG_EQ(std::bit_cast<uint32_t>(
                                          static_cast<float>(features[featureIndex])),
                                      expectedWord,
                                      golden.fixtureId
                                          << ": float32 word differs at feature " << featureIndex);
                NS_TEST_ASSERT_MSG_EQ(features[featureIndex],
                                      static_cast<double>(std::bit_cast<float>(expectedWord)),
                                      golden.fixtureId
                                          << ": widened float32 differs at feature "
                                          << featureIndex);
            }
            NS_TEST_ASSERT_MSG_EQ(nanIndices.size(),
                                  golden.expectedNanCount,
                                  golden.fixtureId << ": NaN count differs");
            for (std::size_t index = 0; index < nanIndices.size(); ++index)
            {
                NS_TEST_ASSERT_MSG_EQ(nanIndices[index],
                                      golden.expectedNanIndices[index],
                                      golden.fixtureId << ": NaN index differs");
            }

            const uint32_t expectedIFrameWord =
                golden.currentSample.frameType == FrameType::I_FRAME ? 0x3f800000U : 0U;
            const uint32_t expectedPFrameWord =
                golden.currentSample.frameType == FrameType::P_FRAME ? 0x3f800000U : 0U;
            NS_TEST_ASSERT_MSG_EQ(std::bit_cast<uint32_t>(static_cast<float>(features[66])),
                                  expectedIFrameWord,
                                  golden.fixtureId << ": I-frame one-hot word differs");
            NS_TEST_ASSERT_MSG_EQ(std::bit_cast<uint32_t>(static_cast<float>(features[67])),
                                  expectedPFrameWord,
                                  golden.fixtureId << ": P-frame one-hot word differs");

            const auto currentRateCounters = std::array<uint64_t, 5>{
                *golden.currentSample.pollingReport->mpduTxAttemptsTotal,
                *golden.currentSample.pollingReport->mpduPositiveAcksTotal,
                *golden.currentSample.pollingReport->mpduTxAttemptFailuresTotal,
                *golden.currentSample.pollingReport->mpduRetriesTotal,
                *golden.currentSample.pollingReport->ppduTxCountTotal,
            };
            for (std::size_t lagIndex = 0; lagIndex < golden.lags.size(); ++lagIndex)
            {
                const auto& lagReport = golden.lags[lagIndex].report;
                const auto lagRateCounters = std::array<uint64_t, 5>{
                    *lagReport.mpduTxAttemptsTotal,
                    *lagReport.mpduPositiveAcksTotal,
                    *lagReport.mpduTxAttemptFailuresTotal,
                    *lagReport.mpduRetriesTotal,
                    *lagReport.ppduTxCountTotal,
                };
                const double exactSpanMs =
                    static_cast<double>(golden.currentSample.pollingReport->captureTimeNs -
                                        lagReport.captureTimeNs) /
                    1000000.0;
                for (std::size_t rateIndex = 0; rateIndex < lagRateCounters.size();
                     ++rateIndex)
                {
                    const double exactRate =
                        static_cast<double>(currentRateCounters[rateIndex] -
                                            lagRateCounters[rateIndex]) /
                        exactSpanMs;
                    const std::size_t featureIndex = 135 + lagIndex * 53 + rateIndex;
                    NS_TEST_ASSERT_MSG_EQ(
                        std::bit_cast<uint32_t>(static_cast<float>(features[featureIndex])),
                        std::bit_cast<uint32_t>(static_cast<float>(exactRate)),
                        golden.fixtureId << ": exact poll-span rate differs at lag "
                                         << exactLags[lagIndex] << " counter " << rateIndex);
                }
            }

            const auto result = TemporalT2ValueModelEvaluator::Evaluate(features);
            const auto& expected = golden.expectedModelResult;
            NS_TEST_ASSERT_MSG_EQ_TOL(result.primaryBad12Logit,
                                      expected.primaryBad12Logit,
                                      1e-11,
                                      golden.fixtureId << ": primary logit differs");
            NS_TEST_ASSERT_MSG_EQ_TOL(result.primaryBad12Probability,
                                      expected.primaryBad12Probability,
                                      1e-12,
                                      golden.fixtureId << ": primary probability differs");
            NS_TEST_ASSERT_MSG_EQ_TOL(result.treatedBad12Logit,
                                      expected.treatedBad12Logit,
                                      1e-11,
                                      golden.fixtureId << ": treated logit differs");
            NS_TEST_ASSERT_MSG_EQ_TOL(result.treatedBad12Probability,
                                      expected.treatedBad12Probability,
                                      1e-12,
                                      golden.fixtureId << ": treated probability differs");
            NS_TEST_ASSERT_MSG_EQ_TOL(result.predictedLogAirtime,
                                      expected.predictedLogAirtime,
                                      1e-9,
                                      golden.fixtureId << ": predicted log airtime differs");
            NS_TEST_ASSERT_MSG_EQ_TOL(result.predictedSecondaryAirtimeUs,
                                      expected.predictedSecondaryAirtimeUs,
                                      1e-8,
                                      golden.fixtureId << ": predicted airtime differs");
            NS_TEST_ASSERT_MSG_EQ_TOL(result.nonnegativeBad12Value,
                                      expected.nonnegativeBad12Value,
                                      1e-12,
                                      golden.fixtureId << ": nonnegative value differs");
            NS_TEST_ASSERT_MSG_EQ(result.valuePerCostScore,
                                  expected.valuePerCostScore,
                                  golden.fixtureId << ": float32 score differs");
            NS_TEST_ASSERT_MSG_EQ(result.passesScoreThreshold,
                                  expected.passesScoreThreshold,
                                  golden.fixtureId << ": score gate differs");

            const bool pFrameGate =
                TemporalT2ValuePredictor::PassesFrameGate(golden.currentSample.frameType);
            NS_TEST_ASSERT_MSG_EQ(pFrameGate,
                                  golden.expectedPFrameGate,
                                  golden.fixtureId << ": P-frame gate differs");
            NS_TEST_ASSERT_MSG_EQ(result.passesScoreThreshold && pFrameGate,
                                  golden.expectedScoreAndPFrameGates,
                                  golden.fixtureId << ": combined score and P-frame gates differ");

            if (golden.fixtureId == "threshold_equal_delayed_not_live")
            {
                NS_TEST_ASSERT_MSG_EQ(features[21],
                                      10.0,
                                      "Equality fixture did not use delayed rolling attempts");
                NS_TEST_ASSERT_MSG_EQ(golden.currentSample.rolling[0].mpduAttempts,
                                      0,
                                      "Equality fixture live sentinel differs");
                NS_TEST_ASSERT_MSG_EQ(features[85],
                                      24599.90625,
                                      "Equality fixture delayed ACK age differs");
                NS_TEST_ASSERT_MSG_EQ(result.valuePerCostScore,
                                      TemporalT2ValueModelEvaluator::GetScoreThreshold(),
                                      "Equality fixture does not equal the frozen threshold");
            }
            if (golden.fixtureId == "nearest_calibration_below_threshold")
            {
                NS_TEST_ASSERT_MSG_EQ(golden.lags[1].sourceSampleActionable,
                                      false,
                                      "Below-threshold fixture lost its nonactionable "
                                      "lag-3 sentinel");
            }
            if (golden.fixtureId == "nearest_calibration_above_threshold")
            {
                NS_TEST_ASSERT_MSG_EQ(golden.lags[1].sourceSampleActionable,
                                      false,
                                      "Above-threshold fixture lost its nonactionable "
                                      "lag-3 sentinel");
                NS_TEST_ASSERT_MSG_EQ(golden.lags[2].sourceSampleActionable,
                                      false,
                                      "Above-threshold fixture lost its nonactionable "
                                      "lag-8 sentinel");
            }
            if (golden.fixtureId == "first_history_ready_lag8_last_ack_missing")
            {
                NS_TEST_ASSERT_MSG_EQ(golden.currentSample.key.frameId,
                                      8,
                                      "First-ready fixture current frame is not 8");
                NS_TEST_ASSERT_MSG_EQ(golden.temporalCsvRowIndex,
                                      0,
                                      "First-ready fixture is not temporal row zero");
                NS_TEST_ASSERT_MSG_EQ(golden.temporalCsvLineNumber,
                                      2,
                                      "First-ready fixture source line is not two");
                NS_TEST_ASSERT_MSG_EQ(golden.lags[0].sourceFrameId,
                                      7,
                                      "First-ready lag-1 identity differs");
                NS_TEST_ASSERT_MSG_EQ(golden.lags[1].sourceFrameId,
                                      5,
                                      "First-ready lag-3 identity differs");
                NS_TEST_ASSERT_MSG_EQ(golden.lags[2].sourceFrameId,
                                      0,
                                      "First-ready lag-8 identity differs");
                NS_TEST_ASSERT_MSG_EQ(std::isnan(features[239]),
                                      true,
                                      "First-ready fixture lag-8 ACK age is not missing");
                NS_TEST_ASSERT_MSG_EQ(features[240],
                                      1.0,
                                      "First-ready fixture lag-8 ACK missing flag differs");
                constexpr std::array<std::size_t, 9> structuralZeroIndices{
                    98, 110, 122, 151, 163, 175, 204, 216, 228};
                constexpr std::array<double, 9> expectedStructuralZeros{
                    1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0};
                for (std::size_t index = 0; index < structuralZeroIndices.size(); ++index)
                {
                    NS_TEST_ASSERT_MSG_EQ(
                        features[structuralZeroIndices[index]],
                        expectedStructuralZeros[index],
                        "First-ready fixture structural-zero feature differs");
                }
            }
        }
    }
};

/**
 * @ingroup tests
 * Verify strict fail-closed validation and malformed direct adapter inputs.
 */
class TemporalT2ValuePredictorValidationTestCase : public TestCase
{
  public:
    /** Constructor. */
    TemporalT2ValuePredictorValidationTestCase()
        : TestCase("Temporal T2 predictor rejects telemetry and history contract drift")
    {
    }

  private:
    void DoRun() override
    {
        TemporalT2ValuePredictor empty;
        auto sample = MakeSample(0);
        NS_TEST_ASSERT_MSG_EQ(HasError(empty, sample), false, "Valid frame zero was rejected");

        auto invalid = sample;
        invalid.key.frameId = 1;
        NS_TEST_ASSERT_MSG_EQ(HasError(empty, invalid), true, "Nonzero first frame was accepted");
        invalid = sample;
        invalid.telemetrySchemaVersion++;
        NS_TEST_ASSERT_MSG_EQ(HasError(empty, invalid), true, "Schema drift was accepted");
        invalid = sample;
        invalid.key.pathId = 0;
        NS_TEST_ASSERT_MSG_EQ(HasError(empty, invalid), true, "Wrong primary path was accepted");
        invalid = sample;
        invalid.sampleStage = "T4";
        NS_TEST_ASSERT_MSG_EQ(HasError(empty, invalid), true, "Wrong stage was accepted");
        invalid = sample;
        invalid.sampleTimeNs++;
        NS_TEST_ASSERT_MSG_EQ(HasError(empty, invalid), true, "Wrong sample time was accepted");
        invalid = sample;
        invalid.featureSupportMask = "0x3ffffffffdfffe";
        NS_TEST_ASSERT_MSG_EQ(HasError(empty, invalid), true, "Support subset was accepted");
        invalid = sample;
        invalid.featureSupportMask = "0x7ffffffffdffff";
        NS_TEST_ASSERT_MSG_EQ(HasError(empty, invalid), true, "Support superset was accepted");
        invalid = sample;
        invalid.featureSupportMask = "bad-mask";
        NS_TEST_ASSERT_MSG_EQ(HasError(empty, invalid),
                              true,
                              "Malformed support mask was accepted");
        invalid = sample;
        invalid.pollingReport.reset();
        NS_TEST_ASSERT_MSG_EQ(HasError(empty, invalid),
                              true,
                              "Missing polling report was accepted");
        invalid = sample;
        invalid.latestFeatureEventTimeNs.reset();
        NS_TEST_ASSERT_MSG_EQ(HasError(empty, invalid),
                              true,
                              "Inconsistent sample watermark was accepted");
        invalid = sample;
        invalid.latestFeatureEventTimeNs = invalid.sampleTimeNs + 1;
        NS_TEST_ASSERT_MSG_EQ(HasError(empty, invalid),
                              true,
                              "Future sample watermark was accepted");

        const auto expectBadReport = [&sample](const PredictionPollingReport& report) {
            return TemporalT2ValuePredictorTestAccess::FindReportError(report,
                                                                       sample.sampleTimeNs)
                .has_value();
        };
        auto report = *sample.pollingReport;
        report.captureTimeNs++;
        NS_TEST_ASSERT_MSG_EQ(expectBadReport(report), true, "Off-cadence report was accepted");
        report = *sample.pollingReport;
        report.availableTimeNs++;
        NS_TEST_ASSERT_MSG_EQ(expectBadReport(report), true, "Wrong report delay was accepted");
        report = *sample.pollingReport;
        report.captureTimeNs -= 1000000;
        report.availableTimeNs -= 1000000;
        NS_TEST_ASSERT_MSG_EQ(expectBadReport(report), true, "Two-ms stale report was accepted");
        report = *sample.pollingReport;
        report.featureSupportMask = "0x3fffffffffffff";
        NS_TEST_ASSERT_MSG_EQ(expectBadReport(report), true, "Polling mask drift was accepted");
        report = *sample.pollingReport;
        report.latestFeatureEventTimeNs.reset();
        NS_TEST_ASSERT_MSG_EQ(expectBadReport(report),
                              true,
                              "Polling watermark drift was accepted");
        report = *sample.pollingReport;
        report.lastTxAttemptTimeNs = report.captureTimeNs + 1;
        NS_TEST_ASSERT_MSG_EQ(expectBadReport(report),
                              true,
                              "Future polling radio event was accepted");
        report = *sample.pollingReport;
        report.mpduTxAttemptsTotal.reset();
        NS_TEST_ASSERT_MSG_EQ(expectBadReport(report), true, "Missing counter was accepted");
        report = *sample.pollingReport;
        report.rolling.pop_back();
        NS_TEST_ASSERT_MSG_EQ(expectBadReport(report), true, "Missing window was accepted");
        report = *sample.pollingReport;
        report.rolling[0].windowUs = 5000;
        NS_TEST_ASSERT_MSG_EQ(expectBadReport(report), true, "Reordered window was accepted");
        report = *sample.pollingReport;
        report.rolling[0].historyCoverageUs = 999;
        NS_TEST_ASSERT_MSG_EQ(expectBadReport(report), true, "Partial coverage was accepted");
        report = *sample.pollingReport;
        report.rolling[0].mpduRetries = report.rolling[0].mpduAttempts + 1;
        NS_TEST_ASSERT_MSG_EQ(expectBadReport(report), true, "Impossible retries were accepted");
        report = *sample.pollingReport;
        report.rolling[0].mpduRetryRatio.reset();
        NS_TEST_ASSERT_MSG_EQ(expectBadReport(report), true, "Missing retry ratio was accepted");
        report = *sample.pollingReport;
        report.rolling[0].mpduQueueToAckMeanUs = std::numeric_limits<double>::max();
        NS_TEST_ASSERT_MSG_EQ(expectBadReport(report),
                              true,
                              "Float32-overflow ACK latency was accepted");
        report = *sample.pollingReport;
        report.rolling[0].mpduQueueToAckMeanUs.reset();
        NS_TEST_ASSERT_MSG_EQ(expectBadReport(report), true, "Partial ACK latency was accepted");
        report = *sample.pollingReport;
        report.rolling[0].phyTxFraction.reset();
        NS_TEST_ASSERT_MSG_EQ(expectBadReport(report), true, "Missing PHY fraction was accepted");
        report = *sample.pollingReport;
        report.rolling[0].phyTxFraction = 2.0;
        NS_TEST_ASSERT_MSG_EQ(expectBadReport(report), true, "Out-of-range fraction was accepted");
        report = *sample.pollingReport;
        report.rolling[0].phyTxFraction = 0.2;
        NS_TEST_ASSERT_MSG_EQ(expectBadReport(report), true, "Bad fraction sum was accepted");

        TemporalT2ValuePredictor stateful;
        stateful.ObservePrimary(sample);
        auto next = MakeSample(1);
        invalid = next;
        invalid.key.frameId = 2;
        NS_TEST_ASSERT_MSG_EQ(HasError(stateful, invalid), true, "Frame gap was accepted");
        invalid = next;
        invalid.runId = "different-run";
        NS_TEST_ASSERT_MSG_EQ(HasError(stateful, invalid), true, "Run drift was accepted");
        invalid = next;
        invalid.latestFeatureEventSequence = 1;
        NS_TEST_ASSERT_MSG_EQ(HasError(stateful, invalid),
                              true,
                              "Sample sequence reset was accepted");
        invalid = next;
        invalid.latestFeatureEventTimeNs = *sample.latestFeatureEventTimeNs - 1;
        NS_TEST_ASSERT_MSG_EQ(HasError(stateful, invalid),
                              true,
                              "Sample time watermark reset accepted");
        invalid = next;
        invalid.pollingReport->latestFeatureEventSequence = 1;
        NS_TEST_ASSERT_MSG_EQ(HasError(stateful, invalid),
                              true,
                              "Poll sequence reset was accepted");
        invalid = next;
        invalid.pollingReport->latestFeatureEventTimeNs =
            *sample.pollingReport->latestFeatureEventTimeNs - 1;
        NS_TEST_ASSERT_MSG_EQ(HasError(stateful, invalid),
                              true,
                              "Poll time watermark reset accepted");
        invalid = next;
        invalid.pollingReport->mpduTxAttemptsTotal =
            *sample.pollingReport->mpduTxAttemptsTotal - 1;
        NS_TEST_ASSERT_MSG_EQ(HasError(stateful, invalid), true, "Counter reset was accepted");

        auto current = MakeSample(8);
        std::array<uint64_t, 3> sourceIds{7, 5, 0};
        std::array<PredictionPollingReport, 3> reports{
            MakeReport(7), MakeReport(5), MakeReport(0)};
        NS_TEST_ASSERT_MSG_EQ(
            ThrowsInvalidArgument([&]() {
                auto wrong = sourceIds;
                wrong[1] = 4;
                TemporalT2ValuePredictorTestAccess::BuildDirect(current, wrong, reports);
            }),
            true,
            "Wrong exact-lag identity was accepted");
        NS_TEST_ASSERT_MSG_EQ(
            ThrowsInvalidArgument([&]() {
                TemporalT2ValuePredictorTestAccess::BuildWithMissingLag(current,
                                                                        sourceIds,
                                                                        reports);
            }),
            true,
            "Missing exact-lag report was accepted");
        NS_TEST_ASSERT_MSG_EQ(
            ThrowsInvalidArgument([&]() {
                auto future = reports;
                future[0].lastTxAttemptTimeNs = future[0].captureTimeNs + 1;
                TemporalT2ValuePredictorTestAccess::BuildDirect(current,
                                                                sourceIds,
                                                                future);
            }),
            true,
            "Future lag radio timestamp was accepted");
        NS_TEST_ASSERT_MSG_EQ(
            ThrowsInvalidArgument([&]() {
                auto infinite = current;
                infinite.pollingReport->rolling[0].mpduQueueToAckMeanUs =
                    std::numeric_limits<double>::infinity();
                TemporalT2ValuePredictorTestAccess::BuildDirect(infinite,
                                                                sourceIds,
                                                                reports);
            }),
            true,
            "Infinite adapter input was accepted");
        NS_TEST_ASSERT_MSG_EQ(
            ThrowsInvalidArgument([&]() {
                auto overflow = current;
                overflow.pollingReport->rolling[0].mpduQueueToAckMeanUs =
                    std::numeric_limits<double>::max();
                TemporalT2ValuePredictorTestAccess::BuildDirect(overflow,
                                                                sourceIds,
                                                                reports);
            }),
            true,
            "Float32-overflow adapter input was accepted");
    }
};

/**
 * @ingroup tests
 * Unit suite for the exact temporal T2 runtime adapter and history.
 */
class TemporalT2ValuePredictorTestSuite : public TestSuite
{
  public:
    /** Constructor. */
    TemporalT2ValuePredictorTestSuite()
        : TestSuite("wifi-streaming-temporal-t2-value-predictor", Type::UNIT)
    {
        AddTestCase(new TemporalT2ValuePredictorHistoryTestCase, Duration::QUICK);
        AddTestCase(new TemporalT2ValuePredictorGoldenParityTestCase, Duration::QUICK);
        AddTestCase(new TemporalT2ValuePredictorValidationTestCase, Duration::QUICK);
    }
};

static TemporalT2ValuePredictorTestSuite g_temporalT2ValuePredictorTestSuite;

} // namespace ns3
