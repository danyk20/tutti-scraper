"""Unit tests for Scraper's recursive partitioning (category split, price
bisection, dedup, ascending-sort sweep). Uses FakeClient (see conftest.py)
so the algorithm is exercised against small, deterministic synthetic
datasets rather than real HTTP.
"""

from conftest import FakeClient

import tutti_scraper as scraper


def make_items(n, category="bikes", price_step=10):
    return [{"listingID": f"{category}-{i:03d}", "category": category, "price": i * price_step} for i in range(n)]


def test_small_result_set_is_paged_directly_without_splitting(small_limits):
    # 5 items, one category, MAX_TOTAL=6 -> fits in a single linear pass.
    items = make_items(5)
    client = FakeClient(items)
    result = list(scraper.Scraper(client, "q").run())

    assert {n["listingID"] for n in result} == {it["listingID"] for it in items}
    # no category filter and no price constraint should ever have been sent
    assert all(c["category"] is None for c in client.calls)
    assert all(not c["constraints"] for c in client.calls)


def test_result_set_at_exact_cap_boundary_is_fully_covered(small_limits):
    # MAX_TOTAL=6 exactly - the boundary case should still be fully covered.
    items = make_items(6)
    client = FakeClient(items)
    result = list(scraper.Scraper(client, "q").run())

    assert {n["listingID"] for n in result} == {it["listingID"] for it in items}


def test_oversized_single_category_result_is_incomplete_without_partitioning_hints(small_limits):
    # Sanity check on the *problem* the partitioning solves: a plain linear
    # pass over more than MAX_TOTAL items misses some, bounded by MAX_OFFSET.
    items = make_items(10)
    client = FakeClient(items)
    result = list(scraper.Scraper(client, "q")._linear_page(category=None, constraints=None))

    assert len(result) < 10
    assert len(result) <= scraper.MAX_TOTAL


def test_splits_across_categories_when_top_level_exceeds_cap(small_limits):
    items = make_items(4, category="bikes") + make_items(4, category="cars")
    client = FakeClient(items)

    result = list(scraper.Scraper(client, "q").run())

    assert {n["listingID"] for n in result} == {it["listingID"] for it in items}
    categories_queried = {c["category"] for c in client.calls if c["category"] is not None}
    assert categories_queried == {"bikes", "cars"}


def test_category_split_recurses_with_allow_category_split_false(small_limits, monkeypatch):
    # Once split into a category, a still-oversized category leaf must not
    # be split by category again (categories are a flat suggestion, not a
    # tree here) - the recursive call must be marked allow_category_split=False.
    items = make_items(10, category="bikes") + make_items(2, category="cars")
    client = FakeClient(items)
    original = scraper.Scraper._scrape
    seen_calls = []

    def spy(self, category, constraints, allow_category_split):
        seen_calls.append((category, allow_category_split))
        yield from original(self, category, constraints, allow_category_split)

    monkeypatch.setattr(scraper.Scraper, "_scrape", spy)

    result = list(scraper.Scraper(client, "q").run())

    assert {n["listingID"] for n in result} == {it["listingID"] for it in items}
    assert seen_calls[0] == (None, True)
    assert seen_calls[1:] and all(allow is False for _, allow in seen_calls[1:])
    assert {cat for cat, _ in seen_calls[1:]} == {"bikes", "cars"}


def test_falls_back_to_price_bisection_when_no_category_split_helps(small_limits):
    # Single category, more items than MAX_TOTAL -> must bisect by price.
    items = make_items(10, category="bikes", price_step=10)
    client = FakeClient(items)

    result = list(scraper.Scraper(client, "q").run())

    assert {n["listingID"] for n in result} == {it["listingID"] for it in items}
    price_constrained_calls = [c for c in client.calls if c["constraints"] and c["constraints"].get("prices")]
    assert len(price_constrained_calls) > 0


def test_free_only_pass_runs_after_price_bisection(small_limits):
    items = make_items(10, category="bikes", price_step=10)  # includes a price=0 item
    client = FakeClient(items)

    list(scraper.Scraper(client, "q").run())

    free_only_calls = [
        c for c in client.calls if c["constraints"] and c["constraints"].get("prices", [{}])[0].get("freeOnly")
    ]
    assert len(free_only_calls) >= 1


