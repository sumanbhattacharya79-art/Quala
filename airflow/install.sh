#!/usr/bin/env bash
# Install Apache Airflow into airflow/.venv using official constraint pins (Python 3.10).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"
AIRFLOW_VERSION="${AIRFLOW_VERSION:-2.8.4}"
PY_MINOR="$("$PY" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
CONSTRAINT_URL="https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PY_MINOR}.txt"

if [[ ! -d .venv ]]; then
  "$PY" -m venv .venv
fi
# shellcheck source=/dev/null
source .venv/bin/activate
python -m pip install --upgrade pip wheel
python -m pip install "apache-airflow==${AIRFLOW_VERSION}" --constraint "${CONSTRAINT_URL}"
echo "Installed $(python -c 'import airflow; print(airflow.__version__)') into $ROOT/.venv"
