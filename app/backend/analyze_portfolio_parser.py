"""
Analyze-portfolio pipeline:

- User CSV → columns + rows in the UI; user types which column is **ticker** and which is **quantity**.
- No cost basis stored for the user-upload flow.
- For each ticker: read ``data_output/{ticker}_monthly.csv`` (case-insensitive filename),
  **last row**, **close** column (case-insensitive header); current_amount = quantity * close.
- Batch/DB job: ``process_holdings`` uses the same latest-close rule; ``cost_basis`` optional (0).

Run from app/ directory:
  cd app && python -m backend.analyze_portfolio_parser [--portfolio-id ID] [--user-id UID]
"""
from __future__ import annotations

import argparse
import csv
import io
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
APP_DIR = PROJECT_ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import json
import math
import re

import pandas as pd

from backtesting.driver import _fetch_alphavantage_price, resolve_load_ticker

_log = logging.getLogger(__name__)

DATA_OUTPUT_DIR = PROJECT_ROOT / "data_output"


def _csv_field_count(line: str) -> int:
    try:
        return len(next(csv.reader([line])))
    except Exception:
        return 0


def _leading_preamble_skip_lines(text: str) -> int:
    """
    Many broker exports start with a title row (one wide cell or few cells), then the real header row.
    If the first data line has many more columns than the first line, skip the first line.
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return 0
    n0 = _csv_field_count(lines[0])
    n1 = _csv_field_count(lines[1])
    if n1 >= 3 and n0 <= n1 - 2:
        return 1
    return 0


def read_analyze_portfolio_csv(data: bytes) -> pd.DataFrame:
    """
    Parse user-uploaded portfolio CSVs: encoding (incl. BOM), delimiter sniffing, optional title-row skip.
    """
    if not data:
        return pd.DataFrame()

    text: str | None = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = data.decode("latin-1", errors="replace")

    skiprows = _leading_preamble_skip_lines(text)
    common_kw: dict = dict(
        engine="python",
        skipinitialspace=True,
        skiprows=skiprows if skiprows else None,
    )

    def _read(**extra) -> pd.DataFrame:
        kw = {**common_kw, **extra}
        if kw.get("skiprows") is None:
            kw.pop("skiprows", None)
        return pd.read_csv(io.StringIO(text), **kw)

    # Prefer pandas delimiter autodetection (respects quoted commas).
    try:
        df = _read(sep=None)
        if len(df.columns) > 1 or len(df) == 0:
            return df
    except (pd.errors.ParserError, ValueError, TypeError):
        pass

    # csv.Sniffer fallback
    try:
        sample = text[:8192] if len(text) > 8192 else text
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        df = _read(sep=dialect.delimiter)
        if len(df.columns) > 1:
            return df
    except (csv.Error, pd.errors.ParserError, ValueError):
        pass

    best: pd.DataFrame | None = None
    best_nc = 0
    for sep in (",", "\t", ";", "|"):
        try:
            df = _read(sep=sep)
            nc = len(df.columns)
            if nc > best_nc:
                best_nc = nc
                best = df
        except (pd.errors.ParserError, ValueError):
            continue
    if best is not None:
        return best
    return _read(sep=",")


def _strip_bom_header(name: str) -> str:
    s = str(name).strip()
    if s.startswith("\ufeff"):
        s = s[1:].lstrip()
    return s


def _coerce_quantity(raw: object) -> float | None:
    """Parse a spreadsheet quantity: ints/floats, strings with commas/currency, skip null/blank/NaN."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        if isinstance(raw, float) and (math.isnan(raw) or math.isinf(raw)):
            return None
        return float(raw)
    s = str(raw).strip()
    if not s or s in ("-", "—", "N/A", "n/a", "#N/A"):
        return None
    s = s.replace(",", "")
    s = re.sub(r"^\s*\$\s*|\s*\$\s*$", "", s).strip()
    s = re.sub(r"^\((.*)\)$", r"-\1", s)
    try:
        v = float(s)
    except ValueError:
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def round_to_significant_digits(x: float, n: int = 5) -> float:
    """Round a non-zero finite float to ``n`` significant digits."""
    if x == 0:
        return 0.0
    if not isinstance(x, (int, float)) or math.isnan(x) or math.isinf(x):
        return float(x)
    xf = float(x)
    mag = math.floor(math.log10(abs(xf)))
    return float(round(xf, int(n - 1 - mag)))


