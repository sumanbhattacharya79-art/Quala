from __future__ import annotations

from pathlib import Path
import os
import io
import json
import logging
import re
import sys
import uuid
from typing import Any, Dict, List, Literal, Optional, Tuple

_log = logging.getLogger(__name__)

import numpy as np
import pandas as pd
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Header, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
APP_DIR = PROJECT_ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from backtesting.backtesting_service import (  # noqa: E402
    backtest_portfolio,
    monte_carlo_questions,
    simulate_monte_carlo,
)
from backtesting.backtesting_service.leveraged_etf import (  # noqa: E402
    LEVERAGED_ETF_UNDERLYING,
    build_prices_for_leveraged_portfolio,
    get_mc_returns_for_leveraged_portfolio,
    pad_prices_to_start_year,
)
from backtesting.backtesting_service.types import (  # noqa: E402
    BacktestConfig,
    IntakeContext,
    MonteCarloConfig,
    RebalancingRule,
)
from backtesting.driver import (  # noqa: E402
    infer_frequency_and_years,
    load_prices_from_data_output,
    load_single_price_series,
    load_sixty_forty_benchmark_prices,
)
from backend.intake_portfolio_builder import handle_intake_message  # noqa: E402
from backend.saved_portfolio_backtest import (  # noqa: E402
    _build_intake_context,
    _merge_growth_what_if_intake_dict,
)
from backend.crewai_app.crew_framework import (  # noqa: E402
    INTAKE_CONTEXT_STORE,
    run_analyze_upload_agent_pipeline,
    run_message,
    set_intake_context,
    SESSION_STORE,
)
from backend.sector_industry_taxonomy import (  # noqa: E402
    ASSET_CLASS_SECTORS,
    GICS_INDUSTRY_SECTORS,
    build_breakdown_ticker_lists,
    build_industry_ticker_lists_from_per_ticker_maps,
    normalize_asset_class_weights,
    normalize_gics_industry_weights,
    normalize_industry_ticker_rollup,
    portfolio_industry_weights_from_per_ticker_maps,
    rollup_weights_from_ticker_classification,
)
from backend.analyze_portfolio_parser import (  # noqa: E402
    build_holdings_from_row_dicts,
    dataframe_upload_preview,
    dedupe_holdings_sum_quantity,
    enrich_holdings_with_latest_close,
    normalize_portfolio_weights_significant_digits,
    omit_zero_current_amount_rows,
    omit_zero_quantity_holdings,
    read_analyze_portfolio_csv,
)
from backend.saved_portfolio_backtest import (  # noqa: E402
    intake_context_from_user_intake_dict,
    run_backtest_for_saved_portfolio,
)
from backend.alphavantage_sector_bridge import (  # noqa: E402
    get_preferred_portfolio_sector_weights,
    gics_sector_for_ticker_via_alphavantage_script,
    per_ticker_normalized_gics_maps_for_tickers,
    seed_ticker_classification_cache_from_alphavantage,
)
from backend.unused_codes.agentic import LLMClient  # noqa: E402
from backend.db_connection import close_db_pool  # noqa: E402
from backend.db import (  # noqa: E402
    append_net_worth_value_history_snapshot,
    build_net_worth_chart_series,
    count_portfolios_for_user,
    create_user,
    delete_analyzed_portfolio,
    delete_life_scenario_for_user,
    delete_saved_portfolio,
    delete_scenario,
    get_analyzed_portfolio_owner_user_id,
    get_life_scenario_for_user,
    get_portfolio,
    get_scenario,
    get_scenarios_by_user,
    get_user_intake,
    get_user_net_worth,
    init_db,
    list_life_scenarios_by_user,
    list_portfolios,
    save_analyzed_portfolio,
    save_life_scenario_bundle,
    update_life_scenario_frozen_growth_median,
    update_life_scenario_planner_intakes,
    save_portfolio,
    save_scenario,
    save_user_intake,
    update_portfolio_intake_snapshot,
    update_portfolio_ticker_weights,
    update_scenario,
    upsert_user_net_worth,
    verify_user,
)
from backend.portfolio_backtest_store import (  # noqa: E402
    copy_portfolio_backtest_to_scenario,
    get_backtest_snapshot,
    get_backtest_snapshot_meta,
    persist_backtest_snapshot,
)
from backend.saved_portfolio_intake import merged_intake_for_saved_portfolio  # noqa: E402
from backend.portfolio_jobs import (  # noqa: E402
    persist_life_planner_portfolio_backtests,
    refresh_all_saved_portfolio_backtests,
    refresh_all_saved_portfolio_values,
)
from backend.portfolio_valuation import (  # noqa: E402
    initialize_positions_for_portfolio,
    rebalance_positions_after_composition_change,
    refresh_all_valuations_for_user,
    refresh_portfolio_valuation,
)
from backend.data_output_gcs import (  # noqa: E402
    bootstrap_data_output_from_gcs_in_background,
    start_periodic_gcs_sync,
    stop_periodic_gcs_sync,
)

FRONTEND_DIR = PROJECT_ROOT / "app" / "frontend"
FRONTEND_DIST = FRONTEND_DIR / "dist"  # React build output (app1.jsx look + app.js logic)
DATA_OUTPUT_DIR = PROJECT_ROOT / "data_output"


class IntakeRequest(BaseModel):
    session_id: Optional[str] = None
    message: str = Field(..., min_length=1)
    intake: Optional[Dict[str, object]] = None  # optional form values for backtest context
    # UI choice: "growth" -> Quala, "retirement" -> Panda (welcome / logged-in option buttons)
    portfolio_flow: Optional[str] = None
    # Monotonic counter from UI; ignores stale in-flight requests after user switches flow
    flow_epoch: Optional[int] = None
    # Next chat turn after retirement post-backtest "Keep refining" — route to Panda for 3 portfolios
    retirement_refinement_after_emu: bool = False
    user_id: Optional[str] = None  # when logged in: attribute Gemini token usage to this user


class ChatResponse(BaseModel):
    session_id: str
    intent: str
    reply: str
    actions: list[dict] = Field(default_factory=list)
    artifacts: dict = Field(default_factory=dict)
    agent: Optional[str] = None  # "Quala" or "Ana" for display


class PortfolioRequest(BaseModel):
    session_id: str
    holdings: Dict[str, float]


class SavePortfolioRequest(BaseModel):
    """Save portfolio to SQLite (saved_portfolios table)."""
    user_id: str  # required: from auth (email+password -> user_id)
    session_id: str
    portfolio_name: str = Field(..., min_length=1, description="User-provided name for the portfolio")
    portfolio_value: Optional[float] = None  # initial/investment value when saved
    portfolio_ticker_weights: Dict[str, float]
    portfolio_sector_weights: Optional[Dict[str, float]] = None
    portfolio_industry_weights: Optional[Dict[str, float]] = None
    portfolio_category: Optional[str] = None  # "growth" (Quala) or "retirement" (Panda); inferred from session if omitted
    intake: Optional[Dict[str, object]] = None  # user intake to persist (when user has signed up)


QUALA_TNC_VERSION = "quala-clickwrap-v1"


class RegisterRequest(BaseModel):
    """Create account with email and password."""
    email_id: str = Field(..., min_length=1)
    password: str = Field(..., min_length=6)
    accept_terms: bool = False
    terms_version: Optional[str] = None


class LoginRequest(BaseModel):
    """Login with email and password."""
    email_id: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class BacktestRequest(BaseModel):
    session_id: str
    rebalancing_rule: str = "monthly"
    threshold: Optional[float] = None
    check_frequency: str = "monthly"
    transaction_cost_bps: float = 5.0
    benchmark_ticker: str = "SPY"  # ignored: API uses 60% SPY / 40% AGG blended benchmark
    mc_years: Optional[int] = None
    mc_sims: int = 500
    blowup_threshold: float = 0.0


class RebalanceRequest(BaseModel):
    session_id: str
    current_holdings: Dict[str, float]
    target_holdings: Optional[Dict[str, float]] = None


class SectorMapRequest(BaseModel):
    session_id: str
    tickers: list[str]


class IntentRequest(BaseModel):
    session_id: str
    intent: str


class IntakeDataRequest(BaseModel):
    """Structured intake data for backtesting (monthly savings, expenses)."""

    session_id: str
    initial_value: float = 1.0
    monthly_savings: float = 0.0
    horizon_years: Optional[int] = None
    planning_for: str = "self"
    birth_dates: list[dict] = Field(default_factory=list, description="List of {year, month}")
    current_monthly_expense: float = 0.0
    display_unit: Optional[str] = None  # "K", "M", or None for output formatting
    retirement_status: Optional[str] = None  # self_retired | partner_retired | both_retired | both_working
    retirement_timeline_self: Optional[str] = None
    retirement_timeline_partner: Optional[str] = None
    country: Optional[str] = None
    state: Optional[str] = None
    inflation_assumption: Optional[float] = None
    spending: Optional[str] = None  # Big / lumpy spending line(s); parsed server-side for Monte Carlo one-time outflows


app = FastAPI(title="Portfolio Optimizer")

# Browser clients on another origin (e.g. Vercel) need CORS. Comma-separated extra origins in CORS_ORIGINS.
_cors_extra = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
_cors_regex = os.environ.get("CORS_ORIGIN_REGEX")
if _cors_regex == "":
    _cors_regex = None
elif _cors_regex is None:
    _cors_regex = r"https://.*\.vercel\.app"
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        *_cors_extra,
    ],
    allow_origin_regex=_cors_regex,
    allow_methods=["*"],
    allow_headers=["*"],
)


