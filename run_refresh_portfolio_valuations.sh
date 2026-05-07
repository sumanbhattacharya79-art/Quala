#!/usr/bin/env bash
# Daily cron: refresh mark-to-market portfolio_value and history for all saved portfolios of a user.
# Requires backend running. Usage: ./run_refresh_portfolio_valuations.sh YOUR_USER_UUID
# Optional: PORTFOLIO_API=http://127.0.0.1:8000
set -euo pipefail
API_ROOT="${PORTFOLIO_API:-http://127.0.0.1:8000}"
USER_ID="${1:?usage: $0 <user_id>}"
curl -sS -X POST "${API_ROOT}/api/portfolio/saved/refresh-valuations" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"${USER_ID}\"}"
echo
