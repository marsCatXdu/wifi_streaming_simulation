/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "ns3/temporal-t2-distribution-predictor.h"
#include "ns3/test.h"

#include <algorithm>
#include <array>
#include <bit>
#include <cmath>
#include <cstdint>
#include <limits>
#include <optional>
#include <stdexcept>
#include <string>

namespace ns3
{

/**
 * @ingroup tests
 * Test-only access to pure paired-adapter validation and construction.
 */
class TemporalT2DistributionPredictorTestAccess
{
  public:
    /**
     * Return the production secondary validation result without mutation.
     *
     * @param predictor Predictor whose current state is used.
     * @param sample Candidate secondary endpoint.
     * @return Error text, or empty when valid.
     */
    static std::optional<std::string> FindSecondaryError(
        const TemporalT2DistributionPredictor& predictor,
        const PredictionSample& sample)
    {
        return predictor.FindSecondaryError(sample);
    }

    /**
     * Return the production secondary-report validation result.
     *
     * @param report Report to validate.
     * @param sampleTimeNs Associated sample time.
     * @return Error text, or empty when valid.
     */
    static std::optional<std::string> FindSecondaryReportError(
        const PredictionPollingReport& report,
        uint64_t sampleTimeNs)
    {
        return TemporalT2DistributionPredictor::FindSecondaryReportError(report,
                                                                          sampleTimeNs);
    }

    /**
     * Return the production immutable-pair validation result.
     *
     * @param primary Candidate primary endpoint.
     * @param secondary Candidate secondary endpoint.
     * @return Error text, or empty when valid.
     */
    static std::optional<std::string> FindPairError(const PredictionSample& primary,
                                                    const PredictionSample& secondary)
    {
        return TemporalT2DistributionPredictor::FindPairError(primary, secondary);
    }

