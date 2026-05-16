from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .types import IntakeContext, MonteCarloConfig


try:
    from backend.intake_parser import monthly_recurring_total_at_age_with_yoy as _monthly_recurring_row_total_at_age
except ImportError:  # standalone backtests without app on PYTHONPATH
    _monthly_recurring_row_total_at_age = None


def _monthly_recurring_total_at_age(
    rows: Optional[List[Dict[str, Any]]],
    age: int,
    current_age: Optional[int] = None,
) -> float:
    """Fallback: same rules as app intake_parser (no YoY)."""
    if not rows or age < 0:
        return 0.0
    total = 0.0
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        try:
            monthly = float(raw.get("monthly", 0) or 0)
        except (TypeError, ValueError):
            monthly = 0.0
        if monthly <= 0:
            continue
        try:
            sa = int(float(raw.get("start_age", 0) or 0))
        except (TypeError, ValueError):
            sa = 0
        try:
            ea = int(float(raw.get("end_age", 0) or 0))
        except (TypeError, ValueError):
            ea = 0
        if ea <= 0:
            ea = 100
        if current_age is not None and sa < current_age:
            sa = current_age
        if age < sa:
            continue
        if age > ea:
            continue
        total += monthly
    return float(total)


def _monthly_recurring_at_age_for_growth(
    rows: Optional[List[Dict[str, Any]]],
    age: int,
    current_age: Optional[int] = None,
) -> float:
    fn = _monthly_recurring_row_total_at_age
    if fn is not None:
        return float(fn(rows, age, current_age=current_age))
    return _monthly_recurring_total_at_age(rows, age, current_age=current_age)


def _completed_age_now_from_dob(birth_year: int, birth_month: int) -> int:
    import datetime

    now = datetime.datetime.now()
    age = now.year - birth_year
    if (now.month, now.day) < (birth_month, 1):
        age -= 1
    return max(0, age)


def _growth_income_extra_monthly(intake: IntakeContext, period_index: int, periods_per_year: int) -> float:
    rows = getattr(intake, "growth_monthly_income_rows", None) or []
    if not rows:
        return 0.0
    bd = getattr(intake, "birth_dates", None) or []
    if not bd:
        return 0.0
    by = bd[0][0]
    bm = bd[0][1] if len(bd[0]) > 1 else 6
    age_now = _completed_age_now_from_dob(by, bm)
    years_elapsed = period_index / float(periods_per_year)
    age = int(age_now + years_elapsed)
    return _monthly_recurring_at_age_for_growth(rows, age, current_age=age_now)


def _growth_misc_monthly(intake: IntakeContext, period_index: int, periods_per_year: int) -> float:
    rows = getattr(intake, "growth_misc_spending_rows", None) or []
    if not rows:
        return 0.0
    bd = getattr(intake, "birth_dates", None) or []
    if not bd:
        return 0.0
    by = bd[0][0]
    bm = bd[0][1] if len(bd[0]) > 1 else 6
    age_now = _completed_age_now_from_dob(by, bm)
    years_elapsed = period_index / float(periods_per_year)
    age = int(age_now + years_elapsed)
    return _monthly_recurring_at_age_for_growth(rows, age, current_age=age_now)


def simulate_monte_carlo(
    returns: pd.Series,
    config: Optional[MonteCarloConfig] = None,
    summary_quantiles: Tuple[float, ...] = (0.1, 0.5, 0.9),
    years: Optional[int] = None,
    frequency: Optional[str] = None,
    periods: Optional[int] = None,
) -> Dict[str, np.ndarray]:
    """
    Simulate return paths from historical returns using bootstrap sampling.

    Returns a dict with keys:
    - paths: simulated equity curves (n_sims, periods)
    - terminal_values: final portfolio values
    - cagr: CAGR per simulation
    - max_drawdown: max drawdown per simulation
    - blowup: boolean array indicating equity <= blowup_threshold
    """
    if config is None:
        config = MonteCarloConfig()
    if years is not None or frequency is not None:
        config = replace(
            config,
            years=years if years is not None else config.years,
            frequency=frequency if frequency is not None else config.frequency,
        )

    clean_returns = returns.dropna().astype(float)
    if clean_returns.empty:
        raise ValueError("Returns series is empty.")

    n_periods = periods if periods is not None else _periods_from_years(config.years, config.frequency)
    idx = _bootstrap_indices(
        len(clean_returns),
        n_periods,
        config.block_size,
        config.n_sims,
        config.seed,
    )
    sampled = clean_returns.values[idx]
    if config.intake_context is not None:
        paths = _returns_to_paths_with_intake(
            sampled, config.intake_context, config.frequency, n_periods
        )
        # CAGR must exclude monthly contributions and expenses: use market-only paths.
        paths_market = config.intake_context.initial_value * np.cumprod(
            1 + sampled, axis=1
        )
        cagr = _cagr_from_paths(
            paths_market, config.frequency, start_value=config.intake_context.initial_value
        )
    else:
        paths = _returns_to_paths(sampled)
        start_val = 1.0
        cagr = _cagr_from_paths(paths, config.frequency, start_value=start_val)
    max_dd = _max_drawdown(paths)
    blowup = (paths <= config.blowup_threshold).any(axis=1)

    # summary_paths and terminal_values include inflows/outflows (for MC charts and tables)
    summary_paths = _summarize_paths(paths, summary_quantiles)
    start_value = 1.0
    if config.intake_context is not None:
        start_value = config.intake_context.initial_value
    metadata = {
        "frequency": config.frequency,
        "years": config.years,
        "periods": n_periods,
        "start_value": start_value,
        "summary_quantiles": summary_quantiles,
    }

    return {
        "paths": paths,
        "terminal_values": paths[:, -1],  # full portfolio at horizon (with contributions/expenses)
        "cagr": cagr,
        "max_drawdown": max_dd,
        "blowup": blowup,
        "summary_paths": summary_paths,  # p10/p50/p90 over time, with inflows/outflows
        "metadata": metadata,
    }


