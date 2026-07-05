"""Shared fixtures for the unit test suite.

FakeClient stands in for TuttiClient in Scraper/CLI tests: it implements the
same .search()/.fetch_detail() interface, backed by an in-memory list of
synthetic listings, so the partitioning algorithm (category split, price
bisection, dedup) can be exercised deterministically without any real HTTP.
HTTP-level behavior of TuttiClient itself (retries, headers) is tested
separately in test_client.py against a mocked API endpoint.
"""

import pytest

import tutti_scraper as scraper


class FakeClient:
    """In-memory stand-in for TuttiClient.

    `items` are dicts with at least "listingID", "category", "price". Filters
    mirror the subset of GraphQL constraints the real Scraper actually sends
    (category, and constraints.prices[0].{min,max,freeOnly}).
    """

    def __init__(self, items, broken_ids=None, boom_on_call=None):
        self.items = items
        self.broken_ids = set(broken_ids or ())
        self.boom_on_call = boom_on_call
        self.calls = []
        self.detail_calls = []
        self._call_count = 0

    def _filtered(self, category, constraints):
        items = self.items
        if category is not None:
            items = [it for it in items if it["category"] == category]
        if constraints and constraints.get("prices"):
            price_filter = constraints["prices"][0]
            if price_filter.get("freeOnly"):
                items = [it for it in items if it["price"] == 0]
            else:
                pmin = price_filter.get("min")
                pmax = price_filter.get("max")
                if pmin is not None:
                    items = [it for it in items if it["price"] >= pmin]
                if pmax is not None:
                    items = [it for it in items if it["price"] < pmax]
        return items

    def search(
        self, query, category=None, constraints=None, offset=0, first=100, sort="TIMESTAMP", direction="DESCENDING"
    ):
        self._call_count += 1
        if self.boom_on_call is not None and self._call_count >= self.boom_on_call:
            raise RuntimeError("boom")
        self.calls.append(
            {
                "query": query,
                "category": category,
                "constraints": constraints,
                "offset": offset,
                "first": first,
                "sort": sort,
                "direction": direction,
            }
        )
        matching = self._filtered(category, constraints)
        ordered = sorted(matching, key=lambda it: it["listingID"])
        if direction == "DESCENDING":
            ordered = list(reversed(ordered))
        page = ordered[offset : offset + first]
        suggested = []
        if category is None:
            cats = sorted({it["category"] for it in matching})
            suggested = [{"categoryID": c, "label": c} for c in cats]
        return {
            "listings": {
                "totalCount": len(matching),
                "edges": [{"node": dict(it)} for it in page],
            },
            "suggestedCategories": suggested,
        }

    def fetch_detail(self, listing_id):
        self.detail_calls.append(listing_id)
        if listing_id in self.broken_ids:
            raise scraper.TuttiError(f"boom for {listing_id}")
        return {"listingID": listing_id, "detail": True}


@pytest.fixture
def small_limits(monkeypatch):
    """Shrink the pagination/bisection constants so partitioning tests can
    use tiny synthetic datasets and still exercise every code path fast."""
    monkeypatch.setattr(scraper, "PAGE_SIZE", 2)
    monkeypatch.setattr(scraper, "MAX_OFFSET", 4)
    monkeypatch.setattr(scraper, "MAX_TOTAL", 6)  # MAX_OFFSET + PAGE_SIZE
    monkeypatch.setattr(scraper, "MAX_PRICE", 100)
    monkeypatch.setattr(scraper, "MAX_BISECT_DEPTH", 10)


@pytest.fixture
def no_sleep(monkeypatch):
    """Make time.sleep a no-op so tests exercising retry/backoff run instantly."""
    monkeypatch.setattr(scraper.time, "sleep", lambda *_args, **_kwargs: None)
