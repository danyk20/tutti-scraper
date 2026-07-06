#!/usr/bin/env python3
"""
tutti.ch listing scraper.

Talks directly to the GraphQL API tutti.ch's own website calls
(https://www.tutti.ch/api/v10/graphql) rather than scraping rendered HTML.
The query documents used here (SEARCH_QUERY, DETAIL_QUERY) were recovered
from tutti.ch's compiled JS bundles and verified against the live API; the
GraphQL schema itself isn't publicly documented.

Discovered endpoints:
  POST https://www.tutti.ch/api/v10/graphql
       operation searchListingsByQuery(query, category, constraints)
       -> paginated listing summaries + suggested sub-categories
       operation listing(listingID)
       -> full detail record for one listing

Required headers (a normal `requests` client must set these explicitly;
tutti.ch's frontend sets them via its own bundled JS):
  X-Tutti-Hash: any UUID, regenerated per request
  X-Tutti-Source: "web LIVE"
  X-Tutti-Client-Identifier: "web/1.0.0+env-live.git-0000000"

Pagination quirk: a single (query, category, constraints, sort) combination
can only be paged up to an offset of ~3000 - larger offsets return a server
error. To cover result sets bigger than that, this scraper recursively
partitions the search: first by every category tutti.ch suggests for the
query, then (if a single category is still too large) by binary-searched
price range, until every slice is small enough to page through completely.
A dedicated free-only pass and an ascending-sort sweep mop up listings a
price filter or a single sort order might otherwise miss. Listings are
de-duplicated by ID across all of this.

After the search phase collects every listing id, the scraper (by default)
visits each listing's detail operation one by one to extract fields the
search summary doesn't return: GPS coordinates, full-resolution images,
structured attributes, and richer seller info. This is slower (one extra
request per listing) but gives full details.

Language: every function that builds a URL or requests server-rendered
slugs takes an optional `lang` (default "de"), matching tutti.ch/<lang>/....
"fr" and "it" are also live tutti.ch locales.

This module can be used two ways:

1. As a standalone CLI script that writes a CSV + JSON file:

    python3 tutti_scraper.py "velo"
    python3 tutti_scraper.py "Tesla Roadster" --out tesla_roadster
    python3 tutti_scraper.py "velo" --no-detail   # skip per-listing detail fetch

2. As a library, imported from another project, returning data directly
   instead of writing files:

    from tutti_scraper import scrape

    result = scrape("velo", max_results=50)
    for row in result.rows:          # flattened dicts, one per listing
        print(row["price"], row["url"])
    result.listings                  # raw (unflattened) API JSON per listing
    result.to_csv("velo.csv")        # optional, if you want a file after all
    result.to_json("velo.json")
"""

import argparse
import csv
import json
import logging
import re
import sys
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

__version__ = "0.3.0"

API_URL = "https://www.tutti.ch/api/v10/graphql"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

SEARCH_QUERY = """
query Search(
  $q: String, $category: ID, $constraints: ListingSearchConstraints,
  $first: Int!, $offset: Int!, $sort: ListingSortMode!, $direction: SortDirection!
) {
  searchListingsByQuery(query: $q, category: $category, constraints: $constraints) {
    suggestedCategories { categoryID label }
    listings(first: $first, offset: $offset, sort: $sort, direction: $direction) {
      totalCount
      edges {
        node {
          listingID
          title
          body
          postcodeInformation { postcode locationName canton { shortName name } }
          timestamp
          formattedPrice
          formattedSource
          highlighted
          primaryCategory { categoryID }
          sellerInfo { alias logoURL }
          thumbnail {
            normalRendition: rendition(width: 235, height: 167) { src }
            retinaRendition: rendition(width: 470, height: 334) { src }
          }
          seoInformation {
            deSlug: slug(language: DE)
            frSlug: slug(language: FR)
            itSlug: slug(language: IT)
          }
        }
      }
    }
  }
}
"""

