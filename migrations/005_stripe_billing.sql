-- Stripe subscriptions: plan tier on "user"
ALTER TABLE "user"
  ADD COLUMN IF NOT EXISTS plan_tier TEXT NOT NULL DEFAULT 'free',
  ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT,
  ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT,
  ADD COLUMN IF NOT EXISTS subscription_status TEXT,
  ADD COLUMN IF NOT EXISTS plan_period_end TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS idx_user_stripe_customer
  ON "user" (stripe_customer_id)
  WHERE stripe_customer_id IS NOT NULL;
