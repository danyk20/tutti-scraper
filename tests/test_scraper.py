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


def test_run_skips_category_split_when_seed_category_given(small_limits):
    items = make_items(10, category="bikes") + make_items(2, category="cars")
    client = FakeClient(items)

    result = list(scraper.Scraper(client, "q", seed_category="bikes").run())

    assert len(result) == 10
    assert all(n["category"] == "bikes" for n in result)
    # every call must carry category="bikes" - never None (which would
    # trigger tutti's suggestedCategories-driven category split)
    assert all(c["category"] == "bikes" for c in client.calls)


def test_suggested_categories_captured_for_unfiltered_top_level_probe(small_limits):
    items = make_items(4, category="bikes") + make_items(4, category="cars")
    client = FakeClient(items)
    scraper_ = scraper.Scraper(client, "q")

    list(scraper_.run())

    assert {c["categoryID"] for c in scraper_.suggested_categories} == {"bikes", "cars"}


def test_suggested_categories_empty_when_category_is_seeded(small_limits):
    items = make_items(4, category="bikes")
    client = FakeClient(items)
    scraper_ = scraper.Scraper(client, "q", seed_category="bikes")

    list(scraper_.run())

    assert scraper_.suggested_categories == []


def test_allow_free_pass_false_suppresses_the_free_only_mop_up_pass(small_limits):
    items = make_items(10, category="bikes")  # includes a price=0 item
    client = FakeClient(items)

    list(scraper.Scraper(client, "q", seed_category="bikes", allow_free_pass=False).run())

    free_only_calls = [
        c for c in client.calls if c["constraints"] and c["constraints"].get("prices", [{}])[0].get("freeOnly")
    ]
    assert free_only_calls == []


def test_bisect_price_merges_base_constraints_instead_of_discarding_them(small_limits):
    # Regression test for a latent bug: _bisect_price used to always rebuild
    # constraints from scratch (price_constraint(pmin, pmax)), silently
    # dropping anything else in base_constraints. Only "prices" exists as a
    # constraint family in practice today, so a sentinel key simulates a
    # hypothetical second one to prove the merge preserves it.
    items = make_items(20, category="bikes")
    client = FakeClient(items)
    seed = {"prices": [{"key": "price"}], "sentinel": "keep-me"}

    scraper_ = scraper.Scraper(client, "q", seed_category="bikes", seed_constraints=seed)
    list(scraper_.run())

    constrained_calls = [c for c in client.calls if c["constraints"]]
    assert constrained_calls  # bisection actually happened
    assert all(c["constraints"].get("sentinel") == "keep-me" for c in constrained_calls)


def test_merge_constraints_helper():
    merge = scraper.Scraper._merge_constraints
    assert merge(None, {"a": 1}) == {"a": 1}
    assert merge({}, {"a": 1}) == {"a": 1}
    assert merge({"a": 1}, None) == {"a": 1}
    assert merge({"a": 1}, {}) == {"a": 1}
    assert merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}
    assert merge({"a": 1}, {"a": 2}) == {"a": 2}  # overlay wins on key conflict


def test_price_bounds_scope_bisection_to_the_seeded_window(small_limits):
    items = make_items(20, category="bikes")  # prices 0, 10, ..., 190
    client = FakeClient(items)
    seed = scraper.price_constraint(50, 150)

    scraper_ = scraper.Scraper(client, "q", seed_category="bikes", seed_constraints=seed, price_bounds=(50, 150))
    list(scraper_.run())

    price_calls = [
        c["constraints"]["prices"][0] for c in client.calls if c["constraints"] and c["constraints"].get("prices")
    ]
    assert price_calls
    for price_filter in price_calls:
        assert price_filter.get("min", 50) >= 50
        assert price_filter.get("max") is None or price_filter["max"] <= 150
