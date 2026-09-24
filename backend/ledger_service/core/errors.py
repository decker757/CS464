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


class MarketTermsUnavailable(LedgerError):
    """market_service could not be reached, or answered as if it were down.

    Wider than its name now suggests: this covers the market's *terms* — its
    `liquidity_b`, `seed_subsidy` and outcomes, read once at first touch — and,
    since ADR 0017, the ledger's attempt to learn whether a market is still
    *open*, read on every trade. Both are the same dependency and the same
    failure mode, so they share one code: a caller cannot act differently on
    "the terms could not be read" versus "the status could not be read", and
    the principle behind this service's error codes is that a caller can act
    differently on each one it has.

    D-030 covers a refused connection, a timeout, a 5xx, and a 200 whose body
    is not a market — the market service is treated as down rather than the
    ledger crashing on a parse error it cannot recover from. Also raised for a
    published market whose `liquidity_b` or `seed_subsidy` arrived null, which
    `service/validation.py` on the other side should never produce: refusing
    beats writing a book with a `b` that can never be priced. ADR 0017 adds a
    404 for a market that already holds a book — that market was published,
    so a 404 at that point is market_service answering incorrectly rather
    than a market that is gone — and a `status` field that is missing or not
    a string, because a sick dependency must never be read as a closed
    market.

    503 because the request was fine and the dependency was not, and because a
    trade that failed this way is worth retrying in a moment.
    """

    status_code = 503
    code = "market_terms_unavailable"
    message = "Could not read this market's terms right now. Try again shortly."


class MarketClosed(LedgerError):
    """This market is not open for trading. [F-8] #109, ADR 0017.

    Read off market_service's public detail endpoint, whose `status` is
    already ADR 0011's derived predicate — the clock's close and an
    administrator's early one both arrive through this one field, so this is
    a comparison against `"open"` rather than a list of the statuses that
    happened to exist when it was written.

    409 for `InsufficientFunds`'s reason: the request is well formed and it is
    the state that refuses it — the same trade would have succeeded an hour
    ago and may never succeed again. Spelled `market_closed` to match
    market_service's own code, so a frontend error handler built for one
    serves both.
    """

    status_code = 409
    code = "market_closed"
    message = "This market is not open for trading."


class MarketNotFound(LedgerError):
    """market_service's public detail endpoint answered 404.

    Deliberately not distinguished from a draft or a submitted market:
    `browsing.get_published` on the other side makes those three
    indistinguishable on purpose, so this side inherits the same ambiguity.
    All three are permanent, which is what separates this from
    `MarketTermsUnavailable` — retrying never helps.
    """

    status_code = 404
    code = "market_not_found"
    message = "No such market."


class MarketNotPublished(LedgerError):
    """The market exists but has no `published_at`, so it gets no book.

    Not the status: a CLOSED, PENDING_RESOLUTION or APPROVED market is
    published and does get a book. `published_at is not None` is the
    condition, read off the response rather than inferred from a status code
    that market_service could change independently of this rule.
    """

    status_code = 409
    code = "market_not_published"
    message = "This market has not been published yet."


class InsufficientSharesOutstanding(LedgerError):
    """A sell larger than this outcome's shares outstanding. [T-1] #21.

    The no-shorting rule, enforced against `q_i` rather than against a
    per-user holding — this service has no positions table, and the holdings
    check is [T-3] #23's, meaning anything only under the trade's lock
    (D-012). What this refuses is a sell that would drive `q_i` negative,
    which `C(q)` has no answer for: the preview would otherwise quote a
    number for shares that do not exist anywhere.

    409 rather than 422: nothing about the request is malformed, and the same
    request succeeds against a book with more shares outstanding. It is the
    state of the book that refuses it, the same distinction `InsufficientFunds`
    already draws.
    """

    status_code = 409
    code = "insufficient_shares_outstanding"
    message = "This sell is larger than the shares outstanding for this outcome."


class QuantityTooLarge(LedgerError):
    """The priced cost is above what `Numeric(18, 4)` can store. [T-1] #21, D-040.

    422, for the reason D-038 refuses a fifth decimal place: this is a
    property of the quantity asked for, not of the book's state, and the
    correction belongs where the typing happened. `InsufficientSharesOutstanding`
    is 409 because the same request succeeds against a book with more shares
    outstanding; this one succeeds against no book at all.

    Refused rather than returned, because "the previewed number is the charged
    number" is this route's whole contract and a `total` of 15 integer digits
    is a number [T-2] #22 cannot write. Returning it quotes a trade whose
    confirm step is a `NumericValueOutOfRange` — a 500 arriving after the
    trader committed to a quote this service answered 200 to.
    """

    status_code = 422
    code = "quantity_too_large"
    message = "This quantity prices above the largest cost the ledger can store."


class ProceedsBelowTick(LedgerError):
    """A sell whose proceeds quantize to `0.0000`. [T-1] #21, D-041.

    The other edge of the quantization `QuantityTooLarge` refuses at: a
    magnitude `Numeric(18, 4)` cannot honestly represent is refused rather
    than quoted, in either direction. Here that means real shares priced at
    nothing — quoting the zero takes them for free, and paying a minimum tick
    would pay the trader more than they are worth, which is the residue
    running toward the trader rather than the pool (D-039). Refusing is the
    only answer that keeps both rules.

    422 rather than 409, decided in D-041 as the closer call of the two: it
    pairs with `QuantityTooLarge` as the two edges of one quantization, and
    the trader's correction — a larger quantity — is typing, the same place
    D-038 and D-040 put it, even though this refusal, unlike that one, does
    depend on the book's `q`.
    """

    status_code = 422
    code = "proceeds_below_tick"
    message = "This sell's proceeds round down to nothing at the ledger's scale."


class CostBelowTick(LedgerError):
    """A buy the engine prices at exactly zero. [T-1] #21.

    `ProceedsBelowTick`'s other side, raised from the same place. Past about
    110·b of skew the engine returns exactly zero, and `ROUND_CEILING` of zero
    is zero, so a real quantity would be quoted for nothing. Its own code
    because a buyer told "proceeds below tick" has been told something false.
    """

    status_code = 422
    code = "cost_below_tick"
    message = "This buy's cost rounds to nothing at the ledger's scale."


class UnknownOutcome(LedgerError):
    """`outcome_id` does not name one of this market's outcomes. [T-1] #21.

    422 rather than 404: the market was found and it is the *parameter* that
    is wrong. A 404 already means "no such market" for this service
    (`MarketNotFound`), and a client could not tell the two apart if both
    outcome and market questions used it — two different bugs with two
    different fixes.
    """

    status_code = 422
    code = "unknown_outcome"
    message = "This outcome does not belong to this market."


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


class MarketBookIncomplete(LedgerError):
    """A market's book exists and has no outcome rows.

    Nothing in this service can write that state: `service/books.py::ensure_open`
    inserts the book and its outcomes in one savepoint. It takes a hand-run
    repair or a half-applied migration. It is named anyway because the price
    read joins the two tables, so such a book reads as *no* book, the cold
    path finds it and returns, and the second read comes back empty — which
    used to be an `IndexError` and a bare, unmapped 500.

    500 because the ledger's own data is wrong: not the caller's to fix, and
    not worth retrying.
    """

    status_code = 500
    code = "market_book_incomplete"
    message = "This market's book is missing its outcomes. This is a server fault."
