/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "redundancy-policy.h"

#include "ns3/double.h"
#include "ns3/uinteger.h"

namespace ns3
{

NS_OBJECT_ENSURE_REGISTERED(RedundancyPolicy);
NS_OBJECT_ENSURE_REGISTERED(FixedLinkPolicy);
NS_OBJECT_ENSURE_REGISTERED(StaticBestLinkPolicy);
NS_OBJECT_ENSURE_REGISTERED(FullDuplicationPolicy);
NS_OBJECT_ENSURE_REGISTERED(SelectiveDuplicationPolicy);

TypeId
RedundancyPolicy::GetTypeId()
{
    static TypeId tid =
        TypeId("ns3::RedundancyPolicy").SetParent<Object>().SetGroupName("WifiStreaming");
    return tid;
}

TypeId
FixedLinkPolicy::GetTypeId()
{
    static TypeId tid =
        TypeId("ns3::FixedLinkPolicy")
            .SetParent<RedundancyPolicy>()
            .SetGroupName("WifiStreaming")
            .AddConstructor<FixedLinkPolicy>()
            .AddAttribute("Path",
                          "Path selected for every frame.",
                          UintegerValue(0),
                          MakeUintegerAccessor(&FixedLinkPolicy::m_path),
                          MakeUintegerChecker<PathId>());
    return tid;
}

FixedLinkPolicy::FixedLinkPolicy() = default;

void
FixedLinkPolicy::SetPath(PathId path)
{
    m_path = path;
}

PolicyDecision
FixedLinkPolicy::Decide(const FrameDescriptor&, const LinkTelemetrySnapshot& telemetry)
{
    PolicyDecision decision;
    decision.primaryPath = m_path;
    decision.reason = "configured fixed link";
    if (auto score = telemetry.pathScores.find(m_path); score != telemetry.pathScores.end())
    {
        decision.primaryScore = score->second;
    }
    return decision;
}

std::string
FixedLinkPolicy::GetName() const
{
    return "fixed_link_" + std::to_string(m_path);
}

TypeId
StaticBestLinkPolicy::GetTypeId()
{
    static TypeId tid =
        TypeId("ns3::StaticBestLinkPolicy")
            .SetParent<RedundancyPolicy>()
            .SetGroupName("WifiStreaming")
            .AddConstructor<StaticBestLinkPolicy>()
            .AddAttribute("Link0Score",
                          "Configured score for link 0; lower is better.",
                          DoubleValue(0),
                          MakeDoubleAccessor(&StaticBestLinkPolicy::m_link0Score),
                          MakeDoubleChecker<double>())
            .AddAttribute("Link1Score",
                          "Configured score for link 1; lower is better.",
                          DoubleValue(1),
                          MakeDoubleAccessor(&StaticBestLinkPolicy::m_link1Score),
                          MakeDoubleChecker<double>());
    return tid;
}

StaticBestLinkPolicy::StaticBestLinkPolicy() = default;

void
StaticBestLinkPolicy::SetPathScores(double link0Score, double link1Score)
{
    m_link0Score = link0Score;
    m_link1Score = link1Score;
}

PolicyDecision
StaticBestLinkPolicy::Decide(const FrameDescriptor&, const LinkTelemetrySnapshot&)
{
    PolicyDecision decision;
    decision.primaryPath = m_link0Score <= m_link1Score ? 0 : 1;
    decision.primaryScore = decision.primaryPath == 0 ? m_link0Score : m_link1Score;
    decision.secondaryScore = decision.primaryPath == 0 ? m_link1Score : m_link0Score;
    decision.reason = "configured static ranking";
    return decision;
}

std::string
StaticBestLinkPolicy::GetName() const
{
    return "static_best";
}

TypeId
FullDuplicationPolicy::GetTypeId()
{
    static TypeId tid =
        TypeId("ns3::FullDuplicationPolicy")
            .SetParent<RedundancyPolicy>()
            .SetGroupName("WifiStreaming")
            .AddConstructor<FullDuplicationPolicy>()
            .AddAttribute("PrimaryPath",
                          "Path carrying copy 0.",
                          UintegerValue(0),
                          MakeUintegerAccessor(&FullDuplicationPolicy::m_primary),
                          MakeUintegerChecker<PathId>())
            .AddAttribute("SecondaryPath",
                          "Path carrying copy 1.",
                          UintegerValue(1),
                          MakeUintegerAccessor(&FullDuplicationPolicy::m_secondary),
                          MakeUintegerChecker<PathId>());
    return tid;
}

FullDuplicationPolicy::FullDuplicationPolicy() = default;

void
FullDuplicationPolicy::SetPaths(PathId primary, PathId secondary)
{
    m_primary = primary;
    m_secondary = secondary;
}

PolicyDecision
FullDuplicationPolicy::Decide(const FrameDescriptor&, const LinkTelemetrySnapshot& telemetry)
{
    PolicyDecision decision;
    decision.primaryPath = m_primary;
    decision.duplicate = true;
    decision.secondaryPath = m_secondary;
    decision.reason = "full frame duplication";
    if (auto score = telemetry.pathScores.find(m_primary); score != telemetry.pathScores.end())
    {
        decision.primaryScore = score->second;
    }
    if (auto score = telemetry.pathScores.find(m_secondary); score != telemetry.pathScores.end())
    {
        decision.secondaryScore = score->second;
    }
    return decision;
}

std::string
FullDuplicationPolicy::GetName() const
{
    return "full_duplication";
}

TypeId
SelectiveDuplicationPolicy::GetTypeId()
{
    static TypeId tid =
        TypeId("ns3::SelectiveDuplicationPolicy")
            .SetParent<RedundancyPolicy>()
            .SetGroupName("WifiStreaming")
            .AddConstructor<SelectiveDuplicationPolicy>()
            .AddAttribute("PrimaryPath",
                          "Path used before any causal duplication action.",
                          UintegerValue(1),
                          MakeUintegerAccessor(&SelectiveDuplicationPolicy::m_primary),
                          MakeUintegerChecker<PathId>());
    return tid;
}

SelectiveDuplicationPolicy::SelectiveDuplicationPolicy() = default;

void
SelectiveDuplicationPolicy::SetPrimaryPath(PathId path)
{
    m_primary = path;
}

PolicyDecision
SelectiveDuplicationPolicy::Decide(const FrameDescriptor&,
                                   const LinkTelemetrySnapshot& telemetry)
{
    PolicyDecision decision;
    decision.primaryPath = m_primary;
    decision.reason = "causal selective duplication primary";
    if (auto score = telemetry.pathScores.find(m_primary); score != telemetry.pathScores.end())
    {
        decision.primaryScore = score->second;
    }
    return decision;
}

std::string
SelectiveDuplicationPolicy::GetName() const
{
    return "selective_duplication";
}

} // namespace ns3
