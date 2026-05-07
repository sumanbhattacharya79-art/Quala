from __future__ import annotations

import io
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import pandas as pd

from google import genai  # noqa: E402

from backend.crewai_app.gemini_token_usage import gemini_model_id_for_google_genai_api  # noqa: E402
from backtesting.driver import run_backtests  # noqa: E402
from backend.prompts import (  # noqa: E402
    ASSUMPTIONS_POLICY,
    INTAKE_CLARIFYING_QUESTIONS,
    METRICS_REQUEST_GUIDANCE,
    RISK_ASSESSMENT_GUIDANCE,
)

INTENTS = ("intake", "upload", "backtest", "rebalance", "general", "clarify")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_OUTPUT_DIR = PROJECT_ROOT / "data_output"
LLM_METADATA_LOG = Path(
    os.getenv(
        "LLM_METADATA_LOG",
        str(PROJECT_ROOT / "output_logs" / "llm_metadata.jsonl"),
    )
)


def _safe_json_extract(text: str) -> Optional[Dict[str, float]]:
    if not text:
        return None
    json_block = _extract_json_object(text)
    if not json_block:
        return None
    try:
        payload = json.loads(json_block)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    cleaned: Dict[str, float] = {}
    for key, value in payload.items():
        if key is None:
            continue
        ticker = str(key).strip().upper()
        if not ticker:
            continue
        numeric_value = _coerce_weight_value(value)
        if numeric_value is None:
            continue
        cleaned[ticker] = numeric_value
    return cleaned or None


def _coerce_weight_value(value: object) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("%"):
            text = text[:-1].strip()
            try:
                return float(text) / 100.0
            except ValueError:
                return None
        try:
            return float(text)
        except ValueError:
            return None
    if isinstance(value, dict):
        for key in ("weight", "value", "allocation", "percent", "pct"):
            if key in value:
                return _coerce_weight_value(value[key])
    return None


def _extract_json_object(text: str) -> Optional[str]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start : end + 1]


def _safe_csv_extract(text: str) -> Optional[Dict[str, float]]:
    if not text:
        return None
    if "," not in text or "\n" not in text:
        return None
    try:
        df = pd.read_csv(io.StringIO(text))
    except Exception:
        return None
    lower_cols = {c.lower(): c for c in df.columns}
    ticker_col = None
    for name in ("ticker", "symbol", "asset"):
        if name in lower_cols:
            ticker_col = lower_cols[name]
            break
    if ticker_col is None:
        return None
    weight_col = None
    for name in ("weight", "allocation", "percent", "pct"):
        if name in lower_cols:
            weight_col = lower_cols[name]
            break
    if weight_col is None:
        return None
    holdings: Dict[str, float] = {}
    for _, row in df.iterrows():
        ticker = str(row[ticker_col]).strip().upper()
        if not ticker:
            continue
        holdings[ticker] = float(row[weight_col])
    return holdings or None


def _normalize_holdings(holdings: Dict[str, float]) -> Dict[str, float]:
    cleaned = {k: float(v) for k, v in holdings.items() if k}
    total = sum(cleaned.values())
    if total <= 0:
        raise ValueError("Holdings must sum to a positive value.")
    return {k: v / total for k, v in cleaned.items()}


def _extract_first_number(text: str) -> Optional[float]:
    tokens = text.replace(",", " ").split()
    for token in tokens:
        cleaned = "".join(ch for ch in token if ch.isdigit() or ch == ".")
        if not cleaned or cleaned == ".":
            continue
        try:
            return float(cleaned)
        except ValueError:
            continue
    return None


def _extract_investment_value(text: str) -> Optional[float]:
    tokens = text.replace(",", " ").split()
    for token in tokens:
        lowered = token.lower().strip().strip("$")
        suffix = None
        if lowered.endswith(("m", "k")) and len(lowered) > 1:
            suffix = lowered[-1]
            lowered = lowered[:-1]
        cleaned = "".join(ch for ch in lowered if ch.isdigit() or ch == ".")
        if not cleaned or cleaned == ".":
            continue
        try:
            value = float(cleaned)
        except ValueError:
            continue
        if suffix == "m":
            value *= 1_000_000
        elif suffix == "k":
            value *= 1_000
        return value
    return None


