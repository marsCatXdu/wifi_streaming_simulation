/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef FRAME_SOURCE_H
#define FRAME_SOURCE_H

#include "streaming-header.h"

#include "ns3/nstime.h"
#include "ns3/object.h"
#include "ns3/random-variable-stream.h"

#include <string>
#include <vector>

namespace ns3
{

/**
 * Abstract finite source of application frame descriptors.
 */
class FrameSource : public Object
{
  public:
    static TypeId GetTypeId();
    ~FrameSource() override = default;

    virtual std::vector<FrameDescriptor> GetFrames() = 0;
    virtual int64_t AssignStreams(int64_t stream);
};

/**
 * CSV-backed frame source.
 */
class TraceFrameSource : public FrameSource
{
  public:
    static TypeId GetTypeId();

    void SetFileName(const std::string& fileName);
    std::vector<FrameDescriptor> GetFrames() override;

  private:
    std::string m_fileName;
};

enum class FrameSizeDistribution
{
    CONSTANT,
    LOGNORMAL,
    EMPIRICAL
};

/**
 * Deterministic-stream synthetic frame source.
 */
class SyntheticFrameSource : public FrameSource
{
  public:
    static TypeId GetTypeId();
    SyntheticFrameSource();

    void SetFps(double fps);
    void SetDuration(Time duration);
    void SetConstantFrameSize(uint32_t bytes);
    void SetLognormalFrameSize(double meanLog, double sigmaLog);
    void SetEmpiricalFrameSizes(const std::vector<uint32_t>& sizes);
    void SetGopLength(uint32_t gopLength);
    void SetKeyframeSizeMultiplier(double multiplier);
    void SetDeadline(uint32_t deadlineUs);

    std::vector<FrameDescriptor> GetFrames() override;
    int64_t AssignStreams(int64_t stream) override;

  private:
    double m_fps{30.0};
    Time m_duration{Seconds(1)};
    uint32_t m_constantSize{1200};
    double m_meanLog{7.0};
    double m_sigmaLog{0.5};
    std::vector<uint32_t> m_empiricalSizes;
    FrameSizeDistribution m_distribution{FrameSizeDistribution::CONSTANT};
    uint32_t m_gopLength{30};
    double m_keyframeSizeMultiplier{1.0};
    uint32_t m_deadlineUs{33333};
    Ptr<LogNormalRandomVariable> m_lognormal;
    Ptr<UniformRandomVariable> m_empiricalIndex;
};

} // namespace ns3

#endif // FRAME_SOURCE_H
