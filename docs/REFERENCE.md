# Reference

Full API surface, return types, and data schema for anyone integrating with
this project as a library — a human developer or an AI agent — without
reading the source. See [README.md](../README.md) for the pitch, install,
and CLI usage.

## How the API is discovered and used

tutti.ch's own frontend loads its data from a **GraphQL API** at
`https://www.tutti.ch/api/v10/graphql` — not a published/documented schema,
but the query documents this scraper sends were recovered from tutti.ch's
compiled JS bundles and verified against the live API. It needs a specific
header set (`X-Tutti-Hash` — any UUID, regenerated per request —
`X-Tutti-Source`, `X-Tutti-Client-Identifier`), which `TuttiClient` sets
automatically.

| Operation | Purpose |
|---|---|
| `searchListingsByQuery(query, category, constraints)` | the search, paginated up to 100 results at a time, plus tutti.ch's own suggested sub-categories for the query |
| `listing(listingID)` | full detail record for one listing — visited once per listing by default |

**The pagination cap, and how this scraper works around it.** A single
`(query, category, constraints, sort)` combination can only be paged up to
an offset of about 3000 — larger offsets return a server error, not more
results. For a query with more matches than that, this scraper recursively
partitions the search:

1. Search the phrase with no filters. If the total is small enough, page
   through it directly.
2. Otherwise, split by every category tutti.ch suggests for the query, and
   repeat per category.
3. If a single category is still too large, bisect it by price range
   (binary search on min/max price) until each slice is small enough to
   page through.
4. A dedicated free-only pass and an ascending-sort sweep mop up listings
   that a price filter or a single sort order might otherwise miss.

Listings are de-duplicated by ID across all of this, so overlap between
slices is harmless. In pathological cases (thousands of listings clustered
at the exact same price, in the exact same category) full coverage still
isn't guaranteed — the scraper logs a warning whenever a slice it visited
was larger than what's reachable.

**Two-phase scraping.** The search operation only returns a summary per
listing. To get everything (GPS coordinates, full-resolution images,
structured attributes, richer seller info), the scraper visits each
listing individually, one by one, via its detail operation, after the
search phase has collected every id — one extra HTTP request per listing,
with a delay between requests. Use `--no-detail` to skip this and keep
only the fast summary fields.

## Filters

`scrape()` and the CLI support two kinds of filter, depending on whether
tutti.ch's API can apply them itself:

- **Server-side** (`category`, `price_from`/`price_to`, `free_only`) — sent
  to tutti.ch as part of the search itself, reusing the same `category`/
  `constraints` machinery the pagination workaround above needs anyway.
  Pinning `category` also skips the auto category-split step, since
  there's nothing left to discover. Use `ScrapeResult.suggested_categories`
  from an unfiltered search to find valid `category` values for your
  query.
- **Client-side** (`canton`, `postcode`, `max_age_days`, `highlighted_only`)
  — applied locally against already-fetched summary fields, since tutti.ch
  has no server-side constraint for them. `max_results` still counts
  *matching* listings, not raw ones fetched before filtering.

`free_only=True` cannot be combined with `price_from`/`price_to` (raises
`ValueError` before any network call) — a price range has no meaning for
free listings.

**Not implemented** (bigger lifts, out of scope for now): true radius/
location search (would need reverse-engineering tutti.ch's place-name-to-
locality-ID resolution) and per-category structured attribute filters like
color/size/brand (each category has its own dynamic filter schema that
would need to be queried and mapped).

## Locales

Every function and the CLI accept a `lang` (default `"de"`) — `"fr"` and
`"it"` are both live tutti.ch locales, used both for the `Accept-Language`
header sent to the API and for which localized URL slug gets used to build
each listing's `url`.

## `scrape()` signature

```python
def scrape(
    query: str,                      # free-text search phrase, e.g. "velo" or "Tesla Roadster"
    *,
    lang: str = "de",                # "de" (default), "fr", or "it"
    detail: bool = True,             # visit every listing individually for full fields (slower)
    sort: str = "timestamp",         # "timestamp" (default), "price", or "relevance"
    max_results: int | None = None,  # stop after this many unique *matching* listings, if given
    delay: float = 1.0,              # seconds between HTTP requests
    verbose: bool = True,            # emit progress via the "tutti_scraper" logger at INFO level
    client: TuttiClient | None = None,  # reuse a client across calls if given
    timeout: float = 30.0,           # seconds per HTTP response before retrying
    max_retries: int = 5,            # max attempts per request before giving up
    category: str | None = None,     # pin to this categoryID, server-side (skips auto category-split)
    price_from: int | None = None,   # CHF, inclusive, server-side
    price_to: int | None = None,     # CHF, inclusive, server-side
    free_only: bool = False,         # only free listings, server-side (see Filters)
    canton: str | None = None,       # 2-letter canton code, client-side
    postcode: str | None = None,     # postcode prefix match, client-side
    max_age_days: int | None = None, # only listings posted within the last N days, client-side
    highlighted_only: bool = False,  # only sponsored/boosted listings, client-side
) -> ScrapeResult:
    ...
```

