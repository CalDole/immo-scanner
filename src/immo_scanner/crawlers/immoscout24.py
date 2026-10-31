"""ImmoScout24 crawler.

ImmoScout24 uses aggressive bot protection, so this runs a real (by default visible)
Chromium with a persistent profile. Once a page has loaded, the search results are
requested as JSON from inside the browser session (the same endpoint the website uses),
falling back to parsing result cards from the DOM.

If you are blocked repeatedly, use saved-search alert emails instead:
`immo import-is24-emails`.
"""

import json
import logging
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from selectolax.parser import HTMLParser, Node

from immo_scanner.crawlers.base import (
    BaseCrawler,
    BlockedError,
    CrawlResult,
    ListingData,
    dedupe,
)
from immo_scanner.crawlers.browser import Browser
from immo_scanner.crawlers.criteria import Criteria
from immo_scanner.crawlers.parsing import (
    clean_text,
    parse_area,
    parse_postcode,
    parse_price,
    parse_rooms,
)

log = logging.getLogger(__name__)

EXPOSE_URL = "https://www.immobilienscout24.de/expose/{}"
EXPOSE_ID_RE = re.compile(r"/expose/(\d+)")
BLOCK_MARKERS = ("Ich bin kein Roboter", "captcha", "Zugriff verweigert", "Access Denied")

FETCH_JSON_JS = """
async (url) => {
  const response = await fetch(url, {
    headers: {"Accept": "application/json"},
    credentials: "include",
  });
  return [response.status, await response.text()];
}
"""


def page_url(search_url: str, page: int) -> str:
    parts = urlsplit(search_url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "pagenumber"]
    if page > 1:
        query.append(("pagenumber", str(page)))
    return urlunsplit(parts._replace(query=urlencode(query)))


def _find_key(data: Any, key: str) -> Any:
    """Depth-first search for the first value stored under `key`."""
    if isinstance(data, dict):
        if key in data:
            return data[key]
        for value in data.values():
            found = _find_key(value, key)
            if found is not None:
                return found
    elif isinstance(data, list):
        for value in data:
            found = _find_key(value, key)
            if found is not None:
                return found
    return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("value")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_entry(entry: dict[str, Any]) -> ListingData | None:
    expose_id = str(entry.get("@id") or entry.get("realEstateId") or "")
    estate = entry.get("resultlist.realEstate") or {}
    if not expose_id or not estate:
        return None
    address = estate.get("address") or {}
    coords = address.get("wgs84Coordinate") or {}
    street = " ".join(p for p in (address.get("street"), address.get("houseNumber")) if p)
    postcode = address.get("postcode")
    quarter = address.get("quarter")
    address_text = ", ".join(
        p for p in (street, " ".join(x for x in (postcode, address.get("city")) if x)) if p
    )
    title = clean_text(estate.get("title"))
    floor = estate.get("floor")
    return ListingData(
        source="immoscout24",
        external_id=expose_id,
        url=EXPOSE_URL.format(expose_id),
        title=title,
        address=address_text or quarter,
        postcode=str(postcode) if postcode else None,
        district=quarter,
        lat=_as_float(coords.get("latitude")),
        lon=_as_float(coords.get("longitude")),
        price=_as_float(estate.get("price")),
        living_area=_as_float(estate.get("livingSpace")),
        rooms=_as_float(estate.get("numberOfRooms")),
        floor=int(floor) if isinstance(floor, int | float) else None,
        is_new_build=bool(
            estate.get("newHomeBuilderExclusive")
            or estate.get("newBuilding")
            or "neubau" in (title or "").lower()
        ),
        raw={
            "type": estate.get("@xsi.type"),
            "private_offer": estate.get("privateOffer"),
            "courtage": (estate.get("courtage") or {}).get("hasCourtage"),
        },
    )


