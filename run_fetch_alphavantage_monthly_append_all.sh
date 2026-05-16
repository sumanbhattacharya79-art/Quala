#!/usr/bin/env bash
# For every ticker in data_output/tickers_in_data_output.csv, run (with --append):
#   1) fetch_alphavantage_example.py       -> data_output/{SYM}_monthly.csv
#   2) fetch_alphavantage_dividend.py      -> data_output/{SYM}_dividend.csv
#   3) fetch_alphavantage_sector_weights.py -> data_output/{sym}_sector_weights.csv (+ optional history)
#
# Intended for cron, e.g. (adjust path and schedule):
#   0 7 1 * * cd /path/to/portfolio_optimizer && ./run_fetch_alphavantage_monthly_append_all.sh >>/tmp/av_monthly.log 2>&1
#
# Env:
#   PYTHON_BIN              default: python3
#   ALPHAVANTAGE_SLEEP_SEC  seconds between tickers after all three steps (default: 12); set 0 to disable
#
# Extra args are passed to each Python script, e.g. --insecure

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SCRIPT_MONTHLY="${ROOT}/data_input/fetch_alphavantage_example.py"
SCRIPT_DIVIDEND="${ROOT}/data_input/fetch_alphavantage_dividend.py"
SCRIPT_SECTOR="${ROOT}/data_input/fetch_alphavantage_sector_weights.py"
TICKERS_FILE="${ROOT}/data_output/tickers_in_data_output.csv"
SLEEP_SEC="${ALPHAVANTAGE_SLEEP_SEC:-12}"

for f in "$SCRIPT_MONTHLY" "$SCRIPT_DIVIDEND" "$SCRIPT_SECTOR"; do
  if [[ ! -f "$f" ]]; then
    echo "Missing script: $f" >&2
    exit 1
  fi
done
if [[ ! -f "$TICKERS_FILE" ]]; then
  echo "Missing ticker list: $TICKERS_FILE" >&2
  exit 1
fi

lines=()
while IFS= read -r line || [[ -n "${line:-}" ]]; do
  [[ -z "${line//[[:space:]]/}" ]] && continue
  lines+=("$line")
done < <(tail -n +2 "$TICKERS_FILE")

n="${#lines[@]}"
if [[ "$n" -eq 0 ]]; then
  echo "No tickers found in $TICKERS_FILE" >&2
  exit 1
fi

failed=0
for ((i = 0; i < n; i++)); do
  line="${lines[$i]}"
  sym="${line%%,*}"
  sym="${sym//$'\r'/}"
  sym="${sym// /}"
  [[ -z "$sym" ]] && continue

  echo "[$(($i + 1))/${n}] ${sym} (monthly + dividend + sector) ..."

  if ! "$PYTHON_BIN" "$SCRIPT_MONTHLY" --symbol "$sym" --append "$@"; then
    echo "FAILED monthly: ${sym}" >&2
    failed=$((failed + 1))
  fi
  if ! "$PYTHON_BIN" "$SCRIPT_DIVIDEND" --symbol "$sym" --append "$@"; then
    echo "FAILED dividend: ${sym}" >&2
    failed=$((failed + 1))
  fi
  if ! "$PYTHON_BIN" "$SCRIPT_SECTOR" --ticker "$sym" --append "$@"; then
    echo "FAILED sector_weights: ${sym}" >&2
    failed=$((failed + 1))
  fi

  if [[ "$i" -lt $((n - 1)) ]] && [[ "$(echo "$SLEEP_SEC" | tr -d '[:space:]')" != "0" ]]; then
    sleep "$SLEEP_SEC"
  fi
done

if [[ "$failed" -gt 0 ]]; then
  echo "Finished with ${failed} failed step(s). (Continuing; see ERROR logs above.)" >&2
fi
echo "Done (${n} tickers)."
exit 0
