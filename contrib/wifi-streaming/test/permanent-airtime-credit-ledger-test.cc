/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "ns3/permanent-airtime-credit-ledger.h"
#include "ns3/test.h"

#include "temporal-t2-distribution-model-goldens-v1.h"

#include <cmath>
#include <cstdint>
#include <limits>
#include <string_view>

namespace ns3
{
namespace
{

constexpr uint64_t START_NS = 1000000000;
constexpr uint64_t STOP_NS = 61000000000;
constexpr double REFILL_FRACTION = 0.006;
constexpr double CAPACITY_US = 360000.0;
constexpr double INITIAL_CREDIT_US = 12000.0;
constexpr double CANONICAL_RESERVATION_US = 1983.760667318285;

uint64_t
AtUs(double relativeUs)
{
    return START_NS + static_cast<uint64_t>(relativeUs * 1000.0);
}

PermanentAirtimeCreditLedger
MakeLedger(double initialCreditUs = INITIAL_CREDIT_US,
           uint64_t startNs = START_NS,
           uint64_t stopNs = STOP_NS)
{
    PermanentAirtimeCreditLedger ledger(
        {REFILL_FRACTION, CAPACITY_US, initialCreditUs, stopNs});
    ledger.Initialize(startNs);
    return ledger;
}

} // namespace

/**
 * @ingroup tests
 * Reproduce all generated deployment credit transitions through public state.
 */
class PermanentAirtimeCreditLedgerGoldenTestCase : public TestCase
{
  public:
    /** Constructor. */
    PermanentAirtimeCreditLedgerGoldenTestCase()
        : TestCase("Permanent airtime ledger matches borrow-repay goldens")
    {
    }

  private:
    /**
     * Compare one complete pre-debit accounting state.
     *
     * @param ledger Ledger to inspect.
     * @param expectedBalanceUs Expected current balance.
     * @param expectedRemainingUs Expected deterministic remaining refill.
     * @param expectedRepayableUs Expected balance plus remaining refill.
     * @param context Failure-message prefix.
     */
    void AssertState(const PermanentAirtimeCreditLedger& ledger,
                     double expectedBalanceUs,
                     double expectedRemainingUs,
                     double expectedRepayableUs,
                     std::string_view context)
    {
        const auto remainingUs = ledger.GetRemainingRefillUs();
        const auto repayableUs = ledger.GetRepayableCreditUs();
        NS_TEST_ASSERT_MSG_EQ(remainingUs.has_value(),
                              true,
                              context << ": remaining refill is absent");
        NS_TEST_ASSERT_MSG_EQ(repayableUs.has_value(),
                              true,
                              context << ": repayable credit is absent");
        NS_TEST_ASSERT_MSG_EQ_TOL(ledger.GetBalanceUs(),
                                  expectedBalanceUs,
                                  1e-9,
                                  context << ": balance differs");
        NS_TEST_ASSERT_MSG_EQ_TOL(*remainingUs,
                                  expectedRemainingUs,
                                  1e-9,
                                  context << ": remaining refill differs");
        NS_TEST_ASSERT_MSG_EQ_TOL(*repayableUs,
                                  expectedRepayableUs,
                                  1e-9,
                                  context << ": repayable credit differs");
    }

