"""
Mr Brown — portfolio drift, drivers, and rebalance hints.

Computes structured FACTS (weights vs initial, 1d/7d moves, prescriptive share deltas)
and sends them to Gemini with a fixed persona prompt. No CrewAI dependency.
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from google import genai

from backend import db as db_module
from backend.crewai_app.gemini_token_usage import gemini_model_id_for_google_genai_api, log_generate_content_usage
from backend.db import record_user_gemini_token_usage
from backend.portfolio_valuation import (
    _mark_to_market_by_ticker_cached,
    _row_positions,
    load_ohlc_frame,
    mark_to_market_latest,
    refresh_portfolio_valuation,
    _normalize_weights,
)

_log = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Broad US / intl / factor ETFs beyond those in alphavantage_sector_bridge (used only for Mr Brown balance heuristics).
_MR_BROWN_EXTRA_ETFS: frozenset[str] = frozenset(
    {
        "SPY",
        "VOO",
        "VTI",
        "IVV",
        "SPLG",
        "SPTM",
        "ITOT",
        "QQQ",
        "QQQM",
        "IWM",
        "DIA",
        "SCHX",
        "SCHB",
        "SCHA",
        "SCHM",
        "SCHG",
        "SCHV",
        "SCHD",
        "DGRO",
        "HDV",
        "VYM",
        "JEPI",
        "JEPQ",
        "QYLD",
        "XYLD",
        "RYLD",
        "VT",
        "ACWI",
        "IEMG",
        "EFA",
        "EEM",
        "VUG",
        "VB",
        "VO",
        "VV",
        "MGK",
        "MTUM",
        "QUAL",
        "USMV",
        "SPLV",
        "RSP",
        "EWJ",
        "EWG",
        "EWU",
        "EWC",
        "FXI",
        "INDA",
        "XLK",
        "XLF",
        "XLE",
        "XLV",
        "XLI",
        "XLY",
        "XLP",
        "XLU",
        "XLB",
        "XLRE",
        "XLC",
        "SOXX",
        "SMH",
        "ARKK",
        "ARKQ",
        "ARKG",
        "ARKF",
        "ARKW",
        "IBIT",
        "FBTC",
        "GBTC",
    }
)
_MR_BROWN_ETF_UNIVERSE: Optional[frozenset[str]] = None


def _mr_brown_etf_universe() -> frozenset[str]:
    global _MR_BROWN_ETF_UNIVERSE
    if _MR_BROWN_ETF_UNIVERSE is not None:
        return _MR_BROWN_ETF_UNIVERSE
    from backend.alphavantage_sector_bridge import (
        BOND_ETF_TICKERS,
        CRYPTO_ETP_TICKERS,
        INTL_EQUITY_TICKERS,
        MATERIALS_TICKERS,
    )

    _MR_BROWN_ETF_UNIVERSE = frozenset(
        BOND_ETF_TICKERS
        | CRYPTO_ETP_TICKERS
        | INTL_EQUITY_TICKERS
        | MATERIALS_TICKERS
        | _MR_BROWN_EXTRA_ETFS
    )
    return _MR_BROWN_ETF_UNIVERSE


def _is_mrbrown_etf_ticker(ticker: str) -> bool:
    t = str(ticker or "").strip().upper()
    return bool(t) and t in _mr_brown_etf_universe()


def user_seeks_rebalance_or_balance_advice(message: str) -> bool:
    """True when the user likely wants allocation / diversification / rebalance guidance (deeper sector rollup)."""
    m = (message or "").lower()
    triggers = (
        "rebalanc",
        "re-balance",
        "re balance",
        "need to rebalance",
        "should i rebalance",
        "do i need",
        "balanced",
        "balance my",
        "allocation",
        "diversif",
        "overweight",
        "concentrat",
        "too much in",
        "sector",
        "sectors",
        "etf",
        "etfs",
        "single stock",
        "individual stock",
        "buy or sell",
        "buy/sell",
        "should i sell",
        "should i buy",
        "trim",
        "add more",
    )
    return any(t in m for t in triggers)


def _rollup_current_gics_sector_weights(
    current_w: Dict[str, float],
    per_ticker_sectors: Dict[str, Dict[str, float]],
) -> Dict[str, float]:
    agg: Dict[str, float] = defaultdict(float)
    for t, w in current_w.items():
        try:
            fw = float(w)
        except (TypeError, ValueError):
            continue
        if fw <= 0:
            continue
        smap = per_ticker_sectors.get(str(t).strip().upper()) or {"Other": 1.0}
        for sec, frac in smap.items():
            try:
                fsec = float(frac)
            except (TypeError, ValueError):
                continue
            agg[str(sec).strip() or "Other"] += fw * fsec
    return {k: round(v, 6) for k, v in sorted(agg.items(), key=lambda kv: -kv[1])}


def _compute_balance_basics(
    row: Dict[str, Any],
    current_w: Dict[str, float],
    *,
    include_deep_sector_rollup: bool,
) -> Dict[str, Any]:
    """
    Structured balance checks for Mr Brown:
    - GICS-style sector sleeves: no sector > 20% of portfolio (requires per-ticker sector maps when deep=True).
    - ETF count: guideline expects *fewer than 8* distinct ETF tickers (i.e. count < 8).
    - Single-name stocks (non-ETF): flag positions with current weight *above* 5% (concentration trim candidates).
    """
    tickers = [str(t).strip().upper() for t in current_w if float(current_w.get(t, 0) or 0) > 1e-8]
    etf_set: Set[str] = {t for t in tickers if _is_mrbrown_etf_ticker(t)}
    etf_count = len(etf_set)
    etf_list_sorted = sorted(etf_set)

    stock_over_5: List[Dict[str, Any]] = []
    for t in tickers:
        if _is_mrbrown_etf_ticker(t):
            continue
        try:
            w = float(current_w.get(t, 0) or 0)
        except (TypeError, ValueError):
            continue
        if w > 0.05 + 1e-9:
            stock_over_5.append({"ticker": t, "current_weight": round(w, 6)})

    out: Dict[str, Any] = {
        "guidelines": {
            "max_sector_weight": 0.20,
            "max_distinct_etf_count_exclusive": 8,
            "note_etf_rule": "Pass when etf_distinct_count < 8 (strictly fewer than eight distinct ETFs in the known-ETF set).",
            "single_stock_concentration": "Non-ETF tickers with current_weight > 0.05 are flagged as concentrated single names.",
        },
        "etf_distinct_count": etf_count,
        "etf_distinct_tickers": etf_list_sorted,
        "etf_count_passes_lt_8": etf_count < 8,
        "non_etf_positions_over_5pct_current_weight": stock_over_5,
        "single_stock_concentration_ok": len(stock_over_5) == 0,
        "current_gics_sector_weights": None,
        "sectors_over_20pct_current": [],
        "sector_rollup_available": False,
        "sector_rollup_note": None,
        "tickers_notable_for_overweight_sector_sleeves": [],
    }

    if include_deep_sector_rollup and tickers:
        try:
            from backend.alphavantage_sector_bridge import per_ticker_normalized_gics_maps_for_tickers

            maps = per_ticker_normalized_gics_maps_for_tickers(tickers)
            rolled = _rollup_current_gics_sector_weights(current_w, maps)
            out["current_gics_sector_weights"] = rolled
            over = [
                {"sector": s, "weight": round(w, 6)}
                for s, w in rolled.items()
                if float(w) > 0.20 + 1e-9
            ]
            out["sectors_over_20pct_current"] = over
            out["sector_rollup_available"] = True
            if over:
                over_secs = {str(r["sector"]) for r in over}
                hints: List[Dict[str, Any]] = []
                for t in tickers:
                    smap = maps.get(t) or {}
                    try:
                        wt = float(current_w.get(t, 0) or 0)
                    except (TypeError, ValueError):
                        continue
                    if wt <= 0:
                        continue
                    for sec in over_secs:
                        try:
                            frac = float(smap.get(sec, 0) or 0)
                        except (TypeError, ValueError):
                            continue
                        c = wt * frac
                        if c >= 0.01:
                            hints.append(
                                {
                                    "ticker": t,
                                    "sector": sec,
                                    "approx_portfolio_weight_in_this_sector_sleeve": round(c, 6),
                                }
                            )
                hints.sort(key=lambda x: -float(x["approx_portfolio_weight_in_this_sector_sleeve"]))
                out["tickers_notable_for_overweight_sector_sleeves"] = hints[:18]
        except Exception as exc:
            out["sector_rollup_note"] = f"Sector rollup failed: {exc}"
            _log.warning("balance_basics sector rollup: %s", exc)
    elif not include_deep_sector_rollup:
        out["sector_rollup_note"] = (
            "Current GICS sector mix is omitted this turn. Ask about rebalancing, diversification, or sector "
            "concentration to compute a mark-to-market sector rollup (uses cached per-ticker sector maps when available)."
        )

    recs: List[str] = []
    if not out["etf_count_passes_lt_8"]:
        recs.append(
            f"ETF count: you show {etf_count} ETF(s) in the known-ETF set; guideline is fewer than eight distinct ETFs. "
            "Consider consolidating overlapping funds or replacing narrow sleeves with fewer, broader holdings (and verify any missing names are ETFs so they count)."
        )
    if stock_over_5:
        names = ", ".join(x["ticker"] for x in stock_over_5[:8])
        recs.append(
            "Single-name concentration: trim or spread "
            f"{names}{'…' if len(stock_over_5) > 8 else ''} — each is over 5% of the portfolio while not classified as an ETF."
        )
    for rowx in out.get("sectors_over_20pct_current") or []:
        recs.append(
            f"Sector sleeve: {rowx['sector']} is about {100 * float(rowx['weight']):.1f}% of the portfolio (>20% guideline); consider trimming or diversifying that sleeve."
        )
    out["balance_recommendations"] = recs
    return out


def _annotate_prescriptive_trades_with_balance_context(
    trades: List[Dict[str, Any]],
    rebalance_watch: List[Dict[str, Any]],
    balance_basics: Dict[str, Any],
) -> None:
    """Mutates each trade dict with flags so the model can align BUY/SELL with 5-25, sector, ETF, and single-stock rules."""
    watch = {str(d.get("ticker") or "").strip().upper() for d in rebalance_watch}
    conc = balance_basics.get("non_etf_positions_over_5pct_current_weight") or []
    conc_set = {str(x.get("ticker") or "").strip().upper() for x in conc}
    sector_hints = balance_basics.get("tickers_notable_for_overweight_sector_sleeves") or []
    sector_flag = {str(h.get("ticker") or "").strip().upper() for h in sector_hints}
    for tr in trades:
        t = str(tr.get("ticker") or "").strip().upper()
        tr["bernstein_5_25_watchlist_includes_ticker"] = t in watch
        is_etf = _is_mrbrown_etf_ticker(t)
        tr["known_etf_for_balance_rules"] = is_etf
        tr["non_etf_over_5pct_current_weight_guideline"] = (t in conc_set) and (not is_etf)
        tr["material_contributor_to_sector_sleeve_over_20pct"] = t in sector_flag


def _bernstein_5_25_facts_summary() -> Dict[str, Any]:
    return {
        "name": "Bernstein-style adaptive 5-25 bands (vs saved initial weights)",
        "band_large_position": "If initial_weight > 20%: flag when absolute drift |current_weight − initial_weight| exceeds 5 percentage points.",
        "band_smaller_position": "If 0 < initial_weight ≤ 20%: flag when |current_weight − initial_weight| / initial_weight exceeds 25%.",
        "matches_json_key": "rebalance_watchlist",
    }


def _gemini_api_key() -> Optional[str]:
    env_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if env_key:
        return env_key
    key_path = PROJECT_ROOT / "gemini_apikey.txt"
    if not key_path.exists():
        return None
    return key_path.read_text(encoding="utf-8").strip() or None


def _mr_brown_model_id() -> str:
    raw = (os.getenv("GEMINI_MODEL_MR_BROWN") or "").strip()
    if raw:
        return raw.split("/", 1)[1].strip() if "/" in raw else raw
    return gemini_model_id_for_google_genai_api()


def _pick_history_on_or_before(hist: List[Dict[str, Any]], target_iso: str) -> Optional[Dict[str, Any]]:
    """hist sorted ascending by date string."""
    best = None
    for row in hist:
        d = str(row.get("date") or "")[:10]
        if d <= target_iso[:10]:
            best = row
    return best


def _pct_change(old: float, new: float) -> Optional[float]:
    if old is None or new is None:
        return None
    if not (old == old and new == new) or old == 0:
        return None
    return (new - old) / abs(old)


def _portfolio_drivers(
    hist: List[Dict[str, Any]], latest: Dict[str, Any], days: int
) -> Dict[str, Any]:
    d_latest = str(latest.get("date") or "")[:10]
    if not d_latest:
        return {"available": False, "reason": "no latest valuation date"}
    try:
        anchor = datetime.strptime(d_latest, "%Y-%m-%d")
    except ValueError:
        return {"available": False, "reason": "bad date"}
    target = (anchor - timedelta(days=days)).strftime("%Y-%m-%d")
    past = _pick_history_on_or_before(hist, target)
    if not past:
        return {"available": False, "reason": "insufficient history", "days": days}
    v0 = float(past.get("value") or 0)
    v1 = float(latest.get("value") or 0)
    bt0 = past.get("by_ticker") if isinstance(past.get("by_ticker"), dict) else {}
    bt1 = latest.get("by_ticker") if isinstance(latest.get("by_ticker"), dict) else {}
    tickers = sorted(set(bt0.keys()) | set(bt1.keys()))
    moves: List[Dict[str, Any]] = []
    for t in tickers:
        a = float(bt0.get(t, 0) or 0)
        b = float(bt1.get(t, 0) or 0)
        moves.append({"ticker": t, "dollar_change": round(b - a, 2), "prior_usd": round(a, 2), "now_usd": round(b, 2)})
    moves.sort(key=lambda x: abs(x["dollar_change"]), reverse=True)
    return {
        "available": True,
        "days": days,
        "as_of_prior": past.get("date"),
        "as_of_latest": latest.get("date"),
        "portfolio_value_prior": v0,
        "portfolio_value_latest": v1,
        "portfolio_pct_change": _pct_change(v0, v1),
        "top_ticker_moves": moves[:12],
    }


def compute_portfolio_facts(
    portfolio_id: str,
    user_id: str,
    *,
    include_balance_deep: bool = False,
) -> Dict[str, Any]:
    row = db_module.get_portfolio(portfolio_id)
    if not row or row.get("user_id") != user_id:
        return {"error": "Portfolio not found or not authorized.", "portfolio_id": portfolio_id}
    try:
        refresh_portfolio_valuation(portfolio_id)
    except Exception as exc:
        _log.warning("mr_brown refresh %s: %s", portfolio_id, exc)
    row = db_module.get_portfolio(portfolio_id) or row
    hist = db_module.get_portfolio_value_history(portfolio_id)
    weights_raw = row.get("portfolio_ticker_weights") or {}
    initial_w = _normalize_weights(weights_raw if isinstance(weights_raw, dict) else {})
    pos = _row_positions(row) or {}
    if not pos:
        return {
            "portfolio_id": portfolio_id,
            "portfolio_name": row.get("portfolio_name"),
            "error": "No stored positions yet; open Update portfolio or re-save to initialize quantities.",
        }

    v_now, d_now = mark_to_market_latest(pos)
    frames: Dict[str, Any] = {}
    for sym in pos:
        fr = load_ohlc_frame(sym)
        if fr is not None:
            frames[sym] = fr
    by_t = _mark_to_market_by_ticker_cached(pos, d_now, frames) if frames else {}
    if v_now <= 0:
        return {"portfolio_id": portfolio_id, "error": "Could not mark portfolio to market (missing prices?)."}

    latest_hist_point = hist[-1] if hist else {"date": d_now, "value": v_now, "by_ticker": by_t}

    current_w: Dict[str, float] = {t: float(by_t.get(t, 0) or 0) / v_now for t in set(initial_w.keys()) | set(by_t.keys())}
    drift: List[Dict[str, Any]] = []
    for t in sorted(set(initial_w.keys()) | set(current_w.keys())):
        wi = float(initial_w.get(t, 0) or 0)
        wc = float(current_w.get(t, 0) or 0)
        drift.append(
            {
                "ticker": t,
                "initial_weight": round(wi, 6),
                "current_weight": round(wc, 6),
                "weight_change": round(wc - wi, 6),
            }
        )

    rebalance_watch: List[Dict[str, Any]] = []
    for d in drift:
        wi = float(d["initial_weight"])
        dw = float(d["weight_change"])
        if wi > 0.20 and abs(dw) > 0.05:
            rebalance_watch.append({**d, "rule": "initial>20% and |Δw|>5pp"})
        elif 0 < wi < 0.20 and abs(dw) / wi > 0.25:
            rebalance_watch.append({**d, "rule": "initial<20% and |Δw|/initial>25%"})

    trades: List[Dict[str, Any]] = []
    for t in sorted(set(initial_w.keys()) | set(pos.keys())):
        wi = float(initial_w.get(t, 0) or 0)
        qty = float((pos.get(t) or {}).get("quantity", 0) or 0)
        cur_val = float(by_t.get(t, 0) or 0)
        px = cur_val / qty if qty > 0 else None
        tgt = wi * v_now
        if px is None or px <= 0:
            continue
        delta_shares = (tgt - cur_val) / px
        if abs(delta_shares) < 1e-6:
            continue
        side = "BUY" if delta_shares > 0 else "SELL"
        trades.append(
            {
                "ticker": t,
                "side": side,
                "shares": round(abs(delta_shares), 6),
                "delta_shares_signed": round(delta_shares, 6),
                "implied_price": round(px, 6),
                "current_value_usd": round(cur_val, 2),
                "target_value_usd": round(tgt, 2),
            }
        )

    total_sell_usd = sum(
        max(float(tr["current_value_usd"]) - float(tr["target_value_usd"]), 0.0) for tr in trades if tr["side"] == "SELL"
    )
    total_buy_usd = sum(
        max(float(tr["target_value_usd"]) - float(tr["current_value_usd"]), 0.0) for tr in trades if tr["side"] == "BUY"
    )

    balance_basics = _compute_balance_basics(
        row,
        current_w,
        include_deep_sector_rollup=include_balance_deep,
    )
    _annotate_prescriptive_trades_with_balance_context(trades, rebalance_watch, balance_basics)

    return {
        "portfolio_id": portfolio_id,
        "portfolio_name": row.get("portfolio_name"),
        "portfolio_category": row.get("portfolio_category") or "growth",
        "valuation_as_of": d_now,
        "portfolio_value_latest": round(v_now, 2),
        "initial_weights": {k: round(v, 6) for k, v in initial_w.items()},
        "current_weights": {k: round(v, 6) for k, v in current_w.items()},
        "drift_by_ticker": drift,
        "bernstein_5_25_rule": _bernstein_5_25_facts_summary(),
        "rebalance_watchlist": rebalance_watch,
        "prescriptive_trades_to_initial_weights": trades,
        "rebalance_cashflow_check_usd": {
            "approx_total_sell_notional": round(max(total_sell_usd, 0), 2),
            "approx_total_buy_notional": round(max(total_buy_usd, 0), 2),
        },
        "change_1d": _portfolio_drivers(hist, latest_hist_point, 1),
        "change_7d": _portfolio_drivers(hist, latest_hist_point, 7),
        "balance_basics": balance_basics,
    }


def _net_worth_summary(user_id: str) -> Dict[str, Any]:
    try:
        bundle = db_module.build_net_worth_chart_series(user_id)
    except Exception as exc:
        return {"error": str(exc)}
    series = bundle.get("series") or []
    if not series:
        return {"series_points": 0, "note": "No net worth history yet."}
    latest = series[-1]
    d_latest = str(latest.get("date") or "")[:10]
    if not d_latest:
        return {"series_points": len(series), "note": "missing dates"}
    try:
        anchor = datetime.strptime(d_latest, "%Y-%m-%d")
    except ValueError:
        return {"series_points": len(series), "note": "bad latest date"}
    p1 = _pick_history_on_or_before(series, (anchor - timedelta(days=1)).strftime("%Y-%m-%d"))
    p7 = _pick_history_on_or_before(series, (anchor - timedelta(days=7)).strftime("%Y-%m-%d"))
    v1 = float(latest.get("value") or 0)
    out: Dict[str, Any] = {
        "valuation_as_of": d_latest,
        "net_worth_latest": v1,
        "change_1d": {},
        "change_7d": {},
    }
    if p1:
        v0 = float(p1.get("value") or 0)
        out["change_1d"] = {
            "prior_date": p1.get("date"),
            "prior_net": v0,
            "delta_usd": round(v1 - v0, 2),
            "pct_change": _pct_change(v0, v1),
        }
    if p7:
        v0 = float(p7.get("value") or 0)
        out["change_7d"] = {
            "prior_date": p7.get("date"),
            "prior_net": v0,
            "delta_usd": round(v1 - v0, 2),
            "pct_change": _pct_change(v0, v1),
        }
    return out


def build_mr_brown_facts_bundle(
    *,
    user_id: str,
    page: str,
    portfolio_id: Optional[str] = None,
    portfolio_ids: Optional[List[str]] = None,
    growth_portfolio_id: Optional[str] = None,
    retirement_portfolio_id: Optional[str] = None,
    include_balance_deep: bool = False,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {"page": page, "user_id": user_id, "portfolios": []}
    if page == "portfolio":
        if not portfolio_id:
            out["note"] = "No portfolio selected on this page."
            return out
        out["portfolios"].append(compute_portfolio_facts(portfolio_id, user_id, include_balance_deep=include_balance_deep))
        return out
    if page == "net_worth":
        out["net_worth"] = _net_worth_summary(user_id)
        ids = [str(x).strip() for x in (portfolio_ids or []) if str(x).strip()]
        for pid in ids:
            prow = db_module.get_portfolio(pid)
            if not prow or prow.get("user_id") != user_id:
                continue
            out["portfolios"].append(compute_portfolio_facts(pid, user_id, include_balance_deep=include_balance_deep))
        if not out["portfolios"] and not out.get("net_worth", {}).get("error"):
            out["note"] = "Link saved portfolios on the net worth sheet to analyze their drift here."
        return out
    if page == "life_plan":
        out["net_worth"] = _net_worth_summary(user_id)
        if growth_portfolio_id:
            out["portfolios"].append(
                compute_portfolio_facts(growth_portfolio_id, user_id, include_balance_deep=include_balance_deep)
            )
        if retirement_portfolio_id:
            out["portfolios"].append(
                compute_portfolio_facts(retirement_portfolio_id, user_id, include_balance_deep=include_balance_deep)
            )
        if not out["portfolios"]:
            out["note"] = "Connect growth and retirement portfolios on this page for drift and rebalance context."
        return out
    out["note"] = "Unknown page kind."
    return out


MR_BROWN_SYSTEM = """You are Mr Brown, a concise portfolio co-pilot.

