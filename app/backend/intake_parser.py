"""Parse user intake freeform text into structured IntakeContext."""

from __future__ import annotations

import math
import re
import datetime
from typing import Any, Dict, List, Optional, Tuple

from backtesting.backtesting_service.types import IntakeContext


def parse_gap_years_from_notes(text: Optional[str]) -> List[int]:
    """Parse gap years (no contributions) from other_notes.
    Matches: 'gap year 2030', 'no contribution in 2030', 'no monthly contribution in year 2030', etc."""
    if not text or not isinstance(text, str):
        return []
    years: List[int] = []
    t = text.lower()
    # Match 4-digit years near gap/contribution keywords
    for m in re.finditer(
        r"(?:gap\s*year|no\s*(?:monthly\s*)?contribution(?:s)?|contribution\s*holiday)\s*(?:in\s*)?(?:year\s*)?(\d{4})\b",
        t,
        re.IGNORECASE,
    ):
        y = int(m.group(1))
        if 2020 <= y <= 2100 and y not in years:
            years.append(y)
    for m in re.finditer(r"\b(\d{4})\s*(?:is\s+)?(?:a\s+)?gap\s*year\b", t, re.IGNORECASE):
        y = int(m.group(1))
        if 2020 <= y <= 2100 and y not in years:
            years.append(y)
    return sorted(years)


