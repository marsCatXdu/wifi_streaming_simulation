/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "ns3/closed-loop-risk-predictor.h"
#include "ns3/prediction-model-evaluator.h"
#include "ns3/primary-tail-t4-model-evaluator.h"
#include "ns3/test.h"

#include "primary-tail-t4-model-golden-v1.h"

#include <array>
#include <cmath>
#include <limits>
#include <optional>
#include <stdexcept>
#include <string>

namespace ns3
{

/**
 * @internal
 * Test-only access to the closed-loop feature adapter.
 * @endinternal
 */
class ClosedLoopRiskPredictorTestAccess
{
  public:
    /**
     * Build the exact raw runtime feature vector.
     *
     * @param sample Frame-aligned sample.
     * @param report Selected causal polling report, or null when unavailable.
     * @return Float32-quantized model input.
     */
    static std::array<double, 101> BuildFeatures(const PredictionSample& sample,
                                                 const PredictionPollingReport* report)
    {
        return ClosedLoopRiskPredictor::BuildFeatures(sample, report);
    }
};

namespace
{

/**
 * Construct a rolling window with distinct integral and fractional sentinels.
 *
 * @param windowUs Rolling-window duration.
 * @param base Integral sentinel base.
 * @param fractionBase Fractional sentinel base.
 * @return Populated rolling sample.
 */
PredictionRollingSample
MakeRollingSample(uint64_t windowUs, uint64_t base, double fractionBase)
{
    PredictionRollingSample sample;
    sample.windowUs = windowUs;
    sample.mpduAttempts = base + 1;
    sample.mpduPositiveAcks = base + 2;
    sample.mpduAttemptFailures = base + 3;
    sample.mpduRetries = base + 4;
    sample.mpduRetryRatio = fractionBase + 0.00125;
    sample.acknowledgedMacServiceBytes = base * 100 + 5;
    sample.mpduQueueToAckMeanUs = static_cast<double>(base) + 0.125;
    sample.mpduQueueToAckP95Us = static_cast<double>(base) + 0.25;
    sample.mpduFirstAttemptToAckMeanUs = static_cast<double>(base) + 0.375;
    sample.mpduFirstAttemptToAckP95Us = static_cast<double>(base) + 0.625;
    sample.phyTxTimeUs = static_cast<double>(base) + 0.75;
    sample.phyRxTimeUs = static_cast<double>(base) + 0.875;
    sample.phyBusyTimeUs = static_cast<double>(base) + 0.3125;
    sample.phyIdleTimeUs = static_cast<double>(base) + 0.4375;
    sample.phyOtherTimeUs = static_cast<double>(base) + 0.5625;
    sample.phyTxFraction = fractionBase + 0.01125;
    sample.phyRxFraction = fractionBase + 0.02125;
    sample.phyBusyFraction = fractionBase + 0.03125;
    sample.phyIdleFraction = fractionBase + 0.04125;
    sample.phyOtherFraction = fractionBase + 0.05125;
    sample.historyCoverageUs = static_cast<double>(windowUs) - 0.125;
    return sample;
}

/** Quantize one raw runtime value exactly as the Python float32 matrix. */
double
QuantizeFloat32(double value)
{
    return static_cast<double>(static_cast<float>(value));
}

/**
 * @ingroup tests
 * Verify C++ parity with deterministic sklearn vectors and frozen provenance.
 */
class PrimaryTailT4ModelParityTestCase : public TestCase
{
  public:
    /** Constructor. */
    PrimaryTailT4ModelParityTestCase()
        : TestCase("Compiled two-head T4 model matches sklearn")
    {
    }