    void DoRun() override
    {
        using temporal_t2_distribution_model_goldens_v1::g_creditCases;
        NS_TEST_ASSERT_MSG_EQ(g_creditCases.size(), 4, "Credit golden count differs");

        {
            const auto& golden = g_creditCases[0];
            auto ledger = MakeLedger();
            NS_TEST_ASSERT_MSG_EQ(ledger.Advance(AtUs(golden.decisionTimeUs)),
                                  true,
                                  "Initial golden advance failed");
            AssertState(ledger,
                        golden.expectedRefilledBalanceUs,
                        golden.expectedRemainingRefillUs,
                        golden.expectedRepayableCreditUs,
                        golden.label);
            NS_TEST_ASSERT_MSG_EQ(ledger.CanDebit(golden.reservationUs),
                                  golden.expectedAdmitted,
                                  "Initial golden admission differs");
            NS_TEST_ASSERT_MSG_EQ(ledger.Debit(golden.reservationUs),
                                  true,
                                  "Initial golden debit failed");
            NS_TEST_ASSERT_MSG_EQ_TOL(ledger.GetBalanceUs(),
                                      golden.expectedBalanceAfterUs,
                                      1e-9,
                                      "Initial golden post-debit balance differs");
            NS_TEST_ASSERT_MSG_EQ(ledger.Finalize(),
                                  true,
                                  "Initial golden did not repay");
            NS_TEST_ASSERT_MSG_EQ_TOL(ledger.GetBalanceUs(),
                                      golden.expectedFinalBalanceUs,
                                      1e-9,
                                      "Initial golden final balance differs");
        }

        {
            const auto& golden = g_creditCases[1];
            auto ledger = MakeLedger();
            NS_TEST_ASSERT_MSG_EQ(ledger.Advance(AtUs(golden.priorTimeUs)),
                                  true,
                                  "Debt setup advance failed");
            NS_TEST_ASSERT_MSG_EQ(ledger.Debit(122000.0),
                                  true,
                                  "Debt setup debit failed");
            NS_TEST_ASSERT_MSG_EQ_TOL(ledger.GetBalanceUs(),
                                      golden.priorBalanceUs,
                                      1e-9,
                                      "Debt golden prior balance differs");
            NS_TEST_ASSERT_MSG_EQ(ledger.Advance(AtUs(golden.decisionTimeUs)),
                                  true,
                                  "Debt golden decision advance failed");
            AssertState(ledger,
                        golden.expectedRefilledBalanceUs,
                        golden.expectedRemainingRefillUs,
                        golden.expectedRepayableCreditUs,
                        golden.label);
            NS_TEST_ASSERT_MSG_EQ(ledger.Debit(golden.reservationUs),
                                  true,
                                  "Debt golden debit failed");
            NS_TEST_ASSERT_MSG_EQ_TOL(ledger.GetBalanceUs(),
                                      golden.expectedBalanceAfterUs,
                                      1e-9,
                                      "Debt golden post-debit balance differs");
            NS_TEST_ASSERT_MSG_EQ(ledger.Finalize(),
                                  true,
                                  "Debt golden did not repay");
            NS_TEST_ASSERT_MSG_EQ_TOL(ledger.GetBalanceUs(),
                                      golden.expectedFinalBalanceUs,
                                      1e-9,
                                      "Debt golden final balance differs");
        }

        {
            const auto& golden = g_creditCases[2];
            auto ledger = MakeLedger(golden.priorBalanceUs,
                                     AtUs(golden.priorTimeUs),
                                     STOP_NS);
            NS_TEST_ASSERT_MSG_EQ(ledger.Advance(AtUs(golden.decisionTimeUs)),
                                  true,
                                  "Capacity golden advance failed");
            AssertState(ledger,
                        golden.expectedRefilledBalanceUs,
                        golden.expectedRemainingRefillUs,
                        golden.expectedRepayableCreditUs,
                        golden.label);
            NS_TEST_ASSERT_MSG_EQ_TOL(ledger.GetDiscardedRefillUs(),
                                      20000.0,
                                      1e-9,
                                      "Capacity golden discarded refill differs");
            NS_TEST_ASSERT_MSG_EQ(ledger.Debit(golden.reservationUs),
                                  true,
                                  "Capacity golden debit failed");
            NS_TEST_ASSERT_MSG_EQ_TOL(ledger.GetBalanceUs(),
                                      golden.expectedBalanceAfterUs,
                                      1e-9,
                                      "Capacity golden post-debit balance differs");
            NS_TEST_ASSERT_MSG_EQ(ledger.Finalize(),
                                  true,
                                  "Capacity golden did not finalize");
            NS_TEST_ASSERT_MSG_EQ_TOL(ledger.GetBalanceUs(),
                                      golden.expectedFinalBalanceUs,
                                      1e-9,
                                      "Capacity golden final balance differs");
        }

        {
            const auto& golden = g_creditCases[3];
            auto ledger = MakeLedger();
            NS_TEST_ASSERT_MSG_EQ(ledger.Advance(AtUs(golden.priorTimeUs)),
                                  true,
                                  "Horizon setup advance failed");
            NS_TEST_ASSERT_MSG_EQ(ledger.Debit(365000.0),
                                  true,
                                  "Horizon setup debit failed");
            NS_TEST_ASSERT_MSG_EQ_TOL(ledger.GetBalanceUs(),
                                      golden.priorBalanceUs,
                                      1e-9,
                                      "Horizon golden prior balance differs");
            NS_TEST_ASSERT_MSG_EQ(ledger.Advance(AtUs(golden.decisionTimeUs)),
                                  true,
                                  "Horizon golden decision advance failed");
            AssertState(ledger,
                        golden.expectedRefilledBalanceUs,
                        golden.expectedRemainingRefillUs,
                        golden.expectedRepayableCreditUs,
                        golden.label);
            NS_TEST_ASSERT_MSG_EQ(ledger.CanDebit(golden.reservationUs),
                                  golden.expectedAdmitted,
                                  "Horizon golden admission differs");
            NS_TEST_ASSERT_MSG_EQ_TOL(ledger.GetBalanceUs(),
                                      golden.expectedBalanceAfterUs,
                                      1e-9,
                                      "Rejected golden changed balance");
            NS_TEST_ASSERT_MSG_EQ(ledger.Finalize(),
                                  true,
                                  "Horizon golden did not finalize");
            NS_TEST_ASSERT_MSG_EQ_TOL(ledger.GetBalanceUs(),
                                      golden.expectedFinalBalanceUs,
                                      1e-9,
                                      "Horizon golden final balance differs");
        }
    }
};

/**
 * @ingroup tests
 * Verify permanent debits, diagnostics, and fail-closed transitions.
 */
class PermanentAirtimeCreditLedgerInvariantTestCase : public TestCase
{
  public:
    /** Constructor. */
    PermanentAirtimeCreditLedgerInvariantTestCase()
        : TestCase("Permanent airtime ledger enforces repayment and no refunds")
    {
    }

