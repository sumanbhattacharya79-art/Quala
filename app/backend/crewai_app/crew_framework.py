import json
import logging
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")
logger = logging.getLogger(__name__)

from crewai import Agent, Crew, LLM, Process, Task
from crewai.tools import BaseTool

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_APP_ROOT = Path(__file__).resolve().parents[2]
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))

from backend.sector_industry_taxonomy import (
    apply_taxonomy_to_artifact,
    normalize_asset_class_weights,
    normalize_gics_industry_weights,
)
from backend.alphavantage_sector_bridge import get_preferred_portfolio_sector_weights

from backtesting.driver import resolve_load_ticker, _CRYPTO_TICKER_ALIAS
from backtesting.price_data_paths import monthly_csv_exists, monthly_csv_path

from .gemini_token_usage import crew_gemini_model_litellm, extract_crew_usage_token_counts, log_crewai_usage

# Session-scoped intake context for RunBacktestTool
_BACKTEST_SESSION_ID = threading.local()
INTAKE_CONTEXT_STORE: Dict[str, object] = {}
_INTAKE_STORE_LOCK = threading.Lock()
# Full multi-line intake prompt text last sent for a session; used to omit re-sending it on later turns
SESSION_FULL_INTAKE_PROMPT_BY_SESSION: Dict[str, str] = {}


def set_intake_context(session_id: str, intake: "IntakeContext") -> None:
    """Store structured intake for a session (called from API or parser)."""
    with _INTAKE_STORE_LOCK:
        INTAKE_CONTEXT_STORE[session_id] = intake


def _upcoming_expenses_tuples_to_big_spending_rows(intake_context: Any) -> List[Dict[str, Any]]:
    """Expose IntakeContext.upcoming_expenses (with optional labels) for timeline chart markers.

    Positive amounts = one-time outflows; negative = one-time inflows (what-if windfall / growth inflow).
    Chart uses positive ``amount`` plus optional ``kind``: ``inflow``.
    """
    rows_out: List[Dict[str, Any]] = []
    for t in getattr(intake_context, "upcoming_expenses", None) or []:
        if not isinstance(t, tuple) or len(t) < 2:
            continue
        try:
            y = float(t[0])
            a = float(t[1])
        except (TypeError, ValueError):
            continue
        if a == 0:
            continue
        row: Dict[str, Any] = {
            "years": int(y) if y >= 1000 else int(y),
            "amount": abs(float(a)),
        }
        if a < 0:
            row["kind"] = "inflow"
        if len(t) >= 3:
            lg = str(t[2]).strip()
            if lg:
                row["label"] = lg
        rows_out.append(row)
    return rows_out


DATA_OUTPUT_DIR = PROJECT_ROOT / "data_output"
MODEL_OUTPUT_DIR = PROJECT_ROOT / "model_output"
FETCH_SCRIPT = PROJECT_ROOT / "data_input" / "fetch_alphavantage_example.py"


# ------------------------------------------------------------------ #
#  LLM helpers                                                        #
# ------------------------------------------------------------------ #

def _gemini_api_key() -> Optional[str]:
    env_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if env_key:
        return env_key
    key_path = PROJECT_ROOT / "gemini_apikey.txt"
    if not key_path.exists():
        return None
    return key_path.read_text(encoding="utf-8").strip() or None


def _configure_litellm_retries() -> None:
    try:
        import litellm
        litellm.num_retries = 3
    except ImportError:
        pass


_configure_litellm_retries()


def build_llm(role: Literal["money_manager", "analyst"] = "money_manager") -> LLM:
    """CrewAI LLM instance. Quala/Panda use ``money_manager``; Ana/Emu use ``analyst`` (see ``crew_gemini_model_litellm``)."""
    model = crew_gemini_model_litellm(role)
    temperature = float(os.getenv("GEMINI_TEMPERATURE", "1.0"))
    return LLM(
        model=model,
        api_key=_gemini_api_key(),
        temperature=temperature,
        timeout=120,
    )


# ------------------------------------------------------------------ #
#  Tools for Analyst Ana                                              #
# ------------------------------------------------------------------ #

class CheckTickerDataTool(BaseTool):
    name: str = "check_ticker_data"
    description: str = (
        "Check which tickers already have historical price data available in "
        "data_output (monthly series). Input: comma-separated ticker symbols (e.g. 'AAPL,MSFT,VTI'). "
        "Returns JSON with 'present' and 'missing' ticker lists."
    )

    def _run(self, tickers: str) -> str:
        ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
        present, missing = [], []
        for ticker in ticker_list:
            load_ticker = resolve_load_ticker(ticker)
            path = monthly_csv_path(DATA_OUTPUT_DIR, load_ticker)
            (present if path.exists() else missing).append(ticker)
        return json.dumps({"present": present, "missing": missing})


class FetchTickerDataTool(BaseTool):
    name: str = "fetch_ticker_data"
    description: str = (
        "Fetch historical monthly price data for tickers from Alpha Vantage and "
        "save to data_output. Input: comma-separated ticker symbols to fetch "
        "(e.g. 'SCHD,VGSH'). Skips tickers that already have data."
    )

    def _run(self, tickers: str) -> str:
        ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
        results = []
        for ticker in ticker_list:
            load_ticker = resolve_load_ticker(ticker)
            path = monthly_csv_path(DATA_OUTPUT_DIR, load_ticker)
            if path.exists():
                results.append(f"{ticker}: already exists, skipped")
                continue
            try:
                cmd = [
                    sys.executable, str(FETCH_SCRIPT),
                    "--symbol", load_ticker, "--insecure",
                ]
                subprocess.run(
                    cmd, check=False, timeout=120,
                    capture_output=True, text=True,
                    cwd=str(PROJECT_ROOT),
                )
                results.append(
                    f"{ticker}: fetched successfully"
                    if monthly_csv_exists(DATA_OUTPUT_DIR, load_ticker)
                    else f"{ticker}: fetch ran but file not created (check API key / rate limit)"
                )
            except Exception as exc:
                results.append(f"{ticker}: fetch failed – {exc}")
        return "\n".join(results) or "No tickers to fetch."


_BACKTEST_ARTIFACT_STORE: Dict[str, dict] = {}


def pop_stored_backtest_artifacts(session_id: str) -> Optional[dict]:
    """Pop backtest artifacts for a session (used by saved-portfolio API, not Crew)."""
    with _BACKTEST_STORE_LOCK:
        return _BACKTEST_ARTIFACT_STORE.pop(session_id, None)


def _peek_stored_backtest_artifacts(session_id: str) -> Optional[dict]:
    """Read artifacts without removing (for Emu prompt after pre-run MC)."""
    with _BACKTEST_STORE_LOCK:
        return _BACKTEST_ARTIFACT_STORE.get(session_id)


_BACKTEST_STORE_LOCK = threading.Lock()


def _sanitize_for_json(obj):
    """Recursively convert numpy/pandas types to plain Python for JSON."""
    import math
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    if hasattr(obj, "item"):
        val = obj.item()
        if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            return None
        return val
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


def _fmt_val(val: float) -> str:
    """Format portfolio value as short form with $ (e.g. $7.74M, $500K)."""
    if val is None or (isinstance(val, float) and (val != val or val == float("inf"))):
        return "$0"
    v = float(val)
    if abs(v) >= 1e9:
        return f"${v / 1e9:.2f}B"
    if abs(v) >= 1e6:
        return f"${v / 1e6:.2f}M"
    if abs(v) >= 1e3:
        return f"${v / 1e3:.1f}K"
    return f"${v:.2f}"


def _format_retirement_mc_facts_for_emu(artifacts: Optional[dict]) -> str:
    """Authoritative Monte Carlo bullet list for Emu (prevents upbeat text when depletion is severe)."""
    if not artifacts or not artifacts.get("is_retirement"):
        return (
            "FACTS (authoritative): Retirement Monte Carlo output is not available in this prompt. "
            "Do not invent depleted fraction, longevity, or success rates. Say the user should rely on "
            "the tables below, and suggest saving if they want to keep this allocation."
        )
    scenarios = artifacts.get("scenarios") or []
    if not scenarios:
        return (
            "FACTS (authoritative): No scenario row was stored. Do not invent metrics; refer to the UI tables."
        )
    mc = scenarios[0].get("monte_carlo") or {}
    mc_years = artifacts.get("mc_years")

    def _pct(x: object) -> Optional[str]:
        if x is None:
            return None
        try:
            return f"{float(x) * 100:.1f}%"
        except (TypeError, ValueError):
            return None

    def _yrs(x: object) -> Optional[str]:
        if x is None:
            return None
        try:
            v = float(x)
            if v != v:
                return None
            return f"{v:.1f} years"
        except (TypeError, ValueError):
            return None

    lines = [
        "FACTS (authoritative — your summary MUST agree with these numbers, not contradict them):"
    ]
    df = mc.get("depleted_fraction")
    pctf = _pct(df)
    if pctf is not None:
        lines.append(
            f"- Fraction of simulation paths that run out of money before the end of the horizon: {pctf}."
        )
    pos = mc.get("probability_of_success")
    pos_s = _pct(pos)
    if pos_s is not None:
        lines.append(
            f"- Estimated probability the portfolio lasts through the full modeled horizon: {pos_s}."
        )

    for label, key in (
        ("10th percentile (stress)", "portfolio_longevity_p10"),
        ("median (50th percentile)", "portfolio_longevity_p50"),
        ("90th percentile (favorable)", "portfolio_longevity_p90"),
    ):
        y = _yrs(mc.get(key))
        if y:
            cap = f" (horizon capped at {int(mc_years)} simulated years)" if mc_years else ""
            lines.append(f"- Years until balance hits zero, {label}: about {y}{cap}.")

    ad_parts = []
    for lab, key in (("P10", "age_at_depletion_p10"), ("P50", "age_at_depletion_p50"), ("P90", "age_at_depletion_p90")):
        x = mc.get(key)
        if x is not None:
            try:
                age_r = int(round(float(x)))
                if age_r == 100:
                    ad_parts.append(f"{lab} -")
                else:
                    ad_parts.append(f"{lab} ~{age_r}")
            except (TypeError, ValueError):
                pass
    if ad_parts:
        lines.append("- Age at portfolio depletion (paths that deplete): " + ", ".join(ad_parts) + ".")

    twr = mc.get("twr_p50")
    if twr is not None:
        try:
            lines.append(
                f"- Median path annualized time-weighted return (TWR): {float(twr) * 100:.2f}%."
            )
        except (TypeError, ValueError):
            pass
    p50_end = mc.get("portfolio_value_p50_end")
    if p50_end is not None:
        try:
            lines.append(
                f"- Median portfolio value at end of horizon (last simulated year): {_fmt_val(float(p50_end))}."
            )
        except (TypeError, ValueError):
            pass

    lines.append(
        "INTERPRETATION RULES: If depleted fraction is high (about 40% or more) or probability of success "
        "is low (about 50% or less), you MUST state clearly that many simulated paths run out of savings well "
        "before the end of the horizon — do NOT say the plan comfortably withstands spending, has strong longevity, "
        "or is robust through late life. If median years-to-depletion is only a handful of years, say that plainly. "
        "Only when depletion is low and success is high may you describe the outcome as relatively resilient (still "
        "not guaranteed). For weak outcomes, encourage saving this portfolio as a snapshot, then using scenario "
        "planning to explore changes — e.g. spending, other income sources like Social Security or pension, "
        "retirement timing, or allocation — rather than leaning only on vague 'reconsider withdrawal rate or allocation'. "
        "Charts and tables carry the detailed breakdown; invite the user to review the detailed metrics and asset breakdown."
    )
    return "\n".join(lines)


MIN_MONTHLY_ROWS = 240  # ~20 years of monthly data

# Crypto tickers use QQQ as proxy for backtest/MC (no crypto price history in data_output).
_CRYPTO_TICKERS: set[str] = set(_CRYPTO_TICKER_ALIAS.keys()) | set(_CRYPTO_TICKER_ALIAS.values())

# Spot-crypto / Bitcoin ETFs: same proxy as on-chain crypto (short or no monthly series in data_output).
_DIGITAL_ASSET_ETF_TICKERS: frozenset[str] = frozenset({
    "IBIT", "GBTC", "FBTC", "BITO", "ARKB", "BTCO", "EZBC", "HODL", "BRRR",
})

# Static mapping: tickers with short history -> correlated substitute with longer history
# Used when LLM lookup fails or is unavailable. Substitutes should have >= 240 monthly rows.
_TICKER_SUBSTITUTE_FALLBACK: dict[str, str] = {
    "VOO": "SPY", "VTI": "SPY", "IVV": "SPY", "ITOT": "SPY",
    "VXUS": "EFA", "IXUS": "EFA", "IEFA": "EFA", "VEA": "EFA",
    "BND": "VBMFX", "AGG": "VBMFX",
    "VGSH": "VBMFX", "GOVT": "VBMFX",
    "VGT": "SPY", "QQQ": "SPY",
    "TQQQ": "QQQ", "SPXL": "SPY", "UPRO": "SPY",  # leveraged ETFs -> underlying for data
    "VTSAX": "VTI", "DGRO": "VTI", "SCHD": "VTI",
}


def _count_csv_lines(path: Path) -> int:
    """Return number of data rows in a CSV (excluding header)."""
    if not path.exists():
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            return sum(1 for _ in f) - 1  # subtract header
    except Exception:
        return 0


def _find_correlated_ticker(ticker: str) -> str | None:
    """Find a correlated ticker with earlier inception (longer history).
    Tries static fallback first, then LLM."""
    # 1. Static fallback for common ETFs
    substitute = _TICKER_SUBSTITUTE_FALLBACK.get(ticker.upper())
    if substitute and substitute != ticker.upper():
        return substitute
    # 2. LLM lookup
    try:
        llm = build_llm("analyst")
        prompt = (
            f"For the ETF/stock ticker {ticker}, return ONE ticker symbol that is "
            "highly correlated and has an earlier inception date (longer price history). "
            "Examples: VOO->SPY (SPY inception 1993), VTI->SPY or ITOT. "
            "Return ONLY the substitute ticker symbol, nothing else (e.g. SPY)."
        )
        result = llm.call([{"role": "user", "content": prompt}])
        text = (result if isinstance(result, str) else str(getattr(result, "content", result) or "")).strip().upper()
        m = re.search(r"\b([A-Z]{2,5})\b", text)
        if m and m.group(1) != ticker.upper():
            return m.group(1)
    except Exception as e:
        logger.warning("LLM correlated ticker lookup failed for %s: %s", ticker, e)
    return None


def _fetch_ticker_data(ticker: str) -> bool:
    """Fetch monthly price data for ticker. Returns True if file exists after fetch."""
    if monthly_csv_exists(DATA_OUTPUT_DIR, ticker):
        return True
    try:
        proc = subprocess.run(
            [sys.executable, str(FETCH_SCRIPT), "--symbol", ticker, "--insecure"],
            check=True,
            timeout=120,
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
        )
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or "").strip()[:800] or (exc.stdout or "").strip()[:800]
        logger.warning("Fetch failed for %s (exit %s): %s", ticker, exc.returncode, err)
    except Exception as exc:
        logger.warning("Fetch failed for %s: %s", ticker, exc)
    return monthly_csv_exists(DATA_OUTPUT_DIR, ticker)


def _ensure_sufficient_data(
    tickers: list[str],
    data_output_dir: Path,
) -> dict[str, str]:
    """Check each ticker's CSV has >= 240 lines. For short ones, find correlated
    substitute, fetch it, return mapping {original: substitute}. UI shows original.
    Crypto tickers always use QQQ as proxy for backtest/MC."""
    substitution: dict[str, str] = {}
    for ticker in tickers:
        # Crypto / digital-asset ETFs: use QQQ as proxy (no long monthly history in data_output)
        u = ticker.upper()
        if u in _CRYPTO_TICKERS or u in _DIGITAL_ASSET_ETF_TICKERS:
            substitution[ticker] = "QQQ"
            logger.info("Using QQQ for %s (backtest/MC proxy)", ticker)
            continue
        path = monthly_csv_path(data_output_dir, ticker)
        if not path.exists():
            _fetch_ticker_data(ticker)
            path = monthly_csv_path(data_output_dir, ticker)
        n = _count_csv_lines(path)
        if n >= MIN_MONTHLY_ROWS:
            continue
        substitute = _find_correlated_ticker(ticker)
        if not substitute:
            logger.warning("No correlated substitute for %s (has %d rows)", ticker, n)
            continue
        if not _fetch_ticker_data(substitute):
            logger.warning("Failed to fetch substitute %s for %s", substitute, ticker)
            continue
        sub_path = monthly_csv_path(data_output_dir, substitute)
        if sub_path.exists() and _count_csv_lines(sub_path) >= MIN_MONTHLY_ROWS:
            substitution[ticker] = substitute
            logger.info("Using %s for %s (insufficient data: %d rows)", substitute, ticker, n)
    return substitution


def _age_completed_years_from_dob(birth_year: int, birth_month: int, today) -> int:
    """Completed age in years; birthday assumed on the 1st of birth_month (matches intake month-only DOB)."""
    age = today.year - birth_year
    if (today.month, today.day) < (birth_month, 1):
        age -= 1
    return age


def _normalize_sector_industry_weights(raw: object) -> Dict[str, float]:
    """Normalize label->weight maps (asset class or GICS sector names) to decimals summing to 1.0."""
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, float] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not k.strip():
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
    if total > 1.5:
        out = {kk: vv / 100.0 for kk, vv in out.items()}
        total = sum(out.values())
    if total <= 0:
        return {}
    return {kk: vv / total for kk, vv in out.items()}


def _merge_sector_industry_maps(
    chosen: Optional[Dict[str, float]], from_artifact: Optional[Dict[str, float]]
) -> Dict[str, float]:
    """Start from Quala/Panda session weights; Ana/Emu JSON updates/overrides keys. Renormalize."""
    ch = chosen if isinstance(chosen, dict) else None
    art = from_artifact if isinstance(from_artifact, dict) else None
    if not ch:
        return _normalize_sector_industry_weights(art or {})
    if not art:
        return dict(ch)
    merged = dict(ch)
    merged.update(art)
    return _normalize_sector_industry_weights(merged)