DETAIL_QUERY = """
query Detail($id: ListingID!) {
  listing(listingID: $id) {
    listingID
    title
    body
    language
    externalURL
    postcodeInformation { postcode locationName canton { shortName name } }
    coordinates { latitude longitude }
    timestamp
    formattedPrice
    formattedSource
    highlighted
    sellerInfo { alias logoURL locationName url memberSince publicAccountID }
    images(first: 15) { rendition(width: 1024, height: 768) { src } }
    primaryCategory {
      categoryID
      label
      parent { categoryID label }
    }
    address
    phoneInfo { isMobile phoneHash }
    properties {
      ... on ListingPropertyDescription { listingPropertyID label text }
    }
    seoInformation {
      deSlug: slug(language: DE)
      frSlug: slug(language: FR)
      itSlug: slug(language: IT)
      numericPrice
    }
  }
}
"""

PAGE_SIZE = 100
MAX_OFFSET = 3000  # highest offset the API accepts reliably (probed live)
MAX_TOTAL = MAX_OFFSET + PAGE_SIZE  # items reachable in one linear pass
MAX_PRICE = 100_000_000  # CHF ceiling for price bisection (covers real estate)
MAX_BISECT_DEPTH = 40

PRIORITY_FIELDS = [
    "listingID",
    "title",
    "price",
    "formattedPrice",
    "category",
    "postcode",
    "locationName",
    "canton",
    "timestamp",
    "sellerAlias",
    "url",
]

__all__ = [
    "scrape",
    "ScrapeResult",
    "TuttiClient",
    "TuttiError",
    "Scraper",
    "search_listings",
    "visit_all_listings",
    "listing_url",
    "price_constraint",
    "flatten_listing",
    "order_fieldnames",
    "save_csv",
    "save_json",
    "build_arg_parser",
    "main",
    "run_cli",
]

# Library code only ever logs through this logger - it never calls
# basicConfig or attaches handlers of its own (that would be rude to a host
# application). The CLI (see _configure_cli_logging(), used by main()) is the
# only place that sets up real handlers, so plain library use is silent
# unless the caller configures logging themselves, e.g.:
#     import logging; logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tutti_scraper")
logger.addHandler(logging.NullHandler())


class TuttiError(Exception):
    pass


class TuttiClient:
    """Thin GraphQL client for tutti.ch's API: session + required headers
    + retry/backoff. Roughly the tutti equivalent of a plain
    requests.Session for a REST API - except tutti's API needs a specific
    header set (see module docstring) and a fresh X-Tutti-Hash per request,
    so it's a small class rather than a bare session."""

    def __init__(self, lang="de", delay=1.0, max_retries=5, timeout=30.0):
        self.lang = lang
        self.delay = delay
        self.max_retries = max_retries
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
                "Accept-Language": f"{lang}-CH",
                "X-Tutti-Source": "web LIVE",
                "X-Tutti-Client-Identifier": "web/1.0.0+env-live.git-0000000",
            }
        )

    def _post(self, query, variables):
        body = {"query": query, "variables": variables}
        backoff = self.delay
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            self.session.headers["X-Tutti-Hash"] = str(uuid.uuid4())
            try:
                resp = self.session.post(API_URL, json=body, timeout=self.timeout)
            except requests.RequestException as exc:
                last_exc = exc
                logger.warning("POST %s failed (%s); retry %d/%d", API_URL, exc, attempt, self.max_retries)
                time.sleep(backoff)
                backoff *= 2
                continue
            if resp.status_code == 200:
                data = resp.json()
                if data.get("errors"):
                    last_exc = TuttiError(str(data["errors"]))
                    logger.warning("GraphQL errors: %s; retry %d/%d", data["errors"], attempt, self.max_retries)
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                time.sleep(self.delay)
                return data["data"]
            if resp.status_code in (429, 500, 502, 503, 504):
                last_exc = TuttiError(f"HTTP {resp.status_code}")
                logger.warning("POST %s -> %d; retry %d/%d", API_URL, resp.status_code, attempt, self.max_retries)
                time.sleep(backoff)
                backoff *= 2
                continue
            resp.raise_for_status()
        raise TuttiError(f"giving up after {self.max_retries} attempts: {last_exc}")

    def search(
        self,
        query,
        category=None,
        constraints=None,
        offset=0,
        first=PAGE_SIZE,
        sort="TIMESTAMP",
        direction="DESCENDING",
    ):
        variables = {
            "q": query,
            "category": category,
            "constraints": constraints,
            "first": first,
            "offset": offset,
            "sort": sort,
            "direction": direction,
        }
        data = self._post(SEARCH_QUERY, variables)
        return data["searchListingsByQuery"]

    def fetch_detail(self, listing_id):
        data = self._post(DETAIL_QUERY, {"id": listing_id})
        return data["listing"]


