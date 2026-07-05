# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-07-05

Initial release.

### Added

- Scraper for tutti.ch listings, usable both as a CLI
  (`tutti-scraper` / `python tutti_scraper.py`) and as a library
  (`from tutti_scraper import scrape`).
- Search by any free-text phrase against tutti.ch's own GraphQL API
  (reverse-engineered from its compiled JS bundles, not a published
  schema).
- Recursive category/price-range partitioning that works around the
  API's ~3000-offset pagination cap, so result sets far larger than one
  page can still be fully covered; listings are de-duplicated by ID
  across every slice.
- Full-detail mode (default): visits every matching listing individually
  to extract GPS coordinates, full-resolution images, structured
  attributes, and richer seller info, generically flattened for CSV
  output; `--no-detail`/`detail=False` for a faster summary-only pass.
- Every listing's raw JSON and flattened CSV row both carry a direct
  `url` back to the original ad.
- `lang` parameter (default `"de"`) for `"fr"`/`"it"` tutti.ch locales.
- `ScrapeResult` dataclass return value (`.rows`, `.listings`,
  `.to_csv()`, `.to_json()`) for library use, with the CLI as a thin
  wrapper around the same `scrape()` function.
- Console script entry point (`tutti-scraper`) and `pip install` support
  via `pyproject.toml` packaging metadata; `--version` flag.
- Logging-based output (`-v`/`--verbose`, `-q`/`--quiet`) instead of bare
  `print()`, so library consumers can configure/suppress it via the
  standard `logging` module.
- Full type hints throughout, checked with mypy; linted and formatted
  with Ruff.
- Unit test suite (100% coverage, all HTTP mocked) plus a smaller
  end-to-end suite against the real live API, run on a separate weekly
  GitHub Actions schedule.
- CI (GitHub Actions) running lint, type-check, and the unit suite on
  every push/PR across Python 3.11 and 3.12.
- MIT license with an explicit statement welcoming AI agents/bots to use
  the project under the same terms as a human developer.
- Project governance docs: `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`,
  `SECURITY.md`, issue/PR templates.
- Deliberately interchangeable in shape with the sibling
  [`autoscout24-scraper`](https://github.com/danyk20/autoscout24-scraper)
  project: same `scrape()`/`ScrapeResult`/CLI/logging pattern, adapted to
  tutti.ch's free-text search and GraphQL API instead of AutoScout24's
  make/model REST API.
