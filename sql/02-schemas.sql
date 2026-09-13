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

-- Each role lands in its own schema by default, so unqualified table names in
-- application code resolve correctly without a search_path in every session.
ALTER ROLE auth_svc   IN DATABASE :"db_name" SET search_path = auth;
ALTER ROLE ledger_svc IN DATABASE :"db_name" SET search_path = ledger;

-- Deliberately no GRANT between the two schemas. Adding one is a decision to
-- couple the services, so it should be a reviewed change, not a convenience.

-- Nothing should land in public by accident; a table there would belong to
-- nobody and be visible to everybody.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
