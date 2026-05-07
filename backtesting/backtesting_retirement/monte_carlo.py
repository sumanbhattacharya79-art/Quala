"""
Monte Carlo simulation for retirement: how long the portfolio will last.
Monthly: sample log-return (log-normal price), sample yield (normal); apply withdrawal.
Output: distribution of years until depletion and path summaries.
"""

from __future__ import annotations

import datetime
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from .types import RetirementConfig, RetirementResult


def simulate_retirement(
    log_return_mean: float,
    log_return_stdev: float,
    yield_mean: float,
    yield_stdev: float,
    config: RetirementConfig,
    summary_quantiles: Tuple[float, ...] = (0.1, 0.5, 0.9),
) -> RetirementResult:
    """
    Run MC simulation: each month total return = exp(log_return) - 1 + monthly_yield,
    then subtract inflation-adjusted withdrawal. Track years until value <= 0.
    yield_mean / yield_stdev are monthly dividend yield (from historical frequency-adjusted series).
    """
    rng = np.random.default_rng(config.seed)
    n_sims = config.n_sims
    max_months = config.max_years * 12
    monthly_inflation = (1 + config.inflation_rate) ** (1 / 12) - 1

    # One-time expenses: same formulas as growth MC with months_in_year_0 = 12 (retirement starts at t=0;
    # year 0 = months 0–11; "in N years" = end of year N after year 0, i.e. month 11 + N*12).
    now = datetime.datetime.now()
    start_year = (
        config.simulation_calendar_year
        if config.simulation_calendar_year is not None
        else now.year
    )
    start_month = (
        config.simulation_calendar_month
        if config.simulation_calendar_month is not None
        else now.month
    )
    months_in_year_0 = 12
    periods_per_year = 12

    def last_period_of_year_from_start(exp_years: float) -> int:
        if exp_years < 1:
            return min(months_in_year_0 - 1, max_months - 1)
        return min(
            months_in_year_0 - 1 + int(exp_years) * periods_per_year,
            max_months - 1,
        )

    def last_period_of_calendar_year(exp_year: int) -> int:
        years_from_start = exp_year - start_year
        if years_from_start <= 0:
            return min(months_in_year_0 - 1, max_months - 1)
        return min(
            months_in_year_0 - 1 + years_from_start * periods_per_year,
            max_months - 1,
        )

    # Raw lump totals by table year (YoY outflow) and SWR year index (0 = first sim year)
    lump_by_table_year: Dict[int, float] = {}
    lump_by_swr_year: Dict[int, float] = {}
    for exp in config.upcoming_expenses or []:
        exp_years, exp_amount = float(exp[0]), float(exp[1])
        if exp_years >= 1000:
            t_hit = last_period_of_calendar_year(int(exp_years))
        else:
            t_hit = last_period_of_year_from_start(exp_years)
        if t_hit < 0 or t_hit >= max_months:
            continue
        ty = min(t_hit // 12 + 1, config.max_years)
        lump_by_table_year[ty] = lump_by_table_year.get(ty, 0.0) + float(exp_amount)
        swr_y = t_hit // 12
        if 0 <= swr_y < config.max_years:
            lump_by_swr_year[swr_y] = lump_by_swr_year.get(swr_y, 0.0) + float(exp_amount)

    applied_expenses: set[Tuple[float, float]] = set()

    def expense_amount_in_path_units(amount: float, _path_values: np.ndarray) -> float:
        return float(amount)

    def apply_expense_once(
        exp_key: Tuple[float, float], amount: float, val: np.ndarray
    ) -> np.ndarray:
        if exp_key in applied_expenses:
            return val
        applied_expenses.add(exp_key)
        to_subtract = expense_amount_in_path_units(amount, val)
        return np.maximum(val - to_subtract, 0.0)

    # Pre-sample: one log-return and one monthly yield per (sim, month)
    log_returns = rng.normal(log_return_mean, log_return_stdev, size=(n_sims, max_months))
    yield_monthly = rng.normal(yield_mean, max(yield_stdev, 1e-10), size=(n_sims, max_months))

    paths = np.zeros((n_sims, max_months + 1))
    paths[:, 0] = config.initial_value
    years_until_depletion = np.full(n_sims, np.nan)
    max_years = config.max_years
    yearly_price_gain = np.zeros((n_sims, max_years))
    yearly_yield_gain = np.zeros((n_sims, max_years))

    yearly_income = config.yearly_income_monthly
    yearly_misc = getattr(config, "yearly_misc_spending_monthly", None)
    discretionary_rule = getattr(config, "discretionary_spend_if_prior_year_return", None)
    disc_extra = np.zeros((n_sims, max_years))
    base_withdrawal = config.monthly_withdrawal
    withdrawal = base_withdrawal
    for t in range(max_months):
        if t > 0 and t % 12 == 0:
            y_enter = t // 12
            if discretionary_rule is not None and y_enter < max_years and y_enter >= 1:
                # (monthly, min_pct): each retirement year y>=1, add monthly if prior year's total return >= hurdle.
                # (monthly, min_pct, start_age, end_age): same, but only when calendar age is in [start_age, end_age].
                # Legacy (monthly, target_year_1based, min_pct): only that single retirement year (target_year >= 2).
                m_disc = min_pct = None
                legacy_single_year_ok = True
                if len(discretionary_rule) == 3:
                    m_disc, aa, min_pct = discretionary_rule
                    legacy_single_year_ok = aa >= 2 and y_enter == aa - 1
                elif len(discretionary_rule) == 4:
                    m_disc, min_pct, _sa, _ea = discretionary_rule
                elif len(discretionary_rule) == 2:
                    m_disc, min_pct = discretionary_rule
                if (
                    m_disc is not None
                    and min_pct is not None
                    and legacy_single_year_ok
                ):
                    age_ok = True
                    if len(discretionary_rule) == 4:
                        ra = getattr(config, "retirement_age", None)
                        if ra is not None:
                            sim_age = int(ra) + y_enter
                            sa = int(discretionary_rule[2])
                            ea = int(discretionary_rule[3])
                            age_ok = sa <= sim_age <= ea
                    if age_ok:
                        yp = y_enter - 1
                        ps = paths[:, yp * 12]
                        tot_g = yearly_price_gain[:, yp] + yearly_yield_gain[:, yp]
                        valid = ps > 1e-12
                        r_vec = np.zeros(n_sims)
                        r_vec[valid] = tot_g[valid] / ps[valid]
                        thr = float(min_pct) / 100.0
                        ok = valid & (r_vec >= thr)
                        infl = (1.0 + config.inflation_rate) ** float(y_enter)
                        disc_extra[:, y_enter] = 0.0
                        disc_extra[ok, y_enter] = float(m_disc) * infl
        # Total monthly return = price return + yield component
        price_return = np.exp(log_returns[:, t]) - 1.0
        total_return = price_return + yield_monthly[:, t]
        value_start = paths[:, t]
        price_gain = value_start * price_return
        yield_gain = value_start * yield_monthly[:, t]
        year_idx = t // 12
        if year_idx < max_years:
            yearly_price_gain[:, year_idx] += price_gain
            yearly_yield_gain[:, year_idx] += yield_gain
        # Net withdrawal: base + misc - income for this year (each can inflate)
        income_this_year = (
            float(yearly_income[year_idx]) if yearly_income and year_idx < len(yearly_income) else 0.0
        )
        misc_scalar = (
            float(yearly_misc[year_idx]) if yearly_misc and year_idx < len(yearly_misc) else 0.0
        )
        misc_vec = misc_scalar + disc_extra[:, year_idx]
        net_withdrawal = np.maximum(0.0, withdrawal + misc_vec - income_this_year)
        next_value = value_start * (1 + total_return) - net_withdrawal
        # Inflation-adjust base withdrawal for next period (income is applied per-year, inflated in place)
        withdrawal = withdrawal * (1 + monthly_inflation)
        next_value = np.maximum(next_value, 0.0)
        # One-time expenses at end of month t (same timing as growth MC)
        for exp in config.upcoming_expenses or []:
            exp_years, exp_amount = float(exp[0]), float(exp[1])
            exp_key = (exp_years, exp_amount)
            if exp_years >= 1000:
                last_period = last_period_of_calendar_year(int(exp_years))
            else:
                last_period = last_period_of_year_from_start(exp_years)
            if t == last_period:
                next_value = apply_expense_once(exp_key, exp_amount, next_value)
        paths[:, t + 1] = next_value
        # Record first month when depleted
        depleted = (years_until_depletion != years_until_depletion) & (next_value <= 0)
        if depleted.any():
            years_until_depletion[depleted] = (t + 1) / 12.0

    # Fraction that depleted within max_years
    depleted_fraction = np.sum(np.isfinite(years_until_depletion)) / n_sims
    probability_of_success = 1.0 - depleted_fraction

    # Magnitude of failure: for depleted paths, total $ of planned withdrawals not funded
    withdrawal_schedule = np.array([
        config.monthly_withdrawal * ((1 + monthly_inflation) ** t)
        for t in range(max_months)
    ])
    total_planned_withdrawals = float(np.sum(withdrawal_schedule))
    magnitude_of_failure = np.full(n_sims, np.nan)
    goal_completion = np.ones(n_sims)
    for i in range(n_sims):
        if np.isfinite(years_until_depletion[i]):
            dep_month = int(np.ceil(years_until_depletion[i] * 12))
            dep_month = min(dep_month, max_months)
            magnitude_of_failure[i] = float(np.sum(withdrawal_schedule[dep_month:]))
            goal_completion[i] = float(np.sum(withdrawal_schedule[:dep_month])) / total_planned_withdrawals
        else:
            goal_completion[i] = 1.0
    failed_mask = np.isfinite(magnitude_of_failure)
    magnitude_p50 = float(np.quantile(magnitude_of_failure[failed_mask], 0.5)) if failed_mask.any() else None
    magnitude_p90 = float(np.quantile(magnitude_of_failure[failed_mask], 0.9)) if failed_mask.any() else None
    goal_completion_p10 = float(np.quantile(goal_completion, 0.10))
    goal_completion_p50 = float(np.quantile(goal_completion, 0.50))
    goal_completion_p90 = float(np.quantile(goal_completion, 0.90))

    # Sustainable withdrawal rate: annual withdrawal / portfolio value at start of year
    # Year 0: first year withdrawal / initial_value
    annual_withdrawal_year0 = float(np.sum(withdrawal_schedule[:12]))
    lump_y0 = lump_by_swr_year.get(0, 0.0)
    withdrawal_rate_year0 = (
        (annual_withdrawal_year0 + lump_y0) / config.initial_value
        if config.initial_value > 0
        else 0
    )
    withdrawal_rates_by_year = []
    for y in range(max_years):
        month_start = y * 12
        if month_start >= paths.shape[1]:
            break
        port_val = paths[:, month_start]
        annual_w = float(np.sum(withdrawal_schedule[month_start : month_start + 12]))
        annual_w += lump_by_swr_year.get(y, 0.0)
        rate = np.full(n_sims, np.nan)
        valid = port_val > 0
        rate[valid] = annual_w / port_val[valid]
        p10_r = float(np.nanquantile(rate, 0.10))
        p50_r = float(np.nanquantile(rate, 0.50))
        p90_r = float(np.nanquantile(rate, 0.90))
        pv = port_val[valid]
        if pv.size:
            portfolio_p10 = float(np.quantile(pv, 0.10))
            portfolio_p50 = float(np.quantile(pv, 0.50))
            portfolio_p90 = float(np.quantile(pv, 0.90))
        else:
            portfolio_p10 = portfolio_p50 = portfolio_p90 = 0.0
        month_after_year = (y + 1) * 12
        if month_after_year < paths.shape[1]:
            port_end = paths[:, month_after_year]
            ve = port_end[np.isfinite(port_end)]
            if ve.size:
                portfolio_end_p10 = float(np.quantile(ve, 0.10))
                portfolio_end_p50 = float(np.quantile(ve, 0.50))
                portfolio_end_p90 = float(np.quantile(ve, 0.90))
            else:
                portfolio_end_p10 = portfolio_end_p50 = portfolio_end_p90 = 0.0
        else:
            portfolio_end_p10 = portfolio_end_p50 = portfolio_end_p90 = 0.0
        withdrawal_rates_by_year.append({
            "year": y,
            "p10": p10_r,
            "p50": p50_r,
            "p90": p90_r,
            "portfolio_p10": portfolio_p10,
            "portfolio_p50": portfolio_p50,
            "portfolio_p90": portfolio_p90,
            "safe_withdrawal_p10": float(p10_r * portfolio_p10),
            "safe_withdrawal_p50": float(p50_r * portfolio_p50),
            "safe_withdrawal_p90": float(p90_r * portfolio_p90),
            "portfolio_end_after_year_p10": portfolio_end_p10,
            "portfolio_end_after_year_p50": portfolio_end_p50,
            "portfolio_end_after_year_p90": portfolio_end_p90,
        })

    # Time-weighted return (TWR) per simulation: geometric linking of period returns (excludes withdrawals)
    # TWR = (1+R1)(1+R2)...(1+Rn) - 1, annualized as (1+TWR)^(1/years) - 1
    years = max_months / 12.0
    monthly_returns = np.exp(log_returns) - 1.0 + yield_monthly
    twr_growth = np.prod(1.0 + monthly_returns, axis=1)
    twr_annual = twr_growth ** (1.0 / years) - 1.0
    twr_p10 = float(np.quantile(twr_annual, 0.10))
    twr_p50 = float(np.quantile(twr_annual, 0.50))
    twr_p90 = float(np.quantile(twr_annual, 0.90))

    # Path summaries: mean and quantiles over time
    summary_paths = {"mean": pd.Series(paths.mean(axis=0))}
    for q in summary_quantiles:
        key = f"p{int(q * 100)}"
        summary_paths[key] = pd.Series(np.quantile(paths, q, axis=0))

    # Yearly $ price/yield: quantiles include depleted paths (they contribute $0 gain).
    summary_yearly_price = {
        "mean": pd.Series(yearly_price_gain.mean(axis=0)),
        "p10": pd.Series(np.quantile(yearly_price_gain, 0.10, axis=0)),
        "p50": pd.Series(np.quantile(yearly_price_gain, 0.50, axis=0)),
        "p90": pd.Series(np.quantile(yearly_price_gain, 0.90, axis=0)),
    }
    # Per-year TWR (price return %): 0% when no balance at year start (included in quantiles).
    yearly_twr_pct = np.zeros((n_sims, max_years))
    for y in range(max_years):
        port_start = paths[:, y * 12]
        valid = port_start > 0
        yearly_twr_pct[valid, y] = yearly_price_gain[valid, y] / port_start[valid]
    summary_yearly_twr = {
        "p10": pd.Series(np.quantile(yearly_twr_pct, 0.10, axis=0)),
        "p50": pd.Series(np.quantile(yearly_twr_pct, 0.50, axis=0)),
        "p90": pd.Series(np.quantile(yearly_twr_pct, 0.90, axis=0)),
    }

    summary_yearly_yield = {
        "mean": pd.Series(yearly_yield_gain.mean(axis=0)),
        "p10": pd.Series(np.quantile(yearly_yield_gain, 0.10, axis=0)),
        "p50": pd.Series(np.quantile(yearly_yield_gain, 0.50, axis=0)),
        "p90": pd.Series(np.quantile(yearly_yield_gain, 0.90, axis=0)),
    }

    # Age of plan failure: retirement_age + years until depletion (or max age if never depleted)
    longevity = pd.Series(years_until_depletion).fillna(max_years)
    age_at_depletion_p10 = None
    age_at_depletion_p50 = None
    age_at_depletion_p90 = None
    if getattr(config, "retirement_age", None) is not None:
        ra = config.retirement_age
        age_at_depletion = ra + longevity
        age_at_depletion_p10 = float(age_at_depletion.quantile(0.10))
        age_at_depletion_p50 = float(age_at_depletion.quantile(0.50))
        age_at_depletion_p90 = float(age_at_depletion.quantile(0.90))

    one_time_lump_by_table_year = [
        float(lump_by_table_year.get(y, 0.0)) for y in range(config.max_years + 1)
    ]

    metadata = {
        "n_sims": n_sims,
        "max_years": config.max_years,
        "initial_value": config.initial_value,
        "monthly_withdrawal_start": config.monthly_withdrawal,
        "log_return_mean": log_return_mean,
        "log_return_stdev": log_return_stdev,
        "yield_mean": yield_mean,
        "yield_stdev": yield_stdev,
        "inflation_rate": config.inflation_rate,
        "twr_p10": twr_p10,
        "twr_p50": twr_p50,
        "twr_p90": twr_p90,
        "probability_of_success": probability_of_success,
        "magnitude_of_failure_p50": magnitude_p50,
        "magnitude_of_failure_p90": magnitude_p90,
        "goal_completion_p10": goal_completion_p10,
        "goal_completion_p50": goal_completion_p50,
        "goal_completion_p90": goal_completion_p90,
        "withdrawal_rate_year0": withdrawal_rate_year0,
        "withdrawal_rates_by_year": withdrawal_rates_by_year,
        "age_at_depletion_p10": age_at_depletion_p10,
        "age_at_depletion_p50": age_at_depletion_p50,
        "age_at_depletion_p90": age_at_depletion_p90,
        "one_time_lump_by_table_year": one_time_lump_by_table_year,
        "simulation_calendar_year": start_year,
        "simulation_calendar_month": start_month,
        "discretionary_spend_if_prior_year_return": discretionary_rule,
    }

    # Sample paths for spaghetti plot: years on x-axis, portfolio value on y-axis
    max_spaghetti = 150
    step = max(1, n_sims // max_spaghetti)
    path_indices = np.arange(0, n_sims, step)[:max_spaghetti]
    year_indices = np.minimum(
        np.arange(0, max_months + 1, 12),
        max_months,
    )
    paths_sample = paths[np.ix_(path_indices, year_indices)]

    return RetirementResult(
        years_until_depletion=pd.Series(years_until_depletion),
        depleted_fraction=float(depleted_fraction),
        summary_paths=summary_paths,
        metadata=metadata,
        twr_p10=twr_p10,
        twr_p50=twr_p50,
        twr_p90=twr_p90,
        summary_yearly_price=summary_yearly_price,
        summary_yearly_yield=summary_yearly_yield,
        summary_yearly_twr=summary_yearly_twr,
        yearly_price_gain=yearly_price_gain,
        yearly_yield_gain=yearly_yield_gain,
        paths_sample=paths_sample.tolist(),
        paths_sample_years=list(range(len(year_indices))),
    )