def parse_json_results(payload: dict[str, Any]) -> tuple[list[ListingData], int | None]:
    entries = _find_key(payload, "resultlistEntry") or []
    if isinstance(entries, dict):
        entries = [entries]
    listings = [item for entry in entries if (item := parse_entry(entry)) is not None]
    paging = _find_key(payload, "paging") or {}
    pages = paging.get("numberOfPages") if isinstance(paging, dict) else None
    return listings, int(pages) if pages else None


def _card_for(anchor: Node, max_depth: int = 8) -> Node | None:
    current = anchor
    for _ in range(max_depth):
        if current.parent is None:
            return None
        current = current.parent
        text = current.text(separator=" ")
        if "€" in text and "m²" in text:
            return current
    return None


def parse_dom_results(html: str) -> list[ListingData]:
    tree = HTMLParser(html)
    listings: dict[str, ListingData] = {}
    for anchor in tree.css('a[href*="/expose/"]'):
        match = EXPOSE_ID_RE.search(anchor.attributes.get("href") or "")
        if not match or match.group(1) in listings:
            continue
        card = _card_for(anchor)
        if card is None:
            continue
        text = clean_text(card.text(separator=" ")) or ""
        title_node = card.css_first("h2, h3, h5")
        address_node = card.css_first(
            '[data-testid="hybridViewAddress"], .result-list-entry__address'
        )
        address = clean_text(address_node.text()) if address_node else None
        listings[match.group(1)] = ListingData(
            source="immoscout24",
            external_id=match.group(1),
            url=EXPOSE_URL.format(match.group(1)),
            title=clean_text(title_node.text()) if title_node else None,
            address=address,
            postcode=parse_postcode(address or text),
            price=parse_price(text),
            living_area=parse_area(text),
            rooms=parse_rooms(text),
            is_new_build="neubau" in text.lower(),
            raw={"parsed_from": "dom"},
        )
    return list(listings.values())


class ImmoScout24Crawler(BaseCrawler):
    source = "immoscout24"

    def _is_blocked(self, browser: Browser) -> bool:
        try:
            content = browser.page.content()
        except Exception:
            return False
        has_results = "/expose/" in content
        return not has_results and any(m.lower() in content.lower() for m in BLOCK_MARKERS)

    def _fetch_json(self, browser: Browser, url: str) -> dict[str, Any] | None:
        try:
            status, body = browser.page.evaluate(FETCH_JSON_JS, url)
        except Exception as exc:
            log.debug("In-page JSON fetch failed: %s", exc)
            return None
        if status != 200:
            log.debug("In-page JSON fetch returned %s", status)
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return None

    def crawl(self, max_pages: int) -> CrawlResult:
        result = CrawlResult()
        search_url = Criteria.from_settings(self.settings).immoscout24_url(
            self.settings.immoscout24_search_url
        )
        with Browser(self.settings, "immoscout24") as browser:
            total_pages = None
            for page in range(1, max_pages + 1):
                url = page_url(search_url, page)
                html = browser.goto(url)
                if self._is_blocked(browser):
                    solved = browser.wait_for_human(
                        lambda: not self._is_blocked(browser), "ImmoScout24 shows a captcha"
                    )
                    if not solved:
                        if result.listings:
                            log.warning("Blocked on page %d, keeping partial results", page)
                            break
                        raise BlockedError("ImmoScout24 blocked the crawler")
                    html = browser.page.content()

                listings: list[ListingData] = []
                payload = self._fetch_json(browser, url)
                if payload is not None:
                    listings, total_pages = parse_json_results(payload)
                if not listings:
                    listings = parse_dom_results(html)
                log.info(
                    "immoscout24 page %d/%s: %d listings", page, total_pages or "?", len(listings)
                )
                result.listings.extend(listings)
                if total_pages is not None and page >= total_pages:
                    result.complete = True
                    break
                if not listings:
                    # An empty page after real results means we ran past the end; an
                    # empty first page means parsing failed.
                    result.complete = page > 1 and total_pages is None
                    break
        result.listings = dedupe(result.listings)
        return result
