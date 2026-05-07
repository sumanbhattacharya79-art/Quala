#!/usr/bin/env bash
# Source from this directory:  source airflow/env.sh
AIRFLOW_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
export AIRFLOW_HOME="${AIRFLOW_HOME:-$AIRFLOW_DIR/airflow_home}"
export AIRFLOW__CORE__DAGS_FOLDER="$AIRFLOW_DIR/dags"
export AIRFLOW__CORE__LOAD_EXAMPLES="False"
# Optional: throttle Alpha Vantage in DAG PythonOperator runs
export ALPHAVANTAGE_SLEEP_SEC="${ALPHAVANTAGE_SLEEP_SEC:-12}"
