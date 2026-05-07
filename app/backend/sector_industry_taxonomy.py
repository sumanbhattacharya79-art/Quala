"""Canonical sector (asset class) vs industry (GICS-style) labels for portfolio breakdown charts."""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Tuple

# High-level asset allocation shown in "Sector breakdown"
ASSET_CLASS_SECTORS: Tuple[str, ...] = (
    "US Stocks",
    "International Stocks",
    "Bonds",
    "Commodities",
    "Digital Assets",
    "Other",
)

# GICS-inspired sectors for "Industry breakdown" (equity + other; bond/crypto/commodity → Other)
GICS_INDUSTRY_SECTORS: Tuple[str, ...] = (
    "Technology",
    "Financials",
    "Communication Services",
    "Consumer Discretionary",
    "Consumer Staples",
    "Health Care",
    "Industrials",
    "Energy",
    "Utilities",
    "Materials",
    "Real Estate",
    "Other",
)

_ASSET_CLASS_SET = set(ASSET_CLASS_SECTORS)
_INDUSTRY_SET = set(GICS_INDUSTRY_SECTORS)

# lowercase → canonical asset class
_ASSET_ALIASES: Dict[str, str] = {
    # US equities / stocks
    "us stocks": "US Stocks",
    "us stock": "US Stocks",
    "us equities": "US Stocks",
    "us equity": "US Stocks",
    "u.s. equities": "US Stocks",
    "u.s. stocks": "US Stocks",
    "domestic equities": "US Stocks",
    "domestic stocks": "US Stocks",
    "diversified us equities": "US Stocks",
    "stocks": "US Stocks",
    "equities": "US Stocks",
    # International
    "international stocks": "International Stocks",
    "intl stocks": "International Stocks",
    "international equities": "International Stocks",
    "intl equities": "International Stocks",
    "international equity": "International Stocks",
    "global ex-us": "International Stocks",
    "global ex us": "International Stocks",
    "developed international": "International Stocks",
    "emerging markets": "International Stocks",
    # Fixed income
    "bonds": "Bonds",
    "bond": "Bonds",
    "fixed income": "Bonds",
    # Commodities / real assets (tangible baskets)
    "commodities": "Commodities",
    "commodity": "Commodities",
    "gold": "Commodities",
    "silver": "Commodities",
    "precious metals": "Commodities",
    "real assets": "Commodities",
    "oil": "Commodities",
    # Digital assets
    "digital assets": "Digital Assets",
    "digital asset": "Digital Assets",
    "crypto": "Digital Assets",
    "cryptocurrency": "Digital Assets",
    "cryptocurrencies": "Digital Assets",
    "bitcoin": "Digital Assets",
    "ethereum": "Digital Assets",
}

# lowercase → canonical industry (plus roll-ups from finer labels)
_INDUSTRY_ALIASES: Dict[str, str] = {
    "information technology": "Technology",
    "healthcare": "Health Care",
    "health care": "Health Care",
    "health": "Health Care",
    "telecommunications": "Communication Services",
    "telecom": "Communication Services",
    "telecommunication services": "Communication Services",
    "media": "Communication Services",
    "entertainment": "Communication Services",
    "interactive media": "Communication Services",
    "consumer cyclical": "Consumer Discretionary",
    "consumer defensive": "Consumer Staples",
    "staples": "Consumer Staples",
    "consumer staples": "Consumer Staples",
    "consumer discretionary": "Consumer Discretionary",
    "software": "Technology",
    "semiconductors": "Technology",
    "semiconductor": "Technology",
    "it services": "Technology",
    "hardware": "Technology",
    "consumer electronics": "Technology",
    "internet retail": "Consumer Discretionary",
    "retail": "Consumer Discretionary",
    "restaurants": "Consumer Discretionary",
    "automotive": "Consumer Discretionary",
    "banks": "Financials",
    "insurance": "Financials",
    "capital markets": "Financials",
    "diversified financials": "Financials",
    "financials": "Financials",
    "pharmaceuticals": "Health Care",
    "biotechnology": "Health Care",
    "biotech": "Health Care",
    "healthcare equipment": "Health Care",
    "healthcare services": "Health Care",
    "oil & gas": "Energy",
    "oil and gas": "Energy",
    "metals & mining": "Materials",
    "metals and mining": "Materials",
    "chemicals": "Materials",
    "aerospace & defense": "Industrials",
    "aerospace and defense": "Industrials",
    "machinery": "Industrials",
    "transportation": "Industrials",
    "building products": "Industrials",
    "electric utilities": "Utilities",
    "water utilities": "Utilities",
    "reits": "Real Estate",
    "reit": "Real Estate",
    "fixed income": "Other",
    "bonds": "Other",
    "commodities": "Other",
}


