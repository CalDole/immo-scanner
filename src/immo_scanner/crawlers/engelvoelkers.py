"""Engel & Völkers property search.

The search page is a Next.js page; its results sit in the embedded `__NEXT_DATA__` JSON
(`dehydratedState.queries[0].state.data`) with 23 listings per page. Paging is
`page=N`, and a page past the end repeats the last one. When a search has 10 or fewer
hits E&V silently widens it to a 50 km radius, so results outside Berlin are dropped.
"""

import json
import logging
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from selectolax.parser import HTMLParser

from immo_scanner.crawlers.base import BaseCrawler, CrawlResult, ListingData, dedupe
from immo_scanner.crawlers.criteria import Criteria

log = logging.getLogger(__name__)

BASE_URL = "https://www.engelvoelkers.com"
DEFAULT_PAGE_SIZE = 23


class NextDataError(ValueError):
    """The page has no parsable `__NEXT_DATA__` script."""


def next_data(html: str) -> dict[str, Any]:
    node = HTMLParser(html).css_first("script#__NEXT_DATA__")
    if node is None:
        raise NextDataError("no __NEXT_DATA__ script")
    try:
        return json.loads(node.text())
    except json.JSONDecodeError as exc:
        raise NextDataError(str(exc)) from exc


def query_data(data: dict[str, Any], index: int = 0) -> dict[str, Any]:
    queries = data.get("props", {}).get("pageProps", {}).get("dehydratedState", {})
    queries = queries.get("queries") or []
    if len(queries) <= index:
        return {}
    return (queries[index].get("state") or {}).get("data") or {}


def search_results(data: dict[str, Any]) -> tuple[list[dict[str, Any]], int | None, int]:
    """(listing records, listingsTotal, page size) of a search page."""
    result = query_data(data)
    items = [entry.get("listing") or {} for entry in result.get("listings") or []]
    options = data.get("props", {}).get("pageProps", {}).get("initialOptions") or {}
    return items, result.get("listingsTotal"), options.get("pageSize") or DEFAULT_PAGE_SIZE


def page_url(search_url: str, page: int) -> str:
    parts = urlsplit(search_url)
    query = "&".join(
        part for part in parts.query.split("&") if part and not part.startswith("page=")
    )
    return urlunsplit(parts._replace(query=f"{query}&page={page}" if query else f"page={page}"))


def expose_url(listing_id: str) -> str:
    return f"{BASE_URL}/de/de/exposes/{listing_id}"


def address_parts(item: dict[str, Any]) -> dict[str, str]:
    """placeType -> text, e.g. {'administrative_area_level_1': 'Berlin', ...}."""
    return {
        part["placeType"]: part["text"]
        for part in item.get("addressComponents") or []
        if part.get("placeType") and part.get("text")
    }


def _range(value: Any) -> tuple[float | None, float | None]:
    if isinstance(value, dict):
        return value.get("min"), value.get("max")
    return value, value


def is_restricted(item: dict[str, Any]) -> bool:
    return bool(
        item.get("hasPriceOnRequest")
        or item.get("isBlurred")
        or item.get("hasAccessPassword")
        or item.get("isUserAccountExclusive")
        or item.get("privacyLevel", "PUBLIC") != "PUBLIC"
    )


def parse_listing(item: dict[str, Any]) -> ListingData | None:
    listing_id = item.get("id")
    if not listing_id or is_restricted(item):
        return None
    address = address_parts(item)
    price = _range((item.get("price") or {}).get("salesPrice"))
    area = _range((item.get("area") or {}).get("livingSurface"))
    rooms = _range(item.get("rooms"))
    raw: dict[str, Any] = {
        "display_id": item.get("displayId"),
        "berlin": address.get("administrative_area_level_1") == "Berlin"
        or address.get("locality") == "Berlin",
        "address": address,
        "is_new": item.get("isNew"),
        "development_unit": item.get("isDevelopmentUnit"),
        "shop": item.get("shopName"),
    }
    for name, (low, high) in (("price", price), ("area", area), ("rooms", rooms)):
        if low is not None and high is not None and low != high:
            raw[f"{name}_range"] = [low, high]
    return ListingData(
        source="engelvoelkers",
        external_id=listing_id,
        url=expose_url(listing_id),
        title=(item.get("profile") or {}).get("title"),
        district=address.get("sublocality_level_1") or address.get("sublocality_level_2"),
        price=price[0],
        living_area=area[0],
        rooms=rooms[0],
        raw=raw,
    )


def parse_search_page(html: str) -> tuple[list[ListingData], list[str], int | None, int]:
    """(Berlin listings, all record IDs on the page, total, page size)."""
    items, total, page_size = search_results(next_data(html))
    ids = [item["id"] for item in items if item.get("id")]
    listings = [
        listing
        for item in items
        if (listing := parse_listing(item)) is not None and listing.raw["berlin"]
    ]
    return listings, ids, total, page_size


class EngelVoelkersCrawler(BaseCrawler):
    source = "engelvoelkers"

    def crawl(self, max_pages: int) -> CrawlResult:
        result = CrawlResult()
        search_url = Criteria.from_settings(self.settings).engelvoelkers_url(
            self.settings.engelvoelkers_search_url
        )
        seen: set[str] = set()
        for page in range(1, max_pages + 1):
            listings, ids, total, page_size = parse_search_page(
                self.fetch(page_url(search_url, page))
            )
            new_ids = set(ids) - seen
            log.info(
                "engelvoelkers page %d: %d listings (%d new IDs, total %s)",
                page,
                len(listings),
                len(new_ids),
                total,
            )
            if not new_ids:
                result.complete = True
                break
            seen |= new_ids
            result.listings.extend(listings)
            if total is not None and page * page_size >= total:
                result.complete = True
                break
        result.listings = dedupe(result.listings)
        return result