def simulate_monte_carlo_pair(
    returns_a: pd.Series,
    returns_b: pd.Series,
    config: Optional[MonteCarloConfig] = None,
    years: Optional[int] = None,
    frequency: Optional[str] = None,
    periods: Optional[int] = None,
) -> Dict[str, np.ndarray]:
    """
    Simulate paired paths for two strategies using aligned return histories.
    """
    if config is None:
        config = MonteCarloConfig()
    if years is not None or frequency is not None:
        config = replace(
            config,
            years=years if years is not None else config.years,
            frequency=frequency if frequency is not None else config.frequency,
        )

    aligned = pd.concat([returns_a, returns_b], axis=1).dropna()
    if aligned.empty:
        raise ValueError("Aligned returns are empty.")

    n_periods = periods if periods is not None else _periods_from_years(config.years, config.frequency)
    idx = _bootstrap_indices(
        len(aligned),
        n_periods,
        config.block_size,
        config.n_sims,
        config.seed,
    )
    sampled = aligned.values[idx]
    paths_a = _returns_to_paths(sampled[:, :, 0])
    paths_b = _returns_to_paths(sampled[:, :, 1])

    return {
        "paths_a": paths_a,
        "paths_b": paths_b,
        "terminal_a": paths_a[:, -1],
        "terminal_b": paths_b[:, -1],
        "cagr_a": _cagr_from_paths(paths_a, config.frequency),
        "cagr_b": _cagr_from_paths(paths_b, config.frequency),
        "max_drawdown_a": _max_drawdown(paths_a),
        "max_drawdown_b": _max_drawdown(paths_b),
    }


