from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd


def _parse_timeline_years_to_retirement(text: Optional[str]) -> Optional[int]:
    """Parse retirement_timeline_self/partner ('10 years', '2036') into whole years from now."""
    if not text or not isinstance(text, str):
        return None
    t = text.strip()
    m = re.search(r"(\d+)\s*years?", t, re.IGNORECASE)
    if m:
        y = int(m.group(1))
        return y if 1 <= y <= 80 else None
    m = re.search(r"(\d{4})\b", t)
    if m:
        target_year = int(m.group(1))
        now_year = datetime.datetime.now().year
        delta = target_year - now_year
        return delta if 1 <= delta <= 80 else None
    if t.isdigit():
        v = int(t)
        if 1 <= v <= 80:
            return v
        if 2020 <= v <= 2100:
            return v - datetime.datetime.now().year
    return None


def retirement_expense_inflation_years(
    planning_for: str,
    retirement_status: Optional[str],
    horizon_years: Optional[int],
    retirement_timeline_self: Optional[str],
    retirement_timeline_partner: Optional[str],
) -> int:
    """Years n for monthly_expense * (1+inflation)**n to match spending at retirement start.

    Uses the later of the two spouses' years-to-retirement for couples (0 if that person is
    already retired). Aligns with user age + max(user retires in, partner retires in) for inflation.
    """
    rs = (retirement_status or "").strip()
    pf = (planning_for or "self").strip()
    if rs == "both_retired":
        return 0

    self_y = _parse_timeline_years_to_retirement(retirement_timeline_self)
    partner_y = _parse_timeline_years_to_retirement(retirement_timeline_partner)
    fallback = int(horizon_years) if horizon_years is not None else 25

    if pf != "couple":
        if rs == "self_retired":
            return 0
        if self_y is not None:
            return int(self_y)
        return fallback

    if rs == "both_working":
        if self_y is not None and partner_y is not None:
            return max(int(self_y), int(partner_y))
        if self_y is not None:
            return int(self_y)
        if partner_y is not None:
            return int(partner_y)
        return fallback

    if rs == "self_retired":
        py = partner_y if partner_y is not None else horizon_years
        return max(0, int(py) if py is not None else fallback)

    if rs == "partner_retired":
        sy = self_y if self_y is not None else horizon_years
        return max(0, int(sy) if sy is not None else fallback)

    return fallback


