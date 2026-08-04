/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef TEMPORAL_T2_VALUE_MODEL_DATA_V1_H
#define TEMPORAL_T2_VALUE_MODEL_DATA_V1_H

#include "temporal-t2-value-model-evaluator.h"

#include <cstdint>
#include <span>
#include <string_view>

namespace ns3
{
namespace temporal_t2_value_model_v1
{

/** One fitted median imputer and its appended missing indicators. */
struct Imputer
{
    std::span<const double> medians; ///< Per-raw-feature training medians.
    std::span<const uint16_t> missingIndicatorRawFeatures; ///< Appended indicator sources.
};

/** One node in a fitted histogram-gradient-boosting classification tree. */
struct Node
{
    double value;       ///< Leaf logit contribution.
    double threshold;   ///< Numeric split threshold.
    uint16_t feature;   ///< Imputed feature index.
    uint16_t left;      ///< Left child relative to the tree root.
    uint16_t right;     ///< Right child relative to the tree root.
    bool missingLeft;   ///< Whether a missing split value follows the left child.
    bool leaf;          ///< Whether this node is a leaf.
};

/** Location of one tree in a classifier's concatenated node array. */
struct Tree
{
    uint32_t offset; ///< First node in the classifier node array.
    uint16_t count;  ///< Number of nodes in the tree.
};

/** One fitted binary HGB classifier. */
struct HgbClassifier
{
    Imputer imputer;            ///< Fitted training-only imputer.
    std::span<const Node> nodes; ///< Concatenated tree nodes.
    std::span<const Tree> trees; ///< Tree locations.
    double baseline;             ///< Initial raw logit.
};

/** Fitted log1p secondary-airtime Ridge pipeline. */
struct RidgeCostModel
{
    Imputer imputer;                     ///< Fitted treated-launch imputer.
    std::span<const double> means;        ///< StandardScaler means.
    std::span<const double> scales;       ///< StandardScaler scales.
    std::span<const double> coefficients; ///< Ridge coefficients.
    double intercept;                   ///< Ridge intercept.
    double logSmearingFactor;           ///< Log of the Duan smearing factor.
    double logCostCap;                   ///< Log1p of the predicted-cost cap.
};

/** Generated policy metadata. */
struct Metadata
{
    TemporalT2ValueModelProvenance provenance; ///< Immutable source provenance.
    std::string_view featureFamily;             ///< Selected feature-family ID.
    std::string_view ranker;                    ///< Selected ranker ID.
    std::string_view frameGate;                 ///< Caller-owned P-frame gate.
    std::string_view featureAdapter;            ///< Float32 feature adapter ID.
    std::string_view scoreAdapter;              ///< Float32 score adapter ID.
    float scoreThreshold;                       ///< Frozen calibration threshold.
};

/**
 * Get ordered model feature names.
 *
 * @return Ordered primary-only feature names.
 */
std::span<const std::string_view> GetFeatureNames();

/**
 * Get generated policy metadata.
 *
 * @return Generated metadata.
 */
const Metadata& GetMetadata();

/**
 * Get the fitted primary bad12 classifier.
 *
 * @return Primary bad12 classifier.
 */
const HgbClassifier& GetPrimaryBad12Classifier();

/**
 * Get the fitted treated bad12 classifier.
 *
 * @return Treated bad12 classifier.
 */
const HgbClassifier& GetTreatedBad12Classifier();

/**
 * Get the fitted log-airtime cost model.
 *
 * @return Learned cost model.
 */
const RidgeCostModel& GetCostModel();

} // namespace temporal_t2_value_model_v1
} // namespace ns3

#endif // TEMPORAL_T2_VALUE_MODEL_DATA_V1_H
