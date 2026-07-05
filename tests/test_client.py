"""Unit tests for TuttiClient (HTTP layer: headers, retries, error handling).

These mock tutti.ch's GraphQL endpoint with `responses` so no real network
call is made. Scraper's partitioning logic is tested separately in
test_scraper.py against a FakeClient, not against this real HTTP layer.
"""

import json

import pytest
import requests
import responses

import tutti_scraper as scraper


def success_body(data):
    return json.dumps({"data": data})


def errors_body(message="Internal Server Error"):
    return json.dumps({"errors": [{"message": message}], "data": None})


@responses.activate
def test_search_success_returns_unwrapped_search_result():
    responses.add(
        responses.POST,
        scraper.API_URL,
        body=success_body({"searchListingsByQuery": {"listings": {"totalCount": 1, "edges": []}}}),
        status=200,
    )
    client = scraper.TuttiClient(delay=0)

    result = client.search("velo")

    assert result == {"listings": {"totalCount": 1, "edges": []}}
    assert len(responses.calls) == 1


@responses.activate
def test_fetch_detail_success_returns_unwrapped_listing():
    responses.add(
        responses.POST,
        scraper.API_URL,
        body=success_body({"listing": {"listingID": "42"}}),
        status=200,
    )
    client = scraper.TuttiClient(delay=0)

    result = client.fetch_detail("42")

    assert result == {"listingID": "42"}


@responses.activate
def test_search_sends_expected_variables():
    responses.add(
        responses.POST,
        scraper.API_URL,
        body=success_body({"searchListingsByQuery": {"listings": {"totalCount": 0, "edges": []}}}),
        status=200,
    )
    client = scraper.TuttiClient(delay=0)

    client.search(
        "velo",
        category="bicycles",
        constraints={"prices": [{"key": "price"}]},
        offset=100,
        first=50,
        sort="PRICE",
        direction="ASCENDING",
    )

    sent = json.loads(responses.calls[0].request.body)
    assert sent["variables"] == {
        "q": "velo",
        "category": "bicycles",
        "constraints": {"prices": [{"key": "price"}]},
        "first": 50,
        "offset": 100,
        "sort": "PRICE",
        "direction": "ASCENDING",
    }


@responses.activate
def test_fetch_detail_sends_id_variable():
    responses.add(
        responses.POST,
        scraper.API_URL,
        body=success_body({"listing": {"listingID": "42"}}),
        status=200,
    )
    client = scraper.TuttiClient(delay=0)

    client.fetch_detail("42")

    sent = json.loads(responses.calls[0].request.body)
    assert sent["variables"] == {"id": "42"}


def test_default_headers_are_set():
    client = scraper.TuttiClient()
    headers = client.session.headers
    assert headers["Content-Type"] == "application/json"
    assert "Mozilla" in headers["User-Agent"]
    assert headers["Accept-Language"] == "de-CH"
    assert headers["X-Tutti-Source"] == "web LIVE"
    assert headers["X-Tutti-Client-Identifier"] == "web/1.0.0+env-live.git-0000000"


@pytest.mark.parametrize("lang,expected", [("de", "de-CH"), ("fr", "fr-CH"), ("it", "it-CH")])
def test_lang_option_sets_accept_language(lang, expected):
    client = scraper.TuttiClient(lang=lang)
    assert client.session.headers["Accept-Language"] == expected


@responses.activate
def test_fresh_hash_header_sent_per_request(no_sleep):
    responses.add(responses.POST, scraper.API_URL, body=errors_body(), status=200)
    responses.add(responses.POST, scraper.API_URL, body=errors_body(), status=200)
    responses.add(
        responses.POST,
        scraper.API_URL,
        body=success_body({"searchListingsByQuery": {"listings": {"totalCount": 0, "edges": []}}}),
        status=200,
    )
    client = scraper.TuttiClient(delay=0)

    client.search("velo")

    hashes = [call.request.headers["X-Tutti-Hash"] for call in responses.calls]
    assert len(hashes) == 3
    assert len(set(hashes)) == 3  # every attempt used a different hash


@responses.activate
def test_graphql_errors_are_retried_then_succeed(no_sleep):
    responses.add(responses.POST, scraper.API_URL, body=errors_body(), status=200)
    responses.add(
        responses.POST,
        scraper.API_URL,
        body=success_body({"searchListingsByQuery": {"listings": {"totalCount": 0, "edges": []}}}),
        status=200,
    )
    client = scraper.TuttiClient(delay=0)

    result = client.search("velo")

    assert result == {"listings": {"totalCount": 0, "edges": []}}
    assert len(responses.calls) == 2


@responses.activate
def test_graphql_errors_exhaust_retries_and_raise(no_sleep):
    for _ in range(3):
        responses.add(responses.POST, scraper.API_URL, body=errors_body("boom"), status=200)
    client = scraper.TuttiClient(delay=0, max_retries=3)

    with pytest.raises(scraper.TuttiError, match="giving up after 3 attempts"):
        client.search("velo")
    assert len(responses.calls) == 3


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
@responses.activate
def test_retryable_http_status_retried_then_succeeds(status, no_sleep):
    responses.add(responses.POST, scraper.API_URL, status=status)
    responses.add(
        responses.POST,
        scraper.API_URL,
        body=success_body({"searchListingsByQuery": {"listings": {"totalCount": 0, "edges": []}}}),
        status=200,
    )
    client = scraper.TuttiClient(delay=0)

    result = client.search("velo")

    assert result == {"listings": {"totalCount": 0, "edges": []}}
    assert len(responses.calls) == 2


@responses.activate
def test_retryable_http_status_exhausts_retries_and_raises_tutti_error(no_sleep):
    for _ in range(4):
        responses.add(responses.POST, scraper.API_URL, status=500)
    client = scraper.TuttiClient(delay=0, max_retries=4)

    with pytest.raises(scraper.TuttiError, match="giving up after 4 attempts"):
        client.search("velo")
    assert len(responses.calls) == 4


@responses.activate
def test_non_retryable_http_status_raises_immediately_without_retry():
    responses.add(responses.POST, scraper.API_URL, status=404)
    client = scraper.TuttiClient(delay=0)

    with pytest.raises(requests.HTTPError):
        client.search("velo")
    assert len(responses.calls) == 1


@responses.activate
def test_connection_error_is_retried_then_succeeds(no_sleep):
    responses.add(responses.POST, scraper.API_URL, body=requests.ConnectionError("boom"))
    responses.add(
        responses.POST,
        scraper.API_URL,
        body=success_body({"searchListingsByQuery": {"listings": {"totalCount": 0, "edges": []}}}),
        status=200,
    )
    client = scraper.TuttiClient(delay=0)

    result = client.search("velo")

    assert result == {"listings": {"totalCount": 0, "edges": []}}
    assert len(responses.calls) == 2


@responses.activate
def test_connection_error_exhausts_retries_and_raises_tutti_error(no_sleep):
    for _ in range(3):
        responses.add(responses.POST, scraper.API_URL, body=requests.ConnectionError("boom"))
    client = scraper.TuttiClient(delay=0, max_retries=3)

    with pytest.raises(scraper.TuttiError, match="giving up after 3 attempts"):
        client.search("velo")
    assert len(responses.calls) == 3
