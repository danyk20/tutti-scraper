"""Unit tests for search_listings() and visit_all_listings() - the two
building blocks scrape() composes. The recursive partitioning algorithm
search_listings() wraps is tested directly (via the Scraper class) in
test_scraper.py. This file covers search_listings()'s own responsibilities:
building the seed for Scraper from price_from/price_to/free_only/category,
applying the client-side canton/postcode/max_age_days/highlighted_only
predicate, and surfacing suggested_categories.
"""

from datetime import UTC, datetime, timedelta

from conftest import FakeClient

import tutti_scraper as scraper


def make_items(n, category="bikes"):
    return [{"listingID": f"{category}-{i:03d}", "category": category, "price": i * 10} for i in range(n)]


def test_search_listings_returns_all_nodes():
    client = FakeClient(make_items(5))

    nodes, _ = scraper.search_listings(client, "velo", verbose=False)

    assert {n["listingID"] for n in nodes} == {it["listingID"] for it in make_items(5)}


def test_search_listings_stops_at_max_results():
    client = FakeClient(make_items(10))

    nodes, _ = scraper.search_listings(client, "velo", max_results=3, verbose=False)

    assert len(nodes) == 3


def test_search_listings_logs_progress_periodically(small_limits, caplog):
    # small_limits shrinks PAGE_SIZE to 2, so 4 items cross one "page" boundary.
    client = FakeClient(make_items(4))

    with caplog.at_level("INFO", logger="tutti_scraper"):
        scraper.search_listings(client, "velo", verbose=True)

    assert "listings found so far" in caplog.text


def test_search_listings_forwards_sort_to_scraper():
    client = FakeClient(make_items(2))

    scraper.search_listings(client, "velo", sort="PRICE", verbose=False)

    assert all(c["sort"] == "PRICE" for c in client.calls if c["first"] != 1)


def test_search_listings_returns_suggested_categories_for_unfiltered_query():
    items = make_items(2, category="bikes") + make_items(2, category="cars")
    client = FakeClient(items)

    _, suggested = scraper.search_listings(client, "velo", verbose=False)

    assert {c["categoryID"] for c in suggested} == {"bikes", "cars"}


def test_search_listings_category_param_pins_and_skips_split(small_limits):
    items = make_items(10, category="bikes") + make_items(2, category="cars")
    client = FakeClient(items)

    nodes, suggested = scraper.search_listings(client, "velo", category="bikes", verbose=False)

    assert len(nodes) == 10
    assert all(n["category"] == "bikes" for n in nodes)
    assert suggested == []  # category was pinned - nothing to suggest


def test_search_listings_price_range_is_server_side_filtered():
    items = make_items(5)  # prices 0, 10, 20, 30, 40

    client = FakeClient(items)
    nodes, _ = scraper.search_listings(client, "velo", price_from=10, price_to=30, verbose=False)

    # [price_from, price_to) - matches Scraper's own bisection convention
    assert {n["price"] for n in nodes} == {10, 20}


def test_search_listings_free_only_returns_only_zero_priced():
    items = make_items(5)  # includes a price=0 item
    client = FakeClient(items)

    nodes, _ = scraper.search_listings(client, "velo", free_only=True, verbose=False)

    assert {n["price"] for n in nodes} == {0}


def test_predicate_filters_by_canton_case_insensitively():
    items = [
        {"listingID": "in-be", "category": "bikes", "price": 0, "postcodeInformation": {"canton": {"shortName": "BE"}}},
        {"listingID": "in-zh", "category": "bikes", "price": 0, "postcodeInformation": {"canton": {"shortName": "ZH"}}},
    ]
    client = FakeClient(items)

    nodes, _ = scraper.search_listings(client, "velo", canton="be", verbose=False)

    assert [n["listingID"] for n in nodes] == ["in-be"]


