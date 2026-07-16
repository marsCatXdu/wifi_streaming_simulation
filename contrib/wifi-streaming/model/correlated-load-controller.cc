/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "correlated-load-controller.h"

#include "controlled-udp-application.h"
#include "ns3/abort.h"
#include "ns3/double.h"
#include "ns3/simulator.h"

#include <algorithm>
#include <cctype>
#include <fstream>
#include <sstream>

namespace ns3
{

NS_OBJECT_ENSURE_REGISTERED(CorrelatedLoadController);

TypeId
CorrelatedLoadController::GetTypeId()
{
    static TypeId tid =
        TypeId("ns3::CorrelatedLoadController")
            .SetParent<Object>()
            .SetGroupName("WifiStreaming")
            .AddConstructor<CorrelatedLoadController>();
    return tid;
}

CorrelatedLoadController::CorrelatedLoadController()
{
    SetLinkCount(2);
}

CorrelatedLoadController::~CorrelatedLoadController() = default;

void
CorrelatedLoadController::SetMode(const std::string& mode)
{
    if (mode == "independent")
    {
        m_mode = CorrelatedLoadMode::INDEPENDENT;
    }
    else if (mode == "common_bursts")
    {
        m_mode = CorrelatedLoadMode::COMMON_BURSTS;
    }
    else if (mode == "mixed_common_and_independent")
    {
        m_mode = CorrelatedLoadMode::MIXED_COMMON_AND_INDEPENDENT;
    }
    else if (mode == "trace_replay")
    {
        m_mode = CorrelatedLoadMode::TRACE_REPLAY;
    }
    else
    {
        NS_ABORT_MSG("Unknown correlated load mode " << mode);
    }
}

CorrelatedLoadMode
CorrelatedLoadController::GetMode() const
{
    return m_mode;
}

void
CorrelatedLoadController::SetLinkCount(uint32_t count)
{
    NS_ABORT_MSG_IF(m_running, "Cannot change link count while controller is running");
    NS_ABORT_MSG_IF(count == 0, "Controller requires at least one link");
    m_linkCount = count;
    m_localOn.assign(count, false);
    m_effectiveOn.assign(count, false);
    m_applications.resize(count);
    m_localVariables.resize(count);
    for (auto& pair : m_localVariables)
    {
        pair = {CreateObject<ExponentialRandomVariable>(),
                CreateObject<ExponentialRandomVariable>()};
    }
    m_commonVariables = {CreateObject<ExponentialRandomVariable>(),
                         CreateObject<ExponentialRandomVariable>()};
}

void
CorrelatedLoadController::SetCommonMeans(Time onMean, Time offMean)
{
    NS_ABORT_MSG_IF(onMean.GetNanoSeconds() <= 0 || offMean.GetNanoSeconds() <= 0,
                    "Common ON/OFF means must be positive");
    m_commonOnMean = onMean;
    m_commonOffMean = offMean;
}

void
CorrelatedLoadController::SetLocalMeans(Time onMean, Time offMean)
{
    NS_ABORT_MSG_IF(onMean.GetNanoSeconds() <= 0 || offMean.GetNanoSeconds() <= 0,
                    "Local ON/OFF means must be positive");
    m_localOnMean = onMean;
    m_localOffMean = offMean;
}

void
CorrelatedLoadController::SetCommonDeterministicDurations(Time onDuration, Time offDuration)
{
    NS_ABORT_MSG_IF(onDuration.GetNanoSeconds() < 0 || offDuration.GetNanoSeconds() < 0,
                    "Common deterministic durations cannot be negative");
    m_commonOnDuration = onDuration;
    m_commonOffDuration = offDuration;
}

void
CorrelatedLoadController::SetLocalDeterministicDurations(Time onDuration, Time offDuration)
{
    NS_ABORT_MSG_IF(onDuration.GetNanoSeconds() < 0 || offDuration.GetNanoSeconds() < 0,
                    "Local deterministic durations cannot be negative");
    m_localOnDuration = onDuration;
    m_localOffDuration = offDuration;
}

void
CorrelatedLoadController::SetTraceFile(const std::string& fileName)
{
    m_traceFile = fileName;
}

void
CorrelatedLoadController::AddApplication(uint32_t link,
                                         Ptr<ControlledUdpApplication> application)
{
    NS_ABORT_MSG_IF(link >= m_linkCount, "Controller application link is out of range");
    NS_ABORT_MSG_IF(!application, "Cannot register a null background application");
    m_applications[link].push_back(application);
}

int64_t
CorrelatedLoadController::AssignStreams(int64_t stream)
{
    int64_t current = stream;
    for (auto variable : m_commonVariables)
    {
        variable->SetStream(current++);
    }
    for (auto& pair : m_localVariables)
    {
        for (auto variable : pair)
        {
            variable->SetStream(current++);
        }
    }
    return current - stream;
}

void
CorrelatedLoadController::Start(Time start, Time stop)
{
    NS_ABORT_MSG_IF(m_running, "Controller is already running");
    NS_ABORT_MSG_IF(stop <= start, "Controller stop must follow start");
    NS_ABORT_MSG_IF(start < Simulator::Now(), "Controller start cannot be in the past");
    NS_ABORT_MSG_IF(m_mode == CorrelatedLoadMode::TRACE_REPLAY && m_traceFile.empty(),
                    "trace_replay requires a trace file");
    m_running = true;
    m_start = start;
    m_stop = stop;
    m_commonOn = false;
    std::fill(m_localOn.begin(), m_localOn.end(), false);
    std::fill(m_effectiveOn.begin(), m_effectiveOn.end(), false);
    m_transitions.clear();
    if (m_mode == CorrelatedLoadMode::TRACE_REPLAY)
    {
        LoadTrace();
        return;
    }
    if (m_mode == CorrelatedLoadMode::COMMON_BURSTS ||
        m_mode == CorrelatedLoadMode::MIXED_COMMON_AND_INDEPENDENT)
    {
        const Time delay = m_commonOffDuration.GetNanoSeconds() > 0
                               ? m_commonOffDuration
                               : Seconds([&]() {
                                     auto rv = m_commonVariables[1];
                                     rv->SetAttribute("Mean",
                                                      DoubleValue(m_commonOffMean.GetSeconds()));
                                     return rv->GetValue();
                                 }());
        m_events.push_back(Simulator::Schedule(start - Simulator::Now() + delay,
                                               &CorrelatedLoadController::CommonTransition,
                                               this));
    }
    if (m_mode == CorrelatedLoadMode::INDEPENDENT ||
        m_mode == CorrelatedLoadMode::MIXED_COMMON_AND_INDEPENDENT)
    {
        for (uint32_t link = 0; link < m_linkCount; ++link)
        {
            auto rv = m_localVariables[link][1];
            rv->SetAttribute("Mean", DoubleValue(m_localOffMean.GetSeconds()));
            const Time delay = m_localOffDuration.GetNanoSeconds() > 0
                                   ? m_localOffDuration
                                   : Seconds(rv->GetValue());
            m_events.push_back(Simulator::Schedule(start - Simulator::Now() + delay,
                                                   &CorrelatedLoadController::LocalTransition,
                                                   this,
                                                   link));
        }
    }
}

void
CorrelatedLoadController::CommonTransition()
{
    if (!m_running || Simulator::Now() >= m_stop)
    {
        return;
    }
    m_commonOn = !m_commonOn;
    for (uint32_t link = 0; link < m_linkCount; ++link)
    {
        Apply(link, m_commonOn || m_localOn[link], "common", true);
    }
    const Time deterministic = m_commonOn ? m_commonOnDuration : m_commonOffDuration;
    auto rv = m_commonVariables[m_commonOn ? 0 : 1];
    const Time mean = m_commonOn ? m_commonOnMean : m_commonOffMean;
    rv->SetAttribute("Mean", DoubleValue(mean.GetSeconds()));
    const Time delay =
        deterministic.GetNanoSeconds() > 0 ? deterministic : Seconds(rv->GetValue());
    if (Simulator::Now() + delay < m_stop)
    {
        m_events.push_back(
            Simulator::Schedule(delay, &CorrelatedLoadController::CommonTransition, this));
    }
}

void
CorrelatedLoadController::LocalTransition(uint32_t link)
{
    if (!m_running || Simulator::Now() >= m_stop)
    {
        return;
    }
    m_localOn[link] = !m_localOn[link];
    Apply(link, m_commonOn || m_localOn[link], "local", true);
    const Time deterministic = m_localOn[link] ? m_localOnDuration : m_localOffDuration;
    auto rv = m_localVariables[link][m_localOn[link] ? 0 : 1];
    const Time mean = m_localOn[link] ? m_localOnMean : m_localOffMean;
    rv->SetAttribute("Mean", DoubleValue(mean.GetSeconds()));
    const Time delay =
        deterministic.GetNanoSeconds() > 0 ? deterministic : Seconds(rv->GetValue());
    if (Simulator::Now() + delay < m_stop)
    {
        m_events.push_back(
            Simulator::Schedule(delay, &CorrelatedLoadController::LocalTransition, this, link));
    }
}

void
CorrelatedLoadController::Apply(uint32_t link,
                                bool on,
                                const std::string& source,
                                bool force)
{
    if (force || m_effectiveOn[link] != on)
    {
        m_effectiveOn[link] = on;
        m_transitions.push_back({Simulator::Now(), link, on, source});
        for (auto application : m_applications[link])
        {
            application->SetActive(on);
        }
    }
}

void
CorrelatedLoadController::LoadTrace()
{
    std::ifstream input(m_traceFile);
    NS_ABORT_MSG_IF(!input, "Cannot open load trace " << m_traceFile);
    std::string line;
    bool first = true;
    while (std::getline(input, line))
    {
        if (line.empty())
        {
            continue;
        }
        std::replace(line.begin(), line.end(), ',', ' ');
        std::istringstream row(line);
        double timestamp;
        uint32_t link;
        std::string state;
        if (!(row >> timestamp >> link >> state))
        {
            if (first)
            {
                first = false;
                continue;
            }
            NS_ABORT_MSG("Malformed load trace row: " << line);
        }
        first = false;
        NS_ABORT_MSG_IF(timestamp < 0 || link >= m_linkCount,
                        "Invalid load trace event: " << line);
        std::transform(state.begin(), state.end(), state.begin(), [](unsigned char c) {
            return std::tolower(c);
        });
        const bool on = state == "1" || state == "on" || state == "true";
        NS_ABORT_MSG_IF(!on && state != "0" && state != "off" && state != "false",
                        "Invalid load trace state: " << state);
        const Time eventTime = m_start + Seconds(timestamp);
        if (eventTime < m_stop)
        {
            m_events.push_back(Simulator::Schedule(eventTime - Simulator::Now(),
                                                   &CorrelatedLoadController::TraceTransition,
                                                   this,
                                                   link,
                                                   on));
        }
    }
}

void
CorrelatedLoadController::TraceTransition(uint32_t link, bool on)
{
    Apply(link, on, "trace", true);
}

void
CorrelatedLoadController::Stop()
{
    m_running = false;
    for (auto& event : m_events)
    {
        event.Cancel();
    }
    m_events.clear();
    for (uint32_t link = 0; link < m_linkCount; ++link)
    {
        Apply(link, false, "stop", false);
    }
}

const std::vector<LoadTransition>&
CorrelatedLoadController::GetTransitions() const
{
    return m_transitions;
}

bool
CorrelatedLoadController::GetEffectiveState(uint32_t link) const
{
    NS_ABORT_MSG_IF(link >= m_linkCount, "Controller state link is out of range");
    return m_effectiveOn[link];
}

void
CorrelatedLoadController::DoDispose()
{
    Stop();
    m_applications.clear();
    Object::DoDispose();
}

} // namespace ns3