    /**
     * Exercise the production direct feature-construction path.
     *
     * @param primaryFeatures Frozen primary feature vector.
     * @param current Current secondary sample.
     * @param currentReport Current secondary delayed report.
     * @param sourceFrameIds Exact lag source identities.
     * @param reports Exact lag reports.
     * @return Composed feature vector.
     */
    static TemporalT2DistributionPredictor::FeatureArray BuildDirect(
        const TemporalT2ValuePredictor::FeatureArray& primaryFeatures,
        const PredictionSample& current,
        const PredictionPollingReport& currentReport,
        const std::array<uint64_t, 3>& sourceFrameIds,
        const std::array<PredictionPollingReport, 3>& reports)
    {
        std::array<TemporalT2DistributionPredictor::LaggedSecondaryInput, 3> lagged;
        for (std::size_t index = 0; index < lagged.size(); ++index)
        {
            lagged[index] = {TemporalT2DistributionPredictor::EXACT_LAGS[index],
                             sourceFrameIds[index],
                             &reports[index]};
        }
        return TemporalT2DistributionPredictor::BuildFeatures(primaryFeatures,
                                                               current,
                                                               currentReport,
                                                               lagged);
    }
};

namespace
{

constexpr uint64_t START_NS = 900000000;
constexpr uint64_t FRAME_SPACING_NS = 34000000;
constexpr std::array<uint64_t, 3> WINDOWS_US{1000, 5000, 20000};

PredictionRollingSample
MakeRolling(uint64_t frameId, std::size_t windowIndex, bool secondary)
{
    PredictionRollingSample rolling;
    rolling.windowUs = WINDOWS_US[windowIndex];
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
    const double adjustment = secondary ? frameId * 0.001 + windowIndex * 0.002 : 0.0;
    rolling.phyTxFraction = 0.1 + adjustment;
    rolling.phyRxFraction = 0.2;
    rolling.phyBusyFraction = 0.3 - adjustment / 2.0;
    rolling.phyOtherFraction = 0.1;
    rolling.phyIdleFraction =
        1.0 - *rolling.phyTxFraction - *rolling.phyRxFraction -
        *rolling.phyBusyFraction - *rolling.phyOtherFraction;
    rolling.historyCoverageUs = rolling.windowUs;
    return rolling;
}

PredictionPollingReport
MakeReport(uint64_t frameId, bool secondary)
{
    const uint64_t generationNs = START_NS + frameId * FRAME_SPACING_NS;
    PredictionPollingReport report;
    report.captureTimeNs = generationNs + 1000000;
    report.availableTimeNs = report.captureTimeNs + 1000000;
    report.latestFeatureEventTimeNs = report.captureTimeNs - 500;
    report.latestFeatureEventSequence = (secondary ? 1000 : 100) + frameId;
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
    report.frequencyBand = secondary ? "2.4GHz" : "5GHz";
    report.centerFrequencyMhz = secondary ? 2437.0 : 5180.0;
    report.rolling = {MakeRolling(frameId, 0, secondary),
                      MakeRolling(frameId, 1, secondary),
                      MakeRolling(frameId, 2, secondary)};
    report.featureSupportMask = "0x3ffffffffdffff";
    return report;
}

PredictionSample
MakePrimary(uint64_t frameId,
            bool actionable = true,
            FrameType frameType = FrameType::P_FRAME)
{
    constexpr uint32_t packetCount = 4;
    constexpr uint32_t frameSize = 4000;
    const uint64_t generationNs = START_NS + frameId * FRAME_SPACING_NS;
    PredictionSample sample;
    sample.runId = "temporal-t2-distribution-predictor-test";
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
    sample.frameMacServiceBytesPendingPrimary =
        sample.frameMacServiceBytesNotAcknowledged;
    sample.rolling = {MakeRolling(frameId + 1000, 0, false),
                      MakeRolling(frameId + 1000, 1, false),
                      MakeRolling(frameId + 1000, 2, false)};
    sample.featureSupportMask = "0x3ffffffffdffff";
    sample.pollingReport = MakeReport(frameId, false);
    return sample;
}

PredictionSample
MakeSecondary(const PredictionSample& primary)
{
    PredictionSample secondary = primary;
    const uint64_t frameId = primary.key.frameId;
    secondary.key = {frameId, 0, 1};
    secondary.latestFeatureEventTimeNs = secondary.sampleTimeNs - 50;
    secondary.latestFeatureEventSequence = 1200 + frameId;
    secondary.senderMacComplete = false;
    secondary.actionable = true;
    secondary.packetsSubmitted = 0;
    secondary.applicationSocketPacketBytesSubmitted = 0;
    secondary.packetsRemainingToSubmit = secondary.framePacketCount;
    secondary.framePacketsMacEnqueued = 0;
    secondary.framePacketsMacDequeued = 0;
    secondary.framePacketsTxSucceeded = 0;
    secondary.frameMpduAttemptFailures = 0;
    secondary.framePacketsTerminallyDropped = 0;
    secondary.framePacketsCurrentlyQueued = 0;
    secondary.frameMacServiceBytesCurrentlyQueued = 0;
    secondary.macQueuePackets = 100 + frameId;
    secondary.macQueueServiceBytes = 10000 + frameId * 100;
    secondary.pollingReport = MakeReport(frameId, true);
    return secondary;
}

bool
HasSecondaryError(const TemporalT2DistributionPredictor& predictor,
                  const PredictionSample& secondary)
{
    return TemporalT2DistributionPredictorTestAccess::FindSecondaryError(predictor,
                                                                          secondary)
        .has_value();
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

uint32_t
FloatWord(double value)
{
    return std::bit_cast<uint32_t>(static_cast<float>(value));
}

} // namespace

/**
 * @ingroup tests
 * Verify paired ownership, exact lags, feature order, and evaluator parity.
 */
class TemporalT2DistributionPredictorHistoryTestCase : public TestCase
{
  public:
    /** Constructor. */
    TemporalT2DistributionPredictorHistoryTestCase()
        : TestCase("Distributional T2 predictor composes exact paired history")
    {
    }

