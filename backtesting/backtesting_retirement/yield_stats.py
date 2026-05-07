"""
Compute monthly dividend yield from historical data.
Infer dividend frequency (monthly, quarterly, semi-annual, annual) from ex-dividend counts.
Monthly yield = (dividend / same-month close) / divisor, where divisor depends on frequency.
"""

from __future__ import annotations

from typing import Tuple

import pandas as pd

# Dividends per year -> divisor to convert raw (dividend/price) to monthly yield
# monthly: 12/year -> each dividend is 1 month's yield -> divisor 1
# quarterly: 4/year -> spread over 3 months -> divisor 3
# semi-annual: 2/year -> divisor 6
# annual: 1/year -> divisor 12
FREQUENCY_DIVISOR = {
    "monthly": 1,
    "quarterly": 3,
    "semi-annual": 6,
    "annual": 12,
}


def infer_dividend_frequency(dividends: pd.DataFrame) -> str:
    """
    Infer frequency from ex-dividend count in trailing 12 months.
    Returns one of: "monthly", "quarterly", "semi-annual", "annual".
    """
    if dividends.empty or "ex_dividend_date" not in dividends.columns:
        return "quarterly"  # default
    df = dividends.copy()
    df["ex_dividend_date"] = pd.to_datetime(df["ex_dividend_date"], errors="coerce")
    df = df.dropna(subset=["ex_dividend_date"]).sort_values("ex_dividend_date", ascending=False)
    if len(df) < 2:
        return "quarterly"
    cutoff = df["ex_dividend_date"].iloc[0] - pd.DateOffset(months=12)
    count_trailing_12m = (df["ex_dividend_date"] > cutoff).sum()
    if count_trailing_12m >= 10:
        return "monthly"
    if count_trailing_12m >= 3:
        return "quarterly"
    if count_trailing_12m >= 2:
        return "semi-annual"
    return "annual"


def monthly_yield_series(
    prices: pd.Series,
    dividends: pd.DataFrame,
) -> Tuple[pd.Series, str]:
    """
    For each ex-dividend date: raw_yield = dividend / same-month close;
    monthly_yield = raw_yield / divisor, where divisor is from inferred frequency
    (monthly=1, quarterly=3, semi-annual=6, annual=12).
    Returns (series of monthly yields, frequency label).
    """
    if dividends.empty or "ex_dividend_date" not in dividends.columns or "amount" not in dividends.columns:
        return pd.Series(dtype=float), "quarterly"
    prices = prices.dropna()
    if prices.empty:
        return pd.Series(dtype=float), "quarterly"
    if not isinstance(prices.index, pd.DatetimeIndex):
        return pd.Series(dtype=float), "quarterly"

    frequency = infer_dividend_frequency(dividends)
    divisor = FREQUENCY_DIVISOR.get(frequency, 3)

    month_end = prices.resample("ME").last().dropna()
    yields = []
    for _, row in dividends.iterrows():
        ex_date = pd.Timestamp(row["ex_dividend_date"])
        amount = float(row["amount"])
        if amount <= 0:
            continue
        year_month = (ex_date.year, ex_date.month)
        mask = (month_end.index.year == year_month[0]) & (month_end.index.month == year_month[1])
        if not mask.any():
            continue
        price = float(month_end.loc[mask].iloc[0])
        if price <= 0:
            continue
        raw_yield = amount / price
        monthly_yield = raw_yield / divisor
        month_key = ex_date.replace(day=1) + pd.offsets.MonthEnd(0)
        yields.append((month_key, monthly_yield))

    if not yields:
        return pd.Series(dtype=float), frequency
    return (
        pd.Series(
            [y for _, y in yields],
            index=pd.DatetimeIndex([d for d, _ in yields], name="date"),
        ),
        frequency,
    )


def yield_mean_stdev(yield_series: pd.Series) -> Tuple[float, float]:
    """Compute mean and standard deviation of monthly yield. Assume normal distribution."""
    clean = yield_series.dropna()
    if len(clean) < 1:
        return 0.0, 0.0
    return float(clean.mean()), float(clean.std()) if len(clean) > 1 else 0.0


def trailing_twelve_month_yield(
    prices: pd.Series,
    dividends: pd.DataFrame,
) -> float:
    """
    Cash dividend yield over the trailing 12 months: sum of dividend amounts with
    ex-dividend dates in (anchor − 12 months, anchor], divided by the month-end close
    at the anchor (last valid price date in ``prices``).

    Uses the same price/dividend inputs as retirement MC (close series + dividend CSV).
    """
    ps = prices.dropna().sort_index()
    if ps.empty:
        return 0.0
    anchor = pd.Timestamp(ps.index.max()).normalize()
    px = float(ps.iloc[-1])
    if px <= 0:
        return 0.0
    cutoff = anchor - pd.DateOffset(months=12)
    if dividends is None or dividends.empty:
        return 0.0
    if "ex_dividend_date" not in dividends.columns or "amount" not in dividends.columns:
        return 0.0
    df = dividends.copy()
    df["ex_dividend_date"] = pd.to_datetime(df["ex_dividend_date"], errors="coerce").dt.normalize()
    df = df.dropna(subset=["ex_dividend_date"])
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    mask = (df["ex_dividend_date"] > cutoff) & (df["ex_dividend_date"] <= anchor)
    total_div = float(df.loc[mask, "amount"].sum())
    if total_div <= 0:
        return 0.0
    return float(total_div / px)


# Backward compatibility: annualized series (e.g. for display) from monthly * 12
def annualized_yield_series(
    prices: pd.Series,
    dividends: pd.DataFrame,
    annualize_factor: float = 4.0,
) -> pd.Series:
    """Deprecated: use monthly_yield_series. Returns monthly yield series * 12 for annualized."""
    monthly, _ = monthly_yield_series(prices, dividends)
    return monthly * 12.0 if not monthly.empty else monthly
