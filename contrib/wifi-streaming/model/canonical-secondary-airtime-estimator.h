/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef CANONICAL_SECONDARY_AIRTIME_ESTIMATOR_H
#define CANONICAL_SECONDARY_AIRTIME_ESTIMATOR_H

#include <cstdint>

namespace ns3
{

/**
 * @ingroup wifi-streaming
 * Allocation-free airtime estimator for the canonical secondary copy.
 *
 * The estimate models one EHT MCS 5, 20 MHz, 800 ns guard-interval, one
 * spatial-stream PPDU.  The nominal estimate includes one PHY preamble and
 * header plus 38 bytes of modeled Wi-Fi MAC overhead per application packet.
 */
class CanonicalSecondaryAirtimeEstimator
{
  public:
    /** Modeled Wi-Fi MAC overhead per application packet. */
    static constexpr uint32_t WIFI_MAC_OVERHEAD_BYTES = 38;

    /**
     * Estimate nominal airtime without safety or retry adjustment.
     *
     * @param packetCount Number of application packets in the packet set.
     * @param expectedMacServiceBytes Sum of expected MAC service bytes.
     * @return Nominal airtime in microseconds.
     */
    static double EstimateNominalUs(uint32_t packetCount,
                                    uint64_t expectedMacServiceBytes);

    /**
     * Estimate airtime with explicit safety and retry multipliers.
     *
     * @param packetCount Number of application packets in the packet set.
     * @param expectedMacServiceBytes Sum of expected MAC service bytes.
     * @param safetyFactor Pre-launch safety multiplier, at least one.
     * @param retryMultiplier Retry or aggregation multiplier, at least one.
     * @return Adjusted airtime in microseconds.
     */
    static double EstimateUs(uint32_t packetCount,
                             uint64_t expectedMacServiceBytes,
                             double safetyFactor,
                             double retryMultiplier);
};

} // namespace ns3

#endif // CANONICAL_SECONDARY_AIRTIME_ESTIMATOR_H
