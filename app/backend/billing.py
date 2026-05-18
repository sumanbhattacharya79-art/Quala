"""Stripe Checkout, Customer Portal, and webhooks."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException

from backend.db import get_user_billing, get_user_by_id, update_user_stripe_billing

_log = logging.getLogger(__name__)

_ACTIVE = frozenset({"active", "trialing"})


def _stripe():
    import stripe

    key = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="Billing is not configured")
    stripe.api_key = key
    return stripe


def stripe_configured() -> bool:
    return bool((os.environ.get("STRIPE_SECRET_KEY") or "").strip())


def _price_id(interval: str) -> str:
    iv = (interval or "monthly").strip().lower()
    if iv in ("year", "yearly", "annual"):
        pid = (os.environ.get("STRIPE_PRICE_BASIC_YEARLY") or "").strip()
        label = "yearly"
    else:
        pid = (os.environ.get("STRIPE_PRICE_BASIC_MONTHLY") or "").strip()
        label = "monthly"
    if not pid:
        raise HTTPException(
            status_code=503,
            detail=f"Stripe price not configured for {label} billing",
        )
    return pid


def _checkout_urls() -> tuple[str, str]:
    success = (os.environ.get("STRIPE_CHECKOUT_SUCCESS_URL") or "").strip()
    cancel = (os.environ.get("STRIPE_CHECKOUT_CANCEL_URL") or "").strip()
    if not success or not cancel:
        raise HTTPException(
            status_code=503,
            detail="STRIPE_CHECKOUT_SUCCESS_URL and STRIPE_CHECKOUT_CANCEL_URL must be set",
        )
    return success, cancel


def _tier_from_subscription_status(status: Optional[str]) -> str:
    st = (status or "").strip().lower()
    return "basic" if st in _ACTIVE else "free"


def _period_end_iso(sub: Any) -> Optional[str]:
    end = getattr(sub, "current_period_end", None)
    if end is None and isinstance(sub, dict):
        end = sub.get("current_period_end")
    if not end:
        return None
    try:
        return datetime.fromtimestamp(int(end), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def apply_subscription_to_user(
    *,
    user_id: str,
    subscription_id: Optional[str],
    customer_id: Optional[str],
    status: Optional[str],
    period_end: Optional[str] = None,
    sub_obj: Any = None,
) -> None:
    tier = _tier_from_subscription_status(status)
    pe = period_end
    if not pe and sub_obj is not None:
        pe = _period_end_iso(sub_obj)
    update_user_stripe_billing(
        user_id=user_id,
        plan_tier=tier,
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id,
        subscription_status=status,
        plan_period_end=pe,
    )
    _log.info(
        "billing: user=%s tier=%s status=%s sub=%s",
        user_id[:8] if user_id else "",
        tier,
        status,
        (subscription_id or "")[:12],
    )


def get_or_create_stripe_customer(user_id: str) -> str:
    stripe = _stripe()
    row = get_user_billing(user_id)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    existing = (row.get("stripe_customer_id") or "").strip()
    if existing:
        return existing
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    email = (user.get("email_id") or "").strip()
    customer = stripe.Customer.create(
        email=email or None,
        metadata={"user_id": user_id},
    )
    cid = customer.id
    update_user_stripe_billing(
        user_id=user_id,
        plan_tier=row.get("plan_tier") or "free",
        stripe_customer_id=cid,
        stripe_subscription_id=row.get("stripe_subscription_id"),
        subscription_status=row.get("subscription_status"),
        plan_period_end=row.get("plan_period_end"),
    )
    return cid


def create_checkout_session(*, user_id: str, billing_interval: str) -> str:
    stripe = _stripe()
    customer_id = get_or_create_stripe_customer(user_id)
    success_url, cancel_url = _checkout_urls()
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        client_reference_id=user_id,
        line_items=[{"price": _price_id(billing_interval), "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"user_id": user_id},
        subscription_data={"metadata": {"user_id": user_id}},
    )
    url = getattr(session, "url", None) or ""
    if not url:
        raise HTTPException(status_code=500, detail="Stripe did not return a checkout URL")
    return url


def create_portal_session(*, user_id: str) -> str:
    stripe = _stripe()
    customer_id = get_or_create_stripe_customer(user_id)
    return_url = (os.environ.get("STRIPE_PORTAL_RETURN_URL") or "").strip()
    if not return_url:
        return_url = _checkout_urls()[1]
    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=return_url,
    )
    url = getattr(session, "url", None) or ""
    if not url:
        raise HTTPException(status_code=500, detail="Stripe did not return a portal URL")
    return url


def _resolve_user_id_from_subscription(sub: Any) -> Optional[str]:
    meta = getattr(sub, "metadata", None) or {}
    if isinstance(meta, dict):
        uid = (meta.get("user_id") or "").strip()
        if uid:
            return uid
    cid = getattr(sub, "customer", None)
    if cid:
        from backend.db import get_user_id_by_stripe_customer

        return get_user_id_by_stripe_customer(str(cid))
    return None


def handle_stripe_webhook(payload: bytes, sig_header: Optional[str]) -> None:
    stripe = _stripe()
    secret = (os.environ.get("STRIPE_WEBHOOK_SECRET") or "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="Webhook secret not configured")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header or "", secret)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook payload") from exc
    except Exception as exc:
        _log.warning("Stripe webhook signature failed: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid webhook signature") from exc

    etype = event.get("type") if isinstance(event, dict) else event.type
    data_obj = event["data"]["object"] if isinstance(event, dict) else event.data.object

    if etype == "checkout.session.completed":
        _on_checkout_completed(data_obj)
    elif etype in ("customer.subscription.updated", "customer.subscription.created"):
        _on_subscription_updated(data_obj)
    elif etype == "customer.subscription.deleted":
        _on_subscription_deleted(data_obj)
    else:
        _log.debug("Stripe webhook ignored: %s", etype)


def _on_checkout_completed(session: Any) -> None:
    user_id = (getattr(session, "client_reference_id", None) or "").strip()
    meta = getattr(session, "metadata", None) or {}
    if not user_id and isinstance(meta, dict):
        user_id = (meta.get("user_id") or "").strip()
    sub_id = getattr(session, "subscription", None)
    customer_id = getattr(session, "customer", None)
    if not user_id or not sub_id:
        _log.warning("checkout.session.completed missing user_id or subscription")
        return
    stripe = _stripe()
    sub = stripe.Subscription.retrieve(str(sub_id))
    apply_subscription_to_user(
        user_id=user_id,
        subscription_id=str(sub.id),
        customer_id=str(customer_id) if customer_id else None,
        status=getattr(sub, "status", None),
        sub_obj=sub,
    )


def _on_subscription_updated(sub: Any) -> None:
    user_id = _resolve_user_id_from_subscription(sub)
    if not user_id:
        _log.warning("subscription.updated: could not resolve user_id")
        return
    apply_subscription_to_user(
        user_id=user_id,
        subscription_id=str(getattr(sub, "id", "") or ""),
        customer_id=str(getattr(sub, "customer", "") or "") or None,
        status=getattr(sub, "status", None),
        sub_obj=sub,
    )


def _on_subscription_deleted(sub: Any) -> None:
    user_id = _resolve_user_id_from_subscription(sub)
    if not user_id:
        return
    row = get_user_billing(user_id) or {}
    apply_subscription_to_user(
        user_id=user_id,
        subscription_id=None,
        customer_id=(row.get("stripe_customer_id") or str(getattr(sub, "customer", "") or "")) or None,
        status="canceled",
        period_end=None,
    )