def monte_carlo_questions(
    portfolio_returns: pd.Series,
    benchmark_returns: pd.Series,
    strategy_a_returns: pd.Series,
    strategy_b_returns: pd.Series,
    config: Optional[MonteCarloConfig] = None,
    goal_value: Optional[float] = None,
    var_levels: Tuple[float, ...] = (0.95, 0.99),
    years: Optional[int] = None,
    frequency: Optional[str] = None,
    periods: Optional[int] = None,
) -> Dict[str, float]:
    """
    Answer common questions using Monte Carlo simulations.
    """
    if config is None:
        config = MonteCarloConfig()
    if years is not None or frequency is not None:
        config = replace(
            config,
            years=years if years is not None else config.years,
            frequency=frequency if frequency is not None else config.frequency,
        )

    pair = simulate_monte_carlo_pair(
        portfolio_returns, benchmark_returns, config, periods=periods
    )
    underperform_prob = float((pair["terminal_a"] < pair["terminal_b"]).mean())
    outperform_prob = float((pair["terminal_a"] > pair["terminal_b"]).mean())

    portfolio_sim = simulate_monte_carlo(
        portfolio_returns,
        config,
        periods=periods,
    )
    drawdown_p5 = float(np.quantile(portfolio_sim["max_drawdown"], 0.05))
    drawdown_p1 = float(np.quantile(portfolio_sim["max_drawdown"], 0.01))
    blowup_prob = float(portfolio_sim["blowup"].mean())
    terminal_values = portfolio_sim["terminal_values"]
    start_val = (
        config.intake_context.initial_value
        if config.intake_context is not None
        else 1.0
    )
    terminal_returns = (terminal_values - start_val) / start_val
    prob_loss = float((terminal_values < start_val).mean())

    cagr_p10, cagr_p50, cagr_p90 = np.quantile(portfolio_sim["cagr"], [0.1, 0.5, 0.9])
    term_p10, term_p50, term_p90 = np.quantile(terminal_values, [0.1, 0.5, 0.9])

    strategy_pair = simulate_monte_carlo_pair(
        strategy_a_returns, strategy_b_returns, config, periods=periods
    )
    strategy_a_better = float(
        (strategy_pair["terminal_a"] > strategy_pair["terminal_b"]).mean()
    )

    prob_reach_goal = None
    if goal_value is not None:
        prob_reach_goal = float((portfolio_sim["paths"] >= goal_value).any(axis=1).mean())

    var_cvar = {}
    for level in var_levels:
        if level <= 0 or level >= 1:
            raise ValueError("var_levels must be between 0 and 1.")
        tail = np.quantile(terminal_returns, 1 - level)
        var_cvar[f"var_{int(level * 100)}"] = float(-tail)
        cvar = terminal_returns[terminal_returns <= tail].mean() if terminal_returns.size else 0.0
        var_cvar[f"cvar_{int(level * 100)}"] = float(-cvar)

    results: Dict[str, float] = {
        "prob_loss": prob_loss,
        "prob_underperform_benchmark": underperform_prob,
        "prob_outperform_benchmark": outperform_prob,
        "drawdown_p5": drawdown_p5,
        "drawdown_p1": drawdown_p1,
        "prob_blowup": blowup_prob,
        "prob_strategy_a_better": strategy_a_better,
        "terminal_value_p10": float(term_p10),
        "terminal_value_p50": float(term_p50),
        "terminal_value_p90": float(term_p90),
        "cagr_p10": float(cagr_p10),
        "cagr_p50": float(cagr_p50),
        "cagr_p90": float(cagr_p90),
    }
    if prob_reach_goal is not None:
        results["prob_reach_goal"] = prob_reach_goal
    results.update(var_cvar)
    return results


def _periods_from_years(years: int, frequency: str) -> int:
    if frequency == "daily":
        return years * 252
    if frequency == "monthly":
        return years * 12
    raise ValueError("Unsupported frequency. Use 'daily' or 'monthly'.")