def _run_retirement_backtest_and_store(
    session_id: str,
    portfolio_weights: Dict[str, float],
    intake_ctx: Optional[object],
    portfolio_sectors: Optional[Dict[str, float]] = None,
    portfolio_industries: Optional[Dict[str, float]] = None,
) -> None:
    """Run retirement Monte Carlo and store artifact for frontend (bar chart, spaghetti, MC table, YoY)."""
    from backtesting.backtesting_retirement.runner import run_retirement_backtest

    preferred_sector_weights = get_preferred_portfolio_sector_weights(portfolio_weights)
    if preferred_sector_weights:
        portfolio_industries = preferred_sector_weights

    initial_value = 1_000_000.0
    monthly_withdrawal = 5000.0
    inflation_rate = 0.03
    max_years = 50
    retirement_age = None
    portfolio_window_ea: Optional[int] = None  # set when what-if portfolio age window overrides MC end age (UI / max_age_assumed)
    upcoming_expenses: list = []
    simulation_calendar_year = None
    simulation_calendar_month = 1
    current_age = None
    if intake_ctx is not None:
        initial_value = getattr(intake_ctx, "initial_value", initial_value) or initial_value
        base_monthly = getattr(intake_ctx, "retirement_monthly_target", monthly_withdrawal) or monthly_withdrawal
        # Do not use truthiness on rate: 0% is valid. Default when unset is 0% (no withdrawal gross-up).
        _raw_tax = getattr(intake_ctx, "retirement_effective_tax_rate", None)
        try:
            tax_dec = float(_raw_tax) if _raw_tax is not None else 0.0
        except (TypeError, ValueError):
            tax_dec = 0.0
        tax_dec = max(0.0, min(0.70, tax_dec))
        # Portfolio gross withdrawal: inflation-adjusted monthly expense × (1 + effective tax rate).
        monthly_withdrawal = base_monthly * (1.0 + tax_dec)
        ir = getattr(intake_ctx, "inflation_rate", None)
        inflation_rate = ir if ir is not None else inflation_rate
        import datetime

        now = datetime.datetime.now()
        birth_dates = getattr(intake_ctx, "birth_dates", None)
        rs = getattr(intake_ctx, "retirement_status", None) or ""
        both_retired = rs == "both_retired"
        horizon = getattr(intake_ctx, "horizon_years", None)
        if both_retired:
            horizon = 0
        longevity = getattr(intake_ctx, "longevity_years", None)
        # Default MC length before we know retirement_age (fallback if no DOB/horizon).
        if longevity is not None and longevity > 0:
            max_years = int(longevity)
        if horizon is not None:
            simulation_calendar_year = now.year + int(horizon)
        else:
            simulation_calendar_year = now.year
        # User's DOB only (birth_dates[0]). Simulation year 0 age = user_age_now + horizon
        # (e.g. self_retired + partner retires in 15y: 47+15=62). MC then runs through user age 100 only.
        current_age = None
        if birth_dates and horizon is not None:
            bd0 = birth_dates[0]
            birth_year = bd0[0]
            birth_month = bd0[1] if len(bd0) > 1 else 6
            user_age_now = _age_completed_years_from_dob(birth_year, birth_month, now)
            current_age = user_age_now
            retirement_age = user_age_now + int(horizon)
        raw_exp = getattr(intake_ctx, "upcoming_expenses", None) or []
        for e in raw_exp:
            if isinstance(e, (list, tuple)) and len(e) >= 2:
                upcoming_expenses.append((float(e[0]), float(e[1])))
            elif isinstance(e, dict):
                upcoming_expenses.append(
                    (
                        float(e.get("years", e.get("years_from_start", 0))),
                        float(e.get("amount", e.get("value", 0))),
                    )
                )

    # Decumulation: optional portfolio age window (what-if) overrides horizon retirement age, max_years, and chart/MC age axis.
    # Otherwise span default [retirement_age, 100] => max_years = 100 - retirement_age (not intake longevity from "today").
    if intake_ctx is not None and retirement_age is not None:
        rpa = getattr(intake_ctx, "retirement_portfolio_start_age", None)
        rpe = getattr(intake_ctx, "retirement_portfolio_end_age", None)
        if rpa is not None or rpe is not None:
            try:
                ra = int(retirement_age)
                sa = int(float(rpa)) if rpa is not None else ra
                ea = int(float(rpe)) if rpe is not None else 100
                sa = max(0, min(sa, 120))
                ea = max(sa, min(ea, 120))
                max_years = max(1, min(100, ea - sa + 1))
                retirement_age = sa
                portfolio_window_ea = ea
            except (TypeError, ValueError):
                pass
        else:
            span = max(0, 100 - int(retirement_age))
            max_years = max(1, min(100, span))
    elif retirement_age is not None:
        span = max(0, 100 - int(retirement_age))
        max_years = max(1, min(100, span))
    else:
        max_years = min(max_years, 100)
        if max_years < 1:
            max_years = 1

    # Per-year income for MC: raw amount at each age, inflated to that year (age = retirement_age + y).
    # Rows may set yoy_annual_pct so the pre-inflation monthly amount compounds each year in the window.
    from backend.intake_parser import (
        monthly_recurring_total_at_age_with_yoy,
        parse_retirement_income_freeform,
    )

    yearly_income_monthly: Optional[List[float]] = None
    inc_rows = getattr(intake_ctx, "retirement_income_rows", None) if intake_ctx else None
    has_inc_rows = isinstance(inc_rows, list) and any(
        isinstance(r, dict) and float(r.get("monthly", 0) or 0) > 0 for r in inc_rows
    )
    if has_inc_rows and retirement_age is not None:
        yearly_income_monthly = [
            monthly_recurring_total_at_age_with_yoy(
                inc_rows, int(retirement_age) + y, current_age=current_age
            )
            * ((1 + inflation_rate) ** y)
            for y in range(max_years)
        ]
    elif intake_ctx:
        inc_ff = str(getattr(intake_ctx, "retirement_income_freeform", None) or "").strip()
        if inc_ff and retirement_age is not None:
            bd = getattr(intake_ctx, "birth_dates", None) or []
            by = bd[0][0] if bd else None
            bm = bd[0][1] if bd and len(bd[0]) > 1 else 6
            income_ff_rows = parse_retirement_income_freeform(inc_ff, by, bm)
            if income_ff_rows:
                yearly_income_monthly = [
                    monthly_recurring_total_at_age_with_yoy(
                        income_ff_rows, int(retirement_age) + y, current_age=current_age
                    )
                    * ((1 + inflation_rate) ** y)
                    for y in range(max_years)
                ]

    # Per-year misc spending for MC (added to base withdrawal each year; correct age windows)
    yearly_misc_spending_monthly: Optional[List[float]] = None
    misc_rows = getattr(intake_ctx, "retirement_misc_spending_rows", None) if intake_ctx else None
    has_misc_rows = isinstance(misc_rows, list) and any(
        isinstance(r, dict) and float(r.get("monthly", 0) or 0) > 0 for r in misc_rows
    )
    if has_misc_rows and retirement_age is not None:
        yearly_misc_spending_monthly = [
            monthly_recurring_total_at_age_with_yoy(
                misc_rows, int(retirement_age) + y, current_age=current_age
            )
            * ((1 + inflation_rate) ** y)
            for y in range(max_years)
        ]
    elif intake_ctx:
        misc_ff = str(getattr(intake_ctx, "retirement_misc_spending_freeform", None) or "").strip()
        if misc_ff and retirement_age is not None:
            bd = getattr(intake_ctx, "birth_dates", None) or []
            by = bd[0][0] if bd else None
            bm = bd[0][1] if bd and len(bd[0]) > 1 else 6
            misc_ff_rows = parse_retirement_income_freeform(misc_ff, by, bm)
            if misc_ff_rows:
                yearly_misc_spending_monthly = [
                    monthly_recurring_total_at_age_with_yoy(
                        misc_ff_rows, int(retirement_age) + y, current_age=current_age
                    )
                    * ((1 + inflation_rate) ** y)
                    for y in range(max_years)
                ]

    discretionary_spend_if_prior_year_return = None
    if intake_ctx is not None:
        _dm = getattr(intake_ctx, "retirement_discretionary_monthly", None)
        _dy = getattr(intake_ctx, "retirement_discretionary_in_year", None)
        _dp = getattr(intake_ctx, "retirement_discretionary_min_prior_year_return_pct", None)
        _dsa = getattr(intake_ctx, "retirement_discretionary_start_age", None)
        _dea = getattr(intake_ctx, "retirement_discretionary_end_age", None)
        if _dm is not None and _dp is not None:
            try:
                dmf = float(_dm)
                dpf = float(_dp)
                if dmf > 0:
                    if _dy is not None and str(_dy).strip() != "":
                        dyi = int(float(_dy))
                        if dyi >= 2:
                            discretionary_spend_if_prior_year_return = (dmf, dyi, dpf)
                    else:
                        dsa = dea = None
                        if (
                            _dsa is not None
                            and _dea is not None
                            and str(_dsa).strip() != ""
                            and str(_dea).strip() != ""
                        ):
                            dsa = int(float(_dsa))
                            dea = int(float(_dea))
                            if 0 <= dsa <= 120 and 0 <= dea <= 120 and dsa <= dea:
                                discretionary_spend_if_prior_year_return = (dmf, dpf, dsa, dea)
                            else:
                                discretionary_spend_if_prior_year_return = (dmf, dpf)
                        else:
                            discretionary_spend_if_prior_year_return = (dmf, dpf)
            except (TypeError, ValueError):
                discretionary_spend_if_prior_year_return = None

    try:
        out = run_retirement_backtest(
            portfolio_weights=portfolio_weights,
            data_output_dir=DATA_OUTPUT_DIR,
            initial_value=initial_value,
            monthly_withdrawal=monthly_withdrawal,
            inflation_rate=inflation_rate,
            max_years=max_years,
            n_sims=5000,
            fetch_if_missing=True,
            retirement_age=retirement_age,
            yearly_income_monthly=yearly_income_monthly,
            yearly_misc_spending_monthly=yearly_misc_spending_monthly,
            upcoming_expenses=upcoming_expenses or None,
            simulation_calendar_year=simulation_calendar_year,
            simulation_calendar_month=simulation_calendar_month,
            discretionary_spend_if_prior_year_return=discretionary_spend_if_prior_year_return,
        )
    except Exception as exc:
        logger.warning("Retirement backtest failed: %s", exc)
        return

    metrics = out["metrics"]
    summary_paths = out.get("summary_paths", {})
    summary_yearly_price = out.get("summary_yearly_price", {})
    summary_yearly_yield = out.get("summary_yearly_yield", {})
    summary_yearly_twr = out.get("summary_yearly_twr", {})
    paths_sample = out.get("paths_sample")
    paths_sample_years = out.get("paths_sample_years")
    data_start = out.get("data_start")
    data_end = out.get("data_end")
    import numpy as np

    yearly_price_gain_paths = out.get("yearly_price_gain")
    yearly_yield_gain_paths = out.get("yearly_yield_gain")

    # Build summary_paths as lists for JSON (p50 at year boundaries for bar chart)
    sp_p10 = summary_paths.get("p10")
    sp_p50 = summary_paths.get("p50")
    sp_p90 = summary_paths.get("p90")
    sp_mean = summary_paths.get("mean")
    def _yearly_from_path(ser):
        out = []
        if ser is not None:
            for year in range(max_years + 1):
                idx = min(year * 12, len(ser) - 1)
                if idx >= 0:
                    try:
                        out.append(float(ser.iloc[idx]))
                    except (IndexError, TypeError):
                        out.append(0.0)
        return out

    yearly_p50 = _yearly_from_path(sp_p50)
    yearly_p10 = _yearly_from_path(sp_p10) or yearly_p50
    yearly_p90 = _yearly_from_path(sp_p90) or yearly_p50

    # YoY table: Year | Portfolio(P10/P50/P90) | Yield $(P10/P50/P90) | TWR(P10/P50/P90) | Outflow | Net(P10/P50/P90)
    # Outflow = same formula as MC: sum of 12 monthly withdrawals with monthly compounding.
    # MC: withdrawal[t] = base * (1+monthly_inflation)^t; year-y total = base * ((1+mi)^12-1)/mi * (1+mi)^(12*(y-1)).
    # When inflation=0: 12*monthly (e.g. 20K/mo → 240K/yr).
    monthly_inflation = (1 + inflation_rate) ** (1 / 12) - 1
    if abs(monthly_inflation) < 1e-10:
        year1_outflow = monthly_withdrawal * 12.0
        yearly_outflows = [year1_outflow] * max_years
    else:
        first_year_sum = monthly_withdrawal * ((1 + monthly_inflation) ** 12 - 1) / monthly_inflation
        yearly_outflows = [
            first_year_sum * (1 + monthly_inflation) ** (12 * (y - 1))
            for y in range(1, max_years + 1)
        ]
    lump_by_year = out.get("metadata", {}).get("one_time_lump_by_table_year") or []

    show_yoy_cashflow_cols = False
    income_ff_rows: list = []
    misc_ff_rows: list = []
    table_retirement_age = retirement_age  # may use default for table when retirement_age is None
    if intake_ctx is not None:
        inc_rows = getattr(intake_ctx, "retirement_income_rows", None)
        misc_rows = getattr(intake_ctx, "retirement_misc_spending_rows", None)
        has_inc = isinstance(inc_rows, list) and any(
            isinstance(r, dict) and float(r.get("monthly", 0) or 0) > 0 for r in inc_rows
        )
        has_misc = isinstance(misc_rows, list) and any(
            isinstance(r, dict) and float(r.get("monthly", 0) or 0) > 0 for r in misc_rows
        )
        if has_inc or has_misc:
            income_ff_rows = inc_rows if has_inc else []
            misc_ff_rows = misc_rows if has_misc else []
            show_yoy_cashflow_cols = True
            if table_retirement_age is None:
                table_retirement_age = 65  # fallback so we can compute age_row for the table
        elif retirement_age is not None:
            inc_ff = str(getattr(intake_ctx, "retirement_income_freeform", None) or "").strip()
            misc_ff = str(getattr(intake_ctx, "retirement_misc_spending_freeform", None) or "").strip()
            if inc_ff or misc_ff:
                from backend.intake_parser import parse_retirement_income_freeform

                bd = getattr(intake_ctx, "birth_dates", None) or []
                by = bd[0][0] if bd else None
                bm = bd[0][1] if bd and len(bd[0]) > 1 else 6
                income_ff_rows = parse_retirement_income_freeform(inc_ff, by, bm) if inc_ff else []
                misc_ff_rows = parse_retirement_income_freeform(misc_ff, by, bm) if misc_ff else []
                show_yoy_cashflow_cols = True

    def _series_at(d, k, idx):
        s = d.get(k)
        return float(s.iloc[idx]) if s is not None and idx < len(s) else None

    # Year-by-Year: portfolio / yield $ / growth $ use MC quantiles over all paths (zeros included).
    # Net uses quantile of pathwise (yield$ + price$ − this row's outflow), not sum of separate quantiles.
    retirement_yearly_table = []
    for year in range(max_years + 1):
        curr_p10 = yearly_p10[year] if year < len(yearly_p10) else None
        curr_p50 = yearly_p50[year] if year < len(yearly_p50) else None
        curr_p90 = yearly_p90[year] if year < len(yearly_p90) else None
        if curr_p50 is None:
            break
        outflow = yearly_outflows[year - 1] if year >= 1 else 0
        if year >= 1 and isinstance(lump_by_year, list) and year < len(lump_by_year):
            outflow = float(outflow) + float(lump_by_year[year] or 0)
        additional_income = 0.0
        additional_spend = 0.0
        if show_yoy_cashflow_cols and year >= 1 and table_retirement_age is not None:
            # Age displayed for this row (frontend uses retAge + year); must align for correct end_age cutoff
            age_row = int(table_retirement_age) + int(year)
            base_income = monthly_recurring_total_at_age_with_yoy(
                income_ff_rows, age_row, current_age=current_age
            ) * 12.0
            base_spend = monthly_recurring_total_at_age_with_yoy(
                misc_ff_rows, age_row, current_age=current_age
            ) * 12.0
            inflation_mult = (1 + inflation_rate) ** max(0, year - 1)
            additional_income = base_income * inflation_mult
            additional_spend = base_spend * inflation_mult
        # Outflow = base + additional_spend - additional_income (misc is per-year, not folded into base)
        portfolio_outflow = float(outflow) + float(additional_spend) - float(additional_income)
        yld_p10 = yld_p50 = yld_p90 = None
        twr_p10 = twr_p50 = twr_p90 = None
        price_p10 = price_p50 = price_p90 = None
        if year >= 1 and summary_yearly_yield:
            y_idx = year - 1
            yld_p10 = _series_at(summary_yearly_yield, "p10", y_idx)
            yld_p50 = _series_at(summary_yearly_yield, "p50", y_idx)
            yld_p90 = _series_at(summary_yearly_yield, "p90", y_idx)
        if year >= 1 and summary_yearly_twr:
            y_idx = year - 1
            twr_p10 = _series_at(summary_yearly_twr, "p10", y_idx)
            twr_p50 = _series_at(summary_yearly_twr, "p50", y_idx)
            twr_p90 = _series_at(summary_yearly_twr, "p90", y_idx)
        if year >= 1 and summary_yearly_price:
            y_idx = year - 1
            price_p10 = _series_at(summary_yearly_price, "p10", y_idx)
            price_p50 = _series_at(summary_yearly_price, "p50", y_idx)
            price_p90 = _series_at(summary_yearly_price, "p90", y_idx)
        if year >= 1:
            o = float(portfolio_outflow)
            y_idx_net = year - 1
            pg = yearly_price_gain_paths
            yg = yearly_yield_gain_paths
            if (
                pg is not None
                and yg is not None
                and hasattr(pg, "shape")
                and y_idx_net < int(pg.shape[1])
            ):
                col = pg[:, y_idx_net].astype(np.float64) + yg[:, y_idx_net].astype(np.float64) - o
                net_p10 = float(np.quantile(col, 0.10))
                net_p50 = float(np.quantile(col, 0.50))
                net_p90 = float(np.quantile(col, 0.90))
            else:
                net_p10 = (yld_p10 if yld_p10 is not None else 0.0) + (price_p10 if price_p10 is not None else 0.0) - o
                net_p50 = (yld_p50 if yld_p50 is not None else 0.0) + (price_p50 if price_p50 is not None else 0.0) - o
                net_p90 = (yld_p90 if yld_p90 is not None else 0.0) + (price_p90 if price_p90 is not None else 0.0) - o
        else:
            net_p10 = net_p50 = net_p90 = None
        port_p10 = float(curr_p10) if curr_p10 is not None else 0.0
        port_p50 = float(curr_p50) if curr_p50 is not None else 0.0
        port_p90 = float(curr_p90) if curr_p90 is not None else 0.0
        row_yoy = {
            "year": year,
            "portfolio_p10": port_p10,
            "portfolio_p50": port_p50,
            "portfolio_p90": port_p90,
            "yield_p10": yld_p10,
            "yield_p50": yld_p50,
            "yield_p90": yld_p90,
            "price_p10": price_p10,
            "price_p50": price_p50,
            "price_p90": price_p90,
            "twr_p10": twr_p10,
            "twr_p50": twr_p50,
            "twr_p90": twr_p90,
            "outflow": portfolio_outflow if year >= 1 else None,
            "net_p10": net_p10,
            "net_p50": net_p50,
            "net_p90": net_p90,
        }
        if show_yoy_cashflow_cols:
            row_yoy["additional_yearly_income"] = additional_income if year >= 1 else None
            row_yoy["additional_annual_spend"] = additional_spend if year >= 1 else None
        retirement_yearly_table.append(row_yoy)

    # End-of-horizon portfolio: last Year-by-Year row (MC quantile balances)
    p10_ser = summary_paths.get("p10")
    p90_ser = summary_paths.get("p90")
    last_idx = min(max_years * 12, len(sp_p50) - 1) if sp_p50 is not None else 0
    if retirement_yearly_table:
        last_row = retirement_yearly_table[-1]
        p10_end = float(last_row.get("portfolio_p10") or 0.0)
        p50_end = float(last_row.get("portfolio_p50") or 0.0)
        p90_end = float(last_row.get("portfolio_p90") or 0.0)
    else:
        p10_end = float(p10_ser.iloc[last_idx]) if p10_ser is not None and last_idx < len(p10_ser) else 0.0
        p50_end = float(sp_p50.iloc[last_idx]) if sp_p50 is not None and last_idx < len(sp_p50) else 0.0
        p90_end = float(p90_ser.iloc[last_idx]) if p90_ser is not None and last_idx < len(p90_ser) else 0.0

    # Normalize weights for composition
    total = sum(portfolio_weights.values()) or 1.0
    composition = {k: v / total for k, v in portfolio_weights.items()}

    def _to_list(s):
        if s is None:
            return []
        if hasattr(s, "tolist"):
            return s.tolist()
        return list(s)

    if portfolio_window_ea is not None:
        max_age_assumed = float(portfolio_window_ea)
    elif retirement_age is not None:
        max_age_assumed = float(int(retirement_age) + int(max_years))
    else:
        max_age_assumed = None

    # Build scenario for frontend (same shape as growth but retirement-specific)
    scenario = {
        "label": "Retirement",
        "scenario": "Retirement",
        "portfolio": composition,
        "summary_paths": {
            "mean": _to_list(sp_mean),
            "p10": _to_list(summary_paths.get("p10")),
            "p50": _to_list(sp_p50),
            "p90": _to_list(summary_paths.get("p90")),
        },
        "paths_sample": paths_sample or [],
        "paths_sample_years": paths_sample_years or [],
        "monte_carlo": {
            "max_age_assumed": max_age_assumed,
            "depleted_fraction": metrics.get("depleted_fraction"),
            "probability_of_success": metrics.get("probability_of_success"),
            "portfolio_longevity_p10": metrics.get("portfolio_longevity_p10"),
            "portfolio_longevity_p50": metrics.get("portfolio_longevity_p50"),
            "portfolio_longevity_p90": metrics.get("portfolio_longevity_p90"),
            "magnitude_of_failure_p50": metrics.get("magnitude_of_failure_p50"),
            "magnitude_of_failure_p90": metrics.get("magnitude_of_failure_p90"),
            "goal_completion_p10": metrics.get("goal_completion_p10"),
            "goal_completion_p50": metrics.get("goal_completion_p50"),
            "goal_completion_p90": metrics.get("goal_completion_p90"),
            "withdrawal_rate_year0": metrics.get("withdrawal_rate_year0"),
            "withdrawal_rates_by_year": metrics.get("withdrawal_rates_by_year"),
            "age_at_depletion_p10": metrics.get("age_at_depletion_p10"),
            "age_at_depletion_p50": metrics.get("age_at_depletion_p50"),
            "age_at_depletion_p90": metrics.get("age_at_depletion_p90"),
            "twr_p10": metrics.get("twr_p10"),
            "twr_p50": metrics.get("twr_p50"),
            "twr_p90": metrics.get("twr_p90"),
            "portfolio_value_p10_end": p10_end,
            "portfolio_value_p50_end": p50_end,
            "portfolio_value_p90_end": p90_end,
            "portfolio_yield_mean_annual": metrics.get("portfolio_yield_mean_annual"),
            "portfolio_log_return_mean_annual": metrics.get("portfolio_log_return_mean_annual"),
            "data_start": data_start,
            "data_end": data_end,
        },
    }

    intake_for_ui = {}
    if intake_ctx is not None:
        import datetime as _dt
        _now = _dt.datetime.now()
        intake_for_ui = {
            "initial_value": getattr(intake_ctx, "initial_value", None),
            "retirement_monthly_target": getattr(intake_ctx, "retirement_monthly_target", None),
            "longevity_years": getattr(intake_ctx, "longevity_years", None),
            "start_month": _now.month,
            "start_year": _now.year,
        }
        _bsr_ret = _upcoming_expenses_tuples_to_big_spending_rows(intake_ctx)
        if _bsr_ret:
            intake_for_ui["big_spending_rows"] = _bsr_ret

    retirement_age_for_ui = int(retirement_age) if retirement_age is not None else None
    asset_correlations = out.get("asset_correlations") or {"tickers": [], "rows": []}
    artifact_body: Dict[str, object] = {
        "is_retirement": True,
        "portfolio_composition": composition,
        "retirement_composition": composition,
        "scenarios": [scenario],
        "retirement_yearly_table": retirement_yearly_table,
        "retirement_yoy_cashflow_columns": show_yoy_cashflow_cols,
        "retirement_age": retirement_age_for_ui,
        "one_time_lump_by_table_year": lump_by_year,
        "mc_years": max_years,
        "mc_sims": 5000,
        "data_date_range": f"{data_start} to {data_end}" if data_start and data_end else None,
        "intake": intake_for_ui,
        "asset_correlations": asset_correlations,
    }
    if portfolio_sectors:
        artifact_body["portfolio_sectors"] = dict(portfolio_sectors)
    if portfolio_industries:
        artifact_body["portfolio_industries"] = dict(portfolio_industries)
    apply_taxonomy_to_artifact(artifact_body)
    if session_id and composition:
        try:
            from backend.main import attach_portfolio_breakdown_tickers_to_artifacts

            attach_portfolio_breakdown_tickers_to_artifacts(
                artifact_body,
                session_id,
                style_quala_or_panda="panda",
            )
        except Exception as exc:
            logger.warning("Retirement chart ticker rollups skipped: %s", exc)
    artifact = _sanitize_for_json(artifact_body)

    with _BACKTEST_STORE_LOCK:
        _BACKTEST_ARTIFACT_STORE[session_id] = artifact
        logger.info("Stored retirement artifact for session_id=%s", session_id[:12] if session_id else None)