def normalize_portfolio_weights_significant_digits(
    weights: dict[str, float],
    significant: int = 5,
) -> dict[str, float]:
    """
    Positive ticker weights: round each to ``significant`` significant digits,
    renormalize to sum 1, round again, then adjust the largest weight so the sum is exactly 1.
    Used for analyze-portfolio backtest / Monte Carlo inputs.
    """
    cleaned: dict[str, float] = {}
    for k, v in weights.items():
        t = str(k).strip().upper()
        if not t:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv <= 0:
            continue
        cleaned[t] = fv
    if not cleaned:
        return {}

    tickers = list(cleaned.keys())
    vals = [round_to_significant_digits(cleaned[t], significant) for t in tickers]
    s = sum(vals)
    if s <= 0:
        eq = 1.0 / len(tickers)
        return {t: round_to_significant_digits(eq, significant) for t in tickers}

    vals = [round_to_significant_digits(v / s, significant) for v in vals]
    mi = max(range(len(vals)), key=lambda i: vals[i])
    rest = sum(vals[i] for i in range(len(vals)) if i != mi)
    vals[mi] = round_to_significant_digits(1.0 - rest, significant)
    if vals[mi] < 0:
        vals[mi] = 0.0
        s2 = sum(vals)
        if s2 > 0:
            vals = [round_to_significant_digits(v / s2, significant) for v in vals]
    return {tickers[i]: float(vals[i]) for i in range(len(tickers))}


_QUANTITY_EPS = 1e-12


def _is_effectively_zero_quantity(qty: object) -> bool:
    try:
        q = float(qty if qty is not None else 0)
    except (TypeError, ValueError):
        return True
    if math.isnan(q) or math.isinf(q):
        return True
    return math.isclose(q, 0.0, rel_tol=0.0, abs_tol=_QUANTITY_EPS)


def omit_zero_quantity_holdings(holdings: list[dict]) -> list[dict]:
    """Drop rows whose quantity is (effectively) zero — e.g. after merging files and summing duplicate tickers."""
    return [h for h in holdings if not _is_effectively_zero_quantity(h.get("quantity"))]


_MONEY_EPS = 1e-9


def _is_effectively_zero_current_value(amount: object) -> bool:
    """True if market value (quantity × close) should be treated as zero."""
    try:
        v = float(amount if amount is not None else 0)
    except (TypeError, ValueError):
        return True
    if math.isnan(v) or math.isinf(v):
        return True
    return math.isclose(v, 0.0, rel_tol=0.0, abs_tol=_MONEY_EPS)


def omit_zero_current_amount_rows(rows: list[dict]) -> tuple[list[dict], dict]:
    """
    After ``enrich_holdings_with_latest_close``: drop rows where ``current_amount`` is ~0
    (e.g. missing price series so close is 0). Recomputes ``total_portfolio_value`` and
    ``weights_by_ticker`` like :func:`enrich_holdings_with_latest_close`.
    """
    kept = [r for r in rows if not _is_effectively_zero_current_value(r.get("current_amount"))]
    total_by_ticker: dict[str, float] = {}
    for row in kept:
        t = str(row.get("ticker", "")).strip().upper()
        if not t:
            continue
        try:
            cv = float(row.get("current_amount", 0) or 0)
        except (TypeError, ValueError):
            cv = 0.0
        total_by_ticker[t] = total_by_ticker.get(t, 0.0) + cv
    total = sum(total_by_ticker.values())
    weights: dict[str, float] = {}
    if total > 0:
        for t, amt in total_by_ticker.items():
            weights[t] = amt / total
    else:
        for t in total_by_ticker:
            weights[t] = 0.0
    meta = {"total_portfolio_value": total, "weights_by_ticker": weights}
    return kept, meta


def resolve_user_column_choice(choice: str, available_columns: list[str]) -> str:
    """Map user-typed column name to actual CSV header (exact match, then case-insensitive)."""
    c = (choice or "").strip()
    if not c:
        raise ValueError("Column name is required.")
    for a in available_columns:
        if a == c:
            return a
    low = c.lower()
    for a in available_columns:
        if str(a).strip().lower() == low:
            return a
    raise ValueError(f"No column matching {choice!r}. Available columns: {available_columns}")


