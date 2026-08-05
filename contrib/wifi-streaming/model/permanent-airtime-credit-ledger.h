/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef PERMANENT_AIRTIME_CREDIT_LEDGER_H
#define PERMANENT_AIRTIME_CREDIT_LEDGER_H

#include <cstdint>
#include <optional>

namespace ns3
{

/**
 * @ingroup wifi-streaming
 * Repayment-enforced airtime ledger whose accepted reservations are permanent.
 *
 * The ledger earns causal credit, caps only positive carry-over, and permits a
 * reservation to make the current balance negative exactly when deterministic
 * refill through the configured stop can repay it.  Accepted reservations are
 * never released or replaced by measured airtime; measurement remains a
 * separate evidence channel.
 *
 * Invalid configuration or a bad state-mutating transition permanently closes
 * the current state.  Invalid query inputs are rejected without changing an
 * otherwise operational ledger.
 */
class PermanentAirtimeCreditLedger
{
  public:
    /** Complete permanent-credit configuration. */
    struct Configuration
    {
        double refillFraction{0}; ///< Credit earned per elapsed microsecond.
        double positiveBalanceCapacityUs{0}; ///< Maximum nonnegative carry-over.
        double initialCreditUs{0}; ///< Credit installed at measurement start.
        uint64_t repaymentStopNs{0}; ///< Exclusive final repayment timestamp.
    };

    /** Construct an unconfigured, fail-closed ledger. */
    PermanentAirtimeCreditLedger() = default;

    /**
     * Construct and configure a ledger.
     *
     * @param configuration Complete ledger configuration.
     */
    explicit PermanentAirtimeCreditLedger(Configuration configuration) noexcept;

    /**
     * Replace configuration and reset all runtime state.
     *
     * @param configuration Complete ledger configuration.
     * @return True when the configuration is valid.
     */
    bool Configure(Configuration configuration) noexcept;

    /** Reset to the default unconfigured state. */
    void Reset() noexcept;

    /**
     * Install startup credit at the causal measurement start.
     *
     * @param startNs Inclusive measurement-start timestamp.
     * @return True on the one valid initialization transition.
     */
    bool Initialize(uint64_t startNs) noexcept;

    /**
     * Add only credit earned through one nondecreasing causal timestamp.
     *
     * @param nowNs Timestamp no later than the repayment stop.
     * @return True when the transition is valid.
     */
    bool Advance(uint64_t nowNs) noexcept;

    /**
     * Test whether one permanent debit can be repaid by the frozen stop.
     *
     * @param reservationUs Positive canonical reservation.
     * @return True only when the current balance plus remaining refill covers
     *         the debit.
     */
    bool CanDebit(double reservationUs) const noexcept;

    /**
     * Permanently debit one pre-admitted canonical reservation.
     *
     * The caller first advances to the decision timestamp, tests admission,
     * and requests the sender action.  This transition follows only a
     * successful sender launch and deliberately has no release counterpart.
     *
     * @param reservationUs Positive admitted canonical reservation.
     * @return True when the debit preserves repayment feasibility.
     */
    bool Debit(double reservationUs) noexcept;

    /**
     * Advance through the repayment stop and prove nonnegative closure.
     *
     * @return True only when all permanent debits are fully repaid.
     */
    bool Finalize() noexcept;

    /** @return True when stored configuration is valid. */
    bool IsConfigured() const noexcept;

    /** @return True after startup credit has been installed. */
    bool IsInitialized() const noexcept;

    /** @return True while causal accounting and debits may proceed. */
    bool IsOperational() const noexcept;

    /** @return True after a successful repayment-stop transition. */
    bool IsFinalized() const noexcept;

    /** @return Configured refill fraction, or zero when unconfigured. */
    double GetRefillFraction() const noexcept;

    /** @return Configured positive-balance capacity, or zero. */
    double GetPositiveBalanceCapacityUs() const noexcept;

    /** @return Configured startup credit, or zero. */
    double GetInitialCreditUs() const noexcept;

    /** @return Configured repayment stop, or zero. */
    uint64_t GetRepaymentStopNs() const noexcept;

    /** @return Measurement start accepted by Initialize(), or zero. */
    uint64_t GetStartTimeNs() const noexcept;

    /** @return Last accepted causal accounting timestamp, or zero. */
    uint64_t GetLastAccountingTimeNs() const noexcept;

    /** @return Current earned balance minus permanent debits. */
    double GetBalanceUs() const noexcept;

    /** @return Current negative-balance magnitude. */
    double GetDebtUs() const noexcept;

    /** @return Most negative balance observed after a debit. */
    double GetMinimumBalanceUs() const noexcept;

    /** @return Largest negative-balance magnitude observed. */
    double GetPeakDebtUs() const noexcept;

    /** @return Sum of every accepted canonical reservation. */
    double GetPermanentDebitedUs() const noexcept;

    /** @return Number of accepted permanent debits. */
    uint64_t GetDebitCount() const noexcept;

    /** @return Gross causally generated refill through the current time. */
    double GetGeneratedRefillUs() const noexcept;

    /** @return Refill discarded while positive balance was at capacity. */
    double GetDiscardedRefillUs() const noexcept;

    /**
     * Return deterministic refill remaining through the frozen stop.
     *
     * @return Remaining refill, or empty outside operational state.
     */
    std::optional<double> GetRemainingRefillUs() const noexcept;

    /**
     * Return current balance plus deterministic remaining refill.
     *
     * @return Repayable credit, or empty outside operational state.
     */
    std::optional<double> GetRepayableCreditUs() const noexcept;

    /**
     * Return startup credit plus all refill generated by the frozen stop.
     *
     * @return Maximum generated credit, or empty before initialization.
     */
    std::optional<double> GetMaximumGeneratedCreditUs() const noexcept;

  private:
    /** Mark the current runtime state permanently fail closed. */
    void FailClosed() noexcept;

    Configuration m_configuration; ///< Current validated configuration.
    uint64_t m_startTimeNs{0}; ///< Accepted causal measurement start.
    uint64_t m_lastAccountingTimeNs{0}; ///< Last accepted causal timestamp.
    double m_balanceUs{0}; ///< Earned credit minus permanent reservations.
    double m_minimumBalanceUs{0}; ///< Smallest post-debit balance.
    double m_permanentDebitedUs{0}; ///< Cumulative accepted reservations.
    double m_generatedRefillUs{0}; ///< Gross causal refill before capacity loss.
    double m_discardedRefillUs{0}; ///< Refill discarded at positive capacity.
    uint64_t m_debitCount{0}; ///< Accepted permanent debit count.
    bool m_configured{false}; ///< Whether configuration validation passed.
    bool m_initialized{false}; ///< Whether startup credit was installed.
    bool m_stateValid{false}; ///< Whether runtime invariants still hold.
    bool m_finalized{false}; ///< Whether repayment closure succeeded.
};

} // namespace ns3

#endif // PERMANENT_AIRTIME_CREDIT_LEDGER_H
