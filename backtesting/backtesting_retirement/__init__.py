"""
Retirement portfolio backtesting: load monthly + dividend data, compute yield and
log-return statistics, run Monte Carlo to estimate how long the portfolio will last.
"""

from .data_loader import (
    load_dividends,
    load_monthly_prices,
    load_prices_and_dividends,
)
from .monte_carlo import simulate_retirement
from .return_stats import log_return_mean_stdev, monthly_log_returns
from .runner import run_retirement_backtest
from .stats_aggregator import (
    compute_ticker_stats,
    portfolio_return_stats,
    portfolio_yield_stats,
)
from .types import RetirementConfig, RetirementResult, TickerStats
from .yield_stats import (
    annualized_yield_series,
    infer_dividend_frequency,
    monthly_yield_series,
    yield_mean_stdev,
)

__all__ = [
    "load_monthly_prices",
    "load_dividends",
    "load_prices_and_dividends",
    "monthly_yield_series",
    "infer_dividend_frequency",
    "annualized_yield_series",
    "yield_mean_stdev",
    "monthly_log_returns",
    "log_return_mean_stdev",
    "compute_ticker_stats",
    "portfolio_return_stats",
    "portfolio_yield_stats",
    "simulate_retirement",
    "run_retirement_backtest",
    "RetirementConfig",
    "RetirementResult",
    "TickerStats",
]