def _update_intake_answers(session: Dict[str, Any], message: str) -> Dict[str, Any]:
    answers = session.get("intake_answers", {})
    freeform = session.get("intake_freeform", [])
    freeform.append(message.strip())
    session["intake_freeform"] = freeform[-5:]
    text = message.lower()
    if any(k in text for k in ("low", "conservative")):
        answers["risk"] = "low"
    if any(k in text for k in ("medium", "moderate", "balanced")):
        answers["risk"] = "medium"
    if any(k in text for k in ("high", "aggressive")):
        answers["risk"] = "high"
    years = _extract_first_number(text)
    if years is not None and "year" in text:
        answers["horizon_years"] = int(years)
    if any(k in text for k in ("retirement", "retire")):
        answers["goal"] = "retirement"
    if "house" in text or "home" in text:
        answers["goal"] = "house"
    if "income" in text:
        answers["goal"] = "income"
    if "education" in text or "college" in text:
        answers["goal"] = "education"
    value = _extract_investment_value(text)
    if value is not None:
        answers["investment_value"] = value
        session["investment_value"] = answers["investment_value"]
    for cur in ("usd", "eur", "inr", "gbp", "cad", "aud"):
        if cur in text:
            answers["currency"] = cur.upper()
    if "esg" in text or "sustainab" in text:
        answers["constraints"] = "esg"
    if "no leverage" in text or "no-leverage" in text:
        answers["constraints"] = "no-leverage"
    session["intake_answers"] = answers
    return answers


def _intake_assumptions(session: Dict[str, Any]) -> Dict[str, Any]:
    answers = session.get("intake_answers", {})
    assumptions = {}
    if "risk" not in answers:
        assumptions["risk"] = "medium"
    if "horizon_years" not in answers:
        assumptions["horizon_years"] = 10
    if "goal" not in answers:
        assumptions["goal"] = "retirement"
    if "currency" not in answers:
        assumptions["currency"] = "USD"
    if "constraints" not in answers:
        assumptions["constraints"] = "none"
    if "investment_value" not in answers:
        assumptions["investment_value"] = 1000
    return assumptions


def _assumptions_text(assumptions: Dict[str, Any]) -> str:
    if not assumptions:
        return "None."
    return "; ".join(f"{key}={value}" for key, value in assumptions.items())


def _request_portfolio_allocation(
    llm: Optional[LLMClient],
    context: Dict[str, Any],
    answers: Dict[str, Any],
    assumptions: Dict[str, Any],
    freeform: Optional[list[str]] = None,
) -> Optional[Dict[str, float]]:
    if llm is None:
        return None
    freeform = freeform or []
    freeform_text = "\n".join(freeform[-5:])
    prompt = (
        "Create an investment portfolio allocation based on the provided context.\n"
        "Return JSON only with tickers as keys and weights as decimals that sum to 1.\n"
        "Do not ask questions. Do not include explanations.\n"
        "Clarifying questions asked:\n"
        f"{INTAKE_CLARIFYING_QUESTIONS}\n"
        f"Provided answers: {json.dumps(answers)}\n"
        f"User responses (freeform): {freeform_text}\n"
        f"Assumptions: {_assumptions_text(assumptions)}\n"
        "JSON:"
    )
    context["llm_reply_attempted"] = True
    try:
        raw = llm.complete(prompt)
    except Exception:
        return None
    return _safe_json_extract(raw or "")


def _parse_backtest_params(message: str) -> Tuple[str, Optional[float]]:
    text = message.lower()
    if "adaptive" in text:
        return "adaptive_5_25", None
    if "none" in text:
        return "none", None
    return "monthly", None


def _is_metrics_request(message: str) -> bool:
    text = message.lower()
    return any(
        key in text
        for key in (
            "beta",
            "sharpe",
            "sortino",
            "volatility",
            "drawdown",
            "cagr",
            "metrics",
            "correlation",
            "tracking error",
        )
    )


def _parse_intent_choice(message: str) -> Optional[str]:
    text = message.strip().lower()
    for intent in ("intake", "upload", "backtest", "rebalance", "general"):
        if text == intent:
            return intent
    for intent in ("intake", "upload", "backtest", "rebalance", "general"):
        if intent in text:
            return intent
    return None


def _google_genai_api_key() -> Optional[str]:
    env_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if env_key:
        return env_key
    key_path = REPO_ROOT / "gemini_apikey.txt"
    if key_path.exists():
        txt = key_path.read_text(encoding="utf-8").strip()
        return txt or None
    return None