class RunBacktestTool(BaseTool):
    name: str = "run_backtest"
    description: str = (
        "Run backtesting and Monte Carlo simulations for the growth portfolio. "
        "Input: (1) Flat ticker:weight JSON, OR (2) "
        '{{\"accumulation\": {{\"tickers\": {{\"VTI\": 0.4, \"BND\": 0.3}}, '
        "\"sectors\": {{\"US Stocks\": 0.6, \"Bonds\": 0.4}}, "
        '\"industries\": {{\"Technology\": 0.25, \"Other\": 0.45, ...}}}}}}. '
        "Only \"tickers\" are used for returns. Optional \"sectors\" stores **asset class** weights only "
        "(US Stocks, International Stocks, Bonds, Commodities, Digital Assets, Other; sum 1.0). "
        "Optional \"industries\" stores **sector** weights (GICS-style names; Technology, Financials, "
        "Communication Services, Consumer Discretionary, Consumer Staples, Health Care, Industrials, "
        "Energy, Utilities, Materials, Real Estate, Other; sum 1.0). "
        "Runs the no-rebalancing scenario only."
    )

    def _run(self, portfolio_json: str) -> str:
        def _to_float(x):
            if isinstance(x, (int, float)):
                return float(x)
            if isinstance(x, dict):
                return float(x.get("weight", x.get("value", x.get("amount", 0))))
            try:
                return float(x)
            except (TypeError, ValueError):
                return 0.0

        def _normalize_portfolio_weights(weight_map: dict) -> dict:
            """Return ticker -> weight with sum 1.0. If values look like percentages (sum > 1.5), scale by 100 first."""
            if not weight_map:
                return {}
            out = {k.strip().upper(): _to_float(v) for k, v in weight_map.items() if isinstance(k, str) and k.strip()}
            total = sum(out.values())
            if total <= 0:
                return {}
            if total > 1.5:
                # Likely percentages (e.g. 60, 40) -> convert to fractions
                out = {k: v / 100.0 for k, v in out.items()}
                total = sum(out.values())
            if total <= 0:
                return {}
            return {k: v / total for k, v in out.items()}

        try:
            parsed = json.loads(portfolio_json)
            if not isinstance(parsed, dict) or not parsed:
                return "Error: input must be a non-empty JSON object."

            # Parse into growth portfolio only (no retirement)
            portfolios_to_run: list[tuple[str, dict]] = []
            if "accumulation" in parsed and isinstance(parsed["accumulation"], dict) and parsed["accumulation"]:
                acc = parsed["accumulation"]
                # Support both {"accumulation": {"VTI": 0.6, "BND": 0.4}} and {"accumulation": {"tickers": {"VTI": 60, "BND": 40}}}
                ticker_map = acc.get("tickers") if isinstance(acc.get("tickers"), dict) else acc
                portfolio = _normalize_portfolio_weights(ticker_map)
                if portfolio:
                    portfolios_to_run.append(("growth", portfolio))
            elif not any(k in parsed for k in ("accumulation", "retirement")):
                # Single portfolio (legacy: flat ticker:weight)
                portfolio = _normalize_portfolio_weights(parsed)
                if not portfolio:
                    return "Error: weights must sum to a positive value."
                portfolios_to_run.append(("growth", portfolio))

            if not portfolios_to_run:
                return "Error: no valid portfolio(s) to run."

            extra_sectors: Dict[str, float] = {}
            extra_industries: Dict[str, float] = {}
            if "accumulation" in parsed and isinstance(parsed["accumulation"], dict):
                _acc0 = parsed["accumulation"]
                if isinstance(_acc0.get("sectors"), dict):
                    extra_sectors = _normalize_sector_industry_weights(_acc0["sectors"])
                if isinstance(_acc0.get("industries"), dict):
                    extra_industries = _normalize_sector_industry_weights(_acc0["industries"])

            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            from backtesting.driver import run_backtests

            intake_context = None
            session_id = getattr(_BACKTEST_SESSION_ID, "session_id", None)
            if session_id:
                with _INTAKE_STORE_LOCK:
                    intake_context = INTAKE_CONTEXT_STORE.get(session_id)
            if intake_context:
                logger.info("RunBacktestTool: session_id=%s initial_value=%s", session_id, intake_context.initial_value)
            else:
                logger.warning("RunBacktestTool: no intake_context for session_id=%s; using default initial_value=1.0", session_id)

            # Step 4: Check data sufficiency (>= 240 lines). If short, use correlated
            # substitute for backtest/MC; UI still shows original tickers.
            all_tickers = list({t for _, p in portfolios_to_run for t in p})
            ticker_substitution = _ensure_sufficient_data(all_tickers, DATA_OUTPUT_DIR)

            # Pre-flight: verify all tickers and benchmark have loadable data (after substitution + crypto alias)
            missing = []
            for t in all_tickers:
                load_ticker = resolve_load_ticker(t, ticker_substitution)
                if not monthly_csv_exists(DATA_OUTPUT_DIR, load_ticker):
                    missing.append(t)
            if missing:
                logger.info(
                    "RunBacktestTool: portfolio CSVs still missing after ensure step: %s; retrying Alpha Vantage",
                    missing,
                )
                retry_missing: list[str] = []
                for t in missing:
                    load_ticker = resolve_load_ticker(t, ticker_substitution)
                    if not _fetch_ticker_data(load_ticker):
                        retry_missing.append(t)
                missing = retry_missing
            for bench_sym in ("SPY", "AGG"):
                if not monthly_csv_exists(DATA_OUTPUT_DIR, bench_sym):
                    _fetch_ticker_data(bench_sym)
                if not monthly_csv_exists(DATA_OUTPUT_DIR, bench_sym):
                    missing.append(f"{bench_sym} (60/40 benchmark)")
            if missing:
                return (
                    f"Missing price data for: {', '.join(missing)}. "
                    "Use fetch_ticker_data with those tickers first, or use a portfolio "
                    "with tickers that have data (e.g. VTI, BND, VXUS, QQQ, SPY)."
                )

            all_scenarios_for_ui: list[dict] = []
            all_results_text: list[str] = []
            observation_parts: list[str] = []  # short summary for Ana's next LLM turn
            primary_portfolio = portfolios_to_run[0][1]
            preferred_sector_weights = get_preferred_portfolio_sector_weights(primary_portfolio)
            if preferred_sector_weights:
                extra_industries = preferred_sector_weights
            mc_years = None
            frequency = None

            for label, portfolio in portfolios_to_run:
                logger.info(
                    "RunBacktestTool: portfolio weights (sum=%.4f) %s",
                    sum(portfolio.values()),
                    {k: round(v, 4) for k, v in portfolio.items()},
                )
                results = run_backtests(
                    portfolio=portfolio,
                    data_output_dir=DATA_OUTPUT_DIR,
                    intake_context=intake_context,
                    ticker_substitution=ticker_substitution or None,
                    scenarios_filter=["none"],
                )
                mc_years = results.get("mc_years", results["years"])
                frequency = results["frequency"]

                # Write same consolidated output as driver for comparison with portfolio_output_all.csv
                try:
                    import pandas as pd
                    consolidated = results.get("consolidated_rows", [])
                    if consolidated:
                        out_path = MODEL_OUTPUT_DIR / "portfolio_output_all.csv"
                        pd.DataFrame(consolidated).to_csv(out_path, index=False)
                        logger.info("RunBacktestTool: wrote %s for comparison", out_path.name)
                except Exception as e:
                    logger.warning("RunBacktestTool: could not write consolidated CSV: %s", e)

                for scenario in results["scenarios"]:
                    m = scenario["metrics"]
                    mc = scenario["monte_carlo"]
                    sc = scenario["scenario"]
                    if sc == "none":
                        scenario_name = "No Rebalancing" if len(portfolios_to_run) == 1 else f"No Rebalancing ({label})"
                    elif sc == "monthly":
                        scenario_name = "Monthly Rebalancing" if len(portfolios_to_run) == 1 else f"Monthly Rebalancing ({label})"
                    elif sc == "adaptive_5_25":
                        scenario_name = "Adaptive 5/25" if len(portfolios_to_run) == 1 else f"Adaptive 5/25 ({label})"
                    else:
                        scenario_name = f"{label}_{sc}"
                    all_scenarios_for_ui.append({
                        "scenario": scenario_name,
                        "metrics": {k: m.get(k) for k in (
                            "cagr", "annualized_volatility", "sharpe_ratio",
                            "sortino_ratio", "max_drawdown", "cumulative_return",
                            "beta", "tracking_error", "information_ratio",
                            "benchmark_correlation",
                            "benchmark_twr",
                            "alpha_twr",
                            "portfolio_value_at_retirement",
                        )},
                        "monte_carlo": {k: mc.get(k) for k in (
                            "prob_loss", "prob_outperform_benchmark",
                            "terminal_value_p10", "terminal_value_p50",
                            "terminal_value_p90",
                            "cagr_p10", "cagr_p50", "cagr_p90",
                            "drawdown_p5", "drawdown_p1",
                            "var_95", "cvar_95",
                        )},
                        "timeseries": scenario.get("timeseries", []),
                        "summary_paths": scenario.get("summary_paths", {}),
                        "paths_sample": scenario.get("paths_sample", []),
                        "paths_sample_years": scenario.get("paths_sample_years", []),
                    })
                    # Ana's chat text: only include no-rebalance scenario (for retirement block below)
                    if sc != "none":
                        continue
                    all_results_text.append(f"\n=== Backtest (No Rebalancing) ===")
                    for key in ("cagr", "annualized_volatility", "sharpe_ratio", "sortino_ratio",
                               "max_drawdown", "cumulative_return", "beta", "tracking_error",
                               "information_ratio"):
                        v = m.get(key)
                        all_results_text.append(f"  {key}: {v:.4f}" if v is not None else f"  {key}: N/A")
                    all_results_text.append(
                        "  --- Monte Carlo (terminal_value_* = portfolio $ at horizon; "
                        "cagr_* = annualized TWR as decimal, e.g. 0.12 = 12%) ---"
                    )
                    mc_order = (
                        "prob_loss",
                        "prob_outperform_benchmark",
                        "prob_underperform_benchmark",
                        "terminal_value_p10",
                        "terminal_value_p50",
                        "terminal_value_p90",
                        "cagr_p10",
                        "cagr_p50",
                        "cagr_p90",
                        "drawdown_p5",
                        "drawdown_p1",
                        "var_95",
                        "cvar_95",
                        "var_99",
                        "cvar_99",
                    )
                    for key in mc_order:
                        v = mc.get(key)
                        all_results_text.append(f"  {key}: {v:.4f}" if v is not None else f"  {key}: N/A")

            # Build a SHORT observation for the LLM so Ana can produce a brief Final Answer (not a wall of text)
            if results.get("scenarios"):
                s0 = results["scenarios"][0]
                m0, mc0 = s0.get("metrics", {}), s0.get("monte_carlo", {})
                cagr = m0.get("cagr")
                sharpe = m0.get("sharpe_ratio")
                max_dd = m0.get("max_drawdown")
                prob_loss = mc0.get("prob_loss")
                observation_parts.append(
                    "Backtest complete. Key results: "
                    + (f"TWR {cagr:.2%}, " if cagr is not None else "")
                    + (f"Sharpe {sharpe:.2}, " if sharpe is not None else "")
                    + (f"Max drawdown {max_dd:.2%}. " if max_dd is not None else "")
                    + (f"Probability of loss: {prob_loss:.1%}." if prob_loss is not None else "")
                )

            intake_for_ui = None
            if intake_context:
                birth_dates = getattr(intake_context, "birth_dates", None) or []
                longevity = getattr(intake_context, "longevity_years", None)
                if longevity is None and intake_context.horizon_years is not None:
                    longevity = min(intake_context.horizon_years + 30, 100)
                import datetime as _dt
                _now = _dt.datetime.now()
                intake_for_ui = {
                    "initial_value": intake_context.initial_value,
                    "monthly_savings": intake_context.monthly_savings,
                    "horizon_years": intake_context.horizon_years,
                    "longevity_years": longevity or 40,
                    "display_unit": getattr(intake_context, "display_unit", None),
                    "spending": getattr(intake_context, "spending", None),
                    "planning_for": getattr(intake_context, "planning_for", "self"),
                    "birth_dates": [{"year": y, "month": m} for y, m in birth_dates],
                    "retirement_monthly_target": getattr(intake_context, "retirement_monthly_target", 0.0),
                    "inflation_rate": getattr(intake_context, "inflation_rate", 0.03),
                    "start_month": _now.month,
                    "start_year": _now.year,
                }
                _bsr = _upcoming_expenses_tuples_to_big_spending_rows(intake_context)
                if _bsr:
                    intake_for_ui["big_spending_rows"] = _bsr
            retirement_composition = None  # Growth-only flow
            # Extract historical data date range from first scenario's timeseries
            data_date_range = None
            if results["scenarios"]:
                ts = results["scenarios"][0].get("timeseries", [])
                if ts and len(ts) >= 2:
                    try:
                        from datetime import datetime
                        d0, d1 = ts[0].get("date"), ts[-1].get("date")
                        if d0 and d1:
                            def _to_dt(v):
                                if hasattr(v, "month") and hasattr(v, "year"):
                                    return v
                                return datetime.fromisoformat(str(v).replace("Z", "+00:00")[:19])
                            start, end = _to_dt(d0), _to_dt(d1)
                            data_date_range = f"{start.month:02d}/{start.year} – {end.month:02d}/{end.year}"
                    except (ValueError, TypeError, AttributeError):
                        pass
            # Build ticker_mapping: original -> data source (substitute if used, else self)
            ticker_mapping = {t: (ticker_substitution or {}).get(t, t) for t in all_tickers}
            portfolio_used = {
                "portfolios": [
                    {"label": label, "portfolio": dict(portfolio), "ticker_mapping": ticker_mapping}
                    for label, portfolio in portfolios_to_run
                ],
                "ticker_mapping": ticker_mapping,
            }
            MODEL_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            portfolio_used_path = MODEL_OUTPUT_DIR / "portfolio_used_for_backtest.json"
            portfolio_used_path.write_text(json.dumps(portfolio_used, indent=2), encoding="utf-8")
            logger.info("Stored portfolio used for backtest/MC to %s", portfolio_used_path)

            growth_artifact: Dict[str, object] = {
                "portfolio_composition": primary_portfolio,
                "retirement_composition": retirement_composition,
                "portfolio_used": portfolio_used,
                "scenarios": all_scenarios_for_ui,
                "frequency": frequency or "monthly",
                "years": 25,
                "mc_years": mc_years or 25,
                "mc_sims": results.get("n_sims", 5000),
                "intake": intake_for_ui,
                "data_date_range": data_date_range,
                "asset_correlations": results.get("asset_correlations", {"tickers": [], "rows": []}),
                "mc_leveraged_substitution": results.get("mc_leveraged_substitution", []),
            }
            if extra_sectors:
                growth_artifact["portfolio_sectors"] = extra_sectors
            if extra_industries:
                growth_artifact["portfolio_industries"] = extra_industries
            apply_taxonomy_to_artifact(growth_artifact)
            if session_id and primary_portfolio:
                try:
                    from backend.main import attach_portfolio_breakdown_tickers_to_artifacts

                    attach_portfolio_breakdown_tickers_to_artifacts(
                        growth_artifact,
                        session_id,
                        style_quala_or_panda="quala",
                    )
                except Exception as exc:
                    logger.warning("Growth chart ticker rollups skipped: %s", exc)
            artifact = _sanitize_for_json(growth_artifact)
            with _BACKTEST_STORE_LOCK:
                # Key by session_id so we can retrieve the artifact regardless of which thread ran the tool
                key = session_id if session_id else str(threading.get_ident())
                _BACKTEST_ARTIFACT_STORE[key] = artifact
                logger.info("RunBacktestTool: stored artifact for key=%s (session_id=%s)", key[:12] if isinstance(key, str) and len(key) > 12 else key, bool(session_id))

            # ---- Median P50 at horizon for Ana (authoritative dollar; no withdrawal-rule math) ----
            p50_at_retirement = None
            if results.get("scenarios"):
                s_first = results["scenarios"][0]
                mc_first = s_first.get("monte_carlo", {})
                p50_at_retirement = mc_first.get("terminal_value_p50")
            if p50_at_retirement is not None and p50_at_retirement > 0:
                all_results_text.append("")
                all_results_text.append("=== MEDIAN AT HORIZON (include in your chat reply) ===")
                all_results_text.append(
                    f"Median (P50) portfolio value at end of planning horizon: {_fmt_val(p50_at_retirement)}."
                )
                all_results_text.append(
                    "AUTHORITATIVE $ median at horizon: terminal_value_p50 above — cite ONLY this dollar figure; "
                    "do not multiply or replace it with any other Monte Carlo line (e.g. chart-only scalings)."
                )
                all_results_text.append(
                    "Tell the user: save this growth portfolio, try different scenario planning, and/or build a retirement "
                    "portfolio (Panda) for an end-to-end life plan."
                )
                all_results_text.append("=== END MEDIAN AT HORIZON ===")
                observation_parts.append(
                    "REQUIRED order for Final Answer: (1) State the median (P50) portfolio value at the planning "
                    f"horizon using this EXACT figure from the tool (terminal_value_p50): {_fmt_val(p50_at_retirement)} "
                    "— do not round or replace with a different dollar amount. (2) Brief risk/growth comment. "
                    "(3) Tell the user to save this growth portfolio, try scenarios, and/or build a retirement "
                    "portfolio for an end-to-end life plan."
                )

            # Return a SHORT observation so Ana can reply with a brief Final Answer (tables/charts are in the UI)
            observation_parts.append(
                "Now output ONLY: Thought: <1 line> then Final Answer: <2-4 sentence summary for the user>."
            )
            return "\n".join(observation_parts)
        except FileNotFoundError as exc:
            logger.warning("RunBacktestTool missing data: %s", exc)
            return (
                f"Data error: {exc}. "
                "Use fetch_ticker_data first for the missing tickers."
            )
        except Exception as exc:
            logger.error(
                "RunBacktestTool failed: %s: %s",
                type(exc).__name__,
                exc,
            )
            logger.exception("RunBacktestTool failed (traceback)")
            return (
                f"Backtest error ({type(exc).__name__}): {exc}. "
                "If this persists after redeploy, check Cloud Run logs for quala-api."
            )


# ------------------------------------------------------------------ #
#  Agents                                                             #
# ------------------------------------------------------------------ #

def build_agents(llm_money_manager: LLM, llm_analyst: LLM) -> Dict[str, Agent]:
    money_manager = Agent(
        role="Mr. Quala",
        goal=(
            "Build growth portfolios for the user. Focus ONLY on the accumulation "
            "phase (growth till retirement). Present 3 options for the user to choose."
        ),
        backstory=(
            "You are Mr. Quala, a 5 star investment portfolio manager. "
            "You introduced yourself at the start of the chat — do NOT "
            "re-introduce yourself on subsequent turns. Just get straight "
            "to the content.\n"
            "You build growth portfolios for the accumulation phase only. "
            "Present 3 options (Conservative, Moderate, Aggressive) and ask the "
            "user to choose one. Once chosen, ONLY Ana (not Emu) will run backtesting "
            "and Monte Carlo for the no-rebalancing scenario only. "
            "If the user asks to refine, refine the growth portfolio and ask again. "
            "During refinement you only answer personal-finance and investing topics (decline off-topic questions). "
            "Otherwise save the chosen portfolio."
        ),
        llm=llm_money_manager,
        allow_delegation=False,
    )

    panda = Agent(
        role="Panda",
        goal=(
            "Build diversified retirement portfolios that achieve the user's goals. "
            "Aim for broad diversification — avoid over-concentration in any one fund. "
            "Use dividend aristocrats, REITs, dividend ETFs, and bonds across "
            "multiple holdings. Present 3 options (Conservative, Moderate, Aggressive) "
            "for the user to choose. Handle ALL retirement refinements yourself; never "
            "delegate to Quala."
        ),
        backstory=(
            "You are Panda, a retirement-focused money manager. You help users "
            "build diversified portfolios for the withdrawal phase. You favor "
            "broad diversification to avoid over-concentration in any one fund. "
            "You use dividend aristocrats and REITs for sustainable higher "
            "yield while spreading risk. You analyze "
            "monthly expenses, medical costs, other notes (travel, boat, charity), "
            "cost of living abroad, taxes, and social security. You present 3 yield-focused "
            "portfolio options and handle all refinements yourself: apply user input "
            "(e.g. add JEPI, tilt yield) to all 3 scenarios, rebuild, present in same format. "
            "Never delegate retirement refinement to Quala. Once the user chooses a retirement "
            "portfolio, ONLY Emu (not Ana) runs the backtest and presents results. "
            "During refinement you only answer personal-finance and investing topics (decline off-topic questions)."
        ),
        llm=llm_money_manager,
        allow_delegation=False,
    )

    analyst = Agent(
        role="Ms. Ana",
        goal=(
            "Validate GROWTH portfolio proposals by running real backtesting and "
            "Monte Carlo simulations using historical stock market data. "
            "You handle ONLY growth (accumulation) portfolios — retirement portfolios "
            "are handled by Emu."
        ),
        backstory=(
            "You are Ana, a meticulous quantitative analyst who works alongside "
            "portfolio manager Mr. Quala. Your specialty is validating GROWTH "
            "portfolios with real historical data. You check data availability, "
            "fetch any missing price data, and run rigorous backtesting and "
            "Monte Carlo simulations using the run_backtest tool (growth only). "
            "You do NOT handle retirement portfolios — those go to Emu. "
            "The backtest tool runs only the no-rebalancing scenario. When presenting "
            "results, give a BRIEF 2–4 sentence summary only — do NOT list or repeat "
            "the detailed metrics (TWR, volatility, Sharpe, Monte Carlo P10/P50/P90, etc.) "
            "since they appear in the tables and charts. Do NOT mention or invent "
            "monthly or adaptive rebalancing. When users ask about portfolio composition "
            "or investment strategy, you delegate those questions to Quala. "
            "In next steps, encourage saving this growth portfolio, trying different "
            "scenarios, and/or building a retirement portfolio with Panda so the user "
            "can shape an end-to-end life plan."
        ),
        llm=llm_analyst,
        tools=[CheckTickerDataTool(), FetchTickerDataTool(), RunBacktestTool()],
        allow_delegation=True,
    )

    emu = Agent(
        role="Emu",
        goal=(
            "Run retirement portfolio backtesting and Monte Carlo using historical "
            "data. Summarize results for the user."
        ),
        backstory=(
            "You are Emu, a quantitative analyst who specializes in retirement "
            "portfolio analysis. When Panda's user chooses a retirement portfolio, "
            "you run backtesting and Monte Carlo simulations (the system runs them "
            "automatically). Your job is to provide a brief 2–4 sentence summary "
            "of the results. The detailed metrics (depleted %, TWR, portfolio value "
            "at age 100, yield, etc.) appear in the tables and charts — do NOT "
            "list them in your reply. Asset class and sector bar charts appear when "
            "Panda included them in the portfolio JSON. Focus on interpretation and next steps. "
            "When outcomes are weak, nudge the user to save the portfolio and use "
            "scenario planning to explore spending, other income (e.g. Social Security, pension), "
            "retirement timing, and allocation — not only abstract withdrawal/allocation warnings."
        ),
        llm=llm_analyst,
        tools=[],  # No tools — retirement backtest runs in pre-run
        allow_delegation=False,
    )

    return {"money_manager": money_manager, "analyst": analyst, "emu": emu, "panda": panda}


# ------------------------------------------------------------------ #
#  Task descriptions (dynamic based on conversation phase)            #
# ------------------------------------------------------------------ #

