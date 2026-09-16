"""Status codes, the error envelope and the response shape. [1.1] #1.

Business rules are asserted in unit_test/service. A test that goes through a
route to check a rule belongs there instead; what belongs here is everything
the frontend parses.
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from service.audit import Actor
from unit_test.conftest import closed_market
from unit_test.conftest import market_json as _payload
from unit_test.conftest import proposal_json as _proposal


# --- the guard ------------------------------------------------------------
async def test_saving_without_a_token_is_401(client: AsyncClient) -> None:
    response = await client.post("/markets", json=_payload())

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_saving_with_a_traders_token_is_403(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """401 and 403 mean different things to the frontend: one says log in
    again, the other says this account never will be allowed in."""
    response = await client.post("/markets", json=_payload(), headers=trader_headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_an_administrator"


async def test_a_trader_cannot_list_markets(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """[1.1] #1: a draft is not visible to traders.

    Asserted on the read routes, not only on the write one. The write guard
    stops a trader creating a market; this is the guard that stops one seeing
    somebody else's.
    """
    response = await client.get("/markets", headers=trader_headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_an_administrator"


async def test_a_trader_cannot_read_a_market_even_by_its_exact_id(
    client: AsyncClient, admin_headers: dict[str, str], trader_headers: dict[str, str]
) -> None:
    """403 rather than 404 here, and deliberately so.

    The admin check runs before the route body, so a trader is turned away
    before ownership is even considered. They learn that they are not an
    administrator, which they already knew, and nothing about whether this
    market exists.
    """
    created = await client.post("/markets", json=_payload(), headers=admin_headers)
    market_id = created.json()["market"]["id"]

    response = await client.get(f"/markets/{market_id}", headers=trader_headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_an_administrator"


async def test_a_trader_sees_the_same_403_for_a_market_that_does_not_exist(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """So the response cannot be used to probe which ids are real."""
    response = await client.get(f"/markets/{uuid.uuid4()}", headers=trader_headers)

    assert response.status_code == 403


async def test_a_garbage_token_is_401_not_500(client: AsyncClient) -> None:
    response = await client.post(
        "/markets", json=_payload(), headers={"Authorization": "Bearer nonsense"}
    )

    assert response.status_code == 401


async def test_the_cookie_is_accepted_as_well_as_the_header(
    client: AsyncClient, admin_id: uuid.UUID
) -> None:
    """Michelle's browser sends the httpOnly cookie the auth service set; a
    service caller sends a bearer header. ADR 0002 requires both to work."""
    from core.config import get_settings
    from unit_test.conftest import mint_token

    client.cookies.set(get_settings().access_cookie_name, mint_token(admin_id))

    response = await client.post("/markets", json=_payload())

    assert response.status_code == 201


async def test_every_market_route_requires_a_token(client: AsyncClient) -> None:
    """Guards against a route being added later without the dependency."""
    assert (await client.get("/markets")).status_code == 401
    assert (await client.get(f"/markets/{uuid.uuid4()}")).status_code == 401
    assert (
        await client.post(f"/markets/{uuid.uuid4()}/publish")
    ).status_code == 401
    assert (
        await client.post(
            f"/markets/{uuid.uuid4()}/propose-outcome", json=_proposal(uuid.uuid4())
        )
    ).status_code == 401


async def test_health_is_open(client: AsyncClient) -> None:
    """The probe must not need a token, or the container never reports healthy."""
    assert (await client.get("/health")).status_code == 200


# --- saving ---------------------------------------------------------------
async def test_the_first_save_is_201_and_later_ones_are_200(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    """Tells the form whether its draft_key took hold."""
    payload = _payload()

    first = await client.post("/markets", json=payload, headers=admin_headers)
    second = await client.post("/markets", json=payload, headers=admin_headers)

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["market"]["id"] == second.json()["market"]["id"]


async def test_the_response_carries_the_market_and_what_blocks_submission(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/markets", json=_payload(close_time=None), headers=admin_headers
    )

    body = response.json()
    assert body["market"]["status"] == "draft"
    assert {p["field"] for p in body["blocking_submission"]} == {"close_time"}


async def test_a_clean_draft_reports_nothing_blocking(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.post("/markets", json=_payload(), headers=admin_headers)

    assert response.json()["blocking_submission"] == []


async def test_the_creator_is_taken_from_the_token_not_the_body(
    client: AsyncClient, admin_id: uuid.UUID, admin_headers: dict[str, str]
) -> None:
    """A creator_id in the body must be ignored, or anyone could plant a market
    in somebody else's drawer."""
    impostor = str(uuid.uuid4())

    response = await client.post(
        "/markets", json=_payload(creator_id=impostor), headers=admin_headers
    )

    assert response.json()["market"]["creator_id"] == str(admin_id)


