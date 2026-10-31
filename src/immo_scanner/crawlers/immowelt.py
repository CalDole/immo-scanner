"""Immowelt crawler (Immonet listings were merged into Immowelt in 2024).

Immowelt's `/classified-search` is bot-protected and only serves later result pages to a
real browser, so this runs Chromium with the persistent `immowelt` profile. Price, area
and room criteria go into the search URL (`priceMin`, `priceMax`, `spaceMin`,
`numberOfRoomsMin`) and the crawler follows `page=N` to the last result page, which
makes the crawl complete. Immowelt does not cap how deep the pagination goes.
"""

import logging
import re
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
    parse_floor,
    parse_number,
    parse_postcode,
    parse_price,
    parse_rooms,
)

log = logging.getLogger(__name__)

EXPOSE_ID_RE = re.compile(r"/expose/([\w-]+)")
# On the last page the highest "zu seite" link is the one before it.
LAST_PAGE_RE = re.compile(r'aria-label="(?:zu seite|aktuelle seite, seite) (\d+)"', re.IGNORECASE)
CARD_TESTID = "serp-core-classified-card-testid"
BLOCK_MARKERS = ("captcha-delivery.com", "Ich bin kein Roboter", "challenge-platform")


def page_url(search_url: str, page: int) -> str:
    parts = urlsplit(search_url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "page"]
    if page > 1:
        query.append(("page", str(page)))
    return urlunsplit(parts._replace(query=urlencode(query, safe=",")))


def is_blocked(html: str) -> bool:
    return CARD_TESTID not in html and any(marker in html for marker in BLOCK_MARKERS)


def _testid(card: Node, testid: str) -> Node | None:
    return card.css_first(f'[data-testid="{testid}"]')


def _split_address(address: str | None) -> str | None:
    """'Musterstr. 1, Steglitz, Berlin (12163)' -> 'Steglitz'."""
    if not address:
        return None
    parts = [re.sub(r"\s*\(\d{5}\)", "", p).strip() for p in address.split(",")]
    parts = [p for p in parts if p and not p.lower().startswith("berlin")]
    return parts[-1] if parts else None


def parse_card(card: Node) -> ListingData | None:
    link = _testid(card, "card-mfe-covering-link-testid") or card.css_first('a[href*="/expose/"]')
    if link is None:
        return None
    href = link.attributes.get("href") or ""
    match = EXPOSE_ID_RE.search(href)
    if not match:
        return None

    price_node = _testid(card, "cardmfe-price-testid")
    price = None
    if price_node is not None:
        price = parse_number(price_node.attributes.get("aria-label")) or parse_price(
            price_node.text(separator=" ")
        )

    facts_node = _testid(card, "cardmfe-keyfacts-testid")
    facts = clean_text(facts_node.text(separator=" ")) if facts_node else None
    address_node = _testid(card, "cardmfe-description-box-address")
    address = clean_text(address_node.text()) if address_node else None
    tags = [clean_text(t.text()) for t in card.css('[data-testid="cardmfe-tag-testid"]')]
    title = clean_text(link.attributes.get("title"))

    return ListingData(
        source="immowelt",
        external_id=match.group(1),
        url=href.split("?")[0],
        title=title,
        address=address,
        postcode=parse_postcode(address),
        district=_split_address(address),
        price=price,
        living_area=parse_area(facts),
        rooms=parse_rooms(facts),
        floor=parse_floor(facts),
        is_new_build=any("neubau" in (t or "").lower() for t in tags),
        raw={"facts": facts, "tags": tags, "address": address},
    )


def parse_search_page(html: str) -> tuple[list[ListingData], int | None]:
    tree = HTMLParser(html)
    listings = [
        item
        for card in tree.css(f'[data-testid="{CARD_TESTID}"]')
        if (item := parse_card(card)) is not None
    ]
    pages = [int(n) for n in LAST_PAGE_RE.findall(html)]
    return listings, max(pages) if pages else None


class ImmoweltCrawler(BaseCrawler):
    source = "immowelt"

    def _load(self, browser: Browser, url: str) -> str:
        html = browser.goto(url, wait_for=f'[data-testid="{CARD_TESTID}"]')
        if is_blocked(html):
            solved = browser.wait_for_human(
                lambda: not is_blocked(browser.page.content()), "Immowelt shows a captcha"
            )
            if not solved:
                raise BlockedError(f"immowelt: captcha on {url}")
            browser.page.wait_for_timeout(2_000)
            html = browser.page.content()
        return html

    def crawl(self, max_pages: int) -> CrawlResult:
        result = CrawlResult()
        search_url = Criteria.from_settings(self.settings).immowelt_url(
            self.settings.immowelt_search_url
        )
        with Browser(self.settings, self.source) as browser:
            last_page = None
            for page in range(1, max_pages + 1):
                try:
                    html = self._load(browser, page_url(search_url, page))
                except BlockedError:
                    if not result.listings:
                        raise
                    log.warning("immowelt: blocked on page %d, keeping partial results", page)
                    break
                listings, found_last = parse_search_page(html)
                last_page = found_last or last_page
                log.info(
                    "immowelt page %d/%s: %d listings", page, last_page or "?", len(listings)
                )
                result.listings.extend(listings)
                if not listings:
                    # An empty page after real results means we ran past the end; an
                    # empty first page means no results or a parsing failure.
                    result.complete = page > 1
                    break
                if last_page is None or page >= last_page:
                    result.complete = True
                    break
        result.listings = dedupe(result.listings)
        return result
