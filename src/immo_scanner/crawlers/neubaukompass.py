"""Neubaukompass crawler.

Neubaukompass lists new-build *projects* rather than single flats, and blocks plain
HTTP clients, so it is crawled with a real browser. The search pages only show projects,
so every project page is opened once to read its "Wohnbeispiele" - the individual flats,
each with its own price, floor plan and exposé page. One flat becomes one listing.

Wohnbeispiele are a published sample, not the full inventory: a project with 23 units
typically advertises a handful of them. Projects that list none stay a single listing,
with `price`, `living_area` and `rooms` holding the lower end of the advertised ranges.
"""

import logging
import re
from urllib.parse import urljoin, urlsplit

from selectolax.parser import HTMLParser, Node

from immo_scanner.crawlers.base import BaseCrawler, CrawlResult, ListingData, dedupe
from immo_scanner.crawlers.browser import Browser
from immo_scanner.crawlers.parsing import NUMBER_RE, clean_text, parse_number, parse_postcode

log = logging.getLogger(__name__)

RANGE = NUMBER_RE + r"(?:\s*[-–]\s*" + NUMBER_RE + r")?"
PRICE_RANGE_RE = re.compile(r"Preis\s+(?:ab\s+)?" + RANGE + r"\s*€", re.IGNORECASE)
AREA_RANGE_RE = re.compile(r"Wohnfläche\s+(?:ab\s+)?" + RANGE + r"\s*m²", re.IGNORECASE)
ROOMS_RANGE_RE = re.compile(r"Zimmer\s+(?:ab\s+)?" + RANGE, re.IGNORECASE)
UNITS_RE = re.compile(r"(\d+)\s+Wohneinheiten\s*(.*?)(?=\s+Preis\b|$)", re.IGNORECASE)
READY_RE = re.compile(
    r"Bezugsfertig\s+(.{1,40}?)(?=\s+(?:Preis|Wohnfläche|Zimmer|Merken|Details|Exposé|Zum)\b|$)",
    re.IGNORECASE,
)
TOTAL_RE = re.compile(r"<b>\s*\d+\s*-\s*\d+\s*</b>\s*von\s*<b>\s*(\d+)\s*</b>")
CARD_SELECTOR = ".nbk-project-card"
UNIT_CARD_SELECTOR = "a.nbk-project-residential-units__item"
UNIT_URL_RE = re.compile(r"/neubau/[\w-]+/(\d+)/expose-(\d+)/")
ADDRESS_LABEL_RE = re.compile(r"^Adresse\b", re.IGNORECASE)


def _range(pattern: re.Pattern[str], text: str) -> tuple[float | None, float | None]:
    match = pattern.search(text)
    if not match:
        return None, None
    low = parse_number(match.group(1))
    high = parse_number(match.group(2)) if match.group(2) else low
    return low, high


def _card_for(node: Node, max_depth: int = 8) -> Node | None:
    current = node
    for _ in range(max_depth):
        if current.parent is None:
            return None
        current = current.parent
        text = current.text(separator=" ")
        if "Preis" in text and "Wohnfläche" in text and current.css_first("a[href]"):
            return current
    return None


def _project_link(card: Node) -> str | None:
    link = card.css_first("a.nbk-project-card-link[href]")
    if link is not None:
        return link.attributes.get("href")
    hrefs = [a.attributes.get("href") or "" for a in card.css("a[href]")]
    hrefs = [h for h in hrefs if h and not h.startswith(("#", "javascript:", "mailto:", "tel:"))]
    preferred = [h for h in hrefs if "/neubau/" in h or "/projekt" in h]
    return (preferred or hrefs or [None])[0]


