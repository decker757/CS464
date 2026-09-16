-- 0005 — market: the proposed outcome and its evidence. [3.1] #9
--
-- Adds six columns:
--   proposed_outcome_id      which outcome was named as the winner
--   proposed_by_id           who proposed it, as an auth.users id
--   proposed_by_username     who they were at the time, snapshotted
--   proposed_at              when
--   proposal_evidence_url    where it can be verified
--   proposal_evidence_note   why, in prose
--
-- All six are null together or set together: backend/market_service's
-- service/market_service.py::propose_outcome writes them in the one
-- transaction that sets status = 'pending_resolution', so a market that is
-- pending resolution always carries a complete proposal and one that is not
-- never carries a partial one.
--
-- `proposed_outcome_id` is deliberately NOT a foreign key to
-- market.market_outcomes(id), and this is the interesting line in the file.
-- The constraint worth having is that the outcome belongs to *this* market,
-- and a plain FK cannot say that: outcome ids are unique across every market,
-- so it would accept another market's "Yes". service/validation.py has to
-- check the real rule regardless, where it is a field-addressed 422 rather
-- than an IntegrityError surfacing as a 500. Given that check, an FK would buy
-- nothing but a dependency cycle between two tables that create_all cannot
-- sort without use_alter. See docs/adr/0013-proposing-an-outcome.md for the
-- composite-key version that would genuinely enforce it, and why it is not
-- worth its second unique index yet.
--
-- `proposed_by_username` is a snapshot for the same reason the audit log's
-- actor_username is one: this service holds no grant on the auth schema and
-- cannot resolve an id to a name, now or ever (ADR 0003), so a name not
-- written here is a name nobody can render later. varchar(32) matches
-- audit.admin_actions.actor_username and the auth service's own column.
--
-- Note what is NOT here. [3.1] #9 also adds 'pending_resolution' to
-- MarketStatus, and that needs no DDL: model/entities.py declares the column
-- as a non-native Enum and SQLAlchemy has defaulted `create_constraint` to
-- False since 1.4, so market.markets.status is a plain varchar(24) with no
-- CHECK to widen. Verify rather than assume:
--
--   SELECT conname, pg_get_constraintdef(oid)
--     FROM pg_constraint WHERE conrelid = 'market.markets'::regclass;
--
-- ix_markets_due_close is untouched and stays correct. Its predicate is
-- status = 'open', and a market reaches 'pending_resolution' only from
-- 'closed', so nothing added here enters or leaves that partial index.
--
-- Apply to:  cs464   (the development database)
-- Not to:    cs464_test — unit_test/conftest.py drops and recreates the schema
--            from the models on every test, so a suite always matches
--            model/entities.py. It is only the long-lived database that drifts.
--
--   docker compose exec -T db psql -U cs464 -d cs464 -v ON_ERROR_STOP=1 \
--     -f /sql/migrations/0005-market-outcome-proposal.sql

ALTER TABLE market.markets
  ADD COLUMN IF NOT EXISTS proposed_outcome_id     uuid,
  ADD COLUMN IF NOT EXISTS proposed_by_id          uuid,
  ADD COLUMN IF NOT EXISTS proposed_by_username    varchar(32),
  ADD COLUMN IF NOT EXISTS proposed_at             timestamptz,
  ADD COLUMN IF NOT EXISTS proposal_evidence_url   varchar(2048),
  ADD COLUMN IF NOT EXISTS proposal_evidence_note  text;
