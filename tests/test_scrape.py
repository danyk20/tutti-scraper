"""Unit tests for the scrape() library entry point (orchestration logic).

These monkeypatch the building-block functions (search_listings,
visit_all_listings) so we can test the orchestration in isolation from
HTTP. End-to-end tests that hit the real API live in test_e2e.py. The
partitioning algorithm itself is tested in test_scraper.py, and
flatten_listing()/save_csv()/save_json() in test_io.py.
"""

import pytest

import tutti_scraper as scraper


@pytest.fixture
def patched_pipeline(monkeypatch):
    """Patch out every network-touching function scrape() calls, and record
    how they were called."""
    calls = {}

    def fake_search_listings(client, query, **kwargs):
        calls["search_listings"] = dict(client=client, query=query, **kwargs)
        nodes = [
            {"listingID": "1", "title": "A", "seoInformation": {"deSlug": "a", "numericPrice": 200}},
            {"listingID": "2", "title": "B", "seoInformation": {"deSlug": "b", "numericPrice": 100}},
        ]
        return nodes, [{"categoryID": "bikes", "label": "Bikes"}]

    def fake_visit_all_listings(client, listings, *, verbose=True):
        calls["visit_all_listings"] = dict(client=client, listings=listings, verbose=verbose)
        return [dict(item, detail=True) for item in listings]

    monkeypatch.setattr(scraper, "search_listings", fake_search_listings)
    monkeypatch.setattr(scraper, "visit_all_listings", fake_visit_all_listings)
    return calls


def test_scrape_happy_path_returns_scrape_result(patched_pipeline):
    result = scraper.scrape("velo", verbose=False)

    assert isinstance(result, scraper.ScrapeResult)
    assert result.query == "velo"
    assert result.total_elements == 2
    assert len(result.rows) == 2
    assert len(result.listings) == 2
    assert result.lang == "de"


def test_scrape_calls_search_listings_with_query_and_uppercased_sort(patched_pipeline):
    scraper.scrape("velo", sort="price", verbose=False)

    assert patched_pipeline["search_listings"]["query"] == "velo"
    assert patched_pipeline["search_listings"]["sort"] == "PRICE"


def test_scrape_passes_max_results_through(patched_pipeline):
    scraper.scrape("velo", max_results=5, verbose=False)

    assert patched_pipeline["search_listings"]["max_results"] == 5


def test_scrape_populates_suggested_categories_from_search_listings(patched_pipeline):
    result = scraper.scrape("velo", verbose=False)

    assert result.suggested_categories == [{"categoryID": "bikes", "label": "Bikes"}]


def test_scrape_defaults_to_de_lang(patched_pipeline):
    result = scraper.scrape("velo", verbose=False)

    assert result.lang == "de"
    assert result.listings[0]["url"] == "https://www.tutti.ch/de/vi/a/1"


def test_scrape_passes_custom_lang_through(patched_pipeline):
    result = scraper.scrape("velo", lang="fr", verbose=False)

    assert result.lang == "fr"
    # the fixture's nodes only carry a deSlug, so under lang="fr" the
    # embedded url is None - that's the expected, honest behavior.
    assert result.listings[0]["url"] is None


def test_scrape_detail_true_by_default_visits_every_listing(patched_pipeline):
    scraper.scrape("velo", verbose=False)

    assert "visit_all_listings" in patched_pipeline
    assert len(patched_pipeline["visit_all_listings"]["listings"]) == 2


def test_scrape_detail_false_skips_visiting(patched_pipeline):
    result = scraper.scrape("velo", detail=False, verbose=False)

    assert "visit_all_listings" not in patched_pipeline
    # rows/listings should come straight from the (summary-shaped) search results
    assert result.listings[0]["listingID"] in ("1", "2")


def test_scrape_rows_sorted_ascending_by_price(patched_pipeline):
    result = scraper.scrape("velo", verbose=False)

    prices = [row["price"] for row in result.rows]
    assert prices == sorted(prices)
    assert prices == [100, 200]


def test_scrape_rows_with_missing_price_sort_last(monkeypatch):
    def fake_search_listings(client, query, **kwargs):
        return [
            {"listingID": "1", "seoInformation": {}},  # no numericPrice
            {"listingID": "2", "seoInformation": {"numericPrice": 50}},
        ], []

    monkeypatch.setattr(scraper, "search_listings", fake_search_listings)

    result = scraper.scrape("velo", detail=False, verbose=False)

    assert result.rows[-1]["price"] in (None, "")  # missing price sorts to the end
    assert result.rows[0]["price"] == 50


