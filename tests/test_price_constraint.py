import tutti_scraper as scraper


def test_no_args_gives_free_only_false_and_no_bounds():
    assert scraper.price_constraint() == {"prices": [{"key": "price", "freeOnly": False}]}


def test_min_only():
    assert scraper.price_constraint(pmin=10) == {"prices": [{"key": "price", "freeOnly": False, "min": 10}]}


def test_max_only():
    assert scraper.price_constraint(pmax=500) == {"prices": [{"key": "price", "freeOnly": False, "max": 500}]}


def test_min_and_max():
    assert scraper.price_constraint(pmin=100, pmax=500) == {
        "prices": [{"key": "price", "freeOnly": False, "min": 100, "max": 500}]
    }


def test_free_only():
    assert scraper.price_constraint(free_only=True) == {"prices": [{"key": "price", "freeOnly": True}]}


def test_zero_bounds_are_kept_not_treated_as_missing():
    # 0 is a valid price bound and must not be dropped by an `is not None` check gone wrong.
    result = scraper.price_constraint(pmin=0, pmax=0)
    assert result["prices"][0]["min"] == 0
    assert result["prices"][0]["max"] == 0
