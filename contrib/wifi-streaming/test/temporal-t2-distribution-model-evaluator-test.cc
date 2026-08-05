/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "ns3/temporal-t2-distribution-model-data-v1.h"
#include "ns3/temporal-t2-distribution-model-evaluator.h"
#include "ns3/test.h"

#include "temporal-t2-distribution-model-goldens-v1.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <string_view>
#include <vector>

namespace ns3
{
namespace
{

using Imputer = temporal_t2_distribution_model_v1::Imputer;

std::vector<double>
Transform(const Imputer& imputer,
          const std::array<double, TemporalT2DistributionModelEvaluator::RAW_FEATURE_COUNT>& raw)
{
    std::array<float, TemporalT2DistributionModelEvaluator::RAW_FEATURE_COUNT> quantized{};
    for (std::size_t index = 0; index < raw.size(); ++index)
    {
        quantized[index] = static_cast<float>(raw[index]);
    }
    std::vector<double> result;
    result.reserve(imputer.medians.size() + imputer.missingIndicatorRawFeatures.size());
    for (std::size_t index = 0; index < quantized.size(); ++index)
    {
        result.push_back(std::isnan(quantized[index]) ? imputer.medians[index]
                                                      : static_cast<double>(quantized[index]));
    }
    for (const auto index : imputer.missingIndicatorRawFeatures)
    {
        result.push_back(std::isnan(quantized[index]) ? 1.0 : 0.0);
    }
    return result;
}

/**
 * @ingroup tests
 * Verify exact Python parity and the frozen distributional runtime contract.
 */
class TemporalT2DistributionModelParityTestCase : public TestCase
{
  public:
    /** Constructor. */
    TemporalT2DistributionModelParityTestCase()
        : TestCase("Compiled distributional temporal T2 model matches Python")
    {
    }

  private:
    /**
     * Compare one fixed model-output array.
     *
     * @tparam N Array width.
     * @param actual Evaluator output.
     * @param expected Frozen Python output.
     * @param tolerance Absolute comparison tolerance.
     * @param context Failure-message prefix.
     */
    template <std::size_t N>
    void
    AssertArrayClose(const std::array<double, N>& actual,
                     const std::array<double, N>& expected,
                     double tolerance,
                     const std::string& context)
    {
        for (std::size_t index = 0; index < N; ++index)
        {
            NS_TEST_ASSERT_MSG_EQ_TOL(actual[index],
                                      expected[index],
                                      tolerance,
                                      context << " element " << index << " differs");
        }
    }

