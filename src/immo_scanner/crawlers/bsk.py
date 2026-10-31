"""BSK Immobilien (Berliner Sparkasse) search results.

Filters live in one path segment (`...--kaufpreis-bis-750000--wohnflaeche-ab-60--...`)
and pages are offsets appended as `/P15`, `/P30`. A project card ("Projekt",
"Neubau-Projekt") lists its units; each unit that is not sold has its own exposé and
becomes a listing of its own.
"""

import logging
import re
from urllib.parse import urljoin

from selectolax.parser import HTMLParser, Node

from immo_scanner.crawlers.base import BaseCrawler, CrawlResult, ListingData, dedupe
from immo_scanner.crawlers.criteria import Criteria
from immo_scanner.crawlers.parsing import clean_text, parse_number

log = logging.getLogger(__name__)

BASE_URL = "https://www.bsk-immobilien.de"
PAGE_SIZE = 15
EXPOSE_ID_RE = re.compile(r"/expose/(\d+)-")
# Project units that are not flats.
NON_FLAT_RE = re.compile(r"Gewerbe|Büro|Praxis|Laden|Stellplatz|Garage", re.IGNORECASE)


def page_url(search_url: str, page: int) -> str:
    base = re.sub(r"/P\d+/?$", "", search_url.rstrip("/"))
    return base if page == 1 else f"{base}/P{PAGE_SIZE * (page - 1)}"


def expose_url(href: str) -> str:
    return urljoin(BASE_URL, re.sub(r"^https?://[^/]+/+", "/", href))


def expose_id(url: str) -> str | None:
    """Objekt-ID of a single exposé; project pages have none."""
    match = EXPOSE_ID_RE.search(url)
    return match.group(1) if match else None


def split_location(text: str | None) -> tuple[str | None, str | None]:
    """'Steglitz-Zehlendorf / Zehlendorf (OT)' -> ('Steglitz-Zehlendorf', 'Zehlendorf')."""
    text = clean_text(text)
    if not text:
        return None, None
    bezirk, _, ortsteil = (part.strip() for part in text.partition("/"))
    ortsteil = clean_text(re.sub(r"\(OT\)", "", ortsteil))
    return bezirk or None, ortsteil


def _facts(entry: Node) -> dict[str, str]:
    """Label/value rows of a card's summary table ('Wohnfläche:' -> '105 m2')."""
    facts = {}
    for row in entry.css(".immo-data table tr"):
        labels, values = row.css("td"), row.css("th")
        if len(labels) != 1 or len(values) != 1:
            continue
        label = (clean_text(labels[0].text()) or "").rstrip(":")
        facts[label] = clean_text(values[0].text()) or ""
    return facts


def _card_context(entry: Node) -> dict:
    tagline = entry.css_first(".tagline")
    geo = entry.css_first(".geo .small")
    bezirk, ortsteil = split_location(geo.text() if geo else None)
    tagline_class = tagline.attributes.get("class", "") if tagline else ""
    kind = clean_text(tagline.text()) if tagline else None
    return {
        "kind": kind,
        "bezirk": bezirk,
        "ortsteil": ortsteil,
        "new_build": "bg-green" in tagline_class or "neubau" in (kind or "").lower(),
    }


def parse_entry(entry: Node) -> ListingData | None:
    link = entry.css_first("[data-href]")
    if link is None:
        return None
    url = expose_url(link.attributes.get("data-href") or "")
    external_id = expose_id(url)
    if not external_id:
        return None
    context = _card_context(entry)
    facts = _facts(entry)
    title_node = entry.css_first(".immo-title")
    rented = facts.get("Vermietet", "").lower() == "ja"
    return ListingData(
        source="bsk",
        external_id=external_id,
        url=url,
        title=clean_text(title_node.text()) if title_node else None,
        district=context["ortsteil"] or context["bezirk"],
        price=parse_number(facts.get("Kaufpreis")),
        living_area=parse_number(facts.get("Wohnfläche")),
        rooms=parse_number(facts.get("Zimmer")),
        is_new_build=context["new_build"],
        raw={**context, "facts": facts, "rented": rented or None},
    )


def parse_project(entry: Node) -> list[ListingData]:
    """Unsold flats of a project card, each with its own exposé."""
    context = _card_context(entry)
    title_node = entry.css_first(".immo-title")
    project = clean_text(title_node.text()) if title_node else None
    listings = []
    for row in entry.css(".immo-data table tr"):
        cells = row.css("td")
        link = row.css_first("a[href*='/expose/']")
        if len(cells) < 4 or link is None:
            continue
        kind = clean_text(cells[0].text())
        if kind and NON_FLAT_RE.search(kind):
            continue
        url = expose_url(link.attributes.get("href") or "")
        # Unit IDs ("29745-40", "29745-GE1") extend the project number.
        external_id = cells[0].attributes.get("data-id") or expose_id(url)
        if not external_id:
            continue
        listings.append(
            ListingData(
                source="bsk",
                external_id=external_id,
                url=url,
                title=f"{kind} – {project}" if project and kind else project or kind,
                district=context["ortsteil"] or context["bezirk"],
                price=parse_number(cells[3].text()),
                living_area=parse_number(cells[1].text()),
                rooms=parse_number(cells[2].text()),
                is_new_build=context["new_build"],
                raw={**context, "kind": kind, "project": project},
            )
        )
    return listings


def parse_search_page(html: str, offset: int = 0) -> tuple[list[ListingData], bool]:
    tree = HTMLParser(html)
    listings: list[ListingData] = []
    for entry in tree.css(".immo-entry"):
        if "projekt" in (entry.attributes.get("class") or "").split():
            listings.extend(parse_project(entry))
        elif (item := parse_entry(entry)) is not None:
            listings.append(item)
    next_offset = f"/P{offset + PAGE_SIZE}"
    has_next = any(
        (a.attributes.get("href") or "").rstrip("/").endswith(next_offset) for a in tree.css("a")
    )
    return dedupe(listings), has_next


class BskCrawler(BaseCrawler):
    source = "bsk"

    def crawl(self, max_pages: int) -> CrawlResult:
        result = CrawlResult()
        search_url = Criteria.from_settings(self.settings).bsk_url(self.settings.bsk_search_url)
        for page in range(1, max_pages + 1):
            html = self.fetch(page_url(search_url, page))
            listings, has_next = parse_search_page(html, PAGE_SIZE * (page - 1))
            log.info("bsk page %d: %d listings", page, len(listings))
            result.listings.extend(listings)
            if not listings or not has_next:
                result.complete = True
                break
        result.listings = dedupe(result.listings)
        return result
