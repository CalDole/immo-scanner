"""Kleinanzeigen ad page: fact list, description and local copies of the photos."""

import logging
import re
from datetime import date
from pathlib import Path

import httpx
from pydantic import BaseModel, Field
from selectolax.parser import HTMLParser

from immo_scanner.config import Settings
from immo_scanner.crawlers.parsing import clean_text, parse_number

log = logging.getLogger(__name__)

AD_ID_RE = re.compile(r"/(\d+)-\d+-\d+")
POSTED_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")
YEAR_RE = re.compile(r"(?:18|19|20)\d{2}")
# $_57 is the large lightbox image; the gallery itself shows the smaller $_59.
LARGE_RULE = "?rule=$_57.AUTO"
GALLERY_RULE = "$_59.AUTO"
TAG_FIELDS = {
    "Balkon": "has_balcony",
    "Terrasse": "has_terrace",
    "Einbauküche": "has_fitted_kitchen",
    "Aufzug": "has_elevator",
}


class MediaItem(BaseModel):
    caption: str | None = None
    type: str = "PICTURE"
    url: str
    path: str | None = None


class Ad(BaseModel):
    ad_id: str | None = None
    url: str = ""
    title: str | None = None
    address: str | None = None
    posted_on: date | None = None
    living_area: float | None = None
    rooms: float | None = None
    bedrooms: float | None = None
    bathrooms: float | None = None
    floor: int | None = None
    apartment_type: str | None = None
    year_built: int | None = None
    provision: str | None = None
    has_balcony: bool | None = None
    has_terrace: bool | None = None
    has_fitted_kitchen: bool | None = None
    has_elevator: bool | None = None
    description: str | None = None
    media: list[MediaItem] = Field(default_factory=list)
    facts: dict[str, str] = Field(default_factory=dict)
    features: list[str] = Field(default_factory=list)

    @property
    def photo_count(self) -> int:
        return sum(item.type == "PICTURE" for item in self.media)


def _text(tree: HTMLParser, selector: str) -> str | None:
    node = tree.css_first(selector)
    return clean_text(node.text()) if node is not None else None


def _floor(value: str | None) -> int | None:
    if value and re.fullmatch(r"-?\d{1,2}", value):
        return int(value)
    return None


def _year(value: str | None) -> int | None:
    if value and YEAR_RE.fullmatch(value):
        return int(value)
    return None


def _posted_on(tree: HTMLParser) -> date | None:
    node = tree.css_first("#viewad-extra-info")
    if node is None:
        return None
    match = POSTED_RE.search(node.text())
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _photos(tree: HTMLParser) -> list[MediaItem]:
    seen: set[str] = set()
    photos = []
    for image in tree.css("#viewad-product .galleryimage-element img"):
        src = image.attributes.get("data-imgsrc") or image.attributes.get("src") or ""
        if "prod-ads/images" not in src:
            continue
        base = src.split("?")[0]
        if base in seen:
            continue
        seen.add(base)
        photos.append(
            MediaItem(caption=clean_text(image.attributes.get("alt")), url=base + LARGE_RULE)
        )
    return photos


def parse_ad(html: str, url: str | None = None) -> Ad:
    tree = HTMLParser(html)
    facts: dict[str, str] = {}
    for item in tree.css("li.addetailslist--detail"):
        value_node = item.css_first(".addetailslist--detail--value")
        label = clean_text(item.text(deep=False))
        if label and value_node is not None:
            facts[label] = clean_text(value_node.text()) or ""
    features = [text for tag in tree.css(".checktag") if (text := clean_text(tag.text()))]
    flags = {field: True for tag, field in TAG_FIELDS.items() if tag in features}
    desc_node = tree.css_first("#viewad-description-text")
    description = desc_node.text(separator="\n", strip=True) if desc_node else None
    ad_id = AD_ID_RE.search(url or "")
    return Ad(
        ad_id=ad_id.group(1) if ad_id else None,
        url=url or "",
        title=_text(tree, "#viewad-title"),
        address=_text(tree, "#viewad-locality"),
        posted_on=_posted_on(tree),
        living_area=parse_number(facts.get("Wohnfläche")),
        rooms=parse_number(facts.get("Zimmer")),
        bedrooms=parse_number(facts.get("Schlafzimmer")),
        bathrooms=parse_number(facts.get("Badezimmer")),
        floor=_floor(facts.get("Etage")),
        apartment_type=facts.get("Wohnungstyp") or None,
        year_built=_year(facts.get("Baujahr")),
        provision=facts.get("Provision") or None,
        description=description or None,
        media=_photos(tree),
        facts=facts,
        features=features,
        **flags,
    )


def _filename(index: int) -> str:
    return f"foto_{index:02d}.jpg"


def download_media(ad: Ad, settings: Settings, client: httpx.Client) -> Path | None:
    """Save each gallery photo; files that already exist are kept."""
    if settings.kleinanzeigen_photo_size == "none" or not ad.ad_id or not ad.media:
        return None
    folder = settings.media_dir / "kleinanzeigen" / ad.ad_id
    folder.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": settings.user_agent, "Referer": "https://www.kleinanzeigen.de/"}
    for index, item in enumerate(ad.media, start=1):
        target = folder / _filename(index)
        item.path = str(target)
        if target.exists():
            continue
        fallback = item.url.replace("$_57.AUTO", GALLERY_RULE)
        saved = False
        for photo_url in (item.url, fallback):
            try:
                response = client.get(photo_url, headers=headers)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                log.warning("Could not download %s: %s", photo_url, exc)
                continue
            target.write_bytes(response.content)
            item.url = str(response.url)
            saved = True
            break
        if not saved:
            item.path = None
    return folder