  private:
    void
    DoRun() override
    {
        using namespace primary_tail_t4_model_golden_v1;
        const auto& provenance = PrimaryTailT4ModelEvaluator::GetProvenance();
        NS_TEST_ASSERT_MSG_EQ(provenance.artifactId,
                              g_provenance.artifactId,
                              "Compiled artifact ID differs from its golden input");
        NS_TEST_ASSERT_MSG_EQ(provenance.modelId,
                              g_provenance.modelId,
                              "Compiled model ID differs from its golden input");
        NS_TEST_ASSERT_MSG_EQ(provenance.evidenceStatus,
                              g_provenance.evidenceStatus,
                              "Compiled evidence status differs from its golden input");
        NS_TEST_ASSERT_MSG_EQ(provenance.pipelineId,
                              g_provenance.pipelineId,
                              "Compiled pipeline ID differs from its golden input");
        NS_TEST_ASSERT_MSG_EQ(provenance.stage,
                              g_provenance.stage,
                              "Compiled decision stage differs from its golden input");
        NS_TEST_ASSERT_MSG_EQ(provenance.featureSet,
                              g_provenance.featureSet,
                              "Compiled feature-set ID differs from its golden input");
        NS_TEST_ASSERT_MSG_EQ(provenance.degradationProfile,
                              g_provenance.degradationProfile,
                              "Compiled degradation profile differs from its golden input");
        NS_TEST_ASSERT_MSG_EQ(provenance.primaryMissTargetId,
                              g_provenance.primaryMissTargetId,
                              "Compiled miss target differs from its golden input");
        NS_TEST_ASSERT_MSG_EQ(provenance.completedTailTargetId,
                              g_provenance.completedTailTargetId,
                              "Compiled tail target differs from its golden input");
        NS_TEST_ASSERT_MSG_EQ(provenance.sourceModelSha256,
                              g_provenance.sourceModelSha256,
                              "Compiled source digest differs from its golden input");
        NS_TEST_ASSERT_MSG_EQ(provenance.datasetSha256,
                              g_provenance.datasetSha256,
                              "Compiled dataset digest differs from its golden input");
        NS_TEST_ASSERT_MSG_EQ(provenance.datasetManifestSha256,
                              g_provenance.datasetManifestSha256,
                              "Compiled dataset-manifest digest differs from its golden input");
        NS_TEST_ASSERT_MSG_EQ(provenance.datasetValidationSha256,
                              g_provenance.datasetValidationSha256,
                              "Compiled dataset-validation digest differs from its golden input");
        NS_TEST_ASSERT_MSG_EQ(provenance.exportSha256,
                              g_provenance.exportSha256,
                              "Compiled export digest differs from its golden input");
        NS_TEST_ASSERT_MSG_EQ(provenance.targetProvenanceSha256,
                              g_provenance.targetProvenanceSha256,
                              "Compiled target digest differs from its golden input");
        NS_TEST_ASSERT_MSG_EQ(provenance.featureContractSha256,
                              g_provenance.featureContractSha256,
                              "Compiled feature digest differs from its golden input");
        NS_TEST_ASSERT_MSG_EQ(provenance.combinerSha256,
                              g_provenance.combinerSha256,
                              "Compiled combiner digest differs from its golden input");
        NS_TEST_ASSERT_MSG_EQ(provenance.primaryMissModelSha256,
                              g_provenance.primaryMissModelSha256,
                              "Compiled miss model digest differs from its golden input");
        NS_TEST_ASSERT_MSG_EQ(provenance.completedTailModelSha256,
                              g_provenance.completedTailModelSha256,
                              "Compiled tail model digest differs from its golden input");

        const auto featureNames = PrimaryTailT4ModelEvaluator::GetFeatureNames();
        const auto physicalNames = PrimaryTailT4ModelEvaluator::GetPhysicalFeatureNames();
        NS_TEST_ASSERT_MSG_EQ(featureNames.size(),
                              101,
                              "Compiled T4 model does not have 101 logical features");
        NS_TEST_ASSERT_MSG_EQ(physicalNames.size(),
                              featureNames.size(),
                              "Logical and physical T4 feature counts differ");
        for (std::size_t i = 0; i < featureNames.size(); ++i)
        {
            NS_TEST_ASSERT_MSG_EQ(featureNames[i],
                                  g_featureNames[i],
                                  "Compiled logical feature order differs from sklearn");
            NS_TEST_ASSERT_MSG_EQ(physicalNames[i],
                                  g_physicalFeatureNames[i],
                                  "Compiled physical feature order differs from sklearn");
        }

        NS_TEST_ASSERT_MSG_EQ(PrimaryTailT4ModelEvaluator::GetTailThresholdUs(),
                              g_tailThresholdUs,
                              "Compiled tail threshold differs from its golden input");
        NS_TEST_ASSERT_MSG_EQ(PrimaryTailT4ModelEvaluator::GetScoreName(),
                              g_scoreName,
                              "Compiled score name differs from its golden input");
        NS_TEST_ASSERT_MSG_EQ(PrimaryTailT4ModelEvaluator::GetScoreKind(),
                              g_scoreKind,
                              "Compiled score kind differs from its golden input");
        NS_TEST_ASSERT_MSG_EQ(PrimaryTailT4ModelEvaluator::GetCombiner(),
                              g_combiner,
                              "Compiled combiner differs from its golden input");
        NS_TEST_ASSERT_MSG_EQ_TOL(PrimaryTailT4ModelEvaluator::GetPrimaryMissWeight(),
                                  g_primaryMissWeight,
                                  0.0,
                                  "Compiled miss weight differs from its golden input");
        NS_TEST_ASSERT_MSG_EQ_TOL(PrimaryTailT4ModelEvaluator::GetCompletedTailWeight(),
                                  g_completedTailWeight,
                                  0.0,
                                  "Compiled tail weight differs from its golden input");
        NS_TEST_ASSERT_MSG_EQ_TOL(PrimaryTailT4ModelEvaluator::GetScoreNormalization(),
                                  g_scoreNormalization,
                                  0.0,
                                  "Compiled score normalization differs from its golden input");

        for (const auto& golden : g_cases)
        {
            const auto result = PrimaryTailT4ModelEvaluator::Evaluate(golden.features);
            NS_TEST_ASSERT_MSG_EQ_TOL(result.primaryMissRankingScore,
                                      golden.primaryMissRankingScore,
                                      1e-11,
                                      "Compiled miss-head score differs from sklearn");
            NS_TEST_ASSERT_MSG_EQ_TOL(result.primaryMissProbability,
                                      golden.primaryMissProbability,
                                      1e-12,
                                      "Compiled miss-head probability differs from sklearn");
            NS_TEST_ASSERT_MSG_EQ_TOL(result.completedTailRankingScore,
                                      golden.completedTailRankingScore,
                                      1e-11,
                                      "Compiled tail-head score differs from sklearn");
            NS_TEST_ASSERT_MSG_EQ_TOL(result.completedTailProbability,
                                      golden.completedTailProbability,
                                      1e-12,
                                      "Compiled tail-head probability differs from sklearn");
            NS_TEST_ASSERT_MSG_EQ_TOL(result.admissionScore,
                                      golden.admissionScore,
                                      1e-12,
                                      "Compiled admission score differs from sklearn");
            const double recombined =
                (result.primaryMissProbability + 0.2 * result.completedTailProbability) / 1.2;
            NS_TEST_ASSERT_MSG_EQ_TOL(result.admissionScore,
                                      recombined,
                                      1e-15,
                                      "Admission score does not use the frozen formula");
            NS_TEST_ASSERT_MSG_EQ(result.admissionScore >= 0.0 && result.admissionScore <= 1.0,
                                  true,
                                  "Admission score is outside its documented bounds");
        }

        bool rejectedWrongWidth = false;
        try
        {
            const std::array<double, 1> wrongWidth{0.0};
            PrimaryTailT4ModelEvaluator::Evaluate(wrongWidth);
        }
        catch (const std::invalid_argument&)
        {
            rejectedWrongWidth = true;
        }
        NS_TEST_ASSERT_MSG_EQ(rejectedWrongWidth, true, "Wrong-width model input was accepted");

        bool rejectedInfinity = false;
        try
        {
            std::array<double, 101> infinite{};
            infinite[37] = std::numeric_limits<double>::infinity();
            PrimaryTailT4ModelEvaluator::Evaluate(infinite);
        }
        catch (const std::invalid_argument&)
        {
            rejectedInfinity = true;
        }
        NS_TEST_ASSERT_MSG_EQ(rejectedInfinity, true, "Infinite model input was accepted");
    }
};

/**
 * @ingroup tests
 * Verify truthful staged scores and every allocation-free runtime feature.
 */
class ClosedLoopStagedRiskTestCase : public TestCase
{
  public:
    /** Constructor. */
    ClosedLoopStagedRiskTestCase()
        : TestCase("Closed-loop predictor dispatches truthful T0 and T4 scores")
    {
    }

