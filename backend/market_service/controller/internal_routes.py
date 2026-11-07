"""Service-to-service routes: no browser calls these. #640, ADR 0021

Apart from `routes.py` and `public_routes.py` because the credential differs: a
key that a service holds, not a token that a user does. The query lives in
`service/published_terms.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from controller.dependencies import DbSession, ServiceKeyChecked
from model.schemas import PublishedMarketsOut, PublishedMarketTermsOut
from service import published_terms

router = APIRouter(prefix="/internal", tags=["internal"])


@router.get(
    "/markets/published",
    response_model=PublishedMarketsOut,
    summary="List published markets' trading terms for another service",
    description=(
        "#640. **Service-to-service; not for the frontend.** The ledger's "
        "local copy of each market's trading terms refreshes from here, so a "
        "trade asks that copy rather than calling market. A browser cannot "
        "call it, because it needs `X-Service-Key`, which no browser holds. "
        "ADR 0021, and ADR 0030, the ledger's local copy.\n\n"
        "`markets` is every market ever published, ordered by `id`, each "
        "exactly `id`, `status` and `close_time` as stored; a draft or a "
        "submitted market is absent. Nothing is derived here: whether a "
        "market still trades is the ledger's to work out from `close_time` "
        "and `status`. `as_of` is taken just before the read, so the list is "
        "at least as fresh as it says.\n\n"
        "An access token does not open this route, and the key opens none "
        "outside `/internal`. Reading is not an audited action."
    ),
    responses={
        401: {"description": "`X-Service-Key` missing or incorrect."},
    },
)
async def list_published_markets(
    session: DbSession, _: ServiceKeyChecked
) -> PublishedMarketsOut:
    as_of = datetime.now(UTC)
    rows = await published_terms.list_published_terms(session)
    return PublishedMarketsOut(
        as_of=as_of,
        markets=[PublishedMarketTermsOut.model_validate(row) for row in rows],
    )
