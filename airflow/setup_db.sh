#!/usr/bin/env bash
# Initialize DB and create default admin (run once after install.sh).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
source "$ROOT/env.sh"
# shellcheck source=/dev/null
source "$ROOT/.venv/bin/activate"

airflow db migrate

if ! airflow users list 2>/dev/null | grep -q 'admin'; then
  airflow users create \
    --username admin \
    --firstname Admin \
    --lastname User \
    --role Admin \
    --email admin@localhost \
    --password "${AIRFLOW_ADMIN_PASSWORD:-admin}"
  echo "Created user: admin / password: ${AIRFLOW_ADMIN_PASSWORD:-admin}"
else
  echo "Admin user already exists; skipping create."
fi

echo "AIRFLOW_HOME=$AIRFLOW_HOME"
echo "DAGs: $AIRFLOW__CORE__DAGS_FOLDER"
airflow dags list
