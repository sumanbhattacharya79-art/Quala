"""
Saved-portfolio backtest for the FastAPI / UI flow (no CrewAI agents).

Retirement what-if fields are merged into core intake here before IntakeContext
and Monte Carlo — see _merge_retirement_what_if_intake_dict and
_merge_windfall_into_upcoming_expenses (structured windfall_inflow_rows + scalar windfall_years/amount fallback).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from backtesting.backtesting_service.types import IntakeContext, retirement_expense_inflation_years
from backtesting.driver import resolve_load_ticker

from backend.alphavantage_sector_bridge import get_preferred_portfolio_sector_weights
from backend.db import get_portfolio
from backend.intake_parser import (
    _parse_number,
    drop_positive_upcoming_expenses_without_spending_intent,
    expense_dicts_to_spending_line,
    parse_retirement_income_freeform,
    parse_spending_to_expense_dicts,
    parse_yoy_annual_rate_from_row_field,
    spending_field_declares_one_time_outflows,
)

from backend.crewai_app.crew_framework import (
    DATA_OUTPUT_DIR,
    INTAKE_CONTEXT_STORE,
    RunBacktestTool,
    _BACKTEST_SESSION_ID,
    _INTAKE_STORE_LOCK,
    _fetch_ticker_data,
    _run_retirement_backtest_and_store,
    pop_stored_backtest_artifacts,
    set_intake_context,
)

logger = logging.getLogger(__name__)


def _optional_intake_age(v: Any) -> Optional[int]:
    if v is None or (isinstance(v, str) and not str(v).strip()):
        return None
    try:
        x = int(float(v))
        return x if 0 <= x <= 120 else None
    except (TypeError, ValueError):
        return None


def _apply_portfolio_age_window_from_raw(intake_ctx: IntakeContext, raw: Dict[str, Any]) -> None:
    for key in (
        "growth_portfolio_start_age",
        "growth_portfolio_end_age",
        "retirement_portfolio_start_age",
        "retirement_portfolio_end_age",
    ):
        if key not in raw:
            continue
        val = raw.get(key)
        if val is None or (isinstance(val, str) and not str(val).strip()):
            object.__setattr__(intake_ctx, key, None)
            continue
        parsed = _optional_intake_age(val)
        object.__setattr__(intake_ctx, key, parsed)


def _age_completed_years_from_dob(birth_year: int, birth_month: int, today) -> int:
    """Completed age in years; birthday assumed on the 1st of birth_month."""
    age = today.year - birth_year
    if (today.month, today.day) < (birth_month, 1):
        age -= 1
    return age


def _current_age_from_intake_dict(i: dict) -> Optional[int]:
    """User's current age from birth_dates; None if not available."""
    import datetime

    now = datetime.datetime.now()
    by, bm = _primary_birth_year_month(i)
    if by is None:
        return None
    return _age_completed_years_from_dob(by, bm, now)


def _estimate_retirement_age_max_years_from_intake_dict(i: dict) -> Tuple[Optional[int], int]:
    """Age at simulation year 0 and MC max_years = 100 - that age (user ages retirement_age..100)."""
    import datetime

    now = datetime.datetime.now()
    birth_dates = None
    if i.get("birth_dates"):
        bd = i["birth_dates"]
        if isinstance(bd, list):
            birth_dates = []
            for b in bd:
                if isinstance(b, dict) and "year" in b:
                    birth_dates.append((int(b["year"]), int(b.get("month", 6))))
                elif isinstance(b, (list, tuple)) and len(b) >= 1:
                    birth_dates.append((int(b[0]), int(b[1]) if len(b) > 1 else 6))
    rs = str(i.get("retirement_status") or "").strip() or ""
    both_retired = rs == "both_retired"
    horizon = i.get("horizon_years")
    if both_retired:
        horizon = 0
    longevity = i.get("longevity_years")
    if birth_dates and longevity is None:
        bd0 = birth_dates[0]
        birth_year, birth_month = bd0[0], (bd0[1] if len(bd0) > 1 else 6)
        user_age_now = _age_completed_years_from_dob(birth_year, birth_month, now)
        longevity = min(max(0, 100 - user_age_now), 100)
    max_years = int(longevity) if longevity and longevity > 0 else 50
    retirement_age = None
    if birth_dates and horizon is not None:
        bd0 = birth_dates[0]
        birth_year, birth_month = bd0[0], (bd0[1] if len(bd0) > 1 else 6)
        user_age_now = _age_completed_years_from_dob(birth_year, birth_month, now)
        retirement_age = user_age_now + int(horizon)
    rpa = i.get("retirement_portfolio_start_age")
    rpe = i.get("retirement_portfolio_end_age")
    if retirement_age is not None and (rpa is not None or rpe is not None):
        try:
            ra = int(retirement_age)
            sa = int(float(rpa)) if rpa is not None else ra
            ea = int(float(rpe)) if rpe is not None else 100
            sa = max(0, min(sa, 120))
            ea = max(sa, min(ea, 120))
            max_years = max(1, min(100, ea - sa + 1))
            retirement_age = sa
        except (TypeError, ValueError):
            pass
    elif retirement_age is not None:
        span = max(0, 100 - int(retirement_age))
        max_years = max(1, min(100, span))
    else:
        max_years = min(max_years, 100)
        if max_years < 1:
            max_years = 1
    return retirement_age, max_years


