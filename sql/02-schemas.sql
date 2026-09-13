-- Schema per service, applied to every database (development and test).
--
-- One Postgres instance keeps the cost down. The boundary between services is
-- enforced by ownership and the absence of cross grants, not by convention:
-- auth_svc cannot read ledger.*, ledger_svc cannot read auth.*. A join across
-- the boundary fails with a permission error while someone is writing it,
-- rather than succeeding quietly and welding the two services together so they
-- can never be separated.

CREATE SCHEMA IF NOT EXISTS auth   AUTHORIZATION auth_svc;
CREATE SCHEMA IF NOT EXISTS ledger AUTHORIZATION ledger_svc;
CREATE SCHEMA IF NOT EXISTS market AUTHORIZATION market_svc;

-- Each role lands in its own schema by default, so unqualified table names in
-- application code resolve correctly without a search_path in every session.
ALTER ROLE auth_svc   IN DATABASE :"db_name" SET search_path = auth;
ALTER ROLE ledger_svc IN DATABASE :"db_name" SET search_path = ledger;
ALTER ROLE market_svc IN DATABASE :"db_name" SET search_path = market;

-- Deliberately no GRANT between the schemas. Adding one is a decision to
-- couple two services, so it should be a reviewed change, not a convenience.
--
-- [1.1] #1 is the first place this bites in earnest: market.markets stores a
-- creator_id that names a row in auth.users, and it cannot be a foreign key,
-- because market_svc cannot see that table. The market service learns who the
-- caller is from the signed access token instead. That is the intended cost,
-- not an oversight; docs/adr/0003-market-service-boundary.md has the argument.

-- Nothing should land in public by accident; a table there would belong to
-- nobody and be visible to everybody.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
