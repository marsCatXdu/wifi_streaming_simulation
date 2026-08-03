/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef PRIMARY_TAIL_T4_MODEL_DATA_V1_H
#define PRIMARY_TAIL_T4_MODEL_DATA_V1_H

#include "primary-tail-t4-model-evaluator.h"

#include <cstddef>
#include <cstdint>
#include <span>
#include <string_view>

namespace ns3
{
namespace primary_tail_t4_model_v1
{

/** Maximum supported preprocessing output width. */
inline constexpr std::size_t MAX_TRANSFORM_COUNT = 256;

/** Operation used to produce one model input from a raw feature. */
enum class TransformKind : uint8_t
{
    IMPUTED_VALUE,         ///< Median-imputed numeric value.
    MISSING_INDICATOR,     ///< Numeric missing-value indicator.
    ONE_HOT_VALUE,         ///< One-hot encoded, mode-imputed categorical value.
    ONE_HOT_MISSING_STATUS ///< One-hot encoded categorical missing status.
};

/** One fitted preprocessing output column. */
struct Transform
{
    TransformKind kind;  ///< Transformation operation.
    uint16_t rawFeature; ///< Raw input feature index.
    double replacement; ///< Replacement for a missing raw input.
    double category;    ///< Category selected by a one-hot operation.
};

/** One node in a histogram-gradient-boosting regression tree. */
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

/** Location of one tree in a predictor's concatenated node array. */
struct Tree
{
    uint32_t offset; ///< First node in the predictor node array.
    uint16_t count;  ///< Number of nodes in the tree.
};

/** Complete fitted data for one calibrated predictor head. */
struct Predictor
{
    std::span<const Transform> transforms; ///< Fitted preprocessing operations.
    std::span<const Node> nodes;            ///< Concatenated tree nodes.
    std::span<const Tree> trees;            ///< Tree locations.
    double baseline;                        ///< Initial raw model score.
    double plattCoefficient;                ///< Platt logistic coefficient.
    double plattIntercept;                  ///< Platt logistic intercept.
};

/** Runtime metadata and immutable provenance generated with the model. */
struct Metadata
{
    PrimaryTailT4ModelProvenance provenance; ///< Model and contract provenance.
    uint32_t tailThresholdUs;                ///< Completed-tail label threshold.
    std::string_view scoreName;              ///< Runtime admission-score name.
    std::string_view scoreKind;              ///< Runtime admission-score kind.
    std::string_view combiner;               ///< Runtime combiner identifier.
    double primaryMissWeight;                ///< Miss-head combiner weight.
    double completedTailWeight;              ///< Tail-head combiner weight.
    double scoreNormalization;               ///< Sum of positive head weights.
};

/**
 * Get the ordered logical model features.
 *
 * @return Ordered logical feature names.
 */
std::span<const std::string_view> GetFeatureNames();

/**
 * Get the ordered physical dataset features.
 *
 * @return Ordered physical feature names.
 */
std::span<const std::string_view> GetPhysicalFeatureNames();

/**
 * Get generated runtime metadata.
 *
 * @return Runtime metadata.
 */
const Metadata& GetMetadata();

/**
 * Get the primary-miss predictor head.
 *
 * @return Primary-miss predictor.
 */
const Predictor& GetPrimaryMissPredictor();

/**
 * Get the completed-tail predictor head.
 *
 * @return Completed-tail predictor.
 */
const Predictor& GetCompletedTailPredictor();

} // namespace primary_tail_t4_model_v1
} // namespace ns3

#endif // PRIMARY_TAIL_T4_MODEL_DATA_V1_H
