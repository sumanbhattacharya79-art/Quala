import argparse
import csv
import sys
from pathlib import Path

import pandas as pd
import requests

import logging

logger = logging.getLogger(__name__)

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from alphavantage_apikey import load_alphavantage_api_key
from alphavantage_merge_utils import atomic_write_csv, merge_timeseries_by_date_index
from list_data_output_tickers import refresh_tickers_list_after_fetch


def _is_crypto_ticker(symbol: str) -> bool:
    csv_path = Path(__file__).resolve().parent / "cryptocurrency_list.csv"
    if not csv_path.exists():
        return False

    symbol_upper = symbol.upper()
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        next(reader, None)  # Skip header
        for row in reader:
            if not row:
                continue
            if row[0].strip().upper() == symbol_upper:
                return True
    return False


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")
    parser = argparse.ArgumentParser(
        description="Fetch Alpha Vantage daily series: TIME_SERIES_DAILY_ADJUSTED (stocks/ETFs) "
        "or DIGITAL_CURRENCY_DAILY (crypto from cryptocurrency_list.csv)."
    )
    parser.add_argument(
        "--apikey",
        default=load_alphavantage_api_key("3VTYJRVCL6BTS18C"),
        help="Alpha Vantage API key (same default as fetch_alphavantage_example.py).",
    )
    parser.add_argument(
        "--symbol",
        default="IBM",
        help="Ticker symbol to fetch.",
    )
    parser.add_argument(
        "--market",
        default="USD",
        help="Fiat market for DIGITAL_CURRENCY_DAILY (e.g. USD, EUR).",
    )
    parser.add_argument(
        "--outputsize",
        choices=("compact", "full"),
        default=None,
        help="Only for stocks/ETFs: compact (last ~100 trading days) or full. "
        "Default: full, or compact when --append is set.",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable SSL verification if your environment requires it.",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Merge into existing CSV by date (new rows only; duplicates keep latest).",
    )
    args = parser.parse_args()

    outputsize = args.outputsize or ("compact" if args.append else "full")

    symbol = args.symbol.upper().strip()
    is_crypto = _is_crypto_ticker(symbol)

    if is_crypto:
        market = args.market.upper().strip()
        url = (
            "https://www.alphavantage.co/query"
            f"?function=DIGITAL_CURRENCY_DAILY&symbol={symbol}&market={market}&apikey={args.apikey}"
        )
    else:
        url = (
            "https://www.alphavantage.co/query"
            f"?function=TIME_SERIES_DAILY_ADJUSTED&outputsize={outputsize}&symbol={symbol}&apikey={args.apikey}"
        )

    try:
        response = requests.get(url, timeout=60, verify=not args.insecure)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        logger.error("ticker history not found: symbol=%s (HTTP/request error: %s)", symbol, exc)
        sys.exit(0)

    if is_crypto:
        timeseries_key = "Time Series (Digital Currency Daily)"
        rename = {
            "1. open": "open",
            "2. high": "high",
            "3. low": "low",
            "4. close": "close",
            "5. volume": "volume",
        }
    else:
        timeseries_key = "Time Series (Daily)"
        rename = {
            "1. open": "open",
            "2. high": "high",
            "3. low": "low",
            "4. close": "close",
            "5. adjusted close": "adjusted_close",
            "6. volume": "volume",
            "7. dividend amount": "dividend_amount",
            "8. split coefficient": "split_coefficient",
        }

    if timeseries_key not in data:
        note = data.get("Note") or data.get("Information") or data.get("Error Message")
        logger.error(
            "ticker history not found: symbol=%s (missing %s; detail=%s)",
            symbol,
            timeseries_key,
            note or data,
        )
        sys.exit(0)

    df = pd.DataFrame.from_dict(data[timeseries_key], orient="index")
    if df.empty:
        logger.error("ticker history not found: symbol=%s (empty daily time series)", symbol)
        sys.exit(0)
    df.index.name = "date"
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    df = df.sort_index()

    output_path = (
        Path(__file__).resolve().parents[1] / "data_output" / f"{symbol}_daily.csv"
    )
    try:
        if args.append:
            merged = merge_timeseries_by_date_index(output_path, df)
            atomic_write_csv(merged, output_path)
            print(f"Merged to {len(merged)} rows at {output_path}")
        else:
            df.to_csv(output_path)
            print(f"Wrote {len(df)} rows to {output_path}")
        refresh_tickers_list_after_fetch(
            output_path.parent,
            log=logger,
            gcs_upload_relative=(output_path.name,),
        )
    except Exception as exc:
        logger.error("ticker history not saved: symbol=%s (%s)", symbol, exc)
        sys.exit(0)


if __name__ == "__main__":
    main()
