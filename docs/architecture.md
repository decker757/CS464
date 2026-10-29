# Architecture

Three diagrams — what the services are and what each one owns, what a trade
does, and what the tables look like, the last split into one ERD per schema.
They are a map of decisions written down elsewhere, not a second copy of them:
each names the ADR or `DECISIONS.md` entry it was drawn from, and where a
diagram and its source disagree, the source is right and the diagram is the
bug.

## The services and what they own

**Drawn from:** [ADR 0003](adr/0003-market-service-boundary.md) (the boundary
and the token), [ADR 0006](adr/0006-audit-log-write-path.md) (the audit grants),
[ADR 0010](adr/0010-realtime-price-broadcast.md) (a relay that owns nothing,
and Redis rather than the database),
[ADR 0017](adr/0017-the-ledger-and-a-stopped-market.md) and
[ADR 0022](adr/0022-the-ledger-and-a-suspended-trader.md) (the ledger's two
per-trade hops), [ADR 0019](adr/0019-settlement-across-services.md) (settlement
tells market_service afterwards),
[ADR 0021](adr/0021-the-leaderboard-and-the-first-service-credential.md) (the
leaderboard's service key), and `sql/01-roles.sql` with `sql/02-schemas.sql`
for the roles and grants themselves.

```mermaid
flowchart LR
    browser["Browser<br/>React, Vite, Tailwind"]

    subgraph svc["Backend services — FastAPI, each as its own Postgres role"]
        direction TB
        auth["auth :8000<br/>as auth_svc"]
        market["market :8001<br/>as market_svc"]
        ledger["ledger :8003<br/>as ledger_svc"]
        audit["audit :8002<br/>as audit_svc"]
        realtime["realtime :8004<br/>no role, no DATABASE_URL"]
    end

    subgraph pg["One Postgres instance — a schema per service, and no grants between them"]
        direction TB
        sAuth[("auth<br/>users<br/>refresh_tokens")]
        sMarket[("market<br/>markets<br/>market_outcomes<br/>resolution_sources")]
        sLedger[("ledger<br/>accounts, transactions, entries<br/>market_books, market_outcomes<br/>positions, orders, market_results<br/>leaderboard_generations, leaderboard_entries")]
        sAudit[("audit<br/>admin_actions<br/>owned by the superuser")]
    end

    redis[("Redis<br/>no persistence")]

    browser --> auth
    browser --> market
    browser --> ledger
    browser --> audit
    browser -->|"WebSocket /ws/prices"| realtime

    ledger -->|"GET /auth/me<br/>POST /internal/users/lookup"| auth
    ledger -->|"GET /public/markets/{id}<br/>POST /markets/{id}/settle"| market

    auth -->|owns| sAuth
    market -->|owns| sMarket
    ledger -->|owns| sLedger
    audit ==>|"the only SELECT"| sAudit

    auth -.->|"INSERT, no SELECT"| sAudit
    market -.->|"INSERT, no SELECT"| sAudit
    ledger -.->|"INSERT, no SELECT"| sAudit

    market --x|"cross-schema read: denied"| sAuth

    ledger -->|"PUBLISH market.price, order.update"| redis
    realtime -->|"SUBSCRIBE both channels"| redis
```

The arrows into Postgres are the whole of each role's access. The crossed one
stands for every pair not drawn: `market_svc` cannot read `auth`, `ledger_svc`
cannot read `market`, and so on, which is why `market.markets.creator_id` and
`ledger.accounts.owner_id` name rows they cannot reference. The arrow from
`realtime` to Redis points at what it subscribes to; the frames travel the
other way. What the drawing cannot say for itself:

- **`realtime` has no database at all** — no role in `sql/01-roles.sql`, no
  schema, no `DATABASE_URL`, and SQLAlchemy is absent from its
  `requirements.txt`. It is the only service a change under `sql/` cannot
  break, and ADR 0010 argues that is most of what made a fifth service
  affordable.
- **The audit log is written by the acting service**, on the request's own
  session and in the request's own transaction. There is no write endpoint on
  `:8002` and the dashed arrows are not calls to it (ADR 0006). `audit_svc`
  also holds INSERT, so its own suite can seed rows; nobody anywhere holds
  UPDATE, DELETE or TRUNCATE.
- **The three arrows out of the ledger carry two different credentials.**
  `/auth/me`, `/public/markets/{id}` and `/markets/{id}/settle` forward the
  caller's own access token unchanged; `/internal/users/lookup` presents
  `LEDGER_SERVICE_KEY`, which is the only service credential in the system and
  has one caller and one route (ADR 0021).
- **Nothing depends on `audit` or `realtime`**, and compose says so: neither is
  in any other service's `depends_on`. A trade survives both being down.
- Every image is built from `backend/` so it carries `backend/shared/` beside
  the service ([ADR 0012](adr/0012-the-shared-package.md)).

## A trade, end to end

The one path that crosses three services. The two upstream checks happen before
any lock is taken, and the price frame happens after the commit.

**Drawn from:** [ADR 0017](adr/0017-the-ledger-and-a-stopped-market.md) (the
replay-before-gate and gate-before-lock order),
[ADR 0022](adr/0022-the-ledger-and-a-suspended-trader.md) (the account check,
and its place in that order),
[ADR 0015](adr/0015-locking-the-read-that-decides-a-write.md) (the book lock
first, the key re-checked under it),
[ADR 0009](adr/0009-the-ledger-write-path.md) (one commit, legs that sum to
zero), [ADR 0010](adr/0010-realtime-price-broadcast.md) (publish after the
commit, and the version gate), and `DECISIONS.md` D-049 (a publish failure is
swallowed and logged) and "One price frame per commit, at the final
`state_version`". The code is
`ledger_service/service/trading.py::execute`.

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant L as ledger
    participant DB as Postgres, ledger schema
    participant A as auth
    participant M as market_service
    participant R as Redis
    participant RT as realtime

    B->>L: POST /ledger/markets/{id}/trades<br/>outcome, side, quantity, state_version, Idempotency-Key
    L->>DB: look the derived key up, no lock

    alt this key already names a committed transaction
        DB-->>L: that transaction
        L-->>B: 200, the stored result
        Note over L: A replay takes no lock, writes nothing and asks nobody,<br/>so a trade that was charged and whose response was lost<br/>still answers with what it did. ADR 0017
    else first time for this key
        L->>A: GET /auth/me, the trader's own token
        A-->>L: 200, or 403 account_suspended
        L->>M: GET /public/markets/{id}, the same token
        M-->>L: the derived status, the clock's close and an admin's in one field
        Note over L,M: Both before the book lock, one after the other, and both fail closed:<br/>403 account_suspended, 409 market_closed, 503 if either is unreachable.<br/>No lock here holds a row in another service still. ADR 0022, ADR 0017
        L->>DB: open and fund the book — once per market<br/>mint the starting grant — once per trader, ever
        L->>DB: SELECT FOR UPDATE on market_books, the wider lock first
        L->>DB: re-check the key under the lock, then the latch, q, b, state_version, the holding
        L->>L: trade_cost_of(q, b) in core/lmsr.py
        L->>DB: write q, the position, the transaction and its entries — pending
        Note over L,DB: COMMIT. posting.post_all is the one statement that commits,<br/>so everything above it is pending until here and rolls back together.<br/>ADR 0009, ADR 0015
        L->>R: PUBLISH market.price at the committed state_version
        Note over L,R: After the commit, never before it, and never on a refusal or a replay.<br/>bus.publish logs a failure and swallows it: the trade has happened and is correct.<br/>A crash in this window loses the frame and a reconnecting client<br/>recovers from GET /ledger/markets/{id}/snapshot. ADR 0010, D-049
        L-->>B: 200, the trade result
        R-->>RT: the price frame
        RT->>RT: drop anything not strictly newer for this market
        RT-->>B: price frame on /ws/prices
    end
```

Two things left off to keep it one path. A commit that moved the price may also
fill resting limit orders inside the same transaction, which adds `order.update`
frames after the price frame
([ADR 0024](adr/0024-resting-orders-fill-inside-the-trade.md)). And the preview
deliberately does not make either upstream call, because it decides nothing —
so a preview can quote a market whose trade then returns `409 market_closed`
(ADR 0017).

## The schemas

Each service builds its schema from its own Alembic history, run as a one-off
step before the app starts ([ADR 0020](adr/0020-migrations-by-alembic.md)).
`audit.admin_actions` is the exception: `sql/02-schemas.sql` creates it, because
no writer holds CREATE there.

Each entity below shows the columns that carry a decision, not every column.
`backend/<service>/model/entities.py` is the complete list, and it is where the
constraints and the reasoning live.

### `market`

**Drawn from:** [ADR 0011](adr/0011-market-auto-close.md) (`close_time` is the
authority and `status` is written down afterwards),
[ADR 0014](adr/0014-closing-a-market-early.md) (`closed_at` earlier than
`close_time` is how an early close is told apart),
[ADR 0013](adr/0013-proposing-an-outcome.md) and
[ADR 0016](adr/0016-deciding-a-proposal.md) (the proposal columns, and
`proposal_id` on a decision),
[ADR 0023](adr/0023-sending-an-approved-outcome-back.md)
(`dispute_window_ends_at` stored at approval),
[ADR 0025](adr/0025-voiding-a-flawed-market.md) (`void_reason` kept on the
row for traders, and no `voided_by`), and
[ADR 0003](adr/0003-market-service-boundary.md) (`creator_id` is not a foreign
key).

```mermaid
erDiagram
    markets {
        uuid id PK
        uuid creator_id "names auth.users, and cannot be an FK"
        uuid draft_key "unique with creator_id"
        varchar status "non-native enum, so a plain varchar with no CHECK"
        timestamptz close_time "trading stops the instant this passes"
        timestamptz closed_at "earlier than close_time means an admin stopped it"
        uuid proposal_id "one of seven proposal columns, all nulled on a reject"
        uuid approved_by_id "with approved_by_username and approved_at"
        timestamptz dispute_window_ends_at "approved_at plus the setting as it was then"
        timestamptz voided_at "with void_reason, set only when voided"
        numeric liquidity_b "b, handed to the ledger at first touch"
        numeric seed_subsidy
    }
    market_outcomes {
        uuid id PK
        uuid market_id FK
        int position "unique within the market"
        varchar label
    }
    resolution_sources {
        uuid id PK
        uuid market_id FK
        int position "unique within the market"
        varchar url
        varchar label
    }
    markets ||--o{ market_outcomes : "replaced wholesale on autosave"
    markets ||--o{ resolution_sources : "replaced wholesale on autosave"
```

Zero-or-more on both, not two-or-more: a draft may have no outcomes yet, and
the minimum is a submission rule rather than a constraint. Replacing either
collection needs its own flush, or the new rows and the orphans collide on the
position constraint within one flush — `_clear_children` is there for that.

### `ledger`

**Drawn from:** [ADR 0009](adr/0009-the-ledger-write-path.md) (no balance
column, signed entries summing to zero, the lazily minted grant),
[ADR 0005](adr/0005-trading-service-boundary.md) and
[ADR 0017](adr/0017-the-ledger-and-a-stopped-market.md) (`market_books` holds
snapshotted terms and carries no status or closing time),
[ADR 0018](adr/0018-positions-are-valued-at-liquidation.md) (what a position is
worth), [ADR 0019](adr/0019-settlement-across-services.md) (`market_results`,
one row per finished market),
[ADR 0021](adr/0021-the-leaderboard-and-the-first-service-credential.md) (the
leaderboard is a snapshot, with the username stored on it),
[ADR 0024](adr/0024-resting-orders-fill-inside-the-trade.md) (`orders`), and
[ADR 0025](adr/0025-voiding-a-flawed-market.md) (a void's `market_results` row,
with its `reason` and `voided_by`, and the `void_refund` and `void_residue`
kinds).

```mermaid
erDiagram
    accounts {
        uuid id PK
        varchar kind "user, platform or market_pool"
        uuid owner_id "unique with kind; names auth.users or a market, and cannot be an FK"
        timestamptz created_at
    }
    transactions {
        uuid id PK
        varchar kind "signup_grant, market_seed, trade_buy, trade_sell, settlement, settlement_residue, void_refund, void_residue"
        varchar idempotency_key "unique; derived by the server, never stored as sent"
        varchar request_fingerprint "over the legs, so a reused key naming different money is refused"
        timestamptz occurred_at
        jsonb context
    }
    entries {
        uuid id PK
        uuid transaction_id FK
        uuid account_id FK
        numeric amount "signed and never zero; a balance is SUM over an account"
        timestamptz created_at
    }
    market_books {
        uuid market_id PK "names market.markets, and cannot be an FK"
        numeric liquidity_b "b, snapshotted at first touch and never re-read"
        numeric seed_subsidy
        uuid pool_account_id FK
        bigint state_version "advanced in the same transaction as the trade"
        numeric credits_traded "every trade's credits, raised beside state_version"
        timestamptz state_changed_at
        timestamptz opened_at
    }
    market_outcomes {
        uuid market_id PK
        uuid outcome_id PK
        int position "unique within the market"
        numeric q "the LMSR share vector the price is computed from"
    }
    positions {
        uuid user_id PK
        uuid market_id PK
        uuid outcome_id PK
        numeric quantity "never negative; no shorting"
        numeric cost_basis
        timestamptz updated_at
    }
    market_results {
        uuid market_id PK "also the FK to market_books"
        varchar kind "settled or voided"
        uuid outcome_id "the winner, FK with market_id into market_outcomes; null on a void"
        timestamptz recorded_at
        text reason "a void's, set exactly when voided"
        uuid voided_by "the administrator who voided, set exactly when voided"
    }
    orders {
        uuid id PK
        bigint seq "the fill order within a market"
        uuid user_id
        uuid market_id FK
        uuid outcome_id FK
        varchar side
        numeric limit_price "strictly between 0 and 1"
        numeric quantity
        numeric filled_quantity
        numeric budget "set if and only if the side is buy"
        varchar status
        varchar idempotency_key "unique"
    }
    leaderboard_generations {
        uuid id PK
        timestamptz computed_at
        int ranked_users
    }
    leaderboard_entries {
        uuid generation_id FK "part of the primary key, with rank"
        int rank "part of the primary key"
        uuid user_id
        varchar username "stored, because the ledger cannot read auth.users"
        numeric net_worth
    }

    accounts ||--o{ entries : "balance is SUM(amount) over these"
    transactions ||--|{ entries : "legs that sum to zero"
    accounts ||--o| market_books : "its pool account"
    market_books ||--|{ market_outcomes : "the book's outcomes and their q"
    market_outcomes ||--o{ positions : "what a user holds"
    market_outcomes ||--o{ orders : "resting against"
    market_books ||--o| market_results : "one row per finished market"
    leaderboard_generations ||--o{ leaderboard_entries : "one generation's ranking"
```

Two absences are the point of this one.

**`accounts` has no balance column and must not grow one.** A balance is
`SUM(amount)` over that account's entries, derived on every read, which is what
makes three separate acceptance criteria true by construction instead of by
vigilance. The table exists to give an account an identity a foreign key can
point at and a single row the write path can lock. Credits are minted by the
one `PLATFORM` account going negative, so `SUM(amount)` over the whole of
`entries` is always exactly zero.

**`entries` has no UPDATE or DELETE path, and a trigger says so at the
database.** `model/entities.py` attaches it as an `after_create` event, so the
per-test schema rebuild gets it, and the Alembic baseline runs the same SQL for
real databases — editing one without the other changes only fresh databases.
`market_results` deliberately carries no such trigger, and `positions` cannot:
it is a rollup, updated on every later trade in the same market.

The other two schemas are small enough to read directly. `auth` is `users` and
`refresh_tokens` (`backend/auth_service/model/entities.py`); `audit` is the
single `admin_actions` table defined in `sql/02-schemas.sql`, whose columns and
grants that file explains at length. No relationship in any of the four crosses
a schema boundary, and the ids that look like they should —
`market.markets.creator_id`, `ledger.accounts.owner_id`,
`ledger.market_books.market_id` — are the cost ADR 0003 chose to pay.
