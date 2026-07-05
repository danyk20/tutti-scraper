# Contributing

Thanks for considering a contribution — human or AI agent, both welcome (see
the [License](README.md#license) section of the README).

## Dev setup

```bash
git clone https://github.com/danyk20/tutti-scraper.git
cd tutti-scraper
pipenv install --dev
```

## Before opening a PR

```bash
pipenv run ruff check .          # lint
pipenv run ruff format .         # format
pipenv run mypy tutti_scraper.py # type-check
pipenv run pytest                # unit tests, must stay at 100% coverage
```

If your change touches request/response handling against the real API,
also run the end-to-end suite (real network calls, ~10s):

```bash
pipenv run pytest -m e2e --no-cov
```

## Expectations

- **Every behavior change needs a test.** The unit suite mocks all HTTP
  (via `responses`) and enforces 100% coverage — a change without a test
  will fail CI on that basis alone.
- **Keep `verbose`/logging output backward compatible** unless the PR is
  specifically about changing it — other code (and the e2e/CLI tests)
  depends on the current message wording.
- If tutti.ch changes its GraphQL schema, required headers, or pagination
  behavior, prefer fixing the affected function directly over adding a
  workaround — the module docstring in `tutti_scraper.py` documents the
  current API shape, and it was reverse-engineered from tutti.ch's own
  compiled JS bundles rather than any published schema.
- Keep the change minimal and focused; this is a small single-file utility
  by design (see the README's [Notes](README.md#notes) section for what's
  intentionally out of scope, e.g. concurrency, Docker, a database layer).
- This project aims to stay interchangeable in shape with its sibling
  [`autoscout24-scraper`](https://github.com/danyk20/autoscout24-scraper)
  (same `scrape()`/`ScrapeResult`/CLI pattern, different data source) —
  when both projects have an equivalent concept, prefer matching its name
  and behavior over inventing a new one, unless tutti.ch's domain genuinely
  requires something different.

## Questions / bug reports

Open a GitHub issue using the bug report template — include the exact
command you ran and, if relevant, the raw API response you got back.

## Releasing (maintainer only)

Publishing to PyPI is automated via `.github/workflows/release.yml` using
PyPI Trusted Publishing (no API tokens stored anywhere) — pushing a tag is
the only manual step:

1. Bump `__version__` in `tutti_scraper.py`.
2. Add a new entry at the top of `CHANGELOG.md` (Keep a Changelog format).
3. Commit those two changes, then tag and push:
   ```bash
   git commit -am "Release vX.Y.Z"
   git tag vX.Y.Z
   git push origin master
   git push origin vX.Y.Z
   ```
4. The release workflow verifies `__version__` matches the tag (fails fast
   if they disagree), builds, publishes to TestPyPI, then to real PyPI.
   Watch the Actions tab.
5. To dry-run the pipeline without a real release, push a pre-release tag
   instead (e.g. `vX.Y.Z-rc1`) — it publishes to TestPyPI only and never
   reaches real PyPI, since the version/tag check and the real-PyPI job
   both key off an exact `vX.Y.Z` tag.

One-time setup this depends on (not done yet for this repo — see the
release workflow's own notes): a Trusted Publisher registered on both
pypi.org and test.pypi.org for this repo, and matching GitHub Environments
named `pypi`/`testpypi`.