def _primary_birth_year_month(out: dict) -> Tuple[Optional[int], int]:
    bd = out.get("birth_dates")
    if isinstance(bd, list) and bd:
        b0 = bd[0]
        if isinstance(b0, dict) and "year" in b0:
            return int(b0["year"]), int(b0.get("month", 6))
        if isinstance(b0, (list, tuple)) and len(b0) >= 1:
            return int(b0[0]), int(b0[1]) if len(b0) > 1 else 6
    return None, 6


def _effective_med_monthly_for_backtest(
    med: float,
    med_until_age: int,
    retirement_age: Optional[int],
    max_years: int,
) -> float:
    if med <= 0:
        return 0.0
    if retirement_age is None or med_until_age <= 0:
        return med
    yrs = max(0, min(max_years, med_until_age - retirement_age))
    return float(med) * (yrs / max(max_years, 1))


def _effective_ss_monthly_for_backtest(
    ss: float,
    ss_start_age: int,
    retirement_age: Optional[int],
    max_years: int,
) -> float:
    if ss <= 0:
        return 0.0
    if retirement_age is None or ss_start_age <= 0:
        return float(ss)
    y_delay = max(0, ss_start_age - retirement_age)
    if y_delay >= max_years:
        return 0.0
    yrs_with_ss = max_years - y_delay
    return float(ss) * (yrs_with_ss / max(max_years, 1))


def _effective_recurring_income_monthly_for_backtest(
    monthly: float,
    start_age: int,
    end_age: int,
    retirement_age: Optional[int],
    max_years: int,
    current_age: Optional[int] = None,
    yoy_rate: float = 0.0,
) -> float:
    """
    Effective average monthly income over the retirement horizon window (same basis as before YoY:
    sum of monthly amounts attributed to each simulation year / max_years). With yoy_rate, each
    active year uses monthly * (1 + yoy_rate) ** (age - sa_clipped).
    """
    if monthly <= 0:
        return 0.0
    if retirement_age is None or max_years <= 0:
        return float(monthly)
    ra = int(retirement_age)
    sa = int(start_age) if start_age > 0 else ra
    ea = int(end_age) if end_age > 0 else 100
    if current_age is not None and sa < current_age:
        sa = current_age
    if ea < sa:
        return 0.0
    total = 0.0
    for y in range(max_years):
        age = ra + y
        if age < sa:
            continue
        if age > ea:
            continue
        years_since = max(0, int(age) - int(sa))
        total += float(monthly) * ((1.0 + yoy_rate) ** years_since)
    return float(total) / max(max_years, 1)


def _income_offset_from_retirement_rows(
    rows: Any,
    retirement_age: Optional[int],
    max_years: int,
    current_age: Optional[int] = None,
) -> float:
    """Sum effective monthly offsets from structured retirement_income_rows."""
    total = 0.0
    if not isinstance(rows, list):
        return total
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        try:
            monthly = float(raw.get("monthly", 0) or 0)
        except (TypeError, ValueError):
            monthly = 0.0
        try:
            sa = int(float(raw.get("start_age", 0) or 0))
        except (TypeError, ValueError):
            sa = 0
        try:
            ea = int(float(raw.get("end_age", 0) or 0))
        except (TypeError, ValueError):
            ea = 0
        if monthly <= 0:
            continue
        yoy_r = parse_yoy_annual_rate_from_row_field(
            raw.get("yoy_annual_pct", raw.get("yoy_pct"))
        )
        total += _effective_recurring_income_monthly_for_backtest(
            monthly, sa, ea, retirement_age, max_years, current_age=current_age, yoy_rate=yoy_r
        )
    return total


