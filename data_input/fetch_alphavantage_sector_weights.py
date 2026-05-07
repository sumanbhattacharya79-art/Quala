import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from alphavantage_merge_utils import (
    atomic_write_csv,
    merge_sector_weights_history,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_OUTPUT_DIR = PROJECT_ROOT / "data_output"

SPY_SECTOR_MAP = {
    # Canonical SPY sector names
    "COMMUNICATION SERVICES": "Communication Services",
    "CONSUMER DISCRETIONARY": "Consumer Discretionary",
    "CONSUMER STAPLES": "Consumer Staples",
    "ENERGY": "Energy",
    "FINANCIALS": "Financials",
    "HEALTH CARE": "Health Care",
    "INDUSTRIALS": "Industrials",
    "INFORMATION TECHNOLOGY": "Technology",
    "MATERIALS": "Materials",
    "REAL ESTATE": "Real Estate",
    "UTILITIES": "Utilities",
    # Common Alpha Vantage / data source variants
    "COMMUNICATION": "Communication Services",
    "CONSUMER CYCLICAL": "Consumer Discretionary",
    "CONSUMER DEFENSIVE": "Consumer Staples",
    "FINANCE": "Financials",
    "HEALTHCARE": "Health Care",
    "TECHNOLOGY": "Technology",
    "BASIC MATERIALS": "Materials",
}

MATERIALS_TICKERS = {
    "IAUM",
    "GLDM",
    "IAU",
    "GLD",
    "GDX",
    "GDXJ",
    "SIVR",
    "SLV",
    "PSLV",
    "SIL",
    "SILJ",
    "ICOP",
    "COPX",
    "CPER",
    "NLR",
    "URA",
    "URNM",
    "URNJ",
}


def _load_apikey(default_key: str) -> str:
    key_path = Path(__file__).resolve().parents[1] / "alphavantage_apikey.txt"
    if key_path.exists():
        key = key_path.read_text().strip()
        if key:
            return key
    return default_key


def _parse_first_json_blob(response_text: str) -> Any:
    """Handle cases where response may contain multiple JSON blobs."""
    text = response_text.strip()
    if not text:
        raise ValueError("Empty API response")

    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(text)
    return obj


def _get_json(url: str, insecure: bool) -> dict[str, Any]:
    response = requests.get(url, timeout=30, verify=not insecure)
    response.raise_for_status()
    payload = _parse_first_json_blob(response.text)
    if not isinstance(payload, dict):
        raise ValueError(f"Unexpected JSON payload type: {type(payload).__name__}")
    return payload


def _pick_match(best_matches: list[dict[str, Any]], ticker: str) -> dict[str, Any]:
    ticker_upper = ticker.upper()
    for match in best_matches:
        if str(match.get("1. symbol", "")).upper() == ticker_upper:
            return match
    return best_matches[0]


def _map_sector_to_spy(raw_sector: str) -> str:
    cleaned = raw_sector.strip()
    if not cleaned:
        raise ValueError("Cannot map empty sector")

    mapped = SPY_SECTOR_MAP.get(cleaned.upper())
    if mapped:
        return mapped
    raise ValueError(f"Unmapped sector '{raw_sector}'")


def _map_sector_weights_to_spy(sector_weights: dict[str, float]) -> dict[str, float]:
    mapped: dict[str, float] = {}
    for raw_sector, weight in sector_weights.items():
        spy_sector = _map_sector_to_spy(raw_sector)
        mapped[spy_sector] = mapped.get(spy_sector, 0.0) + weight
    return mapped


def _is_usd_crypto_ticker(ticker: str) -> bool:
    crypto_path = Path(__file__).resolve().parent / "cryptocurrency_list.csv"
    if not crypto_path.exists():
        return False

    with crypto_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        next(reader, None)  # header
        for row in reader:
            if len(row) < 2:
                continue
            from_currency = row[0].strip().upper()
            to_currency = row[1].strip().upper()
            if to_currency == "USD" and from_currency == ticker:
                return True
    return False


def _fetch_symbol_type(ticker: str, apikey: str, insecure: bool) -> str:
    url = (
        "https://www.alphavantage.co/query"
        f"?function=SYMBOL_SEARCH&keywords={ticker}&apikey={apikey}"
    )
    data = _get_json(url, insecure=insecure)
    best_matches = data.get("bestMatches", [])
    if not best_matches:
        note = data.get("Note") or data.get("Information") or data.get("Error Message")
        if note:
            raise ValueError(
                f"No symbol search matches found for ticker '{ticker}'. Alpha Vantage note: {note}"
            )
        raise ValueError(f"No symbol search matches found for ticker '{ticker}'")
    if not isinstance(best_matches, list):
        raise ValueError(f"Unexpected bestMatches payload: {best_matches}")

    match = _pick_match(best_matches, ticker=ticker)
    asset_type = str(match.get("3. type", "")).strip()
    #print(asset_type)
    if not asset_type:
        raise ValueError(f"No type found in symbol search match: {match}")
    return asset_type


def _fetch_equity_sector_weights(ticker: str, apikey: str, insecure: bool) -> dict[str, float]:
    url = (
        "https://www.alphavantage.co/query"
        f"?function=OVERVIEW&symbol={ticker}&apikey={apikey}"
    )
    data = _get_json(url, insecure=insecure)
    sector = str(data.get("Sector", "")).strip()
    if not sector:
        raise ValueError(f"No Sector found in OVERVIEW response for ticker '{ticker}'")
    return {_map_sector_to_spy(sector): 1.0}


def _fetch_etf_sector_weights(ticker: str, apikey: str, insecure: bool) -> dict[str, float]:
    url = (
        "https://www.alphavantage.co/query"
        f"?function=ETF_PROFILE&symbol={ticker}&apikey={apikey}"
    )
    data = _get_json(url, insecure=insecure)
    #print(data)
    sectors = data.get("sectors", [])
    if not isinstance(sectors, list) or not sectors:
        raise ValueError(f"No sectors found in ETF_PROFILE response for ticker '{ticker}'")

    result: dict[str, float] = {}
    for entry in sectors:
        if not isinstance(entry, dict):
            continue
        sector = str(entry.get("sector", "")).strip()
        weight_raw = str(entry.get("weight", "")).strip()
        if not sector or not weight_raw:
            continue
        try:
            result[sector] = float(weight_raw)
        except ValueError:
            continue

    if not result:
        raise ValueError(f"Unable to parse ETF sector weights for ticker '{ticker}'")
    return _map_sector_weights_to_spy(result)


def write_sector_weights_csv(ticker: str, sector_weights: dict[str, float], out_dir: Path | None = None) -> Path:
    """Persist sector weights to ``{ticker_lower}_sector_weights.csv`` under data_output."""
    root = out_dir if out_dir is not None else DATA_OUTPUT_DIR
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{ticker.upper().strip().lower()}_sector_weights.csv"
    rows = [
        {"sector": sector, "weight": float(sector_weights[sector])}
        for sector in sorted(sector_weights.keys())
    ]
    df = pd.DataFrame(rows)
    atomic_write_csv(df, path, index=False)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch Alpha Vantage sector weights for a ticker."
    )
    parser.add_argument(
        "--apikey",
        default=_load_apikey("3VTYJRVCL6BTS18C"),
        help="Alpha Vantage API key.",
    )
    parser.add_argument(
        "--ticker",
        required=True,
        help="Ticker symbol to fetch (e.g. IBM, QQQ).",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable SSL verification if your environment requires it.",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append a dated snapshot to ``{ticker}_sector_weights_history.csv`` (snapshot CSV unchanged for the app).",
    )
    args = parser.parse_args()

    ticker = args.ticker.upper().strip()
    if _is_usd_crypto_ticker(ticker):
        sector_weights = {"crypto": 1.0}
    elif ticker in MATERIALS_TICKERS:
        sector_weights = {"Materials": 1.0}
    else:
        asset_type = _fetch_symbol_type(ticker=ticker, apikey=args.apikey, insecure=args.insecure)
        #print("asset_type", asset_type)
        if asset_type == "Equity":
            sector_weights = _fetch_equity_sector_weights(
                ticker=ticker, apikey=args.apikey, insecure=args.insecure
            )
        elif asset_type == "ETF":
            sector_weights = _fetch_etf_sector_weights(
                ticker=ticker, apikey=args.apikey, insecure=args.insecure
            )
        else:
            raise ValueError(
                f"Unsupported asset type '{asset_type}' for ticker '{ticker}'. "
                "Supported types: Equity, ETF."
            )

    root = DATA_OUTPUT_DIR
    write_sector_weights_csv(ticker, sector_weights, out_dir=root)
    if args.append:
        as_of = datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
        hist_path = root / f"{ticker.lower()}_sector_weights_history.csv"
        merged = merge_sector_weights_history(hist_path, as_of, sector_weights)
        atomic_write_csv(merged, hist_path, index=False)
        print(f"Appended history to {hist_path} ({len(merged)} rows)")
    print(f"{ticker}, {json.dumps(sector_weights, sort_keys=True)}")


if __name__ == "__main__":
    main()
