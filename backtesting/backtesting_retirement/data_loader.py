"""Load monthly price and dividend data from data_output; fetch if missing.

IMPORTANT: Retirement uses close-only prices here and separate {ticker}_dividend.csv
for yield. We intentionally do NOT use the "7. dividend amount" column from
{ticker}_monthly.csv, to avoid double-counting with the yield from _dividend.csv.
Growth backtesting (driver.py) uses that column for total-return prices instead.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_OUTPUT = PROJECT_ROOT / "data_output"
DATA_INPUT = PROJECT_ROOT / "data_input"


def _resolve_ticker(ticker: str, ticker_substitution: Optional[Dict[str, str]] = None) -> str:
    """Resolve ticker for file lookup (e.g. lowercase, substitution)."""
    sub = ticker_substitution or {}
    return sub.get(ticker, ticker)


def _fetch_monthly_prices(ticker: str, data_output_dir: Path) -> None:
    """Run fetch_alphavantage_example.py for the given ticker (--insecure hardcoded)."""
    script = DATA_INPUT / "fetch_alphavantage_example.py"
    if not script.exists():
        raise FileNotFoundError(f"Fetch script not found: {script}")
    data_output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(script), "--symbol", ticker, "--insecure"]
    subprocess.run(cmd, check=False, cwd=PROJECT_ROOT)


def _fetch_dividends(ticker: str, data_output_dir: Path) -> None:
    """Run fetch_alphavantage_dividend.py for the given ticker (--insecure hardcoded)."""
    script = DATA_INPUT / "fetch_alphavantage_dividend.py"
    if not script.exists():
        raise FileNotFoundError(f"Fetch script not found: {script}")
    data_output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(script), "--symbol", ticker, "--insecure"]
    subprocess.run(cmd, check=False, cwd=PROJECT_ROOT)


def load_monthly_prices(
    data_output_dir: Path,
    tickers: List[str],
    ticker_substitution: Optional[Dict[str, str]] = None,
    fetch_if_missing: bool = True,
) -> pd.DataFrame:
    """
    Load monthly close-only price series from {ticker}_monthly.csv.

    Uses ONLY the 'close' column (NOT dividend amount). Yield is loaded separately
    from {ticker}_dividend.csv and added in MC — using the monthly dividend column
    here would double-count.
    """
    out_dir = data_output_dir or DATA_OUTPUT
    series: Dict[str, pd.Series] = {}
    for ticker in tickers:
        load_ticker = _resolve_ticker(ticker, ticker_substitution)
        from backtesting.price_data_paths import monthly_csv_path

        path = monthly_csv_path(out_dir, load_ticker)
        if not path.exists() and fetch_if_missing:
            _fetch_monthly_prices(load_ticker, out_dir)
            path = monthly_csv_path(out_dir, load_ticker)
        if not path.exists():
            raise FileNotFoundError(f"Missing price file: {path}")
        df = pd.read_csv(path)
        if "date" not in df.columns and df.columns[0].lower() == "date":
            df = df.rename(columns={df.columns[0]: "date"})
        if "close" not in df.columns:
            raise ValueError(f"File {path} must contain 'close' column.")
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        series[ticker] = df["close"].astype(float)
    return pd.DataFrame(series).dropna(how="all")


def load_dividends(
    data_output_dir: Path,
    ticker: str,
    ticker_substitution: Optional[Dict[str, str]] = None,
    fetch_if_missing: bool = True,
) -> pd.DataFrame:
    """
    Load dividend history for one ticker from {ticker}_dividend.csv.
    Expected columns: ex_dividend_date, amount (and optionally declaration_date, etc.).
    """
    out_dir = data_output_dir or DATA_OUTPUT
    load_ticker = _resolve_ticker(ticker, ticker_substitution)
    path = out_dir / f"{load_ticker.lower()}_dividend.csv"
    if not path.exists():
        path = out_dir / f"{load_ticker}_dividend.csv"
    if not path.exists() and fetch_if_missing:
        _fetch_dividends(load_ticker, out_dir)
    if not path.exists():
        raise FileNotFoundError(f"Missing dividend file: {path}")
    df = pd.read_csv(path)
    if "ex_dividend_date" not in df.columns or "amount" not in df.columns:
        raise ValueError(f"File {path} must contain ex_dividend_date and amount.")
    df["ex_dividend_date"] = pd.to_datetime(df["ex_dividend_date"], errors="coerce")
    df = df.dropna(subset=["ex_dividend_date", "amount"])
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0)
    return df


def load_prices_and_dividends(
    data_output_dir: Path,
    tickers: List[str],
    ticker_substitution: Optional[Dict[str, str]] = None,
    fetch_if_missing: bool = True,
) -> Tuple[pd.DataFrame, Dict[str, pd.DataFrame]]:
    """
    Load monthly prices (single DataFrame) and per-ticker dividend DataFrames.
    Returns (prices_df, {ticker: dividends_df}).
    """
    prices = load_monthly_prices(
        data_output_dir, tickers, ticker_substitution, fetch_if_missing
    )
    dividends: Dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        try:
            dividends[ticker] = load_dividends(
                data_output_dir, ticker, ticker_substitution, fetch_if_missing
            )
        except FileNotFoundError:
            dividends[ticker] = pd.DataFrame(columns=["ex_dividend_date", "amount"])
    return prices, dividends
