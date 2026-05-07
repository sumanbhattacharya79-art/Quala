#!/usr/bin/env bash
# Run Alpha Vantage daily fetch for every ticker in data_output/tickers_in_data_output.csv
# and merge new rows into data_output/{SYMBOL}_daily.csv (--append).
#
# Intended for cron, e.g. (6:00 local time; adjust path and TZ):
#   0 6 * * * cd /path/to/portfolio_optimizer && ./run_fetch_alphavantage_daily_append_all.sh >>/tmp/av_daily.log 2>&1
#
# Env:
#   PYTHON_BIN              default: python3
#   ALPHAVANTAGE_SLEEP_SEC  seconds between symbols (default: 12); set 0 to disable
#
# Extra args are passed to fetch_alphavantage_daily.py, e.g. --insecure

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SCRIPT="${ROOT}/data_input/fetch_alphavantage_daily.py"
TICKERS_FILE="${ROOT}/data_output/tickers_in_data_output.csv"
SLEEP_SEC="${ALPHAVANTAGE_SLEEP_SEC:-12}"

if [[ ! -f "$SCRIPT" ]]; then
  echo "Missing script: $SCRIPT" >&2
  exit 1
fi
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

  echo "[$(($i + 1))/${n}] ${sym} ..."
  if ! "$PYTHON_BIN" "$SCRIPT" --symbol "$sym" --append "$@"; then
    echo "FAILED: ${sym}" >&2
    failed=$((failed + 1))
  fi

  if [[ "$i" -lt $((n - 1)) ]] && [[ "$(echo "$SLEEP_SEC" | tr -d '[:space:]')" != "0" ]]; then
    sleep "$SLEEP_SEC"
  fi
done

if [[ "$failed" -gt 0 ]]; then
  echo "Finished with ${failed} failure(s)." >&2
  exit 1
fi
echo "Done (${n} tickers)."
