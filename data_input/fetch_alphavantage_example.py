import argparse
import csv
import sys
from pathlib import Path

import pandas as pd
import requests

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from alphavantage_merge_utils import atomic_write_csv, merge_timeseries_by_date_index


def _load_apikey(default_key: str) -> str:
    key_path = Path(__file__).resolve().parents[1] / "alphavantage_apikey.txt"
    if key_path.exists():
        key = key_path.read_text().strip()
        if key:
            return key
    return default_key


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
    parser = argparse.ArgumentParser(
        description="Fetch Alpha Vantage TIME_SERIES_DAILY data."
    )
    parser.add_argument(
        "--apikey",
        default=_load_apikey("3VTYJRVCL6BTS18C"),
        help="Alpha Vantage API key.",
    )
    parser.add_argument(
        "--symbol",
        default="IBM",
        help="Ticker symbol to fetch.",
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

    symbol = args.symbol.upper().strip()
    is_crypto = _is_crypto_ticker(symbol)

    if is_crypto:
        url = (
            "https://www.alphavantage.co/query"
            f"?function=DIGITAL_CURRENCY_MONTHLY&symbol={symbol}&market=USD&apikey={args.apikey}"
        )
    else:
        url = (
            "https://www.alphavantage.co/query"
            f"?function=TIME_SERIES_MONTHLY_ADJUSTED&outputsize=full&symbol={symbol}&apikey={args.apikey}"
        )
    response = requests.get(url, timeout=30, verify=not args.insecure)
    response.raise_for_status()
    data = response.json()

    if is_crypto:
        timeseries_key = "Time Series (Digital Currency Monthly)"
    else:
        timeseries_key = "Monthly Adjusted Time Series"
    if timeseries_key not in data:
        raise ValueError(f"Unexpected response: {data}")

    df = pd.DataFrame.from_dict(data[timeseries_key], orient="index")
    df.index.name = "date"
    df = df.rename(
        columns={
            "1. open": "open",
            "2. high": "high",
            "3. low": "low",
            "4. close": "close",
            "5. volume": "volume",
        }
    )
    df = df.sort_index()

    output_path = (
        Path(__file__).resolve().parents[1]
        / "data_output"
        / f"{symbol}_monthly.csv"
    )
    if args.append:
        merged = merge_timeseries_by_date_index(output_path, df)
        atomic_write_csv(merged, output_path)
        print(f"Merged to {len(merged)} rows at {output_path}")
    else:
        df.to_csv(output_path)
        print(f"Wrote {len(df)} rows to {output_path}")


if __name__ == "__main__":
    main()
