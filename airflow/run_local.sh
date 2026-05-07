#!/usr/bin/env bash
# Start webserver (port 8080) and scheduler. Press Ctrl+C to stop both.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
source "$ROOT/env.sh"
# shellcheck source=/dev/null
source "$ROOT/.venv/bin/activate"

WEB_PORT="${AIRFLOW_WEBSERVER_PORT:-8080}"

cleanup() {
  kill "${SCHED_PID:-0}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Starting scheduler..."
airflow scheduler &
SCHED_PID=$!

echo "Starting webserver on http://127.0.0.1:${WEB_PORT} (login: admin / password from setup or 'admin')"
exec airflow webserver --port "${WEB_PORT}"