  private:
    void
    DoRun() override
    {
        NS_TEST_ASSERT_MSG_EQ(ClosedLoopRiskPredictor::HasExactStagedModelContract(),
                              true,
                              "Exact T0/T4 model contract is unavailable");
        const auto& t0Identity = ClosedLoopRiskPredictor::GetModelIdentity(0);
        const auto& t4Identity = ClosedLoopRiskPredictor::GetModelIdentity(4000);
        NS_TEST_ASSERT_MSG_EQ(
            t0Identity.scoreKind ==
                ClosedLoopRiskScoreKind::CALIBRATED_PRIMARY_MISS_PROBABILITY,
            true,
            "T0 score kind is not a calibrated miss probability");
        NS_TEST_ASSERT_MSG_EQ(
            t4Identity.scoreKind ==
                ClosedLoopRiskScoreKind::WEIGHTED_HEAD_PROBABILITY_ADMISSION_SCORE,
            true,
            "T4 score kind falsely claims to be one probability");
        NS_TEST_ASSERT_MSG_EQ(
            ClosedLoopRiskPredictor::GetScoreKindName(t0Identity.scoreKind),
            "calibrated_primary_miss_probability",
            "T0 score-kind schema name changed");
        NS_TEST_ASSERT_MSG_EQ(
            ClosedLoopRiskPredictor::GetScoreKindName(t4Identity.scoreKind),
            PrimaryTailT4ModelEvaluator::GetScoreKind(),
            "T4 score-kind schema name differs from the compiled model");

        const auto w1 = MakeRollingSample(1000, 100, 0.1);
        const auto w20 = MakeRollingSample(20000, 200, 0.2);
        const auto w5 = MakeRollingSample(5000, 300, 0.3);
        PredictionPollingReport report;
        report.captureTimeNs = 50000999;
        report.availableTimeNs = 51000999;
        report.mpduTxAttemptsTotal = 1001;
        report.mpduPositiveAcksTotal = 1002;
        report.mpduTxAttemptFailuresTotal = 1003;
        report.mpduRetriesTotal = 1004;
        report.mpduTerminalDropsTotal = 1005;
        report.mpduRetryLimitDropsTotal = 1006;
        report.mpduLifetimeDropsTotal = 1007;
        report.mpduQueueDropsTotal = 1008;
        report.ppduTxCountTotal = 1009;
        report.lastTxAttemptTimeNs = 49877543;
        report.lastPositiveAckTimeNs = 49211988;
        report.currentMcs = 7;
        report.currentNss = 2;
        report.currentChannelWidthMhz = 80;
        report.currentGuardIntervalNs = 800;
        report.frequencyBand = "5GHz";
        report.centerFrequencyMhz = 5180.625;
        report.currentAckSignalDbm = -47.375;
        report.rolling = {w20, w5, w1};

        PredictionSample sample;
        sample.applicationSocketPacketBytesSubmitted = 40803;
        sample.deadlineSlackUs = 29333;
        sample.frameAgeUs = 4000;
        sample.framePacketCount = 37;
        sample.frameSizeBytes = 44400;
        sample.frameType = FrameType::B_FRAME;
        sample.packetsRemainingToSubmit = 3;
        sample.packetsSubmitted = 34;
        sample.sampleTimeNs = 51000777;
        sample.framePacketsMacEnqueued = 31;
        sample.framePacketsMacDequeued = 29;
        sample.framePacketsTxSucceeded = 23;
        sample.frameMpduAttemptFailures = 17;
        sample.framePacketsTerminallyDropped = 3;
        sample.framePacketsCurrentlyQueued = 5;
        sample.frameMacServiceBytesCurrentlyQueued = 6123;
        sample.macQueuePackets = 41;
        sample.macQueueServiceBytes = 49201;
        sample.macQueueOldestEnqueueTimeNs = 50345346;
        sample.packetsAheadOfFrame = 9;
        sample.macServiceBytesAheadOfFrame = 10807;
        sample.framePacketsPendingPrimary = 14;
        sample.frameMacServiceBytesNotAcknowledged = 16809;
        sample.frameMacServiceBytesPendingPrimary = 15611;

        const std::array<double, 101> expectedRaw{{
            40803.0,
            29333.0,
            4000.0,
            37.0,
            44400.0,
            2.0,
            3.0,
            34.0,
            static_cast<double>(w1.acknowledgedMacServiceBytes),
            static_cast<double>(w20.acknowledgedMacServiceBytes),
            static_cast<double>(w5.acknowledgedMacServiceBytes),
            *report.centerFrequencyMhz,
            *report.currentAckSignalDbm,
            static_cast<double>(*report.currentChannelWidthMhz),
            static_cast<double>(*report.currentGuardIntervalNs),
            static_cast<double>(*report.currentMcs),
            static_cast<double>(*report.currentNss),
            1.0,
            123.456,
            789.011,
            static_cast<double>(w1.mpduAttemptFailures),
            static_cast<double>(w20.mpduAttemptFailures),
            static_cast<double>(w5.mpduAttemptFailures),
            static_cast<double>(w1.mpduAttempts),
            static_cast<double>(w20.mpduAttempts),
            static_cast<double>(w5.mpduAttempts),
            *w1.mpduFirstAttemptToAckMeanUs,
            *w20.mpduFirstAttemptToAckMeanUs,
            *w5.mpduFirstAttemptToAckMeanUs,
            *w1.mpduFirstAttemptToAckP95Us,
            *w20.mpduFirstAttemptToAckP95Us,
            *w5.mpduFirstAttemptToAckP95Us,
            static_cast<double>(*report.mpduLifetimeDropsTotal),
            static_cast<double>(w1.mpduPositiveAcks),
            static_cast<double>(w20.mpduPositiveAcks),
            static_cast<double>(w5.mpduPositiveAcks),
            static_cast<double>(*report.mpduPositiveAcksTotal),
            static_cast<double>(*report.mpduQueueDropsTotal),
            *w1.mpduQueueToAckMeanUs,
            *w20.mpduQueueToAckMeanUs,
            *w5.mpduQueueToAckMeanUs,
            *w1.mpduQueueToAckP95Us,
            *w20.mpduQueueToAckP95Us,
            *w5.mpduQueueToAckP95Us,
            static_cast<double>(w1.mpduRetries),
            static_cast<double>(w20.mpduRetries),
            static_cast<double>(w5.mpduRetries),
            static_cast<double>(*report.mpduRetriesTotal),
            static_cast<double>(*report.mpduRetryLimitDropsTotal),
            *w1.mpduRetryRatio,
            *w20.mpduRetryRatio,
            *w5.mpduRetryRatio,
            static_cast<double>(*report.mpduTerminalDropsTotal),
            static_cast<double>(*report.mpduTxAttemptFailuresTotal),
            static_cast<double>(*report.mpduTxAttemptsTotal),
            *w1.phyBusyFraction,
            *w20.phyBusyFraction,
            *w5.phyBusyFraction,
            w1.phyBusyTimeUs,
            w20.phyBusyTimeUs,
            w5.phyBusyTimeUs,
            *w1.phyIdleFraction,
            *w20.phyIdleFraction,
            *w5.phyIdleFraction,
            w1.phyIdleTimeUs,
            w20.phyIdleTimeUs,
            w5.phyIdleTimeUs,
            *w1.phyOtherFraction,
            *w20.phyOtherFraction,
            *w5.phyOtherFraction,
            w1.phyOtherTimeUs,
            w20.phyOtherTimeUs,
            w5.phyOtherTimeUs,
            *w1.phyRxFraction,
            *w20.phyRxFraction,
            *w5.phyRxFraction,
            w1.phyRxTimeUs,
            w20.phyRxTimeUs,
            w5.phyRxTimeUs,
            *w1.phyTxFraction,
            *w20.phyTxFraction,
            *w5.phyTxFraction,
            w1.phyTxTimeUs,
            w20.phyTxTimeUs,
            w5.phyTxTimeUs,
            static_cast<double>(*report.ppduTxCountTotal),
            31.0,
            29.0,
            23.0,
            17.0,
            3.0,
            5.0,
            6123.0,
            41.0,
            49201.0,
            655.431,
            9.0,
            10807.0,
            14.0,
            16809.0,
            15611.0,
        }};
        std::array<double, 101> expectedFeatures;
        for (std::size_t index = 0; index < expectedFeatures.size(); ++index)
        {
            expectedFeatures[index] = QuantizeFloat32(expectedRaw[index]);
        }

        const auto adapted =
            ClosedLoopRiskPredictorTestAccess::BuildFeatures(sample, &report);
        const auto featureNames = PrimaryTailT4ModelEvaluator::GetFeatureNames();
        for (std::size_t index = 0; index < expectedFeatures.size(); ++index)
        {
            if (std::isnan(expectedFeatures[index]))
            {
                NS_TEST_ASSERT_MSG_EQ(std::isnan(adapted[index]),
                                      true,
                                      "Feature " << index << " (" << featureNames[index]
                                                 << ") lost its missing value");
                continue;
            }
            NS_TEST_ASSERT_MSG_EQ_TOL(adapted[index],
                                      expectedFeatures[index],
                                      0.0,
                                      "Feature " << index << " (" << featureNames[index]
                                                 << ") differs from float32 quantization");
        }
        constexpr std::array<std::size_t, 18> fractionalSentinels{
            12, 18, 19, 26, 27, 28, 29, 30, 31, 38, 39, 40, 41, 42, 43, 49, 50, 51,
        };
        for (const auto index : fractionalSentinels)
        {
            NS_TEST_ASSERT_MSG_NE(adapted[index],
                                  std::nearbyint(adapted[index]),
                                  "Continuous feature " << featureNames[index]
                                                        << " was rounded to an integer");
        }

        constexpr std::array<FrameType, 4> frameTypes{
            FrameType::I_FRAME,
            FrameType::P_FRAME,
            FrameType::B_FRAME,
            FrameType::UNKNOWN,
        };
        constexpr std::array<double, 4> encodedFrameTypes{0.0, 1.0, 2.0, -1.0};
        for (std::size_t index = 0; index < frameTypes.size(); ++index)
        {
            auto categorySample = sample;
            categorySample.frameType = frameTypes[index];
            const auto categoryFeatures =
                ClosedLoopRiskPredictorTestAccess::BuildFeatures(categorySample, &report);
            NS_TEST_ASSERT_MSG_EQ_TOL(categoryFeatures[5],
                                      encodedFrameTypes[index],
                                      0.0,
                                      "Frame-type encoding differs from the feature contract");
        }
        const std::array<std::optional<std::string>, 5> frequencyBands{
            "2.4GHz",
            "5GHz",
            "6GHz",
            "unsupported",
            std::nullopt,
        };
        const std::array<double, 5> encodedFrequencyBands{
            0.0,
            1.0,
            2.0,
            -1.0,
            std::numeric_limits<double>::quiet_NaN(),
        };
        for (std::size_t index = 0; index < frequencyBands.size(); ++index)
        {
            auto categoryReport = report;
            categoryReport.frequencyBand = frequencyBands[index];
            const auto categoryFeatures =
                ClosedLoopRiskPredictorTestAccess::BuildFeatures(sample, &categoryReport);
            if (std::isnan(encodedFrequencyBands[index]))
            {
                NS_TEST_ASSERT_MSG_EQ(std::isnan(categoryFeatures[17]),
                                      true,
                                      "Missing frequency band was not encoded as NaN");
            }
            else
            {
                NS_TEST_ASSERT_MSG_EQ_TOL(
                    categoryFeatures[17],
                    encodedFrequencyBands[index],
                    0.0,
                "Frequency-band encoding differs from the feature contract");
            }
        }
        auto missingAgeReport = report;
        missingAgeReport.lastTxAttemptTimeNs.reset();
        const auto missingPollingAge =
            ClosedLoopRiskPredictorTestAccess::BuildFeatures(sample, &missingAgeReport);
        NS_TEST_ASSERT_MSG_EQ(std::isnan(missingPollingAge[18]),
                              true,
                              "Missing polling event time was not encoded as NaN");
        auto missingQueueAgeSample = sample;
        missingQueueAgeSample.macQueueOldestEnqueueTimeNs.reset();
        const auto missingQueueAge =
            ClosedLoopRiskPredictorTestAccess::BuildFeatures(missingQueueAgeSample, &report);
        NS_TEST_ASSERT_MSG_EQ(std::isnan(missingQueueAge[95]),
                              true,
                              "Missing queue event time was not encoded as NaN");

        const auto withoutReport =
            ClosedLoopRiskPredictorTestAccess::BuildFeatures(sample, nullptr);
        for (std::size_t index = 0; index < withoutReport.size(); ++index)
        {
            if (index >= 8 && index < 86)
            {
                NS_TEST_ASSERT_MSG_EQ(std::isnan(withoutReport[index]),
                                      true,
                                      "Absent polling report did not produce missing F1");
            }
            else
            {
                NS_TEST_ASSERT_MSG_EQ_TOL(withoutReport[index],
                                          expectedFeatures[index],
                                          0.0,
                                          "F0/F2 changed when the polling report was absent");
            }
        }

        auto predictor = CreateObject<ClosedLoopRiskPredictor>();
        sample.pollingReport = report;
        sample.sampleOffsetUs = 4000;
        const auto t4Score = predictor->Score(sample);
        const auto expectedT4 = PrimaryTailT4ModelEvaluator::Evaluate(expectedFeatures);
        NS_TEST_ASSERT_MSG_EQ_TOL(t4Score.admissionScore,
                                  expectedT4.admissionScore,
                                  1e-15,
                                  "T4 feature adapter differs from direct model input");
        NS_TEST_ASSERT_MSG_EQ(t4Score.primaryMissProbability.has_value(),
                              true,
                              "T4 score omitted its miss-head probability");
        NS_TEST_ASSERT_MSG_EQ(t4Score.completedTailProbability.has_value(),
                              true,
                              "T4 score omitted its tail-head probability");
        NS_TEST_ASSERT_MSG_EQ_TOL(*t4Score.primaryMissProbability,
                                  expectedT4.primaryMissProbability,
                                  1e-15,
                                  "T4 score reports the wrong miss probability");
        NS_TEST_ASSERT_MSG_EQ_TOL(*t4Score.completedTailProbability,
                                  expectedT4.completedTailProbability,
                                  1e-15,
                                  "T4 score reports the wrong tail probability");

        sample.sampleOffsetUs = 0;
        const auto t0Score = predictor->Score(sample);
        const auto expectedT0 = PredictionModelEvaluator::Evaluate(
            PredictionStage::T0,
            std::span<const double>{expectedFeatures.data(), 86});
        NS_TEST_ASSERT_MSG_EQ_TOL(t0Score.admissionScore,
                                  expectedT0.calibratedProbability,
                                  1e-15,
                                  "T0 feature adapter differs from direct model input");
        NS_TEST_ASSERT_MSG_EQ(t0Score.completedTailProbability.has_value(),
                              false,
                              "T0 score invented a completed-tail probability");
        NS_TEST_ASSERT_MSG_EQ_TOL(predictor->ScorePrimaryMissProbability(sample),
                                  expectedT0.calibratedProbability,
                                  1e-15,
                                  "Probability-only compatibility adapter changed T0");
        predictor->Dispose();
    }
};

/**
 * @ingroup tests
 * Test suite for the compiled two-head T4 model.
 */
class PrimaryTailT4ModelTestSuite : public TestSuite
{
  public:
    /** Constructor. */
    PrimaryTailT4ModelTestSuite()
        : TestSuite("wifi-streaming-primary-tail-t4-model", Type::UNIT)
    {
        AddTestCase(new PrimaryTailT4ModelParityTestCase, TestCase::Duration::QUICK);
        AddTestCase(new ClosedLoopStagedRiskTestCase, TestCase::Duration::QUICK);
    }
};

/** Static test-suite registration. */
static PrimaryTailT4ModelTestSuite g_primaryTailT4ModelTestSuite;

} // namespace
} // namespace ns3