def _norm_key(label: str) -> str:
    return str(label or "").strip()


def _collapse_weights_to_canonical(
    raw: Optional[Dict[str, float]],
    canonical_set: set,
    alias_map: Dict[str, str],
    fallback: str,
) -> Dict[str, float]:
    if not isinstance(raw, dict) or not raw:
        return {}
    buckets: Dict[str, float] = {}
    for k, v in raw.items():
        key = _norm_key(k)
        if not key:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv <= 0:
            continue
        lk = key.lower()
        canon: Optional[str] = None
        if key in canonical_set:
            canon = key
        elif lk in alias_map:
            canon = alias_map[lk]
        else:
            # Title-style match against canonical (e.g. "Us Equities")
            for c in canonical_set:
                if c.lower() == lk:
                    canon = c
                    break
        if not canon or canon not in canonical_set:
            canon = fallback
        buckets[canon] = buckets.get(canon, 0.0) + fv
    total = sum(buckets.values())
    if total <= 0:
        return {}
    return {kk: vv / total for kk, vv in buckets.items()}


def normalize_asset_class_weights(raw: Optional[Dict[str, float]]) -> Dict[str, float]:
    """Map mixed labels into US Stocks, International Stocks, Bonds, Commodities, Digital Assets, Other."""
    return _collapse_weights_to_canonical(
        raw, _ASSET_CLASS_SET, _ASSET_ALIASES, "Other"
    )


def normalize_gics_industry_weights(raw: Optional[Dict[str, float]]) -> Dict[str, float]:
    """Map labels into the 11 named GICS sectors plus Other."""
    return _collapse_weights_to_canonical(
        raw, _INDUSTRY_SET, _INDUSTRY_ALIASES, "Other"
    )


def _canonical_single_label(
    raw_label: str,
    normalize_fn,
) -> str:
    key = str(raw_label or "").strip() or "Other"
    normalized = normalize_fn({key: 1.0})
    if not normalized:
        return "Other"
    return next(iter(normalized.keys()))


def build_breakdown_ticker_lists(
    weights: Dict[str, float],
    ticker_to_asset_class: Dict[str, str],
    ticker_to_sector: Dict[str, str],
) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    """
    Group tickers by canonical asset class and GICS sector for bar-chart tooltips.
    Tickers under each key sorted by descending portfolio weight.
    """
    ac_lists: Dict[str, List[Tuple[str, float]]] = {}
    sec_lists: Dict[str, List[Tuple[str, float]]] = {}
    for t_raw, w in (weights or {}).items():
        t = str(t_raw or "").strip().upper()
        if not t:
            continue
        try:
            wf = float(w)
        except (TypeError, ValueError):
            continue
        if wf <= 0:
            continue
        ac_lab = _canonical_single_label(
            ticker_to_asset_class.get(t, "Other"), normalize_asset_class_weights
        )
        sec_lab = _canonical_single_label(
            ticker_to_sector.get(t, "Other"), normalize_gics_industry_weights
        )
        ac_lists.setdefault(ac_lab, []).append((t, wf))
        sec_lists.setdefault(sec_lab, []).append((t, wf))

    def sort_tickers(bucket: Dict[str, List[Tuple[str, float]]]) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for k, pairs in bucket.items():
            pairs.sort(key=lambda x: -x[1])
            out[k] = [p[0] for p in pairs]
        return out

    return sort_tickers(ac_lists), sort_tickers(sec_lists)


