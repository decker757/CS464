-- [5.3] #19. The production twin of sql/01-roles.sql: the four service login
-- roles, created if missing, with their passwords reset to the deploy's
-- values every time. Idempotent, unlike 01-roles.sql, because this runs on
-- every deploy rather than once on an empty volume. ensure-database.sh runs
-- it, then sql/02-schemas.sql, in one psql session.

\getenv auth_password AUTH_DB_PASSWORD
\getenv ledger_password LEDGER_DB_PASSWORD
\getenv market_password MARKET_DB_PASSWORD
\getenv audit_password AUDIT_DB_PASSWORD

-- 02-schemas.sql reads this for its per-database role settings.
SELECT current_database() AS db_name \gset

SELECT format('CREATE ROLE %I LOGIN', role_name)
  FROM unnest(ARRAY['auth_svc', 'ledger_svc', 'market_svc', 'audit_svc']) AS role_name
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name)
\gexec

ALTER ROLE auth_svc   PASSWORD :'auth_password';
ALTER ROLE ledger_svc PASSWORD :'ledger_password';
ALTER ROLE market_svc PASSWORD :'market_password';
ALTER ROLE audit_svc  PASSWORD :'audit_password';

-- A managed database's admin is not a superuser. 02-schemas.sql gives each
-- service its own schema (CREATE SCHEMA ... AUTHORIZATION auth_svc), and only
-- a member of a role may make it an owner. A no-op when already a member.
GRANT auth_svc, ledger_svc, market_svc TO CURRENT_USER;
