/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "frame-source.h"

#include "ns3/abort.h"
#include "ns3/double.h"
#include "ns3/log.h"

#include <algorithm>
#include <cmath>
#include <fstream>
#include <limits>
#include <sstream>
#include <stdexcept>

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("FrameSource");
NS_OBJECT_ENSURE_REGISTERED(FrameSource);
NS_OBJECT_ENSURE_REGISTERED(TraceFrameSource);
NS_OBJECT_ENSURE_REGISTERED(SyntheticFrameSource);

namespace
{

std::string
Trim(std::string value)
{
    const auto first = value.find_first_not_of(" \t\r\n");
    if (first == std::string::npos)
    {
        return {};
    }
    const auto last = value.find_last_not_of(" \t\r\n");
    return value.substr(first, last - first + 1);
}

std::vector<std::string>
SplitCsv(const std::string& line)
{
    std::vector<std::string> fields;
    std::stringstream stream(line);
    std::string field;
    while (std::getline(stream, field, ','))
    {
        fields.push_back(Trim(field));
    }
    if (!line.empty() && line.back() == ',')
    {
        fields.emplace_back();
    }
    return fields;
}

uint64_t
ParseUnsigned(const std::string& value, const std::string& name, uint32_t line)
{
    std::size_t used = 0;
    uint64_t result;
    try
    {
        result = std::stoull(value, &used);
    }
    catch (const std::exception&)
    {
        throw std::runtime_error("invalid " + name + " at CSV line " + std::to_string(line));
    }
    if (used != value.size())
    {
        throw std::runtime_error("invalid " + name + " at CSV line " + std::to_string(line));
    }
    return result;
}

} // namespace

TypeId
FrameSource::GetTypeId()
{
    static TypeId tid = TypeId("ns3::FrameSource").SetParent<Object>().SetGroupName("WifiStreaming");
    return tid;
}

int64_t
FrameSource::AssignStreams(int64_t stream)
{
    return 0;
}

TypeId
TraceFrameSource::GetTypeId()
{
    static TypeId tid = TypeId("ns3::TraceFrameSource")
                            .SetParent<FrameSource>()
                            .SetGroupName("WifiStreaming")
                            .AddConstructor<TraceFrameSource>();
    return tid;
}

void
TraceFrameSource::SetFileName(const std::string& fileName)
{
    m_fileName = fileName;
}

std::vector<FrameDescriptor>
TraceFrameSource::GetFrames()
{
    std::ifstream input(m_fileName);
    if (!input)
    {
        throw std::runtime_error("cannot open frame trace: " + m_fileName);
    }

    std::string line;
    if (!std::getline(input, line))
    {
        throw std::runtime_error("empty frame trace: " + m_fileName);
    }
    if (!line.empty() && static_cast<unsigned char>(line[0]) == 0xef)
    {
        line.erase(0, 3); // UTF-8 BOM
    }
    const std::vector<std::string> expected{"frame_id",
                                            "generation_time_us",
                                            "size_bytes",
                                            "frame_type",
                                            "deadline_us"};
    if (SplitCsv(line) != expected)
    {
        throw std::runtime_error("invalid frame trace header");
    }

    std::vector<FrameDescriptor> frames;
    uint32_t lineNumber = 1;
    uint64_t previousTime = 0;
    while (std::getline(input, line))
    {
        ++lineNumber;
        if (Trim(line).empty())
        {
            continue;
        }
        auto fields = SplitCsv(line);
        if (fields.size() != expected.size())
        {
            throw std::runtime_error("expected 5 fields at CSV line " +
                                     std::to_string(lineNumber));
        }
        FrameDescriptor frame;
        frame.frameId = ParseUnsigned(fields[0], "frame_id", lineNumber);
        const uint64_t generationUs =
            ParseUnsigned(fields[1], "generation_time_us", lineNumber);
        if (generationUs > std::numeric_limits<uint64_t>::max() / 1000)
        {
            throw std::runtime_error("generation_time_us overflow at CSV line " +
                                     std::to_string(lineNumber));
        }
        frame.generationTimeNs = generationUs * 1000;
        const uint64_t size = ParseUnsigned(fields[2], "size_bytes", lineNumber);
        const uint64_t deadline = ParseUnsigned(fields[4], "deadline_us", lineNumber);
        if (size == 0 || size > std::numeric_limits<uint32_t>::max() ||
            deadline > std::numeric_limits<uint32_t>::max())
        {
            throw std::runtime_error("frame value out of range at CSV line " +
                                     std::to_string(lineNumber));
        }
        frame.frameSizeBytes = size;
        frame.deadlineUs = deadline;
        frame.frameType = FrameTypeFromString(fields[3]);
        if (!frames.empty() && frame.generationTimeNs < previousTime)
        {
            throw std::runtime_error("generation times are not monotonic at CSV line " +
                                     std::to_string(lineNumber));
        }
        previousTime = frame.generationTimeNs;
        frames.push_back(frame);
    }
    return frames;
}

