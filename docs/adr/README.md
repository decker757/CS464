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

Supersede rather than edit. If a decision changes, add a new record and mark
the old one superseded, so the reasoning trail survives.