_QUALA_BASE_DESCRIPTION = (
    "IMPORTANT: Do NOT re-introduce yourself. You already greeted the "
    "user at the start. Jump straight into the content.\n\n"
    "Take the user provided input as context and understand what the user "
    "wants. Focus ONLY on the growth portfolio (accumulation phase till "
    "retirement). Make any assumptions you need beyond the user provided input.\n\n"
    "Your job is to build 3 growth portfolio options that satisfy the user's "
    "criteria:\n"
    "  1) Conservative\n"
    "  2) Moderate\n"
    "  3) Aggressive (but within the user's stated risk limits)\n\n"
    "Put ALL estimates (TWR, Beta, Max drawdown, 2008 crash, 2022 crash) "
    "ONLY in the JSON \"estimates\" block — do NOT list them in the chat. "
    "In the chat, give a brief 2–4 sentence summary per portfolio (why you "
    "chose it, key trade-offs). Do NOT call tools — these are LLM estimates; "
    "Only Ana (not Emu) will run rigorous backtesting with real data once the user picks.\n\n"
    "Clearly explain why you chose certain investments and how they help "
    "the user. Explain your assumptions.\n\n"
    "Consider the user's feedback and adjust the portfolios accordingly. "
    "If the user asks to refine, refine the growth portfolio and ask again. "
    "If the user is not clear, ask them to provide more details.\n\n"
    "At the end of EVERY response, ask the user to pick one (exactly ONCE — do NOT repeat):\n"
    "\"Which portfolio would you like to go with?\n"
    "  1) Conservative\n"
    "  2) Moderate\n"
    "  3) Aggressive\"\n\n"
    "CRITICAL: Present the 3 portfolio summaries and the pick-one question exactly ONCE. "
    "Do NOT repeat the same block of text.\n\n"
    "In the chat, do NOT list tickers/weights, asset class breakdown, or "
    "sector breakdown — those are shown in the tables below from "
    "your JSON. Give only a brief 2–4 sentence summary per portfolio. "
    "Pie charts, asset tables, and estimate tables will be generated from "
    "your JSON.\n\n"
    "CRITICAL: At the very end of your response (AFTER the pick-one "
    "question), you MUST append a machine-readable JSON block wrapped "
    "in <<<PORTFOLIOS_JSON>>> and <<<END_PORTFOLIOS_JSON>>> markers.\n"
    "The JSON must have keys \"conservative\", \"moderate\", \"aggressive\". "
    "Each value is an object with:\n"
    "  - \"tickers\": an OBJECT of ticker:weight pairs (e.g. {{\"VTI\": 0.4, \"BND\": 0.3}}). "
    "    Weights must be decimals that sum to 1.0. Do NOT use a list of tickers — "
    "    you MUST assign a numeric weight to each ticker.\n"
    "  - \"sectors\": REQUIRED JSON key for **asset class** weights (decimals sum 1.0). "
    "    The key name is \"sectors\" for compatibility; in chat prose say **asset class**, never \"asset type\". "
    "    Use ONLY: US Stocks, International Stocks, Bonds, Commodities, Digital Assets, Other "
    "(Other for hybrids, alts, rounding). "
    "    Map each ticker sleeve to one asset class (e.g. VTI/QQQ → US Stocks; BND → Bonds; "
    "VXUS/IEFA → International Stocks; GLD/PDBC → Commodities; BTC/IBIT → Digital Assets).\n"
    "  - \"industries\": REQUIRED JSON key for **sector** weights (GICS-style; equity sleeve), decimals sum 1.0. "
    "    The key name is \"industries\" for compatibility; in chat prose say **sector** or **sectors**, never \"industry\". "
    "    Break down each equity ETF into underlying exposure, then aggregate. "
    "    Use ONLY: Technology, Financials, Communication Services, Consumer Discretionary, "
    "Consumer Staples, Health Care, Industrials, Energy, Utilities, Materials, Real Estate, Other. "
    "    Put non-equity exposure (bonds, digital assets, broad commodities) under this key as "
    "\"Other\" so the sector bar sums to 1.0. Do NOT put US Stocks, Bonds, or International Stocks "
    "in \"industries\" — those belong only in **asset class** (\"sectors\").\n"
    "  - \"estimates\" (REQUIRED for conservative, moderate, AND aggressive): same shape for each — "
    "{{\"cagr_range\": \"...\", \"beta_range\": \"...\", \"max_drawdown_range\": \"...\", "
    "\"crash_2008\": \"...\", \"crash_2022\": \"...\"}}. Ranges must be lowest risk for "
    "Conservative, middle for Moderate, highest for Aggressive.\n"
    "Example (growth portfolio only; JSON \"sectors\" = asset class; JSON \"industries\" = sectors):\n"
    "<<<PORTFOLIOS_JSON>>>\n"
    "{{\"conservative\": {{\"tickers\": {{\"BND\": 0.5, \"VTI\": 0.3, "
    "\"GLD\": 0.2}}, \"sectors\": {{\"Bonds\": 0.5, \"US Stocks\": 0.3, "
    "\"Commodities\": 0.2}}, \"industries\": {{\"Other\": 0.68, \"Technology\": 0.08, "
    "\"Financials\": 0.05, \"Health Care\": 0.04, \"Consumer Staples\": 0.03, "
    "\"Consumer Discretionary\": 0.03, \"Industrials\": 0.03, \"Real Estate\": 0.02, "
    "\"Materials\": 0.01, \"Energy\": 0.01, \"Communication Services\": 0.02}}, \"estimates\": {{\"cagr_range\": "
    "\"4.5% - 6.5%\", \"beta_range\": \"0.55 - 0.75\", \"max_drawdown_range\": \"12% - 18%\", "
    "\"crash_2008\": \"Estimated loss of 8% - 14%. Heavy bonds and gold buffer equity drawdowns.\", "
    "\"crash_2022\": \"Estimated loss of 5% - 11%. Smaller equity sleeve; bonds still declined.\"}}}}, "
    "\"moderate\": "
    "{{\"tickers\": {{\"VTI\": 0.4, \"BND\": 0.2, \"QQQ\": 0.2, \"VXUS\": 0.2}}, "
    "\"sectors\": {{\"US Stocks\": 0.6, \"Bonds\": 0.2, \"International Stocks\": 0.2}}, "
    "\"industries\": {{\"Technology\": 0.26, \"Financials\": 0.11, \"Health Care\": 0.08, "
    "\"Consumer Discretionary\": 0.08, \"Industrials\": 0.07, \"Consumer Staples\": 0.05, "
    "\"Real Estate\": 0.04, \"Materials\": 0.03, \"Energy\": 0.02, "
    "\"Communication Services\": 0.04, \"Utilities\": 0.02, \"Other\": 0.2}}, \"estimates\": {{\"cagr_range\": \"7.0% - 9.0%\", "
    "\"beta_range\": \"0.8 - 0.95\", \"max_drawdown_range\": \"18% - 25%\", "
    "\"crash_2008\": \"Estimated loss of 15% - 20%. Higher equity exposure leads to larger "
    "losses than Conservative; bonds provide some buffer.\", \"crash_2022\": \"Estimated "
    "loss of 10% - 15%. Both equities and bonds faced headwinds; tech tilt in QQQ could "
    "increase sensitivity to rate hikes.\"}}}}, \"aggressive\": {{\"tickers\": "
    "{{\"QQQ\": 0.4, \"VTI\": 0.3, \"VXUS\": 0.3}}, \"sectors\": {{\"US Stocks\": "
    "0.7, \"International Stocks\": 0.3}}, \"industries\": {{\"Technology\": 0.38, "
    "\"Financials\": 0.11, \"Health Care\": 0.09, \"Consumer Discretionary\": 0.1, "
    "\"Industrials\": 0.09, \"Consumer Staples\": 0.05, \"Real Estate\": 0.04, "
    "\"Materials\": 0.04, \"Energy\": 0.02, \"Communication Services\": 0.05, "
    "\"Utilities\": 0.03}}, \"estimates\": {{\"cagr_range\": "
    "\"9.5% - 12.5%\", \"beta_range\": \"1.15 - 1.35\", \"max_drawdown_range\": \"28% - 38%\", "
    "\"crash_2008\": \"Estimated loss of 38% - 48%. High equity and growth tilt.\", "
    "\"crash_2022\": \"Estimated loss of 24% - 30%. Growth-heavy; rate-sensitive.\"}}}}}}\n"
    "<<<END_PORTFOLIOS_JSON>>>\n\n"
)

_CHOOSING_ADDENDUM = (
    "IMPORTANT: You have discussed enough with the user. Present your "
    "final 3 growth portfolios so the user can choose (exactly ONCE — do NOT repeat):\n\n"
    "1. SHOW ALL 3 PORTFOLIOS:\n"
    "   - Conservative (growth only)\n"
    "   - Moderate (growth only)\n"
    "   - Aggressive (growth only)\n\n"
    "   Do NOT list tickers, asset class breakdown, or sector (GICS) "
    "breakdown in the chat — those appear in the tables from your JSON. "
    "Give only a brief 2–4 sentence summary per portfolio.\n\n"
    "2. Put ALL estimates (TWR, Beta, Max drawdown, 2008 crash, 2022 crash) "
    "ONLY in the JSON \"estimates\" block — a separate non-empty \"estimates\" object for "
    "EVERY portfolio (conservative, moderate, aggressive). Do NOT list them in the chat. "
    "In the chat, give a brief 2–4 sentence summary per portfolio only.\n\n"
    "3. After presenting all 3 portfolios with brief summaries, ask the user to pick:\n"
    "\"Please choose one to proceed:\n"
    "  1) Conservative\n"
    "  2) Moderate\n"
    "  3) Aggressive\"\n\n"
    "4. Once the user chooses, ONLY Ana (not Emu) will immediately run backtesting "
    "and Monte Carlo with actual historical data for that chosen growth portfolio only "
    "(no-rebalancing scenario). No confirmation step.\n\n"
    "5. Each portfolio in PORTFOLIOS_JSON MUST include non-empty \"sectors\" (asset class) and \"industries\" (sectors) "
    "(decimals summing to 1.0). **Asset class** in \"sectors\": US Stocks, International Stocks, Bonds, "
    "Commodities, Digital Assets, Other. **Sectors** in \"industries\": Technology, Financials, Communication Services, "
    "Consumer Discretionary, Consumer Staples, Health Care, Industrials, Energy, Utilities, Materials, "
    "Real Estate, Other (non-equity sleeves → sector Other). The system forwards the chosen option "
    "to Ana for backtest + breakdown charts.\n\n"
)

_REFINING_ADDENDUM = (
    "=== REFINING MODE (OVERRIDES ALL OTHER INSTRUCTIONS) ===\n\n"
    "SCOPE: During refinement you ONLY discuss personal finance, investing, retirement planning, portfolios, "
    "markets, and related topics (e.g. ETFs, stocks, bonds, annuities, IRAs, taxes as they relate to investing). "
    "If the user asks something clearly off-topic, politely decline and redirect to finance/investing.\n\n"
    "The user may (a) ask to CHANGE the portfolio, (b) ask ANY finance/investing question without changing the "
    "portfolio, or (c) both. Examples: pros/cons of an annuity, company or fund yield, tax concepts, comparisons, "
    "definitions, strategy trade-offs, macro/markets — all in scope.\n\n"
    "BEHAVIOR:\n"
    "- If they ask a finance/investing question only: answer helpfully and concisely. You are not a tax attorney or CPA — "
    "say so when relevant; note that yields, fees, and rules change over time.\n"
    "- If they ask to change allocations (add/remove/tilt ticker, more bonds, shift weights, etc.): apply to ALL 3 "
    "portfolios and output updated <<<PORTFOLIOS_JSON>>>.\n"
    "- If they ask ONLY for information/education with NO allocation change: re-output the same <<<PORTFOLIOS_JSON>>> "
    "as in Structured portfolios below unchanged.\n"
    "- If they mix Q&A and a portfolio change: answer the question, then apply the change in the JSON.\n"
    "- After pure Q&A, invite them to add tickers or adjust the three options if relevant.\n\n"
    "The PREVIOUS portfolio proposal is below. Use it as the base when changing allocations.\n\n"
    "ONLY if the user says something vague like \"I want to refine\" with no specifics, ask: "
    "\"How would you like to refine? Type your request below — for example: add gold, add AAPL, more bonds, "
    "or ask a finance question.\"\n\n"
)

# ------------------------------------------------------------------ #
#  Panda: Retirement portfolio task                                  #
# ------------------------------------------------------------------ #

_PANDA_BASE_DESCRIPTION = (
    "You are Panda, the user's retirement money manager. Your job is to build "
    "retirement portfolios that achieve their goals. Use the user intake (provided below "
    "when available) and conversation history as context. If intake is provided, use it "
    "immediately — do NOT ask the user to provide it again.\n\n"
    "CRITICAL: When calculating retirement year, ALWAYS use the CURRENT calendar year. "
    "If the user says \"retire in 10 years\", retirement year = current year + 10 "
    "(e.g. in 2026, that is 2036 — NOT 2034). The intake summary below includes the computed "
    "retirement year; use that exact year in your analysis.\n\n"
    "FOLLOW THESE STEPS IN ORDER:\n\n"
    "1. MONTHLY EXPENSE: Look at current monthly expense. Use the inflation assumption from the "
    "user intake when provided (e.g. \"Inflation assumption: 3%%\" — use 3%%). If not in intake, "
    "default to 3%%. Project what monthly expense will be at retirement and every year after. "
    "State the inflation rate you used.\n\n"
    "2. DO NOT CONSIDER when building the 3 scenarios — the user will refine later:\n"
    "   - Other Notes & Considerations (Travel, International Retirement, State Taxes, Social Security)\n"
    "   - Withdrawal Strategy (taxable vs IRA vs Roth order)\n"
    "   Skip these entirely when building the initial 3 portfolios. Focus only on portfolio "
    "composition (tickers, weights, yield). The user can refine and add these considerations later.\n\n"
    "3. THREE PORTFOLIO SCENARIOS (yield-focused):\n"
    "   - CONSERVATIVE: Lowest yield — more bonds, stable income funds; prioritize capital preservation.\n"
    "   - MODERATE: Moderate yield — balanced mix of growth and dividend ETFs.\n"
    "   - AGGRESSIVE: Highest yield — tilt toward dividend aristocrats, REITs, high-yield ETFs (SCHD, JEPI, VYM).\n"
    "   CRITICAL: Yield must be ordered Conservative < Moderate < Aggressive. "
    "The \"estimates\" block for each portfolio must reflect this (e.g. conservative yield ~2%%–3%%, "
    "moderate ~3%%–4%%, aggressive ~4%%–5%%+).\n\n"
    "YIELD-FOCUSED SECURITY SELECTION — Goal: higher yield that sustains dividends while diversifying:\n"
    "   - DIVIDEND ARISTOCRATS: Prefer companies/ETFs with 25+ years of consecutive dividend increases "
    "(e.g. NOBL, SCHD, VIG). These tend to be more sustainable.\n"
    "   - REITs: Include REITs for diversification and often higher yield (e.g. VNQ, O, VICI, STAG). "
    "REITs provide real estate exposure and income.\n"
    "   - DIVERSIFY across sectors (GICS) and asset classes — avoid over-concentration in any "
    "one fund; spread holdings to reduce concentration risk while maximizing yield.\n\n"
    "DIVIDEND SCREENING: Prefer payout ratio <= 60%% (sustainable dividends with room for growth). "
    "Avoid high-yield stocks with payout ratios above 60%% as they may be unsustainable.\n\n"
    "TICKER SELECTION — Consider these when building the 3 portfolios:\n"
    "   - Mix in for yield/income: JEPI, JEPQ, PDBC, RYLD/QYLD (use carefully — high yield, higher risk), "
    "DIV, SPHD; for fixed income: BND or HYMB.\n"
    "   - Anchor in growth with broad market total return ETFs: VTI, VOO, or SCHD.\n"
    "Put ALL portfolio details "
    "(tickers, weights, **asset class** under JSON key \"sectors\", **sectors** under JSON key \"industries\", estimates) "
    "ONLY in the JSON block — do NOT list in chat. "
    "Put ALL estimates (TWR/cagr_range, Beta, max drawdown, **2008 crash impact**, **2022 crash impact**, "
    "yield, projected income at retirement) ONLY in each portfolio's JSON \"estimates\" object — do NOT list them in chat. "
    "For crash_2008 and crash_2022 you MUST give an estimated **portfolio loss range** plus one short reason "
    "(e.g. \"Estimated loss of 10% - 16%. Bond and dividend sleeves buffer equity.\"). "
    "NEVER use \"...\", \"TBD\", or empty placeholders.\n\n"
    "The chosen portfolio's **tickers**, **asset class** (\"sectors\": US Stocks, International Stocks, "
    "Bonds, Commodities, Digital Assets, Other), and **sectors** (\"industries\": Technology, Financials, … per Quala) "
    "are passed to Emu as JSON for bar charts — keep them accurate and summing to 1.0. "
    "Include an \"estimates\" block for EACH portfolio with \"cagr_range\" (e.g. \"5%% - 7%%\"), "
    "\"yield\" or \"yield_range\" (e.g. \"3.5%%\" or \"3.2%% - 4.1%%\"), and "
    "\"projected_income_at_retirement\" (e.g. \"$4,200/month\" or \"$50K/year\") so the user sees "
    "TWR, yield, and projected income for each retirement option. "
    "In the chat, give a brief 2–4 sentence summary per portfolio. "
    "Do NOT mention medical cost, medical expenses, or Medicare when presenting the 3 portfolios "
    "for selection — focus only on yield, TWR, and projected income.\n\n"
    "At the end, ask the user to choose one or keep refining. Once the user chooses, "
    "ONLY Emu (our retirement analyst, not Ana) will run backtesting and Monte Carlo "
    "and present the results. Do NOT mention or ask Ana. When offering refinement, "
    "ask if they want to tilt towards more yield (higher dividend income) or more return "
    "(higher growth/TWR), or if any particular ticker should be added:\n"
    "\"Which retirement portfolio would you like?\n"
    "  1) Conservative\n"
    "  2) Moderate\n"
    "  3) Aggressive\n"
    "Or tell me how you'd like to refine — for example: tilt towards more yield or more "
    "return, or add a specific ticker (e.g. SCHD, JEPI).\"\n\n"
    "TONE: Do NOT use phrases like \"As your expert portfolio manager\" or \"As your advisor\" "
    "in your replies. Be direct and conversational.\n\n"
)

_PANDA_REFINING_ADDENDUM = (
    "CRITICAL: You handle ALL retirement portfolio refinements. Do NOT delegate to Quala. "
    "Quala handles growth portfolios only; retirement is your domain.\n\n"
    "SCOPE: During refinement you ONLY discuss personal finance, investing, retirement planning, portfolios, "
    "income/decumulation, and related topics (e.g. annuities, Social Security, IRAs, taxes as they relate to "
    "retirement investing). If the user asks something clearly off-topic, politely decline and redirect.\n\n"
    "The user may (a) ask to CHANGE the three portfolios, (b) ask ANY finance/investing question without changing "
    "allocations, or (c) both. Examples: pros/cons of annuities, yield of a company or fund, RMDs, withdrawal order, "
    "inflation risk, comparisons — all in scope.\n\n"
    "When refining, the user MAY also specify: travel, international retirement, state taxes, Social Security, "
    "or withdrawal strategy — apply when they ask to change the plan or portfolios.\n\n"
    "When the user asks to refine but has NOT yet specified what, you MUST ask — as Panda — for example:\n"
    "\"How would you like to refine? Type below — e.g. add JEPI, tilt yield, more bonds, or ask a finance question.\"\n\n"
    "When the user wants a portfolio change (e.g. \"add JEPI\", \"increase the yield\", \"adjust inflation to 3%%\"), you MUST:\n"
    "1. Apply the user's input to ALL 3 scenarios (Conservative, Moderate, Aggressive).\n"
    "2. Rebuild the 3 retirement portfolios with the refinement applied.\n"
    "3. Output the results in the SAME format as before: brief 2–4 sentence summary per portfolio "
    "in chat, then the full JSON block with <<<PORTFOLIOS_JSON>>> and <<<END_PORTFOLIOS_JSON>>> "
    "containing all 3 portfolios with tickers, asset class (\"sectors\"), sectors (\"industries\"), and estimates "
    "(including crash_2008 and crash_2022 with real loss ranges — never \"...\"). "
    "Do NOT mention medical cost when presenting portfolios.\n\n"
    "When the user asks ONLY finance Q&A with NO allocation change: answer helpfully; re-output the same "
    "<<<PORTFOLIOS_JSON>>> as in Structured portfolios below unchanged. When they mix Q&A and a change: "
    "answer then update JSON. ALWAYS maintain yield ordering: Conservative (lowest) < Moderate < Aggressive (highest) "
    "when you change portfolios.\n\n"
    "TONE: Do NOT use phrases like \"As your expert portfolio manager\" or \"As your advisor\". "
    "Be direct and conversational.\n\n"
)

