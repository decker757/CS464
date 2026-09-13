"""Status codes, the error envelope and the response shape. [1.1] #1.

Business rules are asserted in unit_test/service. A test that goes through a
route to check a rule belongs there instead; what belongs here is everything
the frontend parses.
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient

from unit_test.conftest import future


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "draft_key": str(uuid.uuid4()),
        "status": "draft",
        "question": "Will Singapore core inflation be below 2% in December 2026?",
        "outcomes": [{"label": "Yes"}, {"label": "No"}],
        "close_time": future(days=30),
        "resolution_time": future(days=45),
        "resolution_criteria": "Resolves YES on the first published MAS print below 2.0%.",
        "resolution_sources": [{"url": "https://www.mas.gov.sg/statistics"}],
    }
    base.update(overrides)
    return base


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