class LLMClient:
    def __init__(self) -> None:
        api_key = _google_genai_api_key()
        self.client = None
        self.last_error: Optional[str] = None
        self.last_raw: Optional[str] = None
        self.raw_history: list[str] = []
        self.last_prompt: Optional[str] = None
        self.prompt_history: list[str] = []
        if api_key:
            self.client = genai.Client(api_key=api_key)
        else:
            self.last_error = "GEMINI_API_KEY / GOOGLE_API_KEY not set and gemini_apikey.txt missing"
        # google.genai expects bare id; env may be LiteLLM-style gemini/gemini-…
        raw_model = os.getenv("GEMINI_MODEL")
        self.model = gemini_model_id_for_google_genai_api(raw_model) if raw_model else gemini_model_id_for_google_genai_api()
        self.backend = "gemini"
        self.used = False

    def complete(self, prompt: str) -> str:
        if self.client is None:
            raise RuntimeError(self.last_error or "Gemini client not initialized")
        self.last_prompt = prompt
        self.prompt_history.append(prompt)
        if len(self.prompt_history) > 10:
            self.prompt_history = self.prompt_history[-10:]
        self.used = True
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
        )
        self.last_raw = response.text or ""
        self.raw_history.append(self.last_raw)
        if len(self.raw_history) > 10:
            self.raw_history = self.raw_history[-10:]
        return self.last_raw

class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, Callable[..., Any]] = {}

    def register(self, name: str, func: Callable[..., Any]) -> None:
        self._tools[name] = func

    def call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        if name not in self._tools:
            raise ValueError(f"Unknown tool: {name}")
        return self._tools[name](*args, **kwargs)


def build_context_pack(session: Dict[str, Any]) -> str:
    portfolio = session.get("portfolio") or {}
    pending = session.get("pending_intent")
    last_intent = session.get("last_intent")
    primary_intent = session.get("primary_intent")
    intake_answers = session.get("intake_answers") or {}
    intake_freeform = session.get("intake_freeform") or []
    investment_value = session.get("investment_value")
    history = session.get("history") or []
    recent = history[-6:]
    history_lines = []
    for item in recent:
        role = item.get("role", "user")
        content = item.get("content", "")
        if not content:
            continue
        history_lines.append(f"{role}: {content}")
    history_text = "\n".join(history_lines) if history_lines else "none"
    return (
        "Context pack:\n"
        f"- portfolio: {json.dumps(portfolio)}\n"
        f"- primary_intent: {primary_intent}\n"
        f"- pending_intent: {pending}\n"
        f"- last_intent: {last_intent}\n"
        f"- investment_value: {investment_value}\n"
        f"- intake_answers: {json.dumps(intake_answers)}\n"
        f"- intake_freeform: {json.dumps(intake_freeform)}\n"
        f"- recent_messages:\n{history_text}\n"
    )


def maybe_llm_reply(
    llm: Optional[LLMClient],
    context: Dict[str, Any],
    prompt: str,
    fallback: str,
) -> str:
    if llm is None:
        return fallback
    context["llm_reply_attempted"] = True
    try:
        context_pack = build_context_pack(context.get("session", {}))
        reply = llm.complete(f"{context_pack}\n{prompt}").strip()
        context["llm_used_for_reply"] = True
        return reply or fallback
    except Exception:
        return fallback


@dataclass
class AgentResult:
    intent: str
    actions: list[dict]
    artifacts: dict
    reply: str


@dataclass
class RouteDecision:
    intent: str
    confidence: float
    source: str
    candidate_intent: Optional[str] = None


class BaseAgent:
    intent: str = "general"

    def run(
        self,
        context: Dict[str, Any],
        message: str,
        tools: ToolRegistry,
        llm: Optional[LLMClient],
    ) -> AgentResult:
        return AgentResult(
            intent=self.intent,
            actions=[],
            artifacts={},
            reply="I can help with portfolios, backtests, and rebalancing.",
        )


