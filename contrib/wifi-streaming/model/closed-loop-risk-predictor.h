/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef CLOSED_LOOP_RISK_PREDICTOR_H
#define CLOSED_LOOP_RISK_PREDICTOR_H

#include "prediction-model-evaluator.h"
#include "prediction-telemetry-collector.h"

#include "ns3/object.h"

#include <cstdint>
#include <vector>

namespace ns3
{

/**
 * Adapt causal sender snapshots to the frozen 1 ms commodity predictor.
 */
class ClosedLoopRiskPredictor : public Object
{
  public:
    /**
     * Return runtime type information.
     *
     * @return The object TypeId.
     */
    static TypeId GetTypeId();

    ClosedLoopRiskPredictor();
    ~ClosedLoopRiskPredictor() override;

    /**
     * Score one immutable snapshot using its selected polling report.
     *
     * @param sample Current primary-path snapshot.
     * @return Platt-calibrated deadline-miss probability.
     */
    double Score(const PredictionSample& sample);

  protected:
    void DoDispose() override;

  private:
    static PredictionStage ResolveStage(uint64_t offsetUs);
    static double OptionalValue(const std::optional<uint64_t>& value);
    static double OptionalValue(const std::optional<uint32_t>& value);
    static double OptionalValue(const std::optional<uint16_t>& value);
    static double OptionalValue(const std::optional<uint8_t>& value);
    static double OptionalValue(const std::optional<double>& value);
    static double AgeUs(uint64_t sampleTimeNs, const std::optional<uint64_t>& eventTimeNs);
    static double EncodeFrameType(FrameType type);
    static double EncodeFrequencyBand(const std::optional<std::string>& band);
    static const PredictionRollingSample* FindWindow(const PredictionPollingReport& report,
                                                     uint64_t windowUs);
    static std::vector<double> BuildFeatures(const PredictionSample& current,
                                             const PredictionPollingReport* report);
};

} // namespace ns3

#endif // CLOSED_LOOP_RISK_PREDICTOR_H
