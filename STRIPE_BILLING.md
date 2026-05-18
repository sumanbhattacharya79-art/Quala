# Stripe billing (Checkout + entitlements)

## Setup

1. **Stripe Dashboard** → Product **Quala Basic** with recurring prices:
   - $2/month → `STRIPE_PRICE_BASIC_MONTHLY`
   - $20/year → `STRIPE_PRICE_BASIC_YEARLY`

2. **Webhook** → `POST https://YOUR-CLOUD-RUN-URL/api/billing/webhook`  
   Events: `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`  
   Signing secret → `STRIPE_WEBHOOK_SECRET`

3. **Supabase** (or local SQLite via `db.py` migrations):

   ```bash
   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/005_stripe_billing.sql
   ```

4. **Cloud Run** env vars:

   ```bash
   STRIPE_SECRET_KEY=sk_test_...
   STRIPE_WEBHOOK_SECRET=whsec_...
   STRIPE_PRICE_BASIC_MONTHLY=price_...
   STRIPE_PRICE_BASIC_YEARLY=price_...
   STRIPE_CHECKOUT_SUCCESS_URL=https://quala-snowy.vercel.app/?billing=success
   STRIPE_CHECKOUT_CANCEL_URL=https://quala-snowy.vercel.app/?billing=cancel
   STRIPE_PORTAL_RETURN_URL=https://quala-snowy.vercel.app/
   ```

5. Redeploy **quala-api** after adding `stripe` to the image.

## Local webhook test

```bash
stripe listen --forward-to localhost:8000/api/billing/webhook
```

Use test card `4242 4242 4242 4242`.

## API

| Route | Purpose |
|-------|---------|
| `GET /api/billing/status?user_id=` | Plan tier for UI |
| `POST /api/billing/checkout-session` | `{ user_id, billing_interval: "monthly" \| "yearly" }` → `{ url }` |
| `POST /api/billing/portal-session` | `{ user_id }` → Stripe Customer Portal |
| `POST /api/billing/webhook` | Stripe-signed events (raw body) |

## Entitlements (HTTP 402)

| Feature | Free | Basic |
|---------|------|--------|
| Saved portfolios | 1 | 5 |
| Scenarios | 0 | 5 |
| Life plans | 0 | 1 |
| Mr Brown | no | yes |

Enforced in `app/backend/entitlements.py` on save portfolio/scenario/life and Mr Brown chat.
