# Auth service API

Base URL `http://localhost:8000` in development. Interactive docs, generated
from the code and authoritative if this page ever disagrees, at
[`/docs`](http://localhost:8000/docs).

Covers [A-1] #29, [A-2] #30 and [A-3] #31, and the frontend halves
[FE][A-1] #46, [FE][A-2] #47 and [FE][A-3] #48.

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
| `username` | 3–32 characters, letters, numbers, hyphens and underscores only |
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
