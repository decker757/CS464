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

-- Deliberately no GRANT between the schemas above. Adding one is a decision to
-- couple two services, so it should be a reviewed change, not a convenience.
--
-- [1.1] #1 is the first place this bites in earnest: market.markets stores a
-- creator_id that names a row in auth.users, and it cannot be a foreign key,
-- because market_svc cannot see that table. The market service learns who the
-- caller is from the signed access token instead. That is the intended cost,
-- not an oversight; docs/adr/0003-market-service-boundary.md has the argument.


-- ===========================================================================
-- audit — the one shared table, and the one deliberate exception. [4.3] #15
-- ===========================================================================
--
-- Read docs/adr/0006-audit-log-write-path.md before changing anything below.
--
-- Every other schema here is owned by the service that uses it. This one is
-- owned by the superuser and used by all of them, because an audit log is not
-- any single service's data: auth writes to it when an account is suspended,
-- market when a market is published or closed, ledger when payouts settle.
--
-- The grants are the whole design:
--
--   writers   INSERT only. They cannot SELECT, so no service can read another
--             service's actions, and the coupling the block above prevents is
--             still prevented. An append-only INSERT grant on a log is not a
--             join into someone else's business data.
--   audit_svc SELECT, to serve the read API, and INSERT so its own suite can
--             seed rows. It owns nothing, so it cannot ALTER or DROP either.
--   nobody    UPDATE, DELETE or TRUNCATE. Not the writers, not the reader.
--             This is what makes the log append-only: there is no path to
--             remove a row, rather than merely no endpoint that does it.
--
-- The table is created here rather than by a service's create_all, for two
-- reasons. No writer has CREATE on this schema, so none of them could make it.
-- And it is shared infrastructure: if one service owned its definition, that
-- service would have to be deployed before anyone else could be audited.

CREATE SCHEMA IF NOT EXISTS audit;
ALTER ROLE audit_svc IN DATABASE :"db_name" SET search_path = audit;

-- Note what this table does NOT have: no foreign keys, no CHECK constraints,
-- and only the columns a writer always knows are NOT NULL.
--
-- That is deliberate and it follows from the same-transaction write path. The
-- audit INSERT commits with the admin action it records, so any constraint
-- this table can violate is a way for the log to abort a legitimate admin
-- action. A CHECK listing the known action types would mean that deploying a
-- story which adds one, before the constraint is widened, breaks the feature
-- itself rather than just the logging. Refusing to record is worse than
-- recording something the reader does not recognise, so action_type is an
-- unconstrained string whose vocabulary lives in each service's code.
CREATE TABLE IF NOT EXISTS audit.admin_actions (
    id              uuid         PRIMARY KEY,

    -- Supplied by the writing service, not defaulted, so the value is the
    -- service's clock and a test can inject one. The default is a safety net
    -- for a row inserted by hand.
    occurred_at     timestamptz  NOT NULL DEFAULT now(),

    -- Taken from the signed access token, never from a request body.
    actor_id        uuid         NOT NULL,

    -- Snapshotted rather than joined. audit_svc has no grant on auth.users and
    -- never will, so a username resolved at read time is impossible here. It
    -- is also the more honest record: this is who they were when they acted,
    -- which survives a later rename, a demotion or a deleted account.
    actor_username  varchar(32)  NOT NULL,
    actor_role      varchar(16)  NOT NULL,

    action_type     varchar(64)  NOT NULL,

    -- target_id is nullable because not every action names one row, and it is
    -- not typed against any table: the target usually lives in a schema this
    -- one cannot see. target_label is the same snapshot argument as the
    -- username - the market's question, the suspended account's name - so the
    -- log reads on its own without a lookup nobody is allowed to perform.
    target_type     varchar(32)  NOT NULL,
    target_id       uuid,
    target_label    text,

    -- Required by some action types and meaningless for others; which is which
    -- is a rule in the writing service, not a constraint here. [2.3] #7 and
    -- [4.2] #14 both make an admin give one.
    reason          text,

    -- Whatever else that action type needs to be reconstructible later. jsonb
    -- rather than more columns because this table is append-only by design:
    -- every future story that records a different shape would otherwise be an
    -- ALTER on the one table in the database that is meant never to change.
    context         jsonb,

    -- Which service wrote the row. Provenance, and the filter you want when
    -- one service is suspected of writing something wrong.
    source_service  varchar(32)  NOT NULL
);

-- Reads are always newest-first and always bounded, and the API pages by
-- keyset on exactly these tuples, so each index covers one filter end to end.
-- id breaks ties: occurred_at alone is not unique and a cursor that cannot
-- distinguish two rows in the same microsecond either repeats or skips.
CREATE INDEX IF NOT EXISTS ix_admin_actions_feed
    ON audit.admin_actions (occurred_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_admin_actions_actor
    ON audit.admin_actions (actor_id, occurred_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_admin_actions_action_type
    ON audit.admin_actions (action_type, occurred_at DESC, id DESC);

-- Belt and braces over the grants below. A grant protects against an
-- application role; this protects against the table owner too, so a stray
-- UPDATE from a psql session that happens to be connected as the superuser
-- fails as loudly as one from a service would.
--
-- Statement-level rather than row-level, which is what lets one trigger cover
-- TRUNCATE alongside UPDATE and DELETE, and what makes it fire even when the
-- statement matches no rows.
CREATE OR REPLACE FUNCTION audit.reject_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        'audit.admin_actions is append-only; % is not permitted', TG_OP
        USING ERRCODE = 'restrict_violation',
              HINT = 'Correct a wrong entry by appending a new one that says so.';
END;
$$;

DROP TRIGGER IF EXISTS admin_actions_append_only ON audit.admin_actions;
CREATE TRIGGER admin_actions_append_only
    BEFORE UPDATE OR DELETE OR TRUNCATE ON audit.admin_actions
    FOR EACH STATEMENT EXECUTE FUNCTION audit.reject_mutation();

-- Writers append and cannot read. Adding SELECT here for any of them would let
-- one service read another's actions and is the line this file is about.
GRANT USAGE  ON SCHEMA audit TO auth_svc, ledger_svc, market_svc, audit_svc;
GRANT INSERT ON audit.admin_actions TO auth_svc, ledger_svc, market_svc;

-- The reader. INSERT as well, so its own suite can seed rows without a second
-- role's credentials; it exposes no route that writes.
GRANT SELECT, INSERT ON audit.admin_actions TO audit_svc;

-- Said explicitly rather than left implicit. These are never granted, and a
-- line that has to be deleted to break the guarantee is harder to break by
-- accident than a grant that was simply never written.
REVOKE UPDATE, DELETE, TRUNCATE ON audit.admin_actions
    FROM auth_svc, ledger_svc, market_svc, audit_svc;


-- Nothing should land in public by accident; a table there would belong to
-- nobody and be visible to everybody.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
