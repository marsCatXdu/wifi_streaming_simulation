/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "ns3/adaptive-airtime-duplication-controller.h"
#include "ns3/canonical-secondary-airtime-estimator.h"
#include "ns3/randomized-intervention-controller.h"
#include "ns3/secondary-airtime-budget-guard.h"
#include "ns3/test.h"

#include <limits>

namespace ns3
{

/**
 * Verify shared canonical airtime estimation and controller parity.
 *
 * @ingroup tests
 */
class CanonicalSecondaryAirtimeEstimatorTestCase : public TestCase
{
  public:
    /** Constructor. */
    CanonicalSecondaryAirtimeEstimatorTestCase()
        : TestCase("Canonical secondary airtime estimator preserves controller estimates")
    {
    }

  private:
    void DoRun() override
    {
        constexpr uint32_t packetCount = 10;
        constexpr uint64_t expectedMacServiceBytes = 12860;
        constexpr double safetyFactor = 1.25;
        constexpr double retryMultiplier = 1.75;

        const double nominalUs = CanonicalSecondaryAirtimeEstimator::EstimateNominalUs(
            packetCount,
            expectedMacServiceBytes);
        const double adjustedUs = CanonicalSecondaryAirtimeEstimator::EstimateUs(
            packetCount,
            expectedMacServiceBytes,
            safetyFactor,
            retryMultiplier);
        NS_TEST_ASSERT_MSG_EQ_TOL(nominalUs,
                                  1587.008533854628,
                                  1e-12,
                                  "Canonical P-frame nominal estimate changed");
        NS_TEST_ASSERT_MSG_EQ_TOL(
            CanonicalSecondaryAirtimeEstimator::EstimateNominalUs(40, 51440),
            6204.0341354185121,
            1e-12,
            "Canonical I-frame nominal estimate changed");
        NS_TEST_ASSERT_MSG_EQ_TOL(adjustedUs,
                                  safetyFactor * retryMultiplier * nominalUs,
                                  0.0,
                                  "Explicit airtime multipliers changed numeric ordering");

        auto randomized = CreateObject<RandomizedInterventionController>();
        NS_TEST_ASSERT_MSG_EQ_TOL(
            randomized->EstimateFullCopyAirtimeUs(packetCount, expectedMacServiceBytes),
            CanonicalSecondaryAirtimeEstimator::EstimateUs(packetCount,
                                                           expectedMacServiceBytes,
                                                           safetyFactor,
                                                           1.0),
            0.0,
            "Randomized controller differs from the canonical estimator");
        NS_TEST_ASSERT_MSG_EQ(
            RandomizedInterventionController::GetCostEstimatorId(),
            "eht_mcs5_20mhz_gi800_nss1_one_ppdu_safety125_v1",
            "Randomized cost-estimator ID changed during the refactor");

        auto adaptive = CreateObject<AdaptiveAirtimeDuplicationController>();
        adaptive->SetCostSafetyFactor(safetyFactor);
        NS_TEST_ASSERT_MSG_EQ_TOL(
            adaptive->EstimateSecondaryAirtimeUs(packetCount,
                                                  expectedMacServiceBytes,
                                                  retryMultiplier),
            adjustedUs,
            0.0,
            "Adaptive controller differs from the canonical estimator");
    }
};

/**
 * Verify startup credit, refill, reservations, measured debt, and recovery.
 *
 * @ingroup tests
 */
class SecondaryAirtimeBudgetGuardAccountingTestCase : public TestCase
{
  public:
    /** Constructor. */
    SecondaryAirtimeBudgetGuardAccountingTestCase()
        : TestCase("Secondary airtime guard accounts for credit, reservations, and debt")
    {
    }

