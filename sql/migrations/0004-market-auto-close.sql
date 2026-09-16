-- 0004 — market: automatic close at closing time. [F-4] #44
--
-- Adds one column and one index:
--   closed_at            when trading was recorded as having stopped
--   ix_markets_due_close the sweeper's working set
--
-- `closed_at` is not `close_time`. `close_time` is the promise made to traders
-- and the instant trading really ended; `closed_at` is when the background
-- sweeper in backend/market_service/service/sweeper.py got around to writing
-- the status down, a few seconds later. A gap between them is normal and is
-- not a market that stayed open — nothing could trade in it either way,
-- because the trade path derives that answer from `close_time` rather than
-- from `status`. See backend/market_service/service/closing.py.
--
-- The index is PARTIAL and keyed on close_time, which is the performance
-- argument for sweeping at all. It holds only the markets still open rather
-- than every market ever created, so it stays small as settled markets
-- accumulate, and it supplies the sweep's ORDER BY close_time LIMIT n as an
-- ordered scan that stops at n instead of reading every open market and
-- sorting. Measured on 20,000 settled and 2,200 open markets, 200 of them due:
-- 5 index buffers and 0.05 ms with it, 36 buffers and 0.17 ms without.
--
-- ix_market_markets_status does not make it redundant: that one is keyed on
-- status alone, so it finds the open markets and then has to sort them.
--
-- Its predicate must stay literally identical to the one declared in
-- model/entities.py, or Postgres builds a second index rather than matching
-- the existing one.
--
-- Not CONCURRENTLY. This table is small and psql would need the statement
-- outside a transaction block; on a table where that mattered, the
-- CONCURRENTLY form is what you would want here instead.
--
-- Note what is NOT here. [F-4] #44 also adds 'closed' to MarketStatus, and
-- that needs no DDL: model/entities.py declares the column as a non-native
-- Enum and SQLAlchemy has defaulted `create_constraint` to False since 1.4, so
-- `market.markets.status` is a plain varchar(24) with no CHECK to widen.
-- Verify rather than assume:
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
--     -f /sql/migrations/0004-market-auto-close.sql

ALTER TABLE market.markets
  ADD COLUMN IF NOT EXISTS closed_at timestamptz;

CREATE INDEX IF NOT EXISTS ix_markets_due_close
  ON market.markets (close_time) WHERE status = 'open';
