/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef CORRELATED_LOAD_CONTROLLER_H
#define CORRELATED_LOAD_CONTROLLER_H

#include "ns3/event-id.h"
#include "ns3/nstime.h"
#include "ns3/object.h"
#include "ns3/random-variable-stream.h"

#include <cstdint>
#include <string>
#include <vector>

namespace ns3
{

class ControlledUdpApplication;

enum class CorrelatedLoadMode
{
    INDEPENDENT,
    COMMON_BURSTS,
    MIXED_COMMON_AND_INDEPENDENT,
    TRACE_REPLAY
};

struct LoadTransition
{
    Time time;
    uint32_t link{0};
    bool on{false};
    std::string source;
};

/**
 * Produces explicit common and per-link ON/OFF processes.
 *
 * Effective mixed-mode state is commonOn OR localOn[link].  A common event is
 * delivered to every registered link even when its effective state is
 * unchanged, making simultaneous cross-band activation explicit.
 */
class CorrelatedLoadController : public Object
{
  public:
    static TypeId GetTypeId();

    CorrelatedLoadController();
    ~CorrelatedLoadController() override;

    void SetMode(const std::string& mode);
    CorrelatedLoadMode GetMode() const;
    void SetLinkCount(uint32_t count);
    void SetCommonMeans(Time onMean, Time offMean);
    void SetLocalMeans(Time onMean, Time offMean);
    void SetCommonDeterministicDurations(Time onDuration, Time offDuration);
    void SetLocalDeterministicDurations(Time onDuration, Time offDuration);
    void SetTraceFile(const std::string& fileName);
    void AddApplication(uint32_t link, Ptr<ControlledUdpApplication> application);
    int64_t AssignStreams(int64_t stream);
    void Start(Time start, Time stop);
    void Stop();

    const std::vector<LoadTransition>& GetTransitions() const;
    bool GetEffectiveState(uint32_t link) const;

  private:
    void CommonTransition();
    void LocalTransition(uint32_t link);
    void Apply(uint32_t link, bool on, const std::string& source, bool force);
    void LoadTrace();
    void TraceTransition(uint32_t link, bool on);
    void DoDispose() override;

    CorrelatedLoadMode m_mode{CorrelatedLoadMode::INDEPENDENT};
    uint32_t m_linkCount{2};
    Time m_commonOnMean{MilliSeconds(100)};
    Time m_commonOffMean{MilliSeconds(100)};
    Time m_localOnMean{MilliSeconds(100)};
    Time m_localOffMean{MilliSeconds(100)};
    Time m_commonOnDuration;
    Time m_commonOffDuration;
    Time m_localOnDuration;
    Time m_localOffDuration;
    std::string m_traceFile;
    Time m_start;
    Time m_stop;
    bool m_running{false};
    bool m_commonOn{false};
    std::vector<bool> m_localOn;
    std::vector<bool> m_effectiveOn;
    std::vector<std::vector<Ptr<ControlledUdpApplication>>> m_applications;
    std::vector<Ptr<ExponentialRandomVariable>> m_commonVariables;
    std::vector<std::vector<Ptr<ExponentialRandomVariable>>> m_localVariables;
    std::vector<EventId> m_events;
    std::vector<LoadTransition> m_transitions;
};

} // namespace ns3

#endif // CORRELATED_LOAD_CONTROLLER_H
