from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd


def compute_metrics(
    portfolio_values: pd.Series,
    portfolio_returns: pd.Series,
    frequency: str,
    risk_free_rate: float = 0.036,
    benchmark_prices: Optional[pd.Series] = None,
    initial_value: Optional[float] = None,
    returns_for_benchmark: Optional[pd.Series] = None,
    portfolio_values_market: Optional[pd.Series] = None,
) -> Dict[str, float]:
    """Compute backtest metrics.

    CAGR = market appreciation only (no money inflow/outflow). When portfolio_values_market
    is provided (intake with contributions/expenses), CAGR uses market-only path:
    (terminal_market / initial_value)^(1/years) - 1. Otherwise uses portfolio_values.
    """
    if portfolio_values.empty:
        raise ValueError("Portfolio values are empty.")

    ann_factor = _annualization_factor(frequency)
    total_periods = len(portfolio_returns)
    # Use actual calendar span for years (CAGR denominator) to avoid inflation from
    # frequency mismatch: e.g. monthly data (12/yr) with ann_factor=252 gives years≈0.05
    # per year, inflating CAGR. Actual span is robust regardless of inferred frequency.
    try:
        idx = portfolio_returns.index
        if hasattr(idx, "min") and hasattr(idx, "max") and len(idx) >= 2:
            span_days = (idx[-1] - idx[0]).days
            years = max(span_days / 365.25, 1.0 / 12.0)  # at least 1 month
        else:
            years = total_periods / ann_factor if ann_factor > 0 else 1.0
    except (TypeError, AttributeError, ValueError):
        years = total_periods / ann_factor if ann_factor > 0 else 1.0

    start_value = portfolio_values.iloc[0]
    end_value = portfolio_values.iloc[-1]

    # CAGR must exclude all cash inflows/outflows (no monthly contributions, no expenses).
    # When intake is set, portfolio_values_market is the path with market returns only.
    if (
        portfolio_values_market is not None
        and not portfolio_values_market.empty
        and initial_value is not None
        and initial_value > 0
    ):
        # Use market-only terminal value (no contributions/expenses)
        end_value_cagr = portfolio_values_market.iloc[-1]
        cagr = (end_value_cagr / initial_value) ** (1 / years) - 1 if years > 0 else 0.0
    else:
        # No intake: portfolio_values has no cash flows
        cagr = (end_value / start_value) ** (1 / years) - 1 if years > 0 and start_value > 0 else 0.0
    vol = portfolio_returns.std(ddof=1) * np.sqrt(ann_factor) if total_periods > 1 else 0.0

    rf_period = risk_free_rate / ann_factor if ann_factor > 0 else 0.0
    excess = portfolio_returns - rf_period
    # Annualized Sharpe/Sortino: (mean excess / period stdev) * sqrt(periods_per_year).
    # Monthly returns -> sqrt(12); daily returns -> sqrt(252).
    sharpe_sortino_root = np.sqrt(12.0) if frequency == "monthly" else np.sqrt(252.0)
    sharpe = (
        excess.mean() / excess.std(ddof=1) * sharpe_sortino_root
        if excess.std(ddof=1) > 0
        else 0.0
    )
    downside = portfolio_returns[portfolio_returns < rf_period]
    sortino = (
        excess.mean() / downside.std(ddof=1) * sharpe_sortino_root
        if downside.std(ddof=1) > 0
        else 0.0
    )

    drawdown = _max_drawdown(portfolio_values)

    metrics = {
        "cagr": float(cagr),
        "annualized_volatility": float(vol),
        "sharpe_ratio": float(sharpe),
        "sortino_ratio": float(sortino),
        "max_drawdown": float(drawdown),
        "cumulative_return": float(end_value / start_value - 1.0) if start_value > 0 else 0.0,
    }

    if benchmark_prices is not None and not benchmark_prices.empty:
        benchmark_returns = benchmark_prices.pct_change().dropna()
        # Use market-only returns for beta when provided (avoids contribution smoothing)
        p_returns = returns_for_benchmark if returns_for_benchmark is not None else portfolio_returns
        aligned = pd.concat([p_returns, benchmark_returns], axis=1).dropna()
        if not aligned.empty:
            p = aligned.iloc[:, 0]
            b = aligned.iloc[:, 1]
            cov = np.cov(p, b, ddof=1)[0, 1]
            beta = cov / np.var(b, ddof=1) if np.var(b, ddof=1) > 0 else 0.0
            tracking_error = (p - b).std(ddof=1) * np.sqrt(ann_factor)
            info_ratio = (
                (p - b).mean() / (p - b).std(ddof=1) * np.sqrt(ann_factor)
                if (p - b).std(ddof=1) > 0
                else 0.0
            )
            corr = np.corrcoef(p, b)[0, 1] if len(aligned) > 1 else 0.0

            metrics.update(
                {
                    "beta": float(beta),
                    "tracking_error": float(tracking_error),
                    "information_ratio": float(info_ratio),
                    "benchmark_correlation": float(corr),
                }
            )

        # Benchmark TWR on the **same calendar span** used for CAGR (portfolio_returns index),
        # with benchmark levels as-of those dates. Alpha = CAGR minus that benchmark TWR so the
        # table is arithmetic: Portfolio TWR − Benchmark TWR = Alpha.
        try:
            pret_idx = portfolio_returns.index
            if len(pret_idx) >= 2 and years > 0:
                bench = benchmark_prices.dropna()
                if not bench.empty:
                    t0, t1 = pret_idx[0], pret_idx[-1]
                    b0 = _series_last_on_or_before(bench, t0)
                    b1 = _series_last_on_or_before(bench, t1)
                    if b0 > 0 and b1 > 0:
                        benchmark_twr = _annualized_twr_cagr(b0, b1, years)
                        metrics["benchmark_twr"] = float(benchmark_twr)
                        metrics["alpha_twr"] = float(metrics["cagr"] - benchmark_twr)
        except (TypeError, ValueError, KeyError, IndexError):
            pass

    return metrics


