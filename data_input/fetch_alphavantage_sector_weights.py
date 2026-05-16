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

import logging

logger = logging.getLogger(__name__)

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from alphavantage_apikey import load_alphavantage_api_key
from alphavantage_merge_utils import (
    atomic_write_csv,
    merge_sector_weights_history,
)
from list_data_output_tickers import refresh_tickers_list_after_fetch

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
    raise ValueError(f"Unmapped sector '{raw_sector}' (sector not found for mapping)")


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
        detail = f" Alpha Vantage: {note}" if note else ""
        raise ValueError(f"No symbol search matches for ticker '{ticker}'.{detail}")
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
        raise ValueError(f"No Sector field in OVERVIEW for ticker '{ticker}' (sector not found)")
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
        raise ValueError(
            f"No sectors list in ETF_PROFILE for ticker '{ticker}' (ETF sector weights not found)"
        )

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
        raise ValueError(
            f"Could not parse usable ETF sector weights for ticker '{ticker}' (weights not found)"
        )
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
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")
    parser = argparse.ArgumentParser(
        description="Fetch Alpha Vantage sector weights for a ticker."
    )
    parser.add_argument(
        "--apikey",
        default=load_alphavantage_api_key("3VTYJRVCL6BTS18C"),
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
    try:
        if _is_usd_crypto_ticker(ticker):
            sector_weights = {"crypto": 1.0}
        elif ticker in MATERIALS_TICKERS:
            sector_weights = {"Materials": 1.0}
        else:
            asset_type = _fetch_symbol_type(ticker=ticker, apikey=args.apikey, insecure=args.insecure)
            if asset_type == "Equity":
                sector_weights = _fetch_equity_sector_weights(
                    ticker=ticker, apikey=args.apikey, insecure=args.insecure
                )
            elif asset_type == "ETF":
                sector_weights = _fetch_etf_sector_weights(
                    ticker=ticker, apikey=args.apikey, insecure=args.insecure
                )
            else:
                logger.error(
                    "sector weights not available: ticker=%s unsupported asset_type=%s",
                    ticker,
                    asset_type,
                )
                sys.exit(0)

        root = DATA_OUTPUT_DIR
        sw_path = write_sector_weights_csv(ticker, sector_weights, out_dir=root)
        if args.append:
            as_of = datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
            hist_path = root / f"{ticker.lower()}_sector_weights_history.csv"
            try:
                merged = merge_sector_weights_history(hist_path, as_of, sector_weights)
                atomic_write_csv(merged, hist_path, index=False)
                print(f"Appended history to {hist_path} ({len(merged)} rows)")
            except Exception as exc:
                logger.error(
                    "sector weights history not updated: ticker=%s (history merge failed: %s)",
                    ticker,
                    exc,
                )
        print(f"{ticker}, {json.dumps(sector_weights, sort_keys=True)}")
        refresh_tickers_list_after_fetch(
            root, log=logger, gcs_upload_relative=(sw_path.name,)
        )
    except ValueError as exc:
        err = str(exc)
        el = err.lower()
        if "etf_profile" in el or "etf sector weights" in el or "weights not found" in el:
            logger.error("ETF sector weights not found: ticker=%s detail=%s", ticker, err)
        elif "sector not found" in el or "unmapped sector" in el or "no sector field" in el:
            logger.error("sector not found: ticker=%s detail=%s", ticker, err)
        elif "symbol search" in el or "bestmatches" in el or "no symbol" in el:
            logger.error("ticker not found in symbol search: ticker=%s detail=%s", ticker, err)
        elif "cannot map empty" in el:
            logger.error("sector not found: ticker=%s detail=%s", ticker, err)
        else:
            logger.error("sector weights fetch skipped: ticker=%s detail=%s", ticker, err)
        sys.exit(0)
    except requests.RequestException as exc:
        logger.error("sector weights request failed: ticker=%s detail=%s", ticker, exc)
        sys.exit(0)
    except Exception as exc:
        logger.exception("sector weights unexpected error: ticker=%s", ticker)
        sys.exit(0)


if __name__ == "__main__":
    main()