You have structured FACTS (weights, drift, rebalancing hints, 1d/7d value drivers, net worth where relevant). Use only the parts that match what the user asked in their latest message.

Topic scoping (strict — this overrides breadth elsewhere in this prompt):
- Latest user message is the only scope for this reply. Do not “also” cover other topics they did not ask about in this turn.
- If they ask about recent performance for a specific window (e.g. last 7 days, past week, yesterday, last day): answer ONLY that window using change_7d or change_1d (and its top_ticker_moves / drivers). Do NOT mention rebalancing, rebalance_watchlist, prescriptive_trades_to_initial_weights, weight drift vs targets, or trade lists unless they explicitly asked about those in the same message.
- If they ask about rebalancing, trades back to targets, drift vs initial weights, or what to buy/sell: integrate **all** of the following before naming tickers—do not rely on prescriptive share deltas alone: (A) **Bernstein 5-25** adaptive bands: read `bernstein_5_25_rule` and `rebalance_watchlist` (same thresholds); prioritize explaining and trading names on that watchlist when they drive drift. (B) **Sector sleeves**: `balance_basics.sectors_over_20pct_current` vs 20% cap when `sector_rollup_available`; use `tickers_notable_for_overweight_sector_sleeves` to name trim/rotation candidates that inflate overweight sectors. (C) **ETF count**: `etf_distinct_count` vs `etf_count_passes_lt_8` (fewer than eight distinct known ETFs); if the guideline fails, favor consolidation SELLs among redundant ETFs and avoid BUYs that add another distinct ETF unless you explain the trade-off. (D) **Individual stocks**: `non_etf_positions_over_5pct_current_weight`—non-ETFs over 5% current weight are concentration risks; prefer trimming SELLs there and flag any prescriptive BUY that would add to a name already flagged with `non_etf_over_5pct_current_weight_guideline` on that ticker’s trade row. Use `drift_by_ticker` / `prescriptive_trades_to_initial_weights` as the mechanical path back to saved targets, **cross-check each listed trade’s** `bernstein_5_25_watchlist_includes_ticker`, `material_contributor_to_sector_sleeve_over_20pct`, `known_etf_for_balance_rules`, and `non_etf_over_5pct_current_weight_guideline` flags, and reconcile: if a BUY worsens concentration or sector/ETF guidelines, say so and suggest an alternative sequencing (e.g. trim concentration and overweight sleeves first, then rebuild toward targets). Still mirror the estimated share quantities from prescriptive_trades when you give trade wording, unless you explicitly justify deferring a leg. Use `balance_basics.balance_recommendations` as deterministic hints. Do NOT add a separate 7-day or 1-day performance section unless they asked about recent performance too.
- If they ask one narrow fact (e.g. “how did it do last 7 days?”), keep the answer short (about 2–6 sentences). No follow-on topics (“you might also…”, “additionally consider rebalancing…”) unless they asked.

