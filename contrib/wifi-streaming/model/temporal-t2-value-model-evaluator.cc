/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "temporal-t2-value-model-evaluator.h"

#include "temporal-t2-value-model-data-v1.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <utility>

namespace ns3
{
namespace
{

constexpr std::size_t RAW_FEATURE_COUNT = 246;

double
TransformedValue(const temporal_t2_value_model_v1::Imputer& imputer,
                 uint16_t transformedFeature,
                 const std::array<float, RAW_FEATURE_COUNT>& features)
{
    if (imputer.medians.size() != features.size())
    {
        throw std::logic_error("compiled temporal T2 imputer has the wrong width");
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
        throw std::logic_error("compiled temporal T2 transform index is invalid");
    }
    const uint16_t rawFeature = imputer.missingIndicatorRawFeatures[indicator];
    if (rawFeature >= features.size())
    {
        throw std::logic_error("compiled temporal T2 missing indicator is invalid");
    }
    return std::isnan(features[rawFeature]) ? 1.0 : 0.0;
}

double
Logistic(double value)
{
    if (value >= 0.0)
    {
        return 1.0 / (1.0 + std::exp(-value));
    }
    const double exponential = std::exp(value);
    return exponential / (1.0 + exponential);
}

std::pair<double, double>
EvaluateClassifier(const temporal_t2_value_model_v1::HgbClassifier& classifier,
                   const std::array<float, RAW_FEATURE_COUNT>& features)
{
    double logit = classifier.baseline;
    for (const auto& tree : classifier.trees)
    {
        uint16_t index = 0;
        while (true)
        {
            if (index >= tree.count || tree.offset + index >= classifier.nodes.size())
            {
                throw std::logic_error("compiled temporal T2 tree child is invalid");
            }
            const auto& node = classifier.nodes[tree.offset + index];
            if (node.leaf)
            {
                logit += node.value;
                break;
            }
            const double value = TransformedValue(classifier.imputer, node.feature, features);
            index = std::isnan(value) ? (node.missingLeft ? node.left : node.right)
                                      : (value <= node.threshold ? node.left : node.right);
        }
    }
    return {logit, Logistic(logit)};
}

std::pair<double, double>
EvaluateCost(const temporal_t2_value_model_v1::RidgeCostModel& model,
             const std::array<float, RAW_FEATURE_COUNT>& features)
{
    const std::size_t transformedCount =
        model.imputer.medians.size() + model.imputer.missingIndicatorRawFeatures.size();
    if (model.means.size() != transformedCount || model.scales.size() != transformedCount ||
        model.coefficients.size() != transformedCount)
    {
        throw std::logic_error("compiled temporal T2 cost model has the wrong width");
    }
    double predictedLog = model.intercept;
    for (std::size_t index = 0; index < transformedCount; ++index)
    {
        if (!(model.scales[index] > 0.0))
        {
            throw std::logic_error("compiled temporal T2 cost scale is invalid");
        }
        const double value =
            TransformedValue(model.imputer, static_cast<uint16_t>(index), features);
        predictedLog +=
            model.coefficients[index] * ((value - model.means[index]) / model.scales[index]);
    }
    const double adjustedLog =
        std::clamp(predictedLog + model.logSmearingFactor, 0.0, model.logCostCap);
    return {predictedLog, std::max(std::expm1(adjustedLog), 1.0)};
}

} // namespace

const TemporalT2ValueModelProvenance&
TemporalT2ValueModelEvaluator::GetProvenance()
{
    return temporal_t2_value_model_v1::GetMetadata().provenance;
}

std::span<const std::string_view>
TemporalT2ValueModelEvaluator::GetFeatureNames()
{
    return temporal_t2_value_model_v1::GetFeatureNames();
}

std::string_view
TemporalT2ValueModelEvaluator::GetFeatureFamily()
{
    return temporal_t2_value_model_v1::GetMetadata().featureFamily;
}

std::string_view
TemporalT2ValueModelEvaluator::GetFeatureAdapter()
{
    return temporal_t2_value_model_v1::GetMetadata().featureAdapter;
}

std::string_view
TemporalT2ValueModelEvaluator::GetRanker()
{
    return temporal_t2_value_model_v1::GetMetadata().ranker;
}

std::string_view
TemporalT2ValueModelEvaluator::GetFrameGate()
{
    return temporal_t2_value_model_v1::GetMetadata().frameGate;
}

std::string_view
TemporalT2ValueModelEvaluator::GetScoreAdapter()
{
    return temporal_t2_value_model_v1::GetMetadata().scoreAdapter;
}

float
TemporalT2ValueModelEvaluator::GetScoreThreshold()
{
    return temporal_t2_value_model_v1::GetMetadata().scoreThreshold;
}

TemporalT2ValueModelResult
TemporalT2ValueModelEvaluator::Evaluate(std::span<const double> features)
{
    if (features.size() != RAW_FEATURE_COUNT || features.size() != GetFeatureNames().size())
    {
        throw std::invalid_argument("temporal T2 feature count differs from compiled model");
    }
    std::array<float, RAW_FEATURE_COUNT> quantized{};
    for (std::size_t index = 0; index < features.size(); ++index)
    {
        const double value = features[index];
        if (!std::isfinite(value) && !std::isnan(value))
        {
            throw std::invalid_argument("temporal T2 features may not contain infinity");
        }
        if (std::isfinite(value) &&
            std::abs(value) > static_cast<double>(std::numeric_limits<float>::max()))
        {
            throw std::invalid_argument("temporal T2 feature overflows float32");
        }
        quantized[index] = static_cast<float>(value);
        if (std::isinf(quantized[index]))
        {
            throw std::invalid_argument("temporal T2 feature overflows float32");
        }
    }

    const auto [primaryLogit, primaryProbability] = EvaluateClassifier(
        temporal_t2_value_model_v1::GetPrimaryBad12Classifier(), quantized);
    const auto [treatedLogit, treatedProbability] = EvaluateClassifier(
        temporal_t2_value_model_v1::GetTreatedBad12Classifier(), quantized);
    const auto [predictedLogAirtime, predictedSecondaryAirtimeUs] =
        EvaluateCost(temporal_t2_value_model_v1::GetCostModel(), quantized);
    const double value = std::max(primaryProbability - treatedProbability, 0.0);
    const float score = static_cast<float>(value / predictedSecondaryAirtimeUs);
    return {primaryLogit,
            primaryProbability,
            treatedLogit,
            treatedProbability,
            predictedLogAirtime,
            predictedSecondaryAirtimeUs,
            value,
            score,
            score >= GetScoreThreshold()};
}

} // namespace ns3