def test_scrape_verbose_false_logs_nothing(patched_pipeline, caplog):
    with caplog.at_level("INFO", logger="tutti_scraper"):
        scraper.scrape("velo", verbose=False)

    assert caplog.text == ""


def test_scrape_verbose_true_logs_progress(patched_pipeline, caplog):
    with caplog.at_level("INFO", logger="tutti_scraper"):
        scraper.scrape("velo", verbose=True)

    assert "Searching tutti.ch for 'velo'" in caplog.text
    assert "Visiting each of 2 listings" in caplog.text


def test_scrape_reuses_provided_client(patched_pipeline):
    sentinel_client = object()

    scraper.scrape("velo", verbose=False, client=sentinel_client)

    assert patched_pipeline["search_listings"]["client"] is sentinel_client


def test_scrape_constructs_client_from_lang_and_delay_when_none_given(monkeypatch, patched_pipeline):
    created = {}

    class SpyClient:
        def __init__(self, lang="de", delay=1.0):
            created["lang"] = lang
            created["delay"] = delay

    monkeypatch.setattr(scraper, "TuttiClient", SpyClient)

    scraper.scrape("velo", lang="it", delay=2.5, verbose=False)

    assert created == {"lang": "it", "delay": 2.5}


def test_scrape_passes_all_new_filters_through_to_search_listings(patched_pipeline):
    scraper.scrape(
        "velo",
        verbose=False,
        category="bicycles",
        price_from=10,
        price_to=100,
        canton="BE",
        postcode="3000",
        max_age_days=7,
        highlighted_only=True,
    )

    kwargs = patched_pipeline["search_listings"]
    assert kwargs["category"] == "bicycles"
    assert kwargs["price_from"] == 10
    assert kwargs["price_to"] == 100
    assert kwargs["canton"] == "BE"
    assert kwargs["postcode"] == "3000"
    assert kwargs["max_age_days"] == 7
    assert kwargs["highlighted_only"] is True


def test_scrape_passes_free_only_through(patched_pipeline):
    scraper.scrape("velo", verbose=False, free_only=True)

    assert patched_pipeline["search_listings"]["free_only"] is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"price_from": 100, "price_to": 50},
    ],
)
def test_scrape_raises_on_inverted_price_range_before_any_network_call(patched_pipeline, kwargs):
    with pytest.raises(ValueError, match="must be <="):
        scraper.scrape("velo", verbose=False, **kwargs)

    # validation must happen before any search_listings()/visit_all_listings() call
    assert patched_pipeline == {}


def test_scrape_allows_equal_price_from_and_price_to(patched_pipeline):
    # equal bounds are a valid (if narrow) range, not an error
    scraper.scrape("velo", verbose=False, price_from=50, price_to=50)

    assert patched_pipeline["search_listings"]["price_from"] == 50


@pytest.mark.parametrize("kwargs", [{"free_only": True, "price_from": 10}, {"free_only": True, "price_to": 50}])
def test_scrape_raises_on_free_only_combined_with_price_range(patched_pipeline, kwargs):
    with pytest.raises(ValueError, match="free_only cannot be combined"):
        scraper.scrape("velo", verbose=False, **kwargs)

    assert patched_pipeline == {}


@pytest.mark.parametrize("max_age_days", [0, -1])
def test_scrape_raises_on_non_positive_max_age_days(patched_pipeline, max_age_days):
    with pytest.raises(ValueError, match="max_age_days must be positive"):
        scraper.scrape("velo", verbose=False, max_age_days=max_age_days)

    assert patched_pipeline == {}


@pytest.mark.parametrize("postcode", ["ab12", "30-00", ""])
def test_scrape_raises_on_non_numeric_postcode(patched_pipeline, postcode):
    with pytest.raises(ValueError, match="postcode must be numeric"):
        scraper.scrape("velo", verbose=False, postcode=postcode)

    assert patched_pipeline == {}


def test_scrape_accepts_numeric_postcode(patched_pipeline):
    scraper.scrape("velo", verbose=False, postcode="3000")

    assert patched_pipeline["search_listings"]["postcode"] == "3000"