class IntakeAgent(BaseAgent):
    intent = "intake"

    def run(
        self,
        context: Dict[str, Any],
        message: str,
        tools: ToolRegistry,
        llm: Optional[LLMClient],
    ) -> AgentResult:
        actions: list[dict] = []
        holdings = tools.call("parse_holdings", message)
        if holdings:
            portfolio = tools.call("save_portfolio", holdings)
            actions.append({"type": "save_portfolio", "holdings": portfolio})
            reply = f"Saved portfolio with {len(portfolio)} assets. You can run a backtest now."
        else:
            _update_intake_answers(context["session"], message)
            assumptions = _intake_assumptions(context["session"])
            answers = context["session"].get("intake_answers", {})
            freeform = context["session"].get("intake_freeform", [])
            proposed = context["session"].get("proposed_portfolio")
            if "accept" in message.lower() and proposed:
                portfolio = tools.call("save_portfolio", proposed)
                actions.append({"type": "save_portfolio", "holdings": portfolio})
                context["session"].pop("proposed_portfolio", None)
                reply = "Got it. I saved the proposed portfolio. You can run a backtest now."
                return AgentResult(
                    intent=self.intent,
                    actions=actions,
                    artifacts={"portfolio": portfolio},
                    reply=reply,
                )
            reply = "Share target weights as JSON (e.g., {\"AAPL\": 0.5, \"MSFT\": 0.5})."
            if answers or freeform:
                proposed_holdings = _request_portfolio_allocation(
                    llm, context, answers, assumptions, freeform
                )
                if proposed_holdings:
                    context["session"]["proposed_portfolio"] = proposed_holdings
                    actions.append({"type": "proposed_portfolio", "holdings": proposed_holdings})
                    reply = (
                        "Here is a proposed allocation. Reply 'accept' to save it, "
                        "or tell me what to change.\n"
                        f"{json.dumps(proposed_holdings)}"
                    )
                else:
                    reply = maybe_llm_reply(
                        llm,
                        context,
                        (
                            f"{INTAKE_CLARIFYING_QUESTIONS}\n"
                            f"Provided answers so far: {json.dumps(answers)}\n"
                            f"User responses so far: {json.dumps(freeform)}\n"
                            f"{ASSUMPTIONS_POLICY}\n"
                            f"Assumptions: {_assumptions_text(assumptions)}\n"
                            f"User message: {message}\n"
                            f"Fallback reply: {reply}\n"
                            "Assistant:"
                        ),
                        reply,
                    )
            else:
                reply = maybe_llm_reply(
                    llm,
                    context,
                    (
                        f"{INTAKE_CLARIFYING_QUESTIONS}\n"
                        f"{ASSUMPTIONS_POLICY}\n"
                        f"Assumptions: {_assumptions_text(assumptions)}\n"
                        f"User message: {message}\n"
                        f"Fallback reply: {reply}\n"
                        "Assistant:"
                    ),
                    reply,
                )
            context["session"]["pending_intent"] = "intake"
            actions.append({"type": "needs_holdings"})
        return AgentResult(
            intent=self.intent,
            actions=actions,
            artifacts={
                "portfolio": holdings or context["session"].get("proposed_portfolio", {}),
                "proposed_portfolio": context["session"].get("proposed_portfolio"),
            },
            reply=reply,
        )


class UploadAgent(BaseAgent):
    intent = "upload"

    def run(
        self,
        context: Dict[str, Any],
        message: str,
        tools: ToolRegistry,
        llm: Optional[LLMClient],
    ) -> AgentResult:
        return AgentResult(
            intent=self.intent,
            actions=[{"type": "request_upload"}],
            artifacts={},
            reply="Upload a CSV with ticker and weight columns.",
        )


