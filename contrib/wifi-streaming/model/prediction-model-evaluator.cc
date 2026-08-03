/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "prediction-model-evaluator.h"

#include "prediction-model-data-v1.h"

#include <cmath>
#include <stdexcept>
#include <vector>

namespace ns3
{
namespace
{

double
ApplyTransform(const prediction_model_v1::Transform& transform,
               std::span<const double> features)
{
    const double raw = features[transform.rawFeature];
    const bool missing = std::isnan(raw);
    switch (transform.kind)
    {
    case prediction_model_v1::TransformKind::IMPUTED_VALUE:
        return missing ? transform.replacement : raw;
    case prediction_model_v1::TransformKind::MISSING_INDICATOR:
        return missing ? 1.0 : 0.0;
    case prediction_model_v1::TransformKind::ONE_HOT_VALUE:
        return (missing ? transform.replacement : raw) == transform.category ? 1.0 : 0.0;
    case prediction_model_v1::TransformKind::ONE_HOT_MISSING_STATUS:
        return (missing ? 1.0 : 0.0) == transform.category ? 1.0 : 0.0;
    }
    throw std::logic_error("unknown prediction preprocessing operation");
}

double
EvaluateTree(const prediction_model_v1::Predictor& predictor,
             const prediction_model_v1::Tree& tree,
             std::span<const double> transformed)
{
    uint16_t index = 0;
    while (true)
    {
        if (index >= tree.count)
        {
            throw std::logic_error("compiled prediction tree has an invalid child index");
        }
        const auto& node = predictor.nodes[tree.offset + index];
        if (node.leaf)
        {
            return node.value;
        }
        const double value = transformed[node.feature];
        index = std::isnan(value) ? (node.missingLeft ? node.left : node.right)
                                 : (value <= node.threshold ? node.left : node.right);
    }
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

} // namespace

std::string_view
PredictionModelEvaluator::GetModelId()
{
    return prediction_model_v1::GetModelId();
}

std::string_view
PredictionModelEvaluator::GetTargetId()
{
    return prediction_model_v1::GetTargetId();
}

std::string_view
PredictionModelEvaluator::GetTargetProvenanceSha256()
{
    return prediction_model_v1::GetTargetProvenanceSha256();
}

std::string_view
PredictionModelEvaluator::GetSourceModelSha256()
{
    return prediction_model_v1::GetSourceModelSha256();
}

std::span<const std::string_view>
PredictionModelEvaluator::GetFeatureNames()
{
    return prediction_model_v1::GetFeatureNames();
}

PredictionModelResult
PredictionModelEvaluator::Evaluate(PredictionStage stage, std::span<const double> features)
{
    const auto names = GetFeatureNames();
    if (features.size() != names.size())
    {
        throw std::invalid_argument("prediction feature count does not match the compiled model");
    }
    for (const double value : features)
    {
        if (!std::isfinite(value) && !std::isnan(value))
        {
            throw std::invalid_argument("prediction features may not contain infinity");
        }
    }

    const auto& predictor = prediction_model_v1::GetPredictor(stage);
    std::vector<double> transformed;
    transformed.reserve(predictor.transforms.size());
    for (const auto& transform : predictor.transforms)
    {
        transformed.push_back(ApplyTransform(transform, features));
    }

    double score = predictor.baseline;
    for (const auto& tree : predictor.trees)
    {
        score += EvaluateTree(predictor, tree, transformed);
    }
    const double probability =
        Logistic(predictor.plattCoefficient * score + predictor.plattIntercept);
    return {score, probability};
}

} // namespace ns3