  private:
    void DoRun() override
    {
        NS_TEST_ASSERT_MSG_EQ(TemporalT2DistributionPredictor::HasExactModelContract(),
                              true,
                              "Frozen distributional adapter contract differs");
        NS_TEST_ASSERT_MSG_EQ(TemporalT2DistributionPredictor::GetFeatureNames().size(),
                              308,
                              "Distributional feature-name width differs");
        NS_TEST_ASSERT_MSG_EQ(
            TemporalT2DistributionPredictor::PassesFrameGate(FrameType::P_FRAME),
            true,
            "P frame did not pass the distributional caller gate");
        NS_TEST_ASSERT_MSG_EQ(
            TemporalT2DistributionPredictor::PassesFrameGate(FrameType::I_FRAME),
            false,
            "I frame passed the distributional caller gate");

        TemporalT2DistributionPredictor predictor;
        TemporalT2ValuePredictor primaryReference;
        for (uint64_t frameId = 0; frameId <= 12; ++frameId)
        {
            const bool actionable = frameId != 10;
            const FrameType frameType =
                frameId == 9 ? FrameType::I_FRAME : FrameType::P_FRAME;
            auto primary = MakePrimary(frameId, actionable, frameType);
            auto secondary = MakeSecondary(primary);
            primaryReference.ObservePrimary(primary);
            const auto evidence = predictor.ObservePair(primary, secondary);
            NS_TEST_ASSERT_MSG_EQ(evidence.ready,
                                  frameId >= 8,
                                  "Paired readiness differs at frame " << frameId);
            NS_TEST_ASSERT_MSG_EQ(evidence.primary.ready,
                                  frameId >= 8,
                                  "Primary readiness differs at frame " << frameId);
            for (std::size_t index = 0; index < evidence.secondaryLags.size(); ++index)
            {
                const uint64_t lag = std::array<uint64_t, 3>{1, 3, 8}[index];
                NS_TEST_ASSERT_MSG_EQ(evidence.secondaryLags[index].lagFrames,
                                      lag,
                                      "Secondary lag order differs");
                NS_TEST_ASSERT_MSG_EQ(evidence.secondaryLags[index].frameId.has_value(),
                                      frameId >= lag,
                                      "Secondary lag presence differs");
                if (frameId >= lag)
                {
                    NS_TEST_ASSERT_MSG_EQ(*evidence.secondaryLags[index].frameId,
                                          frameId - lag,
                                          "Secondary lag identity differs");
                    NS_TEST_ASSERT_MSG_EQ(
                        *evidence.secondaryLags[index].pollCaptureTimeNs,
                        MakeReport(frameId - lag, true).captureTimeNs,
                        "Secondary lag capture differs");
                }
            }
            if (frameId == 12)
            {
                secondary.macQueuePackets = 999999;
                secondary.pollingReport->rolling[0].phyTxFraction = 0.9;
            }
        }

        const auto primaryFeatures = primaryReference.GetFeatureArray(12);
        const auto features = predictor.GetFeatureArray(12);
        for (std::size_t index = 0; index < primaryFeatures.size(); ++index)
        {
            if (std::isnan(primaryFeatures[index]))
            {
                NS_TEST_ASSERT_MSG_EQ(std::isnan(features[index]),
                                      true,
                                      "Primary NaN changed at feature " << index);
            }
            else
            {
                NS_TEST_ASSERT_MSG_EQ(FloatWord(features[index]),
                                      FloatWord(primaryFeatures[index]),
                                      "Primary word changed at feature " << index);
            }
        }
        NS_TEST_ASSERT_MSG_EQ(FloatWord(features[246]),
                              FloatWord(112.0),
                              "Owned secondary queue-packet feature differs");
        NS_TEST_ASSERT_MSG_EQ(FloatWord(features[247]),
                              FloatWord(11200.0),
                              "Owned secondary queue-byte feature differs");

        constexpr std::array<uint64_t, 4> expectedFrames{12, 11, 9, 4};
        constexpr std::array<std::size_t, 4> offsets{248, 263, 278, 293};
        for (std::size_t group = 0; group < expectedFrames.size(); ++group)
        {
            const auto report = MakeReport(expectedFrames[group], true);
            std::size_t featureIndex = offsets[group];
            for (const auto& rolling : report.rolling)
            {
                const std::array<double, 5> expected{
                    *rolling.phyTxFraction,
                    *rolling.phyRxFraction,
                    *rolling.phyBusyFraction,
                    *rolling.phyIdleFraction,
                    *rolling.phyOtherFraction,
                };
                for (const double value : expected)
                {
                    NS_TEST_ASSERT_MSG_EQ(FloatWord(features[featureIndex]),
                                          FloatWord(value),
                                          "Secondary fraction word differs at feature "
                                              << featureIndex);
                    ++featureIndex;
                }
            }
        }

        const auto predicted = predictor.Evaluate(12);
        const auto direct = TemporalT2DistributionModelEvaluator::Evaluate(features);
        for (std::size_t index = 0; index < predicted.controlCdf.size(); ++index)
        {
            NS_TEST_ASSERT_MSG_EQ_TOL(predicted.controlCdf[index],
                                      direct.controlCdf[index],
                                      0.0,
                                      "CONTROL CDF differs at element " << index);
            NS_TEST_ASSERT_MSG_EQ_TOL(predicted.fullCopyCdf[index],
                                      direct.fullCopyCdf[index],
                                      0.0,
                                      "Full-copy CDF differs at element " << index);
        }
        NS_TEST_ASSERT_MSG_EQ_TOL(predicted.deadlineRescueReward,
                                  direct.deadlineRescueReward,
                                  0.0,
                                  "Deadline-rescue reward differs");
        NS_TEST_ASSERT_MSG_EQ_TOL(predicted.tail18CdfGain,
                                  direct.tail18CdfGain,
                                  0.0,
                                  "Tail18 benefit differs");
        NS_TEST_ASSERT_MSG_EQ(
            ThrowsInvalidArgument([&]() { predictor.GetFeatureArray(11); }),
            true,
            "Distributional adapter accepted a non-latest frame");
    }
};

/**
 * @ingroup tests
 * Verify fail-closed secondary, pair, and direct-lag validation.
 */
class TemporalT2DistributionPredictorValidationTestCase : public TestCase
{
  public:
    /** Constructor. */
    TemporalT2DistributionPredictorValidationTestCase()
        : TestCase("Distributional T2 predictor rejects malformed paired evidence")
    {
    }