def _annualization_factor(frequency: str) -> int:
    if frequency == "daily":
        return 252
    if frequency == "monthly":
        return 12
    raise ValueError("Unsupported frequency. Use 'daily' or 'monthly'.")


def _annualized_twr_cagr(price_start: float, price_end: float, span_years: float) -> float:
    """Geometric annual return: (end/start)^(1/years) - 1 (time-weighted over span_years)."""
    if span_years <= 0 or price_start <= 0 or price_end <= 0:
        return 0.0
    return float((price_end / price_start) ** (1.0 / span_years) - 1.0)


def _series_last_on_or_before(series: pd.Series, t: pd.Timestamp) -> float:
    """Last sorted-series value at an index <= t (as-of); if all indices are after t, use first."""
    s = series.sort_index()
    if s.empty:
        raise ValueError("series is empty")
    ts = pd.Timestamp(t)
    sub = s.loc[s.index <= ts]
    if sub.empty:
        return float(s.iloc[0])
    return float(sub.iloc[-1])


def _max_drawdown(values: pd.Series) -> float:
    running_max = values.cummax()
    drawdowns = values / running_max - 1.0
    return float(drawdowns.min())


# Common ticker display names for asset correlation table
_TICKER_NAMES: Dict[str, str] = {
    "VTI": "Vanguard Total Stock Market ETF",
    "VOO": "Vanguard S&P 500 ETF",
    "QQQ": "Invesco QQQ Trust",
    "BND": "Vanguard Total Bond Market ETF",
    "TIP": "iShares TIPS Bond ETF",
    "VXUS": "Vanguard Total International Stock ETF",
    "SPY": "SPDR S&P 500 ETF Trust",
    "EFA": "iShares MSCI EAFE ETF",
    "VBMFX": "Vanguard Total Bond Market Index Fund",
    "GLD": "SPDR Gold Shares",
    "AGG": "iShares Core U.S. Aggregate Bond ETF",
}


def compute_asset_correlations(
    returns: pd.DataFrame,
    frequency: str,
    ticker_names: Optional[Dict[str, str]] = None,
    weights: Optional[Dict[str, float]] = None,
) -> Dict:
    """
    Compute per-asset correlations, CAGR, expected return, and volatility.

    Returns a dict suitable for the UI:
      - tickers: list of ticker symbols in order
      - rows: list of {ticker, name, correlations: {ticker: corr}, cagr, expected_return, volatility}
    """
    if returns.empty or returns.shape[1] == 0:
        return {"tickers": [], "rows": []}

    names = dict(ticker_names or {})
    names.update(_TICKER_NAMES)

    ann_factor = _annualization_factor(frequency)
    tickers = list(returns.columns)
    n_periods = len(returns)
    try:
        idx = returns.index
        if hasattr(idx, "min") and hasattr(idx, "max") and len(idx) >= 2:
            span_days = (idx[-1] - idx[0]).days
            years = max(span_days / 365.25, 1.0 / 12.0)
        else:
            years = n_periods / ann_factor if ann_factor > 0 else 1.0
    except (TypeError, AttributeError, ValueError):
        years = n_periods / ann_factor if ann_factor > 0 else 1.0

    corr_matrix = returns.corr()
    rows = []
    for ticker in tickers:
        series = returns[ticker].dropna()
        if len(series) < 2:
            cagr = 0.0
            exp_ret = 0.0
            vol = 0.0
        else:
            start_val = 1.0
            end_val = (1 + series).prod()
            cagr = (end_val ** (1 / years) - 1) if years > 0 else 0.0
            exp_ret = float(series.mean() * ann_factor)
            vol = float(series.std(ddof=1) * (ann_factor ** 0.5))

        correlations = {}
        for other in tickers:
            c = corr_matrix.loc[ticker, other] if ticker in corr_matrix.index and other in corr_matrix.columns else 0.0
            correlations[other] = 0.0 if pd.isna(c) else float(c)

        weight = (weights or {}).get(ticker)
        rows.append({
            "ticker": ticker,
            "name": names.get(ticker, ticker),
            "weight": round(weight, 4) if weight is not None else None,
            "correlations": correlations,
            "cagr": round(cagr, 4),
            "expected_return": round(exp_ret, 4),
            "volatility": round(vol, 4),
        })
    return {"tickers": tickers, "rows": rows}