def _inflow_rows_to_expense_items(rows: object) -> list[Tuple[float, float, Optional[str]]]:
    """Structured one-time inflow rows {years, amount, label?} → (y, amount, label)."""
    if not isinstance(rows, list):
        return []
    out: list[Tuple[float, float, Optional[str]]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        try:
            y = float(r.get("years", 0))
            a = float(r.get("amount", 0))
        except (TypeError, ValueError):
            continue
        if a <= 0 or y < 0:
            continue
        lg = str(r.get("label") or "").strip() or None
        out.append((y, a, lg))
    return out


def _merge_windfall_into_upcoming_expenses(out: dict) -> None:
    """
    One-time inflows from structured `windfall_inflow_rows` only (years, amount, optional label).
    Legacy scalar pair: windfall_years + windfall_amount (no text parsing).

    MC/engine: positive upcoming_expenses = outflow, negative = inflow.
    Free-form keys `windfall_spending` are ignored (client sends structured rows).
    """
    ue = out.get("upcoming_expenses")
    if not isinstance(ue, list):
        ue = []
    else:
        ue = [dict(e) if isinstance(e, dict) else e for e in ue]

    added = False

    def append_inflow(years_f: float, amount_pos: float, label: Optional[str] = None) -> None:
        nonlocal added
        if amount_pos <= 0:
            return
        d: Dict[str, Any] = {"years": float(years_f), "amount": -abs(float(amount_pos))}
        if label:
            d["label"] = label
        ue.append(d)
        added = True

    structured = out.pop("windfall_inflow_rows", None)
    for y, a, lbl in _inflow_rows_to_expense_items(structured):
        append_inflow(y, a, lbl)

    out.pop("windfall_spending", None)

    wy_raw = out.pop("windfall_years", None)
    wa_raw = out.pop("windfall_amount", None)
    try:
        wy = float(wy_raw) if wy_raw is not None and str(wy_raw).strip() != "" else None
    except (TypeError, ValueError):
        wy = None
    try:
        wa = float(wa_raw) if wa_raw is not None and str(wa_raw).strip() != "" else None
    except (TypeError, ValueError):
        wa = None
    if wy is not None and wa is not None and wa > 0 and wy >= 0:
        append_inflow(float(wy), float(wa), None)

    if added:
        out["upcoming_expenses"] = ue


def _merge_growth_one_time_inflow_into_upcoming_expenses(out: dict) -> None:
    """
    Growth one-time inflows from structured `growth_one_time_inflow_rows` only.
    MC: negative amount = cash in. `growth_one_time_inflow_freeform` is ignored.
    """
    ue = out.get("upcoming_expenses")
    if not isinstance(ue, list):
        ue = []
    else:
        ue = [dict(e) if isinstance(e, dict) else e for e in ue]

    added = False

    def append_inflow(years_f: float, amount_pos: float, label: Optional[str] = None) -> None:
        nonlocal added
        if amount_pos <= 0:
            return
        d: Dict[str, Any] = {"years": float(years_f), "amount": -abs(float(amount_pos))}
        if label:
            d["label"] = label
        ue.append(d)
        added = True

    structured = out.pop("growth_one_time_inflow_rows", None)
    for y, a, lbl in _inflow_rows_to_expense_items(structured):
        append_inflow(y, a, lbl)

    out.pop("growth_one_time_inflow_freeform", None)

    if added:
        out["upcoming_expenses"] = ue


def _ensure_upcoming_expenses_from_spending(out: Dict[str, Any]) -> None:
    """If `upcoming_expenses` is empty, parse one-time items from `spending` (canonical client field)."""
    ue = out.get("upcoming_expenses")
    if isinstance(ue, list) and len(ue) > 0:
        return
    sp = str(out.get("spending") or "").strip()
    if not spending_field_declares_one_time_outflows(sp):
        return
    parsed = parse_spending_to_expense_dicts(sp)
    if parsed:
        out["upcoming_expenses"] = parsed


def _apply_big_spending_rows_to_intake_dict(out: Dict[str, Any]) -> None:
    """Mirror frontend `expensesFromBigSpendingRows` + `bigSpendingNarrativeFromRows` for MC.

    Without this, `drop_positive_upcoming_expenses_without_spending_intent` can strip structured
    `upcoming_expenses` when the client sends only structured rows and `spending` is null.
    """
    rows = out.get("big_spending_rows")
    if not isinstance(rows, list) or not rows:
        return
    expense_dicts: List[Dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        amt_raw = str(r.get("amount") or "").strip()
        years_raw = str(r.get("years") or "").strip()
        if not amt_raw or not years_raw:
            continue
        amt_clean = re.sub(r"[$€£¥,]", "", amt_raw)
        amt_clean = re.sub(r"\s+", "", amt_clean.upper())
        v = _parse_number(amt_clean)
        if v is None or v <= 0:
            continue
        years: Optional[int] = None
        if re.fullmatch(r"\d{4}", years_raw):
            cy = int(years_raw)
            if 2020 <= cy <= 2100:
                years = cy
        if years is None:
            try:
                yf = float(years_raw)
            except (TypeError, ValueError):
                continue
            yf = int(yf)
            if yf < 0 or yf > 150:
                continue
            years = yf
        row_d: Dict[str, Any] = {"years": years, "amount": float(v)}
        lbl = str(r.get("label") or "").strip()
        if lbl:
            row_d["label"] = lbl
        expense_dicts.append(row_d)
    if not expense_dicts:
        return
    out["upcoming_expenses"] = expense_dicts
    sp = str(out.get("spending") or "").strip()
    if not spending_field_declares_one_time_outflows(sp):
        line = expense_dicts_to_spending_line(expense_dicts) or None
        if line:
            out["spending"] = line


def _merge_growth_what_if_intake_dict(i: Optional[dict]) -> Tuple[dict, list, list]:
    """
    Growth portfolio what-if: extra monthly investable income, misc monthly spending
    (structured or free-form age windows), and one-time inflows. Prefers structured
    rows over freeform when present. Returns (dict for IntakeContext, income rows, misc rows).
    """
    if not i:
        return {}, [], []
    out = dict(i)
    _apply_big_spending_rows_to_intake_dict(out)
    drop_positive_upcoming_expenses_without_spending_intent(out)
    _ensure_upcoming_expenses_from_spending(out)
    growth_rows: list = []
    structured = out.get("growth_monthly_income_rows")
    has_structured = isinstance(structured, list) and any(
        isinstance(r, dict) and float(r.get("monthly", 0) or 0) > 0 for r in structured
    )
    if has_structured:
        for r in structured:
            if not isinstance(r, dict):
                continue
            try:
                monthly = float(r.get("monthly", 0) or 0)
            except (TypeError, ValueError):
                monthly = 0.0
            if monthly <= 0:
                continue
            sa = r.get("start_age", r.get("startAge", 0))
            ea = r.get("end_age", r.get("endAge", 0))
            try:
                sa_int = int(float(sa or 0))
            except (TypeError, ValueError):
                sa_int = 0
            try:
                ea_int = int(float(ea or 0))
            except (TypeError, ValueError):
                ea_int = 0
            row_d: Dict[str, Any] = {"monthly": monthly, "start_age": sa_int, "end_age": ea_int if ea_int > 0 else 100}
            _lg = str(r.get("label") or "").strip()
            if _lg:
                row_d["label"] = _lg
            growth_rows.append(row_d)
    else:
        ff = str(out.get("growth_monthly_income_freeform") or "").strip()
        if ff:
            by, bm = _primary_birth_year_month(out)
            growth_rows = parse_retirement_income_freeform(ff, by, bm)

    growth_misc_rows: list = []
    misc_structured = out.get("growth_misc_spending_rows")
    has_misc_structured = isinstance(misc_structured, list) and any(
        isinstance(r, dict) and float(r.get("monthly", 0) or 0) > 0 for r in misc_structured
    )
    if has_misc_structured:
        for r in misc_structured:
            if not isinstance(r, dict):
                continue
            try:
                monthly = float(r.get("monthly", 0) or 0)
            except (TypeError, ValueError):
                monthly = 0.0
            if monthly <= 0:
                continue
            sa = r.get("start_age", r.get("startAge", 0))
            ea = r.get("end_age", r.get("endAge", 0))
            try:
                sa_int = int(float(sa or 0))
            except (TypeError, ValueError):
                sa_int = 0
            try:
                ea_int = int(float(ea or 0))
            except (TypeError, ValueError):
                ea_int = 0
            mrow: Dict[str, Any] = {"monthly": monthly, "start_age": sa_int, "end_age": ea_int if ea_int > 0 else 100}
            _lgm = str(r.get("label") or "").strip()
            if _lgm:
                mrow["label"] = _lgm
            growth_misc_rows.append(mrow)
    else:
        gmff = str(out.get("growth_misc_spending_freeform") or "").strip()
        if gmff:
            by, bm = _primary_birth_year_month(out)
            growth_misc_rows = parse_retirement_income_freeform(gmff, by, bm)

    out.pop("growth_monthly_income_rows", None)
    out.pop("growth_monthly_income_freeform", None)
    out.pop("growth_misc_spending_rows", None)
    out.pop("growth_misc_spending_freeform", None)
    _merge_growth_one_time_inflow_into_upcoming_expenses(out)
    out.pop("big_spending_rows", None)
    return out, growth_rows, growth_misc_rows


def _merge_retirement_what_if_intake_dict(i: Optional[dict]) -> Tuple[dict, float]:
    """
    Fold retirement what-if into core intake fields for the engine.
    Returns (dict for IntakeContext, total_income_offset) for retirement_monthly_target
    (structured retirement_income_rows plus legacy SS/other fields).
    """
    if not i:
        return {}, 0.0
    out = dict(i)
    _apply_big_spending_rows_to_intake_dict(out)
    drop_positive_upcoming_expenses_without_spending_intent(out)
    _ensure_upcoming_expenses_from_spending(out)
    base = float(out.get("current_monthly_expense", 0) or 0)
    has_reloc = bool(str(out.get("relocate_city") or "").strip() or str(out.get("relocate_country") or "").strip())
    col_age = int(out.get("cost_of_living_adjust_age", 0) or 0)
    has_expat = has_reloc or col_age > 0
    col_factor = 0.85 if has_expat else 1.0
    living = base * col_factor
    ret_age, max_y = _estimate_retirement_age_max_years_from_intake_dict(out)
    current_age = _current_age_from_intake_dict(out)
    misc_rows = out.get("retirement_misc_spending_rows")
    has_misc_structured = isinstance(misc_rows, list) and any(
        isinstance(r, dict) and float(r.get("monthly", 0) or 0) > 0 for r in misc_rows
    )
    mff = str(out.get("retirement_misc_spending_freeform") or "").strip()
    parsed_misc: list = []
    if mff:
        by, bm = _primary_birth_year_month(out)
        parsed_misc = parse_retirement_income_freeform(mff, by, bm)
    # Misc spending is NOT folded into current_monthly_expense here. It is passed as
    # retirement_misc_spending_rows and applied per-year in MC and table (correct age windows).
    if has_misc_structured or parsed_misc:
        eff_misc = 0.0
    else:
        med = float(out.get("medical_insurance_monthly", 0) or 0)
        med_until = int(out.get("medical_insurance_until_age", 0) or 0)
        eff_misc = _effective_med_monthly_for_backtest(med, med_until, ret_age, max_y)
    out["current_monthly_expense"] = living + eff_misc

    rows = out.get("retirement_income_rows")
    has_structured = isinstance(rows, list) and any(
        isinstance(r, dict) and float(r.get("monthly", 0) or 0) > 0 for r in rows
    )
    ff = str(out.get("retirement_income_freeform") or "").strip()
    parsed_income: list = []
    if ff:
        by, bm = _primary_birth_year_month(out)
        parsed_income = parse_retirement_income_freeform(ff, by, bm)
    if has_structured:
        income_offset = _income_offset_from_retirement_rows(
            rows, ret_age, max_y, current_age=current_age
        )
    elif parsed_income:
        income_offset = _income_offset_from_retirement_rows(
            parsed_income, ret_age, max_y, current_age=current_age
        )
    else:
        ss = float(out.get("social_security_monthly", 0) or 0)
        ss_age = int(out.get("social_security_start_age", 0) or 0)
        other_raw = max(0.0, float(out.get("other_monthly_income", 0) or 0))
        other_start_age = int(out.get("other_monthly_income_start_age", 0) or 0)
        income_offset = _effective_ss_monthly_for_backtest(ss, ss_age, ret_age, max_y) + _effective_ss_monthly_for_backtest(
            other_raw, other_start_age, ret_age, max_y
        )

    _merge_windfall_into_upcoming_expenses(out)
    for k in (
        "social_security_monthly",
        "social_security_start_age",
        "medical_insurance_monthly",
        "medical_insurance_until_age",
        "relocate_city",
        "relocate_country",
        "cost_of_living_adjust_age",
        "other_monthly_income",
        "other_monthly_income_start_age",
        "retirement_income_rows",
        "retirement_income_freeform",
        "retirement_misc_spending_freeform",
        "retirement_misc_spending_rows",
    ):
        out.pop(k, None)
    out.pop("big_spending_rows", None)
    return out, income_offset


def _build_intake_context(i: dict) -> Optional[IntakeContext]:
    if not i:
        return None
    birth_dates = None
    if i.get("birth_dates"):
        bd = i["birth_dates"]
        if isinstance(bd, list):
            birth_dates = []
            for b in bd:
                if isinstance(b, dict) and "year" in b:
                    birth_dates.append((int(b["year"]), int(b.get("month", 6))))
                elif isinstance(b, (list, tuple)) and len(b) >= 1:
                    birth_dates.append((int(b[0]), int(b[1]) if len(b) > 1 else 6))
    expenses = []
    if i.get("upcoming_expenses"):
        ex = i["upcoming_expenses"]
        if isinstance(ex, list):
            for e in ex:
                if not isinstance(e, dict):
                    continue
                y = int(e.get("years", e.get("years_from_start", 0)))
                a = float(e.get("amount", e.get("value", 0)))
                if y < 0 or a <= 0:
                    continue
                lg = str(e.get("label") or "").strip()
                if lg:
                    expenses.append((y, a, lg))
                else:
                    expenses.append((y, a))
    _rs = str(i.get("retirement_status") or "").strip() or None
    _rts = i.get("retirement_timeline_self")
    _rtp = i.get("retirement_timeline_partner")
    _hz_in = i.get("horizon_years")
    if _hz_in is not None:
        try:
            _hz_out: Optional[int] = int(_hz_in)
        except (TypeError, ValueError):
            _hz_out = retirement_expense_inflation_years(
                planning_for=str(i.get("planning_for", "self") or "self"),
                retirement_status=_rs,
                horizon_years=None,
                retirement_timeline_self=_rts,
                retirement_timeline_partner=_rtp,
            )
    else:
        _hz_out = retirement_expense_inflation_years(
            planning_for=str(i.get("planning_for", "self") or "self"),
            retirement_status=_rs,
            horizon_years=None,
            retirement_timeline_self=_rts,
            retirement_timeline_partner=_rtp,
        )
    _sp = str(i.get("spending") or "").strip() or None
    _tax_raw = i.get("retirement_effective_tax_rate")
    if _tax_raw is None:
        _tax_dec = 0.0
    else:
        try:
            _tf = float(_tax_raw)
        except (TypeError, ValueError):
            _tf = 20.0
        _tax_dec = (_tf / 100.0) if _tf > 1.0 else _tf
    _tax_dec = max(0.0, min(0.70, _tax_dec))
    return IntakeContext(
        initial_value=float(i.get("initial_value", 1.0) or 1.0),
        monthly_savings=float(i.get("monthly_savings", 0) or 0),
        horizon_years=_hz_out,
        longevity_years=int(i["longevity_years"]) if i.get("longevity_years") is not None else None,
        current_monthly_expense=float(i.get("current_monthly_expense", 0) or 0),
        planning_for=str(i.get("planning_for", "self") or "self"),
        birth_dates=birth_dates,
        inflation_rate=(float(v) if (v := i.get("inflation_assumption")) is not None else 3.0) / 100.0,
        upcoming_expenses=expenses or [],
        spending=_sp,
        retirement_status=_rs,
        retirement_timeline_self=(str(_rts).strip() or None) if _rts else None,
        retirement_timeline_partner=(str(_rtp).strip() or None) if _rtp else None,
        retirement_effective_tax_rate=_tax_dec,
    )


def intake_context_from_user_intake_dict(
    user_intake: Optional[Dict[str, Any]],
    is_retirement: bool,
) -> Optional[IntakeContext]:
    """Build IntakeContext from API/DB user intake (with what-if merges)."""
    from backend.intake_parser import coalesce_intake_spending_only

    raw_intake = dict(user_intake or {})
    coalesce_intake_spending_only(raw_intake)
    if is_retirement:
        merged_intake, _income_offset = _merge_retirement_what_if_intake_dict(dict(raw_intake))
        intake_ctx = _build_intake_context(merged_intake)
        if intake_ctx is not None:
            intake_ctx.retirement_income_freeform = str(raw_intake.get("retirement_income_freeform") or "").strip() or None
            intake_ctx.retirement_misc_spending_freeform = (
                str(raw_intake.get("retirement_misc_spending_freeform") or "").strip() or None
            )
            ri_rows = raw_intake.get("retirement_income_rows")
            rm_rows = raw_intake.get("retirement_misc_spending_rows")
            if isinstance(ri_rows, list) and any(
                isinstance(r, dict) and float(r.get("monthly", 0) or 0) > 0 for r in ri_rows
            ):
                intake_ctx.retirement_income_rows = ri_rows
            if isinstance(rm_rows, list) and any(
                isinstance(r, dict) and float(r.get("monthly", 0) or 0) > 0 for r in rm_rows
            ):
                intake_ctx.retirement_misc_spending_rows = rm_rows
            _ter = raw_intake.get("retirement_effective_tax_rate")
            if _ter is not None:
                try:
                    _tf = float(_ter)
                    _td = (_tf / 100.0) if _tf > 1.0 else _tf
                    object.__setattr__(intake_ctx, "retirement_effective_tax_rate", max(0.0, min(0.70, _td)))
                except (TypeError, ValueError):
                    pass
            _dm = raw_intake.get("retirement_discretionary_monthly")
            _dy = raw_intake.get("retirement_discretionary_in_year")
            _dp = raw_intake.get("retirement_discretionary_min_prior_year_return_pct")
            _has_dm = _dm is not None and str(_dm).strip() != ""
            _has_dp = _dp is not None and str(_dp).strip() != ""
            _has_dy = _dy is not None and str(_dy).strip() != ""
            if _has_dm and _has_dp:
                try:
                    object.__setattr__(
                        intake_ctx,
                        "retirement_discretionary_monthly",
                        float(_dm),
                    )
                    object.__setattr__(
                        intake_ctx,
                        "retirement_discretionary_min_prior_year_return_pct",
                        float(_dp),
                    )
                    if _has_dy:
                        object.__setattr__(
                            intake_ctx,
                            "retirement_discretionary_in_year",
                            int(float(_dy)),
                        )
                    else:
                        object.__setattr__(intake_ctx, "retirement_discretionary_in_year", None)
                    _ds0 = raw_intake.get("retirement_discretionary_start_age")
                    _de0 = raw_intake.get("retirement_discretionary_end_age")
                    if _ds0 is not None and str(_ds0).strip() != "" and _de0 is not None and str(_de0).strip() != "":
                        try:
                            object.__setattr__(
                                intake_ctx,
                                "retirement_discretionary_start_age",
                                int(float(_ds0)),
                            )
                            object.__setattr__(
                                intake_ctx,
                                "retirement_discretionary_end_age",
                                int(float(_de0)),
                            )
                        except (TypeError, ValueError):
                            object.__setattr__(intake_ctx, "retirement_discretionary_start_age", None)
                            object.__setattr__(intake_ctx, "retirement_discretionary_end_age", None)
                    else:
                        object.__setattr__(intake_ctx, "retirement_discretionary_start_age", None)
                        object.__setattr__(intake_ctx, "retirement_discretionary_end_age", None)
                except (TypeError, ValueError):
                    pass
            _apply_portfolio_age_window_from_raw(intake_ctx, raw_intake)
        return intake_ctx
    merged_growth, growth_income_rows, growth_misc_rows = _merge_growth_what_if_intake_dict(dict(raw_intake))
    intake_ctx = _build_intake_context(merged_growth)
    if intake_ctx is not None:
        # Always set (including empty) so cleared what-if rows replace prior session state, not stale lists.
        intake_ctx.growth_monthly_income_rows = growth_income_rows or None
        intake_ctx.growth_misc_spending_rows = growth_misc_rows or None
    if intake_ctx is not None:
        intake_ctx.growth_monthly_income_freeform = (
            str(raw_intake.get("growth_monthly_income_freeform") or "").strip() or None
        )
        intake_ctx.growth_misc_spending_freeform = (
            str(raw_intake.get("growth_misc_spending_freeform") or "").strip() or None
        )
        intake_ctx.growth_one_time_inflow_freeform = (
            str(raw_intake.get("growth_one_time_inflow_freeform") or "").strip() or None
        )
        _apply_portfolio_age_window_from_raw(intake_ctx, raw_intake)
    return intake_ctx


def run_backtest_for_saved_portfolio(
    portfolio_id: str,
    portfolio_weights: Dict[str, float],
    user_intake: Optional[Dict[str, Any]] = None,
    is_retirement: bool = False,
    portfolio_sector_weights: Optional[Dict[str, float]] = None,
    portfolio_industry_weights: Optional[Dict[str, float]] = None,
    *,
    use_portfolio_mark_for_initial: bool = True,
) -> Optional[Dict[str, Any]]:
    """Run backtest for a saved portfolio. Returns artifacts for frontend (Ana/Emu style).

    Sector/industry weights are optional UI breakdowns (same as live Quala/Panda flow); tickers drive returns.
    """
    merged_intake: Dict[str, Any] = dict(user_intake or {})
    if not is_retirement and use_portfolio_mark_for_initial:
        prow = get_portfolio(portfolio_id)
        if prow:
            raw_pv = prow.get("portfolio_value")
            try:
                pv = float(raw_pv) if raw_pv is not None else None
            except (TypeError, ValueError):
                pv = None
            if pv is not None and pv > 0:
                merged_intake["initial_value"] = pv
    intake_ctx = intake_context_from_user_intake_dict(merged_intake, is_retirement)
    preferred_sector_weights = get_preferred_portfolio_sector_weights(portfolio_weights)
    if preferred_sector_weights:
        portfolio_industry_weights = preferred_sector_weights
    try:
        if is_retirement:
            _run_retirement_backtest_and_store(
                portfolio_id,
                portfolio_weights,
                intake_ctx,
                portfolio_sector_weights,
                portfolio_industry_weights,
            )
        else:
            for t in portfolio_weights:
                load_ticker = resolve_load_ticker(t)
                path = DATA_OUTPUT_DIR / f"{load_ticker.lower()}_monthly.csv"
                if not path.exists():
                    _fetch_ticker_data(load_ticker)
            if not (DATA_OUTPUT_DIR / "spy_monthly.csv").exists():
                _fetch_ticker_data("SPY")
            if intake_ctx is not None:
                set_intake_context(portfolio_id, intake_ctx)
            else:
                with _INTAKE_STORE_LOCK:
                    INTAKE_CONTEXT_STORE.pop(portfolio_id, None)
            acc_body: Dict[str, Any] = {"tickers": dict(portfolio_weights)}
            if portfolio_sector_weights:
                acc_body["sectors"] = dict(portfolio_sector_weights)
            if portfolio_industry_weights:
                acc_body["industries"] = dict(portfolio_industry_weights)
            _BACKTEST_SESSION_ID.session_id = portfolio_id
            try:
                tool = RunBacktestTool()
                result = tool._run(json.dumps({"accumulation": acc_body}))
                if "Missing price data" in str(result):
                    logger.warning("Growth backtest failed: %s", result)
                    return None
            finally:
                _BACKTEST_SESSION_ID.session_id = None
                with _INTAKE_STORE_LOCK:
                    INTAKE_CONTEXT_STORE.pop(portfolio_id, None)
        return pop_stored_backtest_artifacts(portfolio_id)
    except Exception as e:
        logger.warning("run_backtest_for_saved_portfolio failed: %s", e)
        return None
