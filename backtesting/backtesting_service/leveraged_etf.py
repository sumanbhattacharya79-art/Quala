"""Leveraged ETF handling: use 3x underlying index for both backtest and Monte Carlo.

Leveraged ETFs have daily rebalancing and decay that distort long-horizon projections.
We substitute: TQQQ -> 3x QQQ, SPXL -> 3x SPY, other leveraged ETFs -> 3x SPY.
Both backtest and MC use the same underlying data for consistency.

History must extend at least to 1999. If mapping/data ends before 1999, pad missing
leading entries with 0 return (flat price) so the dataset starts from 1999.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

MIN_START_YEAR = 1999
MIN_YEARS = 20


# TQQQ -> 3x QQQ; SPXL -> 3x SPY; other leveraged ETFs -> 3x SPY
LEVERAGED_ETF_UNDERLYING: dict[str, tuple[str, float]] = {
    "TQQQ": ("QQQ", 3.0),
    "SPXL": ("SPY", 3.0),
    "UPRO": ("SPY", 3.0),
    "TNA": ("SPY", 3.0),
    "UDOW": ("SPY", 3.0),
    "SOXL": ("SPY", 3.0),
    "FAS": ("SPY", 3.0),
    "TECL": ("SPY", 3.0),
    "HIBL": ("SPY", 3.0),
    "LABU": ("SPY", 3.0),
    "YINN": ("SPY", 3.0),
    "ERX": ("SPY", 3.0),
    "SPXS": ("SPY", 3.0),
    "SQQQ": ("QQQ", 3.0),
    "SPXU": ("SPY", 3.0),
    "TZA": ("SPY", 3.0),
    "SDOW": ("SPY", 3.0),
    "SSO": ("SPY", 2.0),
    "QLD": ("QQQ", 2.0),
}


def pad_prices_to_start_year(
    prices: pd.DataFrame,
    start_year: int = MIN_START_YEAR,
    min_years: int = MIN_YEARS,
) -> pd.DataFrame:
    """
    Ensure price data starts from start_year (e.g. 1999). For tickers with shorter
    history, pad missing leading entries with 0 return (bfill first price backward).

    Returns:
        DataFrame with same columns, index from start_year to max date.
    """
    if prices.empty:
        return prices
    idx = prices.index
    end = idx.max()
    # Build target monthly range from start_year-01 to end
    target = pd.date_range(
        start=f"{start_year}-01-01",
        end=end,
        freq="ME",
    )
    out = pd.DataFrame(index=target)
    for col in prices.columns:
        s = prices[col].reindex(target)
        # Leading NaNs: bfill = first valid price backward -> 0 return for missing
        # Trailing NaNs: ffill
        s = s.bfill().ffill()
        out[col] = s
    return out.dropna(how="all")


def _synthetic_leveraged_prices(
    underlying_prices: pd.Series, leverage: float
) -> pd.Series:
    """Build synthetic price series with leverage x underlying returns (compounded)."""
    ret = underlying_prices.pct_change().fillna(0)
    synthetic = (1 + leverage * ret).cumprod()
    synthetic.iloc[0] = 1.0
    return synthetic * underlying_prices.iloc[0]


def build_prices_for_leveraged_portfolio(
    portfolio: dict[str, float],
    raw_prices: pd.DataFrame,
    data_output_dir: Path,
    load_price_fn,
    start_year: Optional[int] = 1999,
) -> tuple[pd.DataFrame, list[str]]:
    """
    Build prices DataFrame where leveraged ETFs are replaced with 3x underlying (synthetic).
    Uses underlying's date range so both backtest and MC share same history (at least from 1999).

    Returns:
        (prices_df, substitutions) - prices for backtest; substitutions for UI.
    """
    tickers = list(portfolio.keys())
    leveraged = [t for t in tickers if t.upper() in LEVERAGED_ETF_UNDERLYING]
    if not leveraged:
        if start_year is not None:
            mask = raw_prices.index.year >= start_year
            return raw_prices.loc[mask].copy(), []
        return raw_prices.copy(), []

    substitutions: list[str] = []
    # Use underlying's index as base for leveraged tickers to get longer history (at least 1999)
    base_index = raw_prices.index
    for ticker in leveraged:
        underlying_ticker, lev = LEVERAGED_ETF_UNDERLYING[ticker.upper()]
        try:
            underlying = load_price_fn(data_output_dir, underlying_ticker)
            if underlying.empty or len(underlying) < 2:
                continue
            synthetic = _synthetic_leveraged_prices(underlying, lev)
            substitutions.append(f"{ticker} → {lev:.0f}x {underlying_ticker}")
            # Extend base_index to include underlying's range when it's longer
            base_index = base_index.union(synthetic.index).sort_values()
        except Exception:
            pass

    # Rebuild: non-leveraged from raw_prices, leveraged from synthetic; align to base_index
    out = pd.DataFrame(index=base_index)
    for ticker in tickers:
        if ticker.upper() in LEVERAGED_ETF_UNDERLYING:
            underlying_ticker, lev = LEVERAGED_ETF_UNDERLYING[ticker.upper()]
            try:
                underlying = load_price_fn(data_output_dir, underlying_ticker)
                if not underlying.empty and len(underlying) >= 2:
                    synthetic = _synthetic_leveraged_prices(underlying, lev)
                    out[ticker] = synthetic.reindex(base_index).ffill().bfill()
            except Exception:
                out[ticker] = raw_prices[ticker].reindex(base_index).ffill().bfill()
        else:
            out[ticker] = raw_prices[ticker].reindex(base_index).ffill().bfill()
    out = out.dropna(how="all")

    if start_year is not None:
        mask = out.index.year >= start_year
        out = out.loc[mask].dropna(how="all")
    # If any ticker still has < min_years, pad missing leading entries with 0 return
    out = pad_prices_to_start_year(out, start_year=start_year or MIN_START_YEAR, min_years=MIN_YEARS)
    return out, list(dict.fromkeys(substitutions))


def _load_underlying_returns(
    data_output_dir: Path,
    ticker: str,
    load_fn,
) -> Optional[pd.Series]:
    """Load underlying ticker returns. Returns None if load fails."""
    try:
        prices = load_fn(data_output_dir, ticker)
        return prices.pct_change().dropna()
    except Exception:
        return None


def get_mc_returns_for_leveraged_portfolio(
    portfolio: dict[str, float],
    returns: pd.DataFrame,
    data_output_dir: Path,
    load_price_fn,
) -> tuple[pd.Series, list[str]]:
    """
    Compute portfolio returns suitable for Monte Carlo when portfolio contains leveraged ETFs.

    For leveraged ETFs (TQQQ, SPXL, etc.), use 3x underlying index returns instead of
    the leveraged ETF's actual returns. This avoids decay/compounding distortion in MC.

    Args:
        portfolio: Dict of ticker -> weight (should sum to 1.0)
        returns: DataFrame of period returns (columns = tickers, index = dates)
        data_output_dir: Path to load underlying price data (QQQ, SPY)
        load_price_fn: Function (data_output_dir, ticker) -> pd.Series of prices

    Returns:
        Series of portfolio returns aligned with returns index.
    """
    tickers = list(portfolio.keys())
    if not tickers:
        return pd.Series(dtype=float), []

    # Check if any leveraged ETFs are in the portfolio
    leveraged = [t for t in tickers if t.upper() in LEVERAGED_ETF_UNDERLYING]
    if not leveraged:
        # No leveraged ETFs: use standard weighted return
        weights = pd.Series(portfolio)
        common = returns.reindex(columns=weights.index).dropna(how="all")
        aligned = common.fillna(0)
        return (aligned * weights).sum(axis=1).dropna(), []

    substitutions: list[str] = []

    # Load underlying returns for needed tickers
    underlying_returns: dict[str, pd.Series] = {}
    for t in leveraged:
        underlying_ticker, leverage = LEVERAGED_ETF_UNDERLYING[t.upper()]
        if underlying_ticker not in underlying_returns:
            ser = _load_underlying_returns(data_output_dir, underlying_ticker, load_price_fn)
            if ser is not None:
                underlying_returns[underlying_ticker] = ser

    # Build modified return per ticker (weighted sum)
    result = pd.Series(0.0, index=returns.index)

    for ticker, weight in portfolio.items():
        if weight <= 0:
            continue
        ticker_upper = ticker.upper()
        if ticker_upper in LEVERAGED_ETF_UNDERLYING:
            underlying_ticker, leverage = LEVERAGED_ETF_UNDERLYING[ticker_upper]
            if underlying_ticker in underlying_returns:
                # Use 3x (or 2x) underlying returns, aligned to returns index
                substitutions.append(f"{ticker} → {leverage:.0f}x {underlying_ticker}")
                underlying = underlying_returns[underlying_ticker]
                scaled = underlying * leverage
                aligned = scaled.reindex(returns.index).ffill().bfill().fillna(0)
                result = result.add(aligned * weight, fill_value=0)
            else:
                # Fallback: use actual leveraged ETF returns if underlying unavailable
                if ticker in returns.columns:
                    result = result.add(returns[ticker].fillna(0) * weight, fill_value=0)
        else:
            if ticker in returns.columns:
                result = result.add(returns[ticker].fillna(0) * weight, fill_value=0)

    return result.dropna(), list(dict.fromkeys(substitutions))  # dedupe