def portfolio_industry_weights_from_per_ticker_maps(
    weights: Dict[str, float],
    per_ticker_maps: Dict[str, Dict[str, float]],
) -> Dict[str, float]:
    """Roll portfolio into GICS sector weights using each ticker's sector-fraction map (e.g. ETF sleeves).

    Contribution from ticker *t* to sector *s* is ``weights[t] * map[t][s]``. Matches the logic of
    aggregating Alpha Vantage / CSV sector weights across holdings.
    """
    agg: Dict[str, float] = {}
    for t_raw, wf in (weights or {}).items():
        t = str(t_raw or "").strip().upper()
        if not t:
            continue
        try:
            wv = float(wf)
        except (TypeError, ValueError):
            continue
        if wv <= 0:
            continue
        raw_m = per_ticker_maps.get(t) or {"Other": 1.0}
        m = normalize_gics_industry_weights(raw_m) or {"Other": 1.0}
        for sec, frac in m.items():
            try:
                fv = float(frac)
            except (TypeError, ValueError):
                continue
            if fv <= 0:
                continue
            agg[sec] = agg.get(sec, 0.0) + wv * fv
    if not agg:
        return {}
    # Do not call ``normalize_gics_industry_weights`` on the aggregate: that rescales to sum 1 and would
    # misstate true portfolio mass (e.g. a 25% JEPI sleeve would look like 100% of the sector bar).
    return agg


def build_industry_ticker_lists_from_per_ticker_maps(
    weights: Dict[str, float],
    per_ticker_maps: Dict[str, Dict[str, float]],
) -> Dict[str, List[str]]:
    """GICS sector → tickers for bar hovers; each ticker listed under every sector it contributes to.

    Within a sector, tickers are ordered by marginal contribution ``weight × sector_fraction`` (desc).
    """
    sec_lists: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
    for t_raw, wf in (weights or {}).items():
        t = str(t_raw or "").strip().upper()
        if not t:
            continue
        try:
            wv = float(wf)
        except (TypeError, ValueError):
            continue
        if wv <= 0:
            continue
        raw_m = per_ticker_maps.get(t) or {"Other": 1.0}
        m = normalize_gics_industry_weights(raw_m) or {"Other": 1.0}
        for sec, frac in m.items():
            try:
                fv = float(frac)
            except (TypeError, ValueError):
                continue
            if fv <= 0:
                continue
            contrib = wv * fv
            if contrib <= 1e-15:
                continue
            sec_lists[sec].append((t, contrib))
    out: Dict[str, List[str]] = {}
    for sec, pairs in sec_lists.items():
        merged: Dict[str, float] = {}
        for sym, c in pairs:
            merged[sym] = merged.get(sym, 0.0) + c
        sorted_syms = sorted(merged.items(), key=lambda x: -x[1])
        out[sec] = [s for s, _ in sorted_syms]
    return out


def rollup_weights_from_ticker_classification(
    weights: Dict[str, float],
    ticker_to_label: Dict[str, str],
    normalize_fn,
) -> Dict[str, float]:
    """
    Sum ticker weights into the same canonical buckets as ``build_breakdown_ticker_lists``.
    Use this for bar-chart segment labels so each row's label matches ``sectors_tickers`` /
    ``industries_tickers`` keys (proposal-only LLM aggregates often disagree with per-ticker classification).
    """
    buckets: Dict[str, float] = {}
    for t_raw, wf in (weights or {}).items():
        t = str(t_raw or "").strip().upper()
        if not t:
            continue
        try:
            wv = float(wf)
        except (TypeError, ValueError):
            continue
        if wv <= 0:
            continue
        lab = _canonical_single_label(ticker_to_label.get(t, "Other"), normalize_fn)
        buckets[lab] = buckets.get(lab, 0.0) + wv
    if not buckets:
        return {}
    return normalize_fn(buckets)


