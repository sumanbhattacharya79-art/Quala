"""
Aggregate per-ticker yield and log-return stats into portfolio-level stats.
Portfolio return (log) = weighted sum of ticker log-returns (uncorrelated assumption).
Portfolio yield = weighted sum of ticker yields.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

import pandas as pd

from .types import TickerStats
from .yield_stats import monthly_yield_series, yield_mean_stdev
from .return_stats import monthly_log_returns, log_return_mean_stdev


def compute_ticker_stats(
    prices: pd.DataFrame,
    dividends: Dict[str, pd.DataFrame],
    annualize_yield_factor: float = 4.0,
    stats_start_by_ticker: Optional[Dict[str, pd.Timestamp]] = None,
) -> Dict[str, TickerStats]:
    """
    Compute mean/stdev of log-return and monthly yield for each ticker.

    ``stats_start_by_ticker``: when set (e.g. first real price after load, before leading
    padding), return and yield stats use only data from that timestamp onward; the padded
    price panel is unchanged for alignment elsewhere.
    """
    result = {}
    for ticker in prices.columns:
        pr = prices[ticker]
        start = (
            stats_start_by_ticker.get(ticker)
            if stats_start_by_ticker
            else None
        )
        if start is not None and not pd.isna(start):
            pr = pr.loc[pd.Timestamp(start) :]

        div = dividends.get(ticker)
        if div is None:
            div = pd.DataFrame(columns=["ex_dividend_date", "amount"])
        else:
            div = div.copy()
            if (
                start is not None
                and not pd.isna(start)
                and not div.empty
                and "ex_dividend_date" in div.columns
            ):
                t0 = pd.Timestamp(start)
                div["ex_dividend_date"] = pd.to_datetime(
                    div["ex_dividend_date"], errors="coerce"
                )
                div = div.dropna(subset=["ex_dividend_date"])
                div = div[div["ex_dividend_date"] >= t0]

        log_ret = monthly_log_returns(pr)
        mu_r, sigma_r = log_return_mean_stdev(log_ret)
        y_series, freq = monthly_yield_series(pr, div)
        mu_y, sigma_y = yield_mean_stdev(y_series)
        result[ticker] = TickerStats(
            ticker=ticker,
            log_return_mean=mu_r,
            log_return_stdev=sigma_r,
            yield_mean=mu_y,
            yield_stdev=sigma_y,
            n_obs_returns=len(log_ret.dropna()),
            n_obs_yield=len(y_series.dropna()),
            dividend_frequency=freq,
        )
    return result


def portfolio_log_return_series(
    prices: pd.DataFrame,
    weights: Dict[str, float],
) -> pd.Series:
    """Compute portfolio monthly log returns as weighted sum of ticker log returns (aligned by date)."""
    series_list = []
    for ticker in prices.columns:
        log_ret = monthly_log_returns(prices[ticker])
        if not log_ret.empty:
            series_list.append(log_ret.rename(ticker))
    if not series_list:
        return pd.Series(dtype=float)
    combined = pd.concat(series_list, axis=1).dropna()
    w = pd.Series(weights)
    portfolio_ret = (combined * w.reindex(combined.columns, fill_value=0)).sum(axis=1)
    return portfolio_ret.dropna()


def portfolio_return_stats(
    ticker_stats: Dict[str, TickerStats],
    weights: Dict[str, float],
) -> Tuple[float, float]:
    """
    Portfolio log-return mean and stdev (uncorrelated: Var = sum w_i^2 sigma_i^2).
    """
    mu = sum(weights.get(t, 0.0) * s.log_return_mean for t, s in ticker_stats.items())
    var = sum(weights.get(t, 0.0) ** 2 * (s.log_return_stdev ** 2) for t, s in ticker_stats.items())
    return mu, var ** 0.5


def portfolio_yield_stats(
    ticker_stats: Dict[str, TickerStats],
    weights: Dict[str, float],
) -> Tuple[float, float]:
    """Portfolio yield mean and stdev (uncorrelated)."""
    mu = sum(weights.get(t, 0.0) * s.yield_mean for t, s in ticker_stats.items())
    var = sum(weights.get(t, 0.0) ** 2 * (s.yield_stdev ** 2) for t, s in ticker_stats.items())
    return mu, var ** 0.5 if var > 0 else 0.0
