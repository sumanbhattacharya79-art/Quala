from __future__ import annotations

from typing import Dict, Iterable, Optional, Tuple

import numpy as np
import pandas as pd

from .metrics import compute_metrics
from .types import BacktestConfig, BacktestResult, IntakeContext, RebalancingRule


def backtest_portfolio(
    prices: pd.DataFrame,
    target_weights: Dict[str, float] | pd.Series,
    benchmark_prices: Optional[pd.Series] = None,
    config: Optional[BacktestConfig] = None,
) -> BacktestResult:
    """
    Run a backtest for a portfolio of assets.

    Parameters
    ----------
    prices : pd.DataFrame
        Price series with DateTimeIndex and columns for each asset.
    target_weights : Dict[str, float] | pd.Series
        Target asset weights that sum to 1.0.
    benchmark_prices : pd.Series | None
        Benchmark price series with matching DateTimeIndex.
    config : BacktestConfig | None
        Backtest configuration.
    """
    if config is None:
        config = BacktestConfig()

    cleaned_prices = _prepare_prices(prices, config.start_date, config.end_date)
    if cleaned_prices.empty:
        raise ValueError("Price series is empty after date filtering.")

    target = _normalize_weights(target_weights, cleaned_prices.columns)
    price_data = _resample_prices(cleaned_prices, config.frequency)
    #print ("price_data:", price_data)
    #exit()
    if benchmark_prices is not None:
        benchmark_prices = _prepare_prices(
            benchmark_prices.to_frame("benchmark"),
            config.start_date,
            config.end_date,
        )["benchmark"]
        benchmark_prices = _resample_prices(benchmark_prices.to_frame("benchmark"), config.frequency)[
            "benchmark"
        ]
    #returns = price_data.pct_change().dropna(how="all")
    returns = price_data.pct_change().dropna(how="any")#.loc['2012-01-01':'2025-12-31']
    #print ("returns:", returns)
    #exit()
    if returns.empty:
        raise ValueError("Price series has insufficient data to compute returns.")

    initial_value = config.initial_value
    if config.intake_context is not None:
        initial_value = config.intake_context.initial_value

    (
        timeseries,
        weights_history,
        rebalancing_events,
    ) = _run_backtest_loop(
        returns,
        target,
        config.rebalancing_rule,
        config.transaction_cost_bps,
        initial_value,
        config.intake_context,
        config.frequency,
    )

    returns_for_beta = (
        timeseries["portfolio_return_market"]
        if config.intake_context is not None and "portfolio_return_market" in timeseries.columns
        else None
    )
    portfolio_values_market = (
        timeseries["portfolio_value_market"]
        if config.intake_context is not None and "portfolio_value_market" in timeseries.columns
        else None
    )
    metrics = compute_metrics(
        timeseries["portfolio_value"],
        timeseries["portfolio_return"],
        config.frequency,
        config.risk_free_rate,
        benchmark_prices,
        initial_value=initial_value if config.intake_context is not None else None,
        returns_for_benchmark=returns_for_beta,
        portfolio_values_market=portfolio_values_market,
    )

    return BacktestResult(
        timeseries=timeseries,
        metrics=metrics,
        weights_history=weights_history,
        rebalancing_events=rebalancing_events,
    )


def _prepare_prices(
    prices: pd.DataFrame, start_date: Optional[str], end_date: Optional[str]
) -> pd.DataFrame:
    data = prices.copy()
    if not isinstance(data.index, pd.DatetimeIndex):
        data.index = pd.to_datetime(data.index)
    data = data.sort_index()
    if start_date is not None:
        data = data.loc[data.index >= pd.to_datetime(start_date)]
    if end_date is not None:
        data = data.loc[data.index <= pd.to_datetime(end_date)]
    data = data.ffill().dropna(how="all")
    return data


def _normalize_weights(
    weights: Dict[str, float] | pd.Series, columns: Iterable[str]
) -> pd.Series:
    if isinstance(weights, dict):
        weights = pd.Series(weights, dtype=float)
    weights = weights.reindex(list(columns)).fillna(0.0)
    total = weights.sum()
    if total <= 0:
        raise ValueError("Target weights must sum to a positive value.")
    return weights / total


def _resample_prices(prices: pd.DataFrame, frequency: str) -> pd.DataFrame:
    if frequency == "daily":
        return prices
    if frequency == "monthly":
        return prices.resample("ME").last().dropna(how="all")
    raise ValueError("Unsupported frequency. Use 'daily' or 'monthly'.")


def _is_end_of_year(date: pd.Timestamp, frequency: str) -> bool:
    """True if this date is the last period of its calendar year (for one-time expense at year end)."""
    if frequency == "monthly":
        return date.month == 12
    if frequency == "daily":
        return date.month == 12 and date.day == 31
    return False


