"""End-to-end tests: real network calls against the live tutti.ch API.

These are marked with @pytest.mark.e2e and excluded by default (see
pyproject.toml addopts). Run them explicitly with:

    pipenv run pytest -m e2e --no-cov

They target "Tesla Roadster" specifically because its inventory on tutti.ch
is small and stable, so the full detail-visiting pipeline and a real CLI
run both complete in a few seconds without hammering the API. They assert
on structural contract (fields present, requests succeed) rather than
exact counts/content, since tutti.ch listing data changes constantly.
"""

import json

import pytest

import tutti_scraper as scraper

pytestmark = pytest.mark.e2e


def test_real_search_returns_expected_shape():
    client = scraper.TuttiClient(delay=0.5)

    result = client.search("velo", first=3)

    assert result["listings"]["totalCount"] > 0
    edges = result["listings"]["edges"]
    assert len(edges) == 3
    node = edges[0]["node"]
    for field in ("listingID", "title", "timestamp", "primaryCategory", "seoInformation"):
        assert field in node


def test_real_detail_returns_expected_shape():
    client = scraper.TuttiClient(delay=0.5)
    node = client.search("velo", first=1)["listings"]["edges"][0]["node"]

    detail = client.fetch_detail(node["listingID"])

    assert detail["listingID"] == node["listingID"]
    for field in ("coordinates", "images", "sellerInfo", "properties"):
        assert field in detail


def test_scrape_real_pipeline_with_and_without_detail():
    fast = scraper.scrape("Tesla Roadster", detail=False, delay=0.5, verbose=False)
    assert fast.total_elements > 0
    assert len(fast.rows) == fast.total_elements
    assert "coordinates" not in fast.listings[0]

    full = scraper.scrape("Tesla Roadster", detail=True, delay=0.5, verbose=False)
    assert full.total_elements == fast.total_elements
    assert "coordinates" in full.listings[0]


def test_scrape_real_pipeline_respects_category_price_and_canton_filters():
    result = scraper.scrape(
        "velo",
        category="bicycles",
        price_from=20,
        price_to=200,
        canton="BE",
        max_results=5,
        delay=0.5,
        verbose=False,
    )

    assert result.total_elements > 0
    assert result.suggested_categories == []  # category was pinned - nothing to suggest
    for row in result.rows:
        assert row["categoryKey"] == "bicycles"
        assert row["cantonKey"] == "BE"
        if row["price"] is not None:  # numericPrice can be absent on some listings
            assert 20 <= row["price"] < 200


def test_cli_end_to_end_writes_csv_and_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    exit_code = scraper.run_cli(["Tesla Roadster", "--max", "2", "--delay", "0.5"])

    assert exit_code == 0
    csv_path = tmp_path / "tesla-roadster.csv"
    json_path = tmp_path / "tesla-roadster.json"
    assert csv_path.exists()
    assert json_path.exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert 0 < len(data) <= 2
    for record in data:
        assert "listingID" in record
        assert "url" in record