def _parse_number(s: str) -> Optional[float]:
    """Parse strings like '1000', '1.5M', '500K', '10 K', '2.7M' into float."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip().upper().replace(",", "")
    s = re.sub(r"\s+", "", s)
    if not s:
        return None
    mult = 1.0
    if s.endswith("M") or s.endswith("MIL") or s.endswith("MILLION"):
        mult = 1e6
        s = re.sub(r"(M|MIL|MILLION)$", "", s)
    elif s.endswith("K") or s.endswith("THOUSAND"):
        mult = 1e3
        s = re.sub(r"(K|THOUSAND)$", "", s)
    elif s.endswith("B") or s.endswith("BN"):
        mult = 1e9
        s = re.sub(r"(B|BN)$", "", s)
    try:
        return float(s) * mult
    except ValueError:
        return None


_WORD_YEARS_SPENDING = {
    "a": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def parse_spending_to_expense_dicts(text: Optional[str]) -> List[Dict[str, Any]]:
    """
    Big / lumpy spending from free-form `spending` only (parity with frontend api.parseExpenses).
    Returns [{years, amount, label?}, …] for folding into Monte Carlo one-time cashflows (engine tuple list).
    """
    if not text or not isinstance(text, str):
        return []
    spending_text = text.strip()
    if not spending_text:
        return []
    expenses: List[Dict[str, Any]] = []

    def _push(years: int, amount: float, label: Optional[str] = None) -> None:
        if amount <= 0 or years < 0:
            return
        row: Dict[str, Any] = {"years": years, "amount": float(amount)}
        if label:
            lg = str(label).strip()
            if lg:
                row["label"] = lg
        expenses.append(row)

    re_purpose_years = re.compile(
        r"([\d,.]+(?:\s*[KMB])?)\s+in\s+([A-Za-z0-9][A-Za-z0-9\s,.'’_]{0,80}?)\s+in\s+(\d+)\s*years?",
        re.IGNORECASE,
    )
    for m in re_purpose_years.finditer(spending_text):
        raw_amt = m.group(1).strip().upper().replace(",", "")
        v = _parse_number(raw_amt)
        if v and v > 0:
            purpose = (m.group(2) or "").strip()
            _push(int(m.group(3)), v, purpose or None)

    re_purpose_word = re.compile(
        r"([\d,.]+(?:\s*[KMB])?)\s+in\s+([A-Za-z0-9][A-Za-z0-9\s,.'’_]{0,80}?)\s+in\s+"
        r"(a|one|two|three|four|five|six|seven|eight|nine|ten)\s*years?",
        re.IGNORECASE,
    )
    for m in re_purpose_word.finditer(spending_text):
        raw_amt = m.group(1).strip().upper().replace(",", "")
        v = _parse_number(raw_amt)
        wy = _WORD_YEARS_SPENDING.get(m.group(3).lower())
        if v and v > 0 and wy is not None:
            purpose = (m.group(2) or "").strip()
            _push(wy, v, purpose or None)

    re_years = re.compile(r"([\d,.]+(?:\s*[KMB])?)\s+in\s+(\d+)\s*years?", re.IGNORECASE)
    for m in re_years.finditer(spending_text):
        raw_amt = m.group(1).strip().upper().replace(",", "")
        v = _parse_number(raw_amt)
        if v and v > 0:
            _push(int(m.group(2)), v, None)

    re_word_year = re.compile(
        r"([\d,.]+(?:\s*[KMB])?)\s+in\s+"
        r"(a|one|two|three|four|five|six|seven|eight|nine|ten)\s*years?",
        re.IGNORECASE,
    )
    for m in re_word_year.finditer(spending_text):
        raw_amt = m.group(1).strip().upper().replace(",", "")
        v = _parse_number(raw_amt)
        wy = _WORD_YEARS_SPENDING.get(m.group(2).lower())
        if v and v > 0 and wy is not None:
            _push(wy, v, None)

    re_cal_year = re.compile(r"([\d,.]+(?:\s*[KMB])?)\s+in\s+(\d{4})\b", re.IGNORECASE)
    for m in re_cal_year.finditer(spending_text):
        raw_amt = m.group(1).strip().upper().replace(",", "")
        v = _parse_number(raw_amt)
        cy = int(m.group(2))
        if v and v > 0 and 2020 <= cy <= 2100:
            _push(cy, v, None)

    return expenses


def _amount_phrase_for_spending_line(amt: float) -> str:
    if amt >= 1_000_000:
        v = amt / 1_000_000
        s = f"{v:.4f}".rstrip("0").rstrip(".")
        return f"{s}M"
    if amt >= 1000:
        v = amt / 1000
        s = f"{v:.4f}".rstrip("0").rstrip(".")
        return f"{s}K"
    return str(int(round(amt)))


def expense_dicts_to_spending_line(items: Optional[List[Dict[str, Any]]]) -> str:
    """Serialize structured expense dicts (years, amount, optional label) into free-form `spending` text."""
    if not items:
        return ""
    parts: List[str] = []
    for e in items:
        if not isinstance(e, dict):
            continue
        try:
            amt = float(e.get("amount", e.get("value", 0)))
        except (TypeError, ValueError):
            continue
        if amt <= 0:
            continue
        try:
            y = int(float(e.get("years", e.get("years_from_start", 0))))
        except (TypeError, ValueError):
            continue
        if y < 0:
            continue
        label = str(e.get("label") or "").strip()
        lbl = f" for {label}" if label else ""
        if y >= 1000:
            parts.append(f"{_amount_phrase_for_spending_line(amt)} in {y}{lbl}")
        else:
            parts.append(f"{_amount_phrase_for_spending_line(amt)} in {y} years{lbl}")
    return ", ".join(parts)


def coalesce_intake_spending_only(d: Optional[Dict[str, Any]]) -> None:
    """Normalize ``upcoming_expenses`` in place: keep only dict rows, drop empty list.

    We intentionally do **not** copy structured one-time flows into ``spending``. That merge
    duplicated Monte Carlo inputs and set ``IntakeContext.spending``, which the growth
    "future projection" chart parses for vertical markers — so users with no big-spending
    text but legacy JSON in ``upcoming_expenses`` still saw bogus "Spending" labels.
    """
    if not d or not isinstance(d, dict):
        return
    ue = d.get("upcoming_expenses")
    if not isinstance(ue, list) or not ue:
        d.pop("upcoming_expenses", None)
        return
    cleaned: List[Dict[str, Any]] = []
    for e in ue:
        if isinstance(e, dict):
            cleaned.append(dict(e))
    if not cleaned:
        d.pop("upcoming_expenses", None)
        return
    d["upcoming_expenses"] = cleaned


def spending_field_declares_one_time_outflows(text: Optional[str]) -> bool:
    """True when ``spending`` should be parsed for Monte Carlo lumpy outflows (not placeholders / empty)."""
    if not text or not isinstance(text, str):
        return False
    s = text.strip()
    if not s:
        return False
    low = s.lower()
    if low in ("none", "not specified", "n/a", "na", "-"):
        return False
    if "no big spending expected" in low:
        return False
    if low.startswith("retirement status:"):
        return False
    # Full intake narrative was sometimes stored in `spending`; do not chart-parse it.
    if "big spending:" in low and "current investment value:" in low:
        return False
    if "monthly investment contributions:" in low and "big spending:" in low:
        return False
    return True


def sanitize_intake_dict_for_timeline_chart(d: Optional[Dict[str, Any]]) -> None:
    """Clear ``intake.spending`` in artifacts when it should not drive timeline markers (mutates dict)."""
    if not d or not isinstance(d, dict):
        return
    if "spending" not in d:
        return
    sp = d.get("spending")
    if sp is None:
        return
    s = str(sp).strip()
    if spending_field_declares_one_time_outflows(s):
        d["spending"] = s
    else:
        d["spending"] = None


def drop_positive_upcoming_expenses_without_spending_intent(d: Optional[Dict[str, Any]]) -> None:
    """Drop structured one-time **outflows** (amount > 0) if `spending` has no real big-spending intent.

    Preserves negative amounts (one-time **inflows** from growth/retirement what-if). Without this, stale
    ``upcoming_expenses`` in the DB still drove Monte Carlo withdrawals while the intake UI showed no spending.
    """
    if not d or not isinstance(d, dict):
        return
    if spending_field_declares_one_time_outflows(str(d.get("spending") or "")):
        return
    ue = d.get("upcoming_expenses")
    if not isinstance(ue, list) or not ue:
        return
    kept: List[Dict[str, Any]] = []
    for e in ue:
        if not isinstance(e, dict):
            continue
        try:
            a = float(e.get("amount", e.get("value", 0)))
        except (TypeError, ValueError):
            continue
        if a < 0:
            kept.append(dict(e))
    if kept:
        d["upcoming_expenses"] = kept
    else:
        d.pop("upcoming_expenses", None)


def _completed_age_now(birth_year: int, birth_month: int) -> int:
    now = datetime.datetime.now()
    age = now.year - birth_year
    if (now.month, now.day) < (birth_month, 1):
        age -= 1
    return age


def parse_retirement_income_freeform(
    text: Optional[str],
    birth_year: Optional[int],
    birth_month: int = 6,
) -> List[Dict[str, Any]]:
    """
    Comma-separated entries like:
    $1500 per month start in 2028 and end in 2040, $1600 start in 5 years and end in 10 years
    -$1000 for the next 5 years, $1000 till 2030, $1000 till 65 years old, $ 1000 (space after $)
    Returns [{ monthly, start_age, end_age }, ...] for saved_portfolio_backtest merge.
    Calendar years map to age via (year - birth_year) when birth_year is set.
    "N years" is interpreted from today: age = current_age + N.
    Negative amounts (-$1000) = outflow. "till" / "until" = end; "till 65 years old" = end at age 65.
    If end is not specified for an entry, end_age defaults to 100.
    """
    if not text or not isinstance(text, str):
        return []
    raw = text.strip()
    if not raw:
        return []
    # Split before each new $-led or -$-led entry; keep single block if no comma pattern
    parts = re.split(r",\s*(?=-?\$)", raw)
    if len(parts) == 1 and "$" not in parts[0]:
        return []

    current_age: Optional[int] = None
    if birth_year is not None:
        current_age = _completed_age_now(birth_year, birth_month)

    rows: List[Dict[str, Any]] = []
    for seg in parts:
        seg = seg.strip()
        if not seg:
            continue
        # Match optional minus, $, optional space, amount, optional suffix (-$1000, $ 1000, $-500)
        amt_m = re.search(
            r"(?:(−|-)\s*)?\$\s*([\d,.]+(?:\.\d+)?)\s*(k|m|thousand|million|b|bn)?",
            seg,
            re.IGNORECASE,
        )
        if not amt_m:
            continue
        is_negative = bool(amt_m.group(1))
        num = amt_m.group(2).strip()
        suf = (amt_m.group(3) or "").upper()
        if suf in ("K", "THOUSAND"):
            num = num + "K"
        elif suf in ("M", "MILLION"):
            num = num + "M"
        elif suf in ("B", "BN"):
            num = num + "B"
        monthly = _parse_number(num)
        if monthly is None or monthly == 0:
            continue
        if is_negative:
            monthly = -float(monthly)

        sm_cal = re.search(
            r"(?:start(?:ing)?|begin(?:ning)?)\s+(?:in|at)\s+(\d{4})\b",
            seg,
            re.IGNORECASE,
        )
        em_cal = re.search(
            r"(?:end(?:ing)?|till|until)\s+(?:in|at|the)?\s*(\d{4})\b",
            seg,
            re.IGNORECASE,
        )
        sm_y = re.search(
            r"(?:start(?:ing)?|begin(?:ning)?)\s+(?:in|at)\s+(\d+)\s*years?\b",
            seg,
            re.IGNORECASE,
        )
        em_y = re.search(
            r"(?:end(?:ing)?|till|until)\s+(?:in|at)\s+(\d+)\s*years?\b",
            seg,
            re.IGNORECASE,
        )
        dur_m = re.search(
            r"(?:for|over)\s+(?:the\s+)?(?:next\s+)?(\d+)\s*years?\b",
            seg,
            re.IGNORECASE,
        )
        sm_at_age = re.search(r"start\s+(?:at|in)\s+age\s+(\d+)", seg, re.IGNORECASE)
        em_at_age = re.search(r"end\s+(?:at|in)\s+age\s+(\d+)", seg, re.IGNORECASE)
        em_till_years_old = re.search(
            r"(?:till|until)\s+(\d+)\s+years?\s+old", seg, re.IGNORECASE
        )

        sa = 0
        ea = 0
        duration_years = int(dur_m.group(1)) if dur_m else None

        if sm_cal and birth_year is not None:
            sy = int(sm_cal.group(1))
            sa = max(0, sy - birth_year)
        elif sm_y and current_age is not None:
            sa = max(0, current_age + int(sm_y.group(1)))
        elif sm_at_age:
            sa = max(0, int(sm_at_age.group(1)))

        if em_cal and birth_year is not None:
            ey = int(em_cal.group(1))
            ea = max(0, ey - birth_year)
        elif em_y and current_age is not None:
            ea = max(0, current_age + int(em_y.group(1)))
        elif em_at_age:
            ea = max(0, int(em_at_age.group(1)))
        elif em_till_years_old:
            ea = max(0, int(em_till_years_old.group(1)))
        elif duration_years is not None:
            if sa > 0:
                ea = sa + duration_years - 1  # "5 years" = 5 ages, not 6
            elif current_age is not None:
                sa = max(0, current_age)
                ea = sa + duration_years - 1

        if ea == 0:
            ea = 100

        rows.append({"monthly": float(monthly), "start_age": sa, "end_age": ea})

    return rows


def monthly_recurring_total_at_age(
    rows: Optional[List[Dict[str, Any]]],
    age: int,
    current_age: Optional[int] = None,
) -> float:
    """
    Sum of monthly amounts from rows active at completed age `age` (inclusive).
    - start_age / end_age: inclusive age window; end_age defaults to 100 when omitted.
    - When current_age is provided and start_age < current_age: clip to current age so we do not
      count income for past years. E.g. user 47 with income 42–100 → apply from 47–100 only.
    """
    if not rows or age < 0:
        return 0.0
    total = 0.0
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
        if ea <= 0:
            ea = 100
        if current_age is not None and sa < current_age:
            sa = current_age
        if age < sa:
            continue
        if age > ea:
            continue
        total += monthly
    return float(total)


def parse_yoy_annual_rate_from_row_field(raw: Any) -> float:
    """
    Decimal rate per calendar year of row activity, e.g. 0.03 for 3%.
    ``yoy_annual_pct`` uses percent scale (3 => 3%) matching other intake percent fields.
    """
    if raw is None:
        return 0.0
    if isinstance(raw, str):
        t = raw.strip().replace("%", "").replace(",", "")
        if not t or t in ("-", "+"):
            return 0.0
        try:
            v = float(t)
        except (TypeError, ValueError):
            return 0.0
    else:
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return 0.0
    if not math.isfinite(v):
        return 0.0
    r = v / 100.0
    return max(-0.95, min(10.0, r))


def monthly_recurring_total_at_age_with_yoy(
    rows: Optional[List[Dict[str, Any]]],
    age: int,
    current_age: Optional[int] = None,
) -> float:
    """
    Like ``monthly_recurring_total_at_age``, but each row's base monthly amount compounds by
    (1 + r) for each completed year since the row's effective start age (after clipping).

    Row fields: same as base helper, plus optional ``yoy_annual_pct`` (see ``parse_yoy_annual_rate_from_row_field``).
    """
    if not rows or age < 0:
        return 0.0
    total = 0.0
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        try:
            monthly = float(raw.get("monthly", 0) or 0)
        except (TypeError, ValueError):
            monthly = 0.0
        if monthly <= 0:
            continue
        try:
            sa = int(float(raw.get("start_age", 0) or 0))
        except (TypeError, ValueError):
            sa = 0
        try:
            ea = int(float(raw.get("end_age", 0) or 0))
        except (TypeError, ValueError):
            ea = 0
        if ea <= 0:
            ea = 100
        if current_age is not None and sa < current_age:
            sa = current_age
        if age < sa:
            continue
        if age > ea:
            continue
        r = parse_yoy_annual_rate_from_row_field(
            raw.get("yoy_annual_pct", raw.get("yoy_pct"))
        )
        years_since = max(0, int(age) - int(sa))
        total += float(monthly) * ((1.0 + r) ** years_since)
    return float(total)


def parse_intake_from_text(text: str) -> IntakeContext:
    """
    Parse freeform intake text into IntakeContext (chat-style cues only).
    One-time expenses / windfalls are not parsed from text; they use structured form/API fields (`spending` etc.).
    Handles patterns like:
    - "investment value: 2700000" or "Current investment value: 2.7M"
    - "monthly contribution: 10000" or "save 10K per month"
    - "retirement timeline: 10 years"
    """
    if not text or not isinstance(text, str):
        return IntakeContext()

    t = text.upper()
    initial_value = 1.0
    display_unit: Optional[str] = None  # "K", "M", or None for output formatting
    monthly_savings = 0.0
    horizon_years: Optional[int] = None

    # Investment value (order matters: most specific first)
    # Use (?:\s*[KMB])? to capture "800 K", "400 K", "1M", "2.7M" etc. (K=1000, M=1e6) -> dollars
    _num = r"[\d,.]+(?:\s*[KMB])?"
    for pat in [
        r"CURRENT\s*INVESTMENT\s*VALUE[:\s]+(" + _num + r")",
        r"INVESTMENT\s*VALUE[:\s]+(" + _num + r")",
        r"CURRENT\s*INVESTMENT[:\s]+(" + _num + r")",
        r"INVESTMENT[:\s]+(" + _num + r")",
        r"VALUE[:\s]+(" + _num + r")\s*(?:USD|DOLLAR)?",
    ]:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            raw = m.group(1).strip().upper()
            v = _parse_number(raw)
            if v and v > 0:
                initial_value = v
                if "K" in raw or "THOUSAND" in raw:
                    display_unit = "K"
                elif "M" in raw or "MIL" in raw or "MILLION" in raw:
                    display_unit = "M"
                break

    # Monthly savings / contribution
    for pat in [
        r"MONTHLY\s*(?:CONTRIBUTION|CONTRIBUTIONS|SAVING|INVESTMENT|ADD)\s+(?:OF\s+)?([\d,.]+(?:\s*[KMB])?)",
        r"ADD\s+(?:A\s+)?(?:MONTHLY\s+)?CONTRIBUTION\s+OF\s+([\d,.]+(?:\s*[KMB])?)",
        r"SAVE\s+([\d,.]+(?:\s*[KMB])?)\s*(?:PER|/)\s*MONTH",
        r"ADD\s+([\d,.]+(?:\s*[KMB])?)\s*(?:PER|/)\s*MONTH",
        r"CONTRIBUTION(?:S)?[:\s]+([\d,.]+(?:\s*[KMB])?)",
        r"([\d,.]+(?:\s*[KMB])?)\s*(?:PER|/)\s*MONTH",
    ]:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            v = _parse_number(m.group(1))
            if v and v >= 0:
                monthly_savings = v
                break

    upcoming_expenses: List[Tuple[float, ...]] = []

    # Horizon / retirement years
    for pat in [
        r"RETIREMENT\s*TIMELINE[:\s]+(\d+)\s*YEARS?",
        r"TIMELINE[:\s]+(\d+)\s*YEARS?",
        r"HORIZON[:\s]+(\d+)\s*YEARS?",
        r"(\d+)\s*YEARS?\s*(?:TIMELINE|HORIZON|TO\s*RETIREMENT)",
    ]:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            horizon_years = int(m.group(1))
            break

    # Current monthly expense (used as retirement monthly withdrawal target, inflation-adjusted)
    current_monthly_expense = 0.0
    for pat in [
        r"CURRENT\s*MONTHLY\s*EXPENSE[:\s]+(" + _num + r")",
        r"MONTHLY\s*EXPENSE[:\s]+(" + _num + r")",
        r"EXPENSE[:\s]+(" + _num + r")\s*(?:PER|/)\s*MONTH",
    ]:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            v = _parse_number(m.group(1))
            if v and v >= 0:
                current_monthly_expense = v
                break

    return IntakeContext(
        initial_value=initial_value,
        monthly_savings=monthly_savings,
        upcoming_expenses=upcoming_expenses,
        horizon_years=horizon_years,
        current_monthly_expense=current_monthly_expense,
        display_unit=display_unit,
    )