class BacktestAgent(BaseAgent):
    intent = "backtest"

    def run(
        self,
        context: Dict[str, Any],
        message: str,
        tools: ToolRegistry,
        llm: Optional[LLMClient],
    ) -> AgentResult:
        session = context.get("session", {})
        holdings = tools.call("parse_holdings", message)
        if holdings:
            portfolio = tools.call("save_portfolio", holdings)
            context["session"]["portfolio"] = portfolio
            context["session"]["pending_intent"] = None
        if "portfolio" not in session:
            reply = "I need a portfolio first. Share target weights or paste a CSV."
            assumptions = {"benchmark": "VOO", "rebalancing": "monthly", "tx_cost_bps": 5}
            prompt = METRICS_REQUEST_GUIDANCE if _is_metrics_request(message) else RISK_ASSESSMENT_GUIDANCE
            reply = maybe_llm_reply(
                llm,
                context,
                (
                    f"{prompt}\n"
                    f"{ASSUMPTIONS_POLICY}\n"
                    f"Assumptions: {_assumptions_text(assumptions)}\n"
                    f"User message: {message}\n"
                    f"Fallback reply: {reply}\n"
                    "Assistant:"
                ),
                reply,
            )
            context["session"]["pending_intent"] = "backtest"
            return AgentResult(intent=self.intent, actions=[], artifacts={}, reply=reply)

        rule, threshold = _parse_backtest_params(message)
        try:
            backtest_result = tools.call("run_backtest", rule, threshold)
        except Exception as exc:
            return AgentResult(
                intent=self.intent,
                actions=[],
                artifacts={},
                reply=f"Backtest failed: {exc}",
            )

        reply = "Backtest + Monte Carlo completed. Here are the results."
        assumptions = {"benchmark": "VOO", "rebalancing": rule, "tx_cost_bps": 5}
        prompt = METRICS_REQUEST_GUIDANCE if _is_metrics_request(message) else RISK_ASSESSMENT_GUIDANCE
        reply = maybe_llm_reply(
            llm,
            context,
            (
                f"{prompt}\n"
                f"{ASSUMPTIONS_POLICY}\n"
                f"Assumptions: {_assumptions_text(assumptions)}\n"
                f"User message: {message}\n"
                f"Fallback reply: {reply}\n"
                "Assistant:"
            ),
            reply,
        )
        return AgentResult(
            intent=self.intent,
            actions=[{"type": "metrics" if _is_metrics_request(message) else "backtest"}],
            artifacts=backtest_result,
            reply=reply,
        )


class RebalanceAgent(BaseAgent):
    intent = "rebalance"

    def run(
        self,
        context: Dict[str, Any],
        message: str,
        tools: ToolRegistry,
        llm: Optional[LLMClient],
    ) -> AgentResult:
        actions: list[dict] = []
        session = context.get("session", {})
        holdings = tools.call("parse_holdings", message)
        if not holdings:
            reply = "Send current holdings JSON to suggest trades."
            assumptions = {"target_portfolio": "session portfolio"}
            reply = maybe_llm_reply(
                llm,
                context,
                (
                    "You are a helpful portfolio assistant. Ask for current holdings in JSON.\n"
                    f"{ASSUMPTIONS_POLICY}\n"
                    f"Assumptions: {_assumptions_text(assumptions)}\n"
                    f"User message: {message}\n"
                    f"Fallback reply: {reply}\n"
                    "Assistant:"
                ),
                reply,
            )
            context["session"]["pending_intent"] = "rebalance"
        else:
            if "portfolio" not in session:
                reply = "I need a target portfolio first. Share target weights or upload a CSV."
            else:
                trades = tools.call("suggest_rebalance", holdings)
                actions.append({"type": "rebalance", "trades": trades})
                if trades:
                    trade_lines = "; ".join(
                        f"{t['action'].upper()} {t['asset']} ({t['delta_weight']:.4f})"
                        for t in trades
                    )
                    reply = f"Suggested trades: {trade_lines}"
                else:
                    reply = "No trades needed. Portfolio is on target."
        reply = maybe_llm_reply(
            llm,
            context,
            (
                "You are a helpful portfolio assistant. Summarize the rebalance outcome.\n"
                "If trades exist, mention it's a trade suggestion. If none, say on target.\n"
                f"{ASSUMPTIONS_POLICY}\n"
                f"User message: {message}\n"
                f"Fallback reply: {reply}\n"
                "Assistant:"
            ),
            reply,
        )
        return AgentResult(
            intent=self.intent,
            actions=actions,
            artifacts={"trades": actions[0]["trades"] if actions else []},
            reply=reply,
        )


class GeneralAgent(BaseAgent):
    intent = "general"

    def run(
        self,
        context: Dict[str, Any],
        message: str,
        tools: ToolRegistry,
        llm: Optional[LLMClient],
    ) -> AgentResult:
        reply = "I can create portfolios, import CSVs, run backtests, and rebalance."
        context["llm_used_for_reply"] = False
        if llm is not None:
            prompt = (
                "You are a helpful portfolio assistant. Reply in 1-2 sentences.\n"
                f"User: {message}\n"
                "Assistant:"
            )
            try:
                reply = llm.complete(prompt).strip() or reply
                context["llm_used_for_reply"] = True
            except Exception:
                pass
        return AgentResult(intent=self.intent, actions=[], artifacts={}, reply=reply)