async def test_timestamps_come_back_with_an_offset(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.post("/markets", json=_payload(), headers=admin_headers)

    market = response.json()["market"]
    assert market["close_time"].endswith("Z") or "+" in market["close_time"]


async def test_a_naive_timestamp_is_422(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/markets", json=_payload(close_time="2027-01-01T00:00:00"), headers=admin_headers
    )

    assert response.status_code == 422


async def test_a_missing_draft_key_is_422(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    payload = _payload()
    del payload["draft_key"]

    response = await client.post("/markets", json=payload, headers=admin_headers)

    assert response.status_code == 422


# --- submitting -----------------------------------------------------------
async def test_a_complete_submission_is_accepted(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/markets", json=_payload(status="submitted"), headers=admin_headers
    )

    assert response.status_code == 201
    body = response.json()
    assert body["market"]["status"] == "submitted"
    assert body["market"]["submitted_at"] is not None


async def test_a_refused_submission_is_422_and_lists_every_field(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    """The `details` list is the only addition to the shared error envelope, so
    a client that ignores it still reads code and message."""
    response = await client.post(
        "/markets",
        json=_payload(status="submitted", question=None, resolution_sources=[]),
        headers=admin_headers,
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "draft_incomplete"
    assert {d["field"] for d in error["details"]} == {"question", "resolution_sources"}


async def test_a_refused_submission_writes_nothing_through_the_real_stack(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    """Atomicity depends on get_session rolling back as the error propagates,
    which only happens on the real request path. Asserted here rather than only
    in the service layer, where the test has to roll back by hand."""
    key = str(uuid.uuid4())
    await client.post(
        "/markets",
        json=_payload(draft_key=key, question="The original question text"),
        headers=admin_headers,
    )

    refused = await client.post(
        "/markets",
        json=_payload(
            draft_key=key, status="submitted", question="An edit", close_time=None
        ),
        headers=admin_headers,
    )

    assert refused.status_code == 422
    listed = (await client.get("/markets", headers=admin_headers)).json()["markets"]
    assert len(listed) == 1
    assert listed[0]["question"] == "The original question text"
    assert listed[0]["status"] == "draft"


async def test_the_summary_timestamps_also_carry_an_offset(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    """The list projection is a separate model from the detail one, so it gets
    its own guard rather than inheriting the assumption."""
    await client.post("/markets", json=_payload(), headers=admin_headers)

    row = (await client.get("/markets", headers=admin_headers)).json()["markets"][0]

    assert row["updated_at"].endswith("Z") or "+" in row["updated_at"]
    assert row["close_time"].endswith("Z") or "+" in row["close_time"]


async def test_an_autosave_after_submission_is_409(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    payload = _payload(status="submitted")
    await client.post("/markets", json=payload, headers=admin_headers)

    response = await client.post(
        "/markets", json={**payload, "status": "draft"}, headers=admin_headers
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "market_not_editable"


# --- publishing [1.3] #3 --------------------------------------------------
async def _submit(client: AsyncClient, headers: dict[str, str], **overrides: object) -> str:
    created = await client.post(
        "/markets", json=_payload(status="submitted", **overrides), headers=headers
    )
    assert created.status_code == 201
    return created.json()["market"]["id"]


async def test_publishing_returns_the_open_market(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    """The whole market comes back, not an acknowledgement, so the form can
    repaint without a second round trip."""
    market_id = await _submit(client, admin_headers)

    response = await client.post(f"/markets/{market_id}/publish", headers=admin_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == market_id
    assert body["status"] == "open"
    assert body["published_at"] is not None


async def test_published_at_carries_an_offset(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    """A new timestamp on the wire gets the same guard as every other one."""
    market_id = await _submit(client, admin_headers)

    body = (
        await client.post(f"/markets/{market_id}/publish", headers=admin_headers)
    ).json()

    assert body["published_at"].endswith("Z") or "+" in body["published_at"]


async def test_publishing_takes_no_body(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    """Nothing about the terms travels with a publish, so there is no request
    that can change a market and expose it in the same call."""
    market_id = await _submit(client, admin_headers, question="The submitted question?")

    body = (
        await client.post(f"/markets/{market_id}/publish", headers=admin_headers)
    ).json()

    assert body["question"] == "The submitted question?"


async def test_publishing_a_draft_is_409(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    created = await client.post("/markets", json=_payload(), headers=admin_headers)
    market_id = created.json()["market"]["id"]

    response = await client.post(f"/markets/{market_id}/publish", headers=admin_headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "market_not_submitted"


async def test_publishing_twice_is_409(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    """A distinct code from the one above, because the remedy differs: submit
    it, versus reload and stop offering the button."""
    market_id = await _submit(client, admin_headers)
    await client.post(f"/markets/{market_id}/publish", headers=admin_headers)

    response = await client.post(f"/markets/{market_id}/publish", headers=admin_headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "market_already_open"


async def test_a_trader_cannot_publish(
    client: AsyncClient, admin_headers: dict[str, str], trader_headers: dict[str, str]
) -> None:
    market_id = await _submit(client, admin_headers)

    response = await client.post(f"/markets/{market_id}/publish", headers=trader_headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_an_administrator"


async def test_another_administrator_cannot_publish_it(
    client: AsyncClient,
    admin_headers: dict[str, str],
    other_admin_headers: dict[str, str],
) -> None:
    """404, like reading it. A 403 would confirm the market exists."""
    market_id = await _submit(client, admin_headers)

    response = await client.post(
        f"/markets/{market_id}/publish", headers=other_admin_headers
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "market_not_found"


async def test_publishing_an_unknown_id_is_404(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.post(
        f"/markets/{uuid.uuid4()}/publish", headers=admin_headers
    )

    assert response.status_code == 404


async def test_a_malformed_id_on_publish_is_422_not_500(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.post("/markets/banana/publish", headers=admin_headers)

    assert response.status_code == 422


async def test_a_blocked_publish_is_422_and_lists_every_field(
    client: AsyncClient, admin_headers: dict[str, str], session
) -> None:
    """The market was complete when it was submitted and has since gone stale.

    Aged through the database rather than by moving the clock, because that is
    what actually happens: a market sits submitted until its close time passes.
    The envelope is the same `draft_incomplete` shape the submit button returns,
    so the form needs no second branch.
    """
    from sqlalchemy import text  # noqa: PLC0415

    market_id = await _submit(client, admin_headers)
    await session.execute(
        text("UPDATE market.markets SET close_time = now() - interval '1 day' "
             "WHERE id = :id"),
        {"id": uuid.UUID(market_id)},
    )
    await session.commit()

    response = await client.post(f"/markets/{market_id}/publish", headers=admin_headers)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "draft_incomplete"
    assert "close_time" in {d["field"] for d in error["details"]}


async def test_a_blocked_publish_leaves_the_market_submitted(
    client: AsyncClient, admin_headers: dict[str, str], session
) -> None:
    """Atomicity on the real request path, where get_session rolls back as the
    error propagates."""
    from sqlalchemy import text  # noqa: PLC0415

    market_id = await _submit(client, admin_headers)
    await session.execute(
        text("UPDATE market.markets SET close_time = now() - interval '1 day' "
             "WHERE id = :id"),
        {"id": uuid.UUID(market_id)},
    )
    await session.commit()

    await client.post(f"/markets/{market_id}/publish", headers=admin_headers)

    reread = await client.get(f"/markets/{market_id}", headers=admin_headers)
    assert reread.json()["status"] == "submitted"
    assert reread.json()["published_at"] is None


async def test_an_autosave_after_publication_is_409(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    """The form may still be open behind the publish button. Michelle stops the
    timer on this, the same as she does on `market_not_editable`."""
    payload = _payload(status="submitted")
    created = await client.post("/markets", json=payload, headers=admin_headers)
    market_id = created.json()["market"]["id"]
    await client.post(f"/markets/{market_id}/publish", headers=admin_headers)

    response = await client.post(
        "/markets", json={**payload, "status": "draft"}, headers=admin_headers
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "market_already_open"


async def test_open_is_not_a_status_the_save_endpoint_accepts(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    """`open` is a real MarketStatus member, and the save request must not take
    it. Publishing goes through its own endpoint precisely so that no request
    can change a market's terms and expose it to traders in one call."""
    response = await client.post(
        "/markets", json=_payload(status="open"), headers=admin_headers
    )

    assert response.status_code == 422


async def test_the_documented_statuses_are_the_only_ones_offered(
    client: AsyncClient,
) -> None:
    """/docs is the contract Michelle codes against, so the schema has to say
    which two values she may send rather than listing all of MarketStatus."""
    schema = (await client.get("/openapi.json")).json()
    status_field = schema["components"]["schemas"]["MarketDraftRequest"]["properties"][
        "status"
    ]

    assert set(status_field["enum"]) == {"draft", "submitted"}


# --- reading --------------------------------------------------------------
async def test_the_creator_can_read_it_back(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    created = await client.post("/markets", json=_payload(), headers=admin_headers)
    market_id = created.json()["market"]["id"]

    response = await client.get(f"/markets/{market_id}", headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["id"] == market_id


async def test_another_administrator_gets_404_not_403(
    client: AsyncClient,
    admin_headers: dict[str, str],
    other_admin_headers: dict[str, str],
) -> None:
    """403 would confirm the market exists, which is most of what the draft
    visibility rule is meant to hide."""
    created = await client.post("/markets", json=_payload(), headers=admin_headers)
    market_id = created.json()["market"]["id"]

    response = await client.get(f"/markets/{market_id}", headers=other_admin_headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "market_not_found"


async def test_an_unknown_id_gives_the_same_404(
    client: AsyncClient, other_admin_headers: dict[str, str]
) -> None:
    response = await client.get(f"/markets/{uuid.uuid4()}", headers=other_admin_headers)

    assert response.json()["error"] == {
        "code": "market_not_found",
        "message": "No such market.",
    }


async def test_a_malformed_id_is_422_not_500(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    assert (await client.get("/markets/banana", headers=admin_headers)).status_code == 422


async def test_the_list_shows_only_my_markets(
    client: AsyncClient,
    admin_headers: dict[str, str],
    other_admin_headers: dict[str, str],
) -> None:
    await client.post("/markets", json=_payload(), headers=admin_headers)
    await client.post("/markets", json=_payload(), headers=other_admin_headers)

    mine = await client.get("/markets", headers=admin_headers)

    assert len(mine.json()["markets"]) == 1


async def test_the_list_is_a_summary_not_the_whole_market(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    await client.post("/markets", json=_payload(), headers=admin_headers)

    row = (await client.get("/markets", headers=admin_headers)).json()["markets"][0]

    assert set(row) == {
        "id",
        "draft_key",
        "status",
        "question",
        "close_time",
        "updated_at",
    }


# --- proposing an outcome [3.1] #9 ----------------------------------------
# No route closes a market — the clock does, and a background sweep writes it
# down — so this section reaches CLOSED through the service layer and then
# drives the route. `session` and `client` share one `clean_database`, so they
# are looking at the same rows.
async def _closed(session: AsyncSession, admin_id: uuid.UUID) -> tuple[str, str]:
    """A closed market of `admin_id`'s, and the id of its first outcome.

    The username matches the one `mint_token` signs by default, because the
    proposer's name is snapshotted from the token and one of the tests below
    reads it back off the response.
    """
    market = await closed_market(
        session, Actor(id=admin_id, username="ernest_t", role="admin")
    )
    return str(market.id), str(market.outcomes[0].id)


async def test_proposing_returns_the_pending_market(
    client: AsyncClient,
    session: AsyncSession,
    admin_id: uuid.UUID,
    admin_headers: dict[str, str],
) -> None:
    """The whole market comes back, not an acknowledgement, so the form can
    repaint without a second round trip."""
    market_id, outcome_id = await _closed(session, admin_id)

    response = await client.post(
        f"/markets/{market_id}/propose-outcome",
        json=_proposal(outcome_id),
        headers=admin_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == market_id
    assert body["status"] == "pending_resolution"
    assert body["proposed_outcome_id"] == outcome_id
    assert body["proposed_by_id"] == str(admin_id)
    assert body["proposed_by_username"] == "ernest_t"
    assert body["proposal_evidence_url"] is not None


async def test_proposed_at_carries_an_offset(
    client: AsyncClient,
    session: AsyncSession,
    admin_id: uuid.UUID,
    admin_headers: dict[str, str],
) -> None:
    """A new timestamp on the wire gets the same guard as every other one."""
    market_id, outcome_id = await _closed(session, admin_id)

    body = (
        await client.post(
            f"/markets/{market_id}/propose-outcome",
            json=_proposal(outcome_id),
            headers=admin_headers,
        )
    ).json()

    assert body["proposed_at"].endswith("Z") or "+" in body["proposed_at"]


async def test_proposing_on_an_open_market_is_409(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    market_id = await _submit(client, admin_headers)
    await client.post(f"/markets/{market_id}/publish", headers=admin_headers)
    outcome_id = (
        await client.get(f"/markets/{market_id}", headers=admin_headers)
    ).json()["outcomes"][0]["id"]

    response = await client.post(
        f"/markets/{market_id}/propose-outcome",
        json=_proposal(outcome_id),
        headers=admin_headers,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "market_not_closed"


async def test_proposing_twice_is_409(
    client: AsyncClient,
    session: AsyncSession,
    admin_id: uuid.UUID,
    admin_headers: dict[str, str],
) -> None:
    """A different code from the one above, because the remedy differs: wait
    for it to close, versus look at the proposal that is already there."""
    market_id, outcome_id = await _closed(session, admin_id)
    await client.post(
        f"/markets/{market_id}/propose-outcome",
        json=_proposal(outcome_id),
        headers=admin_headers,
    )

    response = await client.post(
        f"/markets/{market_id}/propose-outcome",
        json=_proposal(outcome_id),
        headers=admin_headers,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "market_pending_resolution"


async def test_a_trader_cannot_propose(
    client: AsyncClient,
    session: AsyncSession,
    admin_id: uuid.UUID,
    trader_headers: dict[str, str],
) -> None:
    market_id, outcome_id = await _closed(session, admin_id)

    response = await client.post(
        f"/markets/{market_id}/propose-outcome",
        json=_proposal(outcome_id),
        headers=trader_headers,
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_an_administrator"


async def test_another_administrator_cannot_propose(
    client: AsyncClient,
    session: AsyncSession,
    admin_id: uuid.UUID,
    other_admin_headers: dict[str, str],
) -> None:
    """404, not 403. A 403 would confirm the market exists."""
    market_id, outcome_id = await _closed(session, admin_id)

    response = await client.post(
        f"/markets/{market_id}/propose-outcome",
        json=_proposal(outcome_id),
        headers=other_admin_headers,
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "market_not_found"


async def test_proposing_on_an_unknown_id_is_404(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.post(
        f"/markets/{uuid.uuid4()}/propose-outcome",
        json=_proposal(uuid.uuid4()),
        headers=admin_headers,
    )

    assert response.status_code == 404


async def test_a_malformed_id_on_propose_is_422_not_500(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/markets/banana/propose-outcome",
        json=_proposal(uuid.uuid4()),
        headers=admin_headers,
    )

    assert response.status_code == 422


async def test_a_proposal_without_evidence_is_422_and_names_the_field(
    client: AsyncClient,
    session: AsyncSession,
    admin_id: uuid.UUID,
    admin_headers: dict[str, str],
) -> None:
    """This service's own 422, in the same envelope a refused submission uses,
    so the form needs no second renderer — only a different `code`."""
    market_id, outcome_id = await _closed(session, admin_id)

    response = await client.post(
        f"/markets/{market_id}/propose-outcome",
        json=_proposal(outcome_id, evidence_url=None, evidence_note=None),
        headers=admin_headers,
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "proposal_incomplete"
    assert [d["field"] for d in error["details"]] == ["evidence"]


async def test_a_body_with_no_winner_is_fastapis_422_not_ours(
    client: AsyncClient,
    session: AsyncSession,
    admin_id: uuid.UUID,
    admin_headers: dict[str, str],
) -> None:
    """Two 422s exist here and they do not look alike.

    A missing `winning_outcome_id` never reaches this service, so it comes back
    as FastAPI's `{"detail": [...]}`. The frontend branches on the presence of
    `error`, which is what docs/api/market-service.md tells it to do.
    """
    market_id, _ = await _closed(session, admin_id)

    response = await client.post(
        f"/markets/{market_id}/propose-outcome",
        json={"evidence_note": "MAS published 1.8% for December 2026."},
        headers=admin_headers,
    )

    assert response.status_code == 422
    assert "error" not in response.json()


async def test_a_refused_proposal_leaves_the_market_closed(
    client: AsyncClient,
    session: AsyncSession,
    admin_id: uuid.UUID,
    admin_headers: dict[str, str],
) -> None:
    """Atomicity on the real request path, like the blocked publish above.

    Not the service test of the same name. That one calls `propose_outcome`
    directly and rolls back by hand; nothing rolls back by hand on a real
    request. This asserts that the `get_session` dependency does it as the
    error propagates out through FastAPI — if it stopped, the service test
    would still pass and the route would leave a half-written market.
    """
    market_id, outcome_id = await _closed(session, admin_id)

    await client.post(
        f"/markets/{market_id}/propose-outcome",
        json=_proposal(outcome_id, evidence_url="nope", evidence_note=None),
        headers=admin_headers,
    )

    reread = await client.get(f"/markets/{market_id}", headers=admin_headers)
    assert reread.json()["status"] == "closed"
    assert reread.json()["proposed_at"] is None
