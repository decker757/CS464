# ADR 0002: Cookies for the browser, bearer tokens for services

- **Status:** Accepted
- **Date:** 2026-09-13
- **Affects:** [A-2] #30, [A-3] #31, [FE][A-1] #46, [FE][A-2] #47, [FE][A-3] #48
- **Implemented in:** `backend/auth_service/controller/` — `transport.py` and `dependencies.py`

## Context

Two clients need to authenticate against the same service.

Michelle's frontend is a browser. [A-3] #31 requires that refreshing a
protected page preserves access, which means the credential has to survive F5
without the frontend doing anything clever.

The trading engine, the ledger service, and the websocket server ([F-2] #42)
are not browsers. They hold no cookie jar and want to attach a token
explicitly.

## Decision

Issue one JWT and accept it two ways.

| Client | Transport | Set by |
| --- | --- | --- |
| Browser | `httpOnly` cookie, `Secure`, `SameSite=Lax` | `set_auth_cookies` |
| Service | `Authorization: Bearer <jwt>` | caller |

Both funnel into a single signature check in `controller/dependencies.py`.
There is one verification path, not two.

**Precedence: the header wins over the cookie.** An explicitly attached
credential should beat one the browser sent ambiently, so a service call
carrying its own token is never silently reinterpreted as whoever happens to be
logged in.

**Session model.**

| Token | Form | Lifetime | Revocable |
| --- | --- | --- | --- |
| Access | JWT, HS256 | 15 minutes | No |
| Refresh | Opaque random, SHA-256 at rest | 14 days | Yes |

Refresh tokens are single use and rotate on every exchange. Presenting an
already-revoked one is treated as a leaked cookie being replayed, and every
session for that user is revoked.

## Consequences

**What we get.** A page refresh keeps the session with no frontend work.
Cross-site scripting cannot read an `httpOnly` cookie, so a token cannot be
exfiltrated the way a `localStorage` token can. Internal services use plain
headers with no cookie handling. The websocket handshake can use either.

**CORS is now strict.** Credentialed requests cannot use `allow_origins=["*"]`;
the browser silently drops the cookie. `CORS_ORIGINS` must list the frontend
origin exactly, and Michelle must send `credentials: 'include'` on every call.

**CSRF.** `SameSite=Lax` stops the cookie being attached to cross-site POST
requests, which covers our mutating routes, and no route changes state on GET.
That is adequate today. It stops being adequate the moment we set
`SameSite=None`, at which point a double-submit CSRF token becomes mandatory.
See the deployment constraint below, because that moment is a real
possibility.

**Deployment constraint, and the biggest risk in this decision.** A `Lax`
cookie is not sent on cross-site requests at all. Browsers judge that on the
registrable domain, so `app.cs464.dev` calling `api.cs464.dev` is same-site and
works, while a frontend on `something.vercel.app` calling an API on
`something.fly.dev` is cross-site and the cookie is never sent. Local
development is fine because differing ports on `localhost` are still same-site.
**Either deploy the frontend and the API under one registrable domain, or
switch to `SameSite=None; Secure` and add CSRF tokens first.** Decide this
before [5.3] #19 wires up the deploy pipeline.

**Logout is not instantaneous.** A JWT cannot be withdrawn before it expires,
so logout revokes the refresh token and the session dies within one
access-token lifetime. The 15-minute TTL is what bounds that window. If we ever
need immediate revocation, add a denylist keyed on the token's `jti` claim.

**HS256 means shared minting power.** Every service holding `JWT_SECRET` to
verify a token can also forge one. Acceptable for a three-person project with
one trust boundary. The upgrade, if the boundary ever matters, is RS256 or
EdDSA with a published public key, so services verify without being able to
mint.

**Two entry points to keep tested.** `unit_test/test_session.py` covers both.

## Alternatives rejected

**Bearer token only.** The frontend must store the token to survive a refresh,
and the usual place is `localStorage`, which any cross-site scripting bug can
read. It also pushes session plumbing into Michelle's work for no benefit.

**Cookie only.** Awkward for service-to-service calls and for the websocket
handshake, both of which would need a cookie jar they do not otherwise want.