def parse_project_card(card: Node, base_url: str) -> ListingData | None:
    href = _project_link(card)
    if not href:
        return None
    url = urljoin(base_url, href).split("?")[0]
    slug = urlsplit(url).path.strip("/").split("/")[-1]
    text = clean_text(card.text(separator=" ")) or ""

    heading = card.css_first("h2, h3, h4")
    title = clean_text(heading.text()) if heading else None
    units_match = UNITS_RE.search(text)
    address = clean_text(units_match.group(2)) if units_match else None
    postcode = parse_postcode(address or text)
    if postcode is None and "berlin" not in text.lower():
        return None

    price_min, price_max = _range(PRICE_RANGE_RE, text)
    area_min, area_max = _range(AREA_RANGE_RE, text)
    rooms_min, rooms_max = _range(ROOMS_RANGE_RE, text)
    ready = READY_RE.search(text)

    return ListingData(
        source="neubaukompass",
        external_id=slug,
        url=url,
        title=title or address,
        address=address,
        postcode=postcode,
        price=price_min,
        living_area=area_min,
        rooms=rooms_min,
        is_new_build=True,
        raw={
            "units": int(units_match.group(1)) if units_match else None,
            "price_range": [price_min, price_max],
            "area_range": [area_min, area_max],
            "rooms_range": [rooms_min, rooms_max],
            "ready": clean_text(ready.group(1)) if ready else None,
        },
    )


def project_address(html: str) -> str | None:
    """The address from the project page's detail list."""
    for item in HTMLParser(html).css("li"):
        lines = [clean_text(part) for part in item.text(separator="\n").split("\n")]
        lines = [line for line in lines if line]
        if len(lines) > 1 and ADDRESS_LABEL_RE.match(lines[0]):
            return lines[1]
    return None


def _unit_features(card: Node) -> dict[str, str]:
    """{'Preis': '222.507 €', 'Wohnfläche': '33,21 m²', 'Zimmer': '1 Zi.'}"""
    features = {}
    for block in card.css(".nbk-project-residential-units__item-feature"):
        label = block.css_first(".nbk-project-residential-units__item-feature-label")
        value = block.css_first(".nbk-project-residential-units__item-feature-value")
        if label is not None and value is not None:
            features[clean_text(label.text()) or ""] = clean_text(value.text()) or ""
    return features


def parse_unit_cards(html: str, base_url: str, project: ListingData) -> list[ListingData]:
    """One listing per flat advertised in the project's "Wohnbeispiele" section."""
    tree = HTMLParser(html)
    address = project_address(html) or project.address
    postcode = parse_postcode(address or "") or project.postcode
    units = []
    for card in tree.css(UNIT_CARD_SELECTOR):
        href = card.attributes.get("href") or ""
        match = UNIT_URL_RE.search(href)
        if not match:
            continue
        project_id, unit_id = match.groups()
        features = _unit_features(card)
        teaser = card.css_first(".nbk-project-residential-units__item-teaser")
        units.append(
            ListingData(
                source="neubaukompass",
                external_id=unit_id,
                url=urljoin(base_url, href),
                title=clean_text(teaser.text()) if teaser else project.title,
                address=address,
                postcode=postcode,
                price=parse_number(features.get("Preis")),
                living_area=parse_number(features.get("Wohnfläche")),
                rooms=parse_number(features.get("Zimmer")),
                is_new_build=True,
                raw={
                    "unit_id": unit_id,
                    "project_id": project_id,
                    "project_name": project.title,
                    "project_url": project.url,
                    "ready": project.raw.get("ready"),
                },
            )
        )
    return units


def _guess_cards(tree: HTMLParser) -> list[Node]:
    """Fallback if the card markup changes: climb up from 'Wohneinheiten' text."""
    cards: dict[int, Node] = {}
    for node in tree.css("body *"):
        own_text = node.text(deep=False, separator=" ")
        if "Wohneinheiten" in own_text:
            card = _card_for(node)
            if card is not None:
                cards[card.mem_id] = card
    return list(cards.values())


def parse_projects(html: str, base_url: str) -> list[ListingData]:
    tree = HTMLParser(html)
    cards = tree.css(CARD_SELECTOR) or _guess_cards(tree)
    listings = [parse_project_card(card, base_url) for card in cards]
    return dedupe([item for item in listings if item is not None])


def parse_total(html: str) -> int | None:
    match = TOTAL_RE.search(html)
    return int(match.group(1)) if match else None


