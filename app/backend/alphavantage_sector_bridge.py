"""Bridge to Alpha Vantage sector-weight script with graceful fallback behavior."""

from __future__ import annotations

import csv
import json
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from backend.sector_industry_taxonomy import (
    GICS_INDUSTRY_SECTORS,
    normalize_asset_class_weights,
    normalize_gics_industry_weights,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_OUTPUT_DIR = PROJECT_ROOT / "data_output"
SECTOR_SCRIPT_PATH = PROJECT_ROOT / "data_input" / "fetch_alphavantage_sector_weights.py"

# Keep in sync with `data_input/fetch_alphavantage_sector_weights.py` (materials + synthetic sector tags).
MATERIALS_TICKERS = frozenset(
    {
        "IAUM",
        "GLDM",
        "IAU",
        "GLD",
        "GDX",
        "GDXJ",
        "SIVR",
        "SLV",
        "PSLV",
        "SIL",
        "SILJ",
        "ICOP",
        "COPX",
        "CPER",
        "NLR",
        "URA",
        "URNM",
        "URNJ",
    }
)

# Heuristic asset-class hints when AV returns an equity/ETF-style sector breakdown.
BOND_ETF_TICKERS = frozenset(
    {
        "BND",
        "AGG",
        "SCHZ",
        "MUB",
        "TIP",
        "IEF",
        "GOVT",
        "BIV",
        "LQD",
        "HYG",
        "VCIT",
        "VGIT",
        "SHV",
        "VGSH",
        "BSV",
        "VGLT",
        "TLT",
        "VMBS",
        "BNDX",
        "SPIB",
        "SPTL",
        "SPTS",
    }
)

INTL_EQUITY_TICKERS = frozenset(
    {
        "VXUS",
        "IXUS",
        "VEA",
        "IEFA",
        "VWO",
        "IEMG",
        "SCHF",
        "EFA",
        "SPDW",
        "VEU",
        "IDEV",
        "IXP",
        "ACWX",
        "EWJ",
        "EWZ",
        "EEM",
    }
)

# Spot crypto / crypto ETP tickers for asset-class hint when sector weights are all "Other".
CRYPTO_ETP_TICKERS = frozenset(
    {
        "GBTC",
        "IBIT",
        "FBTC",
        "BITB",
        "HODL",
        "BITO",
        "ETHE",
        "ETHA",
        "ETCG",
        "BTF",
    }
)


def _normalize_weights(raw: Dict[str, float]) -> Dict[str, float]:
    cleaned: Dict[str, float] = {}
    for k, v in raw.items():
        key = str(k).strip()
        if not key:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv <= 0:
            continue
        cleaned[key] = fv
    total = sum(cleaned.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in cleaned.items()}


def _parse_sector_script_output(stdout: str) -> Optional[Dict[str, float]]:
    lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
    for line in reversed(lines):
        if "," not in line or "{" not in line:
            continue
        _ticker, payload = line.split(",", 1)
        try:
            parsed = json.loads(payload.strip())
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        normalized = _normalize_weights(parsed)
        if normalized:
            return normalized
    return None


def _sector_weights_csv_path(ticker: str) -> Path:
    t = str(ticker).strip().upper()
    return DATA_OUTPUT_DIR / f"{t.lower()}_sector_weights.csv"


def _write_sector_weights_csv(ticker: str, weights: Dict[str, float]) -> Path:
    """Write ``{ticker}_sector_weights.csv`` under ``data_output`` (same layout as the AV fetch script)."""
    t = str(ticker).strip().upper()
    path = _sector_weights_csv_path(t)
    DATA_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    norm = _normalize_weights(weights)
    if not norm:
        raise ValueError(f"Refusing to persist empty sector weights for {t}")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["sector", "weight"])
        for sector in sorted(norm.keys()):
            writer.writerow([sector, float(norm[sector])])
    return path


def _read_sector_weights_csv(path: Path) -> Optional[Dict[str, float]]:
    if not path.is_file():
        return None
    raw: Dict[str, float] = {}
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or "sector" not in reader.fieldnames:
                return None
            wkey = "weight" if "weight" in reader.fieldnames else reader.fieldnames[-1]
            for row in reader:
                if not row:
                    continue
                sector = str(row.get("sector") or "").strip()
                if not sector:
                    continue
                try:
                    raw[sector] = float(row.get(wkey) or 0)
                except (TypeError, ValueError):
                    continue
    except OSError as exc:
        logger.warning("Could not read sector weights cache %s: %s", path, exc)
        return None
    return _normalize_weights(raw) or None


