from pathlib import Path
import subprocess
import sys
import json
import math
import argparse
from datetime import date

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtesting.backtesting_service import (
    backtest_portfolio,
    compute_asset_correlations,
    monte_carlo_questions,
    simulate_monte_carlo,
)
from backtesting.backtesting_service.leveraged_etf import (
    LEVERAGED_ETF_UNDERLYING,
    build_prices_for_leveraged_portfolio,
    get_mc_returns_for_leveraged_portfolio,
    pad_prices_to_start_year,
)
from backtesting.backtesting_service.types import (
    BacktestConfig,
    IntakeContext,
    MonteCarloConfig,
    RebalancingRule,
)


def build_results_rows(metrics: dict, answers: dict, scenario: str) -> list[dict]:
    rows = []
    for key, value in metrics.items():
        rows.append(
            {
                "scenario": scenario,
                "section": "backtest_metrics",
                "metric": key,
                "value": value,
            }
        )
    for key, value in answers.items():
        rows.append(
            {
                "scenario": scenario,
                "section": "monte_carlo",
                "metric": key,
                "value": value,
            }
        )
    return rows


def save_results_to_csv(
    output_path: Path, metrics: dict, answers: dict, scenario: str
) -> None:
    rows = build_results_rows(metrics, answers, scenario)
    pd.DataFrame(rows).to_csv(output_path, index=False)


def save_summary_outputs(
    output_dir: Path, summary_paths: dict, metadata: dict, prefix: str
) -> None:
    summary_path = output_dir / f"{prefix}_summary_paths.csv"
    metadata_path = output_dir / f"{prefix}_summary_metadata.json"
    pd.DataFrame(summary_paths).to_csv(summary_path, index_label="period")
    metadata_path.write_text(json.dumps(metadata, indent=2))


def save_rebalancing_outputs(
    output_dir: Path,
    scenario: str,
    prices: pd.DataFrame,
    result,
) -> None:
    events = result.rebalancing_events.copy()
    if not events.empty:
        events["action"] = events["trade_weight_delta"].apply(
            lambda v: "buy" if v > 0 else "sell"
        )
        events["price"] = events.apply(
            lambda row: prices.loc[: row["date"], row["asset"]].iloc[-1],
            axis=1,
        )
        events["quantity"] = events["trade_notional"] / events["price"]
        events.to_csv(
            output_dir / f"portfolio_{scenario}_rebalancing_trades.csv", index=False
        )

    weights_history = result.weights_history
    if not weights_history.empty:
        initial_weights = weights_history.iloc[0]
        final_weights = weights_history.iloc[-1]
        weights_df = pd.DataFrame(
            {
                "asset": initial_weights.index,
                "initial_weight": initial_weights.values,
                "final_weight": final_weights.values,
            }
        )
        weights_df.to_csv(
            output_dir / f"portfolio_{scenario}_weights.csv", index=False
        )


def load_portfolio(portfolio_path: Path) -> dict:
    payload = json.loads(portfolio_path.read_text())
    if "portfolio" not in payload or not isinstance(payload["portfolio"], dict):
        raise ValueError("portfolio.json must contain a 'portfolio' object.")
    return payload["portfolio"]


def load_portfolio_input(portfolio_path: Path | None, portfolio_json: str | None) -> dict:
    if portfolio_json:
        payload = json.loads(portfolio_json)
        if not isinstance(payload, dict):
            raise ValueError("portfolio_json must be a JSON object of weights.")
        return payload
    if portfolio_path is None:
        raise ValueError("portfolio_path is required when portfolio_json is not provided.")
    return load_portfolio(portfolio_path)


