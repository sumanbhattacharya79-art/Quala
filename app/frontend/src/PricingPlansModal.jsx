import { useRef, useState } from "react";
import { postJson } from "./api.js";

export function PricingPlansModalBody({ userId, billing, onRequireLogin }) {
  const basicPlanCardRef = useRef(null);
  const [checkoutLoading, setCheckoutLoading] = useState(null);
  const [billingError, setBillingError] = useState("");
  const hasBasic = Boolean(billing?.has_basic);
  const billingReady = Boolean(billing?.billing_configured);

  const scrollToBasic = () => {
    basicPlanCardRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    basicPlanCardRef.current?.focus();
  };

  const startCheckout = async (interval) => {
    setBillingError("");
    if (!userId) {
      onRequireLogin?.();
      return;
    }
    if (!billingReady) {
      setBillingError("Checkout is not configured yet. Try again later.");
      return;
    }
    setCheckoutLoading(interval);
    try {
      const res = await postJson("/api/billing/checkout-session", {
        user_id: userId,
        billing_interval: interval,
      });
      if (res?.url) window.location.href = res.url;
      else setBillingError("Could not start checkout.");
    } catch (err) {
      setBillingError(err?.message || "Checkout failed.");
    } finally {
      setCheckoutLoading(null);
    }
  };

  const openPortal = async () => {
    setBillingError("");
    if (!userId) {
      onRequireLogin?.();
      return;
    }
    setCheckoutLoading("portal");
    try {
      const res = await postJson("/api/billing/portal-session", { user_id: userId });
      if (res?.url) window.location.href = res.url;
    } catch (err) {
      setBillingError(err?.message || "Could not open billing portal.");
    } finally {
      setCheckoutLoading(null);
    }
  };

  return (
    <div className="pricing-plans-modal">
      {hasBasic ? (
        <p className="pricing-plan-active-banner" role="status">
          You are on <strong>Basic</strong>.
          {billing?.plan_period_end
            ? ` Renews through ${String(billing.plan_period_end).slice(0, 10)}.`
            : null}
        </p>
      ) : null}
      {billingError ? (
        <p className="pricing-billing-error" role="alert">
          {billingError}
        </p>
      ) : null}
      <div className="pricing-plans-grid">
        <div className="pricing-plan-card">
          <h4 className="pricing-plan-name">Free</h4>
          <p className="pricing-plan-tag">Get started at no cost.</p>
          <ul className="pricing-feature-list" aria-label="Free plan features">
            <li className="pricing-feature">
              <span className="pricing-icon pricing-icon--ok" aria-hidden>
                ✓
              </span>
              <span>Create and save portfolio (one)</span>
            </li>
            <li className="pricing-feature">
              <span className="pricing-icon pricing-icon--ok" aria-hidden>
                ✓
              </span>
              <span>Net worth monitoring</span>
            </li>
            <li className="pricing-feature">
              <span className="pricing-icon pricing-icon--ok" aria-hidden>
                ✓
              </span>
              <span>Limited AI assistance</span>
            </li>
            <li className="pricing-feature">
              <span className="pricing-icon pricing-icon--no" aria-hidden>
                ✗
              </span>
              <span>Multiple saved portfolios and scenarios (up to 5)</span>
            </li>
            <li className="pricing-feature">
              <span className="pricing-icon pricing-icon--no" aria-hidden>
                ✗
              </span>
              <span>Lifeplans</span>
            </li>
            <li className="pricing-feature">
              <span className="pricing-icon pricing-icon--no" aria-hidden>
                ✗
              </span>
              <span>Scenario planning</span>
            </li>
            <li className="pricing-feature">
              <span className="pricing-icon pricing-icon--no" aria-hidden>
                ✗
              </span>
              <span>Portfolio Rebalancing suggestions</span>
            </li>
            <li className="pricing-feature">
              <span className="pricing-icon pricing-icon--no" aria-hidden>
                ✗
              </span>
              <span>Full AI assistance</span>
            </li>
          </ul>
          {!hasBasic ? (
            <button type="button" className="login-submit-btn pricing-upgrade-btn" onClick={scrollToBasic}>
              See Basic plan
            </button>
          ) : null}
        </div>
        <div
          className="pricing-plan-card pricing-plan-card--basic"
          ref={basicPlanCardRef}
          tabIndex={-1}
        >
          <h4 className="pricing-plan-name">Basic</h4>
          <p className="pricing-plan-price">$2/month or $20/year</p>
          <ul className="pricing-feature-list" aria-label="Basic plan includes">
            <li className="pricing-feature">
              <span className="pricing-icon pricing-icon--ok" aria-hidden>
                ✓
              </span>
              <span>Create and save portfolios and scenarios (up to 5)</span>
            </li>
            <li className="pricing-feature">
              <span className="pricing-icon pricing-icon--ok" aria-hidden>
                ✓
              </span>
              <span>Net worth monitoring</span>
            </li>
            <li className="pricing-feature">
              <span className="pricing-icon pricing-icon--ok" aria-hidden>
                ✓
              </span>
              <span>Lifeplans</span>
            </li>
            <li className="pricing-feature">
              <span className="pricing-icon pricing-icon--ok" aria-hidden>
                ✓
              </span>
              <span>Scenario planning</span>
            </li>
            <li className="pricing-feature">
              <span className="pricing-icon pricing-icon--ok" aria-hidden>
                ✓
              </span>
              <span>Portfolio Rebalancing suggestions</span>
            </li>
            <li className="pricing-feature">
              <span className="pricing-icon pricing-icon--ok" aria-hidden>
                ✓
              </span>
              <span>AI assistance</span>
            </li>
          </ul>
          {hasBasic ? (
            <button
              type="button"
              className="login-submit-btn pricing-upgrade-btn"
              disabled={checkoutLoading === "portal"}
              onClick={openPortal}
            >
              {checkoutLoading === "portal" ? "Opening…" : "Manage subscription"}
            </button>
          ) : (
            <div className="pricing-checkout-actions">
              <button
                type="button"
                className="login-submit-btn pricing-upgrade-btn"
                disabled={Boolean(checkoutLoading)}
                onClick={() => startCheckout("monthly")}
              >
                {checkoutLoading === "monthly" ? "Redirecting…" : "Subscribe — $2/month"}
              </button>
              <button
                type="button"
                className="login-cancel-btn pricing-yearly-btn"
                disabled={Boolean(checkoutLoading)}
                onClick={() => startCheckout("yearly")}
              >
                {checkoutLoading === "yearly" ? "Redirecting…" : "Subscribe — $20/year"}
              </button>
            </div>
          )}
        </div>
      </div>
      <p className="pricing-plans-footnote">
        Free and Basic do not connect to banks or brokerages—you add or upload holdings and keep them current.
      </p>
    </div>
  );
}
