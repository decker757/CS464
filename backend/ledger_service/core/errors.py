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

from decimal import Decimal


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
    """The batch would take an account below what its buy orders reserve.
    [T-2] #22, ADR 0024.

    409 rather than 422: nothing about the request is malformed, and the same
    request may well succeed later or have succeeded a moment ago. It is the
    state of the account that refuses it.

    Carries the figures, because the caller has to tell somebody why their
    trade did not go through, and "insufficient funds" on its own is the least
    useful true sentence available. `balance` is the account's before the
    batch. A refusal after a debit carries `required`, what the batch had
    taken by then; a refusal at the start of the batch carries `shortfall`
    instead, how far the reservation there exceeds the balance. D-NEW "A
    refusal from the running check reports the balance before the batch".

    `movement_key` is the idempotency key of the movement the check refused
    after, None at the start of the batch, and never in the response: it is
    how a caller tells its own movement's refusal from a fill's (D-NEW "A
    fill the running check refuses inside a cascade is `500
    cascade_fill_refused`").
    """

    status_code = 409
    code = "insufficient_funds"

    def __init__(
        self,
        *,
        balance: Decimal,
        reserved: Decimal,
        available: Decimal,
        required: Decimal | None = None,
        shortfall: Decimal | None = None,
        movement_key: str | None = None,
    ) -> None:
        self.balance = balance
        self.reserved = reserved
        self.available = available
        self.required = required
        self.shortfall = shortfall
        self.movement_key = movement_key
        if shortfall is None:
            self.message = (
                f"This account holds {balance} credits, {available} of them "
                f"available, and the transaction needs {required}."
            )
        else:
            self.message = (
                f"This account holds {balance} credits, {available} of them "
                f"available, and this request needs {shortfall} more."
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


class AccountSuspended(LedgerError):
    """[4.2] #14 - the auth service says this account is suspended. ADR 0022.

    Same code and message as auth_service's `AccountSuspended`, so the
    frontend has one handler for both. Not retryable: the account, not the
    request, is refused.
    """

    status_code = 403
    code = "account_suspended"
    message = "This account is suspended. Contact an administrator."


class AuthUnavailable(LedgerError):
    """[4.2] #14 - the auth service did not say whether this account may trade.
    [L-1] #38 - or did not name the leaderboard's holders.

    For the account check: a refused connection, a timeout, a 5xx, or any
    answer that is not a `200`, a `401` or a `403 account_suspended`. For the
    lookup (ADR 0021): a refused connection, a timeout, any answer but a
    `200`, or a `200` whose body is not the documented list of users, on any
    chunk. Fails closed, as ADR 0017's gate does. Its own code rather than
    `market_terms_unavailable`, which would name the wrong service (ADR
    0022). 503 because a retry in a moment may work.
    """

    status_code = 503
    code = "auth_unavailable"
    message = "Could not check this account right now. Try again shortly."


class SettlementUnconfirmed(LedgerError):
    """The payouts committed and market_service was not told. [3.4] #12.

    Raised for every answer to the settle call but a `200`, and for a call
    that never got an answer. Money has moved and the market is not marked
    SETTLED, so every failure means the same thing and has the same remedy:
    the administrator repeats the request, which writes nothing in the ledger.
    Mapping a 401 or a 404 the way `fetch` does, to `NotAuthenticated` or
    `MarketNotFound`, would tell the administrator that nothing happened.
    DECISIONS.md, "`market_terms.mark_settled` accepts only a `200`".

    503 because the dependency did not confirm, and a repeat is the fix.
    """

    status_code = 503
    code = "settlement_unconfirmed"
    message = (
        "The payouts were made, but the market could not be marked settled. "
        "Repeat the request."
    )


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


class MarketNotApproved(LedgerError):
    """A settlement asked for a market that is neither approved nor settled.
    [3.4] #12, ADR 0019.

    Read off market_service's public detail, and only when the ledger holds no
    record for the market: a recorded settlement replays whatever the status
    now says. Spelled as market_service's settle step spells it. 409: the
    request is well formed and the market's state refuses it.
    """

    status_code = 409
    code = "market_not_approved"
    message = "This market has no approved outcome to settle."


class DisputeWindowOpen(LedgerError):
    """A settlement asked for an approved market that is not yet `settleable`.
    [3.4] #12, ADR 0019.

    market_service decides the edge on its own clock and the ledger reads the
    flag rather than recomputing it. Its own code, apart from
    `MarketNotApproved`, because the remedy differs: wait, and repeat. 409, as
    market_service's settle step answers the same case.
    """

    status_code = 409
    code = "dispute_window_open"
    message = "This market's dispute window has not closed yet."


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

    The no-shorting rule against `q_i`, which is the whole market's, not one
    trader's. On the trade route it is unreachable: each outcome's `q` equals
    the sum of the positions in it, so the holding check
    (`InsufficientSharesHeld`) always refuses first. It stays as a backstop —
    nothing in the database stops a hand-repaired book's `q` going negative,
    which `C(q)` has no answer for. The preview still reaches it, because the
    preview checks no holding.

    409 rather than 422: nothing about the request is malformed, and the same
    request succeeds against a book with more shares outstanding. It is the
    state of the book that refuses it, the same distinction `InsufficientFunds`
    already draws.
    """

    status_code = 409
    code = "insufficient_shares_outstanding"
    message = "This sell is larger than the shares outstanding for this outcome."


class InsufficientSharesHeld(LedgerError):
    """A sell larger than the caller's own position in that outcome, less what
    their live sell orders in it reserve. [T-3] #23, ADR 0024.

    409 for `InsufficientFunds`' reason: the request is well formed and the
    state refuses it. Distinct from `InsufficientSharesOutstanding`, whose
    remedy is not the caller's: this one is fixed by selling at most
    `held - reserved`.

    Carries the figures, which the raiser has already put at scale 4
    (`service/trading.py`), so a caller with no position reads
    `"0.0000"` and a request of `10` reads `"10.0000"`, and a sell form can
    offer "sell all" without a second request. `held` is the whole position;
    `reserved` is the part the caller's sell orders hold back.
    """

    status_code = 409
    code = "insufficient_shares_held"

    def __init__(self, *, held: Decimal, reserved: Decimal, requested: Decimal) -> None:
        self.held = held
        self.reserved = reserved
        self.requested = requested
        self.message = (
            f"This account holds {self.held} shares of this outcome, "
            f"{self.reserved} of them reserved by its sell orders, and the "
            f"sell asks for {self.requested}."
        )
        super().__init__(self.message)


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


class QuoteStale(LedgerError):
    """The quoted `state_version` no longer names the book. [T-2] #22.

    "The trade's staleness check is strict `state_version` equality, and the
    field is required" — re-read under the book row's lock and compared
    against what the trade quoted. Either direction is refused: older than the
    book (somebody else traded first) and newer than the book (a version that
    does not exist yet) are both a quote that no longer describes this market.

    409 for `InsufficientFunds`'s reason: nothing about the request is
    malformed, and the same request would have succeeded against yesterday's
    price. Carries `quoted` and `current` so a client can re-preview and
    retry without guessing which way it was wrong.
    """

    status_code = 409
    code = "quote_stale"

    def __init__(self, *, quoted: int, current: int) -> None:
        self.quoted = quoted
        self.current = current
        self.message = (
            f"This quote named state_version {quoted}; the market is now at "
            f"{current}. Re-preview and retry."
        )
        super().__init__(self.message)


class OrderCrossesBook(LedgerError):
    """A new limit order would fire, or be fired by, an open one. [T-6] #26.

    ADR 0024, "Resting orders fill against the AMM inside the trade that moved
    the price", keeps every open order unable to fill at the current price, and
    "A placement that would cross the book is refused, `order_crosses_book`"
    refuses the placement that would break that against another open order
    pushing the price the other way. Your own orders count.

    409 for `InsufficientFunds`'s reason: nothing about the request is
    malformed, and the same limit is accepted once no opposing order is in the
    way. `bound` is the nearest limit on the other side, in the new order's
    own outcome terms, and the limit must clear it strictly: below it for a
    buy, above it for a sell.
    """

    status_code = 409
    code = "order_crosses_book"

    def __init__(self, *, side: str, bound: Decimal) -> None:
        self.side = side
        self.bound = bound
        direction = "below" if side == "buy" else "above"
        self.message = (
            f"An order is in the way at this price: a {side}'s limit must be "
            f"{direction} {bound}"
        )
        super().__init__(self.message)


class MarketNotBinary(LedgerError):
    """A limit order on a market with more than two outcomes. [T-6] #26.

    ADR 0024, "Binary markets only, and no crossed book": with three or more
    outcomes there is no single price to rank orders on, and the cascade has
    no termination proof. 409 for `InsufficientFunds`'s reason: the request is
    well formed, and it is the market that refuses it. Trading the market
    instantly still works.
    """

    status_code = 409
    code = "market_not_binary"
    message = "Limit orders are only available on markets with two outcomes."


class OrderNotFound(LedgerError):
    """No order with this id belongs to the caller. [T-8] #28.

    Another trader's order reads the same as one that does not exist, so ids
    cannot be probed (D-NEW "Cancel calls neither auth nor the market gate").
    """

    status_code = 404
    code = "order_not_found"
    message = "No such order."


class OrderAlreadyFilled(LedgerError):
    """A cancel found the order filled completely. [T-8] #28, ADR 0024.

    The fill took the book lock first and committed first, so there is no
    remainder to release. 409: the request was fine, the order moved on.
    """

    status_code = 409
    code = "order_already_filled"
    message = "This order has already filled completely."


class OrderAlreadyCancelled(LedgerError):
    """A cancel found the order cancelled already. [T-8] #28, ADR 0024.

    A retried cancel lands here, and the client treats it as done: the first
    cancel released the remainder, and a reservation is derived, so nothing
    is released twice.
    """

    status_code = 409
    code = "order_already_cancelled"
    message = "This order has already been cancelled."


class OrderReleased(LedgerError):
    """A cancel found the order's market with a result. [T-8] #28, ADR 0024.

    The result released the remainder in its own commit (D-NEW "An open order
    is live only while its market has no result"), so a cancel has nothing
    left to release.
    """

    status_code = 409
    code = "order_released"
    message = "This order's market has a result, so its remainder is already released."


class LeaderboardRefreshed(LedgerError):
    """A leaderboard cursor names a ranking that is no longer stored. [L-1] #38.

    DECISIONS.md, "A leaderboard cursor names its generation; the current and
    previous generations are served, and an older one is `409
    leaderboard_refreshed`". A pruned generation and one that never existed
    cannot be told apart, and the client's answer is the same: start again
    from page one.

    409 for `QuoteStale`'s reason: nothing about the request is malformed, and
    the same cursor was good a minute ago. Not 410: the board still exists,
    only the client's place in it has gone.
    """

    status_code = 409
    code = "leaderboard_refreshed"
    message = (
        "The ranking this cursor belongs to has been replaced. Load the "
        "leaderboard again from the first page."
    )


class PendingWritesOnReplay(LedgerError):
    """`posting.post` reached its replay branch with unwritten work already in
    the session. [T-2] #22.

    "`posting.post` refuses to replay into a dirty session": the replay branch
    calls `session.commit()`, which flushes the whole session — so a caller
    holding pending writes would have them committed alongside a transaction
    that wrote no entries of its own. This is the alarm for a caller that got
    "A caller holding pending writes must establish under its own lock that
    the idempotency key is absent" wrong, raised before the commit rather than
    rolled back after it, because by the time `post` reaches this point there
    is no way to discard only the caller's writes.

    500 rather than 409: nothing the client sent is wrong and nothing it can
    do differently helps. This is the service reporting a bug in itself.
    """

    status_code = 500
    code = "pending_writes_on_replay"
    message = (
        "A caller reached a replay with unwritten work already pending in "
        "the session. This is a bug in this service, not in the request."
    )


class CascadeFillRefused(LedgerError):
    """The overdraft check refused a trade's batch anywhere but at the trade's
    own movement. [T-7] #27, ADR 0024.

    Impossible by construction, because every fill is bounded by its own
    order's reservation, so this is a bug in the server, like
    `MalformedBatch`, and takes its status. It names no figures: they would
    be another account's. D-NEW "A fill the running check refuses inside a
    cascade is `500 cascade_fill_refused`".
    """

    status_code = 500
    code = "cascade_fill_refused"
    message = (
        "A resting order this trade would have filled was refused by the "
        "overdraft check. This is a bug in this service, not in the request."
    )


class MalformedBatch(LedgerError):
    """`posting.post_all` was handed no movements, one key twice, or a
    reservation drop it cannot apply. [3.4] #12, ADR 0024.

    "An empty batch, or one key twice in a batch, is `MalformedBatch`, a 500":
    the server builds every movement and every key, and the settlement
    request has no body to fix, so this is a bug in the server like
    `PendingWritesOnReplay`, and takes its status. Raised before any SQL, so
    nothing is locked and nothing the caller has pending is committed.
    """

    status_code = 500
    code = "malformed_batch"
    message = (
        "A batch of movements was empty, named one idempotency key twice, or "
        "carried a reservation drop below zero, with no single user account, "
        "or on a movement that does not debit that account. "
        "This is a bug in this service, not in the request."
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


class PriceHistoryInconsistent(LedgerError):
    """A market's trades do not run `state_version` 1, 2, 3, ... in order.

    The price-history replay refuses such a feed, and the service raises this
    around that call. It is not `MarketBookIncomplete`: that is a book the
    engine cannot price, this is a fault in the trades, and two codes tell the
    operator reading the log which to go and look at. DECISIONS.md, "A price
    history whose trades skip or reorder a state version answers 500
    `price_history_inconsistent`".

    500 because the ledger's own data is wrong, and the same feed gives the
    same gap on the next read: nothing the caller does changes it.
    """

    status_code = 500
    code = "price_history_inconsistent"
    message = "This market's trade history is inconsistent. This is a server fault."
