-- 0003 — market: when a market became tradeable. [1.3] #3
--
-- Adds one column:
--   published_at  the moment the status moved to 'open', null until it does
--
-- Nullable and never cleared. `status` remains the authority on what a market
-- is; this records when it became that. The two are written in one transaction
-- in service/market_service.publish, so they cannot disagree.
--
-- Note what is NOT here. [1.3] #3 also adds 'open' to MarketStatus, and that
-- needs no DDL at all: model/entities.py declares the column as a non-native
-- Enum, and SQLAlchemy has defaulted `create_constraint` to False since 1.4,
-- so `market.markets.status` is a plain varchar(24) with no CHECK to widen.
-- Verify before assuming it stayed that way:
--
--   SELECT conname, pg_get_constraintdef(oid)
--     FROM pg_constraint WHERE conrelid = 'market.markets'::regclass;
--
-- Apply to:  cs464   (the development database)
-- Not to:    cs464_test — unit_test/conftest.py drops and recreates the schema
--            from the models on every test, so a suite always matches
--            model/entities.py. It is only the long-lived database that drifts.
--
--   docker compose exec -T db psql -U cs464 -d cs464 -v ON_ERROR_STOP=1 \
--     -f /sql/migrations/0003-market-published-at.sql

ALTER TABLE market.markets
  ADD COLUMN IF NOT EXISTS published_at timestamptz;
