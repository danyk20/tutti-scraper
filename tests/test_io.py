"""Unit tests for flatten_listing()/_scalarize()/order_fieldnames(),
save_csv()/save_json(), and the ScrapeResult dataclass."""

import csv
import json

import tutti_scraper as scraper


def test_scalarize_none_becomes_empty_string():
    assert scraper._scalarize(None) == ""


def test_scalarize_passes_through_plain_scalars():
    assert scraper._scalarize("x") == "x"
    assert scraper._scalarize(5) == 5
    assert scraper._scalarize(5.5) == 5.5
    assert scraper._scalarize(True) is True


def test_scalarize_dict_picks_recognized_main_field():
    assert scraper._scalarize({"name": "Bern", "id": 1}) == "Bern"
    assert scraper._scalarize({"label": "Cats", "id": 2}) == "Cats"
    assert scraper._scalarize({"src": "https://x/y.jpg"}) == "https://x/y.jpg"
    assert scraper._scalarize({"shortName": "BE", "name": "Bern"}) == "Bern"  # name checked first


def test_scalarize_dict_without_recognized_field_falls_back_to_json():
    result = scraper._scalarize({"foo": "bar"})
    assert json.loads(result) == {"foo": "bar"}


def test_scalarize_list_joins_with_semicolons():
    assert scraper._scalarize(["a", "b", 3]) == "a; b; 3"


def test_scalarize_unrecognized_type_falls_back_to_str():
    class Weird:
        def __str__(self):
            return "weird-value"

    assert scraper._scalarize(Weird()) == "weird-value"


def make_detail_item():
    return {
        "listingID": "42",
        "title": "Velo",
        "timestamp": "2026-07-05T15:00:00+02:00",
        "formattedPrice": "550.-",
        "highlighted": False,
        "postcodeInformation": {
            "postcode": "6197",
            "locationName": "Schangnau",
            "canton": {"shortName": "BE", "name": "Bern"},
        },
        "coordinates": {"latitude": 46.8, "longitude": 7.9},
        "sellerInfo": {"alias": "anna", "locationName": "Bern", "memberSince": "2020-01-01"},
        "primaryCategory": {"categoryID": "bicycles", "label": "Bicycles"},
        "thumbnail": {"normalRendition": {"src": "https://c.tutti.ch/thumbnail/1.jpg"}},
        "images": [
            {"rendition": {"src": "https://c.tutti.ch/big/1.jpg"}},
            {"rendition": {"src": "https://c.tutti.ch/big/2.jpg"}},
        ],
        "properties": [
            {"listingPropertyID": "1", "label": "Color", "text": "Red"},
            {"listingPropertyID": "2", "label": "Size", "text": "M"},
        ],
        "seoInformation": {"deSlug": "bern/velos/velo", "numericPrice": 550},
    }


def test_flatten_promotes_price_from_seo_information():
    flat = scraper.flatten_listing(make_detail_item())
    assert flat["price"] == 550
    assert flat["seoInformation_deSlug"] == "bern/velos/velo"
    assert flat["seoInformation_numericPrice"] == 550


def test_flatten_seller_info():
    flat = scraper.flatten_listing(make_detail_item())
    assert flat["sellerAlias"] == "anna"
    assert flat["sellerLocationName"] == "Bern"
    assert flat["sellerMemberSince"] == "2020-01-01"
    assert "sellerInfo" not in flat


def test_flatten_primary_category_prefers_label_over_id():
    flat = scraper.flatten_listing(make_detail_item())
    assert flat["category"] == "Bicycles"
    assert flat["categoryKey"] == "bicycles"


def test_flatten_primary_category_falls_back_to_id_when_no_label():
    item = make_detail_item()
    item["primaryCategory"] = {"categoryID": "bicycles"}
    flat = scraper.flatten_listing(item)
    assert flat["category"] == "bicycles"


def test_flatten_postcode_information():
    flat = scraper.flatten_listing(make_detail_item())
    assert flat["postcode"] == "6197"
    assert flat["locationName"] == "Schangnau"
    assert flat["canton"] == "Bern"
    assert flat["cantonKey"] == "BE"


def test_flatten_thumbnail_url():
    flat = scraper.flatten_listing(make_detail_item())
    assert flat["thumbnailURL"] == "https://c.tutti.ch/thumbnail/1.jpg"
    assert "thumbnail" not in flat