Raises `ValueError` immediately (before any network call) if `price_from >
price_to`, if `free_only` is combined with `price_from`/`price_to`, if
`max_age_days` isn't positive, or if `postcode` isn't numeric. Raises
`TuttiError` on unrecoverable GraphQL/HTTP errors from tutti.ch after
retries are exhausted, and `requests.RequestException` subclasses on
unrecoverable network errors.

**Logging.** Library code never configures logging itself (no
`basicConfig`, no handlers) — it only emits through
`logging.getLogger("tutti_scraper")`, same as any well-behaved library. That
means if you call `scrape()` from your own script with no logging
configuration of your own, `verbose=True`'s progress messages exist but
won't be visible anywhere, by design — Python's standard "libraries don't
talk unless you ask them to" behavior. To see them:

```python
import logging
logging.basicConfig(level=logging.INFO)  # now scrape()'s progress is visible
```

The CLI is the one place that *does* configure real handlers automatically
(`-v`/`-q`) — that's the only difference between running this as a script
versus importing it.

## `ScrapeResult` — the return value

```python
@dataclass
class ScrapeResult:
    query: str              # the search phrase, as requested
    total_elements: int     # number of unique listings found by the search phase
    listings: list[dict]    # raw API objects — see "Data structure" below
    rows: list[dict]        # flattened dicts, one per listing, CSV-ready, sorted by price ascending
    lang: str                # locale that was scraped, e.g. "de"
    suggested_categories: list[dict[str, str]]  # tutti.ch's suggested sub-categories for `query`;
                                                 # empty if `category` was given (nothing left to suggest)

    def to_csv(self, path: str) -> None: ...   # writes self.rows
    def to_json(self, path: str) -> None: ...  # writes self.listings
```

`len(result.rows) == len(result.listings) == result.total_elements` always
holds (barring `--no-detail`/`detail=False`, where they still match — detail
mode only adds fields, it never drops or adds listings).

## Interchangeability with autoscout24-scraper

Both projects share the same shape on purpose, so code written against one
transfers to the other with minimal changes:

| Concept | `autoscout24-scraper` | `tutti-scraper` |
|---|---|---|
| Search call | `scrape(make, model, **filters)` | `scrape(query, **filters)` |
| Return value | `ScrapeResult` (`.rows`, `.listings`, `.to_csv()`, `.to_json()`) | same |
| Common row keys | `row["price"]`, `row["url"]` | same |
| Detail toggle | `detail=True` / `--no-detail` | same |
| Price filter | `price_from`/`price_to` (CHF) | same names, same meaning |
| Reusable transport | `session: requests.Session \| None` | `client: TuttiClient \| None` (see below) |
| Locale/region | `domain: str = "ch"` | `lang: str = "de"` |
| Logging | `logging.getLogger("autoscout24_scraper")`, `-v`/`-q` CLI flags | `logging.getLogger("tutti_scraper")`, same flags |
| CLI entry point | `main(argv=None)` / `run_cli(argv=None)` | same |

Two things are genuinely different, not just renamed, because the two sites'
domains actually differ:

