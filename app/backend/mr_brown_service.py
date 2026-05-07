"""
Mr Brown — portfolio drift, drivers, and rebalance hints.

Computes structured FACTS (weights vs initial, 1d/7d moves, prescriptive share deltas)
and sends them to Gemini with a fixed persona prompt. No CrewAI dependency.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

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


def compute_portfolio_facts(portfolio_id: str, user_id: str) -> Dict[str, Any]:
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

    return {
        "portfolio_id": portfolio_id,
        "portfolio_name": row.get("portfolio_name"),
        "portfolio_category": row.get("portfolio_category") or "growth",
        "valuation_as_of": d_now,
        "portfolio_value_latest": round(v_now, 2),
        "initial_weights": {k: round(v, 6) for k, v in initial_w.items()},
        "current_weights": {k: round(v, 6) for k, v in current_w.items()},
        "drift_by_ticker": drift,
        "rebalance_watchlist": rebalance_watch,
        "prescriptive_trades_to_initial_weights": trades,
        "rebalance_cashflow_check_usd": {
            "approx_total_sell_notional": round(max(total_sell_usd, 0), 2),
            "approx_total_buy_notional": round(max(total_buy_usd, 0), 2),
        },
        "change_1d": _portfolio_drivers(hist, latest_hist_point, 1),
        "change_7d": _portfolio_drivers(hist, latest_hist_point, 7),
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
) -> Dict[str, Any]:
    out: Dict[str, Any] = {"page": page, "user_id": user_id, "portfolios": []}
    if page == "portfolio":
        if not portfolio_id:
            out["note"] = "No portfolio selected on this page."
            return out
        out["portfolios"].append(compute_portfolio_facts(portfolio_id, user_id))
        return out
    if page == "net_worth":
        out["net_worth"] = _net_worth_summary(user_id)
        ids = [str(x).strip() for x in (portfolio_ids or []) if str(x).strip()]
        for pid in ids:
            prow = db_module.get_portfolio(pid)
            if not prow or prow.get("user_id") != user_id:
                continue
            out["portfolios"].append(compute_portfolio_facts(pid, user_id))
        if not out["portfolios"] and not out.get("net_worth", {}).get("error"):
            out["note"] = "Link saved portfolios on the net worth sheet to analyze their drift here."
        return out
    if page == "life_plan":
        out["net_worth"] = _net_worth_summary(user_id)
        if growth_portfolio_id:
            out["portfolios"].append(compute_portfolio_facts(growth_portfolio_id, user_id))
        if retirement_portfolio_id:
            out["portfolios"].append(compute_portfolio_facts(retirement_portfolio_id, user_id))
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
- If they ask about rebalancing, trades back to targets, drift vs initial weights, or what to buy/sell: use drift / rebalance_watchlist / prescriptive_trades as needed. Do NOT add a separate 7-day or 1-day performance section unless they asked about recent performance too.
- If they ask one narrow fact (e.g. “how did it do last 7 days?”), keep the answer short (about 2–6 sentences). No follow-on topics (“you might also…”, “additionally consider rebalancing…”) unless they asked.

FACTS reference (do not re-derive thresholds; only cite when relevant to the question):
- Current weight per ticker: latest marks vs portfolio value vs saved initial weights.
- rebalance_watchlist: (initial_weight > 20% AND |Δweight| > 5pp) OR (initial_weight < 20% AND |Δweight|/initial_weight > 25%).
- prescriptive_trades_to_initial_weights: share deltas to move toward initial targets; rebalance_cashflow_check_usd has approximate sell/buy notionals.
- change_1d / change_7d: portfolio value change over that span and top_ticker_moves.

Rules:
- Ground every number you state in the FACTS JSON. Do not invent prices, weights, or history.
- If the asked window has insufficient history, say so briefly for that window only; do not pivot to unrelated topics.
- When (and only when) the user asked for trades / rebalance: mirror prescriptive_trades_to_initial_weights, label as estimates, wording like “You would execute the following estimated trades…”, e.g. “SELL an estimated 5.25 shares MSFT; BUY an estimated 0.15 shares VOO”.
- When (and only when) you explain rebalancing logic in that trade context, state it is informed by the “Bernstein 5-25 adaptive rule” watch thresholds in FACTS.
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