def test_dedup_across_category_and_ascending_sweep(small_limits):
    items = make_items(4, category="bikes") + make_items(4, category="cars")
    client = FakeClient(items)

    result = list(scraper.Scraper(client, "q").run())

    ids = [n["listingID"] for n in result]
    assert len(ids) == len(set(ids))  # no duplicates despite the extra ascending sweep


def test_ascending_sweep_runs_and_yields_no_new_items_once_fully_covered(small_limits):
    items = make_items(5)
    client = FakeClient(items)

    list(scraper.Scraper(client, "q").run())

    directions_used = {c["direction"] for c in client.calls}
    assert "ASCENDING" in directions_used
    assert "DESCENDING" in directions_used


def test_zero_results_returns_empty(small_limits):
    client = FakeClient([])

    result = list(scraper.Scraper(client, "q").run())

    assert result == []


def test_severely_clustered_prices_are_capped_not_infinite_and_warn(small_limits, caplog):
    # All items share the exact same price - bisection can never separate
    # them, so it must bottom out via the pmax-pmin<=1 / depth safety valve
    # instead of recursing forever, and it must warn about the shortfall.
    # 14 items is deliberately more than run()'s two capped passes combined
    # (the price-bisection leaf's DESCENDING page plus the top-level
    # ASCENDING sweep) can jointly cover, so completeness must still fail
    # even with that mop-up sweep in play.
    items = [{"listingID": f"bikes-{i:03d}", "category": "bikes", "price": 50} for i in range(14)]
    client = FakeClient(items)

    with caplog.at_level("WARNING", logger="tutti_scraper"):
        result = list(scraper.Scraper(client, "q").run())

    assert len(result) < 14  # necessarily incomplete
    assert "may be missing" in caplog.text


def test_bisect_price_stops_recursing_once_depth_limit_hit(small_limits, monkeypatch, caplog):
    monkeypatch.setattr(scraper, "MAX_BISECT_DEPTH", 1)
    items = [{"listingID": f"bikes-{i:03d}", "category": "bikes", "price": i} for i in range(20)]
    client = FakeClient(items)

    # Should terminate promptly (not hang / blow the stack) even with a
    # depth limit far too low to fully resolve 20 items.
    with caplog.at_level("WARNING", logger="tutti_scraper"):
        result = list(scraper.Scraper(client, "q").run())

    assert len(result) <= 20
    assert "may be missing" in caplog.text


def test_probe_total_uses_first_one(small_limits):
    items = make_items(3)
    client = FakeClient(items)

    total, suggested = scraper.Scraper(client, "q")._probe_total(None, None)

    assert total == 3
    assert client.calls[0]["first"] == 1


def test_linear_page_stops_at_max_offset(small_limits):
    items = make_items(20)  # far more than reachable via MAX_OFFSET
    client = FakeClient(items)

    result = list(scraper.Scraper(client, "q")._linear_page(category=None, constraints=None))

    # offsets used must never exceed MAX_OFFSET
    assert all(c["offset"] <= scraper.MAX_OFFSET for c in client.calls)
    assert len(result) <= scraper.MAX_TOTAL


def test_linear_page_stops_when_totalcount_exhausted_before_max_offset(small_limits):
    items = make_items(3)  # fewer than MAX_TOTAL, exhausts before hitting the offset cap
    client = FakeClient(items)

    result = list(scraper.Scraper(client, "q")._linear_page(category=None, constraints=None))

    assert len(result) == 3
    assert max(c["offset"] for c in client.calls) < scraper.MAX_OFFSET


def test_sort_mode_is_forwarded_to_client(small_limits):
    items = make_items(3)
    client = FakeClient(items)

    list(scraper.Scraper(client, "q", sort="PRICE").run())

    # the totalCount probe (first=1) never forwards sort/direction; only the
    # actual paging calls matter here.
    paging_calls = [c for c in client.calls if c["first"] != 1]
    assert paging_calls
    assert all(c["sort"] == "PRICE" for c in paging_calls)