  private:
    void DoRun() override
    {
        TemporalT2DistributionPredictor predictor;
        const auto primary0 = MakePrimary(0);
        const auto secondary0 = MakeSecondary(primary0);
        NS_TEST_ASSERT_MSG_EQ(HasSecondaryError(predictor, secondary0),
                              false,
                              "Valid secondary endpoint was rejected");
        NS_TEST_ASSERT_MSG_EQ(
            TemporalT2DistributionPredictorTestAccess::FindPairError(primary0, secondary0)
                .has_value(),
            false,
            "Valid endpoint pair was rejected");

        auto invalid = secondary0;
        invalid.featureSupportMask = "0x0";
        NS_TEST_ASSERT_MSG_EQ(HasSecondaryError(predictor, invalid),
                              true,
                              "Wrong secondary support mask was accepted");
        invalid = secondary0;
        invalid.packetsSubmitted = 1;
        NS_TEST_ASSERT_MSG_EQ(
            TemporalT2DistributionPredictorTestAccess::FindPairError(primary0, invalid)
                .has_value(),
            true,
            "Secondary current-frame progress was accepted");
        invalid = secondary0;
        invalid.pollingReport->captureTimeNs += 1;
        invalid.pollingReport->availableTimeNs += 1;
        NS_TEST_ASSERT_MSG_EQ(HasSecondaryError(predictor, invalid),
                              true,
                              "Off-cadence secondary report was accepted");
        invalid = secondary0;
        invalid.pollingReport->rolling[0].phyTxFraction = std::nullopt;
        NS_TEST_ASSERT_MSG_EQ(HasSecondaryError(predictor, invalid),
                              true,
                              "Missing secondary fraction was accepted");
        invalid = secondary0;
        invalid.pollingReport->rolling[0].phyTxFraction = 0.9;
        NS_TEST_ASSERT_MSG_EQ(HasSecondaryError(predictor, invalid),
                              true,
                              "Bad secondary fraction sum was accepted");
        invalid = secondary0;
        invalid.pollingReport->rolling[0].windowUs = 5000;
        NS_TEST_ASSERT_MSG_EQ(HasSecondaryError(predictor, invalid),
                              true,
                              "Wrong secondary rolling order was accepted");
        invalid = secondary0;
        invalid.pollingReport->captureTimeNs -= 1000000;
        invalid.pollingReport->availableTimeNs -= 1000000;
        NS_TEST_ASSERT_MSG_EQ(
            TemporalT2DistributionPredictorTestAccess::FindPairError(primary0, invalid)
                .has_value(),
            true,
            "Mismatched paired polling time was accepted");

        predictor.ObservePair(primary0, secondary0);
        const auto primary1 = MakePrimary(1);
        const auto secondary1 = MakeSecondary(primary1);
        invalid = secondary1;
        invalid.key.frameId = 2;
        NS_TEST_ASSERT_MSG_EQ(HasSecondaryError(predictor, invalid),
                              true,
                              "Secondary frame gap was accepted");
        invalid = secondary1;
        invalid.runId = "different-run";
        NS_TEST_ASSERT_MSG_EQ(HasSecondaryError(predictor, invalid),
                              true,
                              "Secondary run drift was accepted");
        invalid = secondary1;
        invalid.latestFeatureEventSequence = 1;
        NS_TEST_ASSERT_MSG_EQ(HasSecondaryError(predictor, invalid),
                              true,
                              "Secondary live sequence reset was accepted");
        invalid = secondary1;
        invalid.pollingReport->latestFeatureEventSequence = 1;
        NS_TEST_ASSERT_MSG_EQ(HasSecondaryError(predictor, invalid),
                              true,
                              "Secondary polling sequence reset was accepted");

        const auto current = MakeSecondary(MakePrimary(8));
        TemporalT2ValuePredictor::FeatureArray primaryFeatures{};
        const std::array<uint64_t, 3> sourceIds{7, 5, 0};
        const std::array<PredictionPollingReport, 3> reports{
            MakeReport(7, true), MakeReport(5, true), MakeReport(0, true)};
        NS_TEST_ASSERT_MSG_EQ(
            ThrowsInvalidArgument([&]() {
                auto wrong = sourceIds;
                wrong[1] = 4;
                TemporalT2DistributionPredictorTestAccess::BuildDirect(
                    primaryFeatures,
                    current,
                    *current.pollingReport,
                    wrong,
                    reports);
            }),
            true,
            "Wrong secondary exact-lag identity was accepted");
    }
};

/**
 * @ingroup tests
 * Test suite for the paired distributional temporal-T2 adapter.
 */
class TemporalT2DistributionPredictorTestSuite : public TestSuite
{
  public:
    /** Constructor. */
    TemporalT2DistributionPredictorTestSuite()
        : TestSuite("wifi-streaming-temporal-t2-distribution-predictor", Type::UNIT)
    {
        AddTestCase(new TemporalT2DistributionPredictorHistoryTestCase, Duration::QUICK);
        AddTestCase(new TemporalT2DistributionPredictorValidationTestCase, Duration::QUICK);
    }
};

static TemporalT2DistributionPredictorTestSuite
    g_temporalT2DistributionPredictorTestSuite;

} // namespace ns3
