-- Cluster-wide login roles, one per service. Runs once per data volume.
--
-- Passwords arrive as psql variables from 00-init.sh, which reads them from
-- the environment. Nothing here is a credential.

CREATE ROLE auth_svc   LOGIN PASSWORD :'auth_password';
CREATE ROLE ledger_svc LOGIN PASSWORD :'ledger_password';

-- Add a role here when a new service lands, then give it a schema in
-- 02-schemas.sql. [F-1] #41 already has ledger_svc waiting.
