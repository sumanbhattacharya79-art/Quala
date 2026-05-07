"""
Token usage for Gemini via the same SDK as the rest of the app (`from google import genai`).

- **LiteLLM / CrewAI** model strings look like ``gemini/gemini-2.5-flash``.
- **google.genai** ``generate_content(..., model=...)`` expects the id without the provider
  prefix, e.g. ``gemini-2.5-flash``.

Example (raw ``generate_content`` response, aligned with your snippet, using this app's model):

    from google import genai
    from backend.crewai_app.gemini_token_usage import (
        gemini_model_id_for_google_genai_api,
        log_generate_content_usage,
    )

    client = genai.Client(api_key=...)
    response = client.models.generate_content(
        model=gemini_model_id_for_google_genai_api(),
        contents=prompt,
    )
    log_generate_content_usage(logger, response, label="my-call")  # uses response.usage_metadata
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CrewAI / LiteLLM model ids — single place to change defaults (or use env).
# Quala & Panda → money_manager; Ana & Emu → analyst.
# LiteLLM expects ``gemini/<model-id>``; see ``gemini_model_id_for_google_genai_api``.
# ---------------------------------------------------------------------------
_DEFAULT_CREW_GEMINI_MONEY_MANAGER = "gemini/gemini-2.5-flash"
_DEFAULT_CREW_GEMINI_ANALYST = "gemini/gemini-2.5-flash"


def crew_gemini_model_litellm(role: Literal["money_manager", "analyst"]) -> str:
    """
    Authoritative LiteLLM model string for Crew agents.

    - ``money_manager``: Mr. Quala and Panda (portfolio construction).
    - ``analyst``: Ms. Ana and Emu (backtest / retirement analysis).

    Override with environment variables (optional):

    - ``GEMINI_MODEL_MONEY_MANAGER`` — Quala / Panda
    - ``GEMINI_MODEL_ANALYST`` — Ana / Emu
    - ``GEMINI_MODEL`` — legacy; if set, used for money_manager when
      ``GEMINI_MODEL_MONEY_MANAGER`` is unset (non-crew code may still rely on this).
    """
    if role == "money_manager":
        return (
            os.getenv("GEMINI_MODEL_MONEY_MANAGER")
            or os.getenv("GEMINI_MODEL")
            or _DEFAULT_CREW_GEMINI_MONEY_MANAGER
        )
    return os.getenv("GEMINI_MODEL_ANALYST") or _DEFAULT_CREW_GEMINI_ANALYST


def default_gemini_model_litellm() -> str:
    """LiteLLM/CrewAI model string; same as money-manager (Quala/Panda) and generic ``google.genai`` default."""
    return crew_gemini_model_litellm("money_manager")


def gemini_model_id_for_google_genai_api(litellm_model: Optional[str] = None) -> str:
    """
    Strip `provider/` prefix from LiteLLM-style names for `google.genai` Client.

    ``gemini/gemini-2.5-flash`` -> ``gemini-2.5-flash``
    """
    m = (litellm_model or default_gemini_model_litellm()).strip()
    if "/" in m:
        return m.split("/", 1)[1].strip()
    return m


@dataclass(frozen=True)
class GenerateContentUsage:
    """Mirrors ``response.usage_metadata`` from ``google.genai`` generate_content."""

    prompt_token_count: int
    candidates_token_count: int
    total_token_count: int


def extract_usage_from_response(response: Any) -> Optional[GenerateContentUsage]:
    """
    Read token counts from a ``google.genai`` ``GenerateContentResponse`` (or compatible).

    Fields match the user snippet: prompt_token_count, candidates_token_count,
    total_token_count.
    """
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return None

    def _i(name: str) -> int:
        v = getattr(meta, name, None)
        if v is None and isinstance(meta, Mapping):
            v = meta.get(name)
        try:
            return int(v) if v is not None else 0
        except (TypeError, ValueError):
            return 0

    return GenerateContentUsage(
        prompt_token_count=_i("prompt_token_count"),
        candidates_token_count=_i("candidates_token_count"),
        total_token_count=_i("total_token_count"),
    )


def log_generate_content_usage(
    log: logging.Logger,
    response: Any,
    label: str = "",
) -> Optional[GenerateContentUsage]:
    """Log input / output / total tokens from a generate_content response."""
    usage = extract_usage_from_response(response)
    if usage is None:
        log.debug("No usage_metadata on response%s", f" ({label})" if label else "")
        return None
    prefix = f"{label}: " if label else ""
    log.info(
        "%sGemini tokens — prompt (input): %s, candidates (output): %s, total: %s",
        prefix,
        usage.prompt_token_count,
        usage.candidates_token_count,
        usage.total_token_count,
    )
    return usage


@dataclass
class GeminiTokenAccumulator:
    """Aggregate usage across multiple ``generate_content`` calls."""

    prompt_token_count: int = 0
    candidates_token_count: int = 0
    total_token_count: int = 0

    def add_response(self, response: Any) -> Optional[GenerateContentUsage]:
        u = extract_usage_from_response(response)
        if u is None:
            return None
        self.prompt_token_count += u.prompt_token_count
        self.candidates_token_count += u.candidates_token_count
        self.total_token_count += u.total_token_count
        return u

    def as_dict(self) -> dict[str, int]:
        return {
            "prompt_token_count": self.prompt_token_count,
            "candidates_token_count": self.candidates_token_count,
            "total_token_count": self.total_token_count,
        }


def extract_crew_usage_token_counts(crew: Any) -> Optional[tuple[int, int, int]]:
    """
    After ``crew.kickoff()`` / ``log_crewai_usage``, read ``UsageMetrics`` from the crew (best-effort).

    Returns ``(prompt_tokens, completion_tokens, total_tokens)`` or ``None`` if unavailable.
    """
    m = getattr(crew, "usage_metrics", None)
    if m is None:
        try:
            m = crew.calculate_usage_metrics()
        except Exception:
            return None
    try:
        pt = int(getattr(m, "prompt_tokens", 0) or 0)
        ct = int(getattr(m, "completion_tokens", 0) or 0)
        tt = int(getattr(m, "total_tokens", 0) or 0)
    except (TypeError, ValueError):
        return None
    if pt == 0 and ct == 0 and tt == 0:
        return None
    return pt, ct, tt


def log_crewai_usage(crew: Any, log: Optional[logging.Logger] = None) -> None:
    """
    After ``crew.kickoff()``, log CrewAI / LiteLLM aggregated usage (best-effort).

    This complements ``extract_usage_from_response`` (raw ``google.genai``); CrewAI does not
    expose the same ``usage_metadata`` object on every path.
    """
    lg = log or logger
    try:
        metrics = crew.calculate_usage_metrics()
    except Exception as e:
        lg.debug("calculate_usage_metrics failed: %s", e)
        return
    try:
        if hasattr(metrics, "model_dump"):
            lg.info("CrewAI Gemini usage: %s", metrics.model_dump())
        elif hasattr(metrics, "__dict__"):
            lg.info("CrewAI Gemini usage: %s", metrics.__dict__)
        else:
            lg.info("CrewAI Gemini usage: %s", metrics)
    except Exception as e:
        lg.debug("Could not log crew usage metrics: %s", e)