  private:
    void DoRun() override
    {
        auto ledger = MakeLedger();
        const auto maximumUs = ledger.GetMaximumGeneratedCreditUs();
        NS_TEST_ASSERT_MSG_EQ(maximumUs.has_value(),
                              true,
                              "Maximum generated credit is absent");
        NS_TEST_ASSERT_MSG_EQ_TOL(*maximumUs,
                                  372000.0,
                                  0.0,
                                  "Maximum generated credit differs");
        NS_TEST_ASSERT_MSG_EQ(ledger.Advance(AtUs(2000.0)),
                              true,
                              "First decision advance failed");
        const double balanceBefore = ledger.GetBalanceUs();
        NS_TEST_ASSERT_MSG_EQ(ledger.CanDebit(CANONICAL_RESERVATION_US),
                              true,
                              "Canonical reservation was rejected");
        NS_TEST_ASSERT_MSG_EQ(ledger.Debit(CANONICAL_RESERVATION_US),
                              true,
                              "Canonical permanent debit failed");
        NS_TEST_ASSERT_MSG_EQ_TOL(ledger.GetPermanentDebitedUs(),
                                  CANONICAL_RESERVATION_US,
                                  0.0,
                                  "Permanent debit total differs");
        NS_TEST_ASSERT_MSG_EQ(ledger.GetDebitCount(),
                              1,
                              "Permanent debit count differs");
        NS_TEST_ASSERT_MSG_EQ_TOL(ledger.GetBalanceUs(),
                                  balanceBefore - CANONICAL_RESERVATION_US,
                                  0.0,
                                  "Permanent debit was not retained");
        NS_TEST_ASSERT_MSG_EQ_TOL(ledger.GetMinimumBalanceUs(),
                                  ledger.GetBalanceUs(),
                                  0.0,
                                  "Minimum balance differs");
        NS_TEST_ASSERT_MSG_EQ_TOL(ledger.GetPeakDebtUs(),
                                  0.0,
                                  0.0,
                                  "Positive debit reported debt");

        const double unchanged = ledger.GetBalanceUs();
        NS_TEST_ASSERT_MSG_EQ(ledger.CanDebit(0.0),
                              false,
                              "Zero reservation was admitted");
        NS_TEST_ASSERT_MSG_EQ(
            ledger.CanDebit(std::numeric_limits<double>::quiet_NaN()),
            false,
            "NaN reservation was admitted");
        NS_TEST_ASSERT_MSG_EQ_TOL(ledger.GetBalanceUs(),
                                  unchanged,
                                  0.0,
                                  "Invalid query changed balance");
        NS_TEST_ASSERT_MSG_EQ(ledger.Finalize(),
                              true,
                              "Valid ledger did not finalize");
        NS_TEST_ASSERT_MSG_EQ(ledger.IsFinalized(),
                              true,
                              "Finalized ledger lacks closure state");
        NS_TEST_ASSERT_MSG_EQ(ledger.IsOperational(),
                              false,
                              "Finalized ledger remained operational");
        NS_TEST_ASSERT_MSG_EQ_TOL(ledger.GetPermanentDebitedUs(),
                                  CANONICAL_RESERVATION_US,
                                  0.0,
                                  "Stop-time refill changed permanent debit");

        PermanentAirtimeCreditLedger invalid;
        NS_TEST_ASSERT_MSG_EQ(invalid.IsConfigured(),
                              false,
                              "Default ledger was configured");
        NS_TEST_ASSERT_MSG_EQ(
            invalid.Configure({0.0, CAPACITY_US, INITIAL_CREDIT_US, STOP_NS}),
            false,
            "Zero refill was accepted");
        NS_TEST_ASSERT_MSG_EQ(
            invalid.Configure({REFILL_FRACTION,
                               INITIAL_CREDIT_US - 1.0,
                               INITIAL_CREDIT_US,
                               STOP_NS}),
            false,
            "Initial credit beyond capacity was accepted");
        NS_TEST_ASSERT_MSG_EQ(
            invalid.Configure({REFILL_FRACTION,
                               CAPACITY_US,
                               INITIAL_CREDIT_US,
                               STOP_NS}),
            true,
            "Valid ledger recovery failed");
        NS_TEST_ASSERT_MSG_EQ(invalid.Initialize(START_NS),
                              true,
                              "Recovered ledger did not initialize");
        NS_TEST_ASSERT_MSG_EQ(invalid.Advance(START_NS - 1),
                              false,
                              "Regressing timestamp was accepted");
        NS_TEST_ASSERT_MSG_EQ(invalid.IsOperational(),
                              false,
                              "Regressing timestamp did not fail closed");

        auto unaffordable = MakeLedger();
        NS_TEST_ASSERT_MSG_EQ(unaffordable.Debit(372000.001),
                              false,
                              "Unaffordable permanent debit was accepted");
        NS_TEST_ASSERT_MSG_EQ(unaffordable.IsOperational(),
                              false,
                              "Unaffordable debit did not fail closed");

        auto late = MakeLedger();
        NS_TEST_ASSERT_MSG_EQ(late.Advance(STOP_NS + 1),
                              false,
                              "Post-stop accounting was accepted");
        NS_TEST_ASSERT_MSG_EQ(late.IsOperational(),
                              false,
                              "Post-stop accounting did not fail closed");
    }
};

/**
 * @ingroup tests
 * Focused suite for permanent borrow-and-repay accounting.
 */
class PermanentAirtimeCreditLedgerTestSuite : public TestSuite
{
  public:
    /** Constructor. */
    PermanentAirtimeCreditLedgerTestSuite()
        : TestSuite("wifi-streaming-permanent-airtime-credit-ledger", Type::UNIT)
    {
        AddTestCase(new PermanentAirtimeCreditLedgerGoldenTestCase, Duration::QUICK);
        AddTestCase(new PermanentAirtimeCreditLedgerInvariantTestCase, Duration::QUICK);
    }
};

static PermanentAirtimeCreditLedgerTestSuite g_permanentAirtimeCreditLedgerTestSuite;

} // namespace ns3