def price_constraint(pmin=None, pmax=None, free_only=False):
    entry = {"key": "price", "freeOnly": free_only}
    if pmin is not None:
        entry["min"] = pmin
    if pmax is not None:
        entry["max"] = pmax
    return {"prices": [entry]}


class Scraper:
    """Recursively partitions a search until every slice fits in one
    linear pass, de-duplicating listings by ID across all slices. This is
    tutti's equivalent of a simple paginated search - more involved than
    that would otherwise be, only because of the ~3000-offset pagination
    cap (see module docstring). search_listings(), below, is the plain
    function wrapper most callers should use instead of this class directly.

    seed_category/seed_constraints let a caller pin the search to a
    server-side filter (a specific category, and/or a price window) from
    the start, instead of always discovering categories/prices from an
    unfiltered query. price_bounds narrows the range _bisect_price()
    searches within (e.g. to a caller-supplied [price_from, price_to]
    instead of [0, MAX_PRICE]). allow_free_pass suppresses the free-only
    mop-up pass when it would contradict an explicit price floor > 0."""

    def __init__(
        self,
        client,
        query,
        sort="TIMESTAMP",
        seed_category=None,
        seed_constraints=None,
        price_bounds=(0, MAX_PRICE),
        allow_free_pass=True,
    ):
        self.client = client
        self.query = query
        self.sort = sort
        self.seed_category = seed_category
        self.seed_constraints = seed_constraints
        self.price_bounds = price_bounds
        self.allow_free_pass = allow_free_pass
        self.seen = set()
        self.suggested_categories = []

    def run(self):
        # A caller-pinned category has nothing to split into - only the
        # unfiltered top-level query ever gets category-split.
        allow_split = self.seed_category is None
        yield from self._scrape(
            category=self.seed_category, constraints=self.seed_constraints, allow_category_split=allow_split
        )
        # Sweep from the opposite end too: for query/category combos that
        # stayed too large even after splitting, DESCENDING and ASCENDING
        # sample different items from the same offset-limited window.
        yield from self._linear_page(
            category=self.seed_category, constraints=self.seed_constraints, direction="ASCENDING"
        )

    @staticmethod
    def _merge_constraints(base, overlay):
        # Shallow overlay is sufficient today: exactly one constraint
        # family ("prices") is ever populated at a time. A future feature
        # needing two simultaneous constraint families (e.g. "prices" and
        # "locations" together) would need a smarter, key-aware merge.
        if not base:
            return overlay
        if not overlay:
            return base
        merged = dict(base)
        merged.update(overlay)
        return merged

    def _probe_total(self, category, constraints):
        result = self.client.search(self.query, category=category, constraints=constraints, offset=0, first=1)
        listings = result["listings"]
        suggested = result.get("suggestedCategories") or []
        # tutti.ch's suggestedCategories reflects `category` only, not
        # `constraints` (confirmed live) - so this is still meaningful even
        # when a price constraint is seeded, as long as no category is set.
        if category is None:
            self.suggested_categories = suggested
        return listings["totalCount"], suggested

    def _linear_page(self, category, constraints, direction="DESCENDING"):
        offset = 0
        while True:
            result = self.client.search(
                self.query,
                category=category,
                constraints=constraints,
                offset=offset,
                first=PAGE_SIZE,
                sort=self.sort,
                direction=direction,
            )
            listings = result["listings"]
            edges = listings["edges"]
            if not edges:
                break
            for edge in edges:
                node = edge["node"]
                listing_id = node["listingID"]
                if listing_id not in self.seen:
                    self.seen.add(listing_id)
                    yield node
            offset += PAGE_SIZE
            if offset > MAX_OFFSET or offset >= listings["totalCount"]:
                break

    def _scrape(self, category, constraints, allow_category_split):
        total, suggested = self._probe_total(category, constraints)
        if total == 0:
            return
        if total <= MAX_TOTAL:
            yield from self._linear_page(category, constraints)
            return
        if allow_category_split and suggested:
            for cat in suggested:
                yield from self._scrape(cat["categoryID"], constraints, allow_category_split=False)
            return
        pmin, pmax = self.price_bounds
        yield from self._bisect_price(category, constraints, pmin, pmax, depth=0)
        if self.allow_free_pass:
            # Price-bucketed constraints can exclude listings with no
            # numeric price (e.g. "price on request"); a dedicated
            # free-only pass recovers at least the free ones.
            free_constraints = self._merge_constraints(constraints, price_constraint(free_only=True))
            yield from self._linear_page(category, free_constraints)

    def _bisect_price(self, category, base_constraints, pmin, pmax, depth):
        constraints = self._merge_constraints(base_constraints, price_constraint(pmin, pmax))
        total, _ = self._probe_total(category, constraints)
        if total == 0:
            return
        if total <= MAX_TOTAL or depth >= MAX_BISECT_DEPTH or pmax - pmin <= 1:
            yield from self._linear_page(category, constraints)
            if total > MAX_TOTAL:
                logger.warning(
                    "category=%s price=[%s,%s] has %d listings but only %d are reachable; "
                    "some listings in this slice may be missing",
                    category,
                    pmin,
                    pmax,
                    total,
                    MAX_TOTAL,
                )
            return
        mid = (pmin + pmax) // 2
        yield from self._bisect_price(category, base_constraints, pmin, mid, depth + 1)
        yield from self._bisect_price(category, base_constraints, mid, pmax, depth + 1)