def merge_gics_industry_keys_preserving_mass(raw: Optional[Dict[str, float]]) -> Dict[str, float]:
    """Rewrite sector keys to canonical GICS labels without rescaling so portfolio masses stay true."""
    if not raw or not isinstance(raw, dict):
        return {}
    out: Dict[str, float] = {}
    for k, v in raw.items():
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv <= 0:
            continue
        canon_map = normalize_gics_industry_weights({str(k).strip(): 1.0})
        ck = next(iter(canon_map.keys()), "Other") if canon_map else "Other"
        out[ck] = out.get(ck, 0.0) + fv
    return out


def normalize_industry_ticker_rollup(raw: Optional[Dict[str, List[str]]]) -> Dict[str, List[str]]:
    """Re-key industry → tickers hover map to canonical GICS names (e.g. Information Technology → Technology)."""
    if not raw or not isinstance(raw, dict):
        return {}
    merged: Dict[str, List[str]] = {}
    for k, arr in raw.items():
        if not isinstance(arr, list):
            continue
        canon_map = normalize_gics_industry_weights({str(k): 1.0})
        canon = next(iter(canon_map.keys()), "Other") if canon_map else "Other"
        out_list = merged.setdefault(canon, [])
        seen = set(out_list)
        for t in arr:
            sym = str(t).strip().upper()
            if sym and sym not in seen:
                seen.add(sym)
                out_list.append(sym)
    return merged


def apply_taxonomy_to_artifact(artifacts: Optional[Dict[str, object]]) -> None:
    """Normalize portfolio_sectors vs portfolio_industries in place for UI charts."""
    if not artifacts:
        return
    inn = artifacts.get("intake")
    if isinstance(inn, dict):
        from backend.intake_parser import sanitize_intake_dict_for_timeline_chart

        sanitize_intake_dict_for_timeline_chart(inn)
    ps = artifacts.get("portfolio_sectors")
    if isinstance(ps, dict) and ps:
        artifacts["portfolio_sectors"] = normalize_asset_class_weights(ps)
    pi = artifacts.get("portfolio_industries")
    if isinstance(pi, dict) and pi:
        artifacts["portfolio_industries"] = merge_gics_industry_keys_preserving_mass(pi)
    pit = artifacts.get("portfolio_industries_tickers")
    if isinstance(pit, dict) and pit:
        artifacts["portfolio_industries_tickers"] = normalize_industry_ticker_rollup(pit)


def _normalize_one_proposal_portfolio(port: Dict[str, object]) -> None:
    if not isinstance(port, dict):
        return
    sec = port.get("sectors")
    if isinstance(sec, dict) and sec:
        port["sectors"] = normalize_asset_class_weights(sec)
    ind = port.get("industries")
    if isinstance(ind, dict) and ind:
        port["industries"] = normalize_gics_industry_weights(ind)
    iticks = port.get("industries_tickers")
    if isinstance(iticks, dict) and iticks:
        port["industries_tickers"] = normalize_industry_ticker_rollup(iticks)
    ret = port.get("retirement")
    if isinstance(ret, dict) and ret:
        rs = ret.get("sectors")
        if isinstance(rs, dict) and rs:
            ret["sectors"] = normalize_asset_class_weights(rs)
        ri = ret.get("industries")
        if isinstance(ri, dict) and ri:
            ret["industries"] = normalize_gics_industry_weights(ri)
        rit = ret.get("industries_tickers")
        if isinstance(rit, dict) and rit:
            ret["industries_tickers"] = normalize_industry_ticker_rollup(rit)


def normalize_all_portfolios_proposals(all_portfolios: Optional[Dict[str, object]]) -> None:
    """Normalize sectors/industries in each Quala/Panda scenario (and nested retirement) for chart labels."""
    if not isinstance(all_portfolios, dict):
        return
    for p in all_portfolios.values():
        _normalize_one_proposal_portfolio(p if isinstance(p, dict) else {})