def _run_backtest_loop(
    returns: pd.DataFrame,
    target: pd.Series,
    rebalancing_rule: RebalancingRule,
    transaction_cost_bps: float,
    initial_value: float,
    intake_context: Optional[IntakeContext] = None,
    frequency: str = "monthly",
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    weights = target.copy()
    portfolio_value = initial_value
    portfolio_value_market = initial_value  # value before contributions, for MC returns
    values = []
    weight_rows = []
    rebalance_rows = []
    drawdown = 0.0
    peak = initial_value
    start_date = returns.index[0]

    for date, r in returns.iterrows():
        r = r.fillna(0.0)
        # Clamp returns to prevent (1+r)<=0 from bad data or leveraged ETF extremes
        r = r.clip(lower=-0.99)
        asset_values = portfolio_value * weights * (1 + r)
        portfolio_value = asset_values.sum()
        # If zero (e.g. after large expenses or extreme losses), use epsilon so backtest continues
        if portfolio_value <= 0:
            portfolio_value = 1e-10
            weights = target.copy()
        else:
            weights = asset_values / portfolio_value

        # CAGR = market appreciation only: track value before any inflow/outflow this period
        if intake_context is not None:
            asset_values_market = portfolio_value_market * weights * (1 + r)
            portfolio_value_before_intake = asset_values_market.sum()
        else:
            portfolio_value_before_intake = portfolio_value

        # Portfolio value = prior value * (1 + market return) + inflows - outflows (CAGR uses market-only path above)
        if intake_context is not None:
            years_elapsed = (date - start_date).days / 365.25
            # Monthly contribution grows with inflation each year
            inflation_rate = getattr(intake_context, "inflation_rate", 0.03)
            contrib = intake_context.monthly_savings * ((1 + inflation_rate) ** years_elapsed)
            if frequency == "monthly":
                portfolio_value += contrib
            elif frequency == "daily":
                portfolio_value += contrib / 21.0  # ~21 trading days/month
            # One-time expenses at END of year n: (years_from_start, amount) or (calendar_year, amount) when >= 1000
            for exp in intake_context.upcoming_expenses:
                exp_years, exp_amount = float(exp[0]), float(exp[1])
                if exp_years >= 1000:
                    # Calendar year: apply at end of that year (last month / last day of year)
                    target_year = int(exp_years)
                    if date.year == target_year and _is_end_of_year(date, frequency):
                        portfolio_value = max(1e-10, portfolio_value - exp_amount)
                        break
                else:
                    # Years from start: apply at end of year n (e.g. "in 3 years" = end of year 3)
                    target_year = start_date.year + int(exp_years)
                    if date.year == target_year and _is_end_of_year(date, frequency):
                        portfolio_value = max(1e-10, portfolio_value - exp_amount)
                        break
            portfolio_value = max(portfolio_value, 1e-10)
            # New money invested per target weights
            weights = target.copy()

        if _should_rebalance(date, weights, target, rebalancing_rule):
            trade_delta = (target - weights).fillna(0.0)
            turnover = 0.5 * trade_delta.abs().sum()
            trade_notional = portfolio_value * turnover
            cost = trade_notional * (transaction_cost_bps / 10000.0)
            portfolio_value = max(portfolio_value - cost, 1e-10)
            weights = target.copy()

            for asset, delta in trade_delta.items():
                if delta == 0:
                    continue
                rebalance_rows.append(
                    {
                        "date": date,
                        "asset": asset,
                        "trade_weight_delta": float(delta),
                        "trade_notional": float(portfolio_value * delta),
                    }
                )

        period_return = portfolio_value / values[-1]["portfolio_value"] - 1.0 if values else 0.0
        # Market-only return (before contributions) for Monte Carlo — avoids double-counting
        period_return_market = (
            portfolio_value_before_intake / portfolio_value_market - 1.0
            if values and intake_context is not None
            else period_return
        )
        # Store market-only value for CAGR (no contributions, no expenses)
        portfolio_value_market = portfolio_value_before_intake
        peak = max(peak, portfolio_value)
        drawdown = min(drawdown, portfolio_value / peak - 1.0)

        values.append(
            {
                "date": date,
                "portfolio_value": float(portfolio_value),
                "portfolio_value_market": float(portfolio_value_market),
                "portfolio_return": float(period_return),
                "portfolio_return_market": float(period_return_market) if intake_context is not None else float(period_return),
                "portfolio_drawdown": float(drawdown),
            }
        )
        weight_rows.append({"date": date, **weights.to_dict()})
    #exit()
    timeseries = pd.DataFrame(values).set_index("date")
    weights_history = pd.DataFrame(weight_rows).set_index("date")
    rebalancing_events = pd.DataFrame(rebalance_rows)
    return timeseries, weights_history, rebalancing_events


def _should_rebalance(
    date: pd.Timestamp,
    weights: pd.Series,
    target: pd.Series,
    rule: RebalancingRule,
) -> bool:
    if rule.rule_type == "none":
        return False
    if rule.rule_type == "monthly":
        return date.is_month_end
    if rule.rule_type == "threshold":
        threshold = rule.threshold if rule.threshold is not None else 0.05
        drift = (weights - target).abs().max()
        if rule.check_frequency == "monthly":
            return date.is_month_end and drift > threshold
        if rule.check_frequency == "weekly":
            return date.weekday() == 4 and drift > threshold
        return drift > threshold
    if rule.rule_type == "adaptive_5_25":
        drift = (weights - target).abs()
        thresholds = pd.Series(index=target.index, dtype=float)
        for asset, target_weight in target.items():
            if target_weight >= 0.20:
                thresholds[asset] = 0.05
            else:
                thresholds[asset] = 0.25 * target_weight
        trigger = (drift > thresholds).any()
        if rule.check_frequency == "monthly":
            return date.is_month_end and trigger
        if rule.check_frequency == "weekly":
            return date.weekday() == 4 and trigger
        return trigger
    raise ValueError("Unsupported rebalancing rule.")

