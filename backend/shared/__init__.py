"""Code every backend service needs an identical copy of. [F-6] #76

ADR 0005 named what belongs here and, just as importantly, what does not:
"Extract when #41 lands, and narrowly: token verification, the settings base,
and the LMSR engine. Not `core`." This package is that list and nothing else.

**The bar for adding a module here.** Two services having similar code is not
enough — ADR 0003 refused to share `core` at two occurrences precisely because
the duplication was asymmetric, and a shared module would have carried a
token-*minting* path into services that must never have one. A module earns a
place here only when every caller needs the identical behaviour and a
divergence between two copies would be a bug rather than a design choice.

What is deliberately still copied per service, and why:

- **`core/database.py`.** Each service's `Base.metadata` is its own, and that
  is load-bearing: `unit_test/conftest.py` calls `drop_all` on it, and
  `create_all` runs against it at boot. One shared `Base` would enrol every
  service's tables in every other service's metadata, and the first conftest
  rebuild would try to drop tables its role has no grant on.
- **`controller/transport.py`.** The five copies agree on very little; each
  service reads a different cookie for a different reason, and the realtime
  one is checking a WebSocket origin by hand.
- **`core/errors.py` and the handler over it.** Each service raises its own
  error hierarchy, which is what keeps a market error from being catchable in
  the ledger. Sharing the handler would mean sharing the base class.

Importable because the Docker build contexts were restructured to make it so:
every service builds from `backend/` rather than from its own directory, and
its image mirrors the repository layout with this package beside it. ADR 0005
called that restructure "the binding constraint", which is why nothing could
live here until it happened.
"""
