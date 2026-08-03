/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "randomized-frame-assignment.h"

#include <cmath>
#include <stdexcept>

namespace ns3
{
namespace
{

constexpr uint64_t SPLITMIX_INCREMENT = 0x9e3779b97f4a7c15ULL;
constexpr uint64_t SPLITMIX_MULTIPLIER_1 = 0xbf58476d1ce4e5b9ULL;
constexpr uint64_t SPLITMIX_MULTIPLIER_2 = 0x94d049bb133111ebULL;
constexpr double UNIT_DRAW_SCALE = 0x1.0p-53;

uint64_t
SplitMix64(uint64_t value)
{
    value += SPLITMIX_INCREMENT;
    value = (value ^ (value >> 30)) * SPLITMIX_MULTIPLIER_1;
    value = (value ^ (value >> 27)) * SPLITMIX_MULTIPLIER_2;
    return value ^ (value >> 31);
}

void
ValidateProbabilities(double t2Probability, double t4Probability)
{
    if (!std::isfinite(t2Probability) || !std::isfinite(t4Probability) ||
        t2Probability < 0.0 || t4Probability < 0.0 ||
        t4Probability > 1.0 - t2Probability)
    {
        throw std::invalid_argument(
            "randomized exploration probabilities must be finite, nonnegative, and sum to at "
            "most one");
    }
}

} // namespace

std::string_view
RandomizedFrameAssignment::GetAlgorithmId()
{
    return "splitmix64_v1";
}

RandomizedExplorationAssignment
RandomizedFrameAssignment::Assign(uint64_t salt,
                                  uint64_t seed,
                                  uint64_t run,
                                  uint64_t frameId,
                                  double t2Probability,
                                  double t4Probability)
{
    ValidateProbabilities(t2Probability, t4Probability);

    uint64_t state = SplitMix64(salt);
    state = SplitMix64(state ^ seed);
    state = SplitMix64(state ^ run);
    const uint64_t rawDraw = SplitMix64(state ^ frameId);
    const double unitDraw = static_cast<double>(rawDraw >> 11) * UNIT_DRAW_SCALE;

    RandomizedExplorationAssignment assignment;
    assignment.rawDraw = rawDraw;
    assignment.unitDraw = unitDraw;
    if (unitDraw < t2Probability)
    {
        assignment.arm = RandomizedExplorationArm::FULL_COPY_T2;
        assignment.armProbability = t2Probability;
    }
    else if (unitDraw < t2Probability + t4Probability)
    {
        assignment.arm = RandomizedExplorationArm::FULL_COPY_T4;
        assignment.armProbability = t4Probability;
    }
    else
    {
        assignment.arm = RandomizedExplorationArm::CONTROL;
        assignment.armProbability = (1.0 - t2Probability) - t4Probability;
    }
    return assignment;
}

} // namespace ns3