    void
    DoRun() override
    {
        NS_TEST_ASSERT_MSG_EQ(TemporalT2DistributionModelEvaluator::HasExactRuntimeContract(),
                              true,
                              "Compiled distributional runtime contract differs");
        NS_TEST_ASSERT_MSG_EQ(TemporalT2DistributionModelEvaluator::GetFeatureNames().size(),
                              308,
                              "Compiled distributional feature count differs");
        NS_TEST_ASSERT_MSG_EQ(
            TemporalT2DistributionModelEvaluator::GetFeatureFamily(),
            "primary_compact_physics_temporal_plus_passive_secondary",
            "Compiled distributional feature family differs");
        NS_TEST_ASSERT_MSG_EQ(TemporalT2DistributionModelEvaluator::GetModelSpecId(),
                              "hgb64_depth3_7leaf_multiclass_v1",
                              "Compiled distributional model specification differs");
        NS_TEST_ASSERT_MSG_EQ(TemporalT2DistributionModelEvaluator::GetObjective(),
                              "deadline_rescue",
                              "Compiled distributional objective differs");
        NS_TEST_ASSERT_MSG_EQ(TemporalT2DistributionModelEvaluator::GetFrameGate(),
                              "p_frames_only",
                              "Compiled distributional frame gate differs");
        NS_TEST_ASSERT_MSG_EQ_TOL(
            TemporalT2DistributionModelEvaluator::GetCanonicalPFrameReservationUs(),
            1983.760667318285,
            0.0,
            "Compiled canonical reservation differs");
        NS_TEST_ASSERT_MSG_EQ_TOL(
            TemporalT2DistributionModelEvaluator::GetMaximumRepayableCreditUs(),
            372000.0,
            0.0,
            "Compiled maximum repayable credit differs");
        NS_TEST_ASSERT_MSG_EQ(TemporalT2DistributionModelEvaluator::GetTimeBinWidthUs(),
                              5000000,
                              "Compiled time-bin width differs");

        const auto& provenance = TemporalT2DistributionModelEvaluator::GetProvenance();
        const auto& expected =
            temporal_t2_distribution_model_goldens_v1::g_provenance;
        NS_TEST_ASSERT_MSG_EQ(provenance.evidenceStatus,
                              expected.evidenceStatus,
                              "Evidence status differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.runtimeContractId,
                              expected.runtimeContractId,
                              "Runtime-contract ID differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.runtimeContractSha256,
                              expected.runtimeContractSha256,
                              "Runtime-contract hash differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.selectedVariant,
                              expected.selectedVariant,
                              "Selected variant differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.trainingGitCommit,
                              expected.trainingGitCommit,
                              "Training commit differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.sourceModelPickleSha256,
                              expected.sourceModelPickleSha256,
                              "Source pickle differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.sourceModelJsonSha256,
                              expected.sourceModelJsonSha256,
                              "Source model JSON differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.sourceReferenceJsonSha256,
                              expected.sourceReferenceJsonSha256,
                              "Source reference JSON differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.sourceMetricsSha256,
                              expected.sourceMetricsSha256,
                              "Source metrics differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.sourceManifestSha256,
                              expected.sourceManifestSha256,
                              "Source manifest differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.exporterSha256,
                              expected.exporterSha256,
                              "Exporter hash differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.portableModelSha256,
                              expected.portableModelSha256,
                              "Portable-model digest differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.deploymentReferenceSha256,
                              expected.deploymentReferenceSha256,
                              "Reference digest differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.featureContractSha256,
                              expected.featureContractSha256,
                              "Feature-contract digest differs");

        const auto& control =
            temporal_t2_distribution_model_v1::GetControlClassifier();
        const auto& fullCopy =
            temporal_t2_distribution_model_v1::GetFullCopyClassifier();
        for (const auto& golden :
             temporal_t2_distribution_model_goldens_v1::g_modelCases)
        {
            const auto controlTransformed = Transform(control.imputer, golden.features);
            const auto fullCopyTransformed = Transform(fullCopy.imputer, golden.features);
            NS_TEST_ASSERT_MSG_EQ(controlTransformed.size(),
                                  golden.controlTransformed.size(),
                                  golden.label << ": CONTROL transformed width differs");
            NS_TEST_ASSERT_MSG_EQ(fullCopyTransformed.size(),
                                  golden.fullCopyTransformed.size(),
                                  golden.label << ": full-copy transformed width differs");
            for (std::size_t index = 0; index < controlTransformed.size(); ++index)
            {
                NS_TEST_ASSERT_MSG_EQ_TOL(controlTransformed[index],
                                          golden.controlTransformed[index],
                                          0.0,
                                          golden.label
                                              << ": CONTROL transform element " << index
                                              << " differs");
                NS_TEST_ASSERT_MSG_EQ_TOL(fullCopyTransformed[index],
                                          golden.fullCopyTransformed[index],
                                          0.0,
                                          golden.label
                                              << ": full-copy transform element " << index
                                              << " differs");
            }

            const auto result = TemporalT2DistributionModelEvaluator::Evaluate(
                golden.features);
            AssertArrayClose(result.controlLogits,
                             golden.expected.controlLogits,
                             1e-11,
                             std::string(golden.label) + " CONTROL logits");
            AssertArrayClose(result.controlProbabilities,
                             golden.expected.controlProbabilities,
                             1e-12,
                             std::string(golden.label) + " CONTROL probabilities");
            AssertArrayClose(result.controlCdf,
                             golden.expected.controlCdf,
                             1e-12,
                             std::string(golden.label) + " CONTROL CDF");
            AssertArrayClose(result.fullCopyLogits,
                             golden.expected.fullCopyLogits,
                             1e-11,
                             std::string(golden.label) + " full-copy logits");
            AssertArrayClose(result.fullCopyProbabilities,
                             golden.expected.fullCopyProbabilities,
                             1e-12,
                             std::string(golden.label) + " full-copy probabilities");
            AssertArrayClose(result.fullCopyCdf,
                             golden.expected.fullCopyCdf,
                             1e-12,
                             std::string(golden.label) + " full-copy CDF");
            NS_TEST_ASSERT_MSG_EQ_TOL(result.deadlineRescueReward,
                                      golden.expected.deadlineRescueReward,
                                      1e-12,
                                      golden.label << ": deadline reward differs");
            NS_TEST_ASSERT_MSG_EQ_TOL(result.tail18CdfGain,
                                      golden.expected.tail18CdfGain,
                                      1e-12,
                                      golden.label << ": tail18 gain differs");
        }

        for (const auto& golden :
             temporal_t2_distribution_model_goldens_v1::g_referenceCases)
        {
            const auto regime = TemporalT2DistributionModelEvaluator::GetCongestionRegime(
                golden.timeBin, golden.runningBusy);
            NS_TEST_ASSERT_MSG_EQ(regime,
                                  golden.expectedRegime,
                                  golden.label << ": congestion regime differs");
            const double opportunity =
                TemporalT2DistributionModelEvaluator::GetOpportunityCost(
                    golden.timeBin, regime, golden.repayableCreditUs);
            if (std::isinf(golden.expectedOpportunityCost))
            {
                NS_TEST_ASSERT_MSG_EQ(std::isinf(opportunity),
                                      true,
                                      golden.label << ": opportunity cost is not infinite");
            }
            else
            {
                NS_TEST_ASSERT_MSG_EQ_TOL(opportunity,
                                          golden.expectedOpportunityCost,
                                          1e-15,
                                          golden.label << ": opportunity cost differs");
            }
        }

        for (const auto& golden :
             temporal_t2_distribution_model_goldens_v1::g_creditCases)
        {
            const double refilled =
                std::min(360000.0,
                         golden.priorBalanceUs +
                             0.006 * (golden.decisionTimeUs - golden.priorTimeUs));
            const double remaining = 0.006 * (60000000.0 - golden.decisionTimeUs);
            const double repayable = refilled + remaining;
            const bool admitted = golden.reservationUs <= repayable;
            const double after = admitted ? refilled - golden.reservationUs : refilled;
            const double finalBalance = std::min(360000.0, after + remaining);
            NS_TEST_ASSERT_MSG_EQ_TOL(refilled,
                                      golden.expectedRefilledBalanceUs,
                                      1e-9,
                                      golden.label << ": refilled balance differs");
            NS_TEST_ASSERT_MSG_EQ_TOL(remaining,
                                      golden.expectedRemainingRefillUs,
                                      1e-9,
                                      golden.label << ": remaining refill differs");
            NS_TEST_ASSERT_MSG_EQ_TOL(repayable,
                                      golden.expectedRepayableCreditUs,
                                      1e-9,
                                      golden.label << ": repayable credit differs");
            NS_TEST_ASSERT_MSG_EQ(admitted,
                                  golden.expectedAdmitted,
                                  golden.label << ": horizon admission differs");
            NS_TEST_ASSERT_MSG_EQ_TOL(after,
                                      golden.expectedBalanceAfterUs,
                                      1e-9,
                                      golden.label << ": post-debit balance differs");
            NS_TEST_ASSERT_MSG_EQ_TOL(finalBalance,
                                      golden.expectedFinalBalanceUs,
                                      1e-9,
                                      golden.label << ": final balance differs");
        }

        bool rejectedWidth = false;
        try
        {
            const std::array<double, 1> wrongWidth{0.0};
            TemporalT2DistributionModelEvaluator::Evaluate(wrongWidth);
        }
        catch (const std::invalid_argument&)
        {
            rejectedWidth = true;
        }
        NS_TEST_ASSERT_MSG_EQ(rejectedWidth, true, "Wrong feature width was accepted");

        bool rejectedInfinity = false;
        try
        {
            std::array<double, 308> values{};
            values[20] = std::numeric_limits<double>::infinity();
            TemporalT2DistributionModelEvaluator::Evaluate(values);
        }
        catch (const std::invalid_argument&)
        {
            rejectedInfinity = true;
        }
        NS_TEST_ASSERT_MSG_EQ(rejectedInfinity, true, "Infinite feature was accepted");

        bool rejectedCredit = false;
        try
        {
            TemporalT2DistributionModelEvaluator::GetOpportunityCost(0, 0, 372001.0);
        }
        catch (const std::invalid_argument&)
        {
            rejectedCredit = true;
        }
        NS_TEST_ASSERT_MSG_EQ(rejectedCredit, true, "Unreachable credit was accepted");
    }
};

} // namespace

/**
 * @ingroup tests
 * Test suite for the compiled distributional temporal-T2 runtime.
 */
class TemporalT2DistributionModelTestSuite : public TestSuite
{
  public:
    /** Constructor. */
    TemporalT2DistributionModelTestSuite()
        : TestSuite("wifi-streaming-temporal-t2-distribution-model", Type::UNIT)
    {
        AddTestCase(new TemporalT2DistributionModelParityTestCase, Duration::QUICK);
    }
};

static TemporalT2DistributionModelTestSuite g_temporalT2DistributionModelTestSuite;

} // namespace ns3
