from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from google import genai

from backend.crewai_app.gemini_token_usage import gemini_model_id_for_google_genai_api, log_generate_content_usage
from backend.prompts import ASSUMPTIONS_POLICY, INTAKE_CLARIFYING_QUESTIONS

_log = logging.getLogger(__name__)


@dataclass
class IntakeResult:
    reply: str
    proposed_portfolio: Optional[Dict[str, float]] = None
    accepted: bool = False
    raw_response: Optional[str] = None
    prompt: Optional[str] = None


def _extract_json_object(text: str) -> Optional[str]:
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start : end + 1]


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


def _safe_json_extract(text: str) -> Optional[Dict[str, float]]:
    json_block = _extract_json_object(text or "")
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


def _build_prompt(session: Dict[str, Any], message: str) -> str:
    history = session.get("intake_history", [])
    history_text = "\n".join(
        f"{item['role']}: {item['content']}" for item in history[-10:]
    )
    freeform = session.get("intake_freeform", [])
    freeform_text = "\n".join(freeform[-10:])
    return (
        "You are an expert portfolio manager.\n"
        "Use the provided user context to propose a portfolio.\n"
        "If you need more info, ask concise follow-up questions.\n"
        "If you can propose now, return JSON only with tickers as keys and weights "
        "as decimals that sum to 1.\n"
        "Add explanations when returning JSON how this choice"
        "help the user achieve their goals.\n"
        "Clarifying guidance:\n"
        f"{INTAKE_CLARIFYING_QUESTIONS}\n"
        f"{ASSUMPTIONS_POLICY}\n"
        f"Conversation so far:\n{history_text}\n"
        f"User responses so far:\n{freeform_text}\n"
        f"Latest user message: {message}\n"
        "Assistant:"
    )


def handle_intake_message(
    session: Dict[str, Any],
    message: str,
    model: Optional[str] = None,
) -> IntakeResult:
    session.setdefault("intake_history", [])
    session.setdefault("intake_freeform", [])

    session["intake_history"].append({"role": "user", "content": message})
    session["intake_freeform"].append(message.strip())
    if len(session["intake_freeform"]) > 20:
        session["intake_freeform"] = session["intake_freeform"][-20:]

    proposed = session.get("proposed_portfolio")
    if "accept" in message.lower() and proposed:
        reply = "Got it. I saved the proposed portfolio. You can run a backtest now."
        session["intake_history"].append({"role": "assistant", "content": reply})
        return IntakeResult(reply=reply, proposed_portfolio=proposed, accepted=True)

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        reply = "Gemini API key is missing. Please set GEMINI_API_KEY."
        session["intake_history"].append({"role": "assistant", "content": reply})
        return IntakeResult(reply=reply)

    client = genai.Client(api_key=api_key)
    prompt = _build_prompt(session, message)
    effective_model = model or gemini_model_id_for_google_genai_api()
    try:
        response = client.models.generate_content(
            model=effective_model,
            contents=prompt,
        )
        log_generate_content_usage(_log, response, label="intake_portfolio")
        raw = response.text or ""
    except Exception as exc:
        message_text = str(exc)
        if "RESOURCE_EXHAUSTED" in message_text or "429" in message_text:
            reply = (
                "Gemini rate limit hit. Please wait ~15s and try again, "
                "or upgrade the API quota."
            )
        else:
            reply = "LLM request failed. Please try again."
        session["intake_history"].append({"role": "assistant", "content": reply})
        return IntakeResult(reply=reply)

    proposed_portfolio = _safe_json_extract(raw)
    if proposed_portfolio:
        session["proposed_portfolio"] = proposed_portfolio
        reply = (
            "Here is a proposed allocation. Reply 'accept' to save it, "
            "or tell me what to change.\n"
            f"{json.dumps(proposed_portfolio)}"
        )
    else:
        reply = raw.strip() or "Can you share more about your risk and goals?"

    session["intake_history"].append({"role": "assistant", "content": reply})
    if len(session["intake_history"]) > 30:
        session["intake_history"] = session["intake_history"][-30:]
    return IntakeResult(
        reply=reply,
        proposed_portfolio=proposed_portfolio,
        accepted=False,
        raw_response=raw,
        prompt=prompt,
    )

