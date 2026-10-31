import logging
import re
from urllib.parse import urljoin

from selectolax.parser import HTMLParser, Node

from immo_scanner.crawlers.base import BaseCrawler, CrawlResult, ListingData, dedupe
from immo_scanner.crawlers.criteria import Criteria
from immo_scanner.crawlers.parsing import (
    clean_text,
    parse_area,
    parse_number,
    parse_postcode,
    parse_rooms,
)

log = logging.getLogger(__name__)

BASE_URL = "https://www.kleinanzeigen.de"
PRICE_ONLY_RE = re.compile(r"^[\d.,]+\s*€(\s*VB)?$")


def page_url(search_url: str, page: int) -> str:
    """Kleinanzeigen puts the page before the category code: .../seite:2/c196l3331."""
    if page == 1:
        return search_url
    head, _, category = search_url.rstrip("/").rpartition("/")
    return f"{head}/seite:{page}/{category}"


def _location(article: Node) -> str | None:
    icon = article.css_first('svg[data-title="locationOutline"]')
    if icon is not None and icon.parent is not None:
        span = icon.parent.css_first("span")
        if span is not None:
            return clean_text(span.text())
    return None


def parse_article(article: Node) -> ListingData | None:
    ad_id = article.attributes.get("data-adid")
    href = article.attributes.get("data-href")
    if not ad_id or not href:
        return None
    for script in article.css("script"):
        script.decompose()

    title_node = article.css_first("h3 a") or article.css_first("h2 a")
    title = clean_text(title_node.text()) if title_node else None

    price = None
    facts = None
    for p in article.css("p"):
        text = clean_text(p.text()) or ""
        if price is None and PRICE_ONLY_RE.match(text):
            price = parse_number(text)
        elif facts is None and ("m²" in text or "Zi." in text) and len(text) < 40:
            facts = text

    location = _location(article)
    postcode = parse_postcode(location)
    district = None
    if location:
        district = clean_text(re.sub(r"^\d{5}\s*", "", location))

    return ListingData(
        source="kleinanzeigen",
        external_id=ad_id,
        url=urljoin(BASE_URL, href),
        title=title,
        address=location,
        postcode=postcode,
        district=district,
        price=price,
        living_area=parse_area(facts),
        rooms=parse_rooms(facts),
        is_new_build="neubau" in (title or "").lower(),
        raw={"location": location, "facts": facts},
    )


def parse_search_page(html: str) -> tuple[list[ListingData], bool]:
    tree = HTMLParser(html)
    listings = [
        item
        for article in tree.css("article[data-adid]")
        if (item := parse_article(article)) is not None
    ]
    has_next = tree.css_first('a[aria-label="Nächste"]') is not None
    return listings, has_next


class KleinanzeigenCrawler(BaseCrawler):
    source = "kleinanzeigen"

    def crawl(self, max_pages: int) -> CrawlResult:
        result = CrawlResult()
        search_url = Criteria.from_settings(self.settings).kleinanzeigen_url(
            self.settings.kleinanzeigen_search_url
        )
        for page in range(1, max_pages + 1):
            html = self.fetch(page_url(search_url, page))
            listings, has_next = parse_search_page(html)
            log.info("kleinanzeigen page %d: %d listings", page, len(listings))
            result.listings.extend(listings)
            if not listings or not has_next:
                result.complete = True
                break
        result.listings = dedupe(result.listings)
        return result
