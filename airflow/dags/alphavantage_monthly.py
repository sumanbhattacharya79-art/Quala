"""
Monthly Alpha Vantage refresh: last calendar day of each month at 15:00 America/Los_Angeles.

Runs ``fetch_alphavantage_example.py``, ``fetch_alphavantage_dividend.py``, and
``fetch_alphavantage_sector_weights.py`` with ``--append`` for each ticker in
``data_output/tickers_in_data_output.csv``.

Schedule uses days 28–31 at 15:00 PT plus a short-circuit so only true month-end runs execute.
"""

from __future__ import annotations

from datetime import timedelta

import pendulum
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.python import ShortCircuitOperator
from airflow.timetables.trigger import CronTriggerTimetable

from alphavantage_lib import fetch_monthly_bundle_for_tickers


def _is_last_calendar_day_of_month_la(**context) -> bool:
    end = context["data_interval_end"]
    la = end.in_timezone("America/Los_Angeles")
    nxt = la + timedelta(days=1)
    return nxt.day == 1


with DAG(
    dag_id="alphavantage_monthly_merge",
    description="Monthly + dividend + sector weights CSV merge (month-end 3pm PT)",
    schedule=CronTriggerTimetable("0 15 28-31 * *", timezone="America/Los_Angeles"),
    start_date=pendulum.datetime(2025, 1, 1, tz="America/Los_Angeles"),
    catchup=False,
    tags=["alphavantage", "monthly"],
) as dag:
    gate = ShortCircuitOperator(
        task_id="last_calendar_day_pacific",
        python_callable=_is_last_calendar_day_of_month_la,
    )
    run_monthly = PythonOperator(
        task_id="fetch_monthly_dividend_sector",
        python_callable=fetch_monthly_bundle_for_tickers,
    )
    gate >> run_monthly
