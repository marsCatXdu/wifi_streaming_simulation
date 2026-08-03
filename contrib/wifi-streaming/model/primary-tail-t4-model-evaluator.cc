/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "primary-tail-t4-model-evaluator.h"

#include "primary-tail-t4-model-data-v1.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <stdexcept>
#include <utility>

namespace ns3
{
namespace
{

double
ApplyTransform(const primary_tail_t4_model_v1::Transform& transform,
               std::span<const double> features)
{
    if (transform.rawFeature >= features.size())
    {
        throw std::logic_error("compiled T4 transform has an invalid raw feature index");
    }
    const double raw = features[transform.rawFeature];
    const bool missing = std::isnan(raw);
    switch (transform.kind)
    {
    case primary_tail_t4_model_v1::TransformKind::IMPUTED_VALUE:
        return missing ? transform.replacement : raw;
    case primary_tail_t4_model_v1::TransformKind::MISSING_INDICATOR:
        return missing ? 1.0 : 0.0;
    case primary_tail_t4_model_v1::TransformKind::ONE_HOT_VALUE:
        return (missing ? transform.replacement : raw) == transform.category ? 1.0 : 0.0;
    case primary_tail_t4_model_v1::TransformKind::ONE_HOT_MISSING_STATUS:
        return (missing ? 1.0 : 0.0) == transform.category ? 1.0 : 0.0;
    }
    throw std::logic_error("unknown compiled T4 preprocessing operation");
}

double
EvaluateTree(const primary_tail_t4_model_v1::Predictor& predictor,
             const primary_tail_t4_model_v1::Tree& tree,
             std::span<const double> transformed)
{
    uint16_t index = 0;
    while (true)
    {
        if (index >= tree.count || tree.offset + index >= predictor.nodes.size())
        {
            throw std::logic_error("compiled T4 tree has an invalid child index");
        }
        const auto& node = predictor.nodes[tree.offset + index];
        if (node.leaf)
        {
            return node.value;
        }
        if (node.feature >= transformed.size())
        {
            throw std::logic_error("compiled T4 tree has an invalid feature index");
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

std::pair<double, double>
EvaluateHead(const primary_tail_t4_model_v1::Predictor& predictor,
             std::span<const double> features)
{
    if (predictor.transforms.size() > primary_tail_t4_model_v1::MAX_TRANSFORM_COUNT)
    {
        throw std::logic_error("compiled T4 preprocessing exceeds its fixed stack capacity");
    }
    std::array<double, primary_tail_t4_model_v1::MAX_TRANSFORM_COUNT> transformed{};
    for (std::size_t i = 0; i < predictor.transforms.size(); ++i)
    {
        transformed[i] = ApplyTransform(predictor.transforms[i], features);
    }
    const std::span<const double> transformedView{transformed.data(),
                                                   predictor.transforms.size()};
    double score = predictor.baseline;
    for (const auto& tree : predictor.trees)
    {
        score += EvaluateTree(predictor, tree, transformedView);
    }
    return {score, Logistic(predictor.plattCoefficient * score + predictor.plattIntercept)};
}

} // namespace

const PrimaryTailT4ModelProvenance&
PrimaryTailT4ModelEvaluator::GetProvenance()
{
    return primary_tail_t4_model_v1::GetMetadata().provenance;
}

std::span<const std::string_view>
PrimaryTailT4ModelEvaluator::GetFeatureNames()
{
    return primary_tail_t4_model_v1::GetFeatureNames();
}

std::span<const std::string_view>
PrimaryTailT4ModelEvaluator::GetPhysicalFeatureNames()
{
    return primary_tail_t4_model_v1::GetPhysicalFeatureNames();
}

uint32_t
PrimaryTailT4ModelEvaluator::GetTailThresholdUs()
{
    return primary_tail_t4_model_v1::GetMetadata().tailThresholdUs;
}

std::string_view
PrimaryTailT4ModelEvaluator::GetScoreName()
{
    return primary_tail_t4_model_v1::GetMetadata().scoreName;
}

std::string_view
PrimaryTailT4ModelEvaluator::GetScoreKind()
{
    return primary_tail_t4_model_v1::GetMetadata().scoreKind;
}

std::string_view
PrimaryTailT4ModelEvaluator::GetCombiner()
{
    return primary_tail_t4_model_v1::GetMetadata().combiner;
}

double
PrimaryTailT4ModelEvaluator::GetPrimaryMissWeight()
{
    return primary_tail_t4_model_v1::GetMetadata().primaryMissWeight;
}

double
PrimaryTailT4ModelEvaluator::GetCompletedTailWeight()
{
    return primary_tail_t4_model_v1::GetMetadata().completedTailWeight;
}

double
PrimaryTailT4ModelEvaluator::GetScoreNormalization()
{
    return primary_tail_t4_model_v1::GetMetadata().scoreNormalization;
}

PrimaryTailT4ModelResult
PrimaryTailT4ModelEvaluator::Evaluate(std::span<const double> features)
{
    if (features.size() != GetFeatureNames().size())
    {
        throw std::invalid_argument("T4 feature count does not match the compiled model");
    }
    for (const double value : features)
    {
        if (!std::isfinite(value) && !std::isnan(value))
        {
            throw std::invalid_argument("T4 prediction features may not contain infinity");
        }
    }

    const auto [missScore, missProbability] =
        EvaluateHead(primary_tail_t4_model_v1::GetPrimaryMissPredictor(), features);
    const auto [tailScore, tailProbability] =
        EvaluateHead(primary_tail_t4_model_v1::GetCompletedTailPredictor(), features);
    const auto& metadata = primary_tail_t4_model_v1::GetMetadata();
    const double admissionScore = std::clamp(
        (metadata.primaryMissWeight * missProbability +
         metadata.completedTailWeight * tailProbability) /
            metadata.scoreNormalization,
        0.0,
        1.0);
    return {missScore, missProbability, tailScore, tailProbability, admissionScore};
}

} // namespace ns3
