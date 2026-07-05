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
