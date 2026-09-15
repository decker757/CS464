"""Domain errors.

Plain exceptions with no framework imports, so the service layer can raise them
without knowing HTTP exists. `controller/errors.py` owns the mapping from these
to status codes.

Three of these have no route that can raise them yet. `InsufficientFunds`,
`IdempotencyKeyReused` and `UnbalancedTransaction` come out of the write path
in `service/ledger_service.py`, which has no HTTP surface until [T-2] #22 adds
one. They carry their status codes now so that the endpoint, when it arrives,
is a route and a docstring rather than a second opinion about what these mean.
"""

from __future__ import annotations


class LedgerError(Exception):
    """Base class for every ledger domain error."""

    status_code: int = 400
    code: str = "ledger_error"
    message: str = "Ledger error."


class NotAuthenticated(LedgerError):
    """Missing, malformed or expired access token."""

    status_code = 401
    code = "invalid_token"
    message = "Not authenticated."


class NotAnAdministrator(LedgerError):
    """Authenticated, but not carrying the admin role.

    Kept distinct from 401 for the same reason as every other service here:
    401 means the session is gone and a fresh login helps, 403 means the
    session is fine and this account will never be allowed in.
    """

    status_code = 403
    code = "not_an_administrator"
    message = "This action requires an administrator account."


class MalformedCursor(LedgerError):
    """The `cursor` parameter did not come from a previous response.

    Its contents are this service's business, so a client should only ever echo
    back what `next_cursor` gave it. Saying so explicitly beats silently
    restarting from the newest page, which would loop forever.
    """

    status_code = 400
    code = "malformed_cursor"
    message = "Pass back the `next_cursor` from the previous response, unmodified."


class InsufficientFunds(LedgerError):
    """The transaction would take an account below zero. [T-2] #22.

    409 rather than 422: nothing about the request is malformed, and the same
    request may well succeed later or have succeeded a moment ago. It is the
    state of the account that refuses it.

    Carries the balance and the shortfall, because the caller is a trading
    service that has to tell somebody why their trade did not go through, and
    "insufficient funds" on its own is the least useful true sentence available.
    """

    status_code = 409
    code = "insufficient_funds"

    def __init__(self, *, balance: object, required: object) -> None:
        self.balance = balance
        self.required = required
        self.message = (
            f"This account holds {balance} credits and the transaction needs "
            f"{required}."
        )
        super().__init__(self.message)


class IdempotencyKeyReused(LedgerError):
    """One key, two different transactions.

    A replay of the same request returns the original transaction and writes
    nothing; that is the whole point of the key. This is the other case: the
    same key arriving with *different* money in it. Answering that with the
    original transaction would tell a caller their trade succeeded when what
    actually happened was somebody else's trade, so it is refused instead.

    409 for the same reason as InsufficientFunds: the request is well formed
    and the conflict is with what is already recorded.
    """

    status_code = 409
    code = "idempotency_key_reused"
    message = (
        "This idempotency key was already used for a different transaction. "
        "Generate a new key for a new transaction, and resend the identical "
        "request to retry an old one."
    )


class UnbalancedTransaction(LedgerError):
    """The legs do not sum to zero, so this is not a movement of credits.

    Double entry is the invariant this service exists to hold: credits move
    between accounts and are never created except by the platform account
    going negative by the same amount. A caller that submits legs which do not
    balance has a bug, not a bad request, and refusing it is what stops that
    bug from becoming an unreconcilable ledger.

    422 because the body is the problem, and it is the caller's to fix.
    """

    status_code = 422
    code = "unbalanced_transaction"
    message = "The debits and credits of a transaction must sum to zero."
