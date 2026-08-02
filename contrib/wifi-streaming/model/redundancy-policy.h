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

} // namespace ns3

#endif // REDUNDANCY_POLICY_H