_PANDA_OUTPUT_FORMAT = (
    "CRITICAL: At the very end of your response, append a machine-readable JSON block "
    "wrapped in <<<PORTFOLIOS_JSON>>> and <<<END_PORTFOLIOS_JSON>>>. Same format as Quala: "
    "The JSON key \"sectors\" must contain ONLY asset class weights: US Stocks, International Stocks, Bonds, "
    "Commodities, Digital Assets, Other. "
    "The JSON key \"industries\" must contain ONLY sector weights: Technology, Financials, Communication Services, Consumer Discretionary, "
    "Consumer Staples, Health Care, Industrials, Energy, Utilities, Materials, Real Estate, Other. "
    "Each of conservative, moderate, aggressive MUST include tickers, asset class (\"sectors\"), sectors (\"industries\"), "
    "and a full \"estimates\" object: cagr_range, beta_range, max_drawdown_range, crash_2008, crash_2022 "
    "(each crash field = estimated loss range + brief reason), yield or yield_range, "
    "projected_income_at_retirement. Conservative = lowest risk/yield; Aggressive = highest.\n"
    "Example (retirement; copy the crash_2008/crash_2022 style — do NOT use \"...\"):\n"
    "<<<PORTFOLIOS_JSON>>>\n"
    "{{\"conservative\": {{\"tickers\": {{\"BND\": 0.45, \"SCHD\": 0.25, \"VNQ\": 0.15, \"VTI\": 0.15}}, "
    "\"sectors\": {{\"Bonds\": 0.45, \"US Stocks\": 0.4, \"Real Estate\": 0.15}}, "
    "\"industries\": {{\"Other\": 0.55, \"Financials\": 0.08, \"Real Estate\": 0.12, \"Utilities\": 0.1, "
    "\"Consumer Staples\": 0.08, \"Health Care\": 0.07}}, "
    "\"estimates\": {{\"cagr_range\": \"4%% - 6%%\", \"beta_range\": \"0.45 - 0.65\", "
    "\"max_drawdown_range\": \"10%% - 16%%\", "
    "\"crash_2008\": \"Estimated loss of 8%% - 14%%. Heavy bonds and dividend ETFs buffer equity drawdowns.\", "
    "\"crash_2022\": \"Estimated loss of 6%% - 12%%. Bonds and REITs fell with rates; equity sleeve smaller.\", "
    "\"yield\": \"2.8%%\", \"projected_income_at_retirement\": \"$3,200/month\"}}}}, "
    "\"moderate\": {{\"tickers\": {{\"BND\": 0.25, \"SCHD\": 0.25, \"VTI\": 0.25, \"VXUS\": 0.15, \"VNQ\": 0.1}}, "
    "\"sectors\": {{\"US Stocks\": 0.5, \"Bonds\": 0.25, \"International Stocks\": 0.15, \"Real Estate\": 0.1}}, "
    "\"industries\": {{\"Technology\": 0.12, \"Financials\": 0.1, \"Health Care\": 0.08, \"Other\": 0.45, "
    "\"Real Estate\": 0.08, \"Consumer Staples\": 0.07, \"Industrials\": 0.06}}, "
    "\"estimates\": {{\"cagr_range\": \"5.5%% - 7.5%%\", \"beta_range\": \"0.7 - 0.9\", "
    "\"max_drawdown_range\": \"15%% - 22%%\", "
    "\"crash_2008\": \"Estimated loss of 14%% - 20%%. Balanced equity and income; bonds helped versus pure equity.\", "
    "\"crash_2022\": \"Estimated loss of 12%% - 18%%. Rate shock hit bonds and dividend stocks together.\", "
    "\"yield\": \"3.4%%\", \"projected_income_at_retirement\": \"$3,900/month\"}}}}, "
    "\"aggressive\": {{\"tickers\": {{\"SCHD\": 0.25, \"JEPI\": 0.2, \"VTI\": 0.25, \"QQQ\": 0.15, \"VNQ\": 0.15}}, "
    "\"sectors\": {{\"US Stocks\": 0.65, \"Real Estate\": 0.15, \"Bonds\": 0.1, \"International Stocks\": 0.1}}, "
    "\"industries\": {{\"Technology\": 0.22, \"Financials\": 0.1, \"Real Estate\": 0.1, \"Other\": 0.35, "
    "\"Health Care\": 0.08, \"Consumer Discretionary\": 0.08}}, "
    "\"estimates\": {{\"cagr_range\": \"7%% - 9.5%%\", \"beta_range\": \"0.95 - 1.15\", "
    "\"max_drawdown_range\": \"22%% - 32%%\", "
    "\"crash_2008\": \"Estimated loss of 28%% - 38%%. Higher equity and income ETF tilt; less bond buffer.\", "
    "\"crash_2022\": \"Estimated loss of 20%% - 28%%. Growth and covered-call sleeves sensitive to rate hikes.\", "
    "\"yield\": \"4.6%%\", \"projected_income_at_retirement\": \"$4,600/month\"}}}}}}\n"
    "<<<END_PORTFOLIOS_JSON>>>\n\n"
    "Output ONLY your direct reply to the user. Do NOT output Thought:, Action:, or internal monologue."
)

# Injected before the user message on every growth refinement turn (replaces narrow “education only” branches).
_UNIFIED_REFINE_GROWTH_USER_PREFIX = (
    "[REFINEMENT TURN — GROWTH] The user is refining or asking follow-ups while viewing three proposed portfolios.\n\n"
    "SCOPE: Answer ONLY personal finance, investing, retirement planning, portfolios, markets, and related topics "
    "(e.g. stocks, bonds, ETFs, mutual funds, annuities, IRAs, taxes as they relate to investing). "
    "If the message is clearly off-topic, politely decline and redirect to finance/investing.\n\n"
    "BEHAVIOR:\n"
    "- If they ask ANY finance/investing question (education, pros/cons, definitions, comparisons, yields, annuities, "
    "macro, risk, tax concepts as they relate to investing, etc.), answer clearly and concisely. You are not a tax "
    "attorney or CPA — disclaim when relevant; note figures and rules change over time.\n"
    "- If they ask to CHANGE the portfolio (add/remove/tilt ticker, shift allocation, more bonds, etc.), apply to "
    "ALL THREE portfolios and output updated <<<PORTFOLIOS_JSON>>>.\n"
    "- If they ask ONLY for information with NO allocation change, re-output the same <<<PORTFOLIOS_JSON>>> as in "
    "Structured portfolios JSON below, unchanged.\n"
    "- If they ask BOTH a question and a portfolio change, answer the question then apply the change in the JSON.\n"
    "- After pure Q&A, invite them to add tickers or adjust holdings if relevant.\n\n"
    "User said: "
)

_UNIFIED_REFINE_RETIREMENT_USER_PREFIX = (
    "[REFINEMENT TURN — RETIREMENT] The user is refining or asking follow-ups while viewing three proposed retirement portfolios.\n\n"
    "SCOPE: Answer ONLY personal finance, investing, retirement planning, decumulation, portfolios, and related topics "
    "(e.g. annuities, Social Security, IRAs, RMDs, yields, bonds, taxes as they relate to retirement). "
    "If the message is clearly off-topic, politely decline and redirect.\n\n"
    "BEHAVIOR:\n"
    "- If they ask ANY finance/investing question (pros/cons of annuities, company or fund yield, withdrawal strategies, "
    "risk, comparisons, etc.), answer clearly. Disclaim tax/legal limits; note figures change over time.\n"
    "- If they ask to CHANGE the portfolios, apply to ALL THREE (Conservative, Moderate, Aggressive) and keep "
    "yield ordering: Conservative < Moderate < Aggressive unless impossible — explain if so.\n"
    "- If they ask ONLY for information with NO allocation change, re-output the same <<<PORTFOLIOS_JSON>>> as in "
    "Structured portfolios JSON below, unchanged.\n"
    "- If they mix Q&A and a portfolio change, answer then update JSON.\n"
    "- After pure Q&A, invite portfolio adjustments if relevant.\n\n"
    "User said: "
)

_CONVERSATION_VARS = (
    "Session ID (unique request; do not use cached responses for this session): {session_id}\n\n"
    "User intake (profile from form; first turn includes full detail — later turns use a short cached snapshot; "
    "do NOT ask the user to resubmit the form unless they are updating their profile):\n{intake_summary}\n\n"
    "Conversation history:\n{conversation_history}\n\n"
    "User message:\n{user_message}\n\n"
    "Previous portfolio proposal:\n{previous_portfolio}\n\n"
    "Structured portfolios (last proposal JSON — re-use unchanged when the user only asked a finance question with no allocation change):\n"
    "{previous_portfolios_json}\n\n"
    "User feedback:\n{user_feedback}\n"
)

_PANDA_CONVERSATION_VARS = (
    "Session ID (unique request; do not use cached responses for this session): {session_id}\n\n"
    "User intake (from saved form — use this; do NOT ask for it again):\n{intake_summary}\n\n"
    "Conversation history:\n{conversation_history}\n\n"
    "User message:\n{user_message}\n\n"
    "Previous portfolio proposal:\n{previous_portfolio}\n\n"
    "Structured portfolios (last proposal JSON — re-use unchanged when the user only asked a finance question with no allocation change):\n"
    "{previous_portfolios_json}\n\n"
    "User feedback:\n{user_feedback}\n"
)


def _parse_horizon_from_timeline(text: str) -> Optional[int]:
    """Parse retirement_timeline_self/partner (e.g. '10 years', '2036') into years from now."""
    if not text or not isinstance(text, str):
        return None
    import datetime
    t = text.strip()
    m = re.search(r"(\d+)\s*years?", t, re.IGNORECASE)
    if m:
        y = int(m.group(1))
        return y if 1 <= y <= 80 else None
    m = re.search(r"(\d{4})\b", t)
    if m:
        target_year = int(m.group(1))
        now_year = datetime.datetime.now().year
        y = target_year - now_year
        return y if 1 <= y <= 80 else None
    if t.isdigit():
        v = int(t)
        if 1 <= v <= 80:
            return v
        if 2020 <= v <= 2100:
            return v - datetime.datetime.now().year
    return None


def _format_intake_summary(
    intake_payload: Optional[Dict[str, Any]] = None,
    intake_context: Optional[object] = None,
) -> str:
    """Build a human-readable intake summary for Panda from payload and/or IntakeContext."""
    import datetime
    current_year = datetime.datetime.now().year
    parts: List[str] = []
    if intake_payload:
        iv = intake_payload.get("initial_value")
        if iv is not None:
            parts.append(f"- Current total investment value: ${float(iv):,.0f}")
        me = intake_payload.get("current_monthly_expense")
        if me is not None:
            parts.append(f"- Current monthly expenses: ${float(me):,.0f}")
        bd = intake_payload.get("birth_dates")
        if bd and isinstance(bd, list):
            for i, b in enumerate(bd[:2]):
                if isinstance(b, dict) and "year" in b:
                    y, m = b.get("year"), b.get("month", 6)
                    label = "Your birth date" if i == 0 else "Partner's birth date"
                    parts.append(f"- {label}: {int(y)}-{int(m):02d}")
        rts = intake_payload.get("retirement_timeline_self")
        rtp = intake_payload.get("retirement_timeline_partner")
        if rts is not None:
            horizon = _parse_horizon_from_timeline(rts)
            if horizon is not None:
                retirement_year = current_year + horizon
                parts.append(f"- Your target retirement year: {retirement_year} (in {horizon} years from {current_year})")
            else:
                parts.append(f"- Your target retirement timeline: {rts}")
        if rtp is not None:
            horizon = _parse_horizon_from_timeline(rtp)
            if horizon is not None:
                retirement_year = current_year + horizon
                parts.append(f"- Partner's target retirement year: {retirement_year} (in {horizon} years from {current_year})")
            else:
                parts.append(f"- Partner's target retirement timeline: {rtp}")
        state = intake_payload.get("state")
        if state:
            parts.append(f"- State of residence: {state}")
        inf = intake_payload.get("inflation_assumption")
        if inf is not None:
            parts.append(f"- Inflation assumption: {float(inf):.1f}%")
        on = intake_payload.get("other_notes")
        if on:
            parts.append(f"- Other notes: {on}")
        rs = intake_payload.get("retirement_status")
        if rs:
            parts.append(f"- Retirement status: {rs}")
        pf = intake_payload.get("planning_for")
        if pf:
            parts.append(f"- Planning for: {pf}")
    if intake_context:
        inf = getattr(intake_context, "inflation_rate", None)
        if inf is not None and parts and not any("inflation" in p.lower() for p in parts):
            parts.append(f"- Inflation assumption: {float(inf) * 100:.1f}%")
    if intake_context and not parts:
        parts.append(f"- Investment value: ${getattr(intake_context, 'initial_value', 0):,.0f}")
        parts.append(f"- Monthly expense: ${getattr(intake_context, 'current_monthly_expense', 0):,.0f}")
        inf = getattr(intake_context, "inflation_rate", None)
        if inf is not None:
            parts.append(f"- Inflation assumption: {float(inf) * 100:.1f}%")
        bd = getattr(intake_context, "birth_dates", None) or []
        for i, (y, m) in enumerate(bd[:2]):
            label = "Your birth date" if i == 0 else "Partner's birth date"
            parts.append(f"- {label}: {y}-{m:02d}")
    if not parts:
        return "(No intake data available — user has not filled the form.)"
    return "\n".join(parts)


def _intake_prompt_is_empty_or_placeholder(text: str) -> bool:
    t = (text or "").strip()
    return not t or t.startswith("(No intake data available")


def _format_intake_summary_compact(
    intake_payload: Optional[Dict[str, Any]] = None,
    intake_context: Optional[object] = None,
) -> str:
    """Small numeric snapshot for follow-up turns (avoids resending the full intake block)."""
    parts: List[str] = []
    if intake_payload:
        iv = intake_payload.get("initial_value")
        if iv is not None:
            parts.append(f"initial_value=${float(iv):,.0f}")
        ms = intake_payload.get("monthly_savings")
        if ms is not None and float(ms) > 0:
            parts.append(f"monthly_savings=${float(ms):,.0f}")
        me = intake_payload.get("current_monthly_expense")
        if me is not None:
            parts.append(f"monthly_expense=${float(me):,.0f}")
        hz = intake_payload.get("horizon_years")
        if hz is not None:
            parts.append(f"horizon_years={hz}")
        inf = intake_payload.get("inflation_assumption")
        if inf is not None:
            parts.append(f"inflation_assumption={float(inf):.1f}%")
        st = intake_payload.get("state")
        if st:
            parts.append(f"state={st}")
        pf = intake_payload.get("planning_for")
        if pf:
            parts.append(f"planning_for={pf}")
    if intake_context and len(parts) < 2:
        iv = getattr(intake_context, "initial_value", None)
        if iv is not None:
            parts.append(f"initial_value=${float(iv):,.0f}")
        me = getattr(intake_context, "current_monthly_expense", None)
        if me is not None:
            parts.append(f"monthly_expense=${float(me):,.0f}")
        hz = getattr(intake_context, "horizon_years", None)
        if hz is not None:
            parts.append(f"horizon_years={hz}")
        inf = getattr(intake_context, "inflation_rate", None)
        if inf is not None:
            parts.append(f"inflation_assumption={float(inf) * 100:.1f}%")
    if not parts:
        return "(numeric snapshot unavailable — use conversation history for intake detail)"
    return "; ".join(parts)


_CACHED_SESSION_INTAKE_HEADER = (
    "[SESSION INTAKE — stored for this session_id after the first turn; do NOT ask the user to resubmit "
    "the full intake form. Use the conversation history for narrative goals, birth dates, timelines, and notes. "
    "Later user messages are incremental only (e.g. refine, choose option).]\n\n"
    "Active numeric snapshot (refresh full detail only if intake changed):\n"
)


def _intake_summary_for_llm(
    session_id: str,
    intake_payload: Optional[Dict[str, object]],
    intake_context: Optional[object],
) -> str:
    """
    First turn with real intake: full formatted summary (and lock for session).
    Same intake on later turns: short cached snapshot + instruction (saves prompt tokens).
    If intake changes (form/API update): send full summary again and refresh lock.
    """
    full = _format_intake_summary(intake_payload=intake_payload, intake_context=intake_context)
    if _intake_prompt_is_empty_or_placeholder(full):
        return full

    with _INTAKE_STORE_LOCK:
        locked = SESSION_FULL_INTAKE_PROMPT_BY_SESSION.get(session_id)
        if locked is None:
            SESSION_FULL_INTAKE_PROMPT_BY_SESSION[session_id] = full
            logger.info("Intake prompt cache: locked full summary for session %s", session_id[:12] if session_id else "")
            return full
        if full != locked:
            SESSION_FULL_INTAKE_PROMPT_BY_SESSION[session_id] = full
            logger.info("Intake prompt cache: intake changed; refreshed full summary for session %s", session_id[:12] if session_id else "")
            return full

    compact = _format_intake_summary_compact(intake_payload, intake_context)
    return (
        _CACHED_SESSION_INTAKE_HEADER
        + compact
        + "\n\n(Full intake bullet list was provided on the first turn of this session in this task; "
        "refer to earlier conversation or that turn if needed.)\n"
    )


def clear_intake_prompt_cache(session_id: str) -> None:
    """Clear locked intake prompt text for a session (e.g. new chat in UI)."""
    with _INTAKE_STORE_LOCK:
        SESSION_FULL_INTAKE_PROMPT_BY_SESSION.pop(session_id, None)


# Reduce CrewAI/ReAct parse errors (Gemini sometimes omits Action: or mixes Action + Final Answer)
_OUTPUT_FORMAT_QUALA = (
    "\n\nOUTPUT: Output ONLY your direct reply to the user. Do NOT output Thought:, reasoning, "
    "or internal monologue. The user sees only your final response. Do not use Action: or Action Input:."
)
_OUTPUT_FORMAT_ANALYST = (
    "\n\nReAct FORMAT (each on its OWN LINE for tools to run):\n"
    "Thought: <reasoning>\n"
    "Action: check_ticker_data | fetch_ticker_data | run_backtest\n"
    "Action Input: <comma-separated tickers or JSON>\n"
    "When done: Thought: ... then Final Answer: <brief summary>. Do NOT output Python code."
)

# Ana / Emu must not paraphrase or "clean" backtest/MC figures (prevents $1.15M vs $1.2M drift).
_ANALYST_GROWTH_NUMERIC_FIDELITY = (
    "NUMERIC FIDELITY (mandatory): In your Final Answer, every figure you cite from the backtest run must "
    "match the run_backtest tool output exactly. For the **median (P50) portfolio value at the planning horizon**, "
    "use ONLY `terminal_value_p50` from the Monte Carlo section (and the MEDIAN AT HORIZON block, which repeats it). "
    "Do **not** substitute `portfolio_value_at_retirement` (single historical path) "
    "or any other field for that P50 dollar amount. Do not round or change digits (no approximations like "
    "\"about $1.2M\" unless the tool printed that exact rounding). Prefer quoting the tool line verbatim for "
    "dollar amounts.\n\n"
)

_EMU_RETIREMENT_NUMERIC_FIDELITY = (
    "NUMERIC FIDELITY (mandatory): Use ONLY the numbers in the FACTS block above for any statistic you mention "
    "(%, dollars, years, ages, depletion, success probability, TWR, ending values). Copy them exactly—do not "
    "recompute, smooth, optimistically round, or substitute different values. If you cannot find a figure in "
    "FACTS, say to check the tables/charts instead of inventing one.\n\n"
)


def _build_quala_task(agent: Agent, phase: str, chosen_label: str = "", **extra: str) -> Task:
    desc = (_REFINING_ADDENDUM + _QUALA_BASE_DESCRIPTION) if phase == "refining" else _QUALA_BASE_DESCRIPTION
    if phase == "choosing":
        desc += _CHOOSING_ADDENDUM
    desc += _CONVERSATION_VARS
    desc += _OUTPUT_FORMAT_QUALA
    return Task(
        description=desc,
        expected_output="A reply to the user: portfolio options or a refined proposal, with clear next steps.",
        agent=agent,
    )


def _build_panda_task(agent: Agent, phase: str, intake_summary: str = "") -> Task:
    """Build Panda retirement portfolio task."""
    desc = _PANDA_BASE_DESCRIPTION
    if phase == "retirement_refining":
        desc += _PANDA_REFINING_ADDENDUM
    desc += _PANDA_CONVERSATION_VARS
    desc += _PANDA_OUTPUT_FORMAT
    return Task(
        description=desc,
        expected_output="A reply with retirement analysis and 3 yield-focused portfolio options, or refined options.",
        agent=agent,
    )


def _build_analyst_task(agent: Agent) -> Task:
    return Task(
        description=(
            "Session ID: {session_id}\n"
            "You are Ana. The user has chosen a portfolio. Your job: validate it with real data, "
            "then output a summary so the user can save it.\n\n"
            "Chosen portfolio (backtest THIS):\n{chosen_portfolio}\n\n"
            "The JSON between <<<BACKTEST_INPUT>>> and <<<END_BACKTEST_INPUT>>> is the portfolio. "
            "It uses shape {{\"accumulation\": {{\"tickers\": {{...}}, \"sectors\": {{...}}, \"industries\": {{...}}}}}} "
            "when Quala provided asset class and sector breakdowns (JSON \"sectors\" = asset class; \"industries\" = sectors). "
            "Extract ticker symbols from \"tickers\" "
            "(e.g. VTI, BND, VXUS). If \"sectors\" and \"industries\" are present, pass them unchanged "
            "inside accumulation — they populate bar charts after the backtest.\n\n"
            "Follow these steps in order. Use ReAct format (Thought / Action / Action Input). "
            "Do NOT output Python code.\n\n"
            "Step 1 — Check data: Call check_ticker_data with all tickers from the portfolio "
            "(comma-separated, e.g. 'VTI,BND,VXUS,QQQ'). It returns {{present: [...], missing: [...]}}.\n\n"
            "Step 2 — Fetch if needed: If 'missing' is not empty, call fetch_ticker_data with "
            "those tickers (comma-separated). Then optionally re-check with check_ticker_data.\n\n"
            "Step 3 — Run backtest: Call run_backtest with the EXACT string from <<<BACKTEST_INPUT>>> "
            "(full accumulation including tickers, and asset class / sector maps if present). "
            "Legacy flat {{\"accumulation\": {{\"VTI\": 0.4, \"BND\": 0.3}}}} is also valid. "
            "This runs backtesting and Monte Carlo (no-rebalancing scenario).\n\n"
            + _ANALYST_GROWTH_NUMERIC_FIDELITY
            + "Step 4 — Final Answer: After run_backtest succeeds, output:\n"
            "  Thought: <one line>\n"
            "  Final Answer: <2–5 sentences>\n"
            "Include in this order: (a) the median (P50) portfolio value at the planning horizon **exactly as "
            "`terminal_value_p50` in the tool output / MEDIAN AT HORIZON block** (same value, verbatim or "
            "equivalent formatting only), (b) a brief interpretation of risk and growth, "
            "(c) tell the user: please SAVE this growth portfolio, try different scenario planning to explore alternatives, "
            "and/or build a retirement portfolio (with Panda) so they can assemble an end-to-end life plan "
            "(accumulation plus decumulation).\n\n"
            "Charts and tables are shown automatically — do NOT recite long metric tables.\n\n"
            "User message:\n{user_message}\n"
            + _OUTPUT_FORMAT_ANALYST
        ),
        expected_output="Brief summary with P50 horizon value; nudge save growth portfolio, scenarios, and/or retirement for full life plan.",
        agent=agent,
    )


def _build_emu_task(agent: Agent) -> Task:
    """Task for Emu: retirement portfolio analysis. Backtest runs in pre-run; Emu summarizes."""
    return Task(
        description=(
            "Session ID: {session_id}\n"
            "You are Emu. The user chose a retirement portfolio. The retirement backtest and Monte Carlo "
            "have already run; charts and tables are in the UI (including asset class and sector breakdown "
            "bar charts when Panda provided those weights).\n\n"
            "{retirement_mc_facts}\n\n"
            + _EMU_RETIREMENT_NUMERIC_FIDELITY
            + "Your job: Write 2–5 sentences that directly reflect the FACTS block above (especially depleted "
            "fraction, probability of success, and years-to-depletion percentiles). Do not invent better numbers. "
            "If the FACTS show heavy early depletion or low success, your tone must be candid about risk — never "
            "generic praise. Mention median TWR and end-of-horizon median value only as they appear in FACTS. "
            "When results are weak or uncertain, prefer nudging the user to SAVE this portfolio and then use "
            "scenario planning to explore how to improve outcomes — e.g. spending, other income sources like "
            "Social Security or pension, retirement timing, or allocation — "
            "rather than only saying they should reconsider withdrawal rate or asset allocation in the abstract. "
            "End by pointing to the detailed metrics and asset breakdown charts for detail.\n\n"
            "Do NOT call any tools.\n\n"
            "Chosen portfolio: {chosen_portfolio}\n\n"
            "User message:\n{user_message}\n"
        ),
        expected_output="Brief summary aligned with FACTS; if weak, nudge save + scenario planning.",
        agent=agent,
    )