# Crypto: normalize XXX-USD -> XXX for loading (data files use BTC, ETH etc.)
_CRYPTO_TICKER_ALIAS: dict[str, str] = {
    "BTC-USD": "BTC", "ETH-USD": "ETH", "SOL-USD": "SOL", "DOGE-USD": "DOGE",
    "XRP-USD": "XRP", "ADA-USD": "ADA", "AVAX-USD": "AVAX", "DOT-USD": "DOT",
    "MATIC-USD": "MATIC", "LINK-USD": "LINK", "UNI-USD": "UNI", "ATOM-USD": "ATOM",
    "LTC-USD": "LTC", "BCH-USD": "BCH", "ETC-USD": "ETC", "XLM-USD": "XLM",
    "ALGO-USD": "ALGO", "VET-USD": "VET", "FIL-USD": "FIL", "TRX-USD": "TRX",
    "NEAR-USD": "NEAR", "APT-USD": "APT", "ARB-USD": "ARB", "OP-USD": "OP",
    "INJ-USD": "INJ", "SUI-USD": "SUI", "SEI-USD": "SEI", "TIA-USD": "TIA",
    "STX-USD": "STX", "PEPE-USD": "PEPE", "WLD-USD": "WLD", "SHIB-USD": "SHIB",
}


def resolve_load_ticker(
    ticker: str, ticker_substitution: dict[str, str] | None = None
) -> str:
    """Resolve ticker to data file symbol (crypto XXX-USD -> XXX, substitution)."""
    sub = ticker_substitution or {}
    load_ticker = sub.get(ticker, ticker)
    if load_ticker.upper().endswith("-USD"):
        load_ticker = _CRYPTO_TICKER_ALIAS.get(
            load_ticker.upper(), load_ticker.upper().split("-")[0]
        )
    return load_ticker


def _dividend_column(df: pd.DataFrame, path: Path) -> str | None:
    """Return dividend column name if present. Try '7. dividend amount', 'dividend amount', 'dividend_amount'."""
    for col in ["7. dividend amount", "dividend amount", "dividend_amount"]:
        if col in df.columns:
            return col
    return None


def _build_total_return_series(close: pd.Series, dividend: pd.Series) -> pd.Series:
    """Build total-return index from close and dividend (reinvested).
    TR_0 = close_0; TR_t = TR_{t-1} * (close_t + div_t) / close_{t-1}.
    """
    close = close.astype(float)
    div = dividend.astype(float).fillna(0) if dividend is not None else pd.Series(0.0, index=close.index)
    div = div.reindex(close.index, fill_value=0)
    prev = close.shift(1)
    # Avoid division by zero; use 1.0 (no change) when prev is zero or NaN
    gross = np.where(prev > 1e-12, (close + div) / prev, 1.0)
    gross = pd.Series(gross, index=close.index)
    gross.iloc[0] = 1.0
    tr = close.iloc[0] * gross.cumprod()
    return tr