@dataclass
class IntakeContext:
    """User intake data for backtesting and Monte Carlo. All amounts in dollars for calculations."""

    initial_value: float = 1.0
    monthly_savings: float = 0.0
    display_unit: Optional[str] = None  # "K", "M", or None (dollars) for output formatting
    # (years_from_start or calendar year if >= 1000, amount); optional str label for UI markers
    upcoming_expenses: Optional[List[Tuple[float, ...]]] = None
    spending: Optional[str] = None
    """User-entered big-spending free-form text only (not synthesized from ``upcoming_expenses``). Used for timeline chart markers."""
    gap_years: Optional[List[int]] = None  # calendar years with no contributions (e.g. [2030] for gap year)
    horizon_years: Optional[int] = None
    longevity_years: Optional[int] = None  # years from current calendar year to planning end year (computed from birth)
    current_monthly_expense: float = 0.0  # used to infer retirement target
    retirement_monthly_target: float = 0.0  # current expense inflated to retirement (see retirement_expense_inflation_years)
    planning_for: str = "self"  # "self" or "couple"
    birth_dates: Optional[List[Tuple[int, int]]] = None  # [(year, month), ...]; longevity inferred to user age 100 (first entry)
    inflation_rate: float = 0.03  # annual inflation (e.g. 0.03 = 3%); used for contribution growth
    retirement_status: Optional[str] = None  # self_retired | partner_retired | both_retired | both_working
    retirement_timeline_self: Optional[str] = None  # free-form; parsed with partner's for expense inflation
    retirement_timeline_partner: Optional[str] = None
    # Free-form retirement what-if (optional); used for YoY table extras when both are set
    retirement_income_freeform: Optional[str] = None
    retirement_misc_spending_freeform: Optional[str] = None
    # Structured rows: [{monthly, start_age, end_age}]; preferred over freeform when present
    retirement_income_rows: Optional[List[Dict[str, Any]]] = None
    retirement_misc_spending_rows: Optional[List[Dict[str, Any]]] = None
    # Decimal e.g. 0.15 = 15%; base monthly withdrawal = retirement_monthly_target * (1 + rate). Default 0 = no gross-up.
    retirement_effective_tax_rate: float = 0.0
    # Optional: extra monthly spend each retirement year (after the first) when prior year's total return
    # (price+yield) >= ``retirement_discretionary_min_prior_year_return_pct``. Optional
    # ``retirement_discretionary_start_age`` / ``end_age`` (calendar age, both set) limit the rule to that window.
    # Legacy saves may set ``retirement_discretionary_in_year`` to pin the rule to a single year only.
    retirement_discretionary_monthly: Optional[float] = None
    retirement_discretionary_in_year: Optional[int] = None
    retirement_discretionary_min_prior_year_return_pct: Optional[float] = None
    retirement_discretionary_start_age: Optional[int] = None
    retirement_discretionary_end_age: Optional[int] = None
    # Optional portfolio modeling window (calendar age). Growth: MC horizon = end − start (needs DOB).
    growth_portfolio_start_age: Optional[int] = None
    growth_portfolio_end_age: Optional[int] = None
    # Retirement decumulation: MC years = end − start (defaults: start = retirement age, end = 100).
    retirement_portfolio_start_age: Optional[int] = None
    retirement_portfolio_end_age: Optional[int] = None
    # Growth what-if: parsed segments from growth_monthly_income_freeform (age windows → extra monthly invest)
    growth_monthly_income_rows: Optional[List[Dict[str, Any]]] = None
    growth_monthly_income_freeform: Optional[str] = None
    growth_one_time_inflow_freeform: Optional[str] = None
    # Growth what-if: extra recurring monthly outflows (age windows), same shape as retirement_misc_spending_rows
    growth_misc_spending_rows: Optional[List[Dict[str, Any]]] = None
    growth_misc_spending_freeform: Optional[str] = None

    def __post_init__(self) -> None:
        if self.upcoming_expenses is None:
            object.__setattr__(self, "upcoming_expenses", [])
        _rs = (self.retirement_status or "").strip()
        both_retired = _rs == "both_retired"
        # Infer retirement target: inflate today's spending to the later household retirement date
        if self.retirement_monthly_target <= 0 and self.current_monthly_expense > 0:
            infl_y = retirement_expense_inflation_years(
                self.planning_for,
                self.retirement_status,
                self.horizon_years,
                self.retirement_timeline_self,
                self.retirement_timeline_partner,
            )
            inferred = self.current_monthly_expense * ((1 + self.inflation_rate) ** infl_y)
            object.__setattr__(self, "retirement_monthly_target", inferred)
        # User (first DOB): years remaining to end of planning at age 100; hard cap 100 years (bad/future DOB).
        if self.longevity_years is None and self.birth_dates:
            now = datetime.datetime.now()
            by, bm = self.birth_dates[0]
            user_age = now.year - int(by)
            if (now.month, now.day) < (int(bm), 1):
                user_age -= 1
            span = max(0, 100 - user_age)
            longevity = min(span, 100)
            object.__setattr__(self, "longevity_years", longevity)
        elif self.longevity_years is None and self.horizon_years is not None and not both_retired and not self.birth_dates:
            object.__setattr__(self, "longevity_years", 30)  # default when no DOB


@dataclass(frozen=True)
class RebalancingRule:
    rule_type: str  # "none", "monthly", "threshold"
    threshold: Optional[float] = None
    check_frequency: str = "monthly"


@dataclass(frozen=True)
class BacktestConfig:
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    frequency: str = "daily"  # "daily" or "monthly"
    rebalancing_rule: RebalancingRule = RebalancingRule("monthly")
    transaction_cost_bps: float = 0.0
    initial_value: float = 1.0
    # Annual nominal risk-free rate (decimal), e.g. 0.036 = 3.6%. Used for Sharpe/Sortino excess returns.
    risk_free_rate: float = 0.036
    intake_context: Optional["IntakeContext"] = None


@dataclass(frozen=True)
class BacktestResult:
    timeseries: pd.DataFrame
    metrics: Dict[str, float]
    weights_history: pd.DataFrame
    rebalancing_events: pd.DataFrame


@dataclass(frozen=True)
class MonteCarloConfig:
    years: int = 30
    n_sims: int = 1000
    frequency: str = "monthly"
    method: str = "bootstrap"  # "bootstrap"
    block_size: int = 12
    seed: Optional[int] = None
    blowup_threshold: float = 0.0
    intake_context: Optional["IntakeContext"] = None