def _build_predicate(canton=None, postcode=None, max_age_days=None, highlighted_only=False):
    """Build a filter over fields already present on a search-summary node
    (canton, postcode, listing age, highlighted-only) - applied client-side
    since tutti.ch's GraphQL API has no server-side constraint for them."""
    canton_upper = canton.upper() if canton else None
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days) if max_age_days is not None else None

    def predicate(node):
        if canton_upper is not None:
            node_canton = ((node.get("postcodeInformation") or {}).get("canton") or {}).get("shortName")
            if not node_canton or node_canton.upper() != canton_upper:
                return False
        if postcode is not None:
            node_postcode = (node.get("postcodeInformation") or {}).get("postcode") or ""
            if not node_postcode.startswith(postcode):
                return False
        if cutoff is not None:
            timestamp = node.get("timestamp")
            if not timestamp or datetime.fromisoformat(timestamp) < cutoff:
                return False
        if highlighted_only and not node.get("highlighted"):
            return False
        return True

    return predicate


def search_listings(
    client,
    query,
    *,
    sort="TIMESTAMP",
    max_results=None,
    verbose=True,
    category=None,
    price_from=None,
    price_to=None,
    free_only=False,
    canton=None,
    postcode=None,
    max_age_days=None,
    highlighted_only=False,
):
    """Search tutti.ch for `query` and return every reachable listing
    summary matching the given filters, as a list of raw node dicts (see
    SEARCH_QUERY for the shape), plus tutti.ch's suggested sub-categories
    for the query (empty if `category` was given - see Scraper). Stops
    early once `max_results` unique *matching* listings have been
    collected, if given.

    `category`/`price_from`/`price_to`/`free_only` are sent to tutti.ch as
    server-side constraints. `canton`/`postcode`/`max_age_days`/
    `highlighted_only` are applied client-side against already-fetched
    summary fields, since tutti.ch's API has no server-side constraint for
    them.
    """
    seed_constraints = None
    price_bounds = (0, MAX_PRICE)
    allow_free_pass = True
    if free_only:
        seed_constraints = price_constraint(free_only=True)
        allow_free_pass = False  # a second free-only pass would be a pure no-op
    elif price_from is not None or price_to is not None:
        seed_constraints = price_constraint(price_from, price_to)
        price_bounds = (price_from or 0, price_to if price_to is not None else MAX_PRICE)
        # A free-only mop-up pass must never surface a free listing when
        # the caller set an explicit price floor above 0.
        allow_free_pass = price_from is None or price_from <= 0

    scraper = Scraper(
        client,
        query,
        sort=sort,
        seed_category=category,
        seed_constraints=seed_constraints,
        price_bounds=price_bounds,
        allow_free_pass=allow_free_pass,
    )
    predicate = _build_predicate(canton, postcode, max_age_days, highlighted_only)

    nodes = []
    for node in scraper.run():
        if not predicate(node):
            continue
        nodes.append(node)
        if verbose and len(nodes) % PAGE_SIZE == 0:
            logger.info("... %d listings found so far", len(nodes))
        if max_results is not None and len(nodes) >= max_results:
            break
    return nodes, scraper.suggested_categories


