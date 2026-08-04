/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "secondary-airtime-budget-guard.h"

#include <algorithm>
#include <cmath>

namespace ns3
{

SecondaryAirtimeBudgetGuard::SecondaryAirtimeBudgetGuard(
    Configuration configuration) noexcept
{
    Configure(configuration);
}

bool
SecondaryAirtimeBudgetGuard::Configure(Configuration configuration) noexcept
{
    Reset();
    const bool valid = std::isfinite(configuration.fraction) &&
                       configuration.fraction > 0 && configuration.fraction <= 1 &&
                       configuration.maxHorizonUs > 0 &&
                       configuration.initialHorizonUs > 0 &&
                       configuration.initialHorizonUs <= configuration.maxHorizonUs;
    if (!valid)
    {
        return false;
    }
    const double capacityUs =
        configuration.fraction * static_cast<double>(configuration.maxHorizonUs);
    const double initialCreditUs =
        configuration.fraction * static_cast<double>(configuration.initialHorizonUs);
    if (!std::isfinite(capacityUs) || !std::isfinite(initialCreditUs) ||
        !(capacityUs > 0) || !(initialCreditUs > 0) || initialCreditUs > capacityUs)
    {
        return false;
    }
    m_configuration = configuration;
    m_capacityUs = capacityUs;
    m_initialCreditUs = initialCreditUs;
    m_configured = true;
    m_stateValid = true;
    return true;
}

void
SecondaryAirtimeBudgetGuard::Reset() noexcept
{
    *this = SecondaryAirtimeBudgetGuard();
}

bool
SecondaryAirtimeBudgetGuard::Initialize(uint64_t nowNs) noexcept
{
    if (!m_configured || !m_stateValid || m_initialized)
    {
        FailClosed();
        return false;
    }
    m_balanceUs = m_initialCreditUs;
    m_lastRefillTimeNs = nowNs;
    m_initialized = true;
    return true;
}

bool
SecondaryAirtimeBudgetGuard::Refill(uint64_t nowNs) noexcept
{
    if (!IsOperational() || nowNs < m_lastRefillTimeNs)
    {
        FailClosed();
        return false;
    }
    if (nowNs == m_lastRefillTimeNs)
    {
        return true;
    }
    const double elapsedUs =
        static_cast<double>(nowNs - m_lastRefillTimeNs) / 1000.0;
    const double replenishedUs = m_configuration.fraction * elapsedUs;
    const double newBalanceUs =
        std::min(m_capacityUs, m_balanceUs + replenishedUs);
    if (!std::isfinite(newBalanceUs))
    {
        FailClosed();
        return false;
    }
    m_balanceUs = newBalanceUs;
    m_lastRefillTimeNs = nowNs;
    return true;
}

bool
SecondaryAirtimeBudgetGuard::CanReserve(
    double estimatedUs,
    double outstandingReservationsUs) const noexcept
{
    if (!std::isfinite(estimatedUs) || !(estimatedUs > 0))
    {
        return false;
    }
    const auto availableUs = GetAvailableBalanceUs(outstandingReservationsUs);
    return availableUs && *availableUs >= estimatedUs;
}

bool
SecondaryAirtimeBudgetGuard::DebitMeasuredAirtime(uint64_t nowNs,
                                                  double measuredUs) noexcept
{
    if (!std::isfinite(measuredUs) || measuredUs < 0 || !Refill(nowNs))
    {
        FailClosed();
        return false;
    }
    const double newBalanceUs = m_balanceUs - measuredUs;
    const double newMeasuredDebitedUs = m_measuredDebitedUs + measuredUs;
    if (!std::isfinite(newBalanceUs) || !std::isfinite(newMeasuredDebitedUs))
    {
        FailClosed();
        return false;
    }
    m_balanceUs = newBalanceUs;
    m_measuredDebitedUs = newMeasuredDebitedUs;
    m_peakDebtUs = std::max(m_peakDebtUs, GetDebtUs());
    return true;
}

bool
SecondaryAirtimeBudgetGuard::IsConfigured() const noexcept
{
    return m_configured;
}

bool
SecondaryAirtimeBudgetGuard::IsInitialized() const noexcept
{
    return m_initialized;
}

bool
SecondaryAirtimeBudgetGuard::IsOperational() const noexcept
{
    return m_configured && m_initialized && m_stateValid;
}

double
SecondaryAirtimeBudgetGuard::GetFraction() const noexcept
{
    return m_configured ? m_configuration.fraction : 0;
}

uint64_t
SecondaryAirtimeBudgetGuard::GetMaxHorizonUs() const noexcept
{
    return m_configured ? m_configuration.maxHorizonUs : 0;
}

uint64_t
SecondaryAirtimeBudgetGuard::GetInitialHorizonUs() const noexcept
{
    return m_configured ? m_configuration.initialHorizonUs : 0;
}

double
SecondaryAirtimeBudgetGuard::GetCapacityUs() const noexcept
{
    return m_capacityUs;
}

double
SecondaryAirtimeBudgetGuard::GetInitialCreditUs() const noexcept
{
    return m_initialCreditUs;
}

double
SecondaryAirtimeBudgetGuard::GetBalanceUs() const noexcept
{
    return m_balanceUs;
}

double
SecondaryAirtimeBudgetGuard::GetDebtUs() const noexcept
{
    return std::max(0.0, -m_balanceUs);
}

double
SecondaryAirtimeBudgetGuard::GetPeakDebtUs() const noexcept
{
    return m_peakDebtUs;
}

double
SecondaryAirtimeBudgetGuard::GetMeasuredAirtimeDebitedUs() const noexcept
{
    return m_measuredDebitedUs;
}

std::optional<double>
SecondaryAirtimeBudgetGuard::GetAvailableBalanceUs(
    double outstandingReservationsUs) const noexcept
{
    if (!IsOperational() || !std::isfinite(outstandingReservationsUs) ||
        outstandingReservationsUs < 0)
    {
        return std::nullopt;
    }
    const double availableUs = m_balanceUs - outstandingReservationsUs;
    return std::isfinite(availableUs) ? std::optional<double>(availableUs)
                                      : std::nullopt;
}

void
SecondaryAirtimeBudgetGuard::FailClosed() noexcept
{
    m_stateValid = false;
}

} // namespace ns3
