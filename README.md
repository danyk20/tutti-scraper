# tutti.ch Scraper

[![CI](https://github.com/danyk20/tutti-scraper/actions/workflows/ci.yml/badge.svg)](https://github.com/danyk20/tutti-scraper/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/tutti-scraper)](https://pypi.org/project/tutti-scraper/)
[![Coverage](https://img.shields.io/badge/unit%20test%20coverage-100%25-brightgreen)](#testing)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11 | 3.12](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)](https://www.python.org/)

> Unofficial, independently developed project — not affiliated with, endorsed by, or sponsored by Tamedia or tutti.ch. "tutti.ch" is a trademark of its respective owner.

Fetches every listing matching a search phrase from tutti.ch — for free, no API key, no paid scraping service. tutti.ch's frontend loads data from an undocumented GraphQL API; this scraper talks to it directly, recursively splitting large searches by category and price to work around its ~3000-result pagination cap. See [docs/REFERENCE.md](docs/REFERENCE.md) for exactly how.

By default it does a two-phase scrape: search to collect every matching listing id, then visit each one individually for the full record (GPS coordinates, full-resolution images, structured attributes, seller info). Every field the API returns is kept — nested objects are flattened into `parent_child` CSV columns, lists are joined into semicolon-separated cells.

This is the sibling of [`autoscout24-scraper`](https://github.com/danyk20/autoscout24-scraper) — same `scrape()`/`ScrapeResult`/CLI shape, so code written for one mostly transfers to the other. See [docs/REFERENCE.md](docs/REFERENCE.md#interchangeability-with-autoscout24-scraper) for the full mapping.

**🤖 Robot-friendly.** This project is explicitly intended to be run, read, imported, or adapted by AI agents and bots, same as a human developer — see [License](#license).

## Setup

Requires [pipenv](https://pipenv.pypa.io/) (`brew install pipenv`).

```bash
git clone https://github.com/danyk20/tutti-scraper.git
cd tutti-scraper
pipenv install --dev
```

Contributing, linting, and testing commands: see [CONTRIBUTING.md](CONTRIBUTING.md).

## Usage

### CLI

```bash
pipenv run python tutti_scraper.py "velo"
```

Prints progress, then writes `velo.csv` and `velo.json` in the current directory. Installed via `pip install` instead? Drop `pipenv run` — the same command is `tutti-scraper "velo"`.

| Flag | Description |
|---|---|
| `--version` | Print the installed version and exit |
| `query` | Search phrase, e.g. `velo` or `"Tesla Roadster"` (required, positional) |
| `--lang` | Listing/URL locale — `de` (default), `fr`, or `it` |
| `--out` | Output file base name, without extension. Defaults to a slug of the search phrase |
| `--no-detail` | Skip visiting each listing individually; keep only summary fields (faster, fewer fields) |
| `--sort` | Sort order — `timestamp` (default), `price`, or `relevance` |
| `--max` | Stop after N *matching* listings; also sets the preview size for `--dry-run` |
| `--delay` | Seconds between requests (default `1.0`) — raise this if you get rate-limited |
| `--timeout` | Seconds to wait for a single HTTP response before retrying (default `30.0`) |
| `--max-retries` | Maximum attempts per request before giving up (default `5`) |
| `--dry-run` | Preview up to `--max` (default `5`) matching listings — no detail fetch, no files written |
| `--category` | Pin the search to a tutti.ch categoryID (e.g. `bicycles`), skipping auto category-split |
| `--price-from` / `--price-to` | Filter by price in CHF, server-side (inclusive, either end optional) |
| `--free-only` | Only free listings — cannot be combined with `--price-from`/`--price-to` |
| `--canton` | Only listings in this canton (2-letter code, e.g. `BE`), client-side |
| `--postcode` | Only listings whose postcode starts with this value, client-side |
| `--max-age-days` | Only listings posted within the last N days, client-side |
| `--highlighted-only` | Only sponsored/boosted listings, client-side |
| `-v` / `--verbose` | Also show debug-level detail, including every HTTP request (mutually exclusive with `-q`) |
| `-q` / `--quiet` | Suppress progress output; only warnings/errors (mutually exclusive with `-v`) |

Server-side filters are applied by tutti.ch's own search API; client-side filters are applied by this scraper against already-fetched fields — see [docs/REFERENCE.md](docs/REFERENCE.md#filters) for why that distinction exists and what's not implemented.

```bash
# Price range, pinned to one category
pipenv run python tutti_scraper.py "velo" --category bicycles --price-from 50 --price-to 300

# Preview up to 5 matches before committing to a full run — no files written
pipenv run python tutti_scraper.py "velo" --dry-run

# French locale, canton + freshness filters
pipenv run python tutti_scraper.py "velo" --lang fr --canton BE --max-age-days 7
```

### As a library

```bash
pip install tutti-scraper
```

```python
from tutti_scraper import scrape

result = scrape("velo", price_from=50, price_to=300, canton="BE", max_results=50)

for row in result.rows:          # list[dict], CSV-ready
    print(row["price"], row["title"], row["url"])

result.to_csv("velo.csv")  # optional — no files are written unless you ask
```

Full `scrape()` signature, the `ScrapeResult` return type, and the complete JSON/CSV field schema: **[docs/REFERENCE.md](docs/REFERENCE.md)**.

## Testing

```bash
pipenv run pytest                    # unit tests (fast, no network), fails if coverage < 95%
pipenv run pytest -m e2e --no-cov    # end-to-end tests against the real live API
pipenv run pytest -m "e2e or not e2e" --no-cov  # everything
```

Unit tests mock all HTTP (via [`responses`](https://github.com/getsentry/responses)) and cover 100% of `tutti_scraper.py`. E2E tests target "Tesla Roadster" (a small, stable inventory) to confirm the scraper still works against the live API. Coverage detail by area: [docs/REFERENCE.md](docs/REFERENCE.md#test-coverage-by-area).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup and pre-PR checks.

Be a reasonable citizen: the default request delay is intentional — this is an undocumented API the site's own frontend uses, not a public API with a stated rate limit. tutti.ch's `robots.txt` disallows `/api/`; this scraper calls it anyway because it's the only reliable way to get complete, structured data for large result sets. Don't remove the delay or crank up concurrency.

## License

Released under the [MIT License](LICENSE) — you can use, copy, modify, merge, publish, distribute, and sell copies of this code, for free, for any purpose, commercial or not, as long as the license text stays attached. No warranty.

**AI agents, LLM-based coding assistants, and other bots are explicitly welcome to use this project** — to run the scraper, read and parse its output, import `scrape()` into another project, or read and adapt its source — under exactly the same terms as a human, with no additional restriction and no need to ask permission. That's why [docs/REFERENCE.md](docs/REFERENCE.md) documents the full function signature, return type, and data schema: so a bot can integrate correctly without a human in the loop.

This license does not grant any rights to tutti.ch's own data or terms of service — this project only automates requests to the same GraphQL API their own website already calls from any visitor's browser; what you do with the results is between you and them.