TypeId
SyntheticFrameSource::GetTypeId()
{
    static TypeId tid = TypeId("ns3::SyntheticFrameSource")
                            .SetParent<FrameSource>()
                            .SetGroupName("WifiStreaming")
                            .AddConstructor<SyntheticFrameSource>();
    return tid;
}

SyntheticFrameSource::SyntheticFrameSource()
    : m_lognormal(CreateObject<LogNormalRandomVariable>()),
      m_empiricalIndex(CreateObject<UniformRandomVariable>())
{
}

void
SyntheticFrameSource::SetFps(double fps)
{
    NS_ABORT_MSG_IF(fps <= 0, "FPS must be positive");
    m_fps = fps;
}

void
SyntheticFrameSource::SetDuration(Time duration)
{
    NS_ABORT_MSG_IF(duration.IsNegative(), "Duration cannot be negative");
    m_duration = duration;
}

void
SyntheticFrameSource::SetConstantFrameSize(uint32_t bytes)
{
    NS_ABORT_MSG_IF(bytes == 0, "Frame size must be positive");
    m_constantSize = bytes;
    m_distribution = FrameSizeDistribution::CONSTANT;
}

void
SyntheticFrameSource::SetLognormalFrameSize(double meanLog, double sigmaLog)
{
    NS_ABORT_MSG_IF(sigmaLog < 0, "Lognormal sigma cannot be negative");
    m_meanLog = meanLog;
    m_sigmaLog = sigmaLog;
    m_distribution = FrameSizeDistribution::LOGNORMAL;
}

void
SyntheticFrameSource::SetEmpiricalFrameSizes(const std::vector<uint32_t>& sizes)
{
    NS_ABORT_MSG_IF(sizes.empty() ||
                        std::any_of(sizes.begin(), sizes.end(), [](uint32_t size) { return size == 0; }),
                    "Empirical sizes must be nonempty and positive");
    m_empiricalSizes = sizes;
    m_distribution = FrameSizeDistribution::EMPIRICAL;
}

void
SyntheticFrameSource::SetGopLength(uint32_t gopLength)
{
    NS_ABORT_MSG_IF(gopLength == 0, "GOP length must be positive");
    m_gopLength = gopLength;
}

void
SyntheticFrameSource::SetKeyframeSizeMultiplier(double multiplier)
{
    NS_ABORT_MSG_IF(!std::isfinite(multiplier) || multiplier < 1.0,
                    "Keyframe size multiplier must be finite and at least one");
    m_keyframeSizeMultiplier = multiplier;
}

void
SyntheticFrameSource::SetDeadline(uint32_t deadlineUs)
{
    m_deadlineUs = deadlineUs;
}

std::vector<FrameDescriptor>
SyntheticFrameSource::GetFrames()
{
    std::vector<FrameDescriptor> frames;
    for (uint64_t id = 0, timeNs = 0; timeNs < static_cast<uint64_t>(m_duration.GetNanoSeconds());
         ++id, timeNs = std::llround(id * 1e9 / m_fps))
    {
        FrameDescriptor frame;
        frame.frameId = id;
        frame.generationTimeNs = timeNs;
        frame.deadlineUs = m_deadlineUs;
        frame.frameType = (id % m_gopLength == 0) ? FrameType::I_FRAME : FrameType::P_FRAME;
        if (m_distribution == FrameSizeDistribution::LOGNORMAL)
        {
            m_lognormal->SetAttribute("Mu", DoubleValue(m_meanLog));
            m_lognormal->SetAttribute("Sigma", DoubleValue(m_sigmaLog));
            frame.frameSizeBytes =
                std::max<uint32_t>(1, std::llround(m_lognormal->GetValue()));
        }
        else if (m_distribution == FrameSizeDistribution::EMPIRICAL)
        {
            const auto index = m_empiricalIndex->GetInteger(0, m_empiricalSizes.size() - 1);
            frame.frameSizeBytes = m_empiricalSizes[index];
        }
        else
        {
            frame.frameSizeBytes = m_constantSize;
        }
        if (frame.frameType == FrameType::I_FRAME)
        {
            const double keyframeSize = frame.frameSizeBytes * m_keyframeSizeMultiplier;
            NS_ABORT_MSG_IF(keyframeSize > std::numeric_limits<uint32_t>::max(),
                            "Keyframe size exceeds uint32_t");
            frame.frameSizeBytes = std::max<uint32_t>(1, std::llround(keyframeSize));
        }
        frames.push_back(frame);
    }
    return frames;
}

int64_t
SyntheticFrameSource::AssignStreams(int64_t stream)
{
    m_lognormal->SetStream(stream);
    m_empiricalIndex->SetStream(stream + 1);
    return 2;
}

} // namespace ns3
