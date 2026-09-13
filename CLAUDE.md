# CS464

LMSR prediction market. Users trade shares in binary outcome markets against an
automated market maker, using mock credits.

Three people: Ernest and Ihsan on backend, Michelle on frontend. Planning lives
in GitHub Project v2 #6.

## Layout

```
backend/auth_service/   registration, login, logout, sessions   [A-1..A-3]
sql/                    roles, schemas and grants for the shared Postgres
docs/adr/               decisions that were expensive to make
scripts/                sprint digest to Telegram
.github/workflows/      path-filtered CI, one workflow per area
```

More services are coming: a ledger ([F-1] #41), an LMSR pricing engine
([F-3] #43), and a websocket server ([F-2] #42).

## Running things

```bash
cp .env.example .env          # fill in every blank; compose refuses to start otherwise
docker compose up --build     # http://localhost:8000/docs
```

```bash
cd backend/auth_service
.venv/bin/pytest              # needs `docker compose up -d db`
.venv/bin/pytest unit_test/core unit_test/model   # no database needed
```

## Things that will waste your time if you do not know them

**`DATABASE_URL` and `JWT_SECRET` are required and have no defaults.** That is
deliberate. A default database URL is a credential in the repo, and a default
signing key is a published key that would silently sign real sessions. The
service refuses to boot without them.

**Changing anything in `sql/` needs `docker compose down -v`.** Postgres runs
init scripts only on first initialisation of the data volume. Without the `-v`
your changes appear to do nothing. It destroys local data.

**Tests run against Postgres, not SQLite**, as `auth_svc` under production
grants. The two engines disagree about naive versus aware timestamps and about
functional unique indexes, and both differences have already caused bugs here.
Do not "simplify" this to SQLite.

**Never commit a credential, including in an example file.** GitGuardian runs
on every pull request and it is usually right. Use angle-bracket placeholders:
`postgresql+asyncpg://user:<PASSWORD>@host:<PORT>/db`. A real-looking username
beside a real-looking password will be flagged even when both are fake.

**A squash merge kills the branch.** Squash, merge commit and rebase are all
enabled on this repo; #65 was squashed. A squash rewrites the content into one
new commit with no shared ancestry, so pushing more work to that branch and
re-opening a pull request collides on every file. After any squash merge, start
a fresh branch from `dev`.

**A workflow skipped by a paths filter reports no status at all.** If you add
branch protection, do not mark one required, or pull requests touching the
other side will wait forever. Require the job, not the workflow. Also never
mark `notify` required; it only runs on failure by design.

## Backend conventions

Imports point one way only: `controller` may use `service`, `service` may use
`core` and `model`, and nothing below reaches back up. `main.py` is the only
file allowed to know about everything. That is why the error classes live in
`core` while the handler that turns them into HTTP responses lives in
`controller`.

Business rules belong in `service` and are tested there without HTTP. Status
codes, cookies and response shapes belong in `controller` and are tested there.
A new test that goes through a route to assert a business rule is in the wrong
layer.

Each service owns one Postgres schema and connects as its own role. There are
no cross grants, so a join across a service boundary fails with a permission
error rather than quietly working. Do not add a grant to make something
convenient; that is a decision to couple two services.

The auth service does not know that credits exist. There are tests that fail if
the word appears in a response or on the `register` signature. If you need a
balance, that is the ledger's job.

## Frontend and backend split

Most user stories are full stack. The story issue belongs to whoever owns the
dominant side; the minority side becomes a linked sub-issue tagged `[FE]` or
`[BE]`. Never assign backend-heavy work to Michelle.

The OpenAPI page at `/docs` is the contract between the two. Change a schema
and you have changed the contract.

## Branches

`dev` is where work lands. `staging` and `main` are promotion targets. Branch
from `dev`, name it `<issue>-<slug>`, open a pull request into `dev`.

Use `Refs #N`, not `Closes #N`, when a ticket has open sub-issues. Auto-closing
a parent whose frontend half is unbuilt hides work.

## Decisions already made

Do not relitigate these without reading them: `docs/adr/`.

- **0001** self-hosting auth rather than a managed provider
- **0002** cookies for browsers, bearer tokens for services

Two known constraints recorded there. Logout cannot revoke an already-issued
access token, so the 15-minute lifetime bounds the window. And a `SameSite=Lax`
cookie is not sent cross-site, so the frontend and API must share a registrable
domain or the scheme changes before [5.3] #19.