- **`client` vs. `session`.** tutti.ch's API needs a specific header set
  regenerated per request (see [How the API is discovered and used](#how-the-api-is-discovered-and-used)),
  so tutti's reusable transport object is a small `TuttiClient` class
  wrapping a session, not a bare `requests.Session`. If you're writing code
  that should work against either scraper, treat this parameter as opaque
  (pass `None` to let each library build its own) rather than constructing
  it yourself.
- **Search shape.** AutoScout24 also lets you filter by mileage/year server
  side, because it's a structured make/model catalog — those don't have a
  tutti equivalent (there's no "mileage" on a general classifieds site).
  tutti.ch adds its own extras instead: `category`/`canton`/`postcode`/
  `max_age_days`/`highlighted_only`/`free_only`, plus `sort`/`max_results`
  for the free-text case (see [Filters](#filters) above).

If you're adapting this project to a *third* data source, following this
same shape (`scrape()` → `ScrapeResult`, `flatten_listing()`/`save_csv()`/
`save_json()`/`order_fieldnames()`, `main()`/`run_cli()`, logging via
`logging.getLogger(...)`) is the recommended path — several of tutti's
utility functions (`_scalarize()`, `order_fieldnames()`, `save_csv()`,
`save_json()`) are generic enough to reuse close to verbatim.

## Data structure

### JSON (`result.listings` / the `.json` file)

The JSON file (and `ScrapeResult.listings`) is a **JSON array of listing
objects**, one per ad found. Every listing object always includes:

| Field | Type | Description |
|---|---|---|
| `listingID` | `string` | tutti.ch's internal listing id |
| `url` | `string \| null` | **Full URL of the original ad** on tutti.ch, e.g. `https://www.tutti.ch/de/vi/bern/velos/velo/76699338` — added by this scraper (the raw API response does not include it), so you can always click straight back to the source listing. `null` if tutti.ch didn't return a URL slug for the requested `lang` |
| `title` | `string` | |
| `body` | `string` | ad description text |
| `timestamp` | `string` | ISO 8601 |
| `formattedPrice` | `string \| null` | display price as tutti.ch renders it, e.g. `"550.-"` |
| `postcodeInformation` | `object` | `{"postcode", "locationName", "canton": {"shortName", "name"}}` |
| `primaryCategory` | `object` | `{"categoryID", ...}` (richer in detail shape, see below) |
| `sellerInfo` | `object` | `{"alias", "logoURL", ...}` (richer in detail shape, see below) |

There are two possible **shapes** for the rest of the object, depending on
whether detail mode ran:

- **Summary shape** (`detail=False` / `--no-detail`): the search operation's
  fields only — includes a `thumbnail` (small renditions), but no GPS
  coordinates, no full-resolution images, no structured attributes.
- **Detail shape** (`detail=True`, the default): adds `coordinates`
  (`{"latitude", "longitude"}`), `images` (`list[{"rendition": {"src"}}]`,
  full resolution), `properties` (`list[{"listingPropertyID", "label",
  "text"}]`, structured attributes specific to the listing's category),
  `address`, `phoneInfo` (`{"isMobile", "phoneHash"}`), a richer
  `sellerInfo` (adds `locationName`, `url`, `memberSince`,
  `publicAccountID`), and a richer `primaryCategory` (adds `label`,
  `parent`). `seoInformation` also gains `numericPrice` (`number \| null`)
  — the raw numeric price the display-only `formattedPrice` string doesn't
  give you, used to sort `result.rows` and promoted to a flat `price`
  column in CSV output.

There is no published/versioned schema for these objects — the tables above
reflect the fields observed in practice as of this writing, recovered from
tutti.ch's own compiled JS. Treat unknown/missing fields defensively
(`.get(...)`, not `[...]`) since tutti.ch can add or omit fields per
listing.

### CSV (`result.rows` / the `.csv` file)

The CSV is a **flattened** version of the same data — one row per listing,
same rows/listings correspondence and order. Flattening rules (also
available programmatically as `flatten_listing()`):

- `seoInformation.numericPrice` is promoted to a top-level `price` column
  (also present as `seoInformation_numericPrice`).
- `sellerInfo` becomes `sellerAlias`, `sellerLocationName`,
  `sellerMemberSince`.
- `primaryCategory` becomes `category` (label if available, else the raw
  category id) and `categoryKey` (always the raw id).
- `postcodeInformation` becomes `postcode`, `locationName`, `canton`,
  `cantonKey`.
- `thumbnail` becomes `thumbnailURL`.
- `images` is joined into one semicolon-separated cell of image URLs.
- `properties` is joined into one semicolon-separated cell of
  `"label: text"` pairs.
- Any other nested object becomes `parent_child` columns, e.g.
  `coordinates.latitude` → `coordinates_latitude`.
- `url` is always present as its own column (same value as the JSON `url`
  field described above).
- Columns are the union of every field seen across all rows (heterogeneous
  listings don't crash the writer — missing values are an empty string),
  with `listingID, title, price, formattedPrice, category, postcode,
  locationName, canton, timestamp, sellerAlias, url` pinned first and
  everything else sorted alphabetically after them.

## Test coverage by area

| Area | Unit tests | E2E tests |
|---|---|---|
| `TuttiClient._post` | retry-then-succeed and exhausted-retries paths for GraphQL errors, 429/5xx, connection errors; no retry on 4xx; fresh hash per attempt | — |
| `Scraper` (partitioning) | direct paging, category split (once, not recursively), price bisection, free-only pass, ascending sweep, dedup, depth/range safety valve + warning | — |
| `search_listings` / `visit_all_listings` | max-results early stop (post-filter), progress logging, detail-fetch-failure fallback, canton/postcode/max-age/highlighted-only predicate, suggested-categories surfacing | real search + detail fetch |
| `flatten_listing` / `_scalarize` / `order_fieldnames` | every branch (nested dicts, lists, missing/unrecognized types) | implicitly, via real data |
| `save_csv` / `save_json` / `ScrapeResult` | heterogeneous rows, unicode, empty input | round-trip against real files |
| `scrape()` | orchestration, sorting, client reuse/construction, verbose logging | full real pipeline, with and without `detail` |
| `main()` / `run_cli()` | every CLI flag, default vs. custom output filenames, all three exit-code paths | real subprocess-equivalent run, real error exit code |

The unit suite covers 100% of `tutti_scraper.py` (the one line excluded via
`# pragma: no cover` is the `if __name__ == "__main__":` guard itself,
which is exercised for real by the e2e suite's CLI run instead).
