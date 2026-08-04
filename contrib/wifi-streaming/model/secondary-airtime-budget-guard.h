/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#ifndef SECONDARY_AIRTIME_BUDGET_GUARD_H
#define SECONDARY_AIRTIME_BUDGET_GUARD_H

#include <cstdint>
#include <optional>

namespace ns3
{

/**
 * @ingroup wifi-streaming
 * Allocation-free token guard for measured secondary airtime.
 *
 * The balance earns airtime at the configured long-run fraction and is capped
 * by the maximum horizon.  Measured airtime is debited from the balance, which
 * may become negative so debt remains visible and must be repaid by later
 * refill.  Outstanding external reservations are subtracted only when
 * computing available admission credit.
 *
 * A default-constructed guard is deliberately unconfigured. Invalid
 * configuration or invalid inputs to state-mutating operations leave or place
 * the guard in a fail-closed state in which no reservation can be admitted.
 * Query-only admission inputs are rejected for that call without invalidating
 * otherwise sound runtime state.
 */
class SecondaryAirtimeBudgetGuard
{
  public:
    /** Complete token-guard configuration. */
    struct Configuration
    {
        double fraction{0};            ///< Long-run airtime fraction in (0, 1].
        uint64_t maxHorizonUs{0};       ///< Horizon defining maximum balance.
        uint64_t initialHorizonUs{0};   ///< Horizon defining startup credit.
    };

    /** Construct an unconfigured, fail-closed guard. */
    SecondaryAirtimeBudgetGuard() = default;

    /**
     * Construct and configure a guard.
     *
     * An invalid configuration leaves the guard unconfigured and fail closed.
     *
     * @param configuration Complete guard configuration.
     */
    explicit SecondaryAirtimeBudgetGuard(Configuration configuration) noexcept;

    /**
     * Replace the configuration and reset all runtime state.
     *
     * @param configuration Complete guard configuration.
     * @return True when the configuration is valid.
     */
    bool Configure(Configuration configuration) noexcept;

    /** Reset to the default unconfigured state. */
    void Reset() noexcept;

    /**
     * Initialize startup credit at a causal timestamp.
     *
     * @param nowNs Initialization timestamp in nanoseconds.
     * @return True on the one valid initialization transition.
     */
    bool Initialize(uint64_t nowNs) noexcept;

    /**
     * Refill through a causal timestamp.
     *
     * A timestamp earlier than the last accepted timestamp permanently closes
     * the current runtime state.
     *
     * @param nowNs Current timestamp in nanoseconds.
     * @return True when the timestamp and state are valid.
     */
    bool Refill(uint64_t nowNs) noexcept;

    /**
     * Test whether one estimate fits after outstanding reservations.
     *
     * @param estimatedUs Proposed reservation in microseconds.
     * @param outstandingReservationsUs Active external reservations.
     * @return True only for a valid, initialized, affordable reservation.
     */
    bool CanReserve(double estimatedUs,
                    double outstandingReservationsUs) const noexcept;

    /**
     * Test whether one estimate fits after bounded future-credit borrowing.
     *
     * The debt limit applies to the balance remaining after both active
     * external reservations and the proposed reservation.  This query does
     * not mutate guard state; measured airtime remains the only debit.
     *
     * @param estimatedUs Proposed reservation in microseconds.
     * @param outstandingReservationsUs Active external reservations.
     * @param maximumDebtUs Maximum permitted negative available balance.
     * @return True only for a valid, initialized reservation within the limit.
     */
    bool CanReserveWithDebtLimit(double estimatedUs,
                                 double outstandingReservationsUs,
                                 double maximumDebtUs) const noexcept;

    /**
     * Debit measured airtime after refilling through its timestamp.
     *
     * @param nowNs Measurement timestamp in nanoseconds.
     * @param measuredUs Measured airtime in microseconds.
     * @return True when the debit was accepted.
     */
    bool DebitMeasuredAirtime(uint64_t nowNs, double measuredUs) noexcept;

    /** @return True when the stored configuration is valid. */
    bool IsConfigured() const noexcept;

    /** @return True after initialization of the current configuration. */
    bool IsInitialized() const noexcept;

    /** @return True when refill, debit, and admission may proceed. */
    bool IsOperational() const noexcept;

    /** @return Configured long-run airtime fraction, or zero when invalid. */
    double GetFraction() const noexcept;

    /** @return Maximum configured horizon in microseconds, or zero. */
    uint64_t GetMaxHorizonUs() const noexcept;

    /** @return Initial configured horizon in microseconds, or zero. */
    uint64_t GetInitialHorizonUs() const noexcept;

    /** @return Maximum token balance in microseconds, or zero. */
    double GetCapacityUs() const noexcept;

    /** @return Startup credit in microseconds, or zero. */
    double GetInitialCreditUs() const noexcept;

    /** @return Current earned balance, which may be negative. */
    double GetBalanceUs() const noexcept;

    /** @return Current debt, equal to the negative balance magnitude. */
    double GetDebtUs() const noexcept;

    /** @return Largest observed debt in the current configuration. */
    double GetPeakDebtUs() const noexcept;

    /** @return Total accepted measured-airtime debits in microseconds. */
    double GetMeasuredAirtimeDebitedUs() const noexcept;

    /**
     * Return unreserved balance when state and input are valid.
     *
     * @param outstandingReservationsUs Active external reservations.
     * @return Balance minus reservations, or empty in fail-closed state.
     */
    std::optional<double> GetAvailableBalanceUs(
        double outstandingReservationsUs) const noexcept;

  private:
    /** Mark the current runtime state permanently fail closed. */
    void FailClosed() noexcept;

    Configuration m_configuration; ///< Current validated configuration.
    double m_capacityUs{0};         ///< Maximum earned balance.
    double m_initialCreditUs{0};    ///< Credit installed at initialization.
    double m_balanceUs{0};          ///< Earned credit minus measured airtime.
    double m_peakDebtUs{0};         ///< Largest negative-balance magnitude.
    double m_measuredDebitedUs{0};  ///< Cumulative accepted measured airtime.
    uint64_t m_lastRefillTimeNs{0}; ///< Last accepted causal timestamp.
    bool m_configured{false};       ///< Whether configuration validation passed.
    bool m_initialized{false};      ///< Whether startup credit was installed.
    bool m_stateValid{false};       ///< Whether runtime invariants still hold.
};

} // namespace ns3

#endif // SECONDARY_AIRTIME_BUDGET_GUARD_H