def test_predicate_filters_by_postcode_prefix():
    items = [
        {"listingID": "3000", "category": "bikes", "price": 0, "postcodeInformation": {"postcode": "3000"}},
        {"listingID": "8000", "category": "bikes", "price": 0, "postcodeInformation": {"postcode": "8000"}},
    ]
    client = FakeClient(items)

    nodes, _ = scraper.search_listings(client, "velo", postcode="30", verbose=False)

    assert [n["listingID"] for n in nodes] == ["3000"]


def test_predicate_filters_by_max_age_days():
    now = datetime.now(UTC)
    fresh = (now - timedelta(days=1)).isoformat()
    stale = (now - timedelta(days=30)).isoformat()
    items = [
        {"listingID": "fresh", "category": "bikes", "price": 0, "timestamp": fresh},
        {"listingID": "stale", "category": "bikes", "price": 0, "timestamp": stale},
    ]
    client = FakeClient(items)

    nodes, _ = scraper.search_listings(client, "velo", max_age_days=7, verbose=False)

    assert [n["listingID"] for n in nodes] == ["fresh"]


def test_predicate_filters_by_highlighted_only():
    items = [
        {"listingID": "boosted", "category": "bikes", "price": 0, "highlighted": True},
        {"listingID": "plain", "category": "bikes", "price": 0, "highlighted": False},
    ]
    client = FakeClient(items)

    nodes, _ = scraper.search_listings(client, "velo", highlighted_only=True, verbose=False)

    assert [n["listingID"] for n in nodes] == ["boosted"]


def test_predicate_treats_missing_fields_as_non_matching():
    items = [{"listingID": "1", "category": "bikes", "price": 0}]  # no postcodeInformation/timestamp at all
    client = FakeClient(items)

    nodes, _ = scraper.search_listings(client, "velo", canton="BE", verbose=False)
    assert nodes == []

    nodes, _ = scraper.search_listings(client, "velo", max_age_days=7, verbose=False)
    assert nodes == []


def test_max_results_counts_post_filter_matches_not_raw_nodes():
    items = [
        {"listingID": f"item-{i}", "category": "bikes", "price": 0, "highlighted": i % 2 == 0} for i in range(6)
    ]  # 3 highlighted, 3 not, interleaved
    client = FakeClient(items)

    nodes, _ = scraper.search_listings(client, "velo", highlighted_only=True, max_results=2, verbose=False)

    assert len(nodes) == 2
    assert all(n["highlighted"] for n in nodes)


def test_visit_all_listings_merges_detail_fields_without_mutating_input():
    listings = [{"listingID": "1", "title": "Velo"}]
    client = FakeClient([])

    result = scraper.visit_all_listings(client, listings, verbose=False)

    assert result[0]["listingID"] == "1"
    assert result[0]["detail"] is True
    assert "detail" not in listings[0]  # original summary dict left untouched


def test_visit_all_listings_keeps_summary_fields_when_detail_fetch_fails(caplog):
    listings = [{"listingID": "broken", "title": "Velo"}]
    client = FakeClient([], broken_ids=["broken"])

    with caplog.at_level("WARNING", logger="tutti_scraper"):
        result = scraper.visit_all_listings(client, listings, verbose=False)

    assert result[0] == {"listingID": "broken", "title": "Velo"}
    assert "broken" in caplog.text


def test_visit_all_listings_logs_progress(caplog):
    listings = [{"listingID": "1"}, {"listingID": "2"}]
    client = FakeClient([])

    with caplog.at_level("INFO", logger="tutti_scraper"):
        scraper.visit_all_listings(client, listings, verbose=True)

    assert "Visited 1/2 listings" in caplog.text
    assert "Visited 2/2 listings" in caplog.text


def test_visit_all_listings_silent_when_not_verbose(caplog):
    listings = [{"listingID": "1"}]
    client = FakeClient([])

    with caplog.at_level("INFO", logger="tutti_scraper"):
        scraper.visit_all_listings(client, listings, verbose=False)

    assert "Visited" not in caplog.text
