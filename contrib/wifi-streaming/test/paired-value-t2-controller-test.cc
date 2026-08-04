/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "temporal-t2-feature-adapter-golden-v1.h"

#include "ns3/csma-module.h"
#include "ns3/frame-receiver.h"
#include "ns3/frame-source.h"
#include "ns3/inet-socket-address.h"
#include "ns3/internet-stack-helper.h"
#include "ns3/ipv4-address-helper.h"
#include "ns3/multipath-sender.h"
#include "ns3/paired-value-t2-controller.h"
#include "ns3/redundancy-policy.h"
#include "ns3/secondary-airtime-meter.h"
#include "ns3/simulator.h"
#include "ns3/string.h"
#include "ns3/test.h"
#include "ns3/udp-socket-factory.h"

#include <algorithm>
#include <array>
#include <bit>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <initializer_list>
#include <limits>
#include <map>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace ns3
{

/**
 * Test-only access to paired-value pure validators and frozen state.
 *
 * @ingroup tests
 */
class PairedValueT2ControllerTestAccess
{
  public:
    /** Return production primary-endpoint validation. */
    static std::optional<std::string> FindPrimaryError(
        Ptr<PairedValueT2Controller> controller,
        const PredictionSample& sample)
    {
        return controller->FindPendingPrimaryError(sample);
    }

    /** Return production hypothetical-secondary endpoint validation. */
    static std::optional<std::string> FindSecondaryError(
        Ptr<PairedValueT2Controller> controller,
        const PredictionSample& sample)
    {
        return controller->FindSecondaryError(sample);
    }

    /** Return production immutable-pair validation. */
    static std::optional<std::string> FindPairError(const PredictionSample& primary,
                                                    const PredictionSample& secondary)
    {
        return PairedValueT2Controller::FindPairError(primary, secondary);
    }

    /** Return production untreated-secondary validation. */
    static std::optional<std::string> FindUntreatedError(
        const PredictionSample& secondary)
    {
        return PairedValueT2Controller::FindUntreatedSecondaryError(secondary);
    }

    /** Return production canonical-descriptor validation. */
    static std::optional<std::string> FindDescriptorError(
        const PredictionSample& primary,
        const DelayedCopyDescriptor& descriptor)
    {
        return PairedValueT2Controller::FindDescriptorError(primary, descriptor);
    }

    /** Return one production status label by frozen ordinal. */
    static std::string_view StatusName(uint8_t ordinal)
    {
        return PairedValueT2Controller::StatusName(
            static_cast<PairedValueT2Controller::DecisionStatus>(ordinal));
    }

    /** Charge a deterministic test-only measured debit before admission. */
    static void DebitGuard(Ptr<PairedValueT2Controller> controller,
                           uint64_t nowNs,
                           double measuredUs)
    {
        NS_ABORT_MSG_IF(!controller->m_guard.DebitMeasuredAirtime(nowNs, measuredUs),
                        "Test-only paired-value guard debit failed");
    }

    /**
     * Return the configured maximum guard horizon.
     *
     * @param controller Controller under test.
     * @return Maximum horizon in microseconds.
     */
    static uint64_t GetGuardMaxHorizonUs(
        Ptr<PairedValueT2Controller> controller)
    {
        return controller->m_guard.GetMaxHorizonUs();
    }

    /**
     * Return the configured maximum guard balance.
     *
     * @param controller Controller under test.
     * @return Maximum balance in microseconds.
     */
    static double GetGuardCapacityUs(Ptr<PairedValueT2Controller> controller)
    {
        return controller->m_guard.GetCapacityUs();
    }

    /**
     * Return the configured startup credit.
     *
     * @param controller Controller under test.
     * @return Startup credit in microseconds.
     */
    static double GetGuardInitialCreditUs(
        Ptr<PairedValueT2Controller> controller)
    {
        return controller->m_guard.GetInitialCreditUs();
    }
};

namespace
{

constexpr uint64_t FRAME_PERIOD_NUMERATOR_NS = 1000000000;
constexpr uint64_t FRAME_PERIOD_DENOMINATOR = 30;
constexpr uint64_t NANOS_PER_MILLISECOND = 1000000;
constexpr uint64_t NANOS_PER_MICROSECOND = 1000;
constexpr uint64_t DEADLINE_US = 33333;

constexpr std::array<std::string_view, 82> DECISION_COLUMNS{
    "schema_version",
    "run_id",
    "frame_id",
    "policy",
    "decision_status",
    "primary_path_id",
    "primary_copy_id",
    "secondary_path_id",
    "secondary_copy_id",
    "sample_stage",
    "sample_offset_us",
    "generation_time_ns",
    "deadline_time_ns",
    "primary_sample_time_ns",
    "secondary_sample_time_ns",
    "primary_feature_watermark_time_ns",
    "primary_feature_watermark_sequence",
    "frame_type",
    "frame_size_bytes",
    "frame_packet_count",
    "primary_actionable",
    "decision_window_start_ns",
    "decision_window_stop_ns",
    "inside_decision_window",
    "history_ready",
    "current_poll_capture_time_ns",
    "current_poll_available_time_ns",
    "lag1_frame_id",
    "lag1_poll_capture_time_ns",
    "lag3_frame_id",
    "lag3_poll_capture_time_ns",
    "lag8_frame_id",
    "lag8_poll_capture_time_ns",
    "feature_evaluated",
    "model_spec_id",
    "model_artifact_sha256",
    "feature_family",
    "feature_count",
    "feature_adapter_id",
    "ordered_feature_names_sha256",
    "ranker",
    "frame_gate",
    "score_adapter_id",
    "score_threshold_float32",
    "primary_bad12_logit",
    "primary_bad12_probability",
    "treated_bad12_logit",
    "treated_bad12_probability",
    "predicted_log_airtime",
    "predicted_secondary_airtime_us",
    "nonnegative_bad12_value",
    "value_per_cost_score_float32",
    "passes_score_threshold",
    "descriptor_checked",
    "descriptor_available",
    "descriptor_frame_packet_count",
    "descriptor_packet_count",
    "descriptor_packet_indices",
    "descriptor_expected_mac_service_bytes",
    "descriptor_deadline_time_ns",
    "canonical_cost_estimator_id",
    "cost_safety_factor",
    "canonical_nominal_airtime_us",
    "canonical_reserved_airtime_us",
    "guard_fraction",
    "guard_max_horizon_us",
    "guard_initial_horizon_us",
    "guard_capacity_us",
    "guard_initial_credit_us",
    "guard_balance_before_us",
    "meter_reserved_before_us",
    "guard_available_before_us",
    "guard_debt_before_us",
    "guard_admission_considered",
    "guard_admitted",
    "launch_attempted",
    "secondary_launched",
    "guard_balance_after_us",
    "meter_reserved_after_us",
    "guard_available_after_us",
    "guard_debt_after_us",
    "learned_cost_token_accounting",
};

std::optional<std::string>
FindDecisionHeaderError(const std::vector<std::string>& header)
{
    if (header.size() != DECISION_COLUMNS.size())
    {
        return "decision column count changed";
    }
    for (std::size_t index = 0; index < DECISION_COLUMNS.size(); ++index)
    {
        if (header[index] != DECISION_COLUMNS[index])
        {
            std::ostringstream error;
            error << "decision column " << index << " is " << header[index]
                  << ", expected " << DECISION_COLUMNS[index];
            return error.str();
        }
    }
    return std::nullopt;
}

/** Minimal typed JSON value used to parse the controller summary. */
struct TestJsonValue
{
    /** JSON value category. */
    enum class Type
    {
        NULL_VALUE,
        BOOLEAN,
        NUMBER,
        STRING,
        ARRAY,
        OBJECT,
    };

    Type type{Type::NULL_VALUE}; ///< Value category
    bool boolean{}; ///< Boolean value
    double number{}; ///< Numeric value
    std::string string; ///< String value
    std::vector<TestJsonValue> array; ///< Array elements
    std::vector<std::pair<std::string, TestJsonValue>> object; ///< Ordered object members
};

/** Strict parser for the JSON subset emitted by the controller summary. */
class TestJsonParser
{
  public:
    /**
     * Construct a parser.
     *
     * @param input Complete JSON document.
     */
    explicit TestJsonParser(std::string_view input)
        : m_input(input)
    {
    }

    /**
     * Parse the complete document.
     *
     * @return Parsed root value.
     */
    TestJsonValue Parse()
    {
        SkipWhitespace();
        auto value = ParseValue();
        SkipWhitespace();
        if (m_position != m_input.size())
        {
            throw std::runtime_error("trailing content after JSON document");
        }
        return value;
    }

  private:
    void SkipWhitespace()
    {
        while (m_position < m_input.size() &&
               std::isspace(static_cast<unsigned char>(m_input[m_position])))
        {
            ++m_position;
        }
    }

    char Take()
    {
        if (m_position == m_input.size())
        {
            throw std::runtime_error("unexpected end of JSON document");
        }
        return m_input[m_position++];
    }

    void Expect(char expected)
    {
        const char actual = Take();
        if (actual != expected)
        {
            throw std::runtime_error("unexpected JSON delimiter");
        }
    }

    bool Consume(std::string_view token)
    {
        if (m_input.substr(m_position, token.size()) != token)
        {
            return false;
        }
        m_position += token.size();
        return true;
    }

    TestJsonValue ParseValue()
    {
        SkipWhitespace();
        if (m_position == m_input.size())
        {
            throw std::runtime_error("missing JSON value");
        }
        switch (m_input[m_position])
        {
        case '{':
            return ParseObject();
        case '[':
            return ParseArray();
        case '"':
        {
            TestJsonValue value;
            value.type = TestJsonValue::Type::STRING;
            value.string = ParseString();
            return value;
        }
        case 't':
            if (Consume("true"))
            {
                TestJsonValue value;
                value.type = TestJsonValue::Type::BOOLEAN;
                value.boolean = true;
                return value;
            }
            break;
        case 'f':
            if (Consume("false"))
            {
                TestJsonValue value;
                value.type = TestJsonValue::Type::BOOLEAN;
                return value;
            }
            break;
        case 'n':
            if (Consume("null"))
            {
                return {};
            }
            break;
        default:
            if (m_input[m_position] == '-' ||
                std::isdigit(static_cast<unsigned char>(m_input[m_position])))
            {
                return ParseNumber();
            }
        }
        throw std::runtime_error("invalid JSON value");
    }

    TestJsonValue ParseObject()
    {
        TestJsonValue value;
        value.type = TestJsonValue::Type::OBJECT;
        Expect('{');
        SkipWhitespace();
        if (m_position < m_input.size() && m_input[m_position] == '}')
        {
            ++m_position;
            return value;
        }
        while (true)
        {
            SkipWhitespace();
            if (m_position == m_input.size() || m_input[m_position] != '"')
            {
                throw std::runtime_error("JSON object key is not a string");
            }
            const std::string key = ParseString();
            SkipWhitespace();
            Expect(':');
            SkipWhitespace();
            const bool duplicate =
                std::any_of(value.object.begin(),
                            value.object.end(),
                            [&key](const auto& entry) { return entry.first == key; });
            if (duplicate)
            {
                throw std::runtime_error("duplicate JSON object key");
            }
            value.object.emplace_back(key, ParseValue());
            SkipWhitespace();
            const char delimiter = Take();
            if (delimiter == '}')
            {
                return value;
            }
            if (delimiter != ',')
            {
                throw std::runtime_error("invalid JSON object delimiter");
            }
        }
    }

    TestJsonValue ParseArray()
    {
        TestJsonValue value;
        value.type = TestJsonValue::Type::ARRAY;
        Expect('[');
        SkipWhitespace();
        if (m_position < m_input.size() && m_input[m_position] == ']')
        {
            ++m_position;
            return value;
        }
        while (true)
        {
            value.array.push_back(ParseValue());
            SkipWhitespace();
            const char delimiter = Take();
            if (delimiter == ']')
            {
                return value;
            }
            if (delimiter != ',')
            {
                throw std::runtime_error("invalid JSON array delimiter");
            }
            SkipWhitespace();
        }
    }

    std::string ParseString()
    {
        Expect('"');
        std::string value;
        while (true)
        {
            const char character = Take();
            if (character == '"')
            {
                return value;
            }
            if (static_cast<unsigned char>(character) < 0x20)
            {
                throw std::runtime_error("control character in JSON string");
            }
            if (character != '\\')
            {
                value.push_back(character);
                continue;
            }
            const char escaped = Take();
            switch (escaped)
            {
            case '"':
            case '\\':
            case '/':
                value.push_back(escaped);
                break;
            case 'b':
                value.push_back('\b');
                break;
            case 'f':
                value.push_back('\f');
                break;
            case 'n':
                value.push_back('\n');
                break;
            case 'r':
                value.push_back('\r');
                break;
            case 't':
                value.push_back('\t');
                break;
            default:
                throw std::runtime_error("unsupported JSON string escape");
            }
        }
    }

    TestJsonValue ParseNumber()
    {
        const std::size_t start = m_position;
        if (m_input[m_position] == '-')
        {
            ++m_position;
        }
        if (m_position == m_input.size() ||
            !std::isdigit(static_cast<unsigned char>(m_input[m_position])))
        {
            throw std::runtime_error("invalid JSON number integer part");
        }
        if (m_input[m_position] == '0')
        {
            ++m_position;
        }
        else
        {
            while (m_position < m_input.size() &&
                   std::isdigit(static_cast<unsigned char>(m_input[m_position])))
            {
                ++m_position;
            }
        }
        if (m_position < m_input.size() && m_input[m_position] == '.')
        {
            ++m_position;
            if (m_position == m_input.size() ||
                !std::isdigit(static_cast<unsigned char>(m_input[m_position])))
            {
                throw std::runtime_error("invalid JSON number fraction");
            }
            while (m_position < m_input.size() &&
                   std::isdigit(static_cast<unsigned char>(m_input[m_position])))
            {
                ++m_position;
            }
        }
        if (m_position < m_input.size() &&
            (m_input[m_position] == 'e' || m_input[m_position] == 'E'))
        {
            ++m_position;
            if (m_position < m_input.size() &&
                (m_input[m_position] == '+' || m_input[m_position] == '-'))
            {
                ++m_position;
            }
            if (m_position == m_input.size() ||
                !std::isdigit(static_cast<unsigned char>(m_input[m_position])))
            {
                throw std::runtime_error("invalid JSON number exponent");
            }
            while (m_position < m_input.size() &&
                   std::isdigit(static_cast<unsigned char>(m_input[m_position])))
            {
                ++m_position;
            }
        }
        TestJsonValue value;
        value.type = TestJsonValue::Type::NUMBER;
        value.number = std::stod(std::string(m_input.substr(start, m_position - start)));
        if (!std::isfinite(value.number))
        {
            throw std::runtime_error("non-finite JSON number");
        }
        return value;
    }

    std::string_view m_input; ///< Complete input document
    std::size_t m_position{}; ///< Next unparsed byte
};

const TestJsonValue&
JsonMember(const TestJsonValue& object, std::string_view key)
{
    if (object.type != TestJsonValue::Type::OBJECT)
    {
        throw std::runtime_error("JSON value is not an object");
    }
    const auto member = std::find_if(object.object.begin(),
                                     object.object.end(),
                                     [key](const auto& entry) { return entry.first == key; });
    if (member == object.object.end())
    {
        throw std::runtime_error("missing JSON object member " + std::string(key));
    }
    return member->second;
}

const TestJsonValue&
JsonPath(const TestJsonValue& root, std::initializer_list<std::string_view> path)
{
    const TestJsonValue* value = &root;
    for (const auto component : path)
    {
        value = &JsonMember(*value, component);
    }
    return *value;
}

std::vector<std::string>
JsonKeys(const TestJsonValue& object)
{
    if (object.type != TestJsonValue::Type::OBJECT)
    {
        throw std::runtime_error("JSON value is not an object");
    }
    std::vector<std::string> keys;
    keys.reserve(object.object.size());
    for (const auto& [key, value] : object.object)
    {
        static_cast<void>(value);
        keys.push_back(key);
    }
    return keys;
}

std::vector<double>
JsonNumbers(const TestJsonValue& array)
{
    if (array.type != TestJsonValue::Type::ARRAY)
    {
        throw std::runtime_error("JSON value is not an array");
    }
    std::vector<double> numbers;
    numbers.reserve(array.array.size());
    for (const auto& value : array.array)
    {
        if (value.type != TestJsonValue::Type::NUMBER)
        {
            throw std::runtime_error("JSON array element is not numeric");
        }
        numbers.push_back(value.number);
    }
    return numbers;
}

const std::string&
JsonString(const TestJsonValue& value)
{
    if (value.type != TestJsonValue::Type::STRING)
    {
        throw std::runtime_error("JSON value is not a string");
    }
    return value.string;
}

double
JsonNumber(const TestJsonValue& value)
{
    if (value.type != TestJsonValue::Type::NUMBER)
    {
        throw std::runtime_error("JSON value is not numeric");
    }
    return value.number;
}

bool
JsonBoolean(const TestJsonValue& value)
{
    if (value.type != TestJsonValue::Type::BOOLEAN)
    {
        throw std::runtime_error("JSON value is not Boolean");
    }
    return value.boolean;
}

std::vector<std::string>
ReadLines(const std::string& path)
{
    std::ifstream input(path);
    std::vector<std::string> lines;
    std::string line;
    while (std::getline(input, line))
    {
        lines.push_back(line);
    }
    return lines;
}

std::string
ReadText(const std::string& path)
{
    std::ifstream input(path);
    std::ostringstream text;
    text << input.rdbuf();
    return text.str();
}

std::vector<std::string>
SplitCsv(const std::string& row)
{
    std::vector<std::string> fields;
    std::istringstream input(row);
    std::string field;
    while (std::getline(input, field, ','))
    {
        fields.push_back(field);
    }
    if (!row.empty() && row.back() == ',')
    {
        fields.emplace_back();
    }
    return fields;
}

std::size_t
FindColumn(const std::vector<std::string>& header, const std::string& name)
{
    const auto found = std::find(header.begin(), header.end(), name);
    NS_ABORT_MSG_IF(found == header.end(), "Test decision column is absent: " << name);
    return std::distance(header.begin(), found);
}

uint64_t
FrameOffsetNs(uint64_t frameId)
{
    return std::llround(static_cast<double>(frameId) * FRAME_PERIOD_NUMERATOR_NS /
                        FRAME_PERIOD_DENOMINATOR);
}

PredictionPollingReport
MakeSimpleReport(uint64_t captureTimeNs, uint64_t counter)
{
    PredictionPollingReport report;
    report.captureTimeNs = captureTimeNs;
    report.availableTimeNs = captureTimeNs + NANOS_PER_MILLISECOND;
    report.mpduTxAttemptsTotal = counter;
    report.mpduPositiveAcksTotal = counter;
    report.mpduTxAttemptFailuresTotal = 0;
    report.mpduRetriesTotal = 0;
    report.mpduTerminalDropsTotal = 0;
    report.mpduRetryLimitDropsTotal = 0;
    report.mpduLifetimeDropsTotal = 0;
    report.mpduQueueDropsTotal = 0;
    report.ppduTxCountTotal = counter;
    for (const uint64_t windowUs : {1000ULL, 5000ULL, 20000ULL})
    {
        PredictionRollingSample rolling;
        rolling.windowUs = windowUs;
        rolling.phyTxFraction = 0.0;
        rolling.phyRxFraction = 0.0;
        rolling.phyBusyFraction = 0.0;
        rolling.phyIdleFraction = 1.0;
        rolling.phyOtherFraction = 0.0;
        rolling.historyCoverageUs = static_cast<double>(windowUs);
        report.rolling.push_back(rolling);
    }
    report.featureSupportMask = "0x3ffffffffdffff";
    return report;
}

PredictionSample
MakeSimplePrimary(const std::string& runId,
                  uint64_t frameId,
                  uint64_t generationTimeNs,
                  bool actionable = true)
{
    PredictionSample sample;
    sample.runId = runId;
    sample.key = {frameId,
                  PairedValueT2Controller::PRIMARY_PATH_ID,
                  PairedValueT2Controller::PRIMARY_COPY_ID};
    sample.sampleStage = "T2";
    sample.sampleOffsetUs = PairedValueT2Controller::T2_OFFSET_US;
    sample.sampleTimeNs = generationTimeNs + 2000 * NANOS_PER_MICROSECOND;
    sample.generationTimeNs = generationTimeNs;
    sample.deadlineTimeNs = generationTimeNs + DEADLINE_US * NANOS_PER_MICROSECOND;
    sample.frameAgeUs = 2000;
    sample.deadlineSlackUs = DEADLINE_US - 2000;
    sample.frameType = frameId % 60 == 0 ? FrameType::I_FRAME : FrameType::P_FRAME;
    sample.frameSizeBytes = sample.frameType == FrameType::I_FRAME ? 48000 : 12000;
    sample.framePacketCount = sample.frameSizeBytes / 1200;
    sample.senderMacComplete = !actionable;
    sample.actionable = actionable;
    sample.packetsSubmitted = sample.framePacketCount;
    sample.applicationSocketPacketBytesSubmitted =
        sample.frameSizeBytes + 50ULL * sample.framePacketCount;
    sample.packetsRemainingToSubmit = 0;
    sample.featureSupportMask = "0x3ffffffffdffff";
    const uint64_t captureTimeNs =
        ((sample.sampleTimeNs / NANOS_PER_MILLISECOND) - 1) * NANOS_PER_MILLISECOND;
    sample.pollingReport = MakeSimpleReport(captureTimeNs, frameId);
    return sample;
}

PredictionSample
MakeSecondary(const PredictionSample& primary)
{
    PredictionSample secondary = primary;
    secondary.key.pathId = PairedValueT2Controller::SECONDARY_PATH_ID;
    secondary.key.copyId = PairedValueT2Controller::SECONDARY_COPY_ID;
    secondary.senderMacComplete = false;
    secondary.actionable = true;
    secondary.packetsSubmitted = 0;
    secondary.applicationSocketPacketBytesSubmitted = 0;
    secondary.packetsRemainingToSubmit = secondary.framePacketCount;
    secondary.framePacketsMacEnqueued = 0;
    secondary.framePacketsMacDequeued = 0;
    secondary.framePacketsTxSucceeded = 0;
    secondary.frameMpduAttemptFailures = 0;
    secondary.framePacketsTerminallyDropped = 0;
    secondary.framePacketsCurrentlyQueued = 0;
    secondary.frameMacServiceBytesCurrentlyQueued = 0;
    // These path-wide values are deliberately malformed.  The controller is
    // contractually forbidden from consulting them on the secondary endpoint.
    secondary.featureSupportMask = "secondary-path-state-must-be-ignored";
    secondary.latestFeatureEventTimeNs = std::numeric_limits<uint64_t>::max();
    secondary.latestFeatureEventSequence = std::numeric_limits<uint64_t>::max();
    secondary.mpduTxAttemptsTotal = std::nullopt;
    secondary.pollingReport = std::nullopt;
    secondary.rolling.clear();
    return secondary;
}

void
SchedulePair(Ptr<PairedValueT2Controller> controller,
             const PredictionSample& primary)
{
    const auto secondary = MakeSecondary(primary);
    Simulator::Schedule(NanoSeconds(primary.sampleTimeNs),
                        &PairedValueT2Controller::NotifySnapshot,
                        controller,
                        primary);
    Simulator::Schedule(NanoSeconds(primary.sampleTimeNs),
                        &PairedValueT2Controller::NotifySnapshot,
                        controller,
                        secondary);
}

void
SchedulePairThenMutateCallerReport(Ptr<PairedValueT2Controller> controller,
                                   PredictionSample primary)
{
    const auto secondary = MakeSecondary(primary);
    Simulator::Schedule(NanoSeconds(primary.sampleTimeNs),
                        [controller, primary = std::move(primary), secondary]() mutable {
                            controller->NotifySnapshot(primary);
                            NS_ABORT_MSG_IF(!primary.pollingReport,
                                            "Stateful test primary report disappeared");
                            primary.pollingReport->captureTimeNs += 777000000;
                            primary.pollingReport->availableTimeNs += 777000000;
                            primary.pollingReport->mpduTxAttemptsTotal =
                                std::numeric_limits<uint64_t>::max();
                            primary.pollingReport->rolling.clear();
                            controller->NotifySnapshot(secondary);
                        });
}

struct OutputPaths
{
    std::string decisions;
    std::string controllerSummary;
    std::string meterEvents;
    std::string meterSettlements;
    std::string meterSummary;
};

OutputPaths
MakePaths(const std::string& label)
{
    const auto base = std::filesystem::temp_directory_path();
    OutputPaths paths{
        (base / ("ns3-paired-value-" + label + "-decisions.csv")).string(),
        (base / ("ns3-paired-value-" + label + "-summary.json")).string(),
        (base / ("ns3-paired-value-" + label + "-meter-events.csv")).string(),
        (base / ("ns3-paired-value-" + label + "-meter-settlements.csv")).string(),
        (base / ("ns3-paired-value-" + label + "-meter-summary.json")).string(),
    };
    for (const auto* path : {&paths.decisions,
                             &paths.controllerSummary,
                             &paths.meterEvents,
                             &paths.meterSettlements,
                             &paths.meterSummary})
    {
        std::remove(path->c_str());
    }
    return paths;
}

void
RemovePaths(const OutputPaths& paths)
{
    for (const auto* path : {&paths.decisions,
                             &paths.controllerSummary,
                             &paths.meterEvents,
                             &paths.meterSettlements,
                             &paths.meterSummary})
    {
        std::remove(path->c_str());
    }
}

struct ControllerSetup
{
    Ptr<MultipathSender> sender;
    Ptr<SecondaryAirtimeMeter> meter;
    Ptr<PairedValueT2Controller> controller;
    OutputPaths paths;
};

ControllerSetup
ConfigureBareController(
    const std::string& label,
    const std::string& runId,
    PairedValueT2Controller::AdmissionProfile admissionProfile =
        PairedValueT2Controller::AdmissionProfile::BASELINE_V1)
{
    auto sender = CreateObject<MultipathSender>();
    auto meter = CreateObject<SecondaryAirtimeMeter>();
    auto controller = CreateObject<PairedValueT2Controller>();
    const auto paths = MakePaths(label);
    meter->SetOutputFiles(runId,
                          paths.meterEvents,
                          paths.meterSettlements,
                          paths.meterSummary);
    controller->SetAdmissionProfile(admissionProfile);
    controller->SetSender(PeekPointer(sender));
    controller->SetAirtimeMeter(meter);
    controller->SetOutputFiles(runId, paths.decisions, paths.controllerSummary);
    return {sender, meter, controller, paths};
}

using GoldenCase =
    temporal_t2_feature_adapter_golden_v1::TemporalT2FeatureAdapterGoldenCase;

PredictionSample
MakeGoldenHistoryPrimary(const GoldenCase& golden,
                         const std::string& runId,
                         uint64_t frameId)
{
    if (frameId == 8)
    {
        auto sample = golden.currentSample;
        sample.runId = runId;
        sample.key.frameId = 8;
        return sample;
    }

    const uint64_t startTimeNs =
        golden.currentSample.generationTimeNs - FrameOffsetNs(8);
    uint64_t sampleTimeNs =
        startTimeNs + FrameOffsetNs(frameId) + 2000 * NANOS_PER_MICROSECOND;
    PredictionPollingReport report;
    if (frameId == 0)
    {
        sampleTimeNs = golden.lags[2].sourceSampleTimeNs;
        report = golden.lags[2].report;
    }
    else if (frameId == 5)
    {
        sampleTimeNs = golden.lags[1].sourceSampleTimeNs;
        report = golden.lags[1].report;
    }
    else if (frameId == 7)
    {
        sampleTimeNs = golden.lags[0].sourceSampleTimeNs;
        report = golden.lags[0].report;
    }
    else
    {
        const std::size_t base = frameId < 5 ? 2 : (frameId < 7 ? 1 : 0);
        report = golden.lags[base].report;
        report.captureTimeNs =
            ((sampleTimeNs / NANOS_PER_MILLISECOND) - 1) * NANOS_PER_MILLISECOND;
        report.availableTimeNs = report.captureTimeNs + NANOS_PER_MILLISECOND;
    }

    auto sample = MakeSimplePrimary(runId,
                                    frameId,
                                    sampleTimeNs - 2000 * NANOS_PER_MICROSECOND);
    sample.sampleTimeNs = sampleTimeNs;
    sample.generationTimeNs = sampleTimeNs - 2000 * NANOS_PER_MICROSECOND;
    sample.deadlineTimeNs = sample.generationTimeNs +
                            DEADLINE_US * NANOS_PER_MICROSECOND;
    sample.latestFeatureEventTimeNs = report.latestFeatureEventTimeNs;
    sample.latestFeatureEventSequence = report.latestFeatureEventSequence;
    sample.pollingReport = std::move(report);
    return sample;
}

ControllerSetup
ConfigureGoldenSender(const GoldenCase& golden,
                      const std::string& label,
                      const std::string& runId,
                      uint16_t port,
                      PairedValueT2Controller::AdmissionProfile admissionProfile =
                          PairedValueT2Controller::AdmissionProfile::BASELINE_V1)
{
    NodeContainer nodes;
    nodes.Create(2);
    InternetStackHelper internet;
    internet.Install(nodes);
    CsmaHelper csma;
    csma.SetChannelAttribute("DataRate", StringValue("100Mbps"));
    csma.SetChannelAttribute("Delay", TimeValue(MicroSeconds(10)));
    const auto devices = csma.Install(nodes);
    Ipv4AddressHelper address;
    address.SetBase("10.92.0.0", "255.255.255.0");
    const auto interfaces = address.Assign(devices);

    auto receiver = CreateObject<FrameReceiver>();
    receiver->SetLocal(InetSocketAddress(Ipv4Address::GetAny(), port));
    receiver->SetHoldForDelayedSecondary(true);
    nodes.Get(1)->AddApplication(receiver);
    receiver->SetStartTime(Time());
    receiver->SetStopTime(NanoSeconds(golden.currentSample.sampleTimeNs + 1000000000));

    auto source = CreateObject<SyntheticFrameSource>();
    source->SetFps(30);
    source->SetDuration(MilliSeconds(300));
    source->SetConstantFrameSize(12000);
    source->SetKeyframeSizeMultiplier(4);
    source->SetGopLength(60);
    source->SetDeadline(DEADLINE_US);

    auto policy = CreateObject<SelectiveDuplicationPolicy>();
    policy->SetPrimaryPath(PairedValueT2Controller::PRIMARY_PATH_ID);
    auto sender = CreateObject<MultipathSender>();
    sender->SetFrameSource(source);
    sender->SetPacketPayloadSize(1200);
    sender->SetExpectedMacServiceOverhead(36);
    sender->SetEmissionMode(EmissionMode::BURST);
    sender->SetPolicy(policy);
    sender->SetDelayedSecondaryPath(PairedValueT2Controller::SECONDARY_PATH_ID);
    for (uint8_t pathId = 0; pathId < 2; ++pathId)
    {
        auto socket = Socket::CreateSocket(nodes.Get(0), UdpSocketFactory::GetTypeId());
        NS_ABORT_MSG_IF(socket->Bind(InetSocketAddress(interfaces.GetAddress(0), 0)) != 0,
                        "Paired-value test sender bind failed");
        NS_ABORT_MSG_IF(
            socket->Connect(InetSocketAddress(interfaces.GetAddress(1), port)) != 0,
            "Paired-value test sender connect failed");
        sender->AddPath(pathId, socket, devices.Get(0));
    }
    nodes.Get(0)->AddApplication(sender);
    const uint64_t senderStartNs =
        golden.currentSample.generationTimeNs - FrameOffsetNs(8);
    sender->SetStartTime(NanoSeconds(senderStartNs));
    sender->SetStopTime(NanoSeconds(golden.currentSample.sampleTimeNs + 500000000));

    auto meter = CreateObject<SecondaryAirtimeMeter>();
    auto controller = CreateObject<PairedValueT2Controller>();
    const auto paths = MakePaths(label);
    meter->SetOutputFiles(runId,
                          paths.meterEvents,
                          paths.meterSettlements,
                          paths.meterSummary);
    controller->SetAdmissionProfile(admissionProfile);
    controller->SetSender(PeekPointer(sender));
    controller->SetAirtimeMeter(meter);
    controller->SetOutputFiles(runId, paths.decisions, paths.controllerSummary);
    return {sender, meter, controller, paths};
}

} // namespace

/**
 * Exercise the real compiled score, launch, meter debit, row, and summary path.
 *
 * @ingroup tests
 */
class PairedValueT2ClosedLoopTestCase : public TestCase
{
  public:
    PairedValueT2ClosedLoopTestCase()
        : TestCase("Paired-value T2 executes the exact-threshold action and reconciles airtime")
    {
    }

  private:
    void AssertKeys(const TestJsonValue& object,
                    std::initializer_list<std::string_view> expected,
                    std::string_view label)
    {
        const auto actual = JsonKeys(object);
        NS_TEST_ASSERT_MSG_EQ(actual.size(),
                              expected.size(),
                              label << " JSON object key count changed");
        if (actual.size() != expected.size())
        {
            return;
        }
        std::size_t index = 0;
        for (const auto key : expected)
        {
            NS_TEST_ASSERT_MSG_EQ(actual[index],
                                  std::string(key),
                                  label << " JSON key order changed at " << index);
            ++index;
        }
    }

    void AssertSummary(const ControllerSetup& setup, const std::string& runId)
    {
        const auto summary = TestJsonParser(ReadText(setup.paths.controllerSummary)).Parse();
        AssertKeys(summary,
                   {"schema_version",
                    "run_id",
                    "policy",
                    "runtime_contract_id",
                    "runtime_contract_sha256",
                    "source_artifacts",
                    "model",
                    "telemetry",
                    "decision_window",
                    "budget_guard",
                    "counts",
                    "airtime",
                    "integrity"},
                   "top-level summary");
        NS_TEST_ASSERT_MSG_EQ(JsonNumber(JsonPath(summary, {"schema_version"})),
                              1.0,
                              "Summary schema version changed");
        NS_TEST_ASSERT_MSG_EQ(JsonString(JsonPath(summary, {"run_id"})),
                              runId,
                              "Summary run ID changed");
        NS_TEST_ASSERT_MSG_EQ(JsonString(JsonPath(summary, {"policy"})),
                              "paired_value_duplication_t2",
                              "Summary policy changed");
        NS_TEST_ASSERT_MSG_EQ(
            JsonString(JsonPath(summary, {"runtime_contract_id"})),
            "paired-value-duplication-t2-runtime-v1",
            "Summary runtime-contract ID changed");
        NS_TEST_ASSERT_MSG_EQ(
            JsonString(JsonPath(summary, {"runtime_contract_sha256"})),
            "b9b9caf6cf49e73cb0669107576a17790f59bda4875c43f676caa426393dbf41",
            "Summary runtime-contract hash changed");

        const auto& artifacts = JsonPath(summary, {"source_artifacts"});
        AssertKeys(artifacts,
                   {"frozen_selection",
                    "canonical_fit_manifest",
                    "canonical_model_pickle",
                    "canonical_candidates",
                    "canonical_metrics"},
                   "source artifacts");
        struct ExpectedArtifact
        {
            std::string_view key; ///< Summary member name
            std::string_view path; ///< Frozen source path
            std::string_view sha256; ///< Frozen source digest
        };
        const std::array<ExpectedArtifact, 5> expectedArtifacts{{
            {"frozen_selection",
             "experiments/model-selection/temporal-t2-primary-only-two-objective-v1.json",
             "c7f886a4ca1a29b9fbd2e25d19d78f994d7136ecdea4f6a16db77eacacf5ce9f"},
            {"canonical_fit_manifest",
             "results/randomized_full_copy_exploration_collection_v1/"
             "temporal_t2_primary_only_two_objective_v1/artifact_manifest.json",
             "b3af02b647c7671a631f3d43ebece75781989889358c845335d4003610a8208f"},
            {"canonical_model_pickle",
             "results/randomized_full_copy_exploration_collection_v1/"
             "temporal_t2_primary_only_two_objective_v1/temporal_t2_value_models.pkl",
             "dff01b0f8319320489709c4039d97011f35439aa92adedbe167fe61b9de7bcb8"},
            {"canonical_candidates",
             "results/randomized_full_copy_exploration_collection_v1/"
             "temporal_t2_primary_only_two_objective_v1/"
             "temporal_t2_value_policy_candidates.csv",
             "7cbd5c622838df0a2f752c3bf9f4c54f333f7d280a9240cb80eda19efb1c28bb"},
            {"canonical_metrics",
             "results/randomized_full_copy_exploration_collection_v1/"
             "temporal_t2_primary_only_two_objective_v1/"
             "temporal_t2_value_training_metrics.json",
             "35929f0638b03ec79f2f3967dd947265c3d73b7fa51f487299cc1d96a555a014"},
        }};
        for (const auto& expected : expectedArtifacts)
        {
            const auto& artifact = JsonMember(artifacts, expected.key);
            AssertKeys(artifact, {"path", "sha256"}, expected.key);
            NS_TEST_ASSERT_MSG_EQ(JsonString(JsonMember(artifact, "path")),
                                  expected.path,
                                  expected.key << " source path changed");
            NS_TEST_ASSERT_MSG_EQ(JsonString(JsonMember(artifact, "sha256")),
                                  expected.sha256,
                                  expected.key << " source hash changed");
        }

        const auto& model = JsonPath(summary, {"model"});
        AssertKeys(model,
                   {"model_spec_id",
                    "artifact_sha256",
                    "feature_family",
                    "feature_count",
                    "feature_adapter_id",
                    "ordered_feature_names_sha256",
                    "ranker",
                    "frame_gate",
                    "score_adapter_id",
                    "score_threshold_float32",
                    "score_threshold_float32_bits_hex"},
                   "model");
        const std::array<std::pair<std::string_view, std::string_view>, 9>
            expectedModelStrings{{
                {"model_spec_id", "hgb64_depth3_7leaf_two_head_ridge_log_cost_v1"},
                {"artifact_sha256",
                 "dff01b0f8319320489709c4039d97011f35439aa92adedbe167fe61b9de7bcb8"},
                {"feature_family", "primary_compact_physics_temporal"},
                {"feature_adapter_id", "finite_numeric_float32_then_float64_one_hot_v1"},
                {"ordered_feature_names_sha256",
                 "a00ebbb9807f99972f2cd009d1b2a20bf0b001cee123ac60d5121b2b1c07209e"},
                {"ranker", "legacy_bad12_value_per_cost"},
                {"frame_gate", "p_frames_only"},
                {"score_adapter_id", "final_candidate_float32_threshold_ge_v1"},
                {"score_threshold_float32_bits_hex", "0x38bbc0e5"},
            }};
        for (const auto& [key, value] : expectedModelStrings)
        {
            NS_TEST_ASSERT_MSG_EQ(JsonString(JsonMember(model, key)),
                                  value,
                                  key << " model metadata changed");
        }
        NS_TEST_ASSERT_MSG_EQ(JsonNumber(JsonMember(model, "feature_count")),
                              246.0,
                              "Model feature count changed");
        NS_TEST_ASSERT_MSG_EQ_TOL(
            JsonNumber(JsonMember(model, "score_threshold_float32")),
            8.952784264693037e-05,
            1e-18,
            "Model threshold changed");

        const auto& telemetry = JsonPath(summary, {"telemetry"});
        AssertKeys(telemetry,
                   {"telemetry_schema_version",
                    "polling_schema_version",
                    "feature_support_mask_version",
                    "primary_required_support_mask_hex",
                    "sample_offsets_us",
                    "history_windows_us",
                    "polling_interval_us",
                    "polling_report_delay_us",
                    "raw_prediction_event_log_enabled",
                    "oracle_features_enabled"},
                   "telemetry");
        NS_TEST_ASSERT_MSG_EQ(JsonNumber(JsonMember(telemetry, "telemetry_schema_version")),
                              3.0,
                              "Telemetry schema version changed");
        NS_TEST_ASSERT_MSG_EQ(JsonNumber(JsonMember(telemetry, "polling_schema_version")),
                              1.0,
                              "Polling schema version changed");
        NS_TEST_ASSERT_MSG_EQ(
            JsonNumber(JsonMember(telemetry, "feature_support_mask_version")),
            2.0,
            "Support-mask version changed");
        NS_TEST_ASSERT_MSG_EQ(
            JsonString(JsonMember(telemetry, "primary_required_support_mask_hex")),
            "0x3ffffffffdffff",
            "Primary support mask changed");
        const auto sampleOffsets = JsonNumbers(JsonMember(telemetry, "sample_offsets_us"));
        NS_TEST_ASSERT_MSG_EQ(sampleOffsets.size(), 2, "Sample-offset array size changed");
        if (sampleOffsets.size() == 2)
        {
            NS_TEST_ASSERT_MSG_EQ(sampleOffsets[0], 0.0, "T0 offset changed");
            NS_TEST_ASSERT_MSG_EQ(sampleOffsets[1], 2000.0, "T2 offset changed");
        }
        const auto historyWindows = JsonNumbers(JsonMember(telemetry, "history_windows_us"));
        const std::array<double, 3> expectedHistoryWindows{1000, 5000, 20000};
        NS_TEST_ASSERT_MSG_EQ(historyWindows.size(),
                              expectedHistoryWindows.size(),
                              "History-window array size changed");
        if (historyWindows.size() == expectedHistoryWindows.size())
        {
            for (std::size_t index = 0; index < historyWindows.size(); ++index)
            {
                NS_TEST_ASSERT_MSG_EQ(historyWindows[index],
                                      expectedHistoryWindows[index],
                                      "History window changed at " << index);
            }
        }
        NS_TEST_ASSERT_MSG_EQ(JsonNumber(JsonMember(telemetry, "polling_interval_us")),
                              1000.0,
                              "Polling interval changed");
        NS_TEST_ASSERT_MSG_EQ(JsonNumber(JsonMember(telemetry, "polling_report_delay_us")),
                              1000.0,
                              "Polling report delay changed");
        NS_TEST_ASSERT_MSG_EQ(
            JsonBoolean(JsonMember(telemetry, "raw_prediction_event_log_enabled")),
            false,
            "Raw prediction-event log unexpectedly enabled");
        NS_TEST_ASSERT_MSG_EQ(JsonBoolean(JsonMember(telemetry, "oracle_features_enabled")),
                              false,
                              "Oracle features unexpectedly enabled");

        const auto& window = JsonPath(summary, {"decision_window"});
        AssertKeys(window,
                   {"measurement_start_ns",
                    "measurement_stop_ns",
                    "decision_start_ns",
                    "decision_stop_ns",
                    "interval_semantics",
                    "decision_stop_guard_us"},
                   "decision window");
        const std::array<std::pair<std::string_view, double>, 5> expectedWindowNumbers{{
            {"measurement_start_ns", 1000000000.0},
            {"measurement_stop_ns", 61000000000.0},
            {"decision_start_ns", 1000000000.0},
            {"decision_stop_ns", 60466000000.0},
            {"decision_stop_guard_us", 534000.0},
        }};
        for (const auto& [key, value] : expectedWindowNumbers)
        {
            NS_TEST_ASSERT_MSG_EQ(JsonNumber(JsonMember(window, key)),
                                  value,
                                  key << " decision-window value changed");
        }
        NS_TEST_ASSERT_MSG_EQ(JsonString(JsonMember(window, "interval_semantics")),
                              "half_open",
                              "Decision-window interval semantics changed");

        const auto& guard = JsonPath(summary, {"budget_guard"});
        AssertKeys(guard,
                   {"canonical_estimator_id",
                    "cost_safety_factor",
                    "fraction",
                    "max_horizon_us",
                    "initial_horizon_us",
                    "capacity_us",
                    "initial_credit_us",
                    "initialization_time_ns",
                    "accounting_absolute_tolerance_us"},
                   "budget guard");
        NS_TEST_ASSERT_MSG_EQ(
            JsonString(JsonMember(guard, "canonical_estimator_id")),
            "eht_mcs5_20mhz_gi800_nss1_one_ppdu_safety125_v1",
            "Canonical estimator ID changed");
        const std::array<std::pair<std::string_view, double>, 8> expectedGuardNumbers{{
            {"cost_safety_factor", 1.25},
            {"fraction", 0.006},
            {"max_horizon_us", 10000000.0},
            {"initial_horizon_us", 2000000.0},
            {"capacity_us", 60000.0},
            {"initial_credit_us", 12000.0},
            {"initialization_time_ns", 1000000000.0},
            {"accounting_absolute_tolerance_us", 1e-9},
        }};
        for (const auto& [key, value] : expectedGuardNumbers)
        {
            NS_TEST_ASSERT_MSG_EQ_TOL(JsonNumber(JsonMember(guard, key)),
                                      value,
                                      1e-15,
                                      key << " budget-guard value changed");
        }

        const auto& counts = JsonPath(summary, {"counts"});
        AssertKeys(counts,
                   {"generated_frames",
                    "paired_t2_frames",
                    "outside_decision_window",
                    "history_warmup",
                    "frame_type_restricted",
                    "not_actionable",
                    "descriptor_unavailable",
                    "feature_evaluated",
                    "below_score_threshold",
                    "score_threshold_passed",
                    "airtime_guard_rejected",
                    "launch_attempted",
                    "launch_rejected",
                    "secondary_launched",
                    "secondary_settled"},
                   "counts");
        const std::array<std::pair<std::string_view, double>, 15> expectedCounts{{
            {"generated_frames", 9},
            {"paired_t2_frames", 9},
            {"outside_decision_window", 0},
            {"history_warmup", 8},
            {"frame_type_restricted", 0},
            {"not_actionable", 0},
            {"descriptor_unavailable", 0},
            {"feature_evaluated", 1},
            {"below_score_threshold", 0},
            {"score_threshold_passed", 1},
            {"airtime_guard_rejected", 0},
            {"launch_attempted", 1},
            {"launch_rejected", 0},
            {"secondary_launched", 1},
            {"secondary_settled", 1},
        }};
        for (const auto& [key, value] : expectedCounts)
        {
            NS_TEST_ASSERT_MSG_EQ(JsonNumber(JsonMember(counts, key)),
                                  value,
                                  key << " summary count changed");
        }

        const auto& airtime = JsonPath(summary, {"airtime"});
        AssertKeys(airtime,
                   {"learned_predicted_cost_sum_evaluated_us",
                    "learned_predicted_cost_sum_launched_us",
                    "canonical_nominal_launched_sum_us",
                    "canonical_reserved_launched_sum_us",
                    "measured_secondary_airtime_debited_us"},
                   "airtime");
        const double learnedEvaluated = JsonNumber(
            JsonMember(airtime, "learned_predicted_cost_sum_evaluated_us"));
        const double learnedLaunched = JsonNumber(
            JsonMember(airtime, "learned_predicted_cost_sum_launched_us"));
        const double nominal =
            JsonNumber(JsonMember(airtime, "canonical_nominal_launched_sum_us"));
        const double reserved =
            JsonNumber(JsonMember(airtime, "canonical_reserved_launched_sum_us"));
        const double measured =
            JsonNumber(JsonMember(airtime, "measured_secondary_airtime_debited_us"));
        for (const double value : {learnedEvaluated,
                                   learnedLaunched,
                                   nominal,
                                   reserved,
                                   measured})
        {
            NS_TEST_ASSERT_MSG_EQ(std::isfinite(value) && value >= 0,
                                  true,
                                  "Summary airtime is invalid");
        }
        NS_TEST_ASSERT_MSG_EQ_TOL(learnedEvaluated,
                                  learnedLaunched,
                                  1e-12,
                                  "Learned evaluated and launched sums differ");
        NS_TEST_ASSERT_MSG_EQ_TOL(reserved,
                                  1.25 * nominal,
                                  1e-9,
                                  "Canonical reserved and nominal sums differ");
        NS_TEST_ASSERT_MSG_EQ_TOL(measured,
                                  1000.0,
                                  1e-12,
                                  "Measured guard debit changed");

        const auto& integrity = JsonPath(summary, {"integrity"});
        AssertKeys(integrity,
                   {"pending_pair_empty",
                    "generated_equals_paired",
                    "status_counts_reconcile",
                    "launches_equal_settlements",
                    "launched_frame_ids_equal_duplicated_frame_ids",
                    "meter_reserved_final_within_tolerance",
                    "meter_reserved_final_raw_us",
                    "meter_reserved_final_normalized_us",
                    "learned_cost_used_for_token_accounting"},
                   "integrity");
        for (const auto key : {"pending_pair_empty",
                               "generated_equals_paired",
                               "status_counts_reconcile",
                               "launches_equal_settlements",
                               "launched_frame_ids_equal_duplicated_frame_ids",
                               "meter_reserved_final_within_tolerance"})
        {
            NS_TEST_ASSERT_MSG_EQ(JsonBoolean(JsonMember(integrity, key)),
                                  true,
                                  key << " integrity proof failed");
        }
        NS_TEST_ASSERT_MSG_EQ(
            JsonBoolean(JsonMember(integrity, "learned_cost_used_for_token_accounting")),
            false,
            "Learned cost entered token accounting");
        NS_TEST_ASSERT_MSG_EQ_TOL(
            JsonNumber(JsonMember(integrity, "meter_reserved_final_raw_us")),
            0.0,
            1e-9,
            "Raw final reservation exceeds tolerance");
        NS_TEST_ASSERT_MSG_EQ(
            JsonNumber(JsonMember(integrity, "meter_reserved_final_normalized_us")),
            0.0,
            "Normalized final reservation is not exact zero");
    }

    void DoRun() override
    {
        const auto& golden =
            temporal_t2_feature_adapter_golden_v1::GetCases()[0];
        const std::string runId = "paired-value-action";
        auto setup = ConfigureGoldenSender(golden, "action", runId, 9291);
        for (uint64_t frameId = 0; frameId <= 8; ++frameId)
        {
            SchedulePair(setup.controller,
                         MakeGoldenHistoryPrimary(golden, runId, frameId));
        }
        const uint64_t decisionTimeNs = golden.currentSample.sampleTimeNs;
        bool requestAndReservationPrecededZeroDelaySend = false;
        Simulator::Schedule(
            NanoSeconds(decisionTimeNs),
            [&setup, &requestAndReservationPrecededZeroDelaySend]() {
                const bool requestAccepted =
                    !setup.sender->GetDelayedSecondaryCopyDescriptor(8).has_value();
                const bool meterRegistered = setup.meter->GetReservedAirtimeUs() > 0;
                const bool zeroDelaySendPending = setup.sender->GetRedundantBytesSent() == 0;
                requestAndReservationPrecededZeroDelaySend =
                    requestAccepted && meterRegistered && zeroDelaySendPending;
            });
        Simulator::Schedule(NanoSeconds(decisionTimeNs + 1), [meter = setup.meter]() {
            meter->ApplyTestPpdu({{8, 1200}}, 1000.0, 0);
        });
        Simulator::Schedule(NanoSeconds(decisionTimeNs + 2),
                            &SecondaryAirtimeMeter::WriteSummary,
                            setup.meter);
        Simulator::Schedule(NanoSeconds(decisionTimeNs + 3), [controller = setup.controller]() {
            controller->WriteSummary(9, {8});
        });
        Simulator::Stop(NanoSeconds(decisionTimeNs + 4));
        Simulator::Run();

        NS_TEST_ASSERT_MSG_EQ(setup.controller->GetPairedFrameCount(),
                              9,
                              "Every generated frame must form one pair");
        NS_TEST_ASSERT_MSG_EQ(setup.controller->GetFeatureEvaluationCount(),
                              1,
                              "Only the history-ready P frame may be evaluated");
        NS_TEST_ASSERT_MSG_EQ(setup.controller->GetLaunchCount(),
                              1,
                              "Exact-threshold fixture did not launch");
        NS_TEST_ASSERT_MSG_EQ(setup.controller->GetSettlementCount(),
                              1,
                              "Launched frame did not settle");
        NS_TEST_ASSERT_MSG_EQ(requestAndReservationPrecededZeroDelaySend,
                              true,
                              "Meter registration did not follow Request before zero-delay send");
        NS_TEST_ASSERT_MSG_GT(setup.sender->GetRedundantBytesSent(),
                              0,
                              "Accepted zero-delay secondary sends did not execute");
        NS_TEST_ASSERT_MSG_EQ_TOL(setup.meter->GetMeasuredAirtimeTotalUs(),
                                  1000.0,
                                  1e-12,
                                  "Measured allocation was not recorded");
        NS_TEST_ASSERT_MSG_EQ_TOL(setup.meter->GetReservedAirtimeUs(),
                                  0.0,
                                  1e-12,
                                  "Final meter reservation was not released");

        const auto lines = ReadLines(setup.paths.decisions);
        NS_TEST_ASSERT_MSG_EQ(lines.size(), 10, "Decision CSV cardinality changed");
        if (lines.size() == 10)
        {
            const auto header = SplitCsv(lines[0]);
            const auto headerError = FindDecisionHeaderError(header);
            NS_TEST_ASSERT_MSG_EQ(headerError.has_value(),
                                  false,
                                  headerError.value_or(""));
            const auto status = FindColumn(header, "decision_status");
            const auto feature = FindColumn(header, "feature_evaluated");
            const auto scorePass = FindColumn(header, "passes_score_threshold");
            const auto launched = FindColumn(header, "secondary_launched");
            const auto before = FindColumn(header, "meter_reserved_before_us");
            const auto after = FindColumn(header, "meter_reserved_after_us");
            const auto reserved = FindColumn(header, "canonical_reserved_airtime_us");
            const auto learnedAccounting =
                FindColumn(header, "learned_cost_token_accounting");
            for (std::size_t index = 1; index <= 8; ++index)
            {
                const auto row = SplitCsv(lines[index]);
                NS_TEST_ASSERT_MSG_EQ(row.size(), 82, "Warmup row width changed");
                NS_TEST_ASSERT_MSG_EQ(row[status],
                                      "history_warmup",
                                      "Warmup gate order changed");
                NS_TEST_ASSERT_MSG_EQ(row[feature], "0", "Warmup evaluated the model");
            }
            const auto action = SplitCsv(lines[9]);
            NS_TEST_ASSERT_MSG_EQ(action.size(), 82, "Action row width changed");
            NS_TEST_ASSERT_MSG_EQ(action[status], "action", "Exact-threshold status changed");
            NS_TEST_ASSERT_MSG_EQ(action[feature], "1", "Action omitted model evidence");
            NS_TEST_ASSERT_MSG_EQ(action[scorePass], "1", "Exact threshold did not pass");
            NS_TEST_ASSERT_MSG_EQ(action[launched], "1", "Action row omitted launch");
            NS_TEST_ASSERT_MSG_EQ(action[learnedAccounting],
                                  "0",
                                  "Learned cost entered token accounting");
            NS_TEST_ASSERT_MSG_EQ_TOL(std::stod(action[after]),
                                      std::stod(action[before]) + std::stod(action[reserved]),
                                      PairedValueT2Controller::ACCOUNTING_TOLERANCE_US,
                                      "Action reservation proof failed");
        }

        AssertSummary(setup, runId);

        setup.controller->Dispose();
        Simulator::Destroy();
        RemovePaths(setup.paths);
    }
};

/**
 * Exercise score rejection and guard rejection using the real frozen model.
 *
 * @ingroup tests
 */
class PairedValueT2OrderedModelGatesTestCase : public TestCase
{
  public:
    PairedValueT2OrderedModelGatesTestCase()
        : TestCase("Paired-value T2 preserves score then measured-airtime guard order")
    {
    }

  private:
    void RunGolden(const GoldenCase& golden,
                   const std::string& label,
                   uint16_t port,
                   bool exhaustGuard,
                   const std::string& expectedStatus,
                   PairedValueT2Controller::AdmissionProfile admissionProfile =
                       PairedValueT2Controller::AdmissionProfile::BASELINE_V1)
    {
        const std::string runId = "paired-value-" + label;
        auto setup = ConfigureGoldenSender(golden,
                                           label,
                                           runId,
                                           port,
                                           admissionProfile);
        for (uint64_t frameId = 0; frameId <= 8; ++frameId)
        {
            if (exhaustGuard && frameId == 8)
            {
                Simulator::Schedule(
                    NanoSeconds(golden.currentSample.sampleTimeNs - 1),
                    &PairedValueT2ControllerTestAccess::DebitGuard,
                    setup.controller,
                    golden.currentSample.sampleTimeNs - 1,
                    59999.0);
            }
            SchedulePair(setup.controller,
                         MakeGoldenHistoryPrimary(golden, runId, frameId));
        }
        Simulator::Stop(NanoSeconds(golden.currentSample.sampleTimeNs + 1));
        Simulator::Run();
        const auto lines = ReadLines(setup.paths.decisions);
        NS_TEST_ASSERT_MSG_EQ(lines.size(), 10, "Golden gate decision rows are incomplete");
        if (lines.size() == 10)
        {
            const auto header = SplitCsv(lines[0]);
            const auto row = SplitCsv(lines[9]);
            NS_TEST_ASSERT_MSG_EQ(row[FindColumn(header, "decision_status")],
                                  expectedStatus,
                                  "Golden model gate status changed");
            NS_TEST_ASSERT_MSG_EQ(row[FindColumn(header, "feature_evaluated")],
                                  "1",
                                  "Golden model gate was not evaluated");
            NS_TEST_ASSERT_MSG_EQ(row[FindColumn(header, "launch_attempted")],
                                  "0",
                                  "Rejected model/guard gate attempted a launch");
            if (PairedValueT2Controller::UsesCostFreeScore(admissionProfile))
            {
                NS_TEST_ASSERT_MSG_EQ(row[FindColumn(header, "ranker")],
                                      "legacy_bad12_value",
                                      "Cost-free ranker metadata differs");
                NS_TEST_ASSERT_MSG_EQ(row[FindColumn(header, "passes_score_threshold")],
                                      "1",
                                      "Cost-free primary threshold rejected the fixture");
                NS_TEST_ASSERT_MSG_EQ(
                    row[FindColumn(header, "passes_emergency_score_threshold")],
                    "0",
                    "Cost-free emergency threshold accepted the fixture");
                NS_TEST_ASSERT_MSG_EQ_TOL(
                    std::stof(row[FindColumn(header, "policy_score_float32")]),
                    static_cast<float>(golden.expectedModelResult.nonnegativeBad12Value),
                    0.0,
                    "Serialized cost-free policy score differs");
            }
        }
        NS_TEST_ASSERT_MSG_EQ(setup.controller->GetLaunchCount(),
                              0,
                              "Rejected golden gate launched a copy");
        setup.controller->Dispose();
        Simulator::Destroy();
        RemovePaths(setup.paths);
    }

    void DoRun() override
    {
        const auto cases = temporal_t2_feature_adapter_golden_v1::GetCases();
        RunGolden(cases[1],
                  "below",
                  9292,
                  false,
                  "below_score_threshold");
        RunGolden(cases[0],
                  "guard",
                  9293,
                  true,
                  "airtime_guard_rejected");
        RunGolden(cases[0],
                  "cost-free-guard",
                  9295,
                  true,
                  "airtime_guard_rejected",
                  PairedValueT2Controller::AdmissionProfile::
                      COST_FREE_SCORE_AWARE_V5);
    }
};

/**
 * Verify isolated score-aware profile identity and decision telemetry.
 *
 * @ingroup tests
 */
class PairedValueT2AdmissionProfileTestCase : public TestCase
{
  public:
    PairedValueT2AdmissionProfileTestCase()
        : TestCase("Paired-value T2 freezes separate score-aware admission evidence")
    {
    }

  private:
    void DoRun() override
    {
        using Profile = PairedValueT2Controller::AdmissionProfile;
        const auto baseline =
            PairedValueT2Controller::ParseAdmissionProfile("baseline_v1");
        const auto scoreAware = PairedValueT2Controller::ParseAdmissionProfile(
            "score_aware_emergency_v2");
        const auto fullHorizon = PairedValueT2Controller::ParseAdmissionProfile(
            "score_aware_full_horizon_v3");
        const auto remainingRefill = PairedValueT2Controller::ParseAdmissionProfile(
            "score_aware_remaining_refill_v4");
        const auto costFree = PairedValueT2Controller::ParseAdmissionProfile(
            "cost_free_score_aware_v5");
        NS_TEST_ASSERT_MSG_EQ(baseline.has_value(),
                              true,
                              "Baseline admission profile did not parse");
        NS_TEST_ASSERT_MSG_EQ(scoreAware.has_value(),
                              true,
                              "Score-aware admission profile did not parse");
        NS_TEST_ASSERT_MSG_EQ(fullHorizon.has_value(),
                              true,
                              "Full-horizon admission profile did not parse");
        NS_TEST_ASSERT_MSG_EQ(remainingRefill.has_value(),
                              true,
                              "Remaining-refill admission profile did not parse");
        NS_TEST_ASSERT_MSG_EQ(costFree.has_value(),
                              true,
                              "Cost-free admission profile did not parse");
        NS_TEST_ASSERT_MSG_EQ(
            PairedValueT2Controller::ParseAdmissionProfile("unsupported").has_value(),
            false,
            "Unsupported admission profile parsed");
        if (!baseline || !scoreAware || !fullHorizon || !remainingRefill || !costFree)
        {
            return;
        }
        NS_TEST_ASSERT_MSG_EQ(static_cast<uint32_t>(*baseline),
                              static_cast<uint32_t>(Profile::BASELINE_V1),
                              "Baseline profile ordinal changed");
        NS_TEST_ASSERT_MSG_EQ(static_cast<uint32_t>(*scoreAware),
                              static_cast<uint32_t>(Profile::SCORE_AWARE_EMERGENCY_V2),
                              "Score-aware profile ordinal changed");
        NS_TEST_ASSERT_MSG_EQ(
            static_cast<uint32_t>(*fullHorizon),
            static_cast<uint32_t>(Profile::SCORE_AWARE_FULL_HORIZON_V3),
            "Full-horizon profile ordinal changed");
        NS_TEST_ASSERT_MSG_EQ(
            static_cast<uint32_t>(*remainingRefill),
            static_cast<uint32_t>(Profile::SCORE_AWARE_REMAINING_REFILL_V4),
            "Remaining-refill profile ordinal changed");
        NS_TEST_ASSERT_MSG_EQ(
            static_cast<uint32_t>(*costFree),
            static_cast<uint32_t>(Profile::COST_FREE_SCORE_AWARE_V5),
            "Cost-free profile ordinal differs");
        NS_TEST_ASSERT_MSG_EQ(PairedValueT2Controller::GetCsvSchemaVersion(*baseline),
                              1,
                              "Baseline decision schema changed");
        NS_TEST_ASSERT_MSG_EQ(PairedValueT2Controller::GetCsvSchemaVersion(*scoreAware),
                              2,
                              "Score-aware decision schema changed");
        NS_TEST_ASSERT_MSG_EQ(
            PairedValueT2Controller::GetCsvSchemaVersion(*fullHorizon),
            2,
            "Full-horizon decision schema changed");
        NS_TEST_ASSERT_MSG_EQ(
            PairedValueT2Controller::GetCsvSchemaVersion(*remainingRefill),
            3,
            "Remaining-refill decision schema differs");
        NS_TEST_ASSERT_MSG_EQ(
            PairedValueT2Controller::GetSummarySchemaVersion(*remainingRefill),
            3,
            "Remaining-refill summary schema differs");
        NS_TEST_ASSERT_MSG_EQ(PairedValueT2Controller::GetCsvSchemaVersion(*costFree),
                              4,
                              "Cost-free decision schema differs");
        NS_TEST_ASSERT_MSG_EQ(
            PairedValueT2Controller::GetSummarySchemaVersion(*costFree),
            4,
            "Cost-free summary schema differs");
        NS_TEST_ASSERT_MSG_EQ(
            PairedValueT2Controller::GetRuntimeContractId(*scoreAware),
            "paired-value-duplication-t2-score-aware-emergency-v2",
            "Score-aware runtime identity changed");
        NS_TEST_ASSERT_MSG_EQ(
            PairedValueT2Controller::GetRuntimeContractSha256(*scoreAware),
            "bdc5b2a944475d1cc31749100e333a2eb2059e106eaf86d918855b721ab3fcda",
            "Score-aware runtime digest changed");
        NS_TEST_ASSERT_MSG_EQ(
            PairedValueT2Controller::GetRuntimeContractId(*fullHorizon),
            "paired-value-duplication-t2-full-horizon-carryover-v3",
            "Full-horizon runtime identity changed");
        NS_TEST_ASSERT_MSG_EQ(
            PairedValueT2Controller::GetRuntimeContractSha256(*fullHorizon),
            "16ccbbfc19ac5c6b824c65b5f00fd0a8792610ea9239e9277390f51eda83f9d8",
            "Full-horizon runtime digest changed");
        NS_TEST_ASSERT_MSG_EQ(
            PairedValueT2Controller::GetRuntimeContractId(*remainingRefill),
            "paired-value-duplication-t2-remaining-refill-borrowing-v4",
            "Remaining-refill runtime identity differs");
        NS_TEST_ASSERT_MSG_EQ(
            PairedValueT2Controller::GetRuntimeContractSha256(*remainingRefill),
            "0b5d31861c862e1b4fb31231936ecd144958939308b21566e97405a29de0d9dd",
            "Remaining-refill runtime digest differs");
        NS_TEST_ASSERT_MSG_EQ(
            PairedValueT2Controller::GetRuntimeContractId(*costFree),
            "paired-value-duplication-t2-cost-free-score-aware-v5",
            "Cost-free runtime identity differs");
        NS_TEST_ASSERT_MSG_EQ(
            PairedValueT2Controller::GetRuntimeContractSha256(*costFree),
            "b7fb00982ae090fe1142b39adf0ad6d26d253741dd5059ed95637dd86047ba96",
            "Cost-free runtime digest differs");
        NS_TEST_ASSERT_MSG_EQ(PairedValueT2Controller::GetPolicyRanker(*costFree),
                              "legacy_bad12_value",
                              "Cost-free ranker differs");
        NS_TEST_ASSERT_MSG_EQ(
            std::bit_cast<uint32_t>(
                PairedValueT2Controller::GetPolicyScoreThreshold(*costFree)),
            0x3e3f68cfU,
            "Cost-free primary threshold differs");
        NS_TEST_ASSERT_MSG_EQ(
            std::bit_cast<uint32_t>(
                PairedValueT2Controller::GetEmergencyScoreThreshold(*costFree)),
            0x3e9d2ac5U,
            "Cost-free emergency threshold differs");
        NS_TEST_ASSERT_MSG_EQ(
            PairedValueT2Controller::GetBudgetMaxHorizonUs(*scoreAware),
            10000000,
            "V2 carry-over horizon changed");
        NS_TEST_ASSERT_MSG_EQ(
            PairedValueT2Controller::GetBudgetCapacityUs(*scoreAware),
            60000,
            "V2 carry-over capacity changed");
        NS_TEST_ASSERT_MSG_EQ(
            PairedValueT2Controller::GetBudgetMaxHorizonUs(*fullHorizon),
            60000000,
            "V3 carry-over horizon differs");
        NS_TEST_ASSERT_MSG_EQ(
            PairedValueT2Controller::GetBudgetCapacityUs(*fullHorizon),
            360000,
            "V3 carry-over capacity differs");
        NS_TEST_ASSERT_MSG_EQ(
            PairedValueT2Controller::GetBudgetMaxHorizonUs(*remainingRefill),
            60000000,
            "V4 carry-over horizon differs");
        NS_TEST_ASSERT_MSG_EQ(
            PairedValueT2Controller::GetBudgetCapacityUs(*remainingRefill),
            360000,
            "V4 carry-over capacity differs");
        NS_TEST_ASSERT_MSG_EQ(
            PairedValueT2Controller::GetBudgetMaxHorizonUs(*costFree),
            10000000,
            "V5 must retain the V2 carry-over horizon");
        NS_TEST_ASSERT_MSG_EQ(
            PairedValueT2Controller::GetBudgetCapacityUs(*costFree),
            60000,
            "V5 must retain the V2 carry-over capacity");
        auto fullHorizonController = CreateObject<PairedValueT2Controller>();
        fullHorizonController->SetAdmissionProfile(*fullHorizon);
        NS_TEST_ASSERT_MSG_EQ(
            PairedValueT2ControllerTestAccess::GetGuardMaxHorizonUs(
                fullHorizonController),
            60000000,
            "V3 runtime guard did not retain the full horizon");
        NS_TEST_ASSERT_MSG_EQ_TOL(
            PairedValueT2ControllerTestAccess::GetGuardCapacityUs(
                fullHorizonController),
            360000.0,
            1e-12,
            "V3 runtime guard capacity differs");
        NS_TEST_ASSERT_MSG_EQ_TOL(
            PairedValueT2ControllerTestAccess::GetGuardInitialCreditUs(
                fullHorizonController),
            12000.0,
            1e-12,
            "V3 must not receive full-horizon startup credit");
        fullHorizonController->Dispose();

        auto setup = ConfigureBareController("score-aware-profile",
                                             "paired-value-score-aware-profile",
                                             *scoreAware);
        NS_TEST_ASSERT_MSG_EQ(
            static_cast<uint32_t>(setup.controller->GetAdmissionProfile()),
            static_cast<uint32_t>(*scoreAware),
                              "Controller did not retain its admission profile");
        const auto lines = ReadLines(setup.paths.decisions);
        NS_TEST_ASSERT_MSG_EQ(lines.size(), 1, "Decision header was not written exactly once");
        if (lines.size() == 1)
        {
            const auto header = SplitCsv(lines[0]);
            NS_TEST_ASSERT_MSG_EQ(header.size(),
                                  DECISION_COLUMNS.size() + 8,
                                  "Score-aware decision column count differs");
            const std::array<std::string_view, 8> expectedSuffix{
                "admission_profile_id",
                "strict_guard_admitted",
                "emergency_score_threshold_float32",
                "passes_emergency_score_threshold",
                "emergency_admission_considered",
                "emergency_maximum_debt_us",
                "emergency_admitted",
                "admission_tier",
            };
            NS_TEST_ASSERT_MSG_EQ(
                std::equal(expectedSuffix.begin(),
                           expectedSuffix.end(),
                           header.end() - expectedSuffix.size()),
                true,
                "Score-aware decision suffix differs from its runtime contract");
        }
        setup.controller->Dispose();
        Simulator::Destroy();
        RemovePaths(setup.paths);

        auto remainingSetup = ConfigureBareController("remaining-refill-profile",
                                                      "paired-value-remaining-refill-profile",
                                                      *remainingRefill);
        const auto remainingLines = ReadLines(remainingSetup.paths.decisions);
        NS_TEST_ASSERT_MSG_EQ(remainingLines.size(),
                              1,
                              "Remaining-refill decision header count differs");
        if (remainingLines.size() == 1)
        {
            const auto header = SplitCsv(remainingLines[0]);
            NS_TEST_ASSERT_MSG_EQ(header.size(),
                                  DECISION_COLUMNS.size() + 11,
                                  "Remaining-refill decision column count differs");
            const std::array<std::string_view, 3> expectedSuffix{
                "remaining_refill_credit_us",
                "remaining_refill_admission_considered",
                "remaining_refill_admitted",
            };
            NS_TEST_ASSERT_MSG_EQ(
                std::equal(expectedSuffix.begin(),
                           expectedSuffix.end(),
                           header.end() - expectedSuffix.size()),
                true,
                "Remaining-refill decision suffix differs from its runtime contract");
        }
        remainingSetup.controller->Dispose();
        Simulator::Destroy();
        RemovePaths(remainingSetup.paths);

        auto costFreeSetup = ConfigureBareController("cost-free-profile",
                                                     "paired-value-cost-free-profile",
                                                     *costFree);
        const auto costFreeLines = ReadLines(costFreeSetup.paths.decisions);
        NS_TEST_ASSERT_MSG_EQ(costFreeLines.size(),
                              1,
                              "Cost-free decision header count differs");
        if (costFreeLines.size() == 1)
        {
            const auto header = SplitCsv(costFreeLines[0]);
            NS_TEST_ASSERT_MSG_EQ(header.size(),
                                  DECISION_COLUMNS.size() + 9,
                                  "Cost-free decision column count differs");
            NS_TEST_ASSERT_MSG_EQ(header.back(),
                                  "policy_score_float32",
                                  "Cost-free policy score is not the appended column");
        }
        costFreeSetup.controller->Dispose();
        Simulator::Destroy();
        RemovePaths(costFreeSetup.paths);
    }
};

/**
 * Prove that V4 adds only the final remaining-refill admission tier.
 *
 * @ingroup tests
 */
class PairedValueT2RemainingRefillAdmissionTestCase : public TestCase
{
  public:
    PairedValueT2RemainingRefillAdmissionTestCase()
        : TestCase("Paired-value T2 admits a low-emergency-score frame against remaining refill")
    {
    }

  private:
    void DoRun() override
    {
        using Profile = PairedValueT2Controller::AdmissionProfile;
        const auto& golden = temporal_t2_feature_adapter_golden_v1::GetCases()[0];
        const std::string runId = "paired-value-remaining-refill-action";
        auto setup = ConfigureGoldenSender(golden,
                                           "remaining-refill-action",
                                           runId,
                                           9294,
                                           Profile::SCORE_AWARE_REMAINING_REFILL_V4);
        for (uint64_t frameId = 0; frameId <= 8; ++frameId)
        {
            if (frameId == 8)
            {
                Simulator::Schedule(
                    NanoSeconds(golden.currentSample.sampleTimeNs - 1),
                    &PairedValueT2ControllerTestAccess::DebitGuard,
                    setup.controller,
                    golden.currentSample.sampleTimeNs - 1,
                    359999.0);
            }
            SchedulePair(setup.controller,
                         MakeGoldenHistoryPrimary(golden, runId, frameId));
        }
        Simulator::Stop(NanoSeconds(golden.currentSample.sampleTimeNs + 1));
        Simulator::Run();

        const auto lines = ReadLines(setup.paths.decisions);
        NS_TEST_ASSERT_MSG_EQ(lines.size(),
                              10,
                              "Remaining-refill decision rows are incomplete");
        if (lines.size() == 10)
        {
            const auto header = SplitCsv(lines[0]);
            const auto row = SplitCsv(lines[9]);
            NS_TEST_ASSERT_MSG_EQ(row.size(), 93, "Remaining-refill row width differs");
            NS_TEST_ASSERT_MSG_EQ(row[FindColumn(header, "decision_status")],
                                  "action",
                                  "Remaining-refill tier did not produce an action");
            NS_TEST_ASSERT_MSG_EQ(row[FindColumn(header, "strict_guard_admitted")],
                                  "0",
                                  "Strict admission unexpectedly accepted the fixture");
            NS_TEST_ASSERT_MSG_EQ(
                row[FindColumn(header, "passes_emergency_score_threshold")],
                "0",
                "Fixture unexpectedly passed the inherited emergency score gate");
            NS_TEST_ASSERT_MSG_EQ(
                row[FindColumn(header, "remaining_refill_admission_considered")],
                "1",
                "Remaining-refill tier was not considered");
            NS_TEST_ASSERT_MSG_EQ(row[FindColumn(header, "remaining_refill_admitted")],
                                  "1",
                                  "Remaining-refill tier did not admit the fixture");
            NS_TEST_ASSERT_MSG_EQ(row[FindColumn(header, "admission_tier")],
                                  "remaining_refill",
                                  "Decision did not identify the final admission tier");
            const double expectedRemainingRefillUs =
                PairedValueT2Controller::BUDGET_FRACTION *
                static_cast<double>(PairedValueT2Controller::MEASUREMENT_STOP_NS -
                                    golden.currentSample.sampleTimeNs) /
                NANOS_PER_MICROSECOND;
            NS_TEST_ASSERT_MSG_EQ_TOL(
                std::stod(row[FindColumn(header, "remaining_refill_credit_us")]),
                expectedRemainingRefillUs,
                1e-9,
                "Decision row did not record exact remaining causal refill");
        }
        NS_TEST_ASSERT_MSG_EQ(setup.controller->GetLaunchCount(),
                              1,
                              "Remaining-refill fixture did not launch one copy");
        setup.controller->Dispose();
        Simulator::Destroy();
        RemovePaths(setup.paths);
    }
};

/**
 * Exercise early gate precedence without querying a delayed descriptor.
 *
 * @ingroup tests
 */
class PairedValueT2EarlyGatesTestCase : public TestCase
{
  public:
    PairedValueT2EarlyGatesTestCase()
        : TestCase("Paired-value T2 applies window history type actionability descriptor order")
    {
    }

  private:
    void RunScenario(const std::string& label,
                     uint64_t frameCount,
                     uint64_t startTimeNs,
                     std::optional<uint64_t> nonactionableFrame,
                     uint64_t inspectedFrame,
                     const std::string& expectedStatus)
    {
        const std::string runId = "paired-value-early-" + label;
        auto setup = ConfigureBareController(label, runId);
        for (uint64_t frameId = 0; frameId < frameCount; ++frameId)
        {
            const uint64_t generationTimeNs = startTimeNs + FrameOffsetNs(frameId);
            const bool actionable = !nonactionableFrame || *nonactionableFrame != frameId;
            SchedulePair(setup.controller,
                         MakeSimplePrimary(runId,
                                           frameId,
                                           generationTimeNs,
                                           actionable));
        }
        const uint64_t finalSampleNs = startTimeNs + FrameOffsetNs(frameCount - 1) +
                                       2000 * NANOS_PER_MICROSECOND;
        Simulator::Stop(NanoSeconds(finalSampleNs + 1));
        Simulator::Run();
        const auto lines = ReadLines(setup.paths.decisions);
        NS_TEST_ASSERT_MSG_EQ(lines.size(), frameCount + 1, "Early-gate row count changed");
        if (lines.size() == frameCount + 1)
        {
            const auto header = SplitCsv(lines[0]);
            const auto row = SplitCsv(lines[inspectedFrame + 1]);
            NS_TEST_ASSERT_MSG_EQ(row[FindColumn(header, "decision_status")],
                                  expectedStatus,
                                  "Early gate precedence changed");
            NS_TEST_ASSERT_MSG_EQ(row[FindColumn(header, "feature_evaluated")],
                                  "0",
                                  "Early gate evaluated the model");
        }
        setup.controller->Dispose();
        Simulator::Destroy();
        RemovePaths(setup.paths);
    }

    void DoRun() override
    {
        RunScenario("outside",
                    1,
                    PairedValueT2Controller::DECISION_STOP_NS -
                        2000 * NANOS_PER_MICROSECOND,
                    std::nullopt,
                    0,
                    "outside_decision_window");
        RunScenario("not-actionable",
                    9,
                    PairedValueT2Controller::DECISION_START_NS,
                    8,
                    8,
                    "not_actionable");
        RunScenario("descriptor",
                    9,
                    PairedValueT2Controller::DECISION_START_NS,
                    std::nullopt,
                    8,
                    "descriptor_unavailable");
        RunScenario("frame-type",
                    61,
                    PairedValueT2Controller::DECISION_START_NS,
                    std::nullopt,
                    60,
                    "frame_type_restricted");
    }
};

/**
 * Prove that every validated report is owned and stored before every gate.
 *
 * @ingroup tests
 */
class PairedValueT2StatefulHistoryTestCase : public TestCase
{
  public:
    PairedValueT2StatefulHistoryTestCase()
        : TestCase("Paired-value T2 stores owned frame-keyed history before all policy gates")
    {
    }

  private:
    void DoRun() override
    {
        const std::string runId = "paired-value-stateful-history";
        auto setup = ConfigureBareController("stateful-history", runId);
        std::array<uint64_t, 13> captureTimes{};
        for (uint64_t frameId = 0; frameId <= 12; ++frameId)
        {
            const uint64_t sampleTimeNs =
                frameId == 0 ? 902000000 : 1002000000 + (frameId - 1) * 33000000;
            bool actionable = frameId != 10;
            auto primary = MakeSimplePrimary(runId,
                                             frameId,
                                             sampleTimeNs - 2000 * NANOS_PER_MICROSECOND,
                                             actionable);
            if (frameId == 9)
            {
                primary.frameType = FrameType::I_FRAME;
                primary.frameSizeBytes = 48000;
                primary.framePacketCount = 40;
                primary.packetsSubmitted = 40;
                primary.applicationSocketPacketBytesSubmitted = 50000;
            }
            captureTimes[frameId] = primary.pollingReport->captureTimeNs;
            primary.pollingReport->mpduTxAttemptsTotal = frameId * 10;
            primary.pollingReport->mpduPositiveAcksTotal = frameId * 10;
            primary.pollingReport->ppduTxCountTotal = frameId * 10;
            SchedulePairThenMutateCallerReport(setup.controller, std::move(primary));
        }
        Simulator::Stop(NanoSeconds(1365000001));
        Simulator::Run();

        const auto lines = ReadLines(setup.paths.decisions);
        NS_TEST_ASSERT_MSG_EQ(lines.size(), 14, "Stateful history row count changed");
        if (lines.size() == 14)
        {
            const auto header = SplitCsv(lines[0]);
            const auto status = FindColumn(header, "decision_status");
            const auto ready = FindColumn(header, "history_ready");
            const auto currentCapture = FindColumn(header, "current_poll_capture_time_ns");
            const auto lag1Frame = FindColumn(header, "lag1_frame_id");
            const auto lag1Capture = FindColumn(header, "lag1_poll_capture_time_ns");
            const auto lag3Frame = FindColumn(header, "lag3_frame_id");
            const auto lag3Capture = FindColumn(header, "lag3_poll_capture_time_ns");
            const auto lag8Frame = FindColumn(header, "lag8_frame_id");
            const auto lag8Capture = FindColumn(header, "lag8_poll_capture_time_ns");
            const auto row = [&lines](uint64_t frameId) {
                return SplitCsv(lines.at(frameId + 1));
            };

            NS_TEST_ASSERT_MSG_EQ(row(0)[status],
                                  "outside_decision_window",
                                  "Frame zero did not stop at the window gate");
            for (uint64_t frameId = 1; frameId <= 7; ++frameId)
            {
                NS_TEST_ASSERT_MSG_EQ(row(frameId)[status],
                                      "history_warmup",
                                      "Warmup frame was not stored before its gate");
            }
            NS_TEST_ASSERT_MSG_EQ(row(8)[status],
                                  "descriptor_unavailable",
                                  "First history-ready frame did not reach descriptor gate");
            NS_TEST_ASSERT_MSG_EQ(row(8)[ready], "1", "Frame eight is not first ready frame");
            NS_TEST_ASSERT_MSG_EQ(row(8)[lag1Frame], "7", "Frame eight lag1 is not exact");
            NS_TEST_ASSERT_MSG_EQ(std::stoull(row(8)[lag1Capture]),
                                  captureTimes[7],
                                  "Frame eight lag1 capture is not exact");
            NS_TEST_ASSERT_MSG_EQ(row(8)[lag3Frame], "5", "Frame eight lag3 is not exact");
            NS_TEST_ASSERT_MSG_EQ(std::stoull(row(8)[lag3Capture]),
                                  captureTimes[5],
                                  "Frame eight lag3 capture is not exact");
            NS_TEST_ASSERT_MSG_EQ(row(8)[lag8Frame], "0", "Outside-window frame was not lag8");
            NS_TEST_ASSERT_MSG_EQ(std::stoull(row(8)[lag8Capture]),
                                  captureTimes[0],
                                  "Mutated caller report replaced owned lag8 evidence");
            NS_TEST_ASSERT_MSG_EQ(row(9)[status],
                                  "frame_type_restricted",
                                  "Actionable I frame did not stop at frame gate");
            NS_TEST_ASSERT_MSG_EQ(row(10)[status],
                                  "not_actionable",
                                  "Nonactionable P frame did not stop after frame gate");
            NS_TEST_ASSERT_MSG_EQ(row(11)[status],
                                  "descriptor_unavailable",
                                  "Frame eleven did not reach descriptor gate");
            NS_TEST_ASSERT_MSG_EQ(row(11)[lag1Frame],
                                  "10",
                                  "Nonactionable frame was not stored as lag1");
            NS_TEST_ASSERT_MSG_EQ(std::stoull(row(11)[lag1Capture]),
                                  captureTimes[10],
                                  "Owned nonactionable lag1 report was changed by caller mutation");
            NS_TEST_ASSERT_MSG_EQ(row(11)[lag3Frame], "8", "Frame eleven lag3 is not exact");
            NS_TEST_ASSERT_MSG_EQ(std::stoull(row(11)[lag3Capture]),
                                  captureTimes[8],
                                  "Frame eleven lag3 capture is not exact");
            NS_TEST_ASSERT_MSG_EQ(row(11)[lag8Frame], "3", "Frame eleven lag8 is not exact");
            NS_TEST_ASSERT_MSG_EQ(std::stoull(row(11)[lag8Capture]),
                                  captureTimes[3],
                                  "Frame eleven lag8 capture is not exact");
            NS_TEST_ASSERT_MSG_EQ(row(12)[status],
                                  "descriptor_unavailable",
                                  "Frame twelve did not reach descriptor gate");
            NS_TEST_ASSERT_MSG_EQ(row(12)[lag3Frame],
                                  "9",
                                  "Frame-type-restricted frame was not stored as lag3");
            NS_TEST_ASSERT_MSG_EQ(std::stoull(row(12)[lag3Capture]),
                                  captureTimes[9],
                                  "Owned I-frame lag3 report was changed by caller mutation");
            for (uint64_t frameId = 0; frameId <= 12; ++frameId)
            {
                NS_TEST_ASSERT_MSG_EQ(std::stoull(row(frameId)[currentCapture]),
                                      captureTimes[frameId],
                                      "Current report was not owned before caller mutation");
            }
            NS_TEST_ASSERT_MSG_EQ(row(12)[lag8Frame], "4", "Frame twelve lag8 is not exact");
            NS_TEST_ASSERT_MSG_EQ(std::stoull(row(12)[lag8Capture]),
                                  captureTimes[4],
                                  "Frame twelve lag8 capture is not exact");
            NS_TEST_ASSERT_MSG_EQ(row(12)[lag1Frame], "11", "Frame twelve lag1 is not exact");
            NS_TEST_ASSERT_MSG_EQ(std::stoull(row(12)[lag1Capture]),
                                  captureTimes[11],
                                  "Frame twelve lag1 capture is not exact");
        }

        setup.controller->Dispose();
        Simulator::Destroy();
        RemovePaths(setup.paths);
    }
};

/**
 * Exercise secondary isolation, pair validation, descriptor closure, and status order.
 *
 * @ingroup tests
 */
class PairedValueT2ValidationTestCase : public TestCase
{
  public:
    PairedValueT2ValidationTestCase()
        : TestCase("Paired-value T2 validators fail closed without secondary features")
    {
    }

  private:
    void DoRun() override
    {
        const std::array<std::string_view, 9> expectedStatuses{
            "outside_decision_window",
            "history_warmup",
            "frame_type_restricted",
            "not_actionable",
            "descriptor_unavailable",
            "below_score_threshold",
            "airtime_guard_rejected",
            "launch_rejected",
            "action",
        };
        for (uint8_t index = 0; index < expectedStatuses.size(); ++index)
        {
            NS_TEST_ASSERT_MSG_EQ(PairedValueT2ControllerTestAccess::StatusName(index),
                                  expectedStatuses[index],
                                  "Frozen status ordering changed");
        }

        auto primary = temporal_t2_feature_adapter_golden_v1::GetCases()[0].currentSample;
        primary.runId = "validation";
        primary.key.frameId = 8;
        auto secondary = MakeSecondary(primary);
        NS_TEST_ASSERT_MSG_EQ(
            PairedValueT2ControllerTestAccess::FindPairError(primary, secondary).has_value(),
            false,
            "Valid immutable pair was rejected");
        NS_TEST_ASSERT_MSG_EQ(
            PairedValueT2ControllerTestAccess::FindUntreatedError(secondary).has_value(),
            false,
            "Malformed forbidden secondary path-wide state was consulted");
        std::vector<std::pair<std::string, PredictionSample>> mismatchedPairs;
        auto AddMismatch = [&mismatchedPairs, &secondary](
                               const std::string& label,
                               const auto& mutate) {
            auto mismatched = secondary;
            mutate(mismatched);
            mismatchedPairs.emplace_back(label, std::move(mismatched));
        };
        AddMismatch("run ID", [](auto& sample) { sample.runId += "-wrong"; });
        AddMismatch("telemetry schema", [](auto& sample) { ++sample.telemetrySchemaVersion; });
        AddMismatch("frame ID", [](auto& sample) { ++sample.key.frameId; });
        AddMismatch("sample stage", [](auto& sample) { sample.sampleStage = "T1"; });
        AddMismatch("sample offset", [](auto& sample) { ++sample.sampleOffsetUs; });
        AddMismatch("sample time", [](auto& sample) { ++sample.sampleTimeNs; });
        AddMismatch("generation time", [](auto& sample) { ++sample.generationTimeNs; });
        AddMismatch("deadline", [](auto& sample) { ++sample.deadlineTimeNs; });
        AddMismatch("frame age", [](auto& sample) { ++sample.frameAgeUs; });
        AddMismatch("deadline slack", [](auto& sample) { ++sample.deadlineSlackUs; });
        AddMismatch("frame size", [](auto& sample) { ++sample.frameSizeBytes; });
        AddMismatch("packet count", [](auto& sample) { ++sample.framePacketCount; });
        AddMismatch("frame type", [](auto& sample) { sample.frameType = FrameType::I_FRAME; });
        for (const auto& [label, mismatched] : mismatchedPairs)
        {
            NS_TEST_ASSERT_MSG_EQ(
                PairedValueT2ControllerTestAccess::FindPairError(primary, mismatched)
                    .has_value(),
                true,
                "Immutable " << label << " pair mismatch was accepted");
        }

        std::vector<std::pair<std::string, PredictionSample>> treatedEndpoints;
        auto AddProgress = [&treatedEndpoints, &secondary](
                               const std::string& label,
                               const auto& mutate) {
            auto treated = secondary;
            mutate(treated);
            treatedEndpoints.emplace_back(label, std::move(treated));
        };
        AddProgress("submitted packets", [](auto& sample) { sample.packetsSubmitted = 1; });
        AddProgress("submitted bytes", [](auto& sample) {
            sample.applicationSocketPacketBytesSubmitted = 1;
        });
        AddProgress("remaining packets", [](auto& sample) {
            --sample.packetsRemainingToSubmit;
        });
        AddProgress("sender completion", [](auto& sample) { sample.senderMacComplete = true; });
        AddProgress("actionability", [](auto& sample) { sample.actionable = false; });
        AddProgress("MAC enqueue", [](auto& sample) { sample.framePacketsMacEnqueued = 1; });
        AddProgress("MAC dequeue", [](auto& sample) { sample.framePacketsMacDequeued = 1; });
        AddProgress("MAC success", [](auto& sample) { sample.framePacketsTxSucceeded = 1; });
        AddProgress("MAC failure", [](auto& sample) { sample.frameMpduAttemptFailures = 1; });
        AddProgress("MAC terminal drop", [](auto& sample) {
            sample.framePacketsTerminallyDropped = 1;
        });
        AddProgress("MAC queue packets", [](auto& sample) {
            sample.framePacketsCurrentlyQueued = 1;
        });
        AddProgress("MAC queue bytes", [](auto& sample) {
            sample.frameMacServiceBytesCurrentlyQueued = 1;
        });
        for (const auto& [label, treated] : treatedEndpoints)
        {
            NS_TEST_ASSERT_MSG_EQ(
                PairedValueT2ControllerTestAccess::FindUntreatedError(treated).has_value(),
                true,
                "Secondary " << label << " progress was accepted");
        }

        DelayedCopyDescriptor descriptor;
        descriptor.frameId = primary.key.frameId;
        descriptor.framePacketCount = primary.framePacketCount;
        descriptor.packetCount = primary.framePacketCount;
        descriptor.deadlineTimeNs = primary.deadlineTimeNs;
        descriptor.expectedMacServiceBytes =
            primary.frameSizeBytes + primary.framePacketCount * (50 + 36);
        for (uint32_t index = 0; index < descriptor.packetCount; ++index)
        {
            descriptor.packetIndices.push_back(index);
        }
        NS_TEST_ASSERT_MSG_EQ(
            PairedValueT2ControllerTestAccess::FindDescriptorError(primary, descriptor)
                .has_value(),
            false,
            "Canonical full descriptor was rejected");
        std::vector<std::pair<std::string, DelayedCopyDescriptor>> invalidDescriptors;
        auto AddInvalidDescriptor = [&invalidDescriptors, &descriptor](
                                        const std::string& label,
                                        const auto& mutate) {
            auto invalid = descriptor;
            mutate(invalid);
            invalidDescriptors.emplace_back(label, std::move(invalid));
        };
        AddInvalidDescriptor("frame ID", [](auto& invalid) { ++invalid.frameId; });
        AddInvalidDescriptor("frame packet count", [](auto& invalid) {
            ++invalid.framePacketCount;
        });
        AddInvalidDescriptor("deadline", [](auto& invalid) { ++invalid.deadlineTimeNs; });
        AddInvalidDescriptor("copy packet count", [](auto& invalid) { --invalid.packetCount; });
        AddInvalidDescriptor("packet-index size", [](auto& invalid) {
            invalid.packetIndices.pop_back();
        });
        AddInvalidDescriptor("packet order", [](auto& invalid) {
            std::swap(invalid.packetIndices[0], invalid.packetIndices[1]);
        });
        AddInvalidDescriptor("zero service bytes", [](auto& invalid) {
            invalid.expectedMacServiceBytes = 0;
        });
        AddInvalidDescriptor("incorrect service bytes", [](auto& invalid) {
            ++invalid.expectedMacServiceBytes;
        });
        for (const auto& [label, invalid] : invalidDescriptors)
        {
            NS_TEST_ASSERT_MSG_EQ(
                PairedValueT2ControllerTestAccess::FindDescriptorError(primary, invalid)
                    .has_value(),
                true,
                "Invalid descriptor " << label << " was accepted");
        }

        const std::string orderRunId = "paired-value-order-validation";
        auto setup = ConfigureBareController("order-validation", orderRunId);
        auto orderedPrimary = MakeSimplePrimary(orderRunId,
                                                8,
                                                PairedValueT2Controller::DECISION_START_NS);
        auto orderedSecondary = MakeSecondary(orderedPrimary);
        std::array<bool, 8> orderChecks{};
        Simulator::Schedule(
            NanoSeconds(orderedPrimary.sampleTimeNs),
            [controller = setup.controller,
             orderedPrimary,
             orderedSecondary,
             &orderChecks]() mutable {
                orderChecks[0] = !PairedValueT2ControllerTestAccess::FindPrimaryError(
                                      controller,
                                      orderedPrimary)
                                      .has_value();
                orderChecks[1] = !PairedValueT2ControllerTestAccess::FindSecondaryError(
                                      controller,
                                      orderedSecondary)
                                      .has_value();
                auto invalid = orderedPrimary;
                invalid.key.pathId = PairedValueT2Controller::SECONDARY_PATH_ID;
                orderChecks[2] = PairedValueT2ControllerTestAccess::FindPrimaryError(controller,
                                                                                     invalid)
                                     .has_value();
                invalid = orderedPrimary;
                invalid.key.copyId = PairedValueT2Controller::SECONDARY_COPY_ID;
                orderChecks[3] = PairedValueT2ControllerTestAccess::FindPrimaryError(controller,
                                                                                     invalid)
                                     .has_value();
                invalid = orderedSecondary;
                invalid.key.pathId = PairedValueT2Controller::PRIMARY_PATH_ID;
                orderChecks[4] = PairedValueT2ControllerTestAccess::FindSecondaryError(
                                     controller,
                                     invalid)
                                     .has_value();
                invalid = orderedSecondary;
                invalid.key.copyId = PairedValueT2Controller::PRIMARY_COPY_ID;
                orderChecks[5] = PairedValueT2ControllerTestAccess::FindSecondaryError(
                                     controller,
                                     invalid)
                                     .has_value();
                invalid = orderedPrimary;
                invalid.sampleStage = "T1";
                orderChecks[6] = PairedValueT2ControllerTestAccess::FindPrimaryError(controller,
                                                                                     invalid)
                                     .has_value();
                invalid = orderedSecondary;
                invalid.sampleStage = "T1";
                orderChecks[7] = PairedValueT2ControllerTestAccess::FindSecondaryError(
                                     controller,
                                     invalid)
                                     .has_value();
            });
        Simulator::Stop(NanoSeconds(orderedPrimary.sampleTimeNs + 1));
        Simulator::Run();
        for (std::size_t index = 0; index < orderChecks.size(); ++index)
        {
            NS_TEST_ASSERT_MSG_EQ(orderChecks[index],
                                  true,
                                  "Endpoint path/copy/stage check failed at " << index);
        }
        setup.controller->Dispose();
        Simulator::Destroy();
        RemovePaths(setup.paths);
    }
};

/**
 * Paired-value T2 controller focused test suite.
 *
 * @ingroup tests
 */
class PairedValueT2ControllerTestSuite : public TestSuite
{
  public:
    PairedValueT2ControllerTestSuite()
        : TestSuite("wifi-streaming-paired-value-t2-controller", Type::UNIT)
    {
        AddTestCase(new PairedValueT2ClosedLoopTestCase(), Duration::QUICK);
        AddTestCase(new PairedValueT2OrderedModelGatesTestCase(), Duration::QUICK);
        AddTestCase(new PairedValueT2AdmissionProfileTestCase(), Duration::QUICK);
        AddTestCase(new PairedValueT2RemainingRefillAdmissionTestCase(), Duration::QUICK);
        AddTestCase(new PairedValueT2EarlyGatesTestCase(), Duration::QUICK);
        AddTestCase(new PairedValueT2StatefulHistoryTestCase(), Duration::QUICK);
        AddTestCase(new PairedValueT2ValidationTestCase(), Duration::QUICK);
    }
};

static PairedValueT2ControllerTestSuite g_pairedValueT2ControllerTestSuite;

} // namespace ns3
