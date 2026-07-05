"""Unit tests for search_listings() and visit_all_listings() - the two
building blocks scrape() composes. The recursive partitioning algorithm
search_listings() wraps is tested directly (via the Scraper class) in
test_scraper.py."""

from conftest import FakeClient

import tutti_scraper as scraper


def make_items(n, category="bikes"):
    return [{"listingID": f"{category}-{i:03d}", "category": category, "price": i * 10} for i in range(n)]


def test_search_listings_returns_all_nodes():
    client = FakeClient(make_items(5))

    nodes = scraper.search_listings(client, "velo", verbose=False)

    assert {n["listingID"] for n in nodes} == {it["listingID"] for it in make_items(5)}


def test_search_listings_stops_at_max_results():
    client = FakeClient(make_items(10))

    nodes = scraper.search_listings(client, "velo", max_results=3, verbose=False)

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
