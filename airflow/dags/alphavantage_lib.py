"""Shared helpers for Alpha Vantage Airflow DAGs."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_INPUT = PROJECT_ROOT / "data_input"
DATA_OUTPUT = PROJECT_ROOT / "data_output"
TICKERS_CSV = DATA_OUTPUT / "tickers_in_data_output.csv"

SCRIPT_MONTHLY = DATA_INPUT / "fetch_alphavantage_example.py"
SCRIPT_DAILY = DATA_INPUT / "fetch_alphavantage_daily.py"
SCRIPT_DIVIDEND = DATA_INPUT / "fetch_alphavantage_dividend.py"
SCRIPT_SECTOR = DATA_INPUT / "fetch_alphavantage_sector_weights.py"


def load_tickers() -> list[str]:
    if not TICKERS_CSV.exists():
        raise FileNotFoundError(f"Ticker list not found: {TICKERS_CSV}")
    lines = TICKERS_CSV.read_text(encoding="utf-8").strip().splitlines()
    if not lines:
        return []
    header = lines[0].strip().lower()
    start = 1 if header == "ticker" else 0
    out: list[str] = []
    for line in lines[start:]:
        t = line.split(",")[0].strip().upper()
        if t:
            out.append(t)
    return out


def sleep_sec() -> float:
    return float(os.environ.get("ALPHAVANTAGE_SLEEP_SEC", "12"))


def run_script(args: list[str]) -> None:
    cmd = [sys.executable, *args]
    subprocess.run(cmd, check=True, cwd=str(PROJECT_ROOT))


def fetch_daily_for_tickers() -> None:
    delay = sleep_sec()
    for symbol in load_tickers():
        run_script(
            [str(SCRIPT_DAILY), "--symbol", symbol, "--append"],
        )
        time.sleep(delay)


def fetch_monthly_bundle_for_tickers() -> None:
    """Per ticker: monthly CSV, dividend CSV, sector snapshot + history."""
    delay = sleep_sec()
    for symbol in load_tickers():
        run_script([str(SCRIPT_MONTHLY), "--symbol", symbol, "--append"])
        run_script([str(SCRIPT_DIVIDEND), "--symbol", symbol, "--append"])
        run_script([str(SCRIPT_SECTOR), "--ticker", symbol, "--append"])
        time.sleep(delay)