def _build_post_analysis_task(agent: Agent) -> Task:
    return Task(
        description=(
            "Session ID (same as Quala; same session): {session_id}\n"
            "You are Ana. Answer about the GROWTH portfolio that was backtested in this session.\n\n"
            "You handle ONLY growth portfolios. Retirement portfolios are handled by Emu.\n\n"
            "You are answering a follow-up question from the user about "
            "their finalized portfolio.\n\n"
            "Conversation history:\n{conversation_history}\n\n"
            "The chosen portfolio details (for this session):\n{chosen_portfolio}\n\n"
            "CRITICAL: The user's chosen portfolio is described above. "
            "All your answers MUST be based on this specific portfolio — "
            "its exact tickers and weights. Do NOT substitute a different "
            "or generic portfolio.\n\n"
            "Answer the user's question using this portfolio. You have "
            "access to tools for checking data, fetching prices, and "
            "running backtests if needed. Use them when the question "
            "requires data-driven analysis.\n\n"
            "When the user asks to REDO the analysis (e.g. 'add monthly "
            "contribution of 20K and redo', 'redo with different parameters'), "
            "call run_backtest with the chosen portfolio. The system uses "
            "the user's intake (initial value, retirement horizon, monthly "
            "contributions) — these are updated from the user's message and "
            "form data before your run. Do NOT ask for them again.\n\n"
            "If the user asks about changing the portfolio composition "
            "or investment strategy, delegate to Mr. Quala.\n\n"
            + _ANALYST_GROWTH_NUMERIC_FIDELITY
            + "When citing results (P50 value at horizon, TWR, drawdown, MC probabilities), use ONLY numbers "
            "from your latest run_backtest tool output or the same-session charts/tables — never altered figures. "
            "If you need fresh numbers, call run_backtest again first.\n\n"
            "When it fits the conversation (e.g. user asks what to do next, or you are closing a summary), "
            "mention they can save this growth portfolio, try different scenarios, and/or start a retirement "
            "portfolio to build an end-to-end life plan — without repeating long metric tables.\n\n"
            "User message:\n{user_message}\n"
           # + _OUTPUT_FORMAT_ANALYST
        ),
        expected_output="Follow-up answer; when relevant, nudge save + scenarios + retirement for full plan.",
        agent=agent,
    )


def _build_emu_post_analysis_task(agent: Agent) -> Task:
    """Post-analysis for Emu: retirement portfolio follow-ups. No tools."""
    return Task(
        description=(
            "Session ID: {session_id}\n"
            "You are Emu. Answer about the RETIREMENT portfolio that was backtested in this session.\n\n"
            "You are answering a follow-up question from the user about "
            "their retirement portfolio.\n\n"
            "Conversation history:\n{conversation_history}\n\n"
            "The chosen retirement portfolio:\n{chosen_portfolio}\n\n"
            "The retirement backtest and Monte Carlo results are in the UI (charts and tables). "
            "Answer based on those results. You do NOT have tools — you cannot run backtests. "
            "When discussing weak outcomes or how to improve, nudge them to save the portfolio if they want "
            "a baseline, then use scenario planning to try different assumptions (spending, other income such as "
            "Social Security or pension, retirement timing, allocation); avoid only generic advice "
            "like 'reconsider withdrawal rate or asset allocation' without that product path. "
            "If the user asks to redo or change parameters, say you are handing them back to Panda "
            "to rebuild the three retirement portfolios (conservative / moderate / aggressive); "
            "they can type what to change next.\n\n"
            "If the user asks about changing the portfolio composition or strategy, "
            "say Panda will take the next message and regenerate those three portfolios.\n\n"
            + _EMU_RETIREMENT_NUMERIC_FIDELITY
            + "When citing any metric, it must match what the user sees in this session's retirement tables "
            "(same as when Emu first summarized). Do not invent or recalculate.\n\n"
            "User message:\n{user_message}\n"
        ),
        expected_output="An answer to the user's follow-up about their retirement portfolio.",
        agent=agent,
    )


# ------------------------------------------------------------------ #
#  Session management                                                 #
# ------------------------------------------------------------------ #

@dataclass
class ChatSession:
    session_id: str
    history: List[Dict[str, str]] = field(default_factory=list)
    last_portfolio: Optional[str] = None
    # Set from UI portfolio_flow: "growth" (Quala) or "retirement" (Panda)
    portfolio_flow: Optional[str] = None
    last_flow_epoch: int = 0
    phase: str = "portfolio_building"
    chosen_portfolio: Optional[str] = None
    chosen_label: Optional[str] = None
    parsed_portfolios: Optional[Dict[str, Dict[str, float]]] = None
    chosen_composition: Optional[Dict[str, float]] = None
    chosen_retirement_composition: Optional[Dict[str, float]] = None
    chosen_sectors: Optional[Dict[str, float]] = None
    chosen_industries: Optional[Dict[str, float]] = None
    choice_ack_pending: bool = False
    # ("quala"|"panda", message) set when user picks a portfolio; consumed after backtest artifacts are final.
    pending_assistant_handoff: Optional[Tuple[str, str]] = None


class SessionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: Dict[str, ChatSession] = {}

    def get(self, session_id: str) -> ChatSession:
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = ChatSession(session_id=session_id)
            return self._sessions[session_id]


SESSION_STORE = SessionStore()


def _format_history(history: List[Dict[str, str]]) -> str:
    """Format the last 24 history entries, tagging which agent produced each reply."""
    if not history:
        return ""
    lines: List[str] = []
    for turn in history[-24:]:
        role = turn.get("role", "user")
        agent = turn.get("agent", "")
        prefix = f"{role} ({agent})" if agent else role
        content = turn.get("content", "")
        lines.append(f"{prefix}: {content}")
    return "\n".join(lines)


# ------------------------------------------------------------------ #
#  Phase-transition helpers  (pure regex — no LLM calls)              #
# ------------------------------------------------------------------ #

_CHOICE_MAP = {
    "1": "conservative", "a": "conservative",
    "2": "moderate",     "b": "moderate",
    "3": "aggressive",   "c": "aggressive",
}


def _detect_portfolio_choice(message: str, allow_named: bool = True) -> Optional[str]:
    """Return 'conservative'/'moderate'/'aggressive' if the user is
    picking a portfolio, otherwise ``None``.  Pure regex, zero LLM calls.
    When allow_named is False (e.g. during portfolio_building), only
    explicit choices like "1", "option 2" count; words like "moderate"
    in "I want moderate risk" are ignored.
    """
    m = message.strip().lower()

    # Standalone number / letter (always count as choice)
    solo = re.match(r"^\s*([123abc])\s*[.!]?\s*$", m)
    if solo:
        return _CHOICE_MAP.get(solo.group(1))

    # "option 1/2/3/a/b/c"
    opt = re.search(r"\boption\s*([123abc])\b", m, re.IGNORECASE)
    if opt:
        return _CHOICE_MAP.get(opt.group(1).lower())

    # "Go with the X portfolio" / "I'll take X" (explicit choice phrases)
    if re.search(r"go\s+with\s+the\s+(conservative|moderate|aggressive)", m):
        return re.search(r"go\s+with\s+the\s+(conservative|moderate|aggressive)", m).group(1)
    if re.search(r"i'?ll\s+take\s+(?:the\s+)?(conservative|moderate|aggressive)", m):
        return re.search(r"i'?ll\s+take\s+(?:the\s+)?(conservative|moderate|aggressive)", m).group(1)

    if not allow_named:
        return None

    # Named portfolio mentions (only when allow_named: avoids "moderate risk" in intake)
    if re.search(r"\bconservative\b", m):
        return "conservative"
    if re.search(r"\bmoderate\b", m):
        return "moderate"
    if re.search(r"\baggressive\b", m):
        return "aggressive"

    return None


_YES_RE = re.compile(
    r"^\s*(?:yes|yeah|yep|yup|sure|ok|okay|go\s*ahead|finalize|"
    r"do\s+it|proceed|absolutely|definitely|y|let'?s\s*do\s*it)\s*[.!]?\s*$",
    re.IGNORECASE,
)
_NO_RE = re.compile(
    r"^\s*(?:no|nah|nope|not?\s*yet|wait|hold\s*on|reconsider|"
    r"change|back|n)\s*[.!]?\s*$",
    re.IGNORECASE,
)


def _detect_yes_no(message: str) -> Optional[str]:
    """Return ``'yes'`` / ``'no'`` / ``None``.  Pure regex."""
    if _YES_RE.match(message.strip()):
        return "yes"
    if _NO_RE.match(message.strip()):
        return "no"
    return None


_REFINE_RE = re.compile(
    r"\b(?:refin|tweak|adjust|chang|modif|custom|add\s+(?:some\s+)?(?:stock|metal|crypto|bond|ticker)s?|"
    r"add\s+[A-Za-z]{2,5}\b|add\s+\d+%?\s*[A-Za-z]{2,5}\b|include\s+\w+|more\s+(?:yield|dividend|bonds?|fixed\s+income|equities?|stocks?|"
    r"cash|gold|silver|[A-Za-z]{2,5}\b)|less\s+\w+|reduce\s+\w+|shift\s+(?:to|toward)|tilt\s+(?:to|toward)|"
    r"yield\s*[>\d%]|\byield\b.*\d|(?:stock|stocks).*\byield\b|\byield\b.*(?:above|over|higher))\b",
    re.IGNORECASE,
)

def _last_assistant_agent(session: ChatSession) -> Optional[str]:
    """Return the agent who produced the most recent assistant message before the latest user turn(s)."""
    i = len(session.history) - 1
    while i >= 0 and session.history[i].get("role") == "user":
        i -= 1
    while i >= 0:
        if session.history[i].get("role") == "assistant":
            return session.history[i].get("agent")
        i -= 1
    return None


def _is_in_retirement_flow(session: ChatSession) -> bool:
    """True if user chose or is working on retirement portfolio."""
    flow = (session.portfolio_flow or "").strip().lower()
    if flow == "growth":
        return False
    if flow == "retirement":
        return True
    if bool(session.chosen_retirement_composition):
        return True
    if session.phase in ("retirement_planning", "retirement_choosing", "retirement_refining"):
        return True
    # Fallback: last agent was Panda or Emu -> retirement flow (refine before choice = Panda; after = Emu)
    last = _last_assistant_agent(session)
    if last in ("Panda", "Emu"):
        return True
    return False


def _refinement_agent(session: ChatSession) -> str:
    """Return which agent should handle refinement: Panda (retirement) or Quala (growth).
    Refinement goes to the agent that created the portfolios being refined."""
    if bool(session.chosen_retirement_composition):
        return "Panda"
    if session.phase in ("retirement_planning", "retirement_choosing", "retirement_refining"):
        return "Panda"
    last = _last_assistant_agent(session)
    if last in ("Panda", "Emu"):
        return "Panda"
    return "Quala"


def _detect_refine(message: str) -> bool:
    """Return True if the user wants to keep refining."""
    return bool(_REFINE_RE.search(message))


_WORK_ON_GROWTH_PORTFOLIO_RE = re.compile(r"\bwork\s+on\s+growth\s+portfolio\b", re.IGNORECASE)
_WORK_ON_RETIREMENT_PORTFOLIO_RE = re.compile(
    r"\bwork\s+on\s+(?:my\s+)?retirement\s+portfolio\b", re.IGNORECASE
)


def _resolve_portfolio_flow(
    portfolio_flow: Optional[str],
    message: str,
) -> Optional[str]:
    """UI field wins; else explicit closing line from welcome buttons."""
    flow = (portfolio_flow or "").strip().lower()
    if flow in ("growth", "retirement"):
        return flow
    text = message or ""
    if _WORK_ON_RETIREMENT_PORTFOLIO_RE.search(text):
        return "retirement"
    if _WORK_ON_GROWTH_PORTFOLIO_RE.search(text):
        return "growth"
    return None


def _apply_portfolio_flow_choice(
    session: ChatSession,
    portfolio_flow: Optional[str],
    current_phase: str,
) -> bool:
    """Apply resolved portfolio_flow. Returns True if this turn switched to retirement_planning."""
    flow = (portfolio_flow or "").strip().lower()
    if flow not in ("growth", "retirement"):
        return False
    session.portfolio_flow = flow
    if flow == "retirement":
        # Do not reset when user is picking 1/2/3 (frontend may resend portfolio_flow=retirement).
        if current_phase in (
            "retirement_choosing",
            "retirement_refining",
            "analyst_running",
            "post_analysis",
        ):
            logger.info(
                "portfolio_flow=retirement kept phase=%s (no reset to retirement_planning)",
                current_phase,
            )
            return False
        session.phase = "retirement_planning"
        session.parsed_portfolios = None
        logger.info(
            "portfolio_flow=retirement -> retirement_planning (was %s)",
            current_phase,
        )
        return True
    if current_phase in ("retirement_planning", "retirement_choosing", "retirement_refining"):
        session.phase = "portfolio_building"
        session.parsed_portfolios = None
        logger.info(
            "portfolio_flow=growth -> portfolio_building (was %s)",
            current_phase,
        )
    return False


def _sync_phase_to_portfolio_flow(session: ChatSession) -> None:
    """Keep phase aligned with portfolio_flow before crew selection."""
    flow = (session.portfolio_flow or "").strip().lower()
    if flow == "retirement":
        if session.phase in ("portfolio_building", "choosing", "refining"):
            session.phase = "retirement_planning"
    elif flow == "growth":
        if session.phase in (
            "retirement_planning",
            "retirement_choosing",
            "retirement_refining",
        ) and not session.chosen_retirement_composition:
            session.phase = "portfolio_building"


def _portfolio_builder_is_panda(session: ChatSession) -> bool:
    """Quala/Ana vs Panda/Emu for portfolio construction — portfolio_flow is authoritative."""
    flow = (session.portfolio_flow or "").strip().lower()
    if flow == "retirement":
        return True
    if flow == "growth":
        return False
    return session.phase in ("retirement_planning", "retirement_choosing", "retirement_refining")


def _is_retirement_portfolio_session(session: ChatSession) -> bool:
    """True when the user is in the Panda/Emu retirement portfolio path (not Quala/Ana growth)."""
    flow = (session.portfolio_flow or "").strip().lower()
    if flow == "retirement":
        return True
    if flow == "growth":
        return False
    if session.phase in ("retirement_planning", "retirement_choosing", "retirement_refining"):
        return True
    last = _last_assistant_agent(session)
    if last in ("Panda", "Emu"):
        return True
    return bool(session.chosen_retirement_composition)


def _try_apply_portfolio_pick(
    session: ChatSession,
    message: str,
    current_phase: str,
) -> bool:
    """If user picked conservative/moderate/aggressive, route to analyst_running (Ana or Emu)."""
    allow_named = current_phase in ("choosing", "retirement_choosing")
    choice = _detect_portfolio_choice(message, allow_named=allow_named)
    if not choice or not session.parsed_portfolios or choice not in session.parsed_portfolios:
        if choice:
            logger.warning(
                "Portfolio choice '%s' not applied: parsed_portfolios=%s phase=%s flow=%s",
                choice,
                list(session.parsed_portfolios.keys()) if session.parsed_portfolios else None,
                current_phase,
                session.portfolio_flow,
            )
        return False

    is_retirement = _is_retirement_portfolio_session(session)
    if is_retirement:
        session.portfolio_flow = "retirement"
    _capture_chosen_portfolio(session, message, is_retirement=is_retirement)
    session.chosen_label = choice
    session.phase = "analyst_running"
    session.choice_ack_pending = True
    lbl = (choice or "your").capitalize()
    if is_retirement:
        session.pending_assistant_handoff = (
            "panda",
            f"Thanks for choosing the {lbl} portfolio. "
            "I'm asking our analyst Emu to run backtesting and Monte Carlo using more accurate data.",
        )
    else:
        session.pending_assistant_handoff = (
            "quala",
            f"Thank you for choosing the {lbl} portfolio. "
            "Let me ask our analyst Ana for a detailed analysis.",
        )
    logger.info(
        "User chose %s portfolio %s -> analyst_running (%s)",
        "retirement" if is_retirement else "growth",
        choice,
        "Emu" if is_retirement else "Ana",
    )
    return True


def _detect_finance_refinement_followup(message: str) -> bool:
    """True when a post-analysis message should route to Quala/Panda for finance Q&A, not only explicit 'refine' regex."""
    if not message or not isinstance(message, str):
        return False
    m = message.strip()
    if len(m) < 8:
        return False
    low = m.lower()
    # Question or open-ended finance discussion
    if "?" in m:
        return True
    if re.search(
        r"\b(?:what|why|how|when|where|should|could|would|explain|describe|compare|pros|cons|"
        r"tell\s+me\s+about|help\s+me\s+understand|difference\s+between|which\s+is\s+better)\b",
        low,
    ):
        return True
    if re.search(
        r"\b(?:annuit|yield|dividend|bond|ira|401k|roth|tax|social\s+security|pension|risk|"
        r"etf|mutual\s+fund|fee|expense|inflation|withdrawal|rmd|reit|commodit|"
        r"market|stock|fund|recession|interest\s+rate|fed|income|allocation|"
        r"rebalanc|diversif|leverage|margin|option|portfolio|macro|economy|cpi|"
        r"deflation|credit|mortgage|refinanc|estate\s+plan)\b",
        low,
    ):
        return True
    return False


def _count_user_turns(history: List[Dict[str, str]]) -> int:
    return sum(1 for h in history if h.get("role") == "user")


_PORTFOLIOS_JSON_RE = re.compile(
    r"<<<PORTFOLIOS_JSON>>>\s*(\{.*?\})\s*<<<END_PORTFOLIOS_JSON>>>",
    re.DOTALL,
)
# Broader regex to strip the entire JSON block from user-facing output (any content between markers)
_STRIP_PORTFOLIOS_RE = re.compile(
    r"<<<PORTFOLIOS_JSON>>>.*?<<<END_PORTFOLIOS_JSON>>>",
    re.DOTALL,
)
# Spurious empty JSON code blocks (LLM sometimes outputs ```json  ```)
_STRIP_SPURIOUS_JSON_BLOCK_RE = re.compile(r"```\s*json\s*```", re.IGNORECASE)


def _extract_run_backtest_input(text: str) -> Optional[str]:
    """Best-effort extraction of the JSON passed to run_backtest from a
    text-only ReAct-style reply or from Python-style output like:
    print(default_api.run_backtest(portfolio_json='{"accumulation": {...}}'))
    Returns the JSON string (without extra quotes) or None.
    """
    # 1. Try ReAct format: Action: run_backtest, Action Input: {...}
    try:
        idx = text.index("Action:")
    except ValueError:
        idx = -1
    if idx >= 0:
        sub = text[idx:]
        if "run_backtest" in sub:
            try:
                ai_idx = sub.index("Action Input:")
                after = sub[ai_idx + len("Action Input:") :]
                line = after.strip().splitlines()[0].strip()
                if line:
                    if (line.startswith("'''") and line.endswith("'''")) or (
                        line.startswith('"""') and line.endswith('"""')
                    ):
                        line = line[3:-3].strip()
                    elif len(line) >= 2 and line[0] in "\"'`" and line[-1] == line[0]:
                        line = line[1:-1].strip()
                    if line and "accumulation" in line:
                        return line
            except ValueError:
                pass

    # 2. Salvage from Python-style: portfolio_json='{"accumulation": {...}}' (single-quoted, JSON inside)
    m = re.search(r"portfolio_json\s*=\s*'(\{[^']+\})'", text)
    if m:
        return m.group(1)
    m = re.search(r'portfolio_json\s*=\s*"(\{[^"]+\})"', text)
    if m:
        return m.group(1)
    # run_backtest('{"accumulation":...}')
    m = re.search(r"run_backtest\s*\(\s*'(\{[^']+\})'", text)
    if m:
        return m.group(1)
    m = re.search(r'run_backtest\s*\(\s*"(\{[^"]+\})"', text)
    if m:
        return m.group(1)
    return None


def _summary_from_artifacts(artifacts: dict) -> str:
    """Build a short 2–4 sentence summary from backtest artifacts for the user when
    Ana's/Emu's reply is missing, raw tool output, or incomplete.
    """
    scenarios = artifacts.get("scenarios") or []
    if not scenarios:
        return (
            "Backtest and Monte Carlo have run. See the charts and metrics below. "
            "Save this growth portfolio if you want a baseline, try different scenario planning, "
            "and consider building a retirement portfolio for an end-to-end life plan."
        )
    s0 = scenarios[0]

    # Retirement-specific summary (Emu)
    if artifacts.get("is_retirement"):
        mc = s0.get("monte_carlo") or {}
        depleted = mc.get("depleted_fraction")
        pos = mc.get("probability_of_success")
        lon_p50 = mc.get("portfolio_longevity_p50")
        lon_p10 = mc.get("portfolio_longevity_p10")
        twr_p50 = mc.get("twr_p50")
        p50_end = mc.get("portfolio_value_p50_end")
        parts = []
        if depleted is not None:
            parts.append(
                f"In Monte Carlo simulations, {depleted:.1%} of paths run out of money before the end of the horizon."
            )
        if pos is not None:
            parts.append(
                f"The estimated probability of surviving the full modeled horizon is about {pos:.1%}."
            )
        if lon_p50 is not None:
            parts.append(
                f"Median years until depletion (0 = broke immediately; capped at horizon): about {float(lon_p50):.1f}."
            )
        elif lon_p10 is not None:
            parts.append(
                f"Stress-case (10th percentile) years until depletion: about {float(lon_p10):.1f}."
            )
        if twr_p50 is not None:
            parts.append(f"The median (P50) time-weighted return is about {twr_p50:.2%} annualized.")
        if p50_end is not None and p50_end > 0:
            parts.append(f"The median portfolio value at the end of the modeled horizon is roughly {_fmt_val(p50_end)}.")
        if depleted is not None and depleted >= 0.4:
            parts.append(
                "Consider saving this portfolio as a baseline, then using scenario planning to try different "
                "assumptions — such as spending, other income sources like Social Security or pension, retirement timing, "
                "or allocation — and see how you might improve these outcomes."
            )
        parts.append(
            "Review the detailed metrics and asset breakdown charts below. You can save this portfolio with the Save button."
        )
        return " ".join(parts).strip()
    m = s0.get("metrics") or {}
    mc = s0.get("monte_carlo") or {}
    cagr = m.get("cagr")
    sharpe = m.get("sharpe_ratio")
    max_dd = m.get("max_drawdown")
    prob_loss = mc.get("prob_loss")
    p50 = mc.get("terminal_value_p50")
    parts = []
    if cagr is not None or sharpe is not None:
        line = "Based on the backtest, this portfolio delivered "
        if cagr is not None:
            line += f"a time-weighted return around {cagr:.2%}"
        if cagr is not None and sharpe is not None:
            line += " with "
        if sharpe is not None:
            line += f"a Sharpe ratio of about {sharpe:.2}"
        line += ", balancing growth and risk with meaningful ups and downs along the way."
        parts.append(line)
    if max_dd is not None:
        parts.append(f"In the worst historical stretch, the portfolio fell about {max_dd:.2%} from peak to trough.")
    if prob_loss is not None:
        parts.append(f"In Monte Carlo simulations, the chance of finishing with less than you started is about {prob_loss:.1%}.")
    if p50 is not None:
        parts.append(
            f"The median (P50) portfolio value at retirement (end of the planning horizon) is roughly {_fmt_val(p50)}."
        )
    parts.append(
        "The detailed numbers are in the tables and charts below. "
        "Save this growth portfolio if you want a baseline, try different scenario planning, "
        "and consider building a retirement portfolio with Panda for an end-to-end life plan."
    )
    return " ".join(parts).strip()