def dataframe_upload_preview(df: pd.DataFrame) -> dict:
    """JSON-safe columns and full row list for UI after upload (no holdings inference)."""
    df = df.copy()
    df.columns = [_strip_bom_header(c) for c in df.columns]
    cols = [str(c) for c in df.columns.tolist()]
    records = json.loads(df.to_json(orient="records"))
    return {"columns": cols, "preview_rows": records}


def build_holdings_from_row_dicts(
    preview_rows: list[dict],
    ticker_column: str,
    quantity_column: str,
    available_columns: list[str] | None = None,
) -> list[dict]:
    """Build [{ticker, quantity}, ...] from raw CSV row dicts using user-selected column names."""
    avail = available_columns
    if avail is None and preview_rows:
        avail = list(preview_rows[0].keys())
    if not avail:
        raise ValueError("No columns available.")
    tcol = resolve_user_column_choice(ticker_column, avail)
    qcol = resolve_user_column_choice(quantity_column, avail)
    holdings: list[dict] = []
    for rec in preview_rows:
        if not isinstance(rec, dict):
            continue
        raw_t = rec.get(tcol)
        if raw_t is None or (isinstance(raw_t, str) and not str(raw_t).strip()):
            continue
        ticker = str(raw_t).strip().upper()
        if not ticker:
            continue
        qty = _coerce_quantity(rec.get(qcol))
        if qty is None or _is_effectively_zero_quantity(qty):
            continue
        holdings.append({"ticker": ticker, "quantity": qty})
    if not holdings:
        raise ValueError(
            "No valid rows with ticker and numeric quantity. "
            "Check the ticker/quantity column names match your CSV headers (including no extra spaces), "
            "and that quantity cells are numbers (commas and $ are OK). Blank or text-only quantity cells are skipped."
        )
    return holdings


def dedupe_holdings_sum_quantity(holdings: list[dict]) -> list[dict]:
    """One row per ticker; quantity is the sum of all rows that share that ticker (any file/order)."""
    agg: dict[str, float] = {}
    for h in holdings:
        t = str(h.get("ticker", "")).strip().upper()
        if not t:
            continue
        try:
            q = float(h.get("quantity", 0) or 0)
        except (TypeError, ValueError):
            q = 0.0
        agg[t] = agg.get(t, 0.0) + q
    return [{"ticker": t, "quantity": q} for t, q in sorted(agg.items())]


def _find_monthly_csv_case_insensitive(data_output_dir: Path, ticker: str) -> Path | None:
    """``{ticker}_monthly.csv`` under data_output_dir; filename match is case-insensitive."""
    target = f"{ticker.strip().lower()}_monthly.csv"
    try:
        for p in data_output_dir.iterdir():
            if p.is_file() and p.name.lower() == target:
                return p
    except OSError:
        pass
    return None


def _close_column_name(df: pd.DataFrame) -> str | None:
    for c in df.columns:
        if str(c).strip().lower() == "close":
            return str(c)
    return None


def read_latest_close_from_monthly_csv(path: Path) -> float:
    """Last row, **close** column only (plain close, not adjusted / total return)."""
    df = pd.read_csv(path)
    if df.empty:
        return 0.0
    close_col = _close_column_name(df)
    if not close_col:
        raise ValueError(f"File {path} has no 'close' column.")
    last = df.iloc[-1]
    try:
        return float(last[close_col])
    except (TypeError, ValueError):
        return 0.0


def latest_close_for_ticker(ticker: str, data_output_dir: Path | None = None) -> float:
    """
    Resolve ``{ticker}_monthly.csv`` (case-insensitive), fetch if missing, return last row close.
    """
    out_dir = data_output_dir or DATA_OUTPUT_DIR
    t = str(ticker).strip().upper()
    if not t:
        return 0.0
    load_sym = resolve_load_ticker(t)
    path = _find_monthly_csv_case_insensitive(out_dir, load_sym)
    if path is None:
        path = _find_monthly_csv_case_insensitive(out_dir, t)
    if path is None:
        try:
            _fetch_alphavantage_price(out_dir, str(load_sym))
        except Exception as e:
            _log.debug("Fetch attempt for %s: %s", load_sym, e)
        path = _find_monthly_csv_case_insensitive(out_dir, load_sym) or _find_monthly_csv_case_insensitive(
            out_dir, t
        )
    if path is None:
        _log.warning("No monthly CSV for ticker %s (resolved %s)", t, load_sym)
        return 0.0
    try:
        return read_latest_close_from_monthly_csv(path)
    except Exception as e:
        _log.warning("Could not read close for %s from %s: %s", t, path, e)
        return 0.0