def _unique_upper_tickers(tickers: List[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for raw in tickers:
        t = str(raw).strip().upper()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _run_sector_weights_subprocess_and_persist(ticker: str) -> Optional[Dict[str, float]]:
    """Invoke ``fetch_alphavantage_sector_weights.py``. Persist to CSV from script output if the file is missing."""
    t = str(ticker).strip().upper()
    if not t or not SECTOR_SCRIPT_PATH.is_file():
        return None
    cmd = [sys.executable, str(SECTOR_SCRIPT_PATH), "--ticker", t]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
            cwd=str(PROJECT_ROOT),
        )
    except Exception as exc:
        logger.warning("Alpha Vantage sector script failed for %s: %s", t, exc)
        return None
    if proc.returncode != 0:
        logger.warning(
            "Alpha Vantage sector script non-zero for %s (code=%s): %s",
            t,
            proc.returncode,
            (proc.stderr or proc.stdout or "").strip(),
        )
        return None
    path = _sector_weights_csv_path(t)
    from_disk = _read_sector_weights_csv(path)
    if from_disk:
        return from_disk
    parsed = _parse_sector_script_output(proc.stdout or "")
    if parsed:
        _write_sector_weights_csv(t, parsed)
        from_disk = _read_sector_weights_csv(path)
        return from_disk or parsed
    return None


def _llm_infer_sector_weights_batch(
    tickers: List[str],
) -> Dict[str, Tuple[Dict[str, float], Optional[str]]]:
    """LLM fallback: sector weights + optional asset_class; normalized weights persisted under ``data_output``."""
    if not tickers:
        return {}
    try:
        from backend.unused_codes.agentic import LLMClient
    except ImportError:
        logger.warning("LLMClient unavailable; cannot infer sector weights for %s", tickers)
        return {}
    sectors = ", ".join(GICS_INDUSTRY_SECTORS)
    ac_list = "US Stocks, International Stocks, Bonds, Commodities, Digital Assets, Other"
    prompt = (
        "For each ticker, return (1) **asset_class** from: "
        f"{ac_list}. "
        "(2) **sectors**: GICS-style sector weights as decimals summing to 1.0. "
        "Sector object keys must use only these names: "
        f"{sectors}. "
        "Bond funds, physical commodity trusts, pure crypto ETPs: use sectors {\"Other\": 1.0}.\n"
        f"Tickers: {', '.join(tickers)}\n\n"
        "Return JSON only. Per ticker shape: "
        '{"QQQ": {"asset_class": "US Stocks", "sectors": {"Technology": 0.5, "Financials": 0.1}}, ...}\n'
    )
    result: Dict[str, Tuple[Dict[str, float], Optional[str]]] = {}
    try:
        llm = LLMClient()
        raw_resp = llm.complete(prompt)
        match = re.search(r"\{[\s\S]*\}", raw_resp)
        if not match:
            return {}
        parsed = json.loads(match.group(0))
    except Exception as exc:
        logger.warning("LLM sector-weight batch failed: %s", exc)
        return {}
    if not isinstance(parsed, dict):
        return {}
    data = {str(k).strip().upper(): v for k, v in parsed.items()}
    for t in tickers:
        entry = data.get(t)
        if not isinstance(entry, dict):
            continue
        sw_raw = entry.get("sectors") or entry.get("sector_weights")
        if not isinstance(sw_raw, dict):
            continue
        cleaned: Dict[str, float] = {}
        for sk, sv in sw_raw.items():
            try:
                fv = float(sv)
            except (TypeError, ValueError):
                continue
            if fv > 0:
                cleaned[str(sk).strip()] = fv
        nw = normalize_gics_industry_weights(cleaned)
        if not nw:
            continue
        try:
            _write_sector_weights_csv(t, nw)
        except (OSError, ValueError) as exc:
            logger.warning("Could not persist LLM sector weights for %s: %s", t, exc)
            continue
        ac = entry.get("asset_class") or entry.get("asset_type")
        ac_hint = str(ac).strip() if ac else None
        result[t] = (nw, ac_hint)
        logger.info("Sector weights for %s inferred via LLM and saved to data_output.", t)
    return result


def resolve_ticker_sector_weights_pipeline(
    tickers: List[str],
) -> Dict[str, Tuple[Dict[str, float], Optional[str]]]:
    """Resolve sector weights: ``data_output`` CSV → AV script (persist) → LLM batch (persist).

    Values are ``(normalized_sector_weights, optional_asset_class_hint)`` where the hint is set only
    when the LLM fallback supplied ``asset_class``.
    """
    out: Dict[str, Tuple[Dict[str, float], Optional[str]]] = {}
    uniq = _unique_upper_tickers(tickers)
    need_script: List[str] = []
    for t in uniq:
        path = _sector_weights_csv_path(t)
        cached = _read_sector_weights_csv(path)
        if cached:
            out[t] = (cached, None)
            logger.debug("Sector weights for %s read from %s", t, path.name)
        else:
            need_script.append(t)

    need_llm: List[str] = []
    for t in need_script:
        sw = _run_sector_weights_subprocess_and_persist(t)
        if sw:
            out[t] = (sw, None)
        else:
            need_llm.append(t)

    if need_llm:
        for t, pair in _llm_infer_sector_weights_batch(need_llm).items():
            out[t] = pair

    return out


def per_ticker_normalized_gics_maps_for_tickers(tickers: List[str]) -> Dict[str, Dict[str, float]]:
    """Per ticker: normalized GICS sector fractions (sum 1). Uses CSV / AV script / LLM pipeline.

    Tickers with no usable map get ``Other: 100%`` so portfolio mass stays accounted for.
    """
    uniq = _unique_upper_tickers(tickers)
    if not uniq:
        return {}
    resolved = resolve_ticker_sector_weights_pipeline(uniq)
    out: Dict[str, Dict[str, float]] = {}
    for t in uniq:
        pair = resolved.get(t)
        if pair and pair[0]:
            nw = normalize_gics_industry_weights(pair[0])
            if nw:
                out[t] = nw
                continue
        out[t] = {"Other": 1.0}
    return out


def _fetch_single_ticker_sector_weights(ticker: str) -> Optional[Dict[str, float]]:
    """Disk → script → LLM; returns sector-weight map only (see :func:`resolve_ticker_sector_weights_pipeline`)."""
    t = str(ticker).strip().upper()
    if not t:
        return None
    resolved = resolve_ticker_sector_weights_pipeline([t])
    pair = resolved.get(t)
    if not pair:
        return None
    return pair[0]


def _top_sector_label(sector_weights: Dict[str, float]) -> Optional[str]:
    normalized = _normalize_weights(sector_weights)
    if not normalized:
        return None
    return max(normalized.items(), key=lambda kv: kv[1])[0]


def _is_crypto_av_weights(sector_weights: Dict[str, float]) -> bool:
    keys = {str(k).strip().lower() for k in (sector_weights or {})}
    return bool(keys) and keys <= {"crypto"}


def _infer_asset_class_from_av(ticker: str, sector_weights: Dict[str, float]) -> str:
    t = str(ticker).strip().upper()
    if t in MATERIALS_TICKERS:
        return "Commodities"
    if _is_crypto_av_weights(sector_weights):
        return "Digital Assets"
    if t in CRYPTO_ETP_TICKERS:
        return "Digital Assets"
    if t in BOND_ETF_TICKERS:
        return "Bonds"
    if t in INTL_EQUITY_TICKERS:
        return "International Stocks"
    return "US Stocks"


def _canonical_gics_label_from_av_weights(sector_weights: Dict[str, float]) -> str:
    if not sector_weights:
        return "Other"
    if _is_crypto_av_weights(sector_weights):
        return "Other"
    top = _top_sector_label(sector_weights)
    if not top:
        return "Other"
    normalized = normalize_gics_industry_weights({top: 1.0})
    if not normalized:
        return "Other"
    return next(iter(normalized.keys()))


def canonical_gics_sector_from_av_weights(
    sector_weights: Optional[Dict[str, float]],
) -> Optional[str]:
    """Single canonical GICS-style label from an AV sector-weight map, or None if empty."""
    if not sector_weights:
        return None
    lab = _canonical_gics_label_from_av_weights(sector_weights)
    return lab if lab else None


def gics_sector_for_ticker_via_alphavantage_script(ticker: str) -> Optional[str]:
    """Canonical GICS label from cached CSV / AV script / LLM sector weights (same pipeline as charts)."""
    t = str(ticker).strip().upper()
    if not t:
        return None
    return canonical_gics_sector_from_av_weights(_fetch_single_ticker_sector_weights(t))


def seed_ticker_classification_cache_from_alphavantage(
    cache: Dict[str, object],
    tickers: List[str],
) -> None:
    """Fill session cache from ``data_output`` CSV, then AV script, then LLM sector-weight fallback.

    Keys match the analyze-upload cache shape: ``sector`` = asset class, ``industry`` = GICS sector.
    When sector weights are resolved (any source), prior cache entries for that ticker are replaced.
    """
    resolved = resolve_ticker_sector_weights_pipeline(tickers)
    for raw in tickers:
        t = str(raw).strip().upper()
        if not t:
            continue
        pair = resolved.get(t)
        if not pair:
            continue
        sw, ac_hint = pair
        if ac_hint:
            ac_m = normalize_asset_class_weights({ac_hint: 1.0})
            ac_norm = next(iter(ac_m), None) if ac_m else None
            if ac_norm and ac_norm != "Other":
                ac_raw = ac_norm
            else:
                ac_raw = _infer_asset_class_from_av(t, sw)
        else:
            ac_raw = _infer_asset_class_from_av(t, sw)
        gics_raw = _canonical_gics_label_from_av_weights(sw)
        ac_m = normalize_asset_class_weights({ac_raw: 1.0})
        gi_m = normalize_gics_industry_weights({gics_raw: 1.0})
        ac = next(iter(ac_m), "Other") if ac_m else "Other"
        gics = next(iter(gi_m), "Other") if gi_m else "Other"
        cache[t] = {"sector": ac, "industry": gics}


def get_preferred_portfolio_sector_weights(
    ticker_weights: Dict[str, float],
) -> Optional[Dict[str, float]]:
    """Portfolio-level sector mix: per ticker use ``data_output`` CSV, else AV script, else LLM weights.

    Tickers with no resolved weights roll into **Other**. Returns None only when every ticker fails.
    """
    norm_ticker_weights = _normalize_weights(
        {str(k).strip().upper(): v for k, v in (ticker_weights or {}).items()}
    )
    if not norm_ticker_weights:
        return None

    resolved = resolve_ticker_sector_weights_pipeline(list(norm_ticker_weights.keys()))
    aggregate: Dict[str, float] = {}
    failed_tw = 0.0
    any_av = False
    for ticker, tw in norm_ticker_weights.items():
        pair = resolved.get(ticker)
        if not pair or not pair[0]:
            logger.info("Sector weights unavailable for %s; rolling weight to Other.", ticker)
            failed_tw += tw
            continue
        sector_w = pair[0]
        any_av = True
        for sector, sw in sector_w.items():
            aggregate[sector] = aggregate.get(sector, 0.0) + (tw * float(sw))
    if failed_tw > 0:
        aggregate["Other"] = aggregate.get("Other", 0.0) + failed_tw
    if not any_av:
        return None
    normalized = _normalize_weights(aggregate)
    return normalized or None


def get_preferred_ticker_sector_labels(
    tickers: list[str],
) -> Optional[Dict[str, str]]:
    """Preferred ticker->sector labels from Alpha Vantage script.

    Partial map: only tickers with AV data are included. Callers merge this over
    LLM/Quala mappings so a single missing ticker no longer discards AV for all.
    """
    resolved = resolve_ticker_sector_weights_pipeline(
        [str(x).strip().upper() for x in tickers if str(x).strip()]
    )
    labels: Dict[str, str] = {}
    for t, pair in resolved.items():
        sector_weights = pair[0] if pair else None
        if not sector_weights:
            continue
        labels[t] = _canonical_gics_label_from_av_weights(sector_weights)
    return labels if labels else None