def _bootstrap_indices(
    n_obs: int,
    periods: int,
    block_size: int,
    n_sims: int,
    seed: Optional[int],
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    if n_obs <= 0:
        raise ValueError("n_obs must be positive.")
    block_size = max(1, min(block_size, n_obs))
    if block_size <= 1:
        return rng.integers(0, n_obs, size=(n_sims, periods))
    n_blocks = int(np.ceil(periods / block_size))
    starts = rng.integers(0, n_obs - block_size + 1, size=(n_sims, n_blocks))
    blocks = [
        np.arange(s, s + block_size)
        for s in starts.flatten()
    ]
    idx = np.concatenate(blocks).reshape(n_sims, -1)[:, :periods]
    return idx


def _returns_to_paths(sampled_returns: np.ndarray) -> np.ndarray:
    if sampled_returns.ndim == 1:
        sampled_returns = sampled_returns[None, :]
    return np.cumprod(1 + sampled_returns, axis=1)


def _returns_to_paths_with_intake(
    sampled_returns: np.ndarray,
    intake: IntakeContext,
    frequency: str,
    periods: int,
) -> np.ndarray:
    """Build paths with monthly contributions (growing with inflation) and one-time expenses.
    Year 0 is partial: use current month so remaining months in year 0 = 12 - start_month.
    E.g. March 2026 -> year 0 = 9 months (Apr-Dec 2026) of contribution + growth."""
    import datetime
    if sampled_returns.ndim == 1:
        sampled_returns = sampled_returns[None, :]
    n_sims, n_periods = sampled_returns.shape
    periods_per_year = 12 if frequency == "monthly" else 252
    inflation_rate = getattr(intake, "inflation_rate", 0.03)
    now = datetime.datetime.now()
    start_year = now.year
    start_month = now.month  # 1-12
    months_in_year_0 = max(1, 12 - start_month)  # e.g. March -> 9 months

    def contrib_at_period(t: int) -> float:
        """Net contribution at period t: savings + extra invest − growth misc spending, inflated from t=0."""
        years = t / periods_per_year
        infl = (1 + inflation_rate) ** years
        base = intake.monthly_savings
        if frequency == "daily":
            base = intake.monthly_savings / 21.0
        extra_m = _growth_income_extra_monthly(intake, t, periods_per_year)
        misc_m = _growth_misc_monthly(intake, t, periods_per_year)
        if frequency == "daily":
            extra_m = extra_m / 21.0
            misc_m = misc_m / 21.0
        return (base + extra_m - misc_m) * infl

    floor = 1e-10  # Same as backtesting: allow path to continue and recover via contributions
    paths = np.zeros_like(sampled_returns, dtype=float)
    paths[:, 0] = np.maximum(
        floor,
        intake.initial_value * (1 + sampled_returns[:, 0]) + contrib_at_period(0),
    )
    # One-time amounts and paths are both in dollars (same as IntakeContext.initial_value).
    # Do not rescale by 1e6: a large expense vs a small portfolio (e.g. $10M vs ~$1K median) must
    # subtract the full amount and floor to ~0, not a tiny fraction of the expense.
    def expense_amount_in_path_units(amount: float, _path_values: np.ndarray) -> float:
        return float(amount)

    # Track which expenses have been applied so each is applied exactly once
    applied_expenses: set[tuple[float, float]] = set()

    def apply_expense_once(exp_key: tuple[float, float], amount: float, val: np.ndarray) -> np.ndarray:
        if exp_key in applied_expenses:
            return val
        applied_expenses.add(exp_key)
        to_subtract = expense_amount_in_path_units(amount, val)
        return np.maximum(floor, val - to_subtract)

    # One-time expenses are applied at END of year n (e.g. "in 3 years" = deduct at end of year 3 = 2029 end).
    # Year 0 is partial (months_in_year_0 months); year 1+ are full 12 months.

    # Last period index of year n (years from start): year 0 ends at months_in_year_0-1, year k at months_in_year_0 + k*12 - 1
    def last_period_of_year_from_start(exp_years: float) -> int:
        if exp_years < 1:
            return min(months_in_year_0 - 1, n_periods - 1)
        return min(months_in_year_0 - 1 + int(exp_years) * periods_per_year, n_periods - 1)

    # Last period index of calendar year Y: e.g. "in 2029" = end of 2029
    def last_period_of_calendar_year(exp_year: int) -> int:
        years_from_start = exp_year - start_year
        if years_from_start <= 0:
            return min(months_in_year_0 - 1, n_periods - 1)
        return min(months_in_year_0 - 1 + years_from_start * periods_per_year, n_periods - 1)

    for t in range(1, n_periods):
        val = paths[:, t - 1] * (1 + sampled_returns[:, t]) + contrib_at_period(t)
        for exp in intake.upcoming_expenses or []:
            if isinstance(exp, dict):
                exp_years = float(exp.get("years", exp.get("years_from_start", 0)) or 0)
                exp_amount = float(exp.get("amount", exp.get("value", 0)) or 0)
            elif isinstance(exp, (list, tuple)) and len(exp) >= 2:
                exp_years, exp_amount = float(exp[0]), float(exp[1])
            else:
                continue
            exp_key = (exp_years, exp_amount)
            if exp_key in applied_expenses:
                continue
            if exp_years >= 1000:
                # Calendar year: apply at END of that year (e.g. 2029 → last period of 2029)
                last_period = last_period_of_calendar_year(int(exp_years))
                if t == last_period and 0 <= t < n_periods:
                    val = apply_expense_once(exp_key, exp_amount, val)
                    break
            else:
                # Years from start: apply at END of year n (e.g. "in 3 years" → end of year 3)
                if t == last_period_of_year_from_start(exp_years) and t < n_periods:
                    val = apply_expense_once(exp_key, exp_amount, val)
                    break
        paths[:, t] = np.maximum(floor, val)
    return paths


def _max_drawdown(paths: np.ndarray) -> np.ndarray:
    running_max = np.maximum.accumulate(paths, axis=1)
    drawdowns = paths / running_max - 1.0
    return drawdowns.min(axis=1)


def _cagr_from_paths(
    paths: np.ndarray, frequency: str, start_value: float = 1.0
) -> np.ndarray:
    """CAGR = (terminal / start_value)^(1/years) - 1."""
    years = paths.shape[1] / _periods_from_years(1, frequency)
    if years <= 0 or start_value <= 0:
        return np.zeros(paths.shape[0])
    return (paths[:, -1] / start_value) ** (1 / years) - 1.0


def _summarize_paths(
    paths: np.ndarray, quantiles: Tuple[float, ...]
) -> Dict[str, np.ndarray]:
    if paths.size == 0:
        raise ValueError("paths are empty.")
    summary = {"mean": paths.mean(axis=0)}
    for q in quantiles:
        if q <= 0 or q >= 1:
            raise ValueError("summary_quantiles must be between 0 and 1.")
        key = f"p{int(q * 100)}"
        summary[key] = np.quantile(paths, q, axis=0)
    return summary



