"""
Daily Alpha Vantage refresh for ``*_daily.csv`` files.

Runs ``fetch_alphavantage_daily.py --append`` for each ticker in
``data_output/tickers_in_data_output.csv``. Default schedule: 06:00 America/Los_Angeles
(override via ``ALPHAVANTAGE_DAILY_CRON``).
"""

from __future__ import annotations

import os

import pendulum
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.timetables.trigger import CronTriggerTimetable

from alphavantage_lib import fetch_daily_for_tickers

_DAILY_CRON = os.environ.get("ALPHAVANTAGE_DAILY_CRON", "0 6 * * *")

with DAG(
    dag_id="alphavantage_daily_merge",
    description="Daily OHLCV CSV merge (compact fetch + merge by date)",
    schedule=CronTriggerTimetable(_DAILY_CRON, timezone="America/Los_Angeles"),
    start_date=pendulum.datetime(2025, 1, 1, tz="America/Los_Angeles"),
    catchup=False,
    tags=["alphavantage", "daily"],
) as dag:
    PythonOperator(
        task_id="fetch_daily_for_all_tickers",
        python_callable=fetch_daily_for_tickers,
    )