class ClarificationAgent(BaseAgent):
    intent = "clarify"

    def run(
        self,
        context: Dict[str, Any],
        message: str,
        tools: ToolRegistry,
        llm: Optional[LLMClient],
    ) -> AgentResult:
        candidate = context.get("candidate_intent") or "general"
        options = ["intake", "upload", "backtest", "rebalance", "general"]
        reply = (
            f"I think you're asking about **{candidate}**. "
            "Is that correct? If not, tell me which one: "
            f"{', '.join(options)}."
        )
        return AgentResult(
            intent=self.intent,
            actions=[{"type": "clarify_intent", "candidate": candidate, "options": options}],
            artifacts={"candidate_intent": candidate},
            reply=reply,
        )


class Router:
    def __init__(self, llm: Optional[LLMClient]) -> None:
        self.llm = llm

    def route(self, message: str, session: Dict[str, Any]) -> RouteDecision:
        pending = session.get("pending_intent")
        if pending == "clarify":
            clarified = _parse_intent_choice(message)
            if clarified:
                return RouteDecision(intent=clarified, confidence=1.0, source="clarify_reply")
        if pending in INTENTS:
            return RouteDecision(intent=pending, confidence=1.0, source="pending")
        primary_intent = session.get("primary_intent")
        if primary_intent == "intake" and not session.get("portfolio"):
            return RouteDecision(intent="intake", confidence=1.0, source="primary_intent")
        if self.llm is None:
            return RouteDecision(intent="clarify", confidence=0.0, source="llm_missing")
        context_pack = build_context_pack(session)
        prompt = (
            "Classify the user intent into one of: intake, upload, backtest, rebalance, general.\n"
            "Return JSON only: {\"intent\": \"...\", \"confidence\": 0-1}.\n"
            "Use these examples:\n"
            "- \"help me create a portfolio\" -> intake\n"
            "- \"build an investment portfolio\" -> intake\n"
            "- \"upload my portfolio\" -> upload\n"
            "- \"upload an existing portfolio and run backtest and montecarlo\" -> backtest\n"
            "- \"run backtest on a portfolio\" -> backtest\n"
            "- \"assess risk\" -> backtest\n"
            "- \"rebalance my holdings\" -> rebalance\n"
            "- \"what is beta\" -> backtest\n"
            "If the message doesn't match, use general.\n"
            f"{context_pack}\n"
            f"Message: {message}\n"
        )
        try:
            raw = self.llm.complete(prompt)
        except Exception as exc:
            session["last_router_error"] = str(exc)
            session["last_router_raw"] = None
            return RouteDecision(intent="clarify", confidence=0.0, source="llm_error")
        session["last_router_raw"] = raw
        json_block = _extract_json_object(raw or "")
        if not json_block:
            return RouteDecision(intent="clarify", confidence=0.0, source="llm_parse_error")
        try:
            data = json.loads(json_block)
        except json.JSONDecodeError:
            return RouteDecision(intent="clarify", confidence=0.0, source="llm_parse_error")
        intent = data.get("intent", "general")
        if intent not in INTENTS:
            return RouteDecision(intent="clarify", confidence=0.0, source="llm_invalid_intent")
        confidence = float(data.get("confidence", 0))
        if confidence < 0.5:
            return RouteDecision(
                intent="clarify",
                confidence=confidence,
                source="llm_low_confidence",
                candidate_intent=intent,
            )
        return RouteDecision(intent=intent, confidence=confidence, source="llm")