def _strip_agent_thought(text: str) -> str:
    """Remove Thought:, Action:, Action Input: from agent output so only the user-facing reply is shown."""
    if not text or not isinstance(text, str):
        return text
    s = text.strip()
    # If Final Answer: present (case-insensitive), use only that
    m = re.search(r"Final\s+Answer\s*:\s*", s, re.IGNORECASE)
    if m:
        out = s[m.end():].strip()
        if out:
            return out
    # Strip Thought: block (multiline until \n\n, or single line)
    s = re.sub(r"Thought\s*:\s*.*?\n\n", "", s, flags=re.DOTALL | re.IGNORECASE)
    s = re.sub(r"Thought\s*:\s*[^\n]+\n?", "", s, flags=re.IGNORECASE)
    s = re.sub(r"Action\s*:\s*[^\n]*(?:\n\s*Action\s+Input\s*:\s*[^\n]*)?", "", s, flags=re.IGNORECASE)
    out = re.sub(r"\n{3,}", "\n\n", s).strip()
    return out if out else ""


_ESTIMATE_PLACEHOLDER_RE = re.compile(
    r"^\.{1,6}$|^…$|^tbd$|^n/a$|^na$|^-$|^pending$|^unknown$",
    re.IGNORECASE,
)

# Fallback when Panda/Quala copy "..." from an old prompt template
_CRASH_ESTIMATE_DEFAULTS: Dict[str, Dict[str, str]] = {
    "conservative": {
        "crash_2008": (
            "Estimated loss of 8% - 14%. Bond-heavy income mix typically fared better than pure equity in 2008."
        ),
        "crash_2022": (
            "Estimated loss of 6% - 12%. Bonds and REITs declined with rising rates; smaller equity sleeve."
        ),
    },
    "moderate": {
        "crash_2008": (
            "Estimated loss of 14% - 20%. Mixed bonds and dividend equities; partial buffer versus equities alone."
        ),
        "crash_2022": (
            "Estimated loss of 12% - 18%. Rate-driven selloff in bonds and income equities together."
        ),
    },
    "aggressive": {
        "crash_2008": (
            "Estimated loss of 28% - 38%. Higher equity and yield tilt; limited bond buffer in 2008."
        ),
        "crash_2022": (
            "Estimated loss of 20% - 28%. Growth and high-yield sleeves sensitive to rate hikes in 2022."
        ),
    },
}


def _sanitize_estimate_placeholders(portfolios: Optional[Dict[str, dict]]) -> None:
    """Replace literal '...' crash placeholders with tier-appropriate LLM-style estimates."""
    if not portfolios:
        return
    for name, p in portfolios.items():
        if not isinstance(p, dict):
            continue
        est = p.get("estimates")
        if not isinstance(est, dict):
            continue
        defaults = _CRASH_ESTIMATE_DEFAULTS.get(
            name.lower(), _CRASH_ESTIMATE_DEFAULTS["moderate"]
        )
        for key in ("crash_2008", "crash_2022"):
            raw = est.get(key)
            if raw is None or _ESTIMATE_PLACEHOLDER_RE.match(str(raw).strip()):
                est[key] = defaults[key]
                logger.warning(
                    "Replaced placeholder estimates.%s for portfolio %s",
                    key,
                    name,
                )


def _parse_portfolios_json(text: str) -> Optional[Dict[str, dict]]:
    """Extract the structured portfolios JSON from Quala's response.

    Returns a dict like::

        {"conservative": {"tickers": {...}, "sectors": {...}, "industries": {...}}, ...}

    Also handles the legacy flat format where each portfolio is just
    {ticker: weight} (promotes it into the nested shape).
    """
    match = _PORTFOLIOS_JSON_RE.search(text)
    json_str = match.group(1) if match else None
    if not json_str:
        # Fallback: try markdown code block (LLM sometimes uses ```json ... ```)
        code_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if code_match:
            json_str = code_match.group(1)
    if not json_str:
        return None
    try:
        data = json.loads(json_str)
        if not isinstance(data, dict):
            return None

        def _parse_weight_map(raw, upper_keys=False):
            def _to_float(x):
                if isinstance(x, (int, float)):
                    return float(x)
                if isinstance(x, dict):
                    return float(x.get("weight", x.get("value", x.get("amount", 0))))
                if isinstance(x, str):
                    s = x.strip().rstrip("%")
                    try:
                        v = float(s)
                        return v / 100.0 if "%" in x else v
                    except ValueError:
                        return 0.0
                try:
                    return float(x)
                except (TypeError, ValueError):
                    return 0.0

            if isinstance(raw, dict):
                out = {
                    (k.strip().upper() if upper_keys else str(k)): _to_float(v)
                    for k, v in raw.items()
                }
                if out and any(v > 0 for v in out.values()):
                    return out
            # Fallback: list of ticker strings -> equal weights
            if isinstance(raw, list):
                items = []
                for item in raw:
                    if isinstance(item, str) and item.strip():
                        items.append((item.strip().upper(), 1.0))
                    elif isinstance(item, dict):
                        sym = item.get("ticker", item.get("symbol", item.get("name", "")))
                        w = _to_float(item.get("weight", item.get("value", item.get("amount", 0))))
                        if sym:
                            items.append((str(sym).strip().upper(), w if w > 0 else 1.0))
                if items:
                    total = sum(w for _, w in items)
                    if total > 0:
                        return {k: (w / total) for k, w in items}
            return {}

        result: Dict[str, dict] = {}
        for name, val in data.items():
            key = name.lower()
            if not isinstance(val, dict):
                continue
            # Format: {"accumulation": {tickers, sectors, industries}, "retirement": {...}}
            if "accumulation" in val and isinstance(val["accumulation"], dict):
                acc = val["accumulation"]
                entry = {
                    "tickers": _parse_weight_map(acc.get("tickers"), upper_keys=True),
                    "sectors": _parse_weight_map(acc.get("sectors")),
                    "industries": _parse_weight_map(acc.get("industries")),
                }
                if isinstance(acc.get("estimates"), dict):
                    entry["estimates"] = {k: str(v) for k, v in acc["estimates"].items() if v}
                if "retirement" in val and isinstance(val["retirement"], dict):
                    r = val["retirement"]
                    entry["retirement"] = {
                        "tickers": _parse_weight_map(r.get("tickers"), upper_keys=True),
                        "sectors": _parse_weight_map(r.get("sectors")),
                        "industries": _parse_weight_map(r.get("industries")),
                    }
                result[key] = entry
            # Format: {"tickers": {...}, "sectors": {...}, "retirement": {...}}
            elif "tickers" in val:
                entry = {
                    "tickers": _parse_weight_map(val.get("tickers"), upper_keys=True),
                    "sectors": _parse_weight_map(val.get("sectors")),
                    "industries": _parse_weight_map(val.get("industries")),
                }
                if isinstance(val.get("estimates"), dict):
                    entry["estimates"] = {k: str(v) for k, v in val["estimates"].items() if v}
                if "retirement" in val and isinstance(val["retirement"], dict):
                    r = val["retirement"]
                    entry["retirement"] = {
                        "tickers": _parse_weight_map(r.get("tickers"), upper_keys=True),
                        "sectors": _parse_weight_map(r.get("sectors")),
                        "industries": _parse_weight_map(r.get("industries")),
                    }
                result[key] = entry
            else:
                result[key] = {
                    "tickers": _parse_weight_map(val, upper_keys=True),
                    "sectors": {},
                    "industries": {},
                }
        return result if result else None
    except (json.JSONDecodeError, ValueError, AttributeError):
        logger.warning("Failed to parse PORTFOLIOS_JSON block")
    return None


def _strip_portfolios_json(text: str) -> str:
    """Remove the machine-readable JSON block from the user-facing reply.
    Uses a permissive regex so any content between markers is stripped.
    Also removes spurious empty JSON code blocks (e.g. ```json  ```).
    """
    t = _STRIP_PORTFOLIOS_RE.sub("", text)
    t = _STRIP_SPURIOUS_JSON_BLOCK_RE.sub("", t)
    return t.rstrip()


def _deduplicate_repeated_block(text: str) -> str:
    """Remove duplicated portfolio-presentation block if the LLM repeated it."""
    markers = ("Which portfolio would you like to go with?", "Please choose one to proceed")
    for marker in markers:
        if text.count(marker) >= 2:
            first_end = text.find(marker)
            if first_end == -1:
                continue
            after_marker = text.find("Aggressive", first_end)
            if after_marker == -1:
                continue
            end_of_first = after_marker + len("Aggressive")
            second_start = text.find(marker, end_of_first)
            if second_start != -1:
                return text[:second_start].rstrip()
    return text


def _capture_chosen_portfolio(session: "ChatSession", message: str, *, is_retirement: bool = False) -> None:
    """Save the user's portfolio choice along with Quala's/Panda's last response."""
    if not is_retirement:
        # Growth pick must not leave a stale retirement composition (would route handoff to Panda/Emu).
        session.chosen_retirement_composition = None
    label = "unknown"
    m = message.strip().lower()
    for name in ("conservative", "moderate", "aggressive"):
        if name in m:
            label = name
            break
    else:
        mapping = {"1": "conservative", "2": "moderate", "3": "aggressive",
                   "a": "conservative", "b": "moderate", "c": "aggressive"}
        solo = re.match(r"^\s*([123abc])\s*[.!]?\s*$", m)
        if solo:
            label = mapping.get(solo.group(1), "unknown")

    last_assistant_msg = ""
    for h in reversed(session.history):
        if h.get("role") == "assistant":
            last_assistant_msg = h.get("content", "")
            break

    session.chosen_sectors = None
    session.chosen_industries = None
    if session.parsed_portfolios and label in session.parsed_portfolios:
        p = session.parsed_portfolios[label]
        tickers = p.get("tickers", p) if isinstance(p, dict) else p
        session.chosen_composition = tickers
        session.chosen_retirement_composition = tickers if is_retirement else None
        if isinstance(p, dict):
            if isinstance(p.get("sectors"), dict) and p["sectors"]:
                session.chosen_sectors = normalize_asset_class_weights(
                    _normalize_sector_industry_weights(p["sectors"])
                )
            if isinstance(p.get("industries"), dict) and p["industries"]:
                session.chosen_industries = normalize_gics_industry_weights(
                    _normalize_sector_industry_weights(p["industries"])
                )

    # Include explicit JSON for run_backtest so Ana does not need to delegate
    ticker_json = ""
    if session.chosen_composition and isinstance(session.chosen_composition, dict):
        acc_body: Dict[str, Any] = {"tickers": dict(session.chosen_composition)}
        if session.chosen_sectors:
            acc_body["sectors"] = session.chosen_sectors
        if session.chosen_industries:
            acc_body["industries"] = session.chosen_industries
        ticker_json = json.dumps({"accumulation": acc_body})

    portfolio_type = "retirement" if is_retirement else "growth"
    if ticker_json:
        analyst_tool = "run_backtest" if not is_retirement else "the system pre-runs the retirement backtest"
        session.chosen_portfolio = (
            f"CHOSEN PORTFOLIO: The user selected the **{label.upper()}** "
            f"{portfolio_type} portfolio. Use ONLY this portfolio — ignore the other two.\n\n"
            f"CRITICAL: The JSON between markers includes **tickers**, and when present **asset class** "
            f"(JSON key \"sectors\") and **sectors** (JSON key \"industries\") weight maps (all JSON objects). "
            f"{'Pass the entire accumulation object to run_backtest unchanged — those maps are required for breakdown charts.' if not is_retirement else 'Emu: these maps are shown as asset class and sector bar charts in the UI.'}\n\n"
            f"<<<BACKTEST_INPUT>>>\n{ticker_json}\n<<<END_BACKTEST_INPUT>>>\n\n"
            + (
                f"You MUST run backtesting and Monte Carlo. Pass the JSON above to run_backtest.\n\n"
                if not is_retirement
                else "Retirement MC is executed automatically from the tickers; asset class / sector JSON is for UI charts only.\n\n"
            )
            + f"{'Panda' if is_retirement else 'Quala'}'s full proposal for reference:\n{last_assistant_msg}"
        )
    else:
        session.chosen_portfolio = (
            f"CHOSEN PORTFOLIO: The user selected the **{label.upper()}** "
            f"{portfolio_type} portfolio. Extract tickers and weights from the proposal below "
            f"and run backtesting.\n\nQuala's full proposal:\n{last_assistant_msg}"
        )


# ------------------------------------------------------------------ #
#  Main entry point                                                   #
# ------------------------------------------------------------------ #

