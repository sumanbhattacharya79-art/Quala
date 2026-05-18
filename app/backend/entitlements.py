"""Free vs Basic plan limits (see Pricing modal in App.jsx)."""

from __future__ import annotations

from fastapi import HTTPException

from backend.db import (
    count_life_scenarios_for_user,
    count_portfolios_for_user,
    count_scenarios_for_user,
    get_user_billing,
)

FREE_MAX_PORTFOLIOS = 1
BASIC_MAX_PORTFOLIOS = 5
FREE_MAX_SCENARIOS = 0
BASIC_MAX_SCENARIOS = 5

_ACTIVE_SUB_STATUSES = frozenset({"active", "trialing"})


def user_has_basic(user_id: str) -> bool:
    row = get_user_billing(user_id)
    if not row:
        return False
    tier = (row.get("plan_tier") or "free").strip().lower()
    status = (row.get("subscription_status") or "").strip().lower()
    return tier == "basic" and status in _ACTIVE_SUB_STATUSES


def billing_status_payload(user_id: str) -> dict:
    row = get_user_billing(user_id) or {}
    basic = user_has_basic(user_id)
    return {
        "plan_tier": "basic" if basic else "free",
        "subscription_status": row.get("subscription_status"),
        "plan_period_end": row.get("plan_period_end"),
        "has_basic": basic,
        "limits": {
            "max_portfolios": BASIC_MAX_PORTFOLIOS if basic else FREE_MAX_PORTFOLIOS,
            "max_scenarios": BASIC_MAX_SCENARIOS if basic else FREE_MAX_SCENARIOS,
            "life_plans": basic,
            "mr_brown": basic,
            "scenario_planning": basic,
        },
    }


def _upgrade_message(feature: str) -> str:
    return (
        f"{feature} requires the Basic plan ($2/month or $20/year). "
        "Open Pricing to upgrade."
    )


def require_basic(user_id: str, feature: str) -> None:
    if not user_has_basic(user_id):
        raise HTTPException(status_code=402, detail=_upgrade_message(feature))


def assert_can_save_portfolio(user_id: str) -> None:
    n = count_portfolios_for_user(user_id)
    if user_has_basic(user_id):
        if n >= BASIC_MAX_PORTFOLIOS:
            raise HTTPException(
                status_code=402,
                detail=f"You can save up to {BASIC_MAX_PORTFOLIOS} portfolios on Basic. "
                "Delete one or upgrade if we add higher tiers.",
            )
        return
    if n >= FREE_MAX_PORTFOLIOS:
        raise HTTPException(
            status_code=402,
            detail=_upgrade_message("Saving more than one portfolio"),
        )


def assert_can_save_scenario(user_id: str) -> None:
    require_basic(user_id, "Scenario planning")
    n = count_scenarios_for_user(user_id)
    if n >= BASIC_MAX_SCENARIOS:
        raise HTTPException(
            status_code=402,
            detail=f"You can save up to {BASIC_MAX_SCENARIOS} scenarios on Basic.",
        )


def assert_can_save_life_plan(user_id: str) -> None:
    require_basic(user_id, "Life plans")
    if count_life_scenarios_for_user(user_id) >= 1:
        raise HTTPException(
            status_code=400,
            detail="Only one life plan can be saved. Delete your current life plan, then save again.",
        )
