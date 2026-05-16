#!/usr/bin/env python3
"""Scan data_output for price/sector files and write a sorted list of unique tickers."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

SUFFIXES = (
    "_monthly.csv",
    "_daily.csv",
    "_dividend.csv",
    "_sector_weights.csv",
)


def write_tickers_in_data_output_csv(
    data_out: Path | None = None,
    *,
    out_path: Path | None = None,
) -> tuple[Path, int]:
    """Scan ``data_out`` for recognized filenames and write ``tickers_in_data_output.csv``.

    Returns ``(path_written, num_tickers)``.
    """
    root = (data_out or Path(__file__).resolve().parents[1] / "data_output").resolve()
    target = (out_path or (root / "tickers_in_data_output.csv")).resolve()

    tickers: set[str] = set()
    if not root.is_dir():
        root.mkdir(parents=True, exist_ok=True)
    for p in root.iterdir():
        if not p.is_file():
            continue
        if p.resolve() == target:
            continue
        name = p.name
        for suf in SUFFIXES:
            if name.endswith(suf):
                tickers.add(name[: -len(suf)].upper())
                break

    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as f:
        f.write("ticker\n")
        for t in sorted(tickers):
            f.write(f"{t}\n")
    return target, len(tickers)


def refresh_tickers_list_after_fetch(
    data_out: Path | None = None,
    *,
    log: logging.Logger | None = None,
    gcs_upload_relative: tuple[str, ...] = (),
) -> None:
    """Rescan ``data_output`` and rewrite ``tickers_in_data_output.csv``.

    When ``GCS_BUCKET`` is set (and ``GCS_UPLOAD_AFTER_FETCH`` is not disabled), uploads the
    ticker list CSV plus any ``gcs_upload_relative`` paths under ``data_output`` to GCS and
    updates the upload marker so other instances / jobs see new data.
    """
    lg = log or logging.getLogger(__name__)
    root = (data_out or Path(__file__).resolve().parents[1] / "data_output").resolve()
    try:
        written, n = write_tickers_in_data_output_csv(root)
        lg.info("Updated tickers_in_data_output.csv (%s tickers)", n)
    except Exception as exc:
        lg.warning("tickers_in_data_output.csv refresh failed: %s", exc)
        return

    try:
        from backend.data_output_gcs import upload_data_output_files_to_gcs_if_configured
    except ImportError:
        return

    extra = [root / rel for rel in gcs_upload_relative if (root / rel).is_file()]
    paths = [written] + [p for p in extra if p.resolve() != written.resolve()]
    uniq: list[Path] = list(dict.fromkeys(paths))
    try:
        upload_data_output_files_to_gcs_if_configured(uniq, local_root=root)
    except Exception as exc:
        lg.warning("GCS upload after ticker list refresh failed: %s", exc)


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
    written, n = write_tickers_in_data_output_csv(data_out, out_path=out_path)
    print(f"Wrote {n} tickers to {written}")


if __name__ == "__main__":
    main()