def visit_all_listings(client, listings, *, verbose=True):
    """Visit each listing's detail operation one by one, merging the
    richer detail fields (coordinates, full-res images, structured
    attributes, seller info) into a copy of its summary dict. A listing
    whose detail fetch fails keeps its summary fields instead of being
    dropped."""
    detailed = []
    total = len(listings)
    for i, node in enumerate(listings, 1):
        record = dict(node)
        try:
            detail = client.fetch_detail(node["listingID"])
            record.update(detail)
        except TuttiError as exc:
            logger.warning("Detail fetch failed for %s: %s", node["listingID"], exc)
        detailed.append(record)
        if verbose:
            logger.info("Visited %d/%d listings", i, total)
    return detailed


def listing_url(node, lang="de"):
    slug = (node.get("seoInformation") or {}).get(f"{lang}Slug")
    if not slug:
        return None
    return f"https://www.tutti.ch/{lang}/vi/{slug}/{node['listingID']}"


def _scalarize(value: Any) -> Any:
    """Turn a nested dict/list value into something that fits one CSV cell."""
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        for key in ("name", "label", "src", "shortName"):
            if key in value and not isinstance(value[key], (dict, list)):
                return value[key]
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, list):
        return "; ".join(str(_scalarize(v)) for v in value)
    return str(value)


def flatten_listing(item: dict[str, Any], lang: str = "de") -> dict[str, Any]:
    """Flatten a listing (search-summary or full-detail shape) into one flat
    dict covering every field tutti.ch returned for it, so nothing is lost."""
    flat: dict[str, Any] = {}
    for key, value in item.items():
        if key == "seoInformation" and isinstance(value, dict):
            flat["price"] = value.get("numericPrice")
            for sub_key, sub_value in value.items():
                flat[f"seoInformation_{sub_key}"] = _scalarize(sub_value)
            continue
        if key == "sellerInfo" and isinstance(value, dict):
            flat["sellerAlias"] = value.get("alias")
            flat["sellerLocationName"] = value.get("locationName")
            flat["sellerMemberSince"] = value.get("memberSince")
            continue
        if key == "primaryCategory" and isinstance(value, dict):
            flat["category"] = value.get("label") or value.get("categoryID")
            flat["categoryKey"] = value.get("categoryID")
            continue
        if key == "postcodeInformation" and isinstance(value, dict):
            flat["postcode"] = value.get("postcode")
            flat["locationName"] = value.get("locationName")
            canton = value.get("canton") or {}
            flat["canton"] = canton.get("name")
            flat["cantonKey"] = canton.get("shortName")
            continue
        if key == "thumbnail" and isinstance(value, dict):
            flat["thumbnailURL"] = (value.get("normalRendition") or {}).get("src")
            continue
        if key == "images" and isinstance(value, list):
            flat["images"] = "; ".join(
                (img.get("rendition") or {}).get("src", "") for img in value if isinstance(img, dict)
            )
            continue
        if key == "properties" and isinstance(value, list):
            flat["properties"] = "; ".join(f"{p.get('label')}: {p.get('text')}" for p in value if isinstance(p, dict))
            continue
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                flat[f"{key}_{sub_key}"] = _scalarize(sub_value)
            continue
        flat[key] = _scalarize(value)
    # search_listings()/visit_all_listings() already embed a lang-correct
    # "url" on the raw item; only fall back to computing a default one here
    # for listings flattened without going through those (e.g. tests).
    flat.setdefault("url", listing_url(item, lang))
    return flat


