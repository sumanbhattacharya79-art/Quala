import argparse
import logging
import sys
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from alphavantage_apikey import load_alphavantage_api_key
from alphavantage_merge_utils import atomic_write_csv, merge_dividend_rows
from list_data_output_tickers import refresh_tickers_list_after_fetch

# Alpha Vantage DIVIDENDS endpoint columns
EXPECTED_COLS = [
    "ex_dividend_date",
    "declaration_date",
    "record_date",
    "payment_date",
    "amount",
]


def _empty_dividend_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=EXPECTED_COLS)


def _parse_dividends_response(data: dict, symbol: str) -> pd.DataFrame:
    """
    Build a normalized dividend DataFrame from Alpha Vantage JSON.
    Returns an empty frame with EXPECTED_COLS when there is no dividend history or usable rows.
    """
    if "Error Message" in data:
        raise ValueError(f"Alpha Vantage error for {symbol}: {data['Error Message']}")
    note = data.get("Note") or data.get("Information")
    if note:
        raise ValueError(f"Alpha Vantage message for {symbol}: {note}")

    if "data" not in data:
        raise ValueError(f"Unexpected response for {symbol} (no 'data' key): {data}")

    raw = data["data"]
    if raw is None:
        return _empty_dividend_frame()
    if not isinstance(raw, list):
        raise ValueError(f"Unexpected 'data' type for {symbol}: {type(raw).__name__}")

    if not raw:
        return _empty_dividend_frame()

    df = pd.DataFrame(raw)
    for col in EXPECTED_COLS:
        if col not in df.columns:
            df[col] = pd.NA
    df = df[EXPECTED_COLS]
    df = df.replace("None", "")

    # Normalize ex_dividend_date: drop rows with no usable date (avoids KeyError on sort)
    def _clean_ex_date(val: object) -> object:
        if val is None or pd.isna(val):
            return pd.NA
        s = str(val).strip()
        if s in ("", "None", "nan", "<NA>"):
            return pd.NA
        return s

    ed = df["ex_dividend_date"].map(_clean_ex_date)
    df = df.assign(ex_dividend_date=ed)
    df = df.dropna(subset=["ex_dividend_date"])

    if df.empty:
        return _empty_dividend_frame()

    df = df.sort_values("ex_dividend_date", ascending=False)
    return df


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")
    parser = argparse.ArgumentParser(
        description="Fetch Alpha Vantage historical dividend data."
    )
    parser.add_argument(
        "--apikey",
        default=load_alphavantage_api_key("3VTYJRVCL6BTS18C"),
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
        help="Merge into existing CSV by ex_dividend_date (duplicates keep latest).",
    )
    args = parser.parse_args()
    symbol = args.symbol.upper().strip()

    url = (
        "https://www.alphavantage.co/query"
        f"?function=DIVIDENDS&symbol={symbol}&apikey={args.apikey}"
    )
    try:
        response = requests.get(url, timeout=30, verify=not args.insecure)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        logger.error(
            "dividend history not found: symbol=%s (HTTP/request error: %s)", symbol, exc
        )
        df = _empty_dividend_frame()
        output_path = (
            Path(__file__).resolve().parents[1]
            / "data_output"
            / f"{symbol}_dividend.csv"
        )
        df.to_csv(output_path, index=False)
        print(f"Wrote {len(df)} rows to {output_path}")
        refresh_tickers_list_after_fetch(
            output_path.parent,
            log=logger,
            gcs_upload_relative=(output_path.name,),
        )
        sys.exit(0)

    try:
        df = _parse_dividends_response(data, symbol=symbol)
    except ValueError as exc:
        logger.error("dividend history not found: symbol=%s detail=%s", symbol, exc)
        df = _empty_dividend_frame()

    output_path = (
        Path(__file__).resolve().parents[1]
        / "data_output"
        / f"{symbol}_dividend.csv"
    )
    try:
        if args.append:
            merged = merge_dividend_rows(output_path, df)
            atomic_write_csv(merged, output_path, index=False)
            print(f"Merged to {len(merged)} rows at {output_path}")
        else:
            df.to_csv(output_path, index=False)
            print(f"Wrote {len(df)} rows to {output_path}")
        refresh_tickers_list_after_fetch(
            output_path.parent,
            log=logger,
            gcs_upload_relative=(output_path.name,),
        )
    except Exception as exc:
        logger.error("dividend history not saved: symbol=%s (%s)", symbol, exc)
        sys.exit(0)


if __name__ == "__main__":
    main()
