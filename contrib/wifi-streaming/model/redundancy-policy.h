/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef REDUNDANCY_POLICY_H
#define REDUNDANCY_POLICY_H

#include "frame-source.h"

#include "ns3/object.h"

#include <cstdint>
#include <map>
#include <optional>
#include <string>

namespace ns3
{

using PathId = uint8_t;

/**
 * Causal information available to a redundancy policy at a frame boundary.
 *
 * The baseline policies do not require dynamic telemetry. Scores allow a
 * caller to provide a static ranking without coupling the sender to a
 * particular telemetry implementation.
 */
struct LinkTelemetrySnapshot
{
    std::map<PathId, double> pathScores;
};

struct PolicyDecision
{
    PathId primaryPath{0};
    bool duplicate{false};
    std::optional<PathId> secondaryPath;
    std::string reason;
    double primaryScore{0};
    double secondaryScore{0};
};

class RedundancyPolicy : public Object
{
  public:
    static TypeId GetTypeId();

    virtual PolicyDecision Decide(const FrameDescriptor& frame,
                                  const LinkTelemetrySnapshot& telemetry) = 0;
    virtual std::string GetName() const = 0;
};

class FixedLinkPolicy : public RedundancyPolicy
{
  public:
    static TypeId GetTypeId();
    FixedLinkPolicy();

    void SetPath(PathId path);
    PolicyDecision Decide(const FrameDescriptor& frame,
                          const LinkTelemetrySnapshot& telemetry) override;
    std::string GetName() const override;

  private:
    PathId m_path{0};
};

class StaticBestLinkPolicy : public RedundancyPolicy
{
  public:
    static TypeId GetTypeId();
    StaticBestLinkPolicy();

    void SetPathScores(double link0Score, double link1Score);
    PolicyDecision Decide(const FrameDescriptor& frame,
                          const LinkTelemetrySnapshot& telemetry) override;
    std::string GetName() const override;

  private:
    double m_link0Score{0};
    double m_link1Score{1};
};

class FullDuplicationPolicy : public RedundancyPolicy
{
  public:
    static TypeId GetTypeId();
    FullDuplicationPolicy();

    void SetPaths(PathId primary, PathId secondary);
    PolicyDecision Decide(const FrameDescriptor& frame,
                          const LinkTelemetrySnapshot& telemetry) override;
    std::string GetName() const override;

  private:
    PathId m_primary{0};
    PathId m_secondary{1};
};

/**
 * Select a fixed primary while allowing a separate causal controller to
 * launch a secondary copy later.
 */
class SelectiveDuplicationPolicy : public RedundancyPolicy
{
  public:
    static TypeId GetTypeId();
    SelectiveDuplicationPolicy();

    /**
     * Set the primary path used for every frame.
     *
     * @param path Primary application path.
     */
    void SetPrimaryPath(PathId path);

    PolicyDecision Decide(const FrameDescriptor& frame,
                          const LinkTelemetrySnapshot& telemetry) override;
    std::string GetName() const override;

  private:
    PathId m_primary{1}; ///< Primary path used before a causal rescue action.
};

/**
 * Select a fixed primary while allowing the adaptive-airtime controller to
 * launch a secondary copy later.
 */
class AdaptiveAirtimeDuplicationPolicy : public RedundancyPolicy
{
  public:
    static TypeId GetTypeId();
    AdaptiveAirtimeDuplicationPolicy();

    /**
     * Set the primary path used for every frame.
     *
     * @param path Primary application path.
     */
    void SetPrimaryPath(PathId path);

    PolicyDecision Decide(const FrameDescriptor& frame,
                          const LinkTelemetrySnapshot& telemetry) override;
    std::string GetName() const override;

  private:
    PathId m_primary{1}; ///< Primary path used before a causal rescue action.
};

/**
 * Identify adaptive duplication whose secondary sends the primary deficit.
 *
 * Admission remains controlled by AdaptiveAirtimeDuplicationController. This
 * distinct policy name preserves the whole-copy policy's experiment contract.
 */
class AdaptiveDeficitDuplicationPolicy : public AdaptiveAirtimeDuplicationPolicy
{
  public:
    static TypeId GetTypeId();
    AdaptiveDeficitDuplicationPolicy();

    PolicyDecision Decide(const FrameDescriptor& frame,
                          const LinkTelemetrySnapshot& telemetry) override;
    std::string GetName() const override;
};

/**
 * Select a fixed primary for randomized delayed full-copy exploration.
 *
 * This policy does not duplicate at the frame boundary. A separate experiment
 * controller uses a precomputed frame assignment to launch a full secondary
 * copy at the assigned T2 or T4 intervention time. The distinct policy type
 * and name preserve experiment provenance.
 */
class RandomizedFullCopyExplorationPolicy : public RedundancyPolicy
{
  public:
    /**
     * Get the policy TypeId.
     *
     * @return Policy TypeId.
     */
    static TypeId GetTypeId();

    RandomizedFullCopyExplorationPolicy();

    /**
     * Set the fixed primary path used for every frame.
     *
     * @param path Primary application path.
     */
    void SetPrimaryPath(PathId path);

    /**
     * Select the fixed primary without immediate duplication.
     *
     * @param frame Frame being assigned to the primary path.
     * @param telemetry Causal link telemetry at the frame boundary.
     * @return Fixed-primary decision for later experimental intervention.
     */
    PolicyDecision Decide(const FrameDescriptor& frame,
                          const LinkTelemetrySnapshot& telemetry) override;

    /**
     * Get the stable policy provenance name.
     *
     * @return Policy name.
     */
    std::string GetName() const override;

  private:
    PathId m_primary{1}; ///< Primary path used before the assigned intervention.
};

/**
 * Select the frozen primary path for paired temporal T2 value control.
 *
 * This policy never duplicates at the frame boundary. The paired-value T2
 * controller may later launch the canonical full secondary copy after its
 * causal telemetry, learned-score, and airtime-budget gates pass.
 */
class PairedValueT2Policy : public RedundancyPolicy
{
  public:
    /**
     * Get the policy TypeId.
     *
     * @return Policy TypeId.
     */
    static TypeId GetTypeId();

    PairedValueT2Policy();

    /**
     * Select frozen primary path 1 without immediate duplication.
     *
     * @param frame Frame being assigned to the primary path.
     * @param telemetry Causal link telemetry at the frame boundary.
     * @return Fixed-primary decision for later paired-value control.
     */
    PolicyDecision Decide(const FrameDescriptor& frame,
                          const LinkTelemetrySnapshot& telemetry) override;

    /**
     * Get the stable policy provenance name.
     *
     * @return Policy name.
     */
    std::string GetName() const override;
};

} // namespace ns3

#endif // REDUNDANCY_POLICY_H