def order_fieldnames(all_keys: Iterable[str]) -> list[str]:
    ordered = [f for f in PRIORITY_FIELDS if f in all_keys]
    remaining = sorted(k for k in all_keys if k not in ordered)
    return ordered + remaining


def save_csv(rows: list[dict[str, Any]], path: str) -> None:
    if not rows:
        logger.warning("no rows to write")
        return
    all_keys: set[str] = set()
    for row in rows:
        all_keys.update(row.keys())
    fieldnames = order_fieldnames(all_keys)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, restval="")
        writer.writeheader()
        writer.writerows(rows)


def save_json(rows: list[dict[str, Any]], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


@dataclass
class ScrapeResult:
    """Everything a scrape() call produced, ready to use in-memory or save to disk."""

    query: str
    total_elements: int
    listings: list[dict[str, Any]] = field(default_factory=list)  # raw API objects (summary or full detail shape)
    rows: list[dict[str, Any]] = field(default_factory=list)  # flattened dicts, one per listing, CSV-ready
    lang: str = "de"  # locale that was scraped, e.g. "de"
    # tutti.ch's own suggested sub-categories for `query` - lets a caller
    # discover valid `category` filter values without a separate lookup.
    # Empty when `category` was given (nothing left to suggest).
    suggested_categories: list[dict[str, str]] = field(default_factory=list)

    def to_csv(self, path: str) -> None:
        save_csv(self.rows, path)

    def to_json(self, path: str) -> None:
        save_json(self.listings, path)


def scrape(
    query: str,
    *,
    lang: str = "de",
    detail: bool = True,
    sort: str = "timestamp",
    max_results: int | None = None,
    delay: float = 1.0,
    verbose: bool = True,
    client: "TuttiClient | None" = None,
    timeout: float = 30.0,
    max_retries: int = 5,
    category: str | None = None,
    price_from: int | None = None,
    price_to: int | None = None,
    free_only: bool = False,
    canton: str | None = None,
    postcode: str | None = None,
    max_age_days: int | None = None,
    highlighted_only: bool = False,
) -> ScrapeResult:
    """Search tutti.ch for `query` and return the results in memory.

    This is the library entry point: it does the same work as the CLI but
    returns a ScrapeResult instead of writing files. The CLI (main(), below)
    is a thin wrapper around this function.

    Args:
        query: Free-text search phrase, e.g. "velo" or "Tesla Roadster".
        lang: Listing/URL locale - "de" (default), "fr", or "it".
        detail: If True (default), visit every listing's detail operation
            one by one to extract every field tutti.ch returns for it
            (coordinates, full-res images, attributes, richer seller info).
            If False, keep only the summary fields from the search results
            (much faster).
        sort: Sort order tutti.ch searches with - "timestamp" (default),
            "price", or "relevance".
        max_results: Stop after this many unique *matching* listings
            (i.e. after every filter below), if given.
        delay: Seconds to wait between requests.
        verbose: If True, emit progress via the "tutti_scraper" logger at
            INFO level.
        client: Optional TuttiClient to reuse (e.g. across repeated calls).
            A new one is created (using `lang`, `delay`, `timeout`, and
            `max_retries`) if not given - if you do pass one, make sure its
            settings match these arguments, since they aren't cross-checked.
        timeout: Seconds to wait for a single HTTP response before retrying.
        max_retries: Maximum attempts per request before giving up with a
            TuttiError.
        category: Pin the search to this tutti.ch categoryID (e.g.
            "bicycles"), skipping auto category-split. Use
            `ScrapeResult.suggested_categories` from an unfiltered search
            to discover valid values for a given query.
        price_from: Minimum price in CHF (inclusive). Sent to tutti.ch as a
            server-side filter.
        price_to: Maximum price in CHF (inclusive). Sent to tutti.ch as a
            server-side filter.
        free_only: Only free listings. Sent to tutti.ch as a server-side
            filter. Cannot be combined with price_from/price_to (raises
            ValueError) - a price range has no meaning for free listings.
        canton: Only listings in this canton (2-letter code, e.g. "BE"),
            case-insensitive. Applied client-side (no server-side
            equivalent), against already-fetched summary fields.
        postcode: Only listings whose postcode starts with this value.
            Applied client-side.
        max_age_days: Only listings posted within the last N days. Applied
            client-side.
        highlighted_only: Only sponsored/boosted listings. Applied
            client-side.

    Returns:
        A ScrapeResult with `.listings` (raw API objects, each including a
        "url" pointing at the original ad), `.rows` (flattened dicts, one
        per listing, sorted by price), and `.suggested_categories`.

    Raises:
        ValueError: if price_from > price_to, if free_only is combined with
            price_from/price_to, if max_age_days isn't positive, or if
            postcode isn't numeric. Raised before any network call.
    """
    if price_from is not None and price_to is not None and price_from > price_to:
        raise ValueError(f"price_from ({price_from}) must be <= price_to ({price_to})")
    if free_only and (price_from is not None or price_to is not None):
        raise ValueError("free_only cannot be combined with price_from/price_to")
    if max_age_days is not None and max_age_days <= 0:
        raise ValueError(f"max_age_days must be positive, got {max_age_days}")
    if postcode is not None and not postcode.isdigit():
        raise ValueError(f"postcode must be numeric, got {postcode!r}")

    client = client or TuttiClient(lang=lang, delay=delay, timeout=timeout, max_retries=max_retries)

    if verbose:
        logger.info("Searching tutti.ch for %r ...", query)
    nodes, suggested_categories = search_listings(
        client,
        query,
        sort=sort.upper(),
        max_results=max_results,
        verbose=verbose,
        category=category,
        price_from=price_from,
        price_to=price_to,
        free_only=free_only,
        canton=canton,
        postcode=postcode,
        max_age_days=max_age_days,
        highlighted_only=highlighted_only,
    )
    total_elements = len(nodes)
    for node in nodes:
        node["url"] = listing_url(node, lang)

    if detail:
        if verbose:
            logger.info("Visiting each of %d listings one by one to extract full details ...", len(nodes))
        listings = visit_all_listings(client, nodes, verbose=verbose)
    else:
        listings = nodes

    rows = [flatten_listing(item, lang) for item in listings]
    rows.sort(key=lambda r: (r.get("price") in (None, ""), r.get("price")))

    return ScrapeResult(
        query=query,
        total_elements=total_elements,
        listings=listings,
        rows=rows,
        lang=lang,
        suggested_categories=suggested_categories,
    )


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return slug or "listings"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scrape tutti.ch listings for a given search phrase.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("query", help="Search phrase, e.g. 'velo' or 'Tesla Roadster'")
    parser.add_argument("--lang", default="de", choices=["de", "fr", "it"], help="Listing/URL locale (default: de)")
    parser.add_argument(
        "--out",
        default=None,
        help="Output file base name (without extension). Defaults to a slug of the search phrase.",
    )
    parser.add_argument(
        "--no-detail",
        action="store_true",
        help="Skip visiting each listing's detail operation; keep only the summary "
        "fields from the search results (faster, fewer fields).",
    )
    parser.add_argument(
        "--sort",
        default="timestamp",
        choices=["timestamp", "price", "relevance"],
        help="Sort order tutti.ch searches with (default: timestamp)",
    )
    parser.add_argument("--max", type=int, default=None, help="Stop after N listings.")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay in seconds between requests.")
    parser.add_argument(
        "--timeout", type=float, default=30.0, help="Seconds to wait for a single HTTP response before retrying."
    )
    parser.add_argument("--max-retries", type=int, default=5, help="Maximum attempts per request before giving up.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview up to --max (default 5) matching listings without visiting detail pages or writing any files.",
    )
    parser.add_argument("--price-from", type=int, default=None, help="Minimum price in CHF (inclusive).")
    parser.add_argument("--price-to", type=int, default=None, help="Maximum price in CHF (inclusive).")
    parser.add_argument(
        "--free-only",
        action="store_true",
        help="Only free listings. Cannot be combined with --price-from/--price-to.",
    )
    parser.add_argument(
        "--category",
        default=None,
        help="Pin the search to a tutti.ch categoryID (e.g. 'bicycles'), skipping auto category-split.",
    )
    parser.add_argument("--canton", default=None, help="Only listings in this canton (2-letter code, e.g. 'BE').")
    parser.add_argument("--postcode", default=None, help="Only listings whose postcode starts with this value.")
    parser.add_argument("--max-age-days", type=int, default=None, help="Only listings posted within the last N days.")
    parser.add_argument("--highlighted-only", action="store_true", help="Only sponsored/boosted listings.")
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        "-v", "--verbose", action="store_true", help="Show debug-level detail, including every HTTP request made."
    )
    verbosity.add_argument(
        "-q", "--quiet", action="store_true", help="Suppress progress output; only warnings/errors are shown."
    )
    return parser


