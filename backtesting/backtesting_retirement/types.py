"""Types for retirement portfolio backtesting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


import pandas as pd


@dataclass
class RetirementConfig:
    """Configuration for retirement Monte Carlo simulation."""

    initial_value: float = 1.0
    """Portfolio value at retirement start (dollars)."""
    monthly_withdrawal: float = 0.0
    """Target monthly withdrawal (e.g. 80% of pre-retirement expense, inflation-adjusted)."""
    inflation_rate: float = 0.03
    """Annual inflation for escalating withdrawals."""
    max_years: int = 50
    """Maximum simulation years (from plan start through planning horizon, e.g. to age 100)."""
    n_sims: int = 5000
    seed: Optional[int] = None
    retirement_age: Optional[int] = None
    """Age at retirement start; used to compute age of plan failure (e.g. age 67)."""
    yearly_income_monthly: Optional[List[float]] = None
    """Per-year monthly income (length max_years): income offset for year y, subtracted from withdrawal."""
    yearly_misc_spending_monthly: Optional[List[float]] = None
    """Per-year extra monthly spend (length max_years): misc spending for year y, added to withdrawal."""
    upcoming_expenses: Optional[List[Tuple[float, ...]]] = None
    """(years_from_start or calendar_year if >=1000, amount). Same semantics as IntakeContext / growth MC."""
    simulation_calendar_year: Optional[int] = None
    """Anchor for calendar-year expenses (default: current year). Use retirement year when known."""
    simulation_calendar_month: Optional[int] = None
    """1–12; first month of retirement simulation (default: current month)."""
    discretionary_spend_if_prior_year_return: Optional[Tuple[float, ...]] = None
    """(monthly_dollars, min_prior_year_total_return_pct): each retirement year after the first
    (y>=1), add ``monthly_dollars`` (inflation-scaled) to withdrawal **per path** only if the prior
    year's portfolio total return (price + yield $ / value at year start) >= the threshold (percent points).

    4-tuple (monthly_dollars, min_pct, start_age, end_age): same, but only when
    ``retirement_age + y_enter`` is in ``[start_age, end_age]`` (needs ``retirement_age``).

    Legacy 3-tuple (monthly_dollars, target_retirement_year_1based, min_pct): only that single year."""


@dataclass
class TickerStats:
    """Per-ticker statistics from historical data."""

    ticker: str
    log_return_mean: float
    log_return_stdev: float
    yield_mean: float
    """Monthly dividend yield (mean)."""
    yield_stdev: float
    """Monthly dividend yield (stdev)."""
    n_obs_returns: int
    n_obs_yield: int
    dividend_frequency: str = "quarterly"
    """Inferred: monthly, quarterly, semi-annual, annual."""


@dataclass
class RetirementResult:
    """Result of retirement Monte Carlo simulation."""

    years_until_depletion: pd.Series
    """Per-simulation: years until portfolio is depleted (NaN if never depleted)."""
    depleted_fraction: float
    """Fraction of simulations where portfolio depleted within max_years."""
    summary_paths: Dict[str, pd.Series]
    """Path summaries: mean, p10, p50, p90 portfolio value over time."""
    metadata: Dict[str, object]
    twr_p10: Optional[float] = None
    twr_p50: Optional[float] = None
    twr_p90: Optional[float] = None
    summary_yearly_price: Optional[Dict[str, pd.Series]] = None
    """Yearly $ gain from price change: mean, p10, p50, p90."""
    summary_yearly_yield: Optional[Dict[str, pd.Series]] = None
    """Yearly $ gain from yield: mean, p10, p50, p90."""
    summary_yearly_twr: Optional[Dict[str, pd.Series]] = None
    """Yearly time-weighted return (price return %): p10, p50, p90."""
    yearly_price_gain: Optional[Any] = None
    """Shape (n_sims, max_years): annual $ from price moves (zeros where path has no balance)."""
    yearly_yield_gain: Optional[Any] = None
    """Shape (n_sims, max_years): annual $ from yield (zeros where path has no balance)."""
    paths_sample: Optional[list] = None
    """Sampled paths for spaghetti plot: list of lists (path_idx, year_idx)."""
    paths_sample_years: Optional[list] = None
    """Year indices for paths_sample x-axis."""