def load_prices_from_data_output(
    data_output_dir: Path,
    tickers: list[str],
    ticker_substitution: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Load total-return price series (close + reinvested dividends) for GROWTH backtesting.

    Uses {ticker}_monthly.csv: close and '7. dividend amount'. Does NOT use {ticker}_dividend.csv.
    Retirement backtesting uses backtesting_retirement/data_loader.py instead: close-only +
    separate _dividend.csv for yield (no overlap).
    """
    sub = ticker_substitution or {}
    series = {}
    for ticker in tickers:
        load_ticker = resolve_load_ticker(ticker, sub)
        filename = f"{load_ticker.lower()}_monthly.csv"
        file_path = data_output_dir / filename
        if not file_path.exists():
            _fetch_alphavantage_price(data_output_dir, load_ticker)
        if not file_path.exists():
            raise FileNotFoundError(f"Missing price file: {file_path}")
        df = pd.read_csv(file_path)
        if "date" not in df.columns or "close" not in df.columns:
            raise ValueError(f"File {file_path} must contain 'date' and 'close' columns.")
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        close = df["close"].astype(float)
        div_col = _dividend_column(df, file_path)
        div = df[div_col].astype(float) if div_col else pd.Series(0.0, index=close.index)
        series[ticker] = _build_total_return_series(close, div)
    prices = pd.DataFrame(series).dropna(how="all")
    return drop_current_month_data(prices)


def _fetch_alphavantage_price(data_output_dir: Path, ticker: str) -> None:
    script_path = Path(__file__).resolve().parents[1] / "data_input" / "fetch_alphavantage_example.py"
    if not script_path.exists():
        return
    data_output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(script_path),
        "--symbol",
        ticker,
        "--insecure",
    ]
    subprocess.run(command, check=False)


def load_single_price_series(data_output_dir: Path, ticker: str) -> pd.Series:
    """Load total-return price series (close + reinvested dividends) for one ticker."""
    filename = f"{ticker.lower()}_monthly.csv"
    file_path = data_output_dir / filename
    if not file_path.exists():
        _fetch_alphavantage_price(data_output_dir, ticker)
    if not file_path.exists():
        raise FileNotFoundError(f"Missing price file: {file_path}")
    df = pd.read_csv(file_path)
    if "date" not in df.columns or "close" not in df.columns:
        raise ValueError(f"File {file_path} must contain 'date' and 'close' columns.")
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    close = df["close"].astype(float)
    div_col = _dividend_column(df, file_path)
    div = df[div_col].astype(float) if div_col else pd.Series(0.0, index=close.index)
    series = _build_total_return_series(close, div)
    return drop_current_month_data(series)


# Classic balanced benchmark: 60% US equities / 40% US aggregate bonds (monthly rebalanced returns).
BENCHMARK_SPY_WEIGHT = 0.6
BENCHMARK_AGG_WEIGHT = 0.4


def load_sixty_forty_benchmark_prices(data_output_dir: Path) -> pd.Series:
    """
    Synthetic total-return benchmark: each period return is
    0.6 * r_SPY + 0.4 * r_AGG on overlapping monthly SPY/AGG series.

    Replaces a pure SPY benchmark for beta, tracking error, and Monte Carlo vs benchmark.
    """
    spy = load_single_price_series(data_output_dir, "SPY")
    agg = load_single_price_series(data_output_dir, "AGG")
    both = pd.concat([spy.rename("spy"), agg.rename("agg")], axis=1).dropna(how="any")
    if both.shape[0] < 3:
        raise ValueError(
            "Need overlapping SPY and AGG history for 60/40 benchmark (check data_output CSVs)."
        )
    r_spy = both["spy"].pct_change()
    r_agg = both["agg"].pct_change()
    r_blend = BENCHMARK_SPY_WEIGHT * r_spy + BENCHMARK_AGG_WEIGHT * r_agg
    r_blend = r_blend.dropna()
    if r_blend.empty:
        raise ValueError("Could not compute blended benchmark returns.")
    level = (1.0 + r_blend).cumprod()
    level.name = "benchmark_60_40_spy_agg"
    return level


def drop_current_month_data(
    prices: pd.DataFrame | pd.Series,
    reference_date: date | None = None,
) -> pd.DataFrame | pd.Series:
    """Remove any data from the current month so backtests use only complete months.

    Example: if run in March 2026, data through Feb 2026 is used; if run in April 2026,
    data through Mar 2026 is used. The current month is excluded because it may have
    incomplete or stale data.

    Args:
        prices: DataFrame or Series with DatetimeIndex.
        reference_date: Date to use as "today". Defaults to date.today().

    Returns:
        Filtered prices with current month rows removed.
    """
    ref = reference_date or date.today()
    ref_year, ref_month = ref.year, ref.month

    if isinstance(prices, pd.Series):
        mask = (prices.index.year != ref_year) | (prices.index.month != ref_month)
        return prices.loc[mask].copy()
    # DataFrame
    mask = (prices.index.year != ref_year) | (prices.index.month != ref_month)
    return prices.loc[mask].copy()


def infer_frequency_and_years(prices: pd.DataFrame) -> tuple[str, int]:
    if prices.empty:
        raise ValueError("Prices are empty; cannot infer frequency or years.")
    idx = prices.index.dropna().sort_values()
    if len(idx) < 2:
        raise ValueError("Need at least two dates to infer frequency.")

    inferred = pd.infer_freq(idx)
    if inferred is not None:
        if inferred.startswith(("B", "D")):
            frequency = "daily"
        elif inferred.startswith(("M", "MS")):
            frequency = "monthly"
        else:
            frequency = "daily"
    else:
        median_delta = idx.to_series().diff().dropna().median()
        frequency = "daily" if median_delta <= pd.Timedelta(days=7) else "monthly"

    years_float = (idx[-1] - idx[0]).days / 365.25
    years = max(1, int(round(years_float)))
    return frequency, years


def run_backtests(
    portfolio: dict,
    data_output_dir: Path,
    transaction_cost_bps: float = 0.0,
    intake_context: IntakeContext | None = None,
    ticker_substitution: dict[str, str] | None = None,
    scenarios_filter: list[str] | None = None,
) -> dict:
    # Ensure weights sum to 1.0 (avoid percentage-scale 60/40 being used as leverage)
    weight_sum = sum(float(w) for w in portfolio.values())
    if weight_sum <= 0:
        raise ValueError("Portfolio weights must sum to a positive value.")
    if weight_sum > 1.5:
        portfolio = {k: float(w) / 100.0 for k, w in portfolio.items()}
        weight_sum = sum(portfolio.values())
    if abs(weight_sum - 1.0) > 1e-6:
        portfolio = {k: float(w) / weight_sum for k, w in portfolio.items()}

    tickers = list(portfolio.keys())
    raw_prices = load_prices_from_data_output(
        data_output_dir, tickers, ticker_substitution=ticker_substitution
    )
    # 1) Try leveraged mapping (TQQQ->3x QQQ). 2) History at least to 1999; if shorter, pad with 0.
    has_leveraged = any(t.upper() in LEVERAGED_ETF_UNDERLYING for t in tickers)
    mc_leveraged_substitution: list[str] = []
    if has_leveraged:
        prices, mc_leveraged_substitution = build_prices_for_leveraged_portfolio(
            portfolio, raw_prices, data_output_dir, load_single_price_series, start_year=1999
        )
    else:
        prices = raw_prices
        if not prices.empty:
            mask = prices.index.year >= 1999
            prices = prices.loc[mask].copy()
        prices = pad_prices_to_start_year(prices, start_year=1999, min_years=20)
    frequency, data_years = infer_frequency_and_years(prices)

    def _completed_age_from_birth_first(bd0) -> int:
        import datetime

        now = datetime.datetime.now()
        birth_year = int(bd0[0])
        birth_month = int(bd0[1]) if len(bd0) > 1 else 6
        age = now.year - birth_year
        if (now.month, now.day) < (birth_month, 1):
            age -= 1
        return max(0, age)

    def _growth_horizon_years_effective(ic, fallback_years: int) -> int:
        """Horizon years: optional portfolio [start_age, end_age] with DOB; else horizon_years; else fallback."""
        if ic is None:
            return fallback_years
        bd = getattr(ic, "birth_dates", None) or []
        if bd:
            ca = _completed_age_from_birth_first(bd[0])
            gsa = getattr(ic, "growth_portfolio_start_age", None)
            gea = getattr(ic, "growth_portfolio_end_age", None)
            if gsa is not None or gea is not None:
                try:
                    sa = int(gsa) if gsa is not None else ca
                    if gea is not None:
                        ea = int(gea)
                    elif ic.horizon_years is not None:
                        _hz = int(ic.horizon_years)
                        _hz = max(1, _hz) if _hz <= 0 else _hz
                        ea = ca + _hz
                    else:
                        ea = ca + fallback_years
                    sa = max(ca, min(sa, 120))
                    ea = max(sa + 1, min(ea, 120))
                    return max(1, min(100, ea - sa))
                except (TypeError, ValueError):
                    pass
        if ic.horizon_years is not None:
            _hz0 = int(ic.horizon_years)
            return max(1, _hz0) if _hz0 <= 0 else _hz0
        return fallback_years
    # MC horizon = data length (no alternative) so CAGRs match backtest
    mc_years_effective = data_years
    years = 25  # for API compatibility
    print(f"Frequency: {frequency}, Data years: {data_years}, MC uses actual return length")
    print("Prices:\n", prices)
 

    benchmark_prices = load_sixty_forty_benchmark_prices(data_output_dir)
    benchmark_returns = benchmark_prices.pct_change().dropna()

    # Asset correlations and per-asset metrics for UI table
    price_data = prices.resample("M").last().dropna(how="all") if frequency == "monthly" else prices
    asset_returns = price_data.pct_change().dropna(how="any")
    asset_correlations = (
        compute_asset_correlations(asset_returns, frequency, weights=portfolio)
        if not asset_returns.empty and asset_returns.shape[1] > 0
        else {"tickers": [], "rows": []}
    )

    scenarios = {
        "none": RebalancingRule("none"),
        "monthly": RebalancingRule("monthly"),
        "adaptive_5_25": RebalancingRule(
            "adaptive_5_25", check_frequency="monthly"
        ),
    }
    if scenarios_filter is not None:
        scenarios = {k: v for k, v in scenarios.items() if k in scenarios_filter}

    consolidated_rows = []
    outputs = []
    effective_mc_years = None
    # Use user's initial portfolio value when intake is set so backtest chart scale matches (e.g. 2.8M not 1.0)
    initial_value = intake_context.initial_value if intake_context is not None else 1.0
    for name, rule in scenarios.items():
        config = BacktestConfig(
            frequency=frequency,
            rebalancing_rule=rule,
            transaction_cost_bps=transaction_cost_bps,
            initial_value=initial_value,
            intake_context=intake_context,
        )

        result = backtest_portfolio(
            prices=prices,
            target_weights=portfolio,
            benchmark_prices=benchmark_prices,
            config=config,
        )

        portfolio_returns = result.timeseries["portfolio_return"].dropna()
        actual_periods = len(portfolio_returns)
        ts = result.timeseries
        pv_col = ts["portfolio_value"]
        periods_per_year = 12 if frequency == "monthly" else 252
        fallback_horizon_years = max(1, round(actual_periods / periods_per_year))
        horizon_years = _growth_horizon_years_effective(intake_context, fallback_horizon_years)
        idx_at_horizon = min(
            horizon_years * periods_per_year - 1,
            actual_periods - 1,
            len(pv_col) - 1,
        )
        idx_at_horizon = max(0, idx_at_horizon)
        value_at_retirement = float(pv_col.iloc[idx_at_horizon]) if idx_at_horizon >= 0 and len(pv_col) > idx_at_horizon else None
        result_metrics = dict(result.metrics)
        result_metrics["portfolio_value_at_retirement"] = value_at_retirement

        # Use market-only returns for MC when intake has contributions — avoids double-counting
        mc_returns = (
            result.timeseries["portfolio_return_market"].dropna()
            if "portfolio_return_market" in result.timeseries.columns
            else portfolio_returns
        )
        # When we used build_prices_for_leveraged_portfolio, backtest already has 3x underlying.
        # Only substitute for MC when we have leveraged but did NOT use adjusted prices (e.g. API path).
        if has_leveraged and not mc_leveraged_substitution:
            price_data = prices.resample("M").last().dropna(how="all") if frequency == "monthly" else prices
            returns_df = price_data.pct_change().dropna(how="any")
            if not returns_df.empty:
                mc_returns, mc_leveraged_substitution = get_mc_returns_for_leveraged_portfolio(
                    portfolio, returns_df, data_output_dir, load_single_price_series
                )
        strategy_a_returns = mc_returns
        strategy_b_returns = benchmark_returns

        effective_mc_years = round(actual_periods / (12 if frequency == "monthly" else 252))
        # Use same horizon as backtest: when intake has horizon_years, MC runs to that year
        # so terminal value and CAGR align with portfolio_value_at_retirement
        mc_periods = min(
            horizon_years * periods_per_year,
            actual_periods,
        )
        mc_years_for_config = round(mc_periods / periods_per_year)
        effective_mc_years = mc_years_for_config
        # block_size=1 when intake: reduces bootstrap bias so MC P50 aligns with backtest
        block_size = 1 if intake_context is not None else 12
        mc_config = MonteCarloConfig(
            years=mc_years_for_config,
            n_sims=5000,
            frequency=frequency,
            blowup_threshold=0.0,
            intake_context=intake_context,
            block_size=block_size,
        )

        answers = monte_carlo_questions(
            portfolio_returns=mc_returns,
            benchmark_returns=benchmark_returns,
            strategy_a_returns=strategy_a_returns,
            strategy_b_returns=strategy_b_returns,
            config=mc_config,
            years=mc_years_for_config,
            frequency=frequency,
            periods=mc_periods,
        )

        consolidated_rows.extend(build_results_rows(result.metrics, answers, name))

        sim = simulate_monte_carlo(
            mc_returns,
            config=mc_config,
            years=mc_years_for_config,
            frequency=frequency,
            periods=mc_periods,
        )

        # Use terminal values from same sim as summary_paths so chart and table match
        tv = sim["terminal_values"]
        answers["terminal_value_p10"] = float(np.quantile(tv, 0.1))
        answers["terminal_value_p50"] = float(np.quantile(tv, 0.5))
        answers["terminal_value_p90"] = float(np.quantile(tv, 0.9))

        # Sample paths for spaghetti plot: years on x-axis, portfolio value on y-axis
        paths = sim["paths"]
        n_paths, n_periods = paths.shape
        periods_per_year = 12 if frequency == "monthly" else 252
        max_spaghetti = 150
        step = max(1, n_paths // max_spaghetti)
        path_indices = np.arange(0, n_paths, step)[:max_spaghetti]
        year_indices = np.minimum(
            np.arange(0, n_periods, periods_per_year),
            n_periods - 1,
        )
        paths_sample = paths[np.ix_(path_indices, year_indices)]
        paths_sample_list = paths_sample.tolist()

        outputs.append(
            {
                "scenario": name,
                "metrics": result_metrics,
                "monte_carlo": answers,
                "timeseries": result.timeseries.reset_index().to_dict(orient="records"),
                "summary_paths": {k: v.tolist() for k, v in sim["summary_paths"].items()},
                "summary_metadata": sim["metadata"],
                "rebalancing_events": result.rebalancing_events.to_dict(orient="records"),
                "paths_sample": paths_sample_list,
                "paths_sample_years": list(range(len(year_indices))),
                "mc_leveraged_substitution": mc_leveraged_substitution,
            }
        )

    mc_sub = outputs[0].get("mc_leveraged_substitution", []) if outputs else []
    return {
        "scenarios": outputs,
        "consolidated_rows": consolidated_rows,
        "frequency": frequency,
        "years": years,
        "mc_years": effective_mc_years or mc_years_effective,
        "data_years": data_years,
        "asset_correlations": asset_correlations,
        "n_sims": 5000,
        "mc_leveraged_substitution": mc_sub,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run backtesting and Monte Carlo simulations for a portfolio."
    )
    parser.add_argument(
        "--portfolio",
        default=str(PROJECT_ROOT / "portfolio.json"),
        help="Path to portfolio.json",
    )
    parser.add_argument(
        "--portfolio-json",
        help="JSON object of portfolio weights (overrides --portfolio).",
    )
    args = parser.parse_args()

    portfolio_path = Path(args.portfolio) if args.portfolio else None
    data_output_dir = PROJECT_ROOT / "data_output"
    portfolio = load_portfolio_input(portfolio_path, args.portfolio_json)

    output_dir = PROJECT_ROOT / "model_output"
    output_dir.mkdir(parents=True, exist_ok=True)

    results = run_backtests(
        portfolio=portfolio,
        data_output_dir=data_output_dir,
    )

    for scenario in results["scenarios"]:
        name = scenario["scenario"]
        output_path = output_dir / f"portfolio_output_{name}.csv"
        save_results_to_csv(output_path, scenario["metrics"], scenario["monte_carlo"], name)
        print(f"Saved results to {output_path}")

        save_summary_outputs(
            output_dir,
            scenario["summary_paths"],
            scenario["summary_metadata"],
            f"portfolio_{name}",
        )
        print(
            f"Saved summary paths to {output_dir / f'portfolio_{name}_summary_paths.csv'} "
            f"and metadata to {output_dir / f'portfolio_{name}_summary_metadata.json'}"
        )

    consolidated_path = output_dir / "portfolio_output_all.csv"
    pd.DataFrame(results["consolidated_rows"]).to_csv(consolidated_path, index=False)
    print(f"Saved consolidated results to {consolidated_path}")


if __name__ == "__main__":
    main()

