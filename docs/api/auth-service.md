# Auth service API

Base URL `http://localhost:8000` in development. Interactive docs, generated
from the code and authoritative if this page ever disagrees, at
[`/docs`](http://localhost:8000/docs).

Covers [A-1] #29, [A-2] #30, [A-3] #31, [4.4] #16 and the user-search half of
[4.1] #13, and the frontend halves [FE][A-1] #46, [FE][A-2] #47, [FE][A-3] #48
and [FE][4.1] #57.

Why we host this ourselves rather than buying it:
[ADR 0001](../adr/0001-self-host-authentication.md). Why browsers get cookies and
services get bearer tokens: [ADR 0002](../adr/0002-auth-token-transport.md).

**This service does not know that credits exist.** Registration does not grant
a starting balance; that is [B-1] #32 and belongs to the ledger. Do not expect
a balance field anywhere below.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/auth/register` | Create an account and open its first session |
| POST | `/auth/login` | Open a session |
| POST | `/auth/refresh` | Exchange a refresh token for a new session |
| POST | `/auth/logout` | Revoke the refresh token and clear both cookies |
| GET | `/auth/me` | The current user |
| GET | `/admin/users` | Search user accounts |
| PATCH | `/admin/users/{user_id}/role` | Move a user between `trader` and `admin` |

## Authentication

Two ways in, one verification.

- **Browser:** both tokens arrive as `httpOnly` cookies on register, login and
  refresh. Send `credentials: 'include'` on every call and ignore the `tokens`
  object in the body — JavaScript cannot read these cookies, by design.
- **Service:** `Authorization: Bearer <jwt>`, and `X-Refresh-Token` for the
  refresh call. **The header wins over the cookie**, so a service call carrying
  its own token is never reinterpreted as whoever happens to be logged in.

| Cookie | Path | Lifetime |
| --- | --- | --- |
| `access_token` | `/` | 15 minutes |
| `refresh_token` | `/auth` | 14 days |

The refresh cookie is path-scoped so the long-lived credential is not attached
to every API call. It still reaches `/auth/refresh` and `/auth/logout`, which
are the two routes that need it.

The access cookie's `/` is what lets `/admin/*` work from a browser at all.
Do not "tidy" it to `/auth` to match its sibling.

**The cookies are `SameSite=Lax`, so they are not sent cross-site.** The
frontend and the API must share a registrable domain or none of this works in
the browser. That constraint is recorded in ADR 0002 and has to be resolved
before [5.3] #19.

**Logout cannot revoke an already-issued access token.** It revokes the refresh
token, so the session cannot be extended, but an access token already in flight
stays valid until it expires. The 15-minute lifetime is what bounds that
window.

## POST /auth/register

`201` on success.

```json
{
  "username": "ernest_t",
  "email": "ernest@example.com",
  "password": "correct-horse-battery"
}
```

| Field | Rule |
| --- | --- |
| `username` | 3–32 characters, letters, numbers, hyphens and underscores only; spaces at either end are trimmed first, and the length is counted after |
| `email` | a valid address; trimmed and lowercased before it is stored |
| `password` | at least 12 characters (`PASSWORD_MIN_LENGTH`), at most 128 |

**Uniqueness is case-insensitive on both** `username` and `email`, so
`Ernest_T` collides with `ernest_t`. A clash is a `409`; see
[Duplicate registration](#duplicate-registration) below, which is the error
worth writing real UI for.

**Everyone registers as a `trader`.** There is no way to ask for `admin` here,
and sending a `role` does nothing. An administrator is promoted by hand in the
database and must log in again afterwards, because authority rides in the
token rather than being looked up per request. [4.4] #16 replaces that.

### Response

The same shape comes back from register, login and refresh, so one parser
covers all three:

```json
{
  "user": {
    "id": "8d136846-bca7-4d19-b693-8cfce410fc8d",
    "username": "ernest_t",
    "email": "ernest@example.com",
    "role": "trader",
    "created_at": "2026-09-13T14:06:38.907220Z"
  },
  "tokens": {
    "access_token": "eyJhbGciOi...",
    "refresh_token": "3f6b1c62...",
    "token_type": "bearer",
    "expires_in": 900
  }
}
```

`user.role` is exposed so the interface can decide whether to render the
market-creation UI at all. **It is a hint for the interface only.** Every
admin-only route re-reads the role from the signed token and never trusts the
client.

## POST /auth/login

```json
{ "identifier": "ernest_t", "password": "correct-horse-battery" }
```

**`identifier` takes either the username or the email.** One field rather than
two on purpose: a failed login cannot reveal which of the two exists. For the
same reason a wrong password and an unknown account return the identical
`401 invalid_credentials`. Do not write copy that distinguishes them, because
the backend does not.

A suspended account is the one exception, and returns `403 account_suspended`
with a message worth showing verbatim.

Returns the same `AuthResponse` as register.

## POST /auth/refresh

Takes the refresh token from the `refresh_token` cookie, or from an
`X-Refresh-Token` header. Returns a fresh `AuthResponse` and sets both cookies
again.

**Single use.** The presented token is revoked as the new one is issued, so a
token cannot be replayed. Keep only the newest.

`401 invalid_token` if it is missing, expired, already used or unknown — send
the user to log in.

## POST /auth/logout

Deliberately unauthenticated, and **always returns `200`**:

```json
{ "message": "Logged out." }
```

It works when the access token has already expired, which is when people
actually click log out. It reports success even for a token that was never
valid, so a caller cannot probe which refresh tokens are live. Both cookies are
cleared either way.

## GET /auth/me

The `user` object above, on its own. The reference protected route: `401`
without a valid access token, `403` if the account has been suspended since the
token was issued.

## GET /admin/users

`200`. Administrator only. [4.1] #13, and the list [4.2] #14 will act on.

| Query | Default | Notes |
| --- | --- | --- |
| `q` | — | Part of a username or an email. Omit to list everybody. |
| `limit` | 50 | Clamped at 200. Asking for more is not an error. |
| `cursor` | — | The `next_cursor` from the previous page, unmodified. |

```jsonc
{
  "users": [
    {
      "id": "5f3e...",
      "username": "michelle_l",
      "email": "michelle@example.com",
      "role": "trader",
      "is_suspended": false,
      "created_at": "2026-09-15T04:21:09.412883Z"
    }
  ],
  "next_cursor": null,
  "has_more": false
}
```

**One box for both fields.** `q` matches a fragment of the username *or* the
email, case-insensitively, because somebody chasing an anomaly has part of a
name from a support message rather than the whole of one. It is matched as
literal text: `%` and `_` are not wildcards, which matters because a username
here may contain `_` and most of the team's do.

Newest registration first, and blank or absent `q` lists everybody — so the
screen opens on the user list rather than an empty state waiting to be typed
into. A search that matches nobody is an empty `users`, not a `404`.

**`id` is the handle to the rest of the story.** The transaction history lives
on the ledger service at `:8003`, which holds no user table and knows a user
only by the id in a signed token:

```js
const { users } = await fetch(`http://localhost:8000/admin/users?q=${q}`,
  { credentials: "include" }).then(r => r.json());

const entries = await fetch(
  `http://localhost:8003/ledger/users/${users[0].id}/entries`,
  { credentials: "include" }).then(r => r.json());
```

`docs/api/ledger-service.md` has that side, including the running balance
beside each entry. **No balance appears here**, on this or any other route:
this service does not know that credits exist.

`is_suspended` is readable here and written by [4.2] #14. It appears on this
route only — on register, login and `/auth/me` it could only ever read `false`,
because a suspended account cannot reach any of the three.

### Paging

Keyset, not offset, the same as the audit feed and the ledger history. An
account registered between page 1 and page 2 would otherwise push the window
down, so the last row of page 1 comes back at the top of page 2 and the one
behind it is never seen — and somebody paging a user list to find the account
they are investigating is exactly the reader who would not notice.

Send `q` again with the cursor: a cursor carries a position, not a filter.
A cursor this service did not issue is a `400 malformed_cursor`; echo the value
back rather than constructing one.

**Searching is not audited**, and neither is opening this page. The log records
decisions — promotions, suspensions — and an entry per keystroke would bury
them.

## PATCH /admin/users/{user_id}/role

`200` on success. Administrator only. [4.4] #16.

```json
{ "role": "admin", "reason": "Covering market resolution while Ihsan is away." }
```

`role` is the role the user should end up with, not a delta, so the same
request sent twice is the same outcome — a double-submitted form is not an
error. `reason` is optional and lands in the audit log verbatim.

```json
{
  "user": { "id": "...", "username": "michelle_l", "role": "admin", "...": "..." },
  "takes_effect_within_seconds": 900
}
```

**There are two roles and there is no tier above them.** Any administrator may
promote or demote any other user; the control against misuse is the audit log,
not a super-admin. [ADR 0007](../adr/0007-admin-tiers-and-role-changes.md)
argues why, and names the condition that would change it. A `role` this service
does not recognise — `super_admin`, from an older version of the ticket — is a
`422` from the schema, not a silent no-op.

**An administrator cannot change their own role** (`403
cannot_change_own_role`), so do not offer the control on the signed-in user's
own row.

**`409 last_administrator` means the demotion would leave nobody in charge.**
You will almost certainly never see it: the caller is an administrator and
cannot be their own target, so a demotion normally leaves at least the caller
behind. It exists for the case where two administrators demote each other at
the same instant, where without it both would succeed. A suspended
administrator does not count towards keeping the set alive, since they cannot
sign in to undo anything — but they can still be demoted. Show the message and
leave the row as it was — it is not retryable until somebody else is promoted.

**`takes_effect_within_seconds` is never zero, and the screen should say so.**
Authority travels in the access token, so the market service on `:8001` keeps
honouring whatever the target's current token says until it expires. The auth
service itself reads the row and is current immediately. The target picks the
new role up on their next access token — an ordinary `/auth/refresh` supplies
that just as well as a fresh login, so in practice the wait is however long
until their client next refreshes, bounded by this number.

**The first administrator is not made here.** Registration always creates a
trader, and the account that may grant administrative authority cannot itself
be granted it, so administrator number one is a manual `UPDATE` against the
database — permanently, by design. Everyone after that comes through this
route.

## Errors

The same envelope the market service uses, so one parser covers both:

```json
{ "error": { "code": "invalid_credentials", "message": "Incorrect username or password." } }
```

| Status | `code` | When |
| --- | --- | --- |
| 401 | `invalid_credentials` | wrong password, or no such account — deliberately the same |
| 401 | `invalid_token` | no access token, or it is expired, forged or malformed |
| 403 | `account_suspended` | credentials were right; the account is suspended |
| 400 | `malformed_cursor` | `cursor` was not one this service issued |
| 403 | `not_an_administrator` | signed in, but not an admin — same code the market service uses |
| 403 | `cannot_change_own_role` | an administrator targeting their own row |
| 404 | `user_not_found` | no user with that id |
| 409 | `last_administrator` | the demotion would leave no administrator; see above |
| 409 | `duplicate_user` | username or email already registered; see `details` |
| 422 | — | FastAPI's own body-validation error, a different shape |

Two failure shapes exist and they do not look alike. Ours carries the envelope
above. A malformed body — a short password, a bad email, a username with a
space — is FastAPI's, and comes back as `{"detail": [...]}`. **Branch on the
presence of `error`.**

### Duplicate registration

`duplicate_user` adds a `details` array naming every field that clashed.
Nothing else in this service does, and it is additive, so a client that ignores
it still reads `code` and `message`:

```json
{
  "error": {
    "code": "duplicate_user",
    "message": "That username and email are already registered.",
    "details": [
      { "field": "username", "message": "That username is already registered." },
      { "field": "email",    "message": "That email is already registered." }
    ]
  }
}
```

`field` is exactly the request field it refers to, so it maps straight onto the
form input — **do not parse `message` to work out which one is wrong**:

```js
const { error } = await res.json();
const byField = Object.fromEntries(
  (error.details ?? []).map(d => [d.field, d.message])
);
setErrors(byField);   // { username: "That username is already registered." }
```

**Both fields are reported when both clash**, so the form can mark them
together rather than sending the user round the loop twice.

Keep the `?? []` above. `details` is present on every duplicate we can
attribute, including the case where two people register the same name at the
same instant — the losing request re-reads the winning row and names the field.
It can still be absent in the pathological case where that winning account is
deleted in the milliseconds in between, and then only the generic `message`
applies. Rare enough that you will never see it, cheap enough to guard.

## Notes for [FE][A-1] #46, [FE][A-2] #47 and [FE][A-3] #48

1. `credentials: 'include'` on every call, or the cookies neither arrive nor
   come back.
2. Ignore the `tokens` object in the browser. The cookies are `httpOnly`;
   storing a copy in `localStorage` throws away the protection they exist for.
3. Do not read the access token to find out when it expires. Treat a `401` as
   the signal: call `/auth/refresh` once, retry the original request, and send
   the user to log in only if the refresh also fails.
4. `403 account_suspended` is not retryable. Refreshing will not fix it — show
   the message and stop.
5. Render `details` on the register form, `error.message` everywhere else.
6. Registration does not return a balance and never will. Ask the ledger.
