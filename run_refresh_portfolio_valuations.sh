#!/usr/bin/env bash
# Daily cron: refresh mark-to-market + daily portfolio_value_history for ALL saved portfolios.
# Prefer this script after daily CSV updates (syncs GCS when GCS_BUCKET is set).
#
# Usage:
#   ./run_refresh_portfolio_valuations.sh              # all portfolios (direct DB + data_output)
#   ./run_refresh_portfolio_valuations.sh USER_UUID    # one user via API (legacy)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
if [[ "${1:-}" == "" ]]; then
  if [[ -n "${GCS_BUCKET:-}" ]]; then
    PYTHONPATH=.:app python3 -c "from backend.data_output_gcs import sync_data_output_from_gcs; sync_data_output_from_gcs()"
  fi
  PYTHONPATH=app python3 scripts/jobs/daily_refresh_saved_portfolio_values.py
  exit $?
fi
API_ROOT="${PORTFOLIO_API:-http://127.0.0.1:8000}"
USER_ID="${1}"
curl -sS -X POST "${API_ROOT}/api/portfolio/saved/refresh-valuations" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"${USER_ID}\"}"
echo