def build_tools(session: Dict[str, Any]) -> ToolRegistry:
    tools = ToolRegistry()

    def parse_holdings(message: str) -> Optional[Dict[str, float]]:
        holdings = _safe_json_extract(message)
        if holdings:
            return _normalize_holdings(holdings)
        holdings = _safe_csv_extract(message)
        if holdings:
            return _normalize_holdings(holdings)
        return None

    def save_portfolio(holdings: Dict[str, float]) -> Dict[str, float]:
        session["portfolio"] = holdings
        return holdings

    def suggest_rebalance(current: Dict[str, float]) -> list[dict]:
        target = session.get("portfolio", {})
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
        return trades

    def run_backtest(rule_type: str, threshold: Optional[float]) -> Dict[str, Any]:
        portfolio = session.get("portfolio")
        if not portfolio:
            raise ValueError("No portfolio found for session.")

        results = run_backtests(
            portfolio=portfolio,
            data_output_dir=DATA_OUTPUT_DIR,
        )

        scenarios = results["scenarios"]
        primary = next((s for s in scenarios if s["scenario"] == "monthly"), None)
        if primary is None and scenarios:
            primary = scenarios[0]

        return {
            "portfolio": portfolio,
            "scenarios": scenarios,
            "metrics": primary["metrics"] if primary else {},
            "monte_carlo": primary["monte_carlo"] if primary else {},
            "timeseries": primary["timeseries"] if primary else [],
            "summary_paths": primary["summary_paths"] if primary else {},
            "summary_metadata": primary["summary_metadata"] if primary else {},
        }

    tools.register("parse_holdings", parse_holdings)
    tools.register("save_portfolio", save_portfolio)
    tools.register("suggest_rebalance", suggest_rebalance)
    tools.register("run_backtest", run_backtest)
    return tools


def get_agent(intent: str) -> BaseAgent:
    if intent == "intake":
        return IntakeAgent()
    if intent == "upload":
        return UploadAgent()
    if intent == "backtest":
        return BacktestAgent()
    if intent == "rebalance":
        return RebalanceAgent()
    if intent == "clarify":
        return ClarificationAgent()
    return GeneralAgent()


def _should_clear_pending(result: AgentResult) -> bool:
    action_types = {action.get("type") for action in result.actions}
    if result.intent == "intake":
        return "save_portfolio" in action_types
    if result.intent == "rebalance":
        return "rebalance" in action_types
    if result.intent == "backtest":
        return "backtest" in action_types or "metrics" in action_types
    return False


def run_agentic_chat(session: Dict[str, Any], message: str) -> AgentResult:
    llm = LLMClient()
    router = Router(llm)
    decision = router.route(message, session)
    tools = build_tools(session)
    agent = get_agent(decision.intent)
    context = {
        "session": session,
        "llm_used_for_reply": False,
        "candidate_intent": decision.candidate_intent,
    }
    result = agent.run(context, message, tools, llm)
    if llm.last_raw:
        result.reply = f"{result.reply}\n\n[LLM_RAW]\n{llm.last_raw}"
    session["last_intent"] = result.intent
    session["last_agent_ts"] = time.time()
    if result.intent in {"intake", "backtest", "rebalance", "upload"}:
        session["primary_intent"] = result.intent
    if result.intent == "clarify":
        session["pending_intent"] = "clarify"
    history = session.setdefault("history", [])
    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": result.reply})
    if len(history) > 20:
        del history[:-20]
    if session.get("pending_intent") == result.intent and _should_clear_pending(result):
        session.pop("pending_intent", None)
    result.artifacts.setdefault("meta", {})
    result.artifacts["meta"].update(
        {
            "llm_backend": llm.backend,
            "llm_model": llm.model,
            "llm_used": llm.used,
            "llm_used_for_reply": bool(context.get("llm_used_for_reply")),
            "llm_reply_attempted": bool(context.get("llm_reply_attempted")),
            "llm_error": llm.last_error,
            "llm_prompt_last": llm.last_prompt,
            "llm_prompt_history": llm.prompt_history,
            "llm_raw_last": llm.last_raw,
            "llm_raw_history": llm.raw_history,
            "intent_source": decision.source,
            "intent_confidence": decision.confidence,
            "intent_candidate": decision.candidate_intent,
            "router_raw": session.get("last_router_raw"),
            "router_error": session.get("last_router_error"),
        }
    )
    _append_metadata_log(session, result)
    return result


def _append_metadata_log(session: Dict[str, Any], result: AgentResult) -> None:
    meta = result.artifacts.get("meta", {})
    record = {
        "ts": time.time(),
        "session_id": session.get("session_id"),
        "intent": result.intent,
        "meta": meta,
    }
    try:
        LLM_METADATA_LOG.parent.mkdir(parents=True, exist_ok=True)
        with LLM_METADATA_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    except Exception:
        # Avoid breaking chat flow if logging fails.
        pass