def _configure_cli_logging(*, verbose: bool, quiet: bool) -> None:
    """Set up console logging for CLI use: progress (INFO, or DEBUG with -v)
    goes to stdout, warnings/errors (-q still shows these) go to stderr.
    Only main() calls this - plain library use of scrape() never touches
    logging config, since that would be rude to whatever application
    imported it."""
    level = logging.DEBUG if verbose else logging.WARNING if quiet else logging.INFO
    plain = logging.Formatter("%(message)s")

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(level)
    stdout_handler.addFilter(lambda record: record.levelno < logging.WARNING)
    stdout_handler.setFormatter(plain)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(plain)

    logger.handlers.clear()
    logger.addHandler(stdout_handler)
    logger.addHandler(stderr_handler)
    logger.setLevel(level)
    logger.propagate = False


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Parses argv (defaults to sys.argv[1:]), scrapes, and
    writes CSV + JSON files. Returns 0 on success; lets exceptions propagate
    (see run_cli() for the error-handling / exit-code wrapper used by the
    __main__ guard below)."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    _configure_cli_logging(verbose=args.verbose, quiet=args.quiet)

    filter_kwargs = dict(
        lang=args.lang,
        sort=args.sort,
        delay=args.delay,
        timeout=args.timeout,
        max_retries=args.max_retries,
        category=args.category,
        price_from=args.price_from,
        price_to=args.price_to,
        free_only=args.free_only,
        canton=args.canton,
        postcode=args.postcode,
        max_age_days=args.max_age_days,
        highlighted_only=args.highlighted_only,
    )

    if args.dry_run:
        preview_size = args.max or 5
        result = scrape(args.query, detail=False, max_results=preview_size, verbose=True, **filter_kwargs)
        logger.info("Dry run: %d matching listing(s) previewed (no files written, no detail fetch).", len(result.rows))
        for row in result.rows:
            logger.info("  %s  %s  %s", row.get("formattedPrice") or "-", row.get("title") or "", row.get("url") or "")
        if result.suggested_categories:
            logger.info("Suggested categories: %s", ", ".join(c["categoryID"] for c in result.suggested_categories))
        return 0

    result = scrape(args.query, detail=not args.no_detail, max_results=args.max, verbose=True, **filter_kwargs)

    out_base = args.out or _slugify(args.query)
    csv_path = f"{out_base}.csv"
    json_path = f"{out_base}.json"
    result.to_csv(csv_path)
    result.to_json(json_path)

    logger.info("\nDone. %d unique listings found.", len(result.rows))
    logger.info("  CSV:  %s", csv_path)
    logger.info("  JSON: %s", json_path)
    return 0


def run_cli(argv: list[str] | None = None) -> int:
    """Run main() and translate exceptions into (message, exit code) the way
    the command line expects. Factored out from the __main__ guard so it can
    be unit-tested directly without spawning a subprocess."""
    try:
        return main(argv) or 0
    except TuttiError as exc:
        logger.error("Error talking to tutti.ch: %s", exc)
        return 1
    except requests.RequestException as exc:
        logger.error("Network error talking to tutti.ch: %s", exc)
        return 1
    except KeyboardInterrupt:
        logger.error("\nInterrupted.")
        return 130


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in test_e2e.py
    sys.exit(run_cli())