def test_flatten_images_joined_with_semicolons():
    flat = scraper.flatten_listing(make_detail_item())
    assert flat["images"] == "https://c.tutti.ch/big/1.jpg; https://c.tutti.ch/big/2.jpg"


def test_flatten_properties_joined_as_label_text_pairs():
    flat = scraper.flatten_listing(make_detail_item())
    assert flat["properties"] == "Color: Red; Size: M"


def test_flatten_generic_nested_dict_uses_parent_child_columns():
    flat = scraper.flatten_listing(make_detail_item())
    assert flat["coordinates_latitude"] == 46.8
    assert flat["coordinates_longitude"] == 7.9


def test_flatten_plain_scalar_fields_pass_through():
    flat = scraper.flatten_listing(make_detail_item())
    assert flat["listingID"] == "42"
    assert flat["title"] == "Velo"
    assert flat["formattedPrice"] == "550.-"
    assert flat["highlighted"] is False


def test_flatten_computes_url_fallback_when_missing():
    flat = scraper.flatten_listing(make_detail_item())
    assert flat["url"] == "https://www.tutti.ch/de/vi/bern/velos/velo/42"


def test_flatten_does_not_overwrite_existing_url():
    item = make_detail_item()
    item["url"] = "https://example.test/already-set"
    flat = scraper.flatten_listing(item)
    assert flat["url"] == "https://example.test/already-set"


def test_flatten_respects_lang_argument():
    item = make_detail_item()
    item["seoInformation"]["frSlug"] = "berne/velos/velo"
    flat = scraper.flatten_listing(item, lang="fr")
    assert flat["url"] == "https://www.tutti.ch/fr/vi/berne/velos/velo/42"


def test_order_fieldnames_pins_priority_fields_first_then_alphabetical():
    keys = {"zeta", "url", "alpha", "listingID", "title"}
    ordered = scraper.order_fieldnames(keys)
    assert ordered[:3] == ["listingID", "title", "url"]  # priority order, only those present
    assert ordered[3:] == sorted(["zeta", "alpha"])


def test_save_csv_writes_header_and_rows(tmp_path):
    rows = [
        {"listingID": "1", "title": "Velo", "price": 100},
        {"listingID": "2", "title": "Töffli", "extra": "only here"},
    ]
    path = tmp_path / "out.csv"

    scraper.save_csv(rows, str(path))

    with open(path, encoding="utf-8", newline="") as f:
        reader = list(csv.DictReader(f))
    assert len(reader) == 2
    assert reader[0]["title"] == "Velo"
    assert reader[0]["extra"] == ""  # missing key filled with empty string
    assert reader[1]["title"] == "Töffli"  # unicode preserved


def test_save_csv_with_no_rows_logs_warning_and_writes_nothing(tmp_path, caplog):
    path = tmp_path / "out.csv"

    with caplog.at_level("WARNING", logger="tutti_scraper"):
        scraper.save_csv([], str(path))

    assert "no rows to write" in caplog.text
    assert not path.exists()


def test_save_json_round_trips_unicode(tmp_path):
    rows = [{"listingID": "1", "title": "Töffli"}]
    path = tmp_path / "out.json"

    scraper.save_json(rows, str(path))

    assert json.loads(path.read_text(encoding="utf-8")) == rows
    assert "Töffli" in path.read_text(encoding="utf-8")  # not \u-escaped


def test_scrape_result_to_csv_writes_rows(tmp_path):
    result = scraper.ScrapeResult(
        query="velo",
        total_elements=1,
        listings=[{"listingID": "1"}],
        rows=[{"listingID": "1", "title": "Velo"}],
    )
    path = tmp_path / "out.csv"

    result.to_csv(str(path))

    with open(path, encoding="utf-8", newline="") as f:
        reader = list(csv.DictReader(f))
    assert reader[0]["title"] == "Velo"


def test_scrape_result_to_json_writes_listings_not_rows(tmp_path):
    result = scraper.ScrapeResult(
        query="velo",
        total_elements=1,
        listings=[{"listingID": "1", "nested": {"a": 1}}],
        rows=[{"listingID": "1", "nested_a": 1}],
    )
    path = tmp_path / "out.json"

    result.to_json(str(path))

    assert json.loads(path.read_text(encoding="utf-8")) == result.listings
