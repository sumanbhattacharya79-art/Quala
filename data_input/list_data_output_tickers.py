#!/usr/bin/env python3
"""Scan data_output for price/sector files and write a sorted list of unique tickers."""

from __future__ import annotations

import argparse
from pathlib import Path

SUFFIXES = (
    "_monthly.csv",
    "_daily.csv",
    "_dividend.csv",
    "_sector_weights.csv",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data_output",
        help="Directory to scan (default: project data_output).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output CSV path (default: DATA_OUTPUT/tickers_in_data_output.csv).",
    )
    args = parser.parse_args()
    data_out: Path = args.data_output
    out_path = args.out or (data_out / "tickers_in_data_output.csv")

    tickers: set[str] = set()
    for p in data_out.iterdir():
        if not p.is_file():
            continue
        if p.resolve() == out_path.resolve():
            continue
        name = p.name
        for suf in SUFFIXES:
            if name.endswith(suf):
                tickers.add(name[: -len(suf)].upper())
                break

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        f.write("ticker\n")
        for t in sorted(tickers):
            f.write(f"{t}\n")
    print(f"Wrote {len(tickers)} tickers to {out_path}")


if __name__ == "__main__":
    main()
