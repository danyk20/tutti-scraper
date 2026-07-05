## What does this change and why?

## Checklist

- [ ] `pipenv run ruff check .` and `pipenv run ruff format --check .` pass
- [ ] `pipenv run mypy tutti_scraper.py` passes
- [ ] `pipenv run pytest` passes with coverage still at 100%
- [ ] Added/updated tests for any behavior change
- [ ] If this touches request/response handling, ran `pipenv run pytest -m e2e --no-cov` against the real API
- [ ] Updated the README if this changes CLI flags, the `scrape()`/`ScrapeResult` API, or the documented data shape
