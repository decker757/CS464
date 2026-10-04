-- 0002 — audit: the shared admin action log. [4.3] #15
--
-- Adds a role, a schema and a table, where 0001 added two columns. That is the
-- larger change, and it is here rather than left to `docker compose down -v`
-- because by now the development database holds drafted markets somebody would
-- rather not lose.
--
-- Unusually for this directory, almost nothing is written out below. Every
-- statement in sql/02-schemas.sql is already idempotent — CREATE ... IF NOT
-- EXISTS, ALTER ROLE, CREATE OR REPLACE, GRANT, REVOKE — so re-running that
-- file IS the migration, and copying its audit block here would leave two
-- definitions of one table to keep in step. The only thing it cannot do is
-- create a login role, because the password is not in the repository.
--
-- Apply to:  cs464   (the development database)
-- Not to:    cs464_test — it is rebuilt from sql/ on a fresh volume, and
--            each service's unit_test/conftest.py rebuilds its own schema per
--            test. If your cs464_test predates this file, run it there too.
--
--   docker compose exec -T db psql -U cs464 -d cs464 -v ON_ERROR_STOP=1 \
--     -v db_name=cs464 -v audit_password="$AUDIT_DB_PASSWORD" \
--     -f /sql/migrations/0002-audit-admin-actions.sql
--
-- AUDIT_DB_PASSWORD must match the value in the repo-root .env, because that
-- is what the audit service will connect with. Set it there first.

-- Roles are cluster-wide and CREATE ROLE has no IF NOT EXISTS, so this selects
-- the statement it needs and executes only when the role is genuinely absent.
-- \gexec rather than a DO block: psql does not interpolate :variables inside
-- dollar-quoted text, so the password would arrive in the role as the literal
-- string ":'audit_password'".
SELECT format('CREATE ROLE audit_svc LOGIN PASSWORD %L', :'audit_password')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audit_svc')
\gexec

-- Everything else: the audit schema, the table, its indexes, the append-only
-- trigger and the grants. Re-running this is safe and also repairs any earlier
-- schema that has drifted.
\i /sql/02-schemas.sql
