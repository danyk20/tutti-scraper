import pytest

import tutti_scraper as scraper


def make_node(**seo):
    return {"listingID": "123", "seoInformation": seo}


@pytest.mark.parametrize("lang,slug_key", [("de", "deSlug"), ("fr", "frSlug"), ("it", "itSlug")])
def test_builds_url_from_matching_lang_slug(lang, slug_key):
    node = make_node(**{slug_key: "bern/tiere/katzen/some-listing"})

    url = scraper.listing_url(node, lang)

    assert url == f"https://www.tutti.ch/{lang}/vi/bern/tiere/katzen/some-listing/123"


def test_default_lang_is_de():
    node = make_node(deSlug="bern/tiere/katzen/some-listing")

    assert scraper.listing_url(node) == "https://www.tutti.ch/de/vi/bern/tiere/katzen/some-listing/123"


def test_missing_slug_for_requested_lang_returns_none():
    node = make_node(frSlug="some-slug")  # no deSlug

    assert scraper.listing_url(node, "de") is None


def test_missing_seo_information_key_returns_none():
    node = {"listingID": "123"}

    assert scraper.listing_url(node, "de") is None


def test_none_slug_value_returns_none():
    node = make_node(deSlug=None)

    assert scraper.listing_url(node, "de") is None
