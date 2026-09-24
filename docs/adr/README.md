# Architecture decision records

One file per decision that was expensive to make and would be expensive to
reverse. Written so someone joining the project, or one of us in week 10, can
see why the code looks the way it does without reconstructing the argument.

| # | Decision | Status |
| --- | --- | --- |
| [0001](0001-self-host-authentication.md) | Self-host authentication instead of using a managed provider | Accepted |
| [0002](0002-auth-token-transport.md) | Cookies for the browser, bearer tokens for services | Accepted |
| [0003](0003-market-service-boundary.md) | A separate market service, and admin authority carried in the token | Accepted |
| [0004](0004-draft-autosave-and-submission.md) | One idempotent endpoint for both autosave and submission | Accepted |
| [0005](0005-trading-service-boundary.md) | A composite trading service, and positions with the ledger | Accepted |
| [0006](0006-audit-log-write-path.md) | One shared audit log, written in the acting service's transaction | Accepted |
| [0007](0007-admin-tiers-and-role-changes.md) | A flat admin tier, and role changes audited rather than approved | Accepted |
| [0008](0008-publishing-a-market.md) | Publishing is its own endpoint, from SUBMITTED only, and one way | Accepted |
| [0009](0009-the-ledger-write-path.md) | Double-entry with derived balances, and a lazily minted grant | Accepted |
| [0010](0010-realtime-price-broadcast.md) | A relay that owns nothing, and a bus that is not the database | Accepted |
| [0011](0011-market-auto-close.md) | The clock closes a market, and a sweep only writes it down | Accepted |
| [0012](0012-the-shared-package.md) | A shared package, and the build contexts that had to move first | Accepted |
| [0013](0013-proposing-an-outcome.md) | Proposing an outcome, from CLOSED only, with evidence, and one at a time | Accepted |
| [0014](0014-closing-a-market-early.md) | Closing a market early, by any administrator, with the reason in the log | Accepted |
| [0015](0015-locking-the-read-that-decides-a-write.md) | Every read that decides a write is locked, and the wider lock goes first | Accepted |
| [0016](0016-deciding-a-proposal.md) | Deciding a proposal, by any administrator but the proposer, with APPROVED as a status | Accepted |
| [0017](0017-the-ledger-and-a-stopped-market.md) | The ledger asks market_service whether a market is still trading, once per trade | Accepted |

Supersede rather than edit. If a decision changes, add a new record and mark
the old one superseded, so the reasoning trail survives.
