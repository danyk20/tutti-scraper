"""Unit tests for main() (CLI argument handling) and run_cli() (exit codes).

scrape() itself is monkeypatched here so these tests never touch the
network; they only verify that CLI flags are translated into the right
scrape() call and that files get written to the right place.
"""

import csv
import json

import pytest
import requests

import tutti_scraper as scraper


@pytest.fixture
def fake_scrape(monkeypatch):
    calls = {}

    def _fake_scrape(query, **kwargs):
        calls["query"] = query
        calls["kwargs"] = kwargs
        return scraper.ScrapeResult(
            query=query,
            total_elements=1,
            listings=[{"listingID": "1", "url": "https://www.tutti.ch/de/vi/x/1"}],
            rows=[{"listingID": "1", "title": "Velo", "price": 100, "url": "https://www.tutti.ch/de/vi/x/1"}],
            lang=kwargs.get("lang", "de"),
        )

    monkeypatch.setattr(scraper, "scrape", _fake_scrape)
    return calls


def test_main_translates_required_flags(fake_scrape, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    exit_code = scraper.main(["velo"])

    assert exit_code == 0
    assert fake_scrape["query"] == "velo"
    assert fake_scrape["kwargs"]["lang"] == "de"
    assert fake_scrape["kwargs"]["detail"] is True  # detail is on unless --no-detail
    assert fake_scrape["kwargs"]["sort"] == "timestamp"
    assert fake_scrape["kwargs"]["max_results"] is None
    assert fake_scrape["kwargs"]["delay"] == 1.0
    # every new filter defaults to "off"
    assert fake_scrape["kwargs"]["category"] is None
    assert fake_scrape["kwargs"]["price_from"] is None
    assert fake_scrape["kwargs"]["price_to"] is None
    assert fake_scrape["kwargs"]["free_only"] is False
    assert fake_scrape["kwargs"]["canton"] is None
    assert fake_scrape["kwargs"]["postcode"] is None
    assert fake_scrape["kwargs"]["max_age_days"] is None
    assert fake_scrape["kwargs"]["highlighted_only"] is False


def test_main_translates_all_new_filter_flags(fake_scrape, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    scraper.main(
        [
            "velo",
            "--category", "bicycles",
            "--price-from", "10",
            "--price-to", "100",
            "--canton", "BE",
            "--postcode", "3000",
            "--max-age-days", "7",
            "--highlighted-only",
        ]
    )  # fmt: skip

    kwargs = fake_scrape["kwargs"]
    assert kwargs["category"] == "bicycles"
    assert kwargs["price_from"] == 10
    assert kwargs["price_to"] == 100
    assert kwargs["canton"] == "BE"
    assert kwargs["postcode"] == "3000"
    assert kwargs["max_age_days"] == 7
    assert kwargs["highlighted_only"] is True


def test_main_translates_free_only_flag(fake_scrape, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    scraper.main(["velo", "--free-only"])

    assert fake_scrape["kwargs"]["free_only"] is True


def test_main_translates_timeout_and_max_retries_flags(fake_scrape, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    scraper.main(["velo", "--timeout", "10", "--max-retries", "2"])

    assert fake_scrape["kwargs"]["timeout"] == 10.0
    assert fake_scrape["kwargs"]["max_retries"] == 2


def test_main_default_timeout_and_max_retries(fake_scrape, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    scraper.main(["velo"])

    assert fake_scrape["kwargs"]["timeout"] == 30.0
    assert fake_scrape["kwargs"]["max_retries"] == 5


def test_main_dry_run_writes_no_files(fake_scrape, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    scraper.main(["velo", "--dry-run"])

    assert list(tmp_path.iterdir()) == []


def test_main_dry_run_calls_scrape_with_detail_false(fake_scrape, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    scraper.main(["velo", "--dry-run"])

    assert fake_scrape["kwargs"]["detail"] is False


def test_main_dry_run_defaults_preview_size_to_5(fake_scrape, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    scraper.main(["velo", "--dry-run"])

    assert fake_scrape["kwargs"]["max_results"] == 5


def test_main_dry_run_respects_max_as_preview_size(fake_scrape, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    scraper.main(["velo", "--dry-run", "--max", "2"])

    assert fake_scrape["kwargs"]["max_results"] == 2


def test_main_dry_run_logs_preview_rows(fake_scrape, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    scraper.main(["velo", "--dry-run"])

    out = capsys.readouterr().out
    assert "1 matching listing(s) previewed" in out
    assert "Velo" in out
    assert "https://www.tutti.ch/de/vi/x/1" in out


def test_main_dry_run_logs_suggested_categories_when_present(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    def _fake_scrape(query, **kwargs):
        return scraper.ScrapeResult(
            query=query,
            total_elements=1,
            listings=[{"listingID": "1"}],
            rows=[{"listingID": "1", "title": "Velo"}],
            suggested_categories=[{"categoryID": "bicycles", "label": "Velos"}],
        )

    monkeypatch.setattr(scraper, "scrape", _fake_scrape)

    scraper.main(["velo", "--dry-run"])

    assert "Suggested categories: bicycles" in capsys.readouterr().out


def test_main_dry_run_returns_0(fake_scrape, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert scraper.main(["velo", "--dry-run"]) == 0


def test_main_propagates_scrape_validation_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def _raise(*a, **k):
        raise ValueError("free_only cannot be combined with price_from/price_to")

    monkeypatch.setattr(scraper, "scrape", _raise)

    with pytest.raises(ValueError, match="free_only cannot be combined"):
        scraper.main(["velo", "--free-only", "--price-from", "10"])


def test_main_no_detail_flag_sets_detail_false(fake_scrape, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    scraper.main(["velo", "--no-detail"])

    assert fake_scrape["kwargs"]["detail"] is False


def test_main_passes_custom_lang(fake_scrape, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    scraper.main(["velo", "--lang", "fr"])

    assert fake_scrape["kwargs"]["lang"] == "fr"


def test_main_passes_sort_max_and_delay(fake_scrape, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    scraper.main(["velo", "--sort", "price", "--max", "5", "--delay", "2.5"])

    assert fake_scrape["kwargs"]["sort"] == "price"
    assert fake_scrape["kwargs"]["max_results"] == 5
    assert fake_scrape["kwargs"]["delay"] == 2.5


def test_main_default_out_is_slug_of_query(fake_scrape, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    scraper.main(["Tesla Roadster!!"])

    assert (tmp_path / "tesla-roadster.csv").exists()
    assert (tmp_path / "tesla-roadster.json").exists()


def test_main_custom_out_base(fake_scrape, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    scraper.main(["velo", "--out", "my_search"])

    assert (tmp_path / "my_search.csv").exists()
    assert (tmp_path / "my_search.json").exists()


def test_main_writes_csv_and_json_matching_scrape_result(fake_scrape, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    scraper.main(["velo", "--out", "out"])

    with open(tmp_path / "out.csv", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["title"] == "Velo"

    data = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert data == [{"listingID": "1", "url": "https://www.tutti.ch/de/vi/x/1"}]


def test_main_verbose_true_logs_done_summary(fake_scrape, tmp_path, monkeypatch, capsys):
    # main() configures its own real stdout/stderr handlers (propagate=False),
    # so its output shows up via capsys, not caplog (which relies on
    # propagation) - see _configure_cli_logging()'s docstring.
    monkeypatch.chdir(tmp_path)

    scraper.main(["velo", "--out", "out"])

    out = capsys.readouterr().out
    assert "Done. 1 unique listings found." in out
    assert "out.csv" in out
    assert "out.json" in out


def test_main_quiet_suppresses_info_logs(fake_scrape, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    scraper.main(["velo", "--out", "out", "-q"])

    assert "Done." not in capsys.readouterr().out


def test_verbose_and_quiet_are_mutually_exclusive(fake_scrape, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        scraper.main(["velo", "-v", "-q"])
    assert excinfo.value.code == 2


def test_version_flag_prints_version_and_exits(capsys):
    with pytest.raises(SystemExit) as excinfo:
        scraper.main(["--version"])
    assert excinfo.value.code == 0
    assert scraper.__version__ in capsys.readouterr().out


def test_run_cli_returns_0_on_success(fake_scrape, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert scraper.run_cli(["velo"]) == 0


def test_run_cli_returns_1_on_tutti_error(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    def _raise(*a, **k):
        raise scraper.TuttiError("boom")

    monkeypatch.setattr(scraper, "scrape", _raise)

    assert scraper.run_cli(["velo"]) == 1


def test_run_cli_returns_1_on_network_error(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    def _raise(*a, **k):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(scraper, "scrape", _raise)

    assert scraper.run_cli(["velo"]) == 1


def test_run_cli_returns_130_on_keyboard_interrupt(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    def _raise(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(scraper, "scrape", _raise)

    assert scraper.run_cli(["velo"]) == 130


@pytest.mark.parametrize(
    "text,expected",
    [
        ("velo", "velo"),
        ("Tesla Roadster", "tesla-roadster"),
        ("Tesla Roadster!!", "tesla-roadster"),
        ("  spaced  out  ", "spaced-out"),
        ("!!!", "listings"),  # nothing left after stripping -> fallback
        ("", "listings"),
    ],
)
def test_slugify(text, expected):
    assert scraper._slugify(text) == expected