def run_message(
    session_id: str,
    message: str,
    inputs: Optional[Dict[str, str]] = None,
    intake_payload: Optional[Dict[str, object]] = None,
    portfolio_flow: Optional[str] = None,
    flow_epoch: Optional[int] = None,
    retirement_refinement_after_emu: bool = False,
    user_id: Optional[str] = None,
    token_usage_source: str = "crew_money_manager",
) -> Dict[str, Any]:
    session = SESSION_STORE.get(session_id)
    session.history.append({"role": "user", "content": message})

    if flow_epoch is not None:
        try:
            epoch = int(flow_epoch)
        except (TypeError, ValueError):
            epoch = None
        if epoch is not None and epoch < session.last_flow_epoch:
            session.history.pop()
            logger.info(
                "Ignoring stale money-manager request flow_epoch=%s < last=%s",
                epoch,
                session.last_flow_epoch,
            )
            return {"reply": "", "artifacts": {}, "agent": None, "stale": True}
        if epoch is not None:
            session.last_flow_epoch = epoch

    # Parse intake from user message and merge with existing (form data takes precedence)
    try:
        from backend.intake_parser import parse_intake_from_text
        from backtesting.backtesting_service.types import IntakeContext, retirement_expense_inflation_years
        parsed = parse_intake_from_text(message)
        with _INTAKE_STORE_LOCK:
            existing = INTAKE_CONTEXT_STORE.get(session_id)
        if existing and isinstance(existing, IntakeContext):
            # Merge: keep form data (birth_dates, planning_for, retirement_monthly_target)
            # update only fields that parser found
            if parsed.initial_value != 1.0:
                object.__setattr__(existing, "initial_value", parsed.initial_value)
            if parsed.monthly_savings > 0:
                object.__setattr__(existing, "monthly_savings", parsed.monthly_savings)
            if parsed.horizon_years is not None:
                object.__setattr__(existing, "horizon_years", parsed.horizon_years)
            if parsed.current_monthly_expense > 0:
                object.__setattr__(existing, "current_monthly_expense", parsed.current_monthly_expense)
                inf_rate = getattr(existing, "inflation_rate", 0.03) or 0.03
                infl_y = retirement_expense_inflation_years(
                    getattr(existing, "planning_for", "self"),
                    getattr(existing, "retirement_status", None),
                    getattr(existing, "horizon_years", None),
                    getattr(existing, "retirement_timeline_self", None),
                    getattr(existing, "retirement_timeline_partner", None),
                )
                inferred = parsed.current_monthly_expense * ((1 + inf_rate) ** infl_y)
                object.__setattr__(existing, "retirement_monthly_target", inferred)
            if parsed.display_unit is not None:
                object.__setattr__(existing, "display_unit", parsed.display_unit)
            intake = existing
        elif parsed.initial_value != 1.0 or parsed.monthly_savings > 0:
            intake = parsed
            with _INTAKE_STORE_LOCK:
                INTAKE_CONTEXT_STORE[session_id] = intake
        else:
            intake = None
        if intake:
            _sp = str(getattr(intake, "spending", None) or "").strip()
            logger.info(
                "Intake: initial=%.0f, monthly_savings=%.0f, horizon=%s, spending_len=%s",
                intake.initial_value,
                intake.monthly_savings,
                intake.horizon_years,
                len(_sp),
            )
    except Exception as e:
        logger.debug("Intake parse skipped: %s", e)

    user_turns = _count_user_turns(session.history)
    if (
        retirement_refinement_after_emu
        and session.phase == "post_analysis"
        and session.chosen_retirement_composition
    ):
        session.portfolio_flow = "retirement"
        session.phase = "retirement_refining"
        logger.info(
            "retirement_refinement_after_emu: post_analysis -> retirement_refining (handoff to Panda)"
        )
    current_phase = session.phase

    # ---- phase transitions (pure regex, no LLM calls) ----
    #
    # Phase meanings:
    # FLOW: Growth = Quala -> Ana (Quala builds portfolios, Ana runs backtest, Quala delivers summary)
    #       Retirement = Panda -> Emu (Panda builds portfolios, Emu runs backtest, Panda delivers summary)
    # - portfolio_building, choosing, refining: Growth flow (Quala). User builds/refines growth portfolios.
    # - retirement_planning, retirement_choosing, retirement_refining: Retirement flow (Panda).
    # - analyst_running: Backtest in progress (Ana+Quala for growth, Emu for retirement).
    # - post_analysis: User has chosen a portfolio and backtest completed. UI shows results with Save/Refine.
    #   Refinement here routes by session state (chosen_retirement_composition -> Panda, else Quala).
    #
    # "Refining before choice": When user is shown 3 options (choosing/retirement_choosing) but hasn't
    # picked one. Any non-choice message = refinement (no need to explicitly say "refine" or click refine).

    resolved_flow = _resolve_portfolio_flow(portfolio_flow, message)
    # Pick 1/2/3 before portfolio_flow button logic — resending portfolio_flow=retirement
    # must not wipe parsed_portfolios or skip Emu handoff.
    portfolio_pick_applied = _try_apply_portfolio_pick(session, message, current_phase)

    if portfolio_pick_applied:
        flow_explicit = False
        if resolved_flow in ("growth", "retirement"):
            session.portfolio_flow = resolved_flow
    else:
        flow_explicit = resolved_flow in ("growth", "retirement")
        if flow_explicit:
            _apply_portfolio_flow_choice(session, resolved_flow, current_phase)
        _sync_phase_to_portfolio_flow(session)

    if not portfolio_pick_applied and not flow_explicit and current_phase in (
        "portfolio_building",
        "choosing",
        "refining",
    ):
        allow_named = current_phase == "choosing"
        choice = _detect_portfolio_choice(message, allow_named=allow_named)
        if session.phase != "analyst_running" and (
            _detect_refine(message)
            or (
                current_phase == "choosing"
                and not (
                    choice
                    and session.parsed_portfolios
                    and choice in (session.parsed_portfolios or {})
                )
            )
        ):
            if _refinement_agent(session) == "Panda":
                session.phase = "retirement_refining"
                logger.info("User refine (portfolio by Panda) -> retirement_refining (Panda)")
            else:
                session.phase = "refining"
                logger.info("User refine (portfolio by Quala) -> refining (Quala)")
        elif current_phase == "portfolio_building" and user_turns >= 6:
            session.phase = "choosing"

    elif not portfolio_pick_applied and not flow_explicit and current_phase in (
        "retirement_planning",
        "retirement_choosing",
        "retirement_refining",
    ):
        allow_named = current_phase == "retirement_choosing"
        choice = _detect_portfolio_choice(message, allow_named=allow_named)
        if _detect_refine(message) or (
            current_phase == "retirement_choosing"
            and not (
                choice
                and session.parsed_portfolios
                and choice in (session.parsed_portfolios or {})
            )
        ):
            session.phase = "retirement_refining"
            logger.info("User refine retirement portfolio before choosing -> retirement_refining (Panda)")
        elif current_phase == "retirement_planning" and user_turns >= 2:
            session.phase = "retirement_choosing"

    elif (
        not flow_explicit
        and current_phase in ("analyst_running", "post_analysis")
        and (_detect_refine(message) or _detect_finance_refinement_followup(message))
    ):
        # Route to agent that created the portfolio: Panda (retirement) or Quala (growth)
        agent = _refinement_agent(session)
        logger.info(
            "Refine in post_analysis: chosen_retirement=%s chosen_composition=%s -> %s",
            bool(session.chosen_retirement_composition),
            bool(session.chosen_composition),
            agent,
        )
        if agent == "Panda":
            session.phase = "retirement_refining"
            logger.info("User refine (portfolio by Panda) -> retirement_refining (Panda)")
        else:
            session.phase = "refining"
            logger.info("User refine (portfolio by Quala) -> refining (Quala)")
    elif not flow_explicit and current_phase == "analyst_running":
        # Generic follow-up during backtest phase -> move to post_analysis
        session.phase = "post_analysis"

    logger.info(
        "session=%s | user_turns=%d | phase: %s -> %s | msg=%.60s",
        session_id, user_turns, current_phase, session.phase,
        message.replace("\n", " "),
    )

    # ---- build agents & inputs ----
    llm_mm = build_llm("money_manager")
    llm_analyst = build_llm("analyst")
    agents = build_agents(llm_mm, llm_analyst)
    conversation_context = _format_history(session.history)

    # Shared by Quala and Ana; session_id is injected into LLM prompt so each request is unique (cache busting)
    chosen_composition_json = ""
    if session.chosen_composition and isinstance(session.chosen_composition, dict):
        chosen_composition_json = json.dumps({"accumulation": session.chosen_composition})
    with _INTAKE_STORE_LOCK:
        intake_ctx = INTAKE_CONTEXT_STORE.get(session_id)
    intake_summary = _intake_summary_for_llm(
        session_id,
        intake_payload=intake_payload,
        intake_context=intake_ctx,
    )
    # When refining: inject explicit instruction so model applies change instead of asking
    user_msg = message
    if session.phase == "refining":
        user_msg = _UNIFIED_REFINE_GROWTH_USER_PREFIX + message
        logger.info("Refining: unified finance Q&A + portfolio prefix, phase=%s", session.phase)
    elif session.phase == "retirement_refining":
        user_msg = _UNIFIED_REFINE_RETIREMENT_USER_PREFIX + message
        logger.info("Retirement refining: unified finance Q&A + portfolio prefix, phase=%s", session.phase)

    _prev_json = ""
    if session.parsed_portfolios:
        try:
            _prev_json = json.dumps(session.parsed_portfolios, default=str)
        except (TypeError, ValueError):
            _prev_json = ""
    if not (_prev_json or "").strip():
        _prev_json = "(none yet — infer from conversation history if needed)"

    runtime_inputs = {
        "session_id": session_id,
        "conversation_history": conversation_context,
        "user_message": user_msg,
        "previous_portfolio": session.last_portfolio or "",
        "previous_portfolios_json": _prev_json,
        "user_feedback": user_msg,
        "chosen_portfolio": session.chosen_portfolio or "",
        "chosen_label": session.chosen_label or "moderate",
        "chosen_composition_json": chosen_composition_json,
        "intake_summary": intake_summary,
        "retirement_mc_facts": "FACTS: Not applicable (this task is not Emu retirement analysis).",
    }
    if inputs:
        runtime_inputs.update(inputs)
    logger.info("run_message: session_id=%s (passed to LLM prompt)", session_id[:12] if session_id else None)

    # ---- pick the right crew for the phase ----
    if session.phase == "analyst_running":
        if (
            session.chosen_retirement_composition
            or (
                _is_retirement_portfolio_session(session)
                and session.chosen_composition
            )
        ):
            if not session.chosen_retirement_composition and session.chosen_composition:
                session.chosen_retirement_composition = dict(session.chosen_composition)
            logger.info("Building Emu crew for retirement backtest (chosen_portfolio=%s)", bool(session.chosen_portfolio))
            task = _build_emu_task(agents["emu"])
            crew = Crew(
                agents=[agents["emu"]],
                tasks=[task],
                process=Process.sequential,
            )
        else:
            logger.info("Building Ana crew for growth backtest (chosen_portfolio=%s)", bool(session.chosen_portfolio))
            task = _build_analyst_task(agents["analyst"])
            crew = Crew(
                agents=[agents["analyst"], agents["money_manager"]],
                tasks=[task],
                process=Process.sequential,
            )
    elif session.phase == "post_analysis":
        if session.chosen_retirement_composition:
            task = _build_emu_post_analysis_task(agents["emu"])
            crew = Crew(
                agents=[agents["emu"]],
                tasks=[task],
                process=Process.sequential,
            )
        else:
            task = _build_post_analysis_task(agents["analyst"])
            crew = Crew(
                agents=[agents["analyst"], agents["money_manager"]],
                tasks=[task],
                process=Process.sequential,
            )
    elif _portfolio_builder_is_panda(session):
        if session.phase not in (
            "retirement_planning",
            "retirement_choosing",
            "retirement_refining",
        ):
            session.phase = "retirement_planning"
        panda_phase = (
            "retirement_refining" if session.phase == "retirement_refining" else "retirement_planning"
        )
        logger.info(
            "Crew: Panda (%s) portfolio_flow=%s phase=%s",
            panda_phase,
            session.portfolio_flow,
            session.phase,
        )
        task = _build_panda_task(agents["panda"], panda_phase)
        crew = Crew(
            agents=[agents["panda"]],
            tasks=[task],
            process=Process.sequential,
        )
    else:
        if session.phase in (
            "retirement_planning",
            "retirement_choosing",
            "retirement_refining",
        ):
            session.phase = "portfolio_building"
        logger.info(
            "Crew: Quala portfolio_flow=%s phase=%s",
            session.portfolio_flow,
            session.phase,
        )
        task = _build_quala_task(
            agents["money_manager"],
            session.phase,
            chosen_label=session.chosen_label or "moderate",
        )
        crew = Crew(
            agents=[agents["money_manager"]],
            tasks=[task],
            process=Process.sequential,
        )

    worker_tid: list = []

    # Run backtest BEFORE crew when portfolio is chosen, so user always gets results
    # even if Ana doesn't call run_backtest. Fetch missing ticker data first.
    if session.phase == "analyst_running" and session.chosen_composition and isinstance(
        session.chosen_composition, dict
    ):
        if (
            _is_retirement_portfolio_session(session)
            and not session.chosen_retirement_composition
        ):
            session.chosen_retirement_composition = dict(session.chosen_composition)
        try:
            tickers = list(session.chosen_composition.keys())
            # Check and fetch missing
            for t in tickers:
                load_ticker = resolve_load_ticker(t)
                if not monthly_csv_exists(DATA_OUTPUT_DIR, load_ticker):
                    _fetch_ticker_data(load_ticker)
            if session.chosen_retirement_composition:
                # Retirement portfolio: run retirement MC (Panda -> Analyst Emu)
                _run_retirement_backtest_and_store(
                    session_id,
                    session.chosen_composition,
                    intake_ctx,
                    getattr(session, "chosen_sectors", None),
                    getattr(session, "chosen_industries", None),
                )
            else:
                # Growth portfolio: run growth backtest
                if not monthly_csv_exists(DATA_OUTPUT_DIR, "SPY"):
                    _fetch_ticker_data("SPY")
                acc_pre: Dict[str, Any] = {"tickers": dict(session.chosen_composition)}
                if getattr(session, "chosen_sectors", None):
                    acc_pre["sectors"] = session.chosen_sectors
                if getattr(session, "chosen_industries", None):
                    acc_pre["industries"] = session.chosen_industries
                rb_input = json.dumps({"accumulation": acc_pre})
                _BACKTEST_SESSION_ID.session_id = session_id
                tool = RunBacktestTool()
                result = tool._run(rb_input)
                if "Missing price data" not in str(result):
                    logger.info("Pre-ran backtest from chosen_composition (before crew)")
        except Exception as e:
            logger.warning("Pre-run backtest failed: %s", e)
        finally:
            _BACKTEST_SESSION_ID.session_id = None

    if (
        session.phase == "analyst_running"
        and session.chosen_retirement_composition
    ):
        art = _peek_stored_backtest_artifacts(session_id)
        runtime_inputs["retirement_mc_facts"] = _format_retirement_mc_facts_for_emu(art)

    def _run_crew():
        worker_tid.append(str(threading.get_ident()))
        _BACKTEST_SESSION_ID.session_id = session_id
        try:
            return crew.kickoff(inputs=runtime_inputs)
        finally:
            _BACKTEST_SESSION_ID.session_id = None

    crew_timeout = int(os.getenv("CREW_TIMEOUT_SECONDS", "180"))
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(_run_crew)
    try:
        output = future.result(timeout=crew_timeout)
    except FuturesTimeoutError:
        logger.error("crew.kickoff() timed out after %ds", crew_timeout)
        pool.shutdown(wait=False, cancel_futures=True)
        output = (
            "I'm sorry, the request timed out due to API rate limits. "
            "Please wait a moment and try again."
        )
    except Exception as exc:
        err_msg = str(exc)
        if "No valid task outputs" in err_msg or "task output" in err_msg.lower():
            # CrewAI couldn't build crew output; gather last task raw output and show it in the UI
            fallback = None
            try:
                for t in getattr(crew, "tasks", []) or []:
                    out = getattr(t, "output", None)
                    if out is None:
                        continue
                    raw = getattr(out, "raw", None) or getattr(out, "raw_output", None)
                    if raw and str(raw).strip():
                        fallback = str(raw).strip()
                        break
            except Exception:
                pass
            if fallback:
                output = fallback
                logger.info("Using gathered LLM output after crew output error")
            else:
                output = (
                    "I had trouble formatting my reply. Please try again or rephrase. "
                    "If it keeps happening, try a shorter message or refresh the page."
                )
        else:
            output = f"Error: {exc}"
    else:
        pool.shutdown(wait=False)
        log_crewai_usage(crew, logger)
        _uid = (user_id or "").strip()
        if _uid:
            try:
                from backend.db import record_user_gemini_token_usage

                counts = extract_crew_usage_token_counts(crew)
                if counts:
                    pt, ct, tt = counts
                    record_user_gemini_token_usage(
                        _uid,
                        source=(token_usage_source or "crew_money_manager")[:120],
                        prompt_tokens=pt,
                        completion_tokens=ct,
                        total_tokens=tt,
                    )
            except Exception as exc:
                logger.debug("Persist crew token usage skipped: %s", exc)

    if not output or not str(output).strip():
        output = "Error: empty response. Check GEMINI_MODEL/API key."

    # ---- collect backtest artifacts if the tool produced any ----
    artifacts: dict = {}
    with _BACKTEST_STORE_LOCK:
        stored = _BACKTEST_ARTIFACT_STORE.pop(session_id, None)
        if not stored and worker_tid:
            stored = _BACKTEST_ARTIFACT_STORE.pop(worker_tid[0], None)
    if stored:
        artifacts = stored
        logger.info("Collected backtest artifacts for session (scenarios=%s)", bool(stored.get("scenarios")))

    # Analyst just finished backtest -> transition to post_analysis so UI shows Save/Refine options
    if session.phase == "analyst_running" and artifacts:
        session.phase = "post_analysis"

    output_str = str(output)

    # If Ana/Emu didn't produce artifacts, salvage by running backtest ourselves.
    # Retirement: use _run_retirement_backtest_and_store (never RunBacktestTool).
    # Growth: use RunBacktestTool.
    if session.phase == "analyst_running" and (not artifacts or not artifacts.get("scenarios")):
        if session.chosen_retirement_composition:
            # Retirement: run retirement MC (never growth RunBacktestTool)
            try:
                _run_retirement_backtest_and_store(
                    session_id,
                    session.chosen_composition,
                    intake_ctx,
                    getattr(session, "chosen_sectors", None),
                    getattr(session, "chosen_industries", None),
                )
                with _BACKTEST_STORE_LOCK:
                    stored = _BACKTEST_ARTIFACT_STORE.pop(session_id, None)
                if stored:
                    artifacts = stored
                    session.phase = "post_analysis"
                    output_str = _summary_from_artifacts(artifacts)
                    logger.info("Salvaged retirement backtest (scenarios=%s)", bool(stored.get("scenarios")))
            except Exception as e:
                logger.warning("Salvage retirement backtest failed: %s", e)
        else:
            # Growth: use RunBacktestTool
            rb_input = _extract_run_backtest_input(output_str)
            if rb_input:
                try:
                    _BACKTEST_SESSION_ID.session_id = session_id
                    tool = RunBacktestTool()
                    tool._run(rb_input)
                    with _BACKTEST_STORE_LOCK:
                        stored = _BACKTEST_ARTIFACT_STORE.pop(session_id, None)
                    if stored:
                        artifacts = stored
                        session.phase = "post_analysis"
                        output_str = _summary_from_artifacts(artifacts)
                        logger.info(
                            "Salvaged run_backtest from text-only Action Input (scenarios=%s)",
                            bool(stored.get("scenarios")),
                        )
                except Exception as e:
                    logger.warning("Salvage run_backtest failed: %s", e)
                finally:
                    _BACKTEST_SESSION_ID.session_id = None

            # Fallback: Ana never called run_backtest. Run directly from chosen_composition.
            if (not artifacts or not artifacts.get("scenarios")) and session.chosen_composition and isinstance(
                session.chosen_composition, dict
            ):
                try:
                    acc_fb: Dict[str, Any] = {"tickers": dict(session.chosen_composition)}
                    if getattr(session, "chosen_sectors", None):
                        acc_fb["sectors"] = session.chosen_sectors
                    if getattr(session, "chosen_industries", None):
                        acc_fb["industries"] = session.chosen_industries
                    rb_input = json.dumps({"accumulation": acc_fb})
                    _BACKTEST_SESSION_ID.session_id = session_id
                    tool = RunBacktestTool()
                    tool._run(rb_input)
                    with _BACKTEST_STORE_LOCK:
                        stored = _BACKTEST_ARTIFACT_STORE.pop(session_id, None)
                    if stored:
                        artifacts = stored
                        session.phase = "post_analysis"
                        output_str = _summary_from_artifacts(artifacts)
                        logger.info(
                            "Ran backtest directly from chosen_composition (Ana did not call run_backtest)"
                        )
                except Exception as e:
                    logger.warning("Direct run_backtest from chosen_composition failed: %s", e)
                finally:
                    _BACKTEST_SESSION_ID.session_id = None

    # Analyst handoff lines for the UI (Quala -> Ana, Panda -> Emu). Stored when the user picks a portfolio,
    # applied here so salvage logic above never overwrites ``artifacts`` after we attach these keys.
    panda_handoff: Optional[str] = None
    quala_handoff: Optional[str] = None
    pend = getattr(session, "pending_assistant_handoff", None)
    if pend:
        kind, handoff_text = pend
        session.pending_assistant_handoff = None
        if kind == "panda":
            panda_handoff = handoff_text
            artifacts["panda_handoff"] = handoff_text
        else:
            quala_handoff = handoff_text
            artifacts["quala_handoff"] = handoff_text

    # When we have backtest artifacts but the reply is raw tool output or incomplete ReAct,
    # show a short summary so the user gets summary + charts/tables.
    if artifacts and (artifacts.get("scenarios") or artifacts.get("portfolio_composition")):
        is_raw_tool_output = (
            "=== Backtest (No Rebalancing) ===" in output_str
            or "Backtest complete. Key results" in output_str
        )
        is_incomplete_react = (
            "Action:" in output_str and "Action Input:" in output_str and "Final Answer:" not in output_str
        )
        if is_raw_tool_output or is_incomplete_react:
            output_str = _summary_from_artifacts(artifacts)
            logger.info("Replaced raw/incomplete analyst output with artifact summary")

    # Tag which agent produced this response so history matches the actual crew/task.
    # Growth: Quala builds 3 portfolios; Ana (analyst task) runs backtest tools and writes the save/scenario summary.
    # Retirement: Panda builds; Emu runs backtest / post-analysis.
    is_retirement_analyst = bool(session.chosen_retirement_composition) or (
        (session.portfolio_flow or "").lower() == "retirement"
        and session.phase in ("analyst_running", "post_analysis")
    )
    responding_agent = (
        ("Emu" if is_retirement_analyst else "Ana")
        if session.phase in ("analyst_running", "post_analysis")
        else "Panda"
        if _portfolio_builder_is_panda(session)
        else "Quala"
    )
    if panda_handoff:
        responding_agent = "Emu"

    if responding_agent in ("Quala", "Panda"):
        parsed = _parse_portfolios_json(output_str)
        if parsed:
            _sanitize_estimate_placeholders(parsed)
            session.parsed_portfolios = parsed
            artifacts["all_portfolios"] = parsed
            date_range = os.getenv("LLM_ESTIMATES_DATE_RANGE")
            if date_range:
                artifacts["llm_estimates_date_range"] = date_range
            try:
                from backend.main import _get_session, enrich_all_portfolios_breakdown_tickers
                from backend.sector_industry_taxonomy import normalize_all_portfolios_proposals

                normalize_all_portfolios_proposals(parsed)
                enrich_all_portfolios_breakdown_tickers(
                    parsed,
                    _get_session(session_id),
                    style_quala_or_panda="panda" if responding_agent == "Panda" else "quala",
                )
            except Exception as exc:
                logger.warning("Proposal chart ticker rollups skipped: %s", exc)
            for name, p in parsed.items():
                tickers = (p.get("tickers") or {}) if isinstance(p, dict) else {}
                if not tickers or not any(w > 0 for w in (tickers.values() if isinstance(tickers, dict) else [])):
                    logger.warning("%s portfolio '%s' has no ticker weights: %s", responding_agent, name, tickers)
            logger.info("Parsed portfolio JSON from %s: %s", responding_agent, list(parsed.keys()))
            # So the next user message can be a choice (e.g. "go with aggressive"), transition to choosing
            choice_keys = {k.lower() for k in parsed if k.lower() in ("conservative", "moderate", "aggressive")}
            if len(choice_keys) >= 2:
                if _is_retirement_portfolio_session(session):
                    session.phase = "retirement_choosing"
                    logger.info(
                        "Panda returned %d retirement portfolio options -> phase set to retirement_choosing",
                        len(choice_keys),
                    )
                elif session.phase == "portfolio_building":
                    session.phase = "choosing"
                    logger.info(
                        "Quala returned %d portfolio options -> phase set to choosing",
                        len(choice_keys),
                    )


        output_str = _strip_portfolios_json(output_str)
        output_str = _deduplicate_repeated_block(output_str)
        session.last_portfolio = output_str

    # Use portfolio from backtest artifacts when available (e.g. after Ana refines); else use session
    if artifacts.get("portfolio_composition"):
        session.chosen_composition = artifacts["portfolio_composition"]
    elif session.chosen_composition:
        artifacts["portfolio_composition"] = session.chosen_composition
    chosen_sec = getattr(session, "chosen_sectors", None)
    chosen_ind = getattr(session, "chosen_industries", None)
    if chosen_sec or artifacts.get("portfolio_sectors"):
        artifacts["portfolio_sectors"] = _merge_sector_industry_maps(
            chosen_sec if isinstance(chosen_sec, dict) else None,
            artifacts.get("portfolio_sectors") if isinstance(artifacts.get("portfolio_sectors"), dict) else None,
        )
    if chosen_ind or artifacts.get("portfolio_industries"):
        artifacts["portfolio_industries"] = _merge_sector_industry_maps(
            chosen_ind if isinstance(chosen_ind, dict) else None,
            artifacts.get("portfolio_industries") if isinstance(artifacts.get("portfolio_industries"), dict) else None,
        )

    apply_taxonomy_to_artifact(artifacts)

    if artifacts.get("portfolio_composition") and (
        artifacts.get("portfolio_sectors") or artifacts.get("portfolio_industries")
    ):
        try:
            from backend.main import attach_portfolio_breakdown_tickers_to_artifacts

            attach_portfolio_breakdown_tickers_to_artifacts(
                artifacts,
                session_id,
                style_quala_or_panda="panda" if bool(session.chosen_retirement_composition) else "quala",
            )
        except Exception as exc:
            logger.warning("Chart ticker rollups (hover) skipped: %s", exc)

    # Avoid Quala 3-option strip + backtest block both rendering for one payload
    if artifacts.get("scenarios") and artifacts.get("all_portfolios"):
        del artifacts["all_portfolios"]

    # Thanks message is shown immediately by frontend when user chooses; backend returns only Ana's summary
    if getattr(session, "choice_ack_pending", False):
        session.choice_ack_pending = False

    output_str = _strip_agent_thought(output_str)

    if panda_handoff:
        session.history.append({"role": "assistant", "agent": "Panda", "content": panda_handoff})
    if quala_handoff:
        session.history.append({"role": "assistant", "agent": "Quala", "content": quala_handoff})
    session.history.append({
        "role": "assistant",
        "agent": responding_agent,
        "content": output_str,
    })

    return {"reply": output_str, "artifacts": artifacts, "agent": responding_agent}


def run_analyze_upload_agent_pipeline(
    session_id: str,
    ticker_weights: Dict[str, float],
    *,
    is_retirement: bool,
    sector_weights: Dict[str, float],
    industry_weights: Dict[str, float],
    intake_payload: Optional[Dict[str, object]] = None,
    user_id: Optional[str] = None,
) -> Dict[str, object]:
    """CSV analyze flow: seed session as if Quala/Panda proposed one 'moderate' portfolio with asset class / sector maps,
    then run the normal user choice → Ana (growth) or Emu (retirement) backtest + explanation."""
    session = SESSION_STORE.get(session_id)
    if session is None:
        raise ValueError(f"Unknown session_id: {session_id}")

    tw = {str(k).strip().upper(): float(v) for k, v in (ticker_weights or {}).items() if str(k).strip()}
    tw = {k: v for k, v in tw.items() if v > 0}
    if not tw:
        raise ValueError("ticker_weights is empty")

    sec = _normalize_sector_industry_weights(sector_weights)
    ind = _normalize_sector_industry_weights(industry_weights)
    sec = normalize_asset_class_weights(sec) if sec else {}
    ind = normalize_gics_industry_weights(ind) if ind else {}

    session.parsed_portfolios = {
        "moderate": {
            "tickers": dict(tw),
            "sectors": dict(sec) if sec else {},
            "industries": dict(ind) if ind else {},
        }
    }
    quala_or_panda = "Panda" if is_retirement else "Quala"
    analyst = "Emu" if is_retirement else "Ana"
    session.history.append(
        {
            "role": "assistant",
            "content": (
                f"{quala_or_panda} classified your uploaded holdings into asset class and sector weights "
                f"(for chart breakdowns). Say **go with the moderate portfolio** to proceed, or pick moderate "
                f"when prompted—{analyst} will run the backtest and summarize results."
            ),
            "agent": quala_or_panda,
        }
    )
    session.phase = "retirement_choosing" if is_retirement else "choosing"
    session.pending_assistant_handoff = None
    session.choice_ack_pending = False

    return run_message(
        session_id,
        "Go with the moderate portfolio",
        intake_payload=intake_payload,
        user_id=user_id,
        token_usage_source="crew_analyze_portfolio",
    )


# ------------------------------------------------------------------ #
#  Backward-compatible helpers                                        #
# ------------------------------------------------------------------ #

def build_tasks(agents: Dict[str, Agent]) -> list[Task]:
    """Build default task list (used by standalone ``build_crew``)."""
    return [_build_quala_task(agents["money_manager"], "portfolio_building")]


def build_crew() -> Crew:
    llm_mm = build_llm("money_manager")
    llm_analyst = build_llm("analyst")
    agents = build_agents(llm_mm, llm_analyst)
    tasks = build_tasks(agents)
    return Crew(
        agents=list(agents.values()),
        tasks=tasks,
        process=Process.sequential,
    )


def run(inputs: Optional[Dict[str, str]] = None) -> str:
    crew = build_crew()
    out = crew.kickoff(inputs=inputs or {})
    log_crewai_usage(crew, logger)
    return out


if __name__ == "__main__":
    output = run()
    print(output)