class _ApiNoCacheMiddleware(BaseHTTPMiddleware):
    """Prevent browsers/CDNs from caching dynamic portfolio/backtest API responses."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        return response


app.add_middleware(_ApiNoCacheMiddleware)


def _static_dir() -> Path:
    """Serve from dist when React app is built; else from frontend root."""
    if FRONTEND_DIST.exists():
        return FRONTEND_DIST
    return FRONTEND_DIR


app.mount("/static", StaticFiles(directory=_static_dir()), name="static")




SESSIONS: Dict[str, Dict[str, object]] = {}


def _new_session_id() -> str:
    return uuid.uuid4().hex


def _get_session(session_id: str) -> Dict[str, object]:
    if session_id not in SESSIONS:
        SESSIONS[session_id] = {"session_id": session_id}
    return SESSIONS[session_id]


def _to_float_safe(x):
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, dict):
        return float(x.get("weight", x.get("value", x.get("amount", 0))))
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _normalize_holdings(holdings: Dict[str, float]) -> Dict[str, float]:
    cleaned: Dict[str, float] = {}
    for ticker, weight in holdings.items():
        if ticker is None:
            continue
        key = str(ticker).strip().upper()
        if not key:
            continue
        cleaned[key] = _to_float_safe(weight)

    if not cleaned:
        raise HTTPException(status_code=400, detail="Holdings are empty.")

    total = sum(cleaned.values())
    if total <= 0:
        raise HTTPException(status_code=400, detail="Holdings must sum to a positive value.")

    if total > 1.5:
        cleaned = {k: v / total for k, v in cleaned.items()}
    else:
        cleaned = {k: v / total for k, v in cleaned.items()}
    return cleaned


def _extract_holdings_from_df(df: pd.DataFrame) -> Dict[str, float]:
    lower_cols = {c.lower(): c for c in df.columns}
    ticker_col = None
    for name in ("ticker", "symbol", "asset"):
        if name in lower_cols:
            ticker_col = lower_cols[name]
            break
    if ticker_col is None:
        raise HTTPException(status_code=400, detail="CSV must include a ticker or symbol column.")

    weight_col = None
    for name in ("weight", "allocation", "percent", "pct"):
        if name in lower_cols:
            weight_col = lower_cols[name]
            break

    value_col = None
    for name in ("value", "market_value", "current_value", "amount"):
        if name in lower_cols:
            value_col = lower_cols[name]
            break

    if weight_col is None and value_col is None:
        raise HTTPException(
            status_code=400,
            detail="CSV must include a weight/allocation or value column.",
        )

    holdings = {}
    for _, row in df.iterrows():
        ticker = str(row[ticker_col]).strip().upper()
        if not ticker:
            continue
        if weight_col is not None:
            holdings[ticker] = float(row[weight_col])
        else:
            holdings[ticker] = float(row[value_col])
    return _normalize_holdings(holdings)


@app.get("/", response_class=FileResponse)
def root() -> FileResponse:
    # Serve React app when built; else vanilla index.html. No-cache so session ID always fresh on reload.
    if FRONTEND_DIST.exists():
        r = FileResponse(FRONTEND_DIST / "index-react.html")
        r.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return r
    r = FileResponse(FRONTEND_DIR / "index.html")
    r.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return r


@app.post("/api/chat/intake", response_model=ChatResponse)
def chat_intake(payload: IntakeRequest) -> ChatResponse:
    session_id = payload.session_id or _new_session_id()
    session = _get_session(session_id)
    result = handle_intake_message(session, payload.message)
    actions: list[dict] = []
    artifacts: dict = {}
    if result.proposed_portfolio:
        artifacts["proposed_portfolio"] = result.proposed_portfolio
    if result.accepted and result.proposed_portfolio:
        session["portfolio"] = result.proposed_portfolio
        artifacts["portfolio"] = result.proposed_portfolio
    return ChatResponse(
        session_id=session_id,
        intent="intake",
        reply=result.reply,
        actions=actions,
        artifacts=artifacts,
    )


@app.post("/api/chat/money-manager", response_model=ChatResponse)
def chat_money_manager(payload: IntakeRequest) -> ChatResponse:
    session_id = payload.session_id or _new_session_id()
    _get_session(session_id)
    if payload.intake:
        from backend.intake_parser import parse_gap_years_from_notes
        from backtesting.backtesting_service.types import IntakeContext

        inflation_pct = float(payload.intake.get("inflation_assumption", 3.0))
        other_notes = payload.intake.get("other_notes") or ""
        gap_years = parse_gap_years_from_notes(other_notes) or payload.intake.get("gap_years")
        _ri_ff = str(payload.intake.get("retirement_income_freeform") or "").strip()
        _rm_ff = str(payload.intake.get("retirement_misc_spending_freeform") or "").strip()
        mg, g_inc_rows, g_misc_rows = _merge_growth_what_if_intake_dict(dict(payload.intake))
        intake = _build_intake_context(mg) or IntakeContext()
        intake.display_unit = mg.get("display_unit") or None
        if gap_years:
            intake.gap_years = gap_years
        intake.retirement_income_freeform = _ri_ff or None
        intake.retirement_misc_spending_freeform = _rm_ff or None
        intake.inflation_rate = inflation_pct / 100.0
        ri_rows = payload.intake.get("retirement_income_rows")
        rm_rows = payload.intake.get("retirement_misc_spending_rows")
        if isinstance(ri_rows, list) and any(
            isinstance(r, dict) and float(r.get("monthly", 0) or 0) > 0 for r in ri_rows
        ):
            intake.retirement_income_rows = ri_rows
        if isinstance(rm_rows, list) and any(
            isinstance(r, dict) and float(r.get("monthly", 0) or 0) > 0 for r in rm_rows
        ):
            intake.retirement_misc_spending_rows = rm_rows
        if g_inc_rows:
            intake.growth_monthly_income_rows = g_inc_rows
        if g_misc_rows:
            intake.growth_misc_spending_rows = g_misc_rows
        intake.growth_monthly_income_freeform = (
            str(payload.intake.get("growth_monthly_income_freeform") or "").strip() or None
        )
        intake.growth_misc_spending_freeform = (
            str(payload.intake.get("growth_misc_spending_freeform") or "").strip() or None
        )
        intake.growth_one_time_inflow_freeform = (
            str(payload.intake.get("growth_one_time_inflow_freeform") or "").strip() or None
        )
        set_intake_context(session_id, intake)
        _log.info("chat_money_manager: synced intake from request initial_value=%s monthly_savings=%s horizon=%s", intake.initial_value, intake.monthly_savings, intake.horizon_years)
    try:
        flow = (payload.portfolio_flow or "").strip().lower()
        if flow not in ("growth", "retirement"):
            flow = None
        result = run_message(
            session_id,
            payload.message,
            intake_payload=payload.intake,
            portfolio_flow=flow,
            flow_epoch=payload.flow_epoch,
            retirement_refinement_after_emu=bool(payload.retirement_refinement_after_emu),
            user_id=(payload.user_id or "").strip() or None,
        )
        if result.get("stale"):
            return ChatResponse(
                session_id=session_id,
                intent="money_manager",
                reply="",
                actions=[],
                artifacts={},
                agent=None,
            )
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    reply = result["reply"]
    artifacts = result.get("artifacts", {})
    agent = result.get("agent")

    actions: list[dict] = []
    crew_session = SESSION_STORE.get(session_id)
    msg_lower = (payload.message or "").lower()
    wants_save = "save" in msg_lower and "portfolio" in msg_lower
    has_portfolio = bool(artifacts.get("portfolio_composition") or artifacts.get("scenarios"))
    _phases_portfolio_pick = (
        "portfolio_building",
        "choosing",
        "refining",
        "retirement_planning",
        "retirement_choosing",
        "retirement_refining",
    )
    if crew_session.phase in _phases_portfolio_pick:
        refine_advisor = (
            "Panda"
            if (getattr(crew_session, "portfolio_flow", None) or "").lower() == "retirement"
            or crew_session.phase
            in ("retirement_planning", "retirement_choosing", "retirement_refining")
            else "Quala"
        )
        actions.append({"type": "show_portfolio_choices", "refine_advisor": refine_advisor})
    elif crew_session.phase == "post_analysis" or (wants_save and has_portfolio):
        refine_advisor = (
            "Panda" if bool(getattr(crew_session, "chosen_retirement_composition", None)) else "Quala"
        )
        actions.append({"type": "show_post_backtest_choices", "refine_advisor": refine_advisor})

    return ChatResponse(
        session_id=session_id,
        intent="money_manager",
        reply=reply,
        actions=actions,
        artifacts=artifacts,
        agent=agent,
    )


@app.post("/api/portfolio")
def create_portfolio(payload: PortfolioRequest) -> Dict[str, object]:
    session = _get_session(payload.session_id)
    session["portfolio"] = _normalize_holdings(payload.holdings)
    return {"status": "ok", "portfolio": session["portfolio"]}


@app.on_event("startup")
def _init_saved_portfolios_db() -> None:
    """Initialize DB; pull market data from GCS in background when configured."""
    bootstrap_data_output_from_gcs_in_background()
    start_periodic_gcs_sync()
    init_db()


@app.on_event("shutdown")
def _close_postgres_pool() -> None:
    """Release Supabase / Postgres pool handles on process exit."""
    stop_periodic_gcs_sync()
    close_db_pool()


@app.post("/api/auth/register")
def register(payload: RegisterRequest) -> Dict[str, object]:
    """Create a new user account. Returns user_id."""
    if not payload.accept_terms or (payload.terms_version or "") != QUALA_TNC_VERSION:
        raise HTTPException(
            status_code=400,
            detail="You must scroll through and accept the Terms & Conditions to create an account.",
        )
    try:
        user_id = create_user(payload.email_id, payload.password)
        return {"status": "ok", "user_id": user_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/auth/login")
def login(payload: LoginRequest) -> Dict[str, object]:
    """Verify credentials. Returns user_id if valid."""
    user_id = verify_user(payload.email_id, payload.password)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return {"status": "ok", "user_id": user_id}


def _save_intake_from_payload(user_id: str, intake: Dict[str, object]) -> None:
    """Convert intake payload to db format and save to user_intake."""
    from backend.intake_parser import coalesce_intake_spending_only

    intake = dict(intake)
    prior = get_user_intake(user_id)
    coalesce_intake_spending_only(intake)
    if "upcoming_expenses" in intake:
        ue_to_save = intake["upcoming_expenses"]
        if not isinstance(ue_to_save, list):
            ue_to_save = None
    else:
        ue_to_save = prior.get("upcoming_expenses") if isinstance(prior, dict) else None
        if not isinstance(ue_to_save, list):
            ue_to_save = None

    def _float(x, default=0.0):
        if x is None:
            return default
        try:
            return float(x)
        except (TypeError, ValueError):
            return default

    def _int(x):
        if x is None:
            return None
        try:
            return int(x)
        except (TypeError, ValueError):
            return None

    birth_dates = intake.get("birth_dates")
    if isinstance(birth_dates, list):
        result = []
        for b in birth_dates:
            if isinstance(b, dict) and "year" in b:
                result.append({"year": int(b["year"]), "month": int(b.get("month", 6))})
            elif isinstance(b, (list, tuple)) and len(b) >= 1:
                result.append({"year": int(b[0]), "month": int(b[1]) if len(b) > 1 else 6})
        birth_dates = result if result else None
    else:
        birth_dates = None

    save_user_intake(
        user_id=user_id,
        initial_value=_float(intake.get("initial_value"), 1.0),
        monthly_savings=_float(intake.get("monthly_savings")),
        horizon_years=_int(intake.get("horizon_years")),
        planning_for=str(intake.get("planning_for", "self") or "self"),
        birth_dates=birth_dates,
        current_monthly_expense=_float(intake.get("current_monthly_expense")),
        upcoming_expenses=ue_to_save,
        display_unit=intake.get("display_unit"),
        retirement_status=intake.get("retirement_status"),
        retirement_timeline_self=intake.get("retirement_timeline_self"),
        retirement_timeline_partner=intake.get("retirement_timeline_partner"),
        country=intake.get("country"),
        state=intake.get("state"),
        inflation_assumption=_float(intake.get("inflation_assumption"), 3.0),
        risk=intake.get("risk"),
        spending=intake.get("spending"),
        other_notes=intake.get("other_notes"),
    )


@app.post("/api/portfolio/save")
def save_portfolio_to_db(payload: SavePortfolioRequest) -> Dict[str, object]:
    """Persist portfolio to saved_portfolios table (SQLite). Also saves user intake when provided."""
    # Infer category from session: Panda (retirement) vs Quala (growth)
    category = payload.portfolio_category
    if not category or category not in ("growth", "retirement"):
        crew_session = SESSION_STORE.get(payload.session_id)
        category = "retirement" if getattr(crew_session, "chosen_retirement_composition", None) else "growth"
    try:
        portfolio_id = save_portfolio(
            user_id=payload.user_id,
            session_id=payload.session_id,
            ticker_weights=payload.portfolio_ticker_weights,
            sector_weights=payload.portfolio_sector_weights,
            industry_weights=payload.portfolio_industry_weights,
            portfolio_name=payload.portfolio_name.strip(),
            portfolio_value=payload.portfolio_value,
            portfolio_category=category,
            intake_snapshot=payload.intake if isinstance(payload.intake, dict) else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if payload.intake and payload.user_id:
        _save_intake_from_payload(payload.user_id, payload.intake)
    try:
        initialize_positions_for_portfolio(portfolio_id)
    except Exception as exc:
        _log.warning("initialize_positions_for_portfolio: %s", exc)
    return {"status": "ok", "portfolio_id": portfolio_id}


class UpdatePortfolioIntakeRequest(BaseModel):
    user_id: str
    intake: Dict[str, object]


@app.put("/api/portfolio/saved/{portfolio_id}/intake")
def update_saved_portfolio_intake(portfolio_id: str, payload: UpdatePortfolioIntakeRequest) -> Dict[str, object]:
    """Update the intake snapshot attached to one saved portfolio (owner only)."""
    row = get_portfolio(portfolio_id)
    if not row:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if row["user_id"] != payload.user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    if not update_portfolio_intake_snapshot(portfolio_id, payload.user_id, dict(payload.intake)):
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return {"status": "ok", "portfolio_id": portfolio_id}


class SaveUserIntakeRequest(BaseModel):
    """Save or update user intake (profile)."""
    user_id: str
    intake: Dict[str, object]


@app.get("/api/user/intake")
def get_intake(user_id: str) -> Dict[str, object]:
    """Fetch user intake by user_id (for returning users to pre-fill form)."""
    intake = get_user_intake(user_id)
    if not intake:
        raise HTTPException(status_code=404, detail="No intake found for user")
    return intake


@app.post("/api/user/intake")
def save_user_intake_endpoint(payload: SaveUserIntakeRequest) -> Dict[str, object]:
    """Save or update user intake (profile)."""
    _save_intake_from_payload(payload.user_id, payload.intake)
    return {"status": "ok"}


class NetWorthLineIn(BaseModel):
    label: str = ""
    price: float = 0.0
    yoy_pct: float = 0.0


class NetWorthPutRequest(BaseModel):
    user_id: str
    assets: List[NetWorthLineIn] = Field(default_factory=list)
    debts: List[NetWorthLineIn] = Field(default_factory=list)
    linked_portfolio_ids: List[str] = Field(default_factory=list)
    linked_portfolio_yoy: Optional[Dict[str, float]] = None


class NetWorthHistoryItemIn(BaseModel):
    kind: Literal["asset", "debt"]
    name: str = ""
    value: float = 0.0
    portfolio_id: Optional[str] = None


class NetWorthHistorySnapshotRequest(BaseModel):
    user_id: str
    items: List[NetWorthHistoryItemIn] = Field(default_factory=list)


@app.get("/api/user/net-worth")
def get_user_net_worth_endpoint(user_id: str = Query(..., description="Account owner")) -> Dict[str, object]:
    """
    Load net worth data. Each asset/debt row includes DB fields:
    id, user_id (implicit), kind (asset list vs debt list), label (type/description),
    price (stored value), yoy_pct, portfolio_id (set for rows synced from linked saved portfolios),
    created_at, updated_at.
    """
    data = get_user_net_worth(user_id)
    if not data:
        return {"assets": [], "debts": [], "linked_portfolio_ids": []}
    assets = data.get("assets") if isinstance(data.get("assets"), list) else []
    debts = data.get("debts") if isinstance(data.get("debts"), list) else []
    linked = data.get("linked_portfolio_ids")
    if not isinstance(linked, list):
        linked = []
    linked = [str(x) for x in linked if x]
    return {
        "assets": assets,
        "debts": debts,
        "linked_portfolio_ids": linked,
    }


@app.put("/api/user/net-worth")
def put_user_net_worth_endpoint(payload: NetWorthPutRequest) -> Dict[str, object]:
    """Save net worth worksheet (non-investment assets, debts, linked saved portfolios)."""
    uid = payload.user_id
    linked_clean: List[str] = []
    for pid in payload.linked_portfolio_ids:
        pid_s = str(pid).strip()
        if not pid_s:
            continue
        row = get_portfolio(pid_s)
        if not row or row.get("user_id") != uid:
            # Stale or third-party id (e.g. portfolio deleted); omit silently — do not fail save or expose ids.
            continue
        linked_clean.append(pid_s)

    def _lines(items: List[NetWorthLineIn]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for it in items:
            label = (it.label or "").strip()
            try:
                price = float(it.price)
            except (TypeError, ValueError):
                price = 0.0
            try:
                yoy = float(it.yoy_pct)
            except (TypeError, ValueError):
                yoy = 0.0
            if price <= 0 and not label:
                continue
            out.append({"label": label or "—", "price": max(0.0, price), "yoy_pct": yoy})
        return out

    linked_yoy_clean: Dict[str, float] = {}
    raw_yoy = payload.linked_portfolio_yoy
    if isinstance(raw_yoy, dict):
        for k, v in raw_yoy.items():
            ks = str(k).strip()
            if not ks or ks not in linked_clean:
                continue
            try:
                linked_yoy_clean[ks] = float(v)
            except (TypeError, ValueError):
                linked_yoy_clean[ks] = 0.0

    doc = {
        "assets": _lines(payload.assets),
        "debts": _lines(payload.debts),
        "linked_portfolio_ids": linked_clean,
        "linked_portfolio_yoy": linked_yoy_clean,
    }
    upsert_user_net_worth(uid, doc)
    saved = get_user_net_worth(uid)
    if saved:
        return {"status": "ok", **saved}
    return {"status": "ok", "assets": [], "debts": [], "linked_portfolio_ids": linked_clean}


@app.post("/api/user/net-worth/history-snapshot")
def post_net_worth_history_snapshot(
    payload: NetWorthHistorySnapshotRequest,
) -> Dict[str, object]:
    """Append dated name/value rows for net worth lines (debounced edits while worksheet is open)."""
    uid = payload.user_id
    rows = [it.model_dump() for it in payload.items]
    n = append_net_worth_value_history_snapshot(uid, rows)
    return {"status": "ok", "logged": n}


@app.get("/api/user/net-worth/chart-series")
def get_net_worth_chart_series_endpoint(
    user_id: str = Query(..., description="Account owner"),
) -> Dict[str, object]:
    """Daily net worth and per-asset stacks for the net worth page chart (from value history + forward fill)."""
    return build_net_worth_chart_series(user_id)


class MrBrownHistoryTurn(BaseModel):
    role: str
    content: str


class MrBrownChatRequest(BaseModel):
    user_id: str
    page: Literal["portfolio", "net_worth", "life_plan"]
    message: str = ""
    portfolio_id: Optional[str] = None
    portfolio_ids: Optional[List[str]] = None
    growth_portfolio_id: Optional[str] = None
    retirement_portfolio_id: Optional[str] = None
    history: Optional[List[MrBrownHistoryTurn]] = None


@app.post("/api/mr-brown/chat")
def mr_brown_chat_endpoint(payload: MrBrownChatRequest) -> Dict[str, object]:
    """Mr Brown: drift / drivers / rebalance chat for portfolio, net worth, or life planner pages."""
    from backend.mr_brown_service import run_mr_brown_chat

    msg = (payload.message or "").strip()
    if not msg:
        raise HTTPException(status_code=400, detail="message is required")
    hist = [t.model_dump() for t in payload.history] if payload.history else None
    return run_mr_brown_chat(
        user_id=payload.user_id,
        page=payload.page,
        message=msg,
        portfolio_id=payload.portfolio_id,
        portfolio_ids=payload.portfolio_ids,
        growth_portfolio_id=payload.growth_portfolio_id,
        retirement_portfolio_id=payload.retirement_portfolio_id,
        history=hist,
    )


@app.get("/api/portfolio/saved")
def list_saved_portfolios(
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    limit: int = 100,
    refresh_valuations: bool = Query(False, description="If true and user_id set, recompute MTM for all saved portfolios"),
) -> Dict[str, object]:
    """List saved portfolios from the database (intake omitted; use GET .../saved/{id} for full intake)."""
    if refresh_valuations and user_id:
        try:
            refresh_all_valuations_for_user(user_id)
        except Exception as exc:
            _log.warning("refresh_all_valuations_for_user: %s", exc)
    rows = list_portfolios(user_id=user_id, session_id=session_id, limit=limit)
    lite = []
    for r in rows:
        d = dict(r)
        d.pop("intake", None)
        lite.append(d)
    return {"portfolios": lite}


@app.get("/api/portfolio/saved/{portfolio_id}")
def get_saved_portfolio(
    portfolio_id: str,
    include_backtest: bool = Query(
        True,
        description="Include persisted backtest/MC artifacts when available",
    ),
    scenario_id: Optional[str] = Query(
        None,
        description="When set, load backtest snapshot for this saved scenario (what-if); else portfolio-level",
    ),
    refresh_mtm: bool = Query(
        True,
        description="Refresh mark-to-market from latest daily CSVs",
    ),
) -> Dict[str, object]:
    """Fetch a single saved portfolio by id. Refreshes mark-to-market from data_output CSVs and returns valuation_history."""
    row = get_portfolio(portfolio_id)
    if not row:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if refresh_mtm:
        try:
            snap = refresh_portfolio_valuation(portfolio_id)
            row = get_portfolio(portfolio_id)
            if not row:
                raise HTTPException(status_code=404, detail="Portfolio not found")
            row["valuation_history"] = snap.get("valuation_history") or []
            row["valuation_as_of"] = snap.get("valuation_as_of")
        except Exception as exc:
            _log.warning("refresh_portfolio_valuation: %s", exc)
            row["valuation_history"] = []
            row["valuation_as_of"] = None
    else:
        from backend.db import get_portfolio_value_history

        row["valuation_history"] = get_portfolio_value_history(portfolio_id)
        row["valuation_as_of"] = None
    if include_backtest:
        sid = (scenario_id or "").strip() or None
        meta = get_backtest_snapshot_meta(portfolio_id, scenario_id=sid)
        art = get_backtest_snapshot(portfolio_id, scenario_id=sid)
        if art:
            row["backtest_artifacts"] = art
            row["backtest_load_source"] = "portfolio_backtest_snapshots"
            row["backtest_scenario_id"] = sid or ""
            if meta:
                row["backtest_persisted_at"] = meta.get("updated_at")
                row["backtest_run_kind"] = meta.get("run_kind")
            _log.info(
                "GET /api/portfolio/saved/%s backtest_source=persisted_db scenario_id=%s scenarios=%s",
                portfolio_id,
                sid or "",
                len(art.get("scenarios") or []) if isinstance(art.get("scenarios"), list) else 0,
            )
        else:
            row["backtest_load_source"] = "none"
            _log.info(
                "GET /api/portfolio/saved/%s backtest_source=none scenario_id=%s",
                portfolio_id,
                sid or "",
            )
    return row


@app.get("/api/portfolio/saved/{portfolio_id}/backtest-artifacts")
def get_saved_portfolio_backtest_artifacts(
    portfolio_id: str,
    user_id: str = Query(..., description="Owner user id"),
    scenario_id: Optional[str] = Query(None, description="Saved scenario id for what-if snapshot"),
) -> Dict[str, object]:
    """Return persisted backtest / Monte Carlo artifacts for chart replay without re-running."""
    row = get_portfolio(portfolio_id)
    if not row:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if row["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    sid = (scenario_id or "").strip() or None
    if sid:
        sc = get_scenario(sid)
        if not sc or sc.get("portfolio_id") != portfolio_id or sc.get("user_id") != user_id:
            raise HTTPException(status_code=404, detail="Scenario not found")
    artifacts = get_backtest_snapshot(portfolio_id, scenario_id=sid)
    if not artifacts:
        raise HTTPException(status_code=404, detail="No persisted backtest for this portfolio")
    is_retirement = (row.get("portfolio_category") or "growth") == "retirement"
    return {
        "portfolio_id": portfolio_id,
        "scenario_id": sid or "",
        "artifacts": artifacts,
        "agent": "Emu" if is_retirement else "Ana",
    }


class RefreshValuationsRequest(BaseModel):
    user_id: str


def _persist_saved_portfolio_backtest(
    portfolio_id: str,
    user_id: str,
    row: Dict[str, object],
    artifacts: Dict[str, object],
    user_intake: Dict[str, object],
    scenario_id: Optional[str] = None,
) -> None:
    try:
        is_retirement = (row.get("portfolio_category") or "growth") == "retirement"
        weights = row.get("portfolio_ticker_weights")
        persist_backtest_snapshot(
            portfolio_id,
            user_id,
            "retirement" if is_retirement else "growth",
            artifacts,
            intake_json=user_intake if isinstance(user_intake, dict) else None,
            portfolio_weights=weights if isinstance(weights, dict) else None,
            scenario_id=scenario_id,
        )
    except Exception as exc:
        _log.warning(
            "persist backtest snapshot %s scenario_id=%s: %s",
            portfolio_id,
            scenario_id or "",
            exc,
        )


@app.post("/api/portfolio/saved/refresh-valuations")
def refresh_saved_portfolio_valuations(payload: RefreshValuationsRequest) -> Dict[str, object]:
    """Recompute portfolio_value and history for every saved portfolio of this user (e.g. daily cron)."""
    n = refresh_all_valuations_for_user(payload.user_id)
    return {"status": "ok", "refreshed": n}


def _require_admin_job_key(x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key")) -> None:
    """When ADMIN_API_KEY is set, batch job endpoints require matching X-Admin-Key header."""
    expected = (os.environ.get("ADMIN_API_KEY") or "").strip()
    if not expected:
        return
    if (x_admin_key or "").strip() != expected:
        raise HTTPException(status_code=403, detail="Invalid admin key")


@app.post("/api/admin/jobs/refresh-all-portfolio-values")
def admin_refresh_all_portfolio_values(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
) -> Dict[str, object]:
    """Batch job: refresh MTM + daily history for every saved portfolio (after daily CSV update)."""
    _require_admin_job_key(x_admin_key)
    return refresh_all_saved_portfolio_values()


@app.post("/api/admin/jobs/refresh-all-portfolio-backtests")
def admin_refresh_all_portfolio_backtests(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
) -> Dict[str, object]:
    """Batch job: re-run backtests/MC for every saved portfolio + monthly history row."""
    _require_admin_job_key(x_admin_key)
    return refresh_all_saved_portfolio_backtests(record_monthly_history=True)


class PortfolioBacktestRequest(BaseModel):
    """Request to run backtest for a saved portfolio."""
    user_id: str  # must match portfolio owner
    intake: Optional[Dict[str, object]] = None  # optional: form values (inflation, horizon, etc.); merged over DB intake
    # Growth: when True (default), MC starting notional uses live portfolio_value over merged intake initial_value.
    # Set False after what-if edits so the submitted intake initial_value is honored.
    use_portfolio_mark_for_initial: bool = True
    scenario_id: Optional[str] = None  # when set, persist under this saved scenario (what-if flow)


@app.delete("/api/portfolio/saved/{portfolio_id}")
def delete_saved_portfolio_endpoint(
    portfolio_id: str,
    user_id: str = Query(..., description="Owner user id; must match portfolio"),
) -> Dict[str, object]:
    """Remove one saved portfolio and its attached intake snapshot (user login row unchanged)."""
    row = get_portfolio(portfolio_id)
    if not row:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if row["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this portfolio")
    if not delete_saved_portfolio(portfolio_id, user_id):
        raise HTTPException(status_code=404, detail="Portfolio not found")
    remaining = count_portfolios_for_user(user_id)
    return {
        "status": "ok",
        "portfolio_id": portfolio_id,
        "remaining_portfolios": remaining,
    }


@app.post("/api/portfolio/saved/{portfolio_id}/backtest")
def run_portfolio_backtest(portfolio_id: str, payload: PortfolioBacktestRequest) -> Dict[str, object]:
    """Run backtest for a saved portfolio. Returns artifacts (Ana-style for growth, Emu-style for retirement)."""
    sid = (payload.scenario_id or "").strip() or None
    _log.info(
        "POST /api/portfolio/saved/%s/backtest action=COMPUTE_MC user_id=%s scenario_id=%s",
        portfolio_id,
        payload.user_id,
        sid or "",
    )
    row = get_portfolio(portfolio_id)
    if not row:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if row["user_id"] != payload.user_id:
        raise HTTPException(status_code=403, detail="Not authorized to run backtest for this portfolio")
    if sid:
        sc = get_scenario(sid)
        if not sc or sc.get("portfolio_id") != portfolio_id or sc.get("user_id") != payload.user_id:
            raise HTTPException(status_code=404, detail="Scenario not found")
    user_intake = merged_intake_for_saved_portfolio(
        portfolio_id, payload.user_id, payload.intake if isinstance(payload.intake, dict) else None
    )
    weights = row["portfolio_ticker_weights"]
    is_retirement = (row.get("portfolio_category") or "growth") == "retirement"
    sec_w = row.get("portfolio_sector_weights")
    ind_w = row.get("portfolio_industry_weights")
    artifacts = run_backtest_for_saved_portfolio(
        portfolio_id=portfolio_id,
        portfolio_weights=weights,
        user_intake=user_intake,
        is_retirement=is_retirement,
        portfolio_sector_weights=sec_w if isinstance(sec_w, dict) else None,
        portfolio_industry_weights=ind_w if isinstance(ind_w, dict) else None,
        use_portfolio_mark_for_initial=payload.use_portfolio_mark_for_initial,
    )
    if not artifacts:
        raise HTTPException(status_code=500, detail="Backtest failed or returned no results")
    _persist_saved_portfolio_backtest(
        portfolio_id, payload.user_id, row, artifacts, user_intake, scenario_id=sid
    )
    _log.info(
        "POST /api/portfolio/saved/%s/backtest action=PERSISTED_SNAPSHOT scenario_id=%s",
        portfolio_id,
        sid or "",
    )
    try:
        snap = refresh_portfolio_valuation(portfolio_id)
        row = get_portfolio(portfolio_id)
        if row:
            row["valuation_history"] = snap.get("valuation_history") or []
            row["valuation_as_of"] = snap.get("valuation_as_of")
    except Exception as exc:
        _log.warning("refresh after backtest: %s", exc)
    return {
        "portfolio": row,
        "artifacts": artifacts,
        "agent": "Emu" if is_retirement else "Ana",
        "backtest_load_source": "computed",
        "backtest_scenario_id": sid or "",
    }


class UpdatePortfolioCompositionRequest(BaseModel):
    """Update saved portfolio ticker weights, then run backtest with merged intake."""

    user_id: str
    ticker_weights: Dict[str, float]
    intake: Optional[Dict[str, object]] = None


@app.put("/api/portfolio/saved/{portfolio_id}/composition")
def update_saved_portfolio_composition(
    portfolio_id: str, payload: UpdatePortfolioCompositionRequest
) -> Dict[str, object]:
    """Replace ticker weights for a saved portfolio, set updated_at, run growth or retirement backtest."""
    row = get_portfolio(portfolio_id)
    if not row:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if row["user_id"] != payload.user_id:
        raise HTTPException(status_code=403, detail="Not authorized to update this portfolio")
    if not payload.ticker_weights or not isinstance(payload.ticker_weights, dict):
        raise HTTPException(status_code=400, detail="ticker_weights is required")
    if not update_portfolio_ticker_weights(portfolio_id, payload.user_id, payload.ticker_weights):
        raise HTTPException(
            status_code=400,
            detail="Could not update portfolio (invalid or empty weights)",
        )
    try:
        rebalance_positions_after_composition_change(portfolio_id)
    except Exception as exc:
        _log.warning("rebalance_positions_after_composition_change: %s", exc)
    row = get_portfolio(portfolio_id)
    if not row:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    user_intake = merged_intake_for_saved_portfolio(
        portfolio_id, payload.user_id, payload.intake if isinstance(payload.intake, dict) else None
    )
    weights = row["portfolio_ticker_weights"]
    is_retirement = (row.get("portfolio_category") or "growth") == "retirement"
    sec_w = row.get("portfolio_sector_weights")
    ind_w = row.get("portfolio_industry_weights")
    artifacts = run_backtest_for_saved_portfolio(
        portfolio_id=portfolio_id,
        portfolio_weights=weights,
        user_intake=user_intake,
        is_retirement=is_retirement,
        portfolio_sector_weights=sec_w if isinstance(sec_w, dict) else None,
        portfolio_industry_weights=ind_w if isinstance(ind_w, dict) else None,
        use_portfolio_mark_for_initial=True,
    )
    if not artifacts:
        raise HTTPException(status_code=500, detail="Backtest failed or returned no results")
    _persist_saved_portfolio_backtest(portfolio_id, payload.user_id, row, artifacts, user_intake)
    try:
        snap = refresh_portfolio_valuation(portfolio_id)
        row = get_portfolio(portfolio_id)
        if row:
            row["valuation_history"] = snap.get("valuation_history") or []
            row["valuation_as_of"] = snap.get("valuation_as_of")
    except Exception as exc:
        _log.warning("refresh after composition: %s", exc)
    return {
        "portfolio": row,
        "artifacts": artifacts,
        "agent": "Emu" if is_retirement else "Ana",
    }


class SaveScenarioRequest(BaseModel):
    """Request to save a scenario."""
    user_id: str
    portfolio_id: str
    scenario_name: str  # user-provided base name; will be suffixed with portfolio name
    description: Optional[str] = None
    intake: Dict[str, object]  # full intake form including what-if parameters


@app.post("/api/scenario/save")
def save_scenario_endpoint(payload: SaveScenarioRequest) -> Dict[str, object]:
    """Save a scenario (intake + what-if params) for a portfolio."""
    row = get_portfolio(payload.portfolio_id)
    if not row:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if row["user_id"] != payload.user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    portfolio_name = (row.get("portfolio_name") or "My Portfolio").strip()
    base = (payload.scenario_name or "").strip() or "scenario"
    slug = re.sub(r"[^\w\-]", "-", portfolio_name.lower()).replace("--", "-").strip("-") or "portfolio"
    scenario_name = f"{base}-{slug}"
    try:
        sid = save_scenario(
            portfolio_id=payload.portfolio_id,
            user_id=payload.user_id,
            scenario_name=scenario_name,
            portfolio_name=portfolio_name,
            intake=payload.intake,
            description=payload.description,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    linked = copy_portfolio_backtest_to_scenario(
        payload.portfolio_id,
        payload.user_id,
        sid,
        from_scenario_id="",
    )
    return {
        "status": "ok",
        "scenario_id": sid,
        "scenario_name": scenario_name,
        "backtest_linked": linked,
    }


@app.get("/api/scenarios")
def list_scenarios(user_id: str = Query(...)) -> Dict[str, object]:
    """List all saved scenarios for a user, grouped by portfolio."""
    scenarios = get_scenarios_by_user(user_id)
    return {"scenarios": scenarios}


class SaveLifeScenarioRequest(BaseModel):
    """Save growth + retirement intakes as one named life scenario (sidebar: Life planner)."""

    user_id: str
    name: str
    growth_portfolio_id: str
    retirement_portfolio_id: str
    growth_intake: Dict[str, object]
    retirement_intake: Dict[str, object]
    description: Optional[str] = None
    frozen_growth_median_at_retirement_usd: Optional[float] = None
    retirement_success_percent: Optional[float] = None
    growth_scenario_id: Optional[str] = None
    retirement_scenario_id: Optional[str] = None


class UpdateLifeScenarioFrozenMedianRequest(BaseModel):
    user_id: str
    frozen_growth_median_at_retirement_usd: Optional[float] = None
    retirement_success_percent: Optional[float] = None


class UpdateLifePlannerIntakesRequest(BaseModel):
    """Update life planner intakes only (does not modify linked growth/retirement saved_scenarios rows)."""

    user_id: str
    growth_intake: Dict[str, object]
    retirement_intake: Dict[str, object]
    name: Optional[str] = None


@app.post("/api/life-scenario/save")
def save_life_scenario_endpoint(
    payload: SaveLifeScenarioRequest,
    background_tasks: BackgroundTasks,
) -> Dict[str, object]:
    try:
        out = save_life_scenario_bundle(
            user_id=payload.user_id,
            life_name=payload.name,
            growth_portfolio_id=payload.growth_portfolio_id,
            retirement_portfolio_id=payload.retirement_portfolio_id,
            growth_intake=payload.growth_intake,
            retirement_intake=payload.retirement_intake,
            description=payload.description,
            frozen_growth_median_at_retirement_usd=payload.frozen_growth_median_at_retirement_usd,
            retirement_success_percent=payload.retirement_success_percent,
            growth_scenario_id=payload.growth_scenario_id,
            retirement_scenario_id=payload.retirement_scenario_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    background_tasks.add_task(
        persist_life_planner_portfolio_backtests,
        payload.user_id,
        payload.growth_portfolio_id,
        payload.retirement_portfolio_id,
        dict(payload.growth_intake),
        dict(payload.retirement_intake),
    )
    return {"status": "ok", **out}


@app.get("/api/life-scenarios")
def list_life_scenarios_endpoint(user_id: str = Query(...)) -> Dict[str, object]:
    return {"life_scenarios": list_life_scenarios_by_user(user_id)}


@app.get("/api/life-scenario/{life_scenario_id}")
def get_life_scenario_endpoint(
    life_scenario_id: str,
    user_id: str = Query(..., description="Owner user id"),
    include_backtest: bool = Query(
        True,
        description="Include persisted growth/retirement backtest artifacts when available",
    ),
) -> Dict[str, object]:
    row = get_life_scenario_for_user(life_scenario_id, user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Life scenario not found")
    if include_backtest:
        g = row.get("growth") or {}
        r = row.get("retirement") or {}
        gpid = g.get("portfolio_id")
        rpid = r.get("portfolio_id")
        if gpid:
            g_art = get_backtest_snapshot(str(gpid))
            if g_art:
                row["growth_backtest_artifacts"] = g_art
        if rpid:
            r_art = get_backtest_snapshot(str(rpid))
            if r_art:
                row["retirement_backtest_artifacts"] = r_art
    return row


@app.put("/api/life-scenario/{life_scenario_id}/planner-intakes")
def update_life_planner_intakes_endpoint(
    life_scenario_id: str,
    payload: UpdateLifePlannerIntakesRequest,
) -> Dict[str, object]:
    ok = update_life_scenario_planner_intakes(
        life_scenario_id,
        payload.user_id,
        dict(payload.growth_intake),
        dict(payload.retirement_intake),
        name=payload.name,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Life scenario not found")
    return {"status": "ok"}


@app.put("/api/life-scenario/{life_scenario_id}/frozen-growth-median")
def update_life_scenario_frozen_median_endpoint(
    life_scenario_id: str,
    payload: UpdateLifeScenarioFrozenMedianRequest,
) -> Dict[str, object]:
    ok = update_life_scenario_frozen_growth_median(
        life_scenario_id,
        payload.user_id,
        frozen_growth_median_at_retirement_usd=payload.frozen_growth_median_at_retirement_usd,
        retirement_success_percent=payload.retirement_success_percent,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Life scenario not found")
    row = get_life_scenario_for_user(life_scenario_id, payload.user_id)
    return {
        "status": "ok",
        "frozen_growth_median_at_retirement_usd": (row or {}).get("frozen_growth_median_at_retirement_usd"),
        "retirement_success_percent": (row or {}).get("retirement_success_percent"),
    }


@app.delete("/api/life-scenario/{life_scenario_id}")
def delete_life_scenario_endpoint(
    life_scenario_id: str,
    user_id: str = Query(..., description="Owner user id"),
) -> Dict[str, object]:
    ok = delete_life_scenario_for_user(life_scenario_id, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Life scenario not found")
    return {"status": "ok"}


@app.get("/api/scenario/{scenario_id}")
def get_saved_scenario(
    scenario_id: str,
    include_backtest: bool = Query(
        True,
        description="Include persisted what-if backtest for this scenario when available",
    ),
) -> Dict[str, object]:
    """Fetch a single scenario by id."""
    row = get_scenario(scenario_id)
    if not row:
        raise HTTPException(status_code=404, detail="Scenario not found")
    if include_backtest:
        pid = str(row.get("portfolio_id") or "")
        art = get_backtest_snapshot(pid, scenario_id=scenario_id) if pid else None
        if art:
            row["backtest_artifacts"] = art
            row["backtest_load_source"] = "portfolio_backtest_snapshots"
            row["backtest_scenario_id"] = scenario_id
            meta = get_backtest_snapshot_meta(pid, scenario_id=scenario_id)
            if meta:
                row["backtest_persisted_at"] = meta.get("updated_at")
            _log.info(
                "GET /api/scenario/%s backtest_source=persisted_db scenarios=%s",
                scenario_id,
                len(art.get("scenarios") or []) if isinstance(art.get("scenarios"), list) else 0,
            )
        else:
            row["backtest_load_source"] = "none"
            _log.info("GET /api/scenario/%s backtest_source=none", scenario_id)
    return row


class UpdateScenarioRequest(BaseModel):
    """Request to update a scenario."""
    user_id: str
    intake: Dict[str, object]
    description: Optional[str] = None


@app.put("/api/scenario/{scenario_id}")
def update_scenario_endpoint(scenario_id: str, payload: UpdateScenarioRequest) -> Dict[str, object]:
    """Update a scenario's intake and description."""
    ok = update_scenario(
        scenario_id=scenario_id,
        user_id=payload.user_id,
        intake=payload.intake,
        description=payload.description,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return {"status": "ok"}


@app.delete("/api/scenario/{scenario_id}")
def delete_scenario_endpoint(
    scenario_id: str,
    user_id: str = Query(...),
) -> Dict[str, object]:
    """Delete a scenario. Portfolio and other scenarios remain."""
    ok = delete_scenario(scenario_id=scenario_id, user_id=user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return {"status": "ok"}


@app.post("/api/portfolio/upload")
def upload_portfolio(
    session_id: str = Form(...), file: UploadFile = File(...)
) -> Dict[str, object]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing CSV file.")
    data = file.file.read()
    try:
        df = pd.read_csv(io.BytesIO(data))
    except Exception as exc:  # pragma: no cover - passthrough for parse errors
        raise HTTPException(status_code=400, detail=f"CSV parse error: {exc}") from exc

    holdings = _extract_holdings_from_df(df)
    session = _get_session(session_id)
    session["portfolio"] = holdings
    return {"status": "ok", "portfolio": holdings}


class SaveAnalyzedPortfolioRequest(BaseModel):
    user_id: str
    holdings: list[dict]


class AnalyzePortfolioEnrichRequest(BaseModel):
    """Holdings from the merged/editable table; server dedupes tickers, loads latest close, drops zero-value rows."""

    holdings: list[dict]


class AnalyzePortfolioBatchInput(BaseModel):
    """One uploaded file: raw rows from /parse plus user-mapped column names."""

    preview_rows: list[dict]
    ticker_column: str
    quantity_column: str
    columns: Optional[list[str]] = None


class AnalyzePortfolioValuesRequest(BaseModel):
    """One or more files; holdings are merged and duplicate tickers are summed before pricing."""

    batches: list[AnalyzePortfolioBatchInput]

    @model_validator(mode="after")
    def _require_batches(self) -> "AnalyzePortfolioValuesRequest":
        if not self.batches:
            raise ValueError("At least one file batch is required.")
        return self


@app.post("/api/analyze-portfolio/parse")
def analyze_portfolio_parse_preview(file: UploadFile = File(...)) -> Dict[str, object]:
    """Upload CSV: return column names and row data for UI (no market data yet)."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing CSV file.")
    data = file.file.read()
    try:
        df = read_analyze_portfolio_csv(data)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"CSV parse error: {exc}") from exc
    preview = dataframe_upload_preview(df)
    return {"status": "ok", **preview}


@app.post("/api/analyze-portfolio/values")
def analyze_portfolio_values(payload: AnalyzePortfolioValuesRequest) -> Dict[str, object]:
    """Merge all file batches, sum quantity per duplicate ticker, then load latest monthly close per ticker."""
    holdings_all: list[dict] = []
    try:
        for batch in payload.batches:
            if not batch.preview_rows:
                raise ValueError("Each batch must have at least one preview row.")
            part = build_holdings_from_row_dicts(
                batch.preview_rows,
                batch.ticker_column,
                batch.quantity_column,
                available_columns=batch.columns,
            )
            holdings_all.extend(part)
        merged = omit_zero_quantity_holdings(dedupe_holdings_sum_quantity(holdings_all))
        if not merged:
            raise ValueError("No holdings after merging files.")
        rows, meta = enrich_holdings_with_latest_close(merged)
        rows, meta = omit_zero_current_amount_rows(rows)
        if not rows:
            raise ValueError(
                "No holdings with a non-zero market value after pricing (e.g. missing monthly price data for all tickers)."
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "ok",
        "rows": rows,
        "total_portfolio_value": meta.get("total_portfolio_value", 0.0),
        "weights_by_ticker": meta.get("weights_by_ticker", {}),
    }


@app.post("/api/analyze-portfolio/enrich")
def analyze_portfolio_enrich(payload: AnalyzePortfolioEnrichRequest) -> Dict[str, object]:
    """Re-price holdings after table edits (new rows, ticker/qty changes): same pipeline as /values after merge."""
    cleaned: list[dict] = []
    for h in payload.holdings:
        if not isinstance(h, dict):
            continue
        t = str(h.get("ticker", "")).strip().upper()
        if not t:
            continue
        try:
            q = float(h.get("quantity", 0) or 0)
        except (TypeError, ValueError):
            continue
        cleaned.append({"ticker": t, "quantity": q})
    try:
        merged = omit_zero_quantity_holdings(dedupe_holdings_sum_quantity(cleaned))
        if not merged:
            raise ValueError("No valid holdings (each row needs a ticker and non-zero quantity).")
        rows, meta = enrich_holdings_with_latest_close(merged)
        rows, meta = omit_zero_current_amount_rows(rows)
        if not rows:
            raise ValueError(
                "No holdings with a non-zero market value after pricing (e.g. missing monthly price data for all tickers)."
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "ok",
        "rows": rows,
        "total_portfolio_value": meta.get("total_portfolio_value", 0.0),
        "weights_by_ticker": meta.get("weights_by_ticker", {}),
    }


@app.post("/api/analyze-portfolio/save")
def save_analyzed_portfolio_endpoint(payload: SaveAnalyzedPortfolioRequest) -> Dict[str, object]:
    """Save analyzed portfolio. Returns portfolio_id."""
    if not payload.holdings:
        raise HTTPException(status_code=400, detail="Holdings cannot be empty.")
    portfolio_id = save_analyzed_portfolio(payload.user_id, payload.holdings)
    return {"status": "ok", "portfolio_id": portfolio_id}


@app.delete("/api/analyze-portfolio/{portfolio_id}")
def delete_analyzed_portfolio_endpoint(
    portfolio_id: str,
    user_id: str = Query(..., description="Owner user id; must match analyzed portfolio row"),
) -> Dict[str, object]:
    """Remove one analyzed CSV snapshot row (upload / agent-backtest persistence)."""
    owner = get_analyzed_portfolio_owner_user_id(portfolio_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="Analyzed portfolio not found")
    if owner != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this analyzed portfolio")
    if not delete_analyzed_portfolio(portfolio_id, user_id):
        raise HTTPException(status_code=404, detail="Analyzed portfolio not found")
    return {"status": "ok", "portfolio_id": portfolio_id}


class AnalyzePortfolioBacktestRequest(BaseModel):
    """Run growth (Ana) or income/retirement (Emu) backtest + MC from target weights only."""

    user_id: str
    weights_by_ticker: dict[str, float]
    portfolio_kind: Optional[Literal["growth", "income"]] = None
    intake: Optional[Dict[str, object]] = None


def _intake_for_analyze_backtest(user_id: str, payload_intake: Optional[Dict[str, object]]) -> Dict[str, object]:
    """Merge DB user_intake with optional request overrides (no saved-portfolio snapshot)."""
    user_intake: Dict[str, object] = {}
    db_intake = get_user_intake(user_id)
    if db_intake:
        skip = {"user_id", "created_at", "updated_at"}
        for k, v in db_intake.items():
            if k in skip:
                continue
            user_intake[k] = v
    if payload_intake and isinstance(payload_intake, dict):
        user_intake.update(payload_intake)
    return user_intake


def _resolve_analyze_portfolio_kind(
    requested_kind: Optional[Literal["growth", "income"]],
    user_intake: Dict[str, object],
) -> Literal["growth", "income"]:
    if requested_kind in ("growth", "income"):
        return requested_kind

    retirement_status = str(user_intake.get("retirement_status") or "").strip()
    if retirement_status == "both_working":
        return "growth"
    if retirement_status == "both_retired":
        return "income"
    if retirement_status in {"self_retired", "partner_retired"}:
        raise HTTPException(
            status_code=400,
            detail=(
                "Please choose Growth or Retirement analysis when one partner is retired and the other is working."
            ),
        )
    return "growth"


@app.post("/api/analyze-portfolio/backtest")
def analyze_portfolio_backtest(payload: AnalyzePortfolioBacktestRequest) -> Dict[str, object]:
    """Growth → same path as saved growth backtest; income → retirement backtest + MC."""
    if not payload.weights_by_ticker:
        raise HTTPException(status_code=400, detail="weights_by_ticker cannot be empty.")

    raw_weights: Dict[str, float] = {}
    for k, v in payload.weights_by_ticker.items():
        t = str(k).strip().upper()
        if not t:
            continue
        try:
            raw_weights[t] = float(v)
        except (TypeError, ValueError):
            continue
    weights = normalize_portfolio_weights_significant_digits(raw_weights, significant=5)
    if not weights:
        raise HTTPException(status_code=400, detail="Weights must sum to a positive value.")

    user_intake = _intake_for_analyze_backtest(payload.user_id, payload.intake)
    portfolio_kind = _resolve_analyze_portfolio_kind(payload.portfolio_kind, user_intake)
    is_retirement = portfolio_kind == "income"

    # Persist ticker + weight only (no quantity / dollar amounts); backtest uses weights dict alone.
    snapshot = [{"ticker": t, "weight": float(weights[t])} for t in weights]
    analyzed_id = save_analyzed_portfolio(payload.user_id, snapshot)
    run_id = str(uuid.uuid4())
    preferred_sector_weights = get_preferred_portfolio_sector_weights(weights)
    try:
        artifacts = run_backtest_for_saved_portfolio(
            portfolio_id=run_id,
            portfolio_weights=weights,
            user_intake=user_intake,
            is_retirement=is_retirement,
            portfolio_industry_weights=preferred_sector_weights,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Backtest failed: {exc}") from exc
    if not artifacts:
        raise HTTPException(status_code=500, detail="Backtest failed or returned no results.")
    return {
        "status": "ok",
        "analyzed_portfolio_id": analyzed_id,
        "backtest_session_id": run_id,
        "artifacts": artifacts,
        "agent": "Emu" if is_retirement else "Ana",
        "portfolio_kind": portfolio_kind,
    }


_UPLOAD_SECTOR_OPTIONS = list(ASSET_CLASS_SECTORS)

_UPLOAD_INDUSTRY_OPTIONS = list(GICS_INDUSTRY_SECTORS)


def _normalize_group_weights(raw: Dict[str, float]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for k, v in raw.items():
        if not k or not isinstance(k, str):
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv > 0:
            out[k.strip()] = fv
    total = sum(out.values())
    if total <= 0:
        return {}
    return {kk: vv / total for kk, vv in out.items()}


def _aggregate_labels_to_weights(
    ticker_weights: Dict[str, float],
    ticker_to_label: Dict[str, str],
) -> Dict[str, float]:
    buckets: Dict[str, float] = {}
    for t, w in ticker_weights.items():
        t_up = str(t).strip().upper()
        if not t_up:
            continue
        try:
            wf = float(w)
        except (TypeError, ValueError):
            continue
        if wf <= 0:
            continue
        lbl = (ticker_to_label.get(t_up) or "Other").strip()
        buckets[lbl] = buckets.get(lbl, 0.0) + wf
    return _normalize_group_weights(buckets)


def _llm_classify_tickers_sector_industry(
    http_session: dict,
    tickers: List[str],
    *,
    style_quala_or_panda: Literal["quala", "panda"],
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Ticker -> (asset class, GICS sector).

    Alpha Vantage sector-weights script fills the session cache first; one LLM batch only for
    tickers the script could not classify.
    """
    seen: set[str] = set()
    tickers_uq: List[str] = []
    for raw in tickers:
        t = str(raw).strip().upper()
        if not t or t in seen:
            continue
        seen.add(t)
        tickers_uq.append(t)
    tickers = tickers_uq
    cache = http_session.setdefault("analyze_upload_ticker_classification", {})
    seed_ticker_classification_cache_from_alphavantage(cache, tickers)
    missing = [t for t in tickers if t not in cache]
    if missing:
        llm = LLMClient()
        role = (
            "You are Quala, a growth portfolio advisor. "
            if style_quala_or_panda == "quala"
            else "You are Panda, a retirement-income portfolio advisor. "
        )
        prompt = (
            f"{role}"
            "For each ticker, assign exactly one **asset class** and one **sector** (GICS-style) from the lists below.\n"
            "**Asset class** = broad holding bucket (e.g. US-listed equity ETFs → US Stocks; VXUS/IEFA → International Stocks; "
            "BND/AGG → Bonds; GLD/PDBC → Commodities; crypto ETPs → Digital Assets).\n"
            "**Sector** = equity GICS sector for underlying exposure (bond-only / digital-asset-only / commodity-only sleeves → Other).\n"
            "Use the closest match; never invent names outside the lists.\n\n"
            f"Valid asset classes: {', '.join(_UPLOAD_SECTOR_OPTIONS)}\n"
            f"Valid sectors: {', '.join(_UPLOAD_INDUSTRY_OPTIONS)}\n\n"
            f"Tickers: {', '.join(missing)}\n\n"
            "Return JSON only, shape: "
            '{"AAPL":{"asset_class":"US Stocks","sector":"Technology"}, ...}\n'
            "(Keys `asset_class` or `asset_type` for asset class; `sector` or `industry` for GICS sector.)\n"
        )
        try:
            raw = llm.complete(prompt)
            match = re.search(r"\{[\s\S]*\}", raw)
            if match:
                parsed = json.loads(match.group(0))
                data = (
                    {str(k).strip().upper(): v for k, v in parsed.items()}
                    if isinstance(parsed, dict)
                    else {}
                )
            else:
                data = {}
        except (RuntimeError, Exception):
            data = {}
        for t in missing:
            entry = data.get(t) if isinstance(data, dict) else None
            if isinstance(entry, dict):
                sec = str(
                    entry.get("asset_class") or entry.get("asset_type") or ""
                ).strip()
                ind = str(entry.get("sector") or entry.get("industry") or "").strip()
                if sec not in _UPLOAD_SECTOR_OPTIONS:
                    sec = "Other"
                if ind not in _UPLOAD_INDUSTRY_OPTIONS:
                    ind = "Other"
                cache[t] = {"sector": sec, "industry": ind}
            else:
                cache[t] = {"sector": "Other", "industry": "Other"}
    out_sec: Dict[str, str] = {}
    out_ind: Dict[str, str] = {}
    for t in tickers:
        row = cache.get(t) or {"sector": "Other", "industry": "Other"}
        out_sec[t] = row.get("sector", "Other")
        out_ind[t] = row.get("industry", "Other")
    return out_sec, out_ind


def _sector_industry_weights_for_analyze_upload(
    http_session: dict,
    ticker_weights: Dict[str, float],
    *,
    for_retirement: bool,
) -> Tuple[Dict[str, float], Dict[str, float]]:
    tickers = sorted({str(k).strip().upper() for k in ticker_weights if str(k).strip()})
    ts, _ti = _llm_classify_tickers_sector_industry(
        http_session,
        tickers,
        style_quala_or_panda="panda" if for_retirement else "quala",
    )
    sec_w = _aggregate_labels_to_weights(ticker_weights, ts)
    sec_w = normalize_asset_class_weights(sec_w) if sec_w else {}
    per_maps = per_ticker_normalized_gics_maps_for_tickers(tickers)
    ind_w = portfolio_industry_weights_from_per_ticker_maps(ticker_weights, per_maps)
    return sec_w, ind_w


def _rollup_from_analyze_classification_cache(
    weights: Dict[str, float],
    http_session: Dict[str, object],
) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    """Build asset-class / sector tooltip maps (AV script cache first, then LLM-filled gaps)."""
    cache = http_session.setdefault("analyze_upload_ticker_classification", {})
    seed_ticker_classification_cache_from_alphavantage(cache, sorted(weights.keys()))
    ts: Dict[str, str] = {}
    for t in weights:
        row = cache.get(t)
        if isinstance(row, dict):
            ts[t] = str(row.get("sector") or "Other")
        else:
            ts[t] = "Other"
    ac_roll, _ = build_breakdown_ticker_lists(
        weights, ts, {t: "Other" for t in weights}
    )
    per_maps = per_ticker_normalized_gics_maps_for_tickers(sorted(weights.keys()))
    sec_roll = build_industry_ticker_lists_from_per_ticker_maps(weights, per_maps)
    return ac_roll, normalize_industry_ticker_rollup(sec_roll)


def attach_portfolio_breakdown_tickers_to_artifacts(
    artifacts: Dict[str, object],
    session_id: str,
    *,
    style_quala_or_panda: Literal["quala", "panda"] = "quala",
) -> None:
    """Add portfolio_*_tickers for bar-chart hovers (Alpha Vantage first, LLM fallback)."""
    comp = artifacts.get("portfolio_composition")
    if not isinstance(comp, dict) or not comp:
        return
    weights: Dict[str, float] = {}
    for k, v in comp.items():
        t = str(k).strip().upper()
        if not t:
            continue
        try:
            wf = float(v)
        except (TypeError, ValueError):
            continue
        if wf > 0:
            weights[t] = wf
    if not weights:
        return
    http = _get_session(session_id)
    tickers_sorted = sorted(weights.keys())
    t_ac, _t_sec = _llm_classify_tickers_sector_industry(
        http,
        tickers_sorted,
        style_quala_or_panda=style_quala_or_panda,
    )
    per_maps = per_ticker_normalized_gics_maps_for_tickers(tickers_sorted)
    ac_roll, _ = build_breakdown_ticker_lists(
        weights, t_ac, {t: "Other" for t in weights}
    )
    sec_roll = build_industry_ticker_lists_from_per_ticker_maps(weights, per_maps)
    artifacts["portfolio_sectors_tickers"] = ac_roll
    artifacts["portfolio_industries_tickers"] = normalize_industry_ticker_rollup(sec_roll)
    artifacts["portfolio_sectors"] = rollup_weights_from_ticker_classification(
        weights, t_ac, normalize_asset_class_weights
    )
    artifacts["portfolio_industries"] = portfolio_industry_weights_from_per_ticker_maps(
        weights, per_maps
    )


def _ticker_weights_positive(raw: object) -> Dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, float] = {}
    for k, v in raw.items():
        t = str(k).strip().upper()
        if not t:
            continue
        try:
            wf = float(v)
        except (TypeError, ValueError):
            continue
        if wf > 0:
            out[t] = wf
    return out


def enrich_all_portfolios_breakdown_tickers(
    all_portfolios: Dict[str, object],
    http_session: Dict[str, object],
    *,
    style_quala_or_panda: Literal["quala", "panda"],
) -> None:
    """
    Add sectors_tickers / industries_tickers to each scenario in all_portfolios (and retirement arm)
    for the same bar hover behavior as post-backtest charts.
    """
    if not isinstance(all_portfolios, dict) or not all_portfolios:
        return
    universe: set[str] = set()
    for p in all_portfolios.values():
        if not isinstance(p, dict):
            continue
        universe.update(_ticker_weights_positive(p.get("tickers")).keys())
        ret = p.get("retirement")
        if isinstance(ret, dict):
            universe.update(_ticker_weights_positive(ret.get("tickers")).keys())
    if not universe:
        return
    tickers_sorted = sorted(universe)
    t_ac, _t_sec = _llm_classify_tickers_sector_industry(
        http_session,
        tickers_sorted,
        style_quala_or_panda=style_quala_or_panda,
    )
    per_maps = per_ticker_normalized_gics_maps_for_tickers(tickers_sorted)
    for p in all_portfolios.values():
        if not isinstance(p, dict):
            continue
        w = _ticker_weights_positive(p.get("tickers"))
        if w:
            ac_roll, _ = build_breakdown_ticker_lists(w, t_ac, {t: "Other" for t in w})
            sec_roll = build_industry_ticker_lists_from_per_ticker_maps(w, per_maps)
            p["sectors_tickers"] = ac_roll
            p["industries_tickers"] = normalize_industry_ticker_rollup(sec_roll)
            p["sectors"] = rollup_weights_from_ticker_classification(w, t_ac, normalize_asset_class_weights)
            p["industries"] = portfolio_industry_weights_from_per_ticker_maps(w, per_maps)
        ret = p.get("retirement")
        if isinstance(ret, dict):
            w_r = _ticker_weights_positive(ret.get("tickers"))
            if w_r:
                ac_r, _ = build_breakdown_ticker_lists(w_r, t_ac, {t: "Other" for t in w_r})
                sec_r = build_industry_ticker_lists_from_per_ticker_maps(w_r, per_maps)
                ret["sectors_tickers"] = ac_r
                ret["industries_tickers"] = normalize_industry_ticker_rollup(sec_r)
                ret["sectors"] = rollup_weights_from_ticker_classification(
                    w_r, t_ac, normalize_asset_class_weights
                )
                ret["industries"] = portfolio_industry_weights_from_per_ticker_maps(w_r, per_maps)


class AnalyzePortfolioAgentBacktestRequest(BaseModel):
    """Analyze upload: asset class + GICS sector classification, then Ana/Emu crew backtest + narrative."""

    session_id: Optional[str] = None
    user_id: str
    weights_by_ticker: dict[str, float]
    portfolio_kind: Optional[Literal["growth", "income"]] = None
    intake: Optional[Dict[str, object]] = None


@app.post("/api/analyze-portfolio/agent-backtest")
def analyze_portfolio_agent_backtest(payload: AnalyzePortfolioAgentBacktestRequest) -> Dict[str, object]:
    """Uploaded portfolio weights → LLM asset class / sector (Quala or Panda framing) → Ana or Emu backtest + explanation."""
    if not payload.weights_by_ticker:
        raise HTTPException(status_code=400, detail="weights_by_ticker cannot be empty.")

    raw_weights: Dict[str, float] = {}
    for k, v in payload.weights_by_ticker.items():
        t = str(k).strip().upper()
        if not t:
            continue
        try:
            raw_weights[t] = float(v)
        except (TypeError, ValueError):
            continue
    weights = normalize_portfolio_weights_significant_digits(raw_weights, significant=5)
    if not weights:
        raise HTTPException(status_code=400, detail="Weights must sum to a positive value.")

    user_intake = _intake_for_analyze_backtest(payload.user_id, payload.intake)
    portfolio_kind = _resolve_analyze_portfolio_kind(payload.portfolio_kind, user_intake)
    is_retirement = portfolio_kind == "income"
    sid = payload.session_id or str(uuid.uuid4())
    _get_session(sid)

    intake_ctx = intake_context_from_user_intake_dict(user_intake, is_retirement)
    set_intake_context(sid, intake_ctx or IntakeContext())

    http_session = _get_session(sid)
    sec_w, ind_w = _sector_industry_weights_for_analyze_upload(
        http_session,
        weights,
        for_retirement=is_retirement,
    )

    snapshot = [{"ticker": t, "weight": float(weights[t])} for t in weights]
    analyzed_id = save_analyzed_portfolio(payload.user_id, snapshot)

    try:
        result = run_analyze_upload_agent_pipeline(
            sid,
            dict(weights),
            is_retirement=is_retirement,
            sector_weights=sec_w,
            industry_weights=ind_w,
            intake_payload=payload.intake,
            user_id=(payload.user_id or "").strip() or None,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agent backtest failed: {exc}") from exc

    artifacts = dict(result.get("artifacts") or {})
    if not artifacts.get("scenarios") and not artifacts.get("portfolio_composition"):
        raise HTTPException(status_code=500, detail="Agent backtest returned no results.")

    ac_ticks, sec_ticks = _rollup_from_analyze_classification_cache(weights, http_session)
    artifacts["portfolio_sectors_tickers"] = ac_ticks
    artifacts["portfolio_industries_tickers"] = sec_ticks
    result["artifacts"] = artifacts

    return {
        "status": "ok",
        "session_id": sid,
        "analyzed_portfolio_id": analyzed_id,
        "artifacts": artifacts,
        "agent": result.get("agent"),
        "reply": result.get("reply"),
        "portfolio_kind": portfolio_kind,
        "portfolio_sectors": sec_w,
        "portfolio_industries": ind_w,
    }


@app.post("/api/backtest")
def run_backtest(payload: BacktestRequest) -> Dict[str, object]:
    session = _get_session(payload.session_id)
    if "portfolio" not in session:
        raise HTTPException(status_code=400, detail="No portfolio found for session.")
    portfolio = session["portfolio"]
    tickers = list(portfolio.keys())

    raw_prices = load_prices_from_data_output(DATA_OUTPUT_DIR, tickers)
    has_leveraged = any(t.upper() in LEVERAGED_ETF_UNDERLYING for t in tickers)
    mc_leveraged_substitution: list[str] = []
    if has_leveraged:
        prices, mc_leveraged_substitution = build_prices_for_leveraged_portfolio(
            portfolio, raw_prices, DATA_OUTPUT_DIR, load_single_price_series, start_year=1999
        )
    else:
        prices = raw_prices
        if not prices.empty:
            mask = prices.index.year >= 1999
            prices = prices.loc[mask].copy()
        prices = pad_prices_to_start_year(prices, start_year=1999, min_years=20)
    frequency, years = infer_frequency_and_years(prices)

    benchmark_prices = load_sixty_forty_benchmark_prices(DATA_OUTPUT_DIR)

    if payload.rebalancing_rule == "threshold":
        rule = RebalancingRule(
            "threshold",
            threshold=payload.threshold if payload.threshold is not None else 0.05,
            check_frequency=payload.check_frequency,
        )
    else:
        rule = RebalancingRule(payload.rebalancing_rule)

    intake_context = INTAKE_CONTEXT_STORE.get(payload.session_id)
    initial_value = intake_context.initial_value if intake_context is not None else 1.0
    config = BacktestConfig(
        frequency=frequency,
        rebalancing_rule=rule,
        transaction_cost_bps=payload.transaction_cost_bps,
        initial_value=initial_value,
        intake_context=intake_context,
    )
    result = backtest_portfolio(
        prices=prices,
        target_weights=portfolio,
        benchmark_prices=benchmark_prices,
        config=config,
    )

    portfolio_returns = result.timeseries["portfolio_return"].dropna()
    mc_returns = (
        result.timeseries["portfolio_return_market"].dropna()
        if intake_context is not None and "portfolio_return_market" in result.timeseries.columns
        else portfolio_returns
    )
    # When build_prices_for_leveraged_portfolio was used, backtest already has 3x underlying.
    # Only substitute for MC when leveraged but we did NOT use adjusted prices.
    if has_leveraged and not mc_leveraged_substitution:
        price_data = prices.resample("ME").last().dropna(how="all") if frequency == "monthly" else prices
        returns_df = price_data.pct_change().dropna(how="any")
        if not returns_df.empty:
            mc_returns, mc_leveraged_substitution = get_mc_returns_for_leveraged_portfolio(
                portfolio, returns_df, DATA_OUTPUT_DIR, load_single_price_series
            )
    benchmark_returns = benchmark_prices.pct_change().dropna()

    actual_periods = len(portfolio_returns)
    periods_per_year = 12 if frequency == "monthly" else 252
    if intake_context is not None and intake_context.horizon_years is not None:
        _hz_api = int(intake_context.horizon_years)
        horizon_years = max(1, _hz_api) if _hz_api <= 0 else _hz_api
    else:
        horizon_years = max(1, round(actual_periods / periods_per_year))
    pv_col = result.timeseries["portfolio_value"]
    idx_at_horizon = min(
        horizon_years * periods_per_year - 1,
        actual_periods - 1,
        len(pv_col) - 1,
    )
    idx_at_horizon = max(0, idx_at_horizon)
    value_at_retirement = float(pv_col.iloc[idx_at_horizon]) if idx_at_horizon >= 0 and len(pv_col) > idx_at_horizon else None
    metrics = dict(result.metrics)
    if value_at_retirement is not None:
        metrics["portfolio_value_at_retirement"] = value_at_retirement

    mc_periods = min(
        horizon_years * periods_per_year,
        actual_periods,
    )
    mc_years_effective = round(mc_periods / periods_per_year)
    mc_config = MonteCarloConfig(
        years=payload.mc_years or mc_years_effective,
        n_sims=payload.mc_sims,
        frequency=frequency,
        blowup_threshold=payload.blowup_threshold,
        intake_context=intake_context,
    )
    answers = monte_carlo_questions(
        portfolio_returns=mc_returns,
        benchmark_returns=benchmark_returns,
        strategy_a_returns=mc_returns,
        strategy_b_returns=benchmark_returns,
        config=mc_config,
        years=payload.mc_years or mc_years_effective,
        frequency=frequency,
        periods=mc_periods,
    )

    sim = simulate_monte_carlo(
        mc_returns,
        config=mc_config,
        years=payload.mc_years or mc_years_effective,
        frequency=frequency,
        periods=mc_periods,
    )
    summary_paths = {k: v.tolist() for k, v in sim["summary_paths"].items()}

    # Use terminal values from same sim as summary_paths so chart and table match
    tv = sim["terminal_values"]
    answers["terminal_value_p10"] = float(np.quantile(tv, 0.1))
    answers["terminal_value_p50"] = float(np.quantile(tv, 0.5))
    answers["terminal_value_p90"] = float(np.quantile(tv, 0.9))

    out: Dict[str, object] = {
        "metrics": metrics,
        "monte_carlo": answers,
        "timeseries": result.timeseries.reset_index().to_dict(orient="records"),
        "rebalancing_events": result.rebalancing_events.to_dict(orient="records"),
        "summary_paths": summary_paths,
        "summary_metadata": sim["metadata"],
    }
    if mc_leveraged_substitution:
        out["mc_leveraged_substitution"] = mc_leveraged_substitution
    return out


@app.post("/api/rebalance")
def rebalance(payload: RebalanceRequest) -> Dict[str, object]:
    session = _get_session(payload.session_id)
    if payload.target_holdings is not None:
        target = _normalize_holdings(payload.target_holdings)
    else:
        if "portfolio" not in session:
            raise HTTPException(status_code=400, detail="No portfolio found for session.")
        target = session["portfolio"]

    current = _normalize_holdings(payload.current_holdings)
    all_assets = sorted(set(target) | set(current))
    trades = []
    for asset in all_assets:
        delta = target.get(asset, 0.0) - current.get(asset, 0.0)
        if abs(delta) < 1e-6:
            continue
        trades.append(
            {
                "asset": asset,
                "target_weight": target.get(asset, 0.0),
                "current_weight": current.get(asset, 0.0),
                "delta_weight": delta,
                "action": "buy" if delta > 0 else "sell",
            }
        )

    return {"trades": trades, "target": target, "current": current}


@app.post("/api/sector-map")
def sector_map(payload: SectorMapRequest) -> Dict[str, object]:
    session = _get_session(payload.session_id)
    tickers = [t.strip().upper() for t in payload.tickers if t and str(t).strip()]
    if not tickers:
        raise HTTPException(status_code=400, detail="Tickers are required.")

    cached = session.get("sector_map", {})
    missing = [t for t in tickers if t not in cached]
    sectors = list(GICS_INDUSTRY_SECTORS)

    if missing:
        still_llm: List[str] = []
        for ticker in missing:
            av_lab = gics_sector_for_ticker_via_alphavantage_script(ticker)
            if av_lab and av_lab in sectors:
                cached[ticker] = av_lab
            else:
                still_llm.append(ticker)

        if still_llm:
            llm = LLMClient()
            prompt = (
                "Map each ticker to one of these sectors:\n"
                f"{', '.join(sectors)}\n"
                "Return JSON only with tickers as keys and sector names as values.\n"
                f"Tickers: {', '.join(still_llm)}\n"
            )
            try:
                raw = llm.complete(prompt)
                match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
                if match:
                    mapping = json.loads(match.group(0))
                else:
                    mapping = {}
            except Exception:
                mapping = {}

            for ticker in still_llm:
                sector = mapping.get(ticker) if isinstance(mapping, dict) else None
                if not sector or sector not in sectors:
                    cached[ticker] = "Other"
                else:
                    cached[ticker] = sector

    session["sector_map"] = cached
    return {"sector_map": {t: cached.get(t, "Other") for t in tickers}}


@app.post("/api/session/intake-data")
def store_intake_data(payload: IntakeDataRequest) -> Dict[str, object]:
    """Store structured intake for backtesting (monthly savings, expenses, birth dates)."""
    import logging
    from backtesting.backtesting_service.types import IntakeContext

    _log = logging.getLogger("portfolio_optimizer")
    _log.info("store_intake_data: session_id=%s initial_value=%s", payload.session_id, payload.initial_value)

    expenses = []
    _spending = str(getattr(payload, "spending", None) or "").strip() or None
    if _spending:
        from backend.intake_parser import parse_spending_to_expense_dicts, spending_field_declares_one_time_outflows

        if not spending_field_declares_one_time_outflows(_spending):
            _spending = None
    if _spending:
        for d in parse_spending_to_expense_dicts(_spending):
            y = int(d["years"])
            a = _to_float_safe(d.get("amount", 0))
            if y < 0 or a <= 0:
                continue
            lbl = str(d.get("label") or "").strip()
            if lbl:
                expenses.append((y, a, lbl))
            else:
                expenses.append((y, a))
    birth_dates = [
        (int(b["year"]), int(b.get("month", 6)))
        for b in payload.birth_dates
        if isinstance(b, dict) and "year" in b
    ]
    inflation_rate = (payload.inflation_assumption if payload.inflation_assumption is not None else 3.0) / 100.0
    _rts = getattr(payload, "retirement_timeline_self", None)
    _rtp = getattr(payload, "retirement_timeline_partner", None)
    intake = IntakeContext(
        initial_value=payload.initial_value,
        monthly_savings=payload.monthly_savings,
        horizon_years=payload.horizon_years,
        planning_for=payload.planning_for or "self",
        birth_dates=birth_dates if birth_dates else None,
        current_monthly_expense=payload.current_monthly_expense,
        upcoming_expenses=sorted(expenses, key=lambda x: x[0]),
        spending=_spending,
        display_unit=payload.display_unit or None,
        inflation_rate=inflation_rate,
        retirement_status=(str(payload.retirement_status or "").strip() or None),
        retirement_timeline_self=(str(_rts).strip() or None) if _rts else None,
        retirement_timeline_partner=(str(_rtp).strip() or None) if _rtp else None,
    )
    set_intake_context(payload.session_id, intake)
    return {"status": "ok"}


@app.post("/api/session/intent")
def set_session_intent(payload: IntentRequest) -> Dict[str, object]:
    session = _get_session(payload.session_id)
    intent = payload.intent.strip().lower()
    if intent not in {"intake", "backtest", "rebalance", "upload", "general"}:
        raise HTTPException(status_code=400, detail="Invalid intent.")
    session["primary_intent"] = intent
    session["pending_intent"] = intent
    return {"status": "ok", "intent": intent}

