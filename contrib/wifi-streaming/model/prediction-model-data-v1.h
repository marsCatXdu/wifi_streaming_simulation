/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef PREDICTION_MODEL_DATA_V1_H
#define PREDICTION_MODEL_DATA_V1_H

#include "prediction-model-evaluator.h"

#include <cstddef>
#include <cstdint>
#include <span>
#include <string_view>

namespace ns3
{
namespace prediction_model_v1
{

/**
 * Operation used to produce one model input from a raw feature.
 */
enum class TransformKind : uint8_t
{
    IMPUTED_VALUE,         ///< Median-imputed numeric value.
    MISSING_INDICATOR,     ///< Numeric missing-value indicator.
    ONE_HOT_VALUE,         ///< One-hot encoded, mode-imputed categorical value.
    ONE_HOT_MISSING_STATUS ///< One-hot encoded categorical missing-value indicator.
};

/**
 * One fitted preprocessing output column.
 */
struct Transform
{
    TransformKind kind;  ///< Transformation operation.
    uint16_t rawFeature; ///< Raw input feature index.
    double replacement; ///< Value used when the raw input is missing.
    double category;    ///< Category selected by a one-hot operation.
};

/**
 * One node in a histogram-gradient-boosting regression tree.
 */
struct Node
{
    double value;       ///< Leaf contribution.
    double threshold;   ///< Numeric split threshold.
    uint16_t feature;   ///< Transformed feature index.
    uint16_t left;      ///< Left child index relative to the tree root.
    uint16_t right;     ///< Right child index relative to the tree root.
    bool missingLeft;   ///< Whether a missing split value follows the left child.
    bool leaf;          ///< Whether this node is a leaf.
};

/**
 * Location of one tree in a predictor's node array.
 */
struct Tree
{
    uint32_t offset; ///< First node in the predictor node array.
    uint16_t count;  ///< Number of nodes in the tree.
};

/**
 * Complete fitted data for one stage-specific predictor.
 */
struct Predictor
{
    PredictionStage stage;                  ///< Prediction stage.
    std::span<const Transform> transforms; ///< Fitted preprocessing operations.
    std::span<const Node> nodes;       ///< Concatenated tree nodes.
    std::span<const Tree> trees;       ///< Tree locations.
    double baseline;                   ///< Initial raw model score.
    double plattCoefficient;           ///< Platt logistic coefficient.
    double plattIntercept;             ///< Platt logistic intercept.
};

/**
 * Get the common raw feature contract.
 *
 * @return Ordered raw feature names.
 */
std::span<const std::string_view> GetFeatureNames();

/**
 * Get the fitted predictor for a stage.
 *
 * @param stage Prediction stage.
 * @return Fitted predictor data.
 */
const Predictor& GetPredictor(PredictionStage stage);

} // namespace prediction_model_v1
} // namespace ns3

#endif // PREDICTION_MODEL_DATA_V1_H