  private:
    void DoRun() override
    {
        constexpr uint64_t startNs = 1000000000;
        constexpr double fraction = 0.006;
        constexpr uint64_t maxHorizonUs = 10000000;
        constexpr uint64_t initialHorizonUs = 2000000;
        SecondaryAirtimeBudgetGuard guard({fraction, maxHorizonUs, initialHorizonUs});

        NS_TEST_ASSERT_MSG_EQ(guard.IsConfigured(), true, "Valid guard configuration failed");
        NS_TEST_ASSERT_MSG_EQ_TOL(guard.GetCapacityUs(),
                                  60000.0,
                                  0.0,
                                  "Ten-second maximum did not produce 60 ms capacity");
        NS_TEST_ASSERT_MSG_EQ_TOL(guard.GetInitialCreditUs(),
                                  12000.0,
                                  0.0,
                                  "Two-second initial horizon did not produce 12 ms credit");
        NS_TEST_ASSERT_MSG_EQ(guard.Initialize(startNs), true, "Guard failed to initialize");
        NS_TEST_ASSERT_MSG_EQ_TOL(guard.GetBalanceUs(),
                                  12000.0,
                                  0.0,
                                  "Guard did not install initial credit");

        const auto initialAvailable = guard.GetAvailableBalanceUs(2000.0);
        NS_TEST_ASSERT_MSG_EQ(initialAvailable.has_value(),
                              true,
                              "Valid outstanding reservations were rejected");
        NS_TEST_ASSERT_MSG_EQ_TOL(*initialAvailable,
                                  10000.0,
                                  0.0,
                                  "Reservations were not subtracted from balance");
        NS_TEST_ASSERT_MSG_EQ(guard.CanReserve(10000.0, 2000.0),
                              true,
                              "Exact available balance was rejected");
        NS_TEST_ASSERT_MSG_EQ(guard.CanReserve(10000.001, 2000.0),
                              false,
                              "Reservation beyond available balance was admitted");

        NS_TEST_ASSERT_MSG_EQ(guard.DebitMeasuredAirtime(startNs, 15000.0),
                              true,
                              "Measured airtime debit failed");
        NS_TEST_ASSERT_MSG_EQ_TOL(guard.GetBalanceUs(),
                                  -3000.0,
                                  0.0,
                                  "Overspend was not retained as negative balance");
        NS_TEST_ASSERT_MSG_EQ_TOL(guard.GetDebtUs(),
                                  3000.0,
                                  0.0,
                                  "Current debt is incorrect");
        NS_TEST_ASSERT_MSG_EQ_TOL(guard.GetPeakDebtUs(),
                                  3000.0,
                                  0.0,
                                  "Peak debt is incorrect");
        NS_TEST_ASSERT_MSG_EQ_TOL(guard.GetMeasuredAirtimeDebitedUs(),
                                  15000.0,
                                  0.0,
                                  "Measured debit total is incorrect");
        NS_TEST_ASSERT_MSG_EQ(guard.CanReserve(1.0, 0.0),
                              false,
                              "Debt admitted a new reservation");

        NS_TEST_ASSERT_MSG_EQ(guard.Refill(startNs + 500000000),
                              true,
                              "Debt-repayment refill failed");
        NS_TEST_ASSERT_MSG_EQ_TOL(guard.GetBalanceUs(),
                                  0.0,
                                  1e-12,
                                  "Refill did not repay debt at the configured rate");
        NS_TEST_ASSERT_MSG_EQ_TOL(guard.GetDebtUs(),
                                  0.0,
                                  0.0,
                                  "Repaid debt remained visible");
        NS_TEST_ASSERT_MSG_EQ_TOL(guard.GetPeakDebtUs(),
                                  3000.0,
                                  0.0,
                                  "Debt recovery erased peak debt history");

        NS_TEST_ASSERT_MSG_EQ(guard.Refill(startNs + 20500000000ULL),
                              true,
                              "Capacity refill failed");
        NS_TEST_ASSERT_MSG_EQ_TOL(guard.GetBalanceUs(),
                                  60000.0,
                                  0.0,
                                  "Refill exceeded or missed maximum capacity");
    }
};

/**
 * Verify invalid configuration and runtime inputs fail closed.
 *
 * @ingroup tests
 */
class SecondaryAirtimeBudgetGuardFailClosedTestCase : public TestCase
{
  public:
    /** Constructor. */
    SecondaryAirtimeBudgetGuardFailClosedTestCase()
        : TestCase("Secondary airtime guard fails closed on invalid state")
    {
    }

  private:
    void DoRun() override
    {
        SecondaryAirtimeBudgetGuard guard;
        NS_TEST_ASSERT_MSG_EQ(guard.IsConfigured(),
                              false,
                              "Default guard was not fail closed");
        NS_TEST_ASSERT_MSG_EQ(guard.Initialize(1000),
                              false,
                              "Unconfigured guard initialized");
        NS_TEST_ASSERT_MSG_EQ(guard.CanReserve(1.0, 0.0),
                              false,
                              "Unconfigured guard admitted a reservation");

        NS_TEST_ASSERT_MSG_EQ(
            guard.Configure({0.006, 1000000, 2000000}),
            false,
            "Initial horizon larger than maximum was accepted");
        NS_TEST_ASSERT_MSG_EQ(guard.IsConfigured(),
                              false,
                              "Invalid configuration left the guard configured");
        NS_TEST_ASSERT_MSG_EQ(
            guard.Configure({0.006, 10000000, 2000000}),
            true,
            "Valid reconfiguration failed to recover the guard");
        NS_TEST_ASSERT_MSG_EQ(guard.Initialize(2000), true, "Valid guard failed to initialize");
        NS_TEST_ASSERT_MSG_EQ(guard.GetAvailableBalanceUs(-1.0).has_value(),
                              false,
                              "Negative outstanding reservation was accepted");
        NS_TEST_ASSERT_MSG_EQ(
            guard.CanReserve(std::numeric_limits<double>::quiet_NaN(), 0.0),
            false,
            "NaN reservation estimate was admitted");

        NS_TEST_ASSERT_MSG_EQ(guard.Refill(1999),
                              false,
                              "Regressing timestamp was accepted");
        NS_TEST_ASSERT_MSG_EQ(guard.IsOperational(),
                              false,
                              "Timestamp regression did not close the guard");
        NS_TEST_ASSERT_MSG_EQ(guard.CanReserve(1.0, 0.0),
                              false,
                              "Closed guard admitted a reservation");
        NS_TEST_ASSERT_MSG_EQ(guard.DebitMeasuredAirtime(3000, 1.0),
                              false,
                              "Closed guard accepted a measured debit");
    }
};

/**
 * Focused suite for reusable secondary-airtime primitives.
 *
 * @ingroup tests
 */
class SecondaryAirtimePrimitivesTestSuite : public TestSuite
{
  public:
    /** Constructor. */
    SecondaryAirtimePrimitivesTestSuite()
        : TestSuite("wifi-streaming-secondary-airtime-primitives", Type::UNIT)
    {
        AddTestCase(new CanonicalSecondaryAirtimeEstimatorTestCase(), Duration::QUICK);
        AddTestCase(new SecondaryAirtimeBudgetGuardAccountingTestCase(), Duration::QUICK);
        AddTestCase(new SecondaryAirtimeBudgetGuardFailClosedTestCase(), Duration::QUICK);
    }
};

static SecondaryAirtimePrimitivesTestSuite g_secondaryAirtimePrimitivesTestSuite;

} // namespace ns3
