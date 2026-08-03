/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef RANDOMIZED_FRAME_ASSIGNMENT_H
#define RANDOMIZED_FRAME_ASSIGNMENT_H

#include <cstdint>
#include <string_view>

namespace ns3
{

/**
 * @ingroup wifi-streaming
 * Experiment arm assigned to one frame in randomized full-copy exploration.
 */
enum class RandomizedExplorationArm : uint8_t
{
    CONTROL,      ///< Do not launch an exploratory secondary copy.
    FULL_COPY_T2, ///< Launch a full secondary copy at T2.
    FULL_COPY_T4  ///< Launch a full secondary copy at T4.
};

/**
 * @ingroup wifi-streaming
 * Immutable result of one randomized frame assignment.
 */
struct RandomizedExplorationAssignment
{
    uint64_t rawDraw{0};  ///< Full-width deterministic draw.
    double unitDraw{0};   ///< Top-53-bit draw in [0, 1).
    RandomizedExplorationArm arm{RandomizedExplorationArm::CONTROL}; ///< Assigned arm.
    double armProbability{1};                                      ///< Assigned-arm probability.
};

/**
 * @ingroup wifi-streaming
 * Assign frames to randomized delayed full-copy experiment arms.
 *
 * The splitmix64_v1 contract is platform-stable and consumes no ns-3 random
 * stream. It first applies SplitMix64 to the salt. It then folds seed, run,
 * and frame ID, in that order, by XORing each field into the preceding state
 * and applying SplitMix64 again. The top 53 result bits are scaled by 2^-53
 * to form the unit draw.
 *
 * For valid probabilities pT2 and pT4, the half-open arm intervals are
 * [0, pT2) for FULL_COPY_T2, [pT2, pT2 + pT4) for FULL_COPY_T4, and
 * [pT2 + pT4, 1) for CONTROL.
 */
class RandomizedFrameAssignment
{
  public:
    /**
     * Get the stable assignment-algorithm identifier.
     *
     * @return Assignment-algorithm identifier.
     */
    static std::string_view GetAlgorithmId();

    /**
     * Assign one frame to an experiment arm.
     *
     * @param salt Explicit experiment salt.
     * @param seed ns-3 experiment seed.
     * @param run ns-3 experiment run number.
     * @param frameId Frame identifier within the run.
     * @param t2Probability Probability of FULL_COPY_T2.
     * @param t4Probability Probability of FULL_COPY_T4.
     * @return Deterministic draw, arm, and assigned-arm probability.
     * @throws std::invalid_argument if either probability is non-finite or
     *         negative, or if their sum exceeds one.
     */
    static RandomizedExplorationAssignment Assign(uint64_t salt,
                                                   uint64_t seed,
                                                   uint64_t run,
                                                   uint64_t frameId,
                                                   double t2Probability,
                                                   double t4Probability);
};

} // namespace ns3

#endif // RANDOMIZED_FRAME_ASSIGNMENT_H