def _latest_close_prices_for_tickers(
    tickers: list[str],
    data_output_dir: Path | None = None,
) -> dict[str, float]:
    """Latest **close** (last line) per ticker, keyed by uppercase user ticker."""
    out: dict[str, float] = {}
    for t in tickers:
        t = str(t).strip().upper()
        if not t:
            continue
        out[t] = latest_close_for_ticker(t, data_output_dir)
    return out


def enrich_holdings_with_latest_close(
    holdings: list[dict],
    data_output_dir: Path | None = None,
) -> tuple[list[dict], dict]:
    """
    One output row per input holding: ticker, quantity, close, current_amount (= quantity * close).
    Call after ``dedupe_holdings_sum_quantity`` so each ticker appears once.
    """
    rows_out: list[dict] = []
    ticker_keys: list[str] = []
    for h in holdings:
        t = str(h.get("ticker", "")).strip().upper()
        if t:
            ticker_keys.append(t)
    unique_tickers = list(dict.fromkeys(ticker_keys))
    close_by_ticker = _latest_close_prices_for_tickers(unique_tickers, data_output_dir)
    total_by_ticker: dict[str, float] = {}
    for row in holdings:
        t = str(row.get("ticker", "")).strip().upper()
        if not t:
            continue
        try:
            qty = float(row.get("quantity", 0) or 0)
        except (TypeError, ValueError):
            qty = 0.0
        close = float(close_by_ticker.get(t, 0) or 0)
        cv = qty * close
        total_by_ticker[t] = total_by_ticker.get(t, 0.0) + cv
        rows_out.append(
            {
                "ticker": t,
                "quantity": qty,
                "close": close,
                "current_amount": cv,
            }
        )
    total = sum(total_by_ticker.values())
    weights: dict[str, float] = {}
    if total > 0:
        for t, amt in total_by_ticker.items():
            weights[t] = amt / total
    else:
        for t in total_by_ticker:
            weights[t] = 0.0
    meta = {"total_portfolio_value": total, "weights_by_ticker": weights}
    return rows_out, meta


def _holdings_snapshot_is_weights_only(holdings: list[dict]) -> bool:
    """True when every non-empty row has target weight and no quantity (analyze backtest snapshot)."""
    seen = False
    for row in holdings:
        ticker = str(row.get("ticker", "")).strip().upper()
        if not ticker:
            continue
        seen = True
        try:
            q = float(row.get("quantity", 0) or 0)
        except (TypeError, ValueError):
            q = 0.0
        if q != 0:
            return False
        if "weight" not in row:
            return False
        try:
            w = float(row.get("weight", 0) or 0)
        except (TypeError, ValueError):
            return False
        if w <= 0:
            return False
    return seen


