-- 0001 — market: LMSR pricing parameters. [1.2] #2
--
-- Adds the two terms an administrator sets at creation:
--   liquidity_b   the LMSR liquidity parameter, which fixes the platform's
--                 worst-case loss at b*ln(n)
--   seed_subsidy  the mock credits put up to cover that loss
--
-- Both nullable, matching model/entities.py, because a draft is allowed to be
-- incomplete and the three-second autosave must never fail on a half-typed
-- form. Completeness is enforced at submission in service/validation.py.
--
-- Numeric(18, 4) rather than double precision: the subsidy is money and will
-- share arithmetic with the ledger's entries in [F-1] #41. model/schemas.py
-- bounds the request to exactly this range, so an out-of-range value is a 422
-- rather than a numeric overflow surfacing as a 500.
--
-- Apply to:  cs464   (the development database)
-- Not to:    cs464_test — unit_test/conftest.py rebuilds the schema per test.
--
--   docker compose exec -T db psql -U cs464 -d cs464 -v ON_ERROR_STOP=1 \
--     -f /sql/migrations/0001-market-lmsr-parameters.sql

ALTER TABLE market.markets
  ADD COLUMN IF NOT EXISTS liquidity_b  numeric(18, 4),
  ADD COLUMN IF NOT EXISTS seed_subsidy numeric(18, 4);
