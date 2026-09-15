# Migrations

Changes to a schema that **already exists**. Applied by hand, not by the
Postgres entrypoint.

## Why these are not in `sql/`

`sql/00-init.sh` runs once, on first initialisation of the data volume, and it
names `01-roles.sql` and `02-schemas.sql` explicitly. A file added beside them
would never run on a database that already exists, which is the only situation
a migration is for. On a fresh volume it would be worse than useless: the
entrypoint runs before any service starts, so `market.markets` does not exist
yet and an `ALTER TABLE` against it fails.

Tables are created by SQLAlchemy's `create_all` at service startup. That only
ever issues `CREATE TABLE IF NOT EXISTS`, so it creates a missing table and
never touches one that is already there. A new column therefore reaches a fresh
database automatically and an existing one not at all, and the service then
fails every request with `column ... does not exist` on a model change that is
perfectly correct.

These files close that gap until [F-1] #41 brings Alembic, which is where this
directory is headed.

## Applying them

Against the development database, in filename order:

```bash
docker compose exec -T db psql -U cs464 -d cs464 -v ON_ERROR_STOP=1 \
  -f /sql/migrations/0001-market-lmsr-parameters.sql
```

`sql/` is already mounted at `/sql` in the db container, so no copying is
needed. Re-running is safe: every file here must be idempotent
(`IF NOT EXISTS`), because nothing records which have been applied.

The **test** databases need nothing. `unit_test/conftest.py` drops and
recreates the schema from the models on every test, so a suite always matches
`model/entities.py`. It is only the long-lived `cs464` database that drifts.

## Adding one

Name it `NNNN-<service>-<what-changed>.sql`, make every statement idempotent,
say which ticket it came from, and state which databases it belongs on.