def process_holdings(
    holdings: list[dict],
    data_output_dir: Path | None = None,
) -> tuple[
    dict[str, float],
    dict[str, float],
    dict[str, float],
    dict[str, float],
    dict[str, float],
    float,
]:
    """
    From holdings [{ticker, quantity, cost_basis?}, ...] or weight-only [{ticker, weight}, ...], compute:
    - cost_basis_by_ticker (0 if missing), quantity_by_ticker
    - current_price_by_ticker: latest row **close** from ``{ticker}_monthly.csv``
    - current_amount_by_ticker, weights_by_ticker, total_portfolio_value
    """
    data_output_dir = data_output_dir or DATA_OUTPUT_DIR

    if _holdings_snapshot_is_weights_only(holdings):
        raw_w: dict[str, float] = {}
        for row in holdings:
            ticker = str(row.get("ticker", "")).strip().upper()
            if not ticker:
                continue
            raw_w[ticker] = float(row.get("weight", 0) or 0)
        total_w = sum(raw_w.values())
        if total_w <= 0:
            return ({}, {}, {}, {}, {}, 0.0)
        weights_by_ticker = {t: w / total_w for t, w in raw_w.items()}
        tickers = list(weights_by_ticker.keys())
        current_price_by_ticker = _latest_close_prices_for_tickers(tickers, data_output_dir)
        cost_basis_by_ticker = {t: 0.0 for t in tickers}
        quantity_by_ticker = {t: 0.0 for t in tickers}
        nominal_total = 1.0
        current_amount_by_ticker = {t: weights_by_ticker[t] * nominal_total for t in tickers}
        return (
            cost_basis_by_ticker,
            quantity_by_ticker,
            current_price_by_ticker,
            current_amount_by_ticker,
            weights_by_ticker,
            nominal_total,
        )

    cost_basis_by_ticker: dict[str, float] = {}
    quantity_by_ticker: dict[str, float] = {}
    for row in holdings:
        ticker = str(row.get("ticker", "")).strip().upper()
        if not ticker:
            continue
        cost_basis_by_ticker[ticker] = float(row.get("cost_basis", 0) or 0)
        quantity_by_ticker[ticker] = float(row.get("quantity", 0) or 0)

    tickers = list(cost_basis_by_ticker.keys())
    current_price_by_ticker = _latest_close_prices_for_tickers(tickers, data_output_dir)

    current_amount_by_ticker: dict[str, float] = {}
    for t in tickers:
        qty = quantity_by_ticker.get(t, 0)
        price = current_price_by_ticker.get(t, 0)
        current_amount_by_ticker[t] = qty * price

    total = sum(current_amount_by_ticker.values())
    weights_by_ticker: dict[str, float] = {}
    if total > 0:
        for t in tickers:
            weights_by_ticker[t] = current_amount_by_ticker[t] / total
    else:
        for t in tickers:
            weights_by_ticker[t] = 0.0

    return (
        cost_basis_by_ticker,
        quantity_by_ticker,
        current_price_by_ticker,
        current_amount_by_ticker,
        weights_by_ticker,
        total,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="Parse analyzed portfolios and compute weights.")
    parser.add_argument("--portfolio-id", help="Process only this portfolio ID")
    parser.add_argument("--user-id", help="Process only portfolios for this user")
    args = parser.parse_args()

    from backend.db import (
        init_db,
        list_analyzed_portfolios,
        update_analyzed_portfolio_computed,
    )

    init_db()
    rows = list_analyzed_portfolios(user_id=args.user_id if args.user_id else None)
    if args.portfolio_id:
        rows = [r for r in rows if r.get("portfolio_id") == args.portfolio_id]
    if not rows:
        _log.info("No portfolios to process.")
        return

    for row in rows:
        pid = row.get("portfolio_id")
        holdings_json = row.get("holdings_json")
        if not holdings_json:
            _log.warning("Portfolio %s has no holdings_json", pid)
            continue
        try:
            holdings = json.loads(holdings_json)
        except json.JSONDecodeError as e:
            _log.warning("Portfolio %s invalid JSON: %s", pid, e)
            continue
        if not isinstance(holdings, list):
            _log.warning("Portfolio %s holdings_json is not a list", pid)
            continue

        _log.info("Processing portfolio %s (%d holdings)", pid, len(holdings))
        try:
            (
                cost_basis_by_ticker,
                quantity_by_ticker,
                current_price_by_ticker,
                current_amount_by_ticker,
                weights_by_ticker,
                total_value,
            ) = process_holdings(holdings, DATA_OUTPUT_DIR)
        except Exception as e:
            _log.exception("Failed to process %s: %s", pid, e)
            continue

        ok = update_analyzed_portfolio_computed(
            portfolio_id=pid,
            cost_basis_by_ticker=cost_basis_by_ticker,
            quantity_by_ticker=quantity_by_ticker,
            current_price_by_ticker=current_price_by_ticker,
            current_amount_by_ticker=current_amount_by_ticker,
            weights_by_ticker=weights_by_ticker,
            total_portfolio_value=total_value,
        )
        if ok:
            _log.info("Updated %s: total_value=%.2f", pid, total_value)
        else:
            _log.warning("Update failed for %s", pid)


if __name__ == "__main__":
    main()
