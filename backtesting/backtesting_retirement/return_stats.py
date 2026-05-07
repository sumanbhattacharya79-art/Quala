"""
Compute monthly log-returns from price series. Assume log-normal (log-returns are normal).
Compute mean and stdev of log-returns.
"""

from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd


def monthly_log_returns(prices: pd.Series) -> pd.Series:
    """Log return each month: log(price_t / price_{t-1})."""
    prices = prices.dropna()
    if len(prices) < 2:
        return pd.Series(dtype=float)
    if isinstance(prices.index, pd.DatetimeIndex):
        # Ensure month-end alignment if needed
        p = prices.resample("ME").last().dropna()
    else:
        p = prices
    return np.log(p / p.shift(1)).dropna()


def log_return_mean_stdev(log_returns: pd.Series) -> Tuple[float, float]:
    """Mean and stdev of monthly log-returns (for normal assumption)."""
    clean = log_returns.dropna()
    if len(clean) < 1:
        return 0.0, 0.0
    return float(clean.mean()), float(clean.std()) if len(clean) > 1 else 0.0
