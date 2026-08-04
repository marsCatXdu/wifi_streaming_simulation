/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "ns3/temporal-t2-value-model-evaluator.h"
#include "ns3/test.h"

#include "temporal-t2-value-model-golden-v1.h"

#include <array>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace ns3
{
namespace
{

/**
 * @ingroup tests
 * Verify exact sklearn parity and the frozen temporal T2 score contract.
 */
class TemporalT2ValueModelParityTestCase : public TestCase
{
  public:
    /** Constructor. */
    TemporalT2ValueModelParityTestCase()
        : TestCase("Compiled temporal T2 value model matches sklearn")
    {
    }

  private:
    void
    DoRun() override
    {
        const auto featureNames = TemporalT2ValueModelEvaluator::GetFeatureNames();
        NS_TEST_ASSERT_MSG_EQ(featureNames.size(),
                              246,
                              "Compiled temporal T2 feature count differs");
        for (const auto name : featureNames)
        {
            NS_TEST_ASSERT_MSG_EQ(name.find("secondary"),
                                  std::string_view::npos,
                                  "Secondary feature escaped the compiled model");
        }
        NS_TEST_ASSERT_MSG_EQ(TemporalT2ValueModelEvaluator::GetRanker(),
                              "legacy_bad12_value_per_cost",
                              "Compiled temporal T2 ranker differs");
        NS_TEST_ASSERT_MSG_EQ(TemporalT2ValueModelEvaluator::GetFeatureFamily(),
                              "primary_compact_physics_temporal",
                              "Compiled temporal T2 feature family differs");
        NS_TEST_ASSERT_MSG_EQ(TemporalT2ValueModelEvaluator::GetFeatureAdapter(),
                              "finite_numeric_float32_then_float64_one_hot_v1",
                              "Compiled temporal T2 feature adapter differs");
        NS_TEST_ASSERT_MSG_EQ(TemporalT2ValueModelEvaluator::GetFrameGate(),
                              "p_frames_only",
                              "Compiled temporal T2 frame gate differs");
        NS_TEST_ASSERT_MSG_EQ(TemporalT2ValueModelEvaluator::GetScoreAdapter(),
                              "final_candidate_float32_threshold_ge_v1",
                              "Compiled temporal T2 score adapter differs");
        NS_TEST_ASSERT_MSG_EQ_TOL(TemporalT2ValueModelEvaluator::GetScoreThreshold(),
                                  8.952784264693037e-05F,
                                  0.0,
                                  "Compiled temporal T2 threshold differs");

        const auto& provenance = TemporalT2ValueModelEvaluator::GetProvenance();
        const auto& goldenProvenance = temporal_t2_value_model_golden_v1::g_provenance;
        NS_TEST_ASSERT_MSG_EQ(provenance.evidenceStatus,
                              goldenProvenance.evidenceStatus,
                              "Compiled evidence status differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.featureContractId,
                              goldenProvenance.featureContractId,
                              "Compiled feature-contract ID differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.modelSpecId,
                              goldenProvenance.modelSpecId,
                              "Compiled model-spec ID differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.selectionId,
                              goldenProvenance.selectionId,
                              "Compiled selection ID differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.trainingGitCommit,
                              goldenProvenance.trainingGitCommit,
                              "Compiled clean-fit commit differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.sourceModelSha256,
                              goldenProvenance.sourceModelSha256,
                              "Compiled source model differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.sourceMetricsSha256,
                              goldenProvenance.sourceMetricsSha256,
                              "Compiled source metrics differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.sourceManifestSha256,
                              goldenProvenance.sourceManifestSha256,
                              "Compiled source manifest differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.frozenSelectionSha256,
                              goldenProvenance.frozenSelectionSha256,
                              "Compiled frozen selection differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.datasetManifestSha256,
                              goldenProvenance.datasetManifestSha256,
                              "Compiled dataset manifest differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.datasetMetadataSha256,
                              goldenProvenance.datasetMetadataSha256,
                              "Compiled dataset metadata differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.datasetCsvSha256,
                              goldenProvenance.datasetCsvSha256,
                              "Compiled dataset CSV differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.trainerSha256,
                              goldenProvenance.trainerSha256,
                              "Compiled trainer differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.exporterSha256,
                              goldenProvenance.exporterSha256,
                              "Compiled exporter differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.plainModelSha256,
                              goldenProvenance.plainModelSha256,
                              "Compiled plain-model digest differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.featureContractSha256,
                              goldenProvenance.featureContractSha256,
                              "Compiled feature-contract digest differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.selectedPolicySha256,
                              goldenProvenance.selectedPolicySha256,
                              "Compiled selected-policy digest differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.primaryHeadSha256,
                              goldenProvenance.primaryHeadSha256,
                              "Compiled primary-head digest differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.treatedHeadSha256,
                              goldenProvenance.treatedHeadSha256,
                              "Compiled treated-head digest differs");
        NS_TEST_ASSERT_MSG_EQ(provenance.costModelSha256,
                              goldenProvenance.costModelSha256,
                              "Compiled cost-model digest differs");

        for (const auto& golden : temporal_t2_value_model_golden_v1::g_cases)
        {
            const auto result = TemporalT2ValueModelEvaluator::Evaluate(golden.features);
            NS_TEST_ASSERT_MSG_EQ_TOL(result.primaryBad12Logit,
                                      golden.expected.primaryBad12Logit,
                                      1e-11,
                                      golden.label << ": primary logit differs");
            NS_TEST_ASSERT_MSG_EQ_TOL(result.primaryBad12Probability,
                                      golden.expected.primaryBad12Probability,
                                      1e-12,
                                      golden.label << ": primary probability differs");
            NS_TEST_ASSERT_MSG_EQ_TOL(result.treatedBad12Logit,
                                      golden.expected.treatedBad12Logit,
                                      1e-11,
                                      golden.label << ": treated logit differs");
            NS_TEST_ASSERT_MSG_EQ_TOL(result.treatedBad12Probability,
                                      golden.expected.treatedBad12Probability,
                                      1e-12,
                                      golden.label << ": treated probability differs");
            NS_TEST_ASSERT_MSG_EQ_TOL(result.predictedLogAirtime,
                                      golden.expected.predictedLogAirtime,
                                      1e-9,
                                      golden.label << ": predicted log airtime differs");
            NS_TEST_ASSERT_MSG_EQ_TOL(result.predictedSecondaryAirtimeUs,
                                      golden.expected.predictedSecondaryAirtimeUs,
                                      1e-8,
                                      golden.label << ": predicted airtime differs");
            NS_TEST_ASSERT_MSG_EQ_TOL(result.nonnegativeBad12Value,
                                      golden.expected.nonnegativeBad12Value,
                                      1e-12,
                                      golden.label << ": nonnegative value differs");
            NS_TEST_ASSERT_MSG_EQ(result.valuePerCostScore,
                                  golden.expected.valuePerCostScore,
                                  golden.label << ": float32 score differs");
            NS_TEST_ASSERT_MSG_EQ(result.passesScoreThreshold,
                                  golden.expected.passesScoreThreshold,
                                  golden.label << ": threshold result differs");
            NS_TEST_ASSERT_MSG_EQ(
                result.passesScoreThreshold,
                result.valuePerCostScore >= TemporalT2ValueModelEvaluator::GetScoreThreshold(),
                golden.label << ": threshold comparator is not >=");
        }

        bool rejectedWidth = false;
        try
        {
            const std::array<double, 1> wrongWidth{0.0};
            TemporalT2ValueModelEvaluator::Evaluate(wrongWidth);
        }
        catch (const std::invalid_argument&)
        {
            rejectedWidth = true;
        }
        NS_TEST_ASSERT_MSG_EQ(rejectedWidth, true, "Wrong feature width was accepted");

        bool rejectedInfinity = false;
        try
        {
            std::array<double, 246> values{};
            values[20] = std::numeric_limits<double>::infinity();
            TemporalT2ValueModelEvaluator::Evaluate(values);
        }
        catch (const std::invalid_argument&)
        {
            rejectedInfinity = true;
        }
        NS_TEST_ASSERT_MSG_EQ(rejectedInfinity, true, "Infinite feature was accepted");

        bool rejectedFloatOverflow = false;
        try
        {
            std::array<double, 246> values{};
            values[20] = std::numeric_limits<double>::max();
            TemporalT2ValueModelEvaluator::Evaluate(values);
        }
        catch (const std::invalid_argument&)
        {
            rejectedFloatOverflow = true;
        }
        NS_TEST_ASSERT_MSG_EQ(rejectedFloatOverflow,
                              true,
                              "Float32-overflow feature was accepted");
    }
};

} // namespace

/**
 * @ingroup tests
 * Test suite for the compiled temporal T2 value model.
 */
class TemporalT2ValueModelTestSuite : public TestSuite
{
  public:
    /** Constructor. */
    TemporalT2ValueModelTestSuite()
        : TestSuite("wifi-streaming-temporal-t2-value-model", Type::UNIT)
    {
        AddTestCase(new TemporalT2ValueModelParityTestCase, Duration::QUICK);
    }
};

static TemporalT2ValueModelTestSuite g_temporalT2ValueModelTestSuite;

} // namespace ns3
