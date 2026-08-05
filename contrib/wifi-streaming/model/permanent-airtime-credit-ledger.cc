/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "permanent-airtime-credit-ledger.h"

#include <algorithm>
#include <cmath>

namespace ns3
{

PermanentAirtimeCreditLedger::PermanentAirtimeCreditLedger(
    Configuration configuration) noexcept
{
    Configure(configuration);
}

bool
PermanentAirtimeCreditLedger::Configure(Configuration configuration) noexcept
{
    Reset();
    if (!std::isfinite(configuration.refillFraction) ||
        !(configuration.refillFraction > 0) || configuration.refillFraction > 1 ||
        !std::isfinite(configuration.positiveBalanceCapacityUs) ||
        !(configuration.positiveBalanceCapacityUs > 0) ||
        !std::isfinite(configuration.initialCreditUs) ||
        !(configuration.initialCreditUs > 0) ||
        configuration.initialCreditUs > configuration.positiveBalanceCapacityUs ||
        configuration.repaymentStopNs == 0)
    {
        return false;
    }
    m_configuration = configuration;
    m_configured = true;
    m_stateValid = true;
    return true;
}

void
PermanentAirtimeCreditLedger::Reset() noexcept
{
    *this = PermanentAirtimeCreditLedger();
}

bool
PermanentAirtimeCreditLedger::Initialize(uint64_t startNs) noexcept
{
    if (!m_configured || !m_stateValid || m_initialized ||
        startNs >= m_configuration.repaymentStopNs)
    {
        FailClosed();
        return false;
    }
    m_startTimeNs = startNs;
    m_lastAccountingTimeNs = startNs;
    m_balanceUs = m_configuration.initialCreditUs;
    m_minimumBalanceUs = m_balanceUs;
    m_initialized = true;
    return true;
}

bool
PermanentAirtimeCreditLedger::Advance(uint64_t nowNs) noexcept
{
    if (!IsOperational() || nowNs < m_lastAccountingTimeNs ||
        nowNs > m_configuration.repaymentStopNs)
    {
        FailClosed();
        return false;
    }
    if (nowNs == m_lastAccountingTimeNs)
    {
        return true;
    }
    const double elapsedUs =
        static_cast<double>(nowNs - m_lastAccountingTimeNs) / 1000.0;
    const double generatedUs = m_configuration.refillFraction * elapsedUs;
    const double uncappedUs = m_balanceUs + generatedUs;
    const double newBalanceUs =
        std::min(m_configuration.positiveBalanceCapacityUs, uncappedUs);
    const double discardedUs = std::max(0.0, uncappedUs - newBalanceUs);
    const double totalGeneratedUs = m_generatedRefillUs + generatedUs;
    const double totalDiscardedUs = m_discardedRefillUs + discardedUs;
    if (!std::isfinite(generatedUs) || !std::isfinite(newBalanceUs) ||
        !std::isfinite(totalGeneratedUs) || !std::isfinite(totalDiscardedUs))
    {
        FailClosed();
        return false;
    }
    m_balanceUs = newBalanceUs;
    m_generatedRefillUs = totalGeneratedUs;
    m_discardedRefillUs = totalDiscardedUs;
    m_lastAccountingTimeNs = nowNs;
    return true;
}

bool
PermanentAirtimeCreditLedger::CanDebit(double reservationUs) const noexcept
{
    if (!std::isfinite(reservationUs) || !(reservationUs > 0))
    {
        return false;
    }
    const auto repayableUs = GetRepayableCreditUs();
    return repayableUs && *repayableUs >= reservationUs;
}

bool
PermanentAirtimeCreditLedger::Debit(double reservationUs) noexcept
{
    if (!CanDebit(reservationUs))
    {
        FailClosed();
        return false;
    }
    const double newBalanceUs = m_balanceUs - reservationUs;
    const double totalDebitedUs = m_permanentDebitedUs + reservationUs;
    if (!std::isfinite(newBalanceUs) || !std::isfinite(totalDebitedUs))
    {
        FailClosed();
        return false;
    }
    m_balanceUs = newBalanceUs;
    m_minimumBalanceUs = std::min(m_minimumBalanceUs, m_balanceUs);
    m_permanentDebitedUs = totalDebitedUs;
    ++m_debitCount;
    const auto remainingUs = GetRemainingRefillUs();
    if (!remainingUs || m_balanceUs < -*remainingUs)
    {
        FailClosed();
        return false;
    }
    return true;
}

bool
PermanentAirtimeCreditLedger::Finalize() noexcept
{
    if (!IsOperational() ||
        !Advance(m_configuration.repaymentStopNs) || m_balanceUs < 0)
    {
        FailClosed();
        return false;
    }
    m_finalized = true;
    return true;
}

bool
PermanentAirtimeCreditLedger::IsConfigured() const noexcept
{
    return m_configured;
}

bool
PermanentAirtimeCreditLedger::IsInitialized() const noexcept
{
    return m_initialized;
}

bool
PermanentAirtimeCreditLedger::IsOperational() const noexcept
{
    return m_configured && m_initialized && m_stateValid && !m_finalized;
}

bool
PermanentAirtimeCreditLedger::IsFinalized() const noexcept
{
    return m_finalized && m_stateValid;
}

double
PermanentAirtimeCreditLedger::GetRefillFraction() const noexcept
{
    return m_configured ? m_configuration.refillFraction : 0;
}

double
PermanentAirtimeCreditLedger::GetPositiveBalanceCapacityUs() const noexcept
{
    return m_configured ? m_configuration.positiveBalanceCapacityUs : 0;
}

double
PermanentAirtimeCreditLedger::GetInitialCreditUs() const noexcept
{
    return m_configured ? m_configuration.initialCreditUs : 0;
}

uint64_t
PermanentAirtimeCreditLedger::GetRepaymentStopNs() const noexcept
{
    return m_configured ? m_configuration.repaymentStopNs : 0;
}

uint64_t
PermanentAirtimeCreditLedger::GetStartTimeNs() const noexcept
{
    return m_initialized ? m_startTimeNs : 0;
}

uint64_t
PermanentAirtimeCreditLedger::GetLastAccountingTimeNs() const noexcept
{
    return m_initialized ? m_lastAccountingTimeNs : 0;
}

double
PermanentAirtimeCreditLedger::GetBalanceUs() const noexcept
{
    return m_balanceUs;
}

double
PermanentAirtimeCreditLedger::GetDebtUs() const noexcept
{
    return std::max(0.0, -m_balanceUs);
}

double
PermanentAirtimeCreditLedger::GetMinimumBalanceUs() const noexcept
{
    return m_minimumBalanceUs;
}

double
PermanentAirtimeCreditLedger::GetPeakDebtUs() const noexcept
{
    return std::max(0.0, -m_minimumBalanceUs);
}

double
PermanentAirtimeCreditLedger::GetPermanentDebitedUs() const noexcept
{
    return m_permanentDebitedUs;
}

uint64_t
PermanentAirtimeCreditLedger::GetDebitCount() const noexcept
{
    return m_debitCount;
}

double
PermanentAirtimeCreditLedger::GetGeneratedRefillUs() const noexcept
{
    return m_generatedRefillUs;
}

double
PermanentAirtimeCreditLedger::GetDiscardedRefillUs() const noexcept
{
    return m_discardedRefillUs;
}

std::optional<double>
PermanentAirtimeCreditLedger::GetRemainingRefillUs() const noexcept
{
    if (!IsOperational() ||
        m_lastAccountingTimeNs > m_configuration.repaymentStopNs)
    {
        return std::nullopt;
    }
    const double remainingUs = static_cast<double>(
                                   m_configuration.repaymentStopNs -
                                   m_lastAccountingTimeNs) /
                               1000.0;
    const double refillUs = m_configuration.refillFraction * remainingUs;
    return std::isfinite(refillUs) ? std::optional<double>(refillUs)
                                   : std::nullopt;
}

std::optional<double>
PermanentAirtimeCreditLedger::GetRepayableCreditUs() const noexcept
{
    const auto remainingUs = GetRemainingRefillUs();
    if (!remainingUs)
    {
        return std::nullopt;
    }
    const double repayableUs = m_balanceUs + *remainingUs;
    return std::isfinite(repayableUs) ? std::optional<double>(repayableUs)
                                      : std::nullopt;
}

std::optional<double>
PermanentAirtimeCreditLedger::GetMaximumGeneratedCreditUs() const noexcept
{
    if (!m_initialized || !m_stateValid)
    {
        return std::nullopt;
    }
    const double durationUs =
        static_cast<double>(m_configuration.repaymentStopNs - m_startTimeNs) /
        1000.0;
    const double maximumUs =
        m_configuration.initialCreditUs + m_configuration.refillFraction * durationUs;
    return std::isfinite(maximumUs) ? std::optional<double>(maximumUs)
                                    : std::nullopt;
}

void
PermanentAirtimeCreditLedger::FailClosed() noexcept
{
    m_stateValid = false;
    m_finalized = false;
}

} // namespace ns3
