/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "temporal-t2-distribution-model-evaluator.h"

#include "temporal-t2-distribution-model-data-v1.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace ns3
{
namespace
{

using ModelData = temporal_t2_distribution_model_v1::MulticlassClassifier;
using Imputer = temporal_t2_distribution_model_v1::Imputer;

constexpr double CREDIT_TOLERANCE_US = 1e-9;

double
TransformedValue(const Imputer& imputer,
                 uint16_t transformedFeature,
                 const std::array<float,
                                  TemporalT2DistributionModelEvaluator::RAW_FEATURE_COUNT>&
                     features)
{
    if (imputer.medians.size() != features.size())
    {
        throw std::logic_error("compiled distributional T2 imputer has the wrong width");
    }
    if (transformedFeature < features.size())
    {
        const float raw = features[transformedFeature];
        return std::isnan(raw) ? imputer.medians[transformedFeature]
                               : static_cast<double>(raw);
    }
    const std::size_t indicator = transformedFeature - features.size();
    if (indicator >= imputer.missingIndicatorRawFeatures.size())
    {
        throw std::logic_error("compiled distributional T2 transform index is invalid");
    }
    const uint16_t rawFeature = imputer.missingIndicatorRawFeatures[indicator];
    if (rawFeature >= features.size())
    {
        throw std::logic_error("compiled distributional T2 missing indicator is invalid");
    }
    return std::isnan(features[rawFeature]) ? 1.0 : 0.0;
}

struct HeadResult
{
    std::array<double, 6> logits; ///< Raw class scores.
    std::array<double, 6> probabilities; ///< Dirichlet-smoothed probabilities.
    std::array<double, 5> cdf; ///< Cumulative probability at finite thresholds.
};

HeadResult
EvaluateHead(const ModelData& model,
             const std::array<float,
                              TemporalT2DistributionModelEvaluator::RAW_FEATURE_COUNT>&
                 features)
{
    HeadResult result;
    result.logits = model.baseline;
    for (const auto& tree : model.trees)
    {
        if (tree.classIndex >= result.logits.size())
        {
            throw std::logic_error("compiled distributional T2 tree class is invalid");
        }
        uint16_t index = 0;
        while (true)
        {
            if (index >= tree.count || tree.offset + index >= model.nodes.size())
            {
                throw std::logic_error("compiled distributional T2 tree child is invalid");
            }
            const auto& node = model.nodes[tree.offset + index];
            if (node.leaf)
            {
                result.logits[tree.classIndex] += node.value;
                break;
            }
            const double value = TransformedValue(model.imputer, node.feature, features);
            index = std::isnan(value) ? (node.missingLeft ? node.left : node.right)
                                      : (value <= node.threshold ? node.left : node.right);
        }
    }

    const double maximum = *std::max_element(result.logits.begin(), result.logits.end());
    std::array<double, 6> softmax{};
    double denominator = 0.0;
    for (std::size_t index = 0; index < softmax.size(); ++index)
    {
        softmax[index] = std::exp(result.logits[index] - maximum);
        denominator += softmax[index];
    }
    if (!(denominator > 0.0) || !std::isfinite(denominator) || model.trainingCount == 0 ||
        !(model.dirichletAlpha > 0.0))
    {
        throw std::logic_error("compiled distributional T2 probability state is invalid");
    }
    const double smoothedDenominator =
        model.trainingCount + model.dirichletAlpha * softmax.size();
    double cumulative = 0.0;
    for (std::size_t index = 0; index < softmax.size(); ++index)
    {
        result.probabilities[index] =
            (model.trainingCount * softmax[index] / denominator + model.dirichletAlpha) /
            smoothedDenominator;
        cumulative += result.probabilities[index];
        if (index < result.cdf.size())
        {
            result.cdf[index] = cumulative;
        }
    }
    return result;
}

bool
ValidateClassifier(const ModelData& model)
{
    if (model.imputer.medians.size() !=
            TemporalT2DistributionModelEvaluator::RAW_FEATURE_COUNT ||
        model.trees.size() != 64 * TemporalT2DistributionModelEvaluator::CLASS_COUNT ||
        model.trainingCount == 0 || !(model.dirichletAlpha > 0.0))
    {
        return false;
    }
    for (const auto feature : model.imputer.missingIndicatorRawFeatures)
    {
        if (feature >= model.imputer.medians.size())
        {
            return false;
        }
    }
    for (const auto& tree : model.trees)
    {
        if (tree.count == 0 || tree.classIndex >= TemporalT2DistributionModelEvaluator::CLASS_COUNT ||
            tree.offset + tree.count > model.nodes.size())
        {
            return false;
        }
    }
    return true;
}

bool
ValidateRuntimeContract()
{
    const auto names = temporal_t2_distribution_model_v1::GetFeatureNames();
    const auto bins = temporal_t2_distribution_model_v1::GetReferenceBins();
    const auto& metadata = temporal_t2_distribution_model_v1::GetMetadata();
    if (names.size() != TemporalT2DistributionModelEvaluator::RAW_FEATURE_COUNT ||
        bins.size() != TemporalT2DistributionModelEvaluator::TIME_BIN_COUNT ||
        metadata.provenance.runtimeContractId != "temporal-t2-shadow-borrow-runtime-v1" ||
        metadata.provenance.selectedVariant != "primary_secondary_hgb64" ||
        metadata.featureFamily !=
            "primary_compact_physics_temporal_plus_passive_secondary" ||
        metadata.modelSpecId != "hgb64_depth3_7leaf_multiclass_v1" ||
        metadata.objective != "deadline_rescue" || metadata.frameGate != "p_frames_only" ||
        !(metadata.canonicalPFrameReservationUs > 0.0) ||
        metadata.maximumRepayableCreditUs != 372000.0 || metadata.timeBinWidthUs != 5000000 ||
        !ValidateClassifier(temporal_t2_distribution_model_v1::GetControlClassifier()) ||
        !ValidateClassifier(temporal_t2_distribution_model_v1::GetFullCopyClassifier()))
    {
        return false;
    }
    for (const auto& bin : bins)
    {
        if (!std::isfinite(bin.congestionCutpoints[0]) ||
            !std::isfinite(bin.congestionCutpoints[1]) ||
            !(bin.congestionCutpoints[0] < bin.congestionCutpoints[1]))
        {
            return false;
        }
        for (const auto& curve : bin.congestionCurves)
        {
            if (curve.trainingRunCount == 0)
            {
                return false;
            }
            double prior = std::numeric_limits<double>::infinity();
            for (const double density : curve.densityDescending)
            {
                if (!(density > 0.0) || !std::isfinite(density) || density > prior)
                {
                    return false;
                }
                prior = density;
            }
        }
    }
    return true;
}

void
RequireRuntimeContract()
{
    static const bool valid = ValidateRuntimeContract();
    if (!valid)
    {
        throw std::logic_error("compiled distributional T2 runtime contract differs");
    }
}

} // namespace

const TemporalT2DistributionModelProvenance&
TemporalT2DistributionModelEvaluator::GetProvenance()
{
    RequireRuntimeContract();
    return temporal_t2_distribution_model_v1::GetMetadata().provenance;
}

std::span<const std::string_view>
TemporalT2DistributionModelEvaluator::GetFeatureNames()
{
    RequireRuntimeContract();
    return temporal_t2_distribution_model_v1::GetFeatureNames();
}

std::string_view
TemporalT2DistributionModelEvaluator::GetFeatureFamily()
{
    RequireRuntimeContract();
    return temporal_t2_distribution_model_v1::GetMetadata().featureFamily;
}

std::string_view
TemporalT2DistributionModelEvaluator::GetFeatureAdapter()
{
    RequireRuntimeContract();
    return temporal_t2_distribution_model_v1::GetMetadata().featureAdapter;
}

std::string_view
TemporalT2DistributionModelEvaluator::GetModelSpecId()
{
    RequireRuntimeContract();
    return temporal_t2_distribution_model_v1::GetMetadata().modelSpecId;
}

std::string_view
TemporalT2DistributionModelEvaluator::GetObjective()
{
    RequireRuntimeContract();
    return temporal_t2_distribution_model_v1::GetMetadata().objective;
}

std::string_view
TemporalT2DistributionModelEvaluator::GetFrameGate()
{
    RequireRuntimeContract();
    return temporal_t2_distribution_model_v1::GetMetadata().frameGate;
}

double
TemporalT2DistributionModelEvaluator::GetCanonicalPFrameReservationUs()
{
    RequireRuntimeContract();
    return temporal_t2_distribution_model_v1::GetMetadata().canonicalPFrameReservationUs;
}

double
TemporalT2DistributionModelEvaluator::GetMaximumRepayableCreditUs()
{
    RequireRuntimeContract();
    return temporal_t2_distribution_model_v1::GetMetadata().maximumRepayableCreditUs;
}

uint32_t
TemporalT2DistributionModelEvaluator::GetTimeBinWidthUs()
{
    RequireRuntimeContract();
    return temporal_t2_distribution_model_v1::GetMetadata().timeBinWidthUs;
}

TemporalT2DistributionModelResult
TemporalT2DistributionModelEvaluator::Evaluate(std::span<const double> features)
{
    RequireRuntimeContract();
    if (features.size() != RAW_FEATURE_COUNT)
    {
        throw std::invalid_argument("distributional T2 feature count differs from compiled model");
    }
    std::array<float, RAW_FEATURE_COUNT> quantized{};
    for (std::size_t index = 0; index < features.size(); ++index)
    {
        const double value = features[index];
        if (!std::isfinite(value) && !std::isnan(value))
        {
            throw std::invalid_argument("distributional T2 features may not contain infinity");
        }
        if (std::isfinite(value) &&
            std::abs(value) > static_cast<double>(std::numeric_limits<float>::max()))
        {
            throw std::invalid_argument("distributional T2 feature overflows binary32");
        }
        quantized[index] = static_cast<float>(value);
        if (std::isinf(quantized[index]))
        {
            throw std::invalid_argument("distributional T2 feature overflows binary32");
        }
    }

    const auto control = EvaluateHead(
        temporal_t2_distribution_model_v1::GetControlClassifier(), quantized);
    const auto fullCopy = EvaluateHead(
        temporal_t2_distribution_model_v1::GetFullCopyClassifier(), quantized);
    return {control.logits,
            control.probabilities,
            control.cdf,
            fullCopy.logits,
            fullCopy.probabilities,
            fullCopy.cdf,
            std::max(fullCopy.cdf.back() - control.cdf.back(), 0.0),
            fullCopy.cdf[1] - control.cdf[1]};
}

uint8_t
TemporalT2DistributionModelEvaluator::GetTimeBin(double decisionTimeUs)
{
    RequireRuntimeContract();
    if (!std::isfinite(decisionTimeUs) || decisionTimeUs < 0.0 ||
        decisionTimeUs > 60000000.0)
    {
        throw std::invalid_argument("distributional T2 decision time is outside measurement");
    }
    return std::min<uint8_t>(
        static_cast<uint8_t>(decisionTimeUs / GetTimeBinWidthUs()), TIME_BIN_COUNT - 1);
}

uint8_t
TemporalT2DistributionModelEvaluator::GetCongestionRegime(
    uint8_t timeBin,
    double runningPrimaryBusy20ms)
{
    RequireRuntimeContract();
    const auto bins = temporal_t2_distribution_model_v1::GetReferenceBins();
    if (timeBin >= bins.size() || !std::isfinite(runningPrimaryBusy20ms) ||
        runningPrimaryBusy20ms < 0.0 || runningPrimaryBusy20ms > 1.0)
    {
        throw std::invalid_argument("distributional T2 congestion state is invalid");
    }
    const auto& cutpoints = bins[timeBin].congestionCutpoints;
    if (runningPrimaryBusy20ms < cutpoints[0])
    {
        return 0;
    }
    if (runningPrimaryBusy20ms < cutpoints[1])
    {
        return 1;
    }
    return 2;
}

double
TemporalT2DistributionModelEvaluator::GetOpportunityCost(uint8_t timeBin,
                                                          uint8_t regime,
                                                          double repayableCreditUs)
{
    RequireRuntimeContract();
    const auto bins = temporal_t2_distribution_model_v1::GetReferenceBins();
    const auto& metadata = temporal_t2_distribution_model_v1::GetMetadata();
    if (timeBin >= bins.size() || regime >= REGIME_COUNT || !std::isfinite(repayableCreditUs) ||
        repayableCreditUs < 0.0 ||
        repayableCreditUs > metadata.maximumRepayableCreditUs + CREDIT_TOLERANCE_US)
    {
        throw std::invalid_argument("distributional T2 shadow-price query is invalid");
    }
    const auto& curve = bins[timeBin].congestionCurves[regime];
    if (curve.densityDescending.empty())
    {
        return std::numeric_limits<double>::infinity();
    }
    const long double target = static_cast<long double>(repayableCreditUs) *
                               static_cast<long double>(curve.trainingRunCount);
    const long double cost = static_cast<long double>(metadata.canonicalPFrameReservationUs);
    std::size_t low = 0;
    std::size_t high = curve.densityDescending.size();
    while (low < high)
    {
        const std::size_t middle = (low + high) / 2;
        if (cost * static_cast<long double>(middle + 1) <= target)
        {
            low = middle + 1;
        }
        else
        {
            high = middle;
        }
    }
    const std::size_t affordable = low;
    if (affordable == 0)
    {
        return std::numeric_limits<double>::infinity();
    }
    if (affordable >= curve.densityDescending.size())
    {
        return curve.complete ? 0.0 : curve.densityDescending.back();
    }
    return curve.densityDescending[affordable - 1];
}

bool
TemporalT2DistributionModelEvaluator::HasExactRuntimeContract()
{
    return ValidateRuntimeContract();
}

} // namespace ns3
