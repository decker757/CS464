# sql/migrations

One file, and it is not a schema migration.

`0002-audit-admin-actions.sql` creates the `audit_svc` login role and re-runs
`sql/02-schemas.sql`, for a database that predates [4.3] #15. Roles are
cluster-wide and the password is not in the repository. The audit table, its
trigger and every grant belong to the superuser rather than to any service, so
no service's migration history can own them (ADR 0006, ADR 0020). Apply it by
hand, to `cs464` and to `cs464_test` if yours predates it:

```bash
docker compose exec -T db psql -U cs464 -d cs464 -v ON_ERROR_STOP=1 \
  -v db_name=cs464 -v audit_password="$AUDIT_DB_PASSWORD" \
  -f /sql/migrations/0002-audit-admin-actions.sql
```

`AUDIT_DB_PASSWORD` must match the value in the repo-root `.env`. For the test
database, change both `cs464`s after `-d` and `db_name=` to `cs464_test`.

## Where schema changes go now

Each service that owns a schema (auth, market, ledger) keeps its history in
`backend/<service>/migrations/`, and compose runs `<service>-migrate` before
the service starts. A model change needs a revision. Root `CLAUDE.md` has the
commands, and ADR 0020 has the decision.

## If a migrate step refuses your database

`0001` and `0003` to `0007` were hand-applied ALTERs that nothing recorded.
[F-5] #75 folded them into the baselines and deleted them. A development
database from before #75 that missed one is refused by its migrate step, which
names the missing column, index or type and changes nothing. Read the refusal
with `docker compose logs market-migrate` (or `auth-migrate`, `ledger-migrate`).

The files are still in git history. From the repository root, list them under
the commit that deleted them:

```bash
git log --diff-filter=D --name-only --format=%h -- sql/migrations/
```

Pick the file whose name matches what the refusal says is missing. Apply it,
with that commit's id in place of `<commit>` and the file's name in place of
`<file>`:

```bash
git show <commit>^:sql/migrations/<file> \
  | docker compose exec -T db psql -U cs464 -d cs464 -v ON_ERROR_STOP=1
```

Then run `docker compose up` again. Every one of those files is idempotent, so
applying one twice does no harm.

That works only while each service's baseline is its newest revision. After
that, the answer is `docker compose down -v`, which destroys local data.

## Why this is not in `sql/` itself

`sql/00-init.sh` runs once, on first initialisation of the data volume, and it
names `01-roles.sql` and `02-schemas.sql` explicitly. A file added beside them
would never run on a database that already exists, which is the only situation
a file here is for.