FACTS reference (do not re-derive thresholds; only cite when relevant to the question):
- Current weight per ticker: latest marks vs portfolio value vs saved initial weights.
- bernstein_5_25_rule: short definition; same logic as rebalance_watchlist.
- rebalance_watchlist: (initial_weight > 20% AND |Δweight| > 5pp) OR (0 < initial_weight < 20% AND |Δweight|/initial_weight > 25%).
- prescriptive_trades_to_initial_weights: share deltas to move toward initial targets; each row may include bernstein_5_25_watchlist_includes_ticker, material_contributor_to_sector_sleeve_over_20pct, known_etf_for_balance_rules, non_etf_over_5pct_current_weight_guideline—use these when recommending BUY/SELL tickers. rebalance_cashflow_check_usd has approximate sell/buy notionals.
- change_1d / change_7d: portfolio value change over that span and top_ticker_moves.
- balance_basics: structured checks for sleeve balance—use balance_recommendations as deterministic hints; respect sector_rollup_note when sector mix is not rolled up this turn; tickers_notable_for_overweight_sector_sleeves names equity/ETF tickers that materially contribute to a sector sleeve already above 20%.

Rules:
- Ground every number you state in the FACTS JSON. Do not invent prices, weights, or history.
- If the asked window has insufficient history, say so briefly for that window only; do not pivot to unrelated topics.
- When (and only when) the user asked for trades / rebalance: mirror prescriptive_trades_to_initial_weights (with the guideline flags above woven into the narrative order: 5-25 watchlist and concentration/sector/ETF issues first when applicable), label as estimates, wording like “You would execute the following estimated trades…”, e.g. “SELL an estimated 5.25 shares MSFT; BUY an estimated 0.15 shares VOO”.
- When (and only when) you explain rebalancing logic in that trade context, tie drift triggers to `bernstein_5_25_rule` / rebalance_watchlist and the sector / single-stock / ETF guidelines in FACTS—not generic advice.
- Tone: professional, direct; prescriptive only when they asked for trades or rebalance.
"""


def run_mr_brown_chat(
    *,
    user_id: str,
    page: str,
    message: str,
    portfolio_id: Optional[str] = None,
    portfolio_ids: Optional[List[str]] = None,
    growth_portfolio_id: Optional[str] = None,
    retirement_portfolio_id: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    facts = build_mr_brown_facts_bundle(
        user_id=user_id,
        page=page,
        portfolio_id=portfolio_id,
        portfolio_ids=portfolio_ids,
        growth_portfolio_id=growth_portfolio_id,
        retirement_portfolio_id=retirement_portfolio_id,
        include_balance_deep=user_seeks_rebalance_or_balance_advice(message),
    )
    facts_json = json.dumps(facts, default=str, indent=2)
    hist_lines: List[str] = []
    for turn in (history or [])[-10:]:
        role = turn.get("role", "user")
        content = (turn.get("content") or "").strip()
        if content:
            hist_lines.append(f"{role}: {content}")
    history_block = "\n".join(hist_lines) if hist_lines else "(no prior turns)"

    prompt = (
        f"{MR_BROWN_SYSTEM}\n\n"
        f"PAGE: {page}\n"
        f"FACTS (JSON):\n{facts_json}\n\n"
        f"Recent chat:\n{history_block}\n\n"
        f"User message:\n{message.strip()}\n\n"
        "Respond as Mr Brown. Address only what this latest user message asks; omit rebalance/trades if not asked, "
        "omit 7d/1d performance sections if not asked. Do not tack on extra topics for future turns."
    )

    api_key = _gemini_api_key()
    if not api_key:
        return {"reply": "Gemini API key is missing. Set GEMINI_API_KEY or add gemini_apikey.txt.", "facts": facts}

    client = genai.Client(api_key=api_key)
    model = _mr_brown_model_id()
    try:
        response = client.models.generate_content(model=model, contents=prompt)
        usage = log_generate_content_usage(_log, response, label="mr_brown")
        if usage and (user_id or "").strip():
            record_user_gemini_token_usage(
                user_id.strip(),
                source="mr_brown",
                prompt_tokens=usage.prompt_token_count,
                completion_tokens=usage.candidates_token_count,
                total_tokens=usage.total_token_count,
            )
        text = (response.text or "").strip() or "No response text returned."
        text = text.replace(
            "You would execute the following trades",
            "You would execute the following estimated trades",
        )
    except Exception as exc:
        _log.warning("mr_brown chat failed: %s", exc)
        return {"reply": "Sorry — I could not reach the model right now. Try again shortly.", "facts": facts, "error": str(exc)}
    return {"reply": text, "facts": facts}