def find_next_url(html: str, current_url: str) -> str | None:
    tree = HTMLParser(html)
    candidates = tree.css('a[rel="next"]')
    for a in tree.css("a[href]"):
        label = (a.attributes.get("aria-label") or a.text() or "").strip().lower()
        if label in ("weiter", "nächste", "nächste seite", "›", "»") or "nächste" in label:
            candidates.append(a)
    for a in candidates:
        href = a.attributes.get("href")
        if href:
            return urljoin(current_url, href)
    return None


class NeubaukompassCrawler(BaseCrawler):
    source = "neubaukompass"

    def _click_page(self, browser: Browser, page_number: int) -> bool | None:
        """Click a numbered pagination button and wait for the cards to be replaced.

        Returns None if there is no such button, False if the page failed to load.
        """
        page = browser.page
        button = page.locator(f'button[data-live-page-number-param="{page_number}"]')
        if not button.count():
            return None
        first_card = page.locator(f"{CARD_SELECTOR} a.nbk-project-card-link").first
        before = first_card.get_attribute("href") if first_card.count() else None
        browser.pause()
        browser.accept_cookies()
        try:
            button.first.click(timeout=10_000)
            page.wait_for_function(
                """before => {
                    const a = document.querySelector('.nbk-project-card a.nbk-project-card-link');
                    return a && a.getAttribute('href') !== before;
                }""",
                arg=before,
                timeout=20_000,
            )
        except Exception:
            log.warning("neubaukompass: page %d did not load", page_number)
            return False
        return True

    def _load_more(self, browser: Browser) -> bool:
        label = re.compile(r"(mehr|weitere)\s+(projekte|anzeigen|laden|ergebnisse)", re.I)
        button = browser.page.get_by_role("button", name=label)
        try:
            if button.count() and button.first.is_visible():
                button.first.click(timeout=5_000)
                browser.page.wait_for_timeout(2_500)
                return True
        except Exception:
            return False
        return False

    def _expand_projects(
        self, browser: Browser, projects: list[ListingData]
    ) -> list[ListingData]:
        """Open each project page and turn its Wohnbeispiele into one listing per flat."""
        listings = []
        for index, project in enumerate(projects, start=1):
            try:
                html = browser.fetch_html(project.url)
            except Exception:
                log.warning("neubaukompass: could not open %s", project.url)
                listings.append(project)
                continue
            units = parse_unit_cards(html, project.url, project)
            # Without published Wohnbeispiele the project itself is all we can offer.
            listings.extend(units or [project])
            if index % 20 == 0:
                log.info("neubaukompass: expanded %d/%d projects", index, len(projects))
        log.info(
            "neubaukompass: %d projects produced %d flats", len(projects), len(listings)
        )
        return listings

    def crawl(self, max_pages: int) -> CrawlResult:
        result = CrawlResult()
        visited: set[str] = set()
        url = self.settings.neubaukompass_search_url
        with Browser(self.settings, "neubaukompass") as browser:
            html = browser.goto(url, wait_for="text=Wohneinheiten")
            if "Wohneinheiten" not in html:
                browser.wait_for_human(
                    lambda: "Wohneinheiten" in browser.page.content(),
                    "Neubaukompass shows a bot check",
                )
                html = browser.page.content()
            visited.add(url)
            total = parse_total(html)
            projects: list[ListingData] = []
            for page in range(1, max_pages + 1):
                listings = parse_projects(html, browser.page.url)
                log.info(
                    "neubaukompass page %d: %d projects (total %s)", page, len(listings), total
                )
                projects.extend(listings)
                next_url = find_next_url(html, browser.page.url)
                clicked = self._click_page(browser, page + 1)
                if clicked:
                    html = browser.page.content()
                elif clicked is False:
                    break
                elif next_url and next_url not in visited:
                    visited.add(next_url)
                    html = browser.goto(next_url, wait_for="text=Wohneinheiten")
                elif self._load_more(browser):
                    html = browser.page.content()
                else:
                    result.complete = True
                    break
            result.listings = self._expand_projects(browser, dedupe(projects))
        result.listings = dedupe(result.listings)
        return result
