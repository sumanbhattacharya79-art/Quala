"""
Mark-to-market saved portfolios using local Alpha Vantage CSVs in data_output/.

Price data: prefers ``{TICKER}_daily.csv``, falls back to ``{TICKER}_monthly.csv`` if daily is missing.

At save: quantity_i = portfolio_value * weight_i / price_i(as_of_date)
Daily value: sum_i quantity_i * price_i(date) — fixed holdings, floating prices.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from backend import db as db_module

_log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_OUTPUT = PROJECT_ROOT / "data_output"


def _pick_close_column(df: pd.DataFrame) -> Optional[str]:
    for c in df.columns:
        cl = str(c).strip().lower().replace(" ", "_")
        if "adjusted" in cl and "close" in cl:
            return c
    for c in df.columns:
        cl = str(c).strip().lower()
        if cl == "close" or cl.endswith(".close") or cl == "4._close":
            return c
    for c in df.columns:
        if "close" in str(c).lower():
            return c
    return None


def load_ohlc_frame(ticker: str) -> Optional[pd.DataFrame]:
    """Load daily CSV if present, else monthly. Returns sorted frame with DatetimeIndex."""
    t = ticker.upper().strip()
    for fname in (f"{t}_daily.csv", f"{t}_monthly.csv"):
        path = DATA_OUTPUT / fname
        if not path.exists():
            continue
        try:
            df = pd.read_csv(path, index_col=0)
            df.index = pd.to_datetime(df.index, errors="coerce")
            df = df[df.index.notna()].sort_index()
            if df.empty:
                continue
            col = _pick_close_column(df)
            if not col:
                continue
            df = df[[col]].rename(columns={col: "_close"})
            return df
        except Exception as e:
            _log.debug("load_ohlc_frame %s: %s", path, e)
    return None


def close_on_or_before(frame: pd.DataFrame, as_of: pd.Timestamp) -> Optional[float]:
    """Last close on or before ``as_of`` (uses searchsorted for speed on repeated calls)."""
    if frame.empty:
        return None
    try:
        pos = int(frame.index.searchsorted(as_of, side="right")) - 1
    except (TypeError, AttributeError):
        sub = frame[frame.index <= as_of]
        if sub.empty:
            return None
        v = sub.iloc[-1]["_close"]
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    if pos < 0:
        return None
    v = frame.iloc[pos]["_close"]
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def latest_close(frame: pd.DataFrame) -> Tuple[pd.Timestamp, float]:
    row = frame.iloc[-1]
    return frame.index[-1], float(row["_close"])


def _normalize_weights(weights: Dict[str, Any]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for k, v in weights.items():
        if v is None or k is None:
            continue
        key = str(k).strip().upper()
        if not key:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv <= 0:
            continue
        out[key] = out.get(key, 0.0) + fv
    total = sum(out.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in out.items()}


def build_positions_from_value(
    weights: Dict[str, float],
    portfolio_value: float,
    as_of_date: str,
) -> Tuple[Dict[str, Dict[str, float]], str]:
    """
    quantity_i = portfolio_value * weight_i / price_i (price on or before as_of_date).
    Returns (positions dict, effective as_of YYYY-MM-DD from data).
    """
    wn = _normalize_weights(weights)
    if not wn or portfolio_value <= 0:
        return {}, as_of_date[:10]
    as_ts = pd.Timestamp(as_of_date[:10])
    positions: Dict[str, Dict[str, float]] = {}
    effective_dates: List[pd.Timestamp] = []

    for sym, w in wn.items():
        fr = load_ohlc_frame(sym)
        if fr is None:
            raise ValueError(f"No price file for {sym} under data_output/ (expected {sym}_daily.csv or _monthly.csv)")
        px = close_on_or_before(fr, as_ts)
        if px is None or px <= 0:
            px2 = close_on_or_before(fr, fr.index.max())
            if px2 is None or px2 <= 0:
                raise ValueError(f"No usable price for {sym} on or before {as_of_date[:10]}")
            px = px2
        sub = fr[fr.index <= as_ts]
        d_used = sub.index[-1] if not sub.empty else fr.index[-1]
        effective_dates.append(d_used)
        qty = (portfolio_value * w) / px
        positions[sym] = {"weight": float(w), "quantity": float(qty), "init_price": float(px)}

    eff = max(effective_dates).strftime("%Y-%m-%d") if effective_dates else as_of_date[:10]
    return positions, eff


def mark_to_market_latest(positions: Dict[str, Dict[str, float]]) -> Tuple[float, str]:
    """Each ticker priced at its last available bar (typical daily refresh)."""
    total = 0.0
    dates: List[str] = []
    for sym, pos in positions.items():
        qty = float(pos.get("quantity", 0) or 0)
        if qty == 0:
            continue
        fr = load_ohlc_frame(sym)
        if fr is None:
            continue
        d, px = latest_close(fr)
        dates.append(d.strftime("%Y-%m-%d"))
        total += qty * px
    ref = max(dates) if dates else datetime.utcnow().strftime("%Y-%m-%d")
    return total, ref


def _mark_to_market_cached(
    positions: Dict[str, Dict[str, float]],
    as_of_date: str,
    frames: Dict[str, pd.DataFrame],
) -> float:
    as_ts = pd.Timestamp(as_of_date[:10])
    total = 0.0
    for sym, pos in positions.items():
        qty = float(pos.get("quantity", 0) or 0)
        if qty == 0:
            continue
        fr = frames.get(sym)
        if fr is None:
            continue
        px = close_on_or_before(fr, as_ts)
        if px is None:
            px = close_on_or_before(fr, fr.index.max())
        if px is None:
            continue
        total += qty * px
    return total


def _mark_to_market_by_ticker_cached(
    positions: Dict[str, Dict[str, float]],
    as_of_date: str,
    frames: Dict[str, pd.DataFrame],
) -> Dict[str, float]:
    """Dollar value held in each ticker on ``as_of_date`` (qty * close on or before date)."""
    as_ts = pd.Timestamp(as_of_date[:10])
    out: Dict[str, float] = {}
    for sym, pos in positions.items():
        qty = float(pos.get("quantity", 0) or 0)
        if qty == 0:
            continue
        fr = frames.get(sym)
        if fr is None:
            continue
        px = close_on_or_before(fr, as_ts)
        if px is None:
            px = close_on_or_before(fr, fr.index.max())
        if px is None:
            continue
        out[sym] = float(qty * px)
    return out


def build_daily_valuation_series(
    positions: Dict[str, Dict[str, float]],
    start_date: str,
    end_date: Optional[str] = None,
) -> List[Tuple[str, float, Dict[str, float]]]:
    """
    One row per **trading day** in range (union of dates in holdings' price files).
    Each row: (date, total_value, by_ticker dollar amounts). Uses cached frames per ticker.
    """
    if not positions:
        return []
    start_ts = pd.Timestamp(start_date[:10])
    end_ts = pd.Timestamp((end_date or datetime.utcnow().strftime("%Y-%m-%d"))[:10])
    frames: Dict[str, pd.DataFrame] = {}
    for sym in positions:
        fr = load_ohlc_frame(sym)
        if fr is not None:
            frames[sym] = fr
    if not frames:
        return []
    date_set: set[str] = set()
    for fr in frames.values():
        mask = (fr.index >= start_ts) & (fr.index <= end_ts)
        for idx in fr.index[mask]:
            date_set.add(idx.strftime("%Y-%m-%d"))
    sorted_dates = sorted(date_set)
    if not sorted_dates:
        d = end_ts.strftime("%Y-%m-%d")
        by_t = _mark_to_market_by_ticker_cached(positions, d, frames)
        v = _mark_to_market_cached(positions, d, frames)
        return [(d, v, by_t)]
    out: List[Tuple[str, float, Dict[str, float]]] = []
    for d in sorted_dates:
        by_t = _mark_to_market_by_ticker_cached(positions, d, frames)
        v = _mark_to_market_cached(positions, d, frames)
        out.append((d, v, by_t))
    return out


def _valuation_series_for_category(
    positions: Dict[str, Dict[str, float]],
    start_date: str,
    _portfolio_category: str,
    end_date: Optional[str] = None,
) -> List[Tuple[str, float, Dict[str, float]]]:
    """Daily points for all portfolio categories (growth and retirement)."""
    return build_daily_valuation_series(positions, start_date, end_date)


def _row_positions(row: Dict[str, Any]) -> Optional[Dict[str, Dict[str, float]]]:
    raw = row.get("portfolio_ticker_positions") or row.get("ticker_positions_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, dict) or not raw:
        return None
    return {str(k).upper(): v for k, v in raw.items() if isinstance(v, dict)}


def initialize_positions_for_portfolio(portfolio_id: str) -> None:
    """Compute and store quantities at portfolio creation (or migration)."""
    row = db_module.get_portfolio(portfolio_id)
    if not row:
        return
    weights = row.get("portfolio_ticker_weights")
    if not isinstance(weights, dict):
        return
    pv = row.get("portfolio_value")
    if pv is None or float(pv) <= 0:
        intake = row.get("intake") or {}
        try:
            pv = float(intake.get("initial_value") or 0)
        except (TypeError, ValueError):
            pv = 0.0
    if pv <= 0:
        _log.warning("portfolio %s: skip positions init (no portfolio_value)", portfolio_id)
        return
    created = row.get("created_at") or ""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(created))
    as_of = m.group(1) if m else datetime.utcnow().strftime("%Y-%m-%d")
    try:
        positions, eff = build_positions_from_value(weights, float(pv), as_of)
    except ValueError as e:
        _log.warning("positions init failed for %s: %s", portfolio_id, e)
        return
    db_module.set_portfolio_positions(portfolio_id, positions, eff)
    cat = (row.get("portfolio_category") or "growth").strip().lower()
    hist = _valuation_series_for_category(positions, eff, cat)
    if hist:
        db_module.replace_portfolio_value_history(portfolio_id, hist)
    v_now, d_now = mark_to_market_latest(positions)
    frames_now: Dict[str, pd.DataFrame] = {}
    for sym in positions:
        fr = load_ohlc_frame(sym)
        if fr is not None:
            frames_now[sym] = fr
    holdings_now = _mark_to_market_by_ticker_cached(positions, d_now, frames_now)
    db_module.upsert_portfolio_value_row(portfolio_id, d_now, v_now, holdings_now)
    db_module.update_portfolio_market_value(portfolio_id, v_now)


def refresh_portfolio_valuation(portfolio_id: str) -> Dict[str, Any]:
    """
    Ensure positions exist, recompute latest MTM, upsert today's history point.
    Full **daily** valuation history is rebuilt on each refresh (all categories).
    """
    row = db_module.get_portfolio(portfolio_id)
    if not row:
        return {}
    pos = _row_positions(row)
    if not pos:
        initialize_positions_for_portfolio(portfolio_id)
        row = db_module.get_portfolio(portfolio_id)
        pos = _row_positions(row) or {}
    if not pos:
        return {"portfolio_value": row.get("portfolio_value"), "valuation_history": []}

    as_of = row.get("positions_as_of_date") or row.get("created_at", "")[:10]
    hist = build_daily_valuation_series(pos, str(as_of)[:10])
    if hist:
        db_module.replace_portfolio_value_history(portfolio_id, hist)

    v_now, d_now = mark_to_market_latest(pos)
    frames_now: Dict[str, pd.DataFrame] = {}
    for sym in pos:
        fr = load_ohlc_frame(sym)
        if fr is not None:
            frames_now[sym] = fr
    holdings_now = _mark_to_market_by_ticker_cached(pos, d_now, frames_now)
    db_module.upsert_portfolio_value_row(portfolio_id, d_now, v_now, holdings_now)
    db_module.update_portfolio_market_value(portfolio_id, v_now)
    full_hist = db_module.get_portfolio_value_history(portfolio_id)
    return {
        "portfolio_value": v_now,
        "valuation_as_of": d_now,
        "valuation_history": full_hist,
        "portfolio_ticker_positions": pos,
    }


def rebalance_positions_after_composition_change(portfolio_id: str) -> None:
    """After weights change: MTM old basket for NAV, then new quantities at latest prices."""
    row = db_module.get_portfolio(portfolio_id)
    if not row:
        return
    old_pos = _row_positions(row)
    weights = row.get("portfolio_ticker_weights")
    if not isinstance(weights, dict):
        return
    if old_pos:
        v, d_ref = mark_to_market_latest(old_pos)
    else:
        v = float(row.get("portfolio_value") or 0)
        d_ref = datetime.utcnow().strftime("%Y-%m-%d")
    if v <= 0:
        return
    try:
        positions, eff = build_positions_from_value(weights, float(v), d_ref)
    except ValueError as e:
        _log.warning("rebalance failed for %s: %s", portfolio_id, e)
        return
    db_module.set_portfolio_positions(portfolio_id, positions, eff)
    cat = (row.get("portfolio_category") or "growth").strip().lower()
    hist = _valuation_series_for_category(positions, eff, cat)
    db_module.replace_portfolio_value_history(portfolio_id, hist)
    v2, d2 = mark_to_market_latest(positions)
    frames2: Dict[str, pd.DataFrame] = {}
    for sym in positions:
        fr = load_ohlc_frame(sym)
        if fr is not None:
            frames2[sym] = fr
    holdings2 = _mark_to_market_by_ticker_cached(positions, d2, frames2)
    db_module.upsert_portfolio_value_row(portfolio_id, d2, v2, holdings2)
    db_module.update_portfolio_market_value(portfolio_id, v2)


def refresh_all_valuations_for_user(user_id: str) -> int:
    rows = db_module.list_portfolios(user_id=user_id, limit=500)
    n = 0
    for r in rows:
        try:
            refresh_portfolio_valuation(r["portfolio_id"])
            n += 1
        except Exception as e:
            _log.warning("refresh %s: %s", r.get("portfolio_id"), e)
    return n
