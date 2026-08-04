/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "canonical-secondary-airtime-estimator.h"

#include "ns3/abort.h"
#include "ns3/eht-phy.h"
#include "ns3/wifi-phy.h"
#include "ns3/wifi-tx-vector.h"

#include <cmath>

namespace ns3
{

namespace
{

WifiTxVector
MakeCanonicalEstimatorTxVector()
{
    return WifiTxVector(EhtPhy::GetEhtMcs5(),
                        0,
                        WIFI_PREAMBLE_EHT_MU,
                        NanoSeconds(800),
                        1,
                        1,
                        0,
                        MHz_u{20},
                        false);
}

} // namespace

double
CanonicalSecondaryAirtimeEstimator::EstimateNominalUs(
    uint32_t packetCount,
    uint64_t expectedMacServiceBytes)
{
    NS_ABORT_MSG_IF(packetCount == 0, "Canonical airtime estimate requires packets");
    NS_ABORT_MSG_IF(expectedMacServiceBytes == 0,
                    "Canonical airtime estimate requires MAC service bytes");
    const auto txVector = MakeCanonicalEstimatorTxVector();
    const uint64_t rateBps =
        EhtPhy::GetEhtMcs5().GetDataRate(MHz_u{20}, NanoSeconds(800), 1);
    NS_ABORT_MSG_IF(rateBps == 0, "Canonical EHT MCS5 estimate resolved to zero rate");
    const double preambleUs =
        WifiPhy::CalculatePhyPreambleAndHeaderDuration(txVector).GetSeconds() * 1e6;
    const double macBytes =
        static_cast<double>(expectedMacServiceBytes) +
        static_cast<double>(WIFI_MAC_OVERHEAD_BYTES) * packetCount;
    const double payloadUs = 8.0 * macBytes / static_cast<double>(rateBps) * 1e6;
    return preambleUs + payloadUs;
}

double
CanonicalSecondaryAirtimeEstimator::EstimateUs(uint32_t packetCount,
                                                uint64_t expectedMacServiceBytes,
                                                double safetyFactor,
                                                double retryMultiplier)
{
    NS_ABORT_MSG_IF(!std::isfinite(safetyFactor) || safetyFactor < 1,
                    "Canonical airtime safety factor must be finite and >= 1");
    NS_ABORT_MSG_IF(!std::isfinite(retryMultiplier) || retryMultiplier < 1,
                    "Canonical airtime retry multiplier must be finite and >= 1");
    return safetyFactor * retryMultiplier *
           EstimateNominalUs(packetCount, expectedMacServiceBytes);
}

} // namespace ns3
