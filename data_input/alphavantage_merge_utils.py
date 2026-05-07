"""Helpers to merge new Alpha Vantage rows into existing CSVs without dropping history."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def merge_timeseries_by_date_index(
    output_path: Path,
    new_df: pd.DataFrame,
    *,
    date_index_name: str = "date",
) -> pd.DataFrame:
    """
    Merge ``new_df`` (indexed by date string or datetime) into existing CSV at ``output_path``.
    Duplicate dates keep the **last** occurrence (new API data wins).
    """
    new_df = new_df.copy()
    if new_df.index.name != date_index_name:
        new_df.index.name = date_index_name

    if not output_path.exists():
        return new_df.sort_index()

    old = pd.read_csv(output_path, index_col=0)
    old.index = pd.to_datetime(old.index).strftime("%Y-%m-%d")
    new_df.index = pd.to_datetime(new_df.index).strftime("%Y-%m-%d")

    combined = pd.concat([old, new_df])
    combined = combined[~combined.index.duplicated(keep="last")]
    combined = combined.sort_index()
    combined.index.name = date_index_name
    return combined


def merge_dividend_rows(output_path: Path, new_df: pd.DataFrame) -> pd.DataFrame:
    """Merge dividend rows on ``ex_dividend_date``; duplicates keep last."""
    new_df = new_df.copy()
    if new_df.empty or "ex_dividend_date" not in new_df.columns:
        if not output_path.exists():
            return new_df
        old = pd.read_csv(output_path)
        return old.sort_values("ex_dividend_date", ascending=False)
    if not output_path.exists():
        return new_df.sort_values("ex_dividend_date", ascending=False)

    old = pd.read_csv(output_path)
    combined = pd.concat([old, new_df], ignore_index=True)
    if "ex_dividend_date" not in combined.columns:
        return new_df
    combined = combined.drop_duplicates(subset=["ex_dividend_date"], keep="last")
    return combined.sort_values("ex_dividend_date", ascending=False)


def atomic_write_csv(df: pd.DataFrame, output_path: Path, *, index: bool = True) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(output_path.suffix + ".tmp")
    df.to_csv(tmp, index=index)
    tmp.replace(output_path)


def merge_sector_weights_history(
    output_path: Path,
    as_of_date: str,
    sector_weights: dict[str, float],
) -> pd.DataFrame:
    """
    Append one snapshot (as_of_date × sectors) to ``*_sector_weights_history.csv``.
    Same ``as_of_date`` replaced if re-fetched (dedupe keep last).
    """
    new_rows = pd.DataFrame(
        [
            {"as_of_date": as_of_date, "sector": s, "weight": float(w)}
            for s, w in sector_weights.items()
        ]
    )
    if not output_path.exists():
        return new_rows.sort_values(["as_of_date", "sector"])

    old = pd.read_csv(output_path)
    combined = pd.concat([old, new_rows], ignore_index=True)
    combined = combined.drop_duplicates(subset=["as_of_date", "sector"], keep="last")
    return combined.sort_values(["as_of_date", "sector"])
