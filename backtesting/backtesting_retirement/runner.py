"""
Orchestrate retirement backtest: load data, compute stats, run MC, return metrics.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from backtesting.backtesting_service.leveraged_etf import pad_prices_to_start_year
from backtesting.backtesting_service.metrics import compute_asset_correlations

from .data_loader import load_prices_and_dividends
from .monte_carlo import simulate_retirement
from .yield_stats import trailing_twelve_month_yield
from .stats_aggregator import (
    compute_ticker_stats,
    portfolio_log_return_series,
    portfolio_return_stats,
    portfolio_yield_stats,
)
from .types import RetirementConfig, RetirementResult


def run_retirement_backtest(
    portfolio_weights: Dict[str, float],
    data_output_dir: Path,
    initial_value: float = 1.0,
    monthly_withdrawal: float = 0.0,
    inflation_rate: float = 0.03,
    max_years: int = 50,
    n_sims: int = 5000,
    seed: Optional[int] = None,
    ticker_substitution: Optional[Dict[str, str]] = None,
    fetch_if_missing: bool = True,
    annualize_yield_factor: float = 4.0,
    retirement_age: Optional[int] = None,
    yearly_income_monthly: Optional[List[float]] = None,
    yearly_misc_spending_monthly: Optional[List[float]] = None,
    upcoming_expenses: Optional[List[Tuple[float, float]]] = None,
    simulation_calendar_year: Optional[int] = None,
    simulation_calendar_month: Optional[int] = None,
    discretionary_spend_if_prior_year_return: Optional[Tuple[float, ...]] = None,
) -> Dict[str, Any]:
    """
    Full retirement analysis pipeline.

    Returns dict with:
    - result: RetirementResult
    - ticker_stats: per-ticker stats
    - portfolio_log_return_mean, portfolio_log_return_stdev
    - portfolio_yield_mean, portfolio_yield_stdev
    - metrics: portfolio_longevity_p10/p50/p90, depleted_fraction, etc.
    """
    tickers = list(portfolio_weights.keys())
    if not tickers:
        raise ValueError("portfolio_weights must not be empty.")
    # Normalize weights to sum to 1
    total = sum(portfolio_weights.values())
    weights = {t: portfolio_weights[t] / total for t in tickers}

    prices, dividends = load_prices_and_dividends(
        data_output_dir,
        tickers,
        ticker_substitution=ticker_substitution,
        fetch_if_missing=fetch_if_missing,
    )

    # Match growth backtest (driver.py): anchor history at 1999 so UI "Historical data" and
    # MC inputs align. Raw CSVs often start later (e.g. 2004 fund inception); we pad leading
    # months with flat prices (0 return) before computing stats — same as non-leveraged MC path.
    if not prices.empty:
        mask = prices.index.year >= 1999
        prices = prices.loc[mask].copy()

    # Per-ticker stats (MC μ/σ and yield) use only post–first-valid price; padding below is unchanged.
    stats_start_by_ticker: Dict[str, pd.Timestamp] = {}
    for t in prices.columns:
        fv = prices[t].first_valid_index()
        if fv is not None and not pd.isna(fv):
            stats_start_by_ticker[t] = pd.Timestamp(fv)

    prices = pad_prices_to_start_year(prices, start_year=1999, min_years=20)

    # Date range of price data used for return/yield stats (after padding)
    price_dates = prices.dropna(how="all").index
    data_start = price_dates.min().strftime("%Y-%m") if len(price_dates) > 0 else None
    data_end = price_dates.max().strftime("%Y-%m") if len(price_dates) > 0 else None

    # Monthly price returns → correlation matrix for UI (aligned with growth driver.py)
    price_m = prices.resample("M").last().dropna(how="all")
    asset_return_df = price_m.pct_change().dropna(how="any")
    asset_correlations = (
        compute_asset_correlations(asset_return_df, "monthly", weights=weights)
        if not asset_return_df.empty and asset_return_df.shape[1] > 0
        else {"tickers": [], "rows": []}
    )
    if asset_correlations.get("rows"):
        asset_correlations["retirement_yield_column"] = "ttm"
        for row in asset_correlations["rows"]:
            t = row.get("ticker")
            if not t or t not in prices.columns:
                row["ttm_yield"] = 0.0
                continue
            div_df = dividends.get(t)
            if div_df is None:
                div_df = pd.DataFrame(columns=["ex_dividend_date", "amount"])
            y = trailing_twelve_month_yield(prices[t], div_df)
            row["ttm_yield"] = round(y, 4)

    ticker_stats = compute_ticker_stats(
        prices,
        dividends,
        annualize_yield_factor=annualize_yield_factor,
        stats_start_by_ticker=stats_start_by_ticker,
    )
    log_return_series = portfolio_log_return_series(prices, weights)
    mu_r, sigma_r = portfolio_return_stats(ticker_stats, weights)
    mu_y, sigma_y = portfolio_yield_stats(ticker_stats, weights)

    config = RetirementConfig(
        initial_value=initial_value,
        monthly_withdrawal=monthly_withdrawal,
        inflation_rate=inflation_rate,
        max_years=max_years,
        n_sims=n_sims,
        seed=seed,
        retirement_age=retirement_age,
        yearly_income_monthly=yearly_income_monthly,
        yearly_misc_spending_monthly=yearly_misc_spending_monthly,
        upcoming_expenses=upcoming_expenses,
        simulation_calendar_year=simulation_calendar_year,
        simulation_calendar_month=simulation_calendar_month,
        discretionary_spend_if_prior_year_return=discretionary_spend_if_prior_year_return,
    )
    result = simulate_retirement(mu_r, sigma_r, mu_y, sigma_y, config)

    # Portfolio longevity: years until depletion across ALL paths (non-depleted = max_years)
    # P10/P50/P90 answer "how long will the portfolio last?" for the retiree
    longevity = result.years_until_depletion.fillna(config.max_years)
    meta = result.metadata
    metrics = {
        "depleted_fraction": result.depleted_fraction,
        "probability_of_success": meta.get("probability_of_success", 1.0 - result.depleted_fraction),
        "magnitude_of_failure_p50": meta.get("magnitude_of_failure_p50"),
        "magnitude_of_failure_p90": meta.get("magnitude_of_failure_p90"),
        "goal_completion_p10": meta.get("goal_completion_p10"),
        "goal_completion_p50": meta.get("goal_completion_p50"),
        "goal_completion_p90": meta.get("goal_completion_p90"),
        "withdrawal_rate_year0": meta.get("withdrawal_rate_year0"),
        "withdrawal_rates_by_year": meta.get("withdrawal_rates_by_year"),
        "age_at_depletion_p10": meta.get("age_at_depletion_p10"),
        "age_at_depletion_p50": meta.get("age_at_depletion_p50"),
        "age_at_depletion_p90": meta.get("age_at_depletion_p90"),
        "portfolio_longevity_p10": float(longevity.quantile(0.10)),
        "portfolio_longevity_p50": float(longevity.quantile(0.50)),
        "portfolio_longevity_p90": float(longevity.quantile(0.90)),
        "twr_p10": result.twr_p10,
        "twr_p50": result.twr_p50,
        "twr_p90": result.twr_p90,
        "portfolio_log_return_mean_annual": mu_r * 12,
        "portfolio_log_return_stdev_annual": sigma_r * (12 ** 0.5),
        "portfolio_yield_mean": mu_y,
        "portfolio_yield_stdev": sigma_y,
        "portfolio_yield_mean_annual": mu_y * 12,
        "portfolio_yield_stdev_annual": sigma_y * (12 ** 0.5),
    }

    return {
        "result": result,
        "ticker_stats": ticker_stats,
        "portfolio_log_return_mean": mu_r,
        "portfolio_log_return_stdev": sigma_r,
        "portfolio_yield_mean": mu_y,
        "portfolio_yield_stdev": sigma_y,
        "metrics": metrics,
        "summary_paths": result.summary_paths,
        "summary_yearly_price": getattr(result, "summary_yearly_price", None),
        "summary_yearly_yield": getattr(result, "summary_yearly_yield", None),
        "summary_yearly_twr": getattr(result, "summary_yearly_twr", None),
        "yearly_price_gain": getattr(result, "yearly_price_gain", None),
        "yearly_yield_gain": getattr(result, "yearly_yield_gain", None),
        "paths_sample": getattr(result, "paths_sample", None),
        "paths_sample_years": getattr(result, "paths_sample_years", None),
        "metadata": result.metadata,
        "data_start": data_start,
        "data_end": data_end,
        "log_return_series": log_return_series,
        "asset_correlations": asset_correlations,
    }
