"""Full Neubaukompass unit exposé: facts, texts, documents and local copies of the media.

The detail list carries machine-readable `data-scout-id` attributes ("numberOfRooms",
"calculatedPricePerSqm", ...), which are stable across projects; the German labels are
only used for the few items that have no id, such as the address. Longer texts sit in
sections with fixed anchor ids (`#lage`, `#beschreibung`, ...).
"""

import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from pydantic import BaseModel, Field
from selectolax.parser import HTMLParser, Node

from immo_scanner.config import Settings
from immo_scanner.crawlers.parsing import clean_text, parse_floor, parse_number

log = logging.getLogger(__name__)

BASE_URL = "https://www.neubaukompass.de"
PHOTO_PATH = "/pictures/listings/"
UNIT_URL_RE = re.compile(r"/neubau/[\w-]+/(\d+)/expose-(\d+)/")
FORMAT_RE = re.compile(r"/format/(\w+)")
YEAR_RE = re.compile(r"(?:18|19|20)\d{2}")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"\+?\d[\d\s/().-]{7,}\d")
ENERGY_CLASS_ATTR = "data-components--molecules--energy--energy-energy-efficiency-class-value"
ENERGY_DEMAND_ATTR = "data-components--molecules--energy--energy-energy-demand-value"
# "Energieausweistyp : Bedarfsausweis", "Erstellungsdatum: ab dem 01.05.2014"
ENERGY_LABELS = {
    "energy_certificate_type": r"Energieausweistyp",
    "energy_source": r"Wesentliche[rn]?\s+Energietr[äa]ger",
    "energy_certificate_date": r"Erstellungsdatum",
}
SECTIONS = {
    "location_text": "lage",
    "equipment_text": "ausstattungimueberblick",
    "description": "beschreibung",
}
# Links are grouped under these headings inside #dokumenteundlinks.
DOCUMENT_GROUPS = {
    "floor_plan_url": re.compile(r"Grundriss", re.IGNORECASE),
    "energy_certificate_url": re.compile(r"Energieausweis|Sonstige Dokumente", re.IGNORECASE),
    "website_url": re.compile(r"Links zu Internetseiten|Internetseite", re.IGNORECASE),
}
# The website heading reads "Links zu Internetseiten / virtuellen Rundgängen etc." and
# usually lists the 360° tour before the developer's own page.
TOUR_RE = re.compile(r"kuula\.co|matterport|ogulo|360|rundgang", re.IGNORECASE)


class MediaItem(BaseModel):
    caption: str | None = None
    type: str
    url: str
    path: str | None = None


class Unit(BaseModel):
    unit_id: str | None = None
    object_number: str | None = None
    project_id: str | None = None
    project_name: str | None = None
    project_url: str | None = None
    title: str | None = None
    address: str | None = None
    unit_type: str | None = None
    price: float | None = None
    price_per_m2: float | None = None
    floor: int | None = None
    total_floors: int | None = None
    rooms: float | None = None
    bedrooms: float | None = None
    bathrooms: float | None = None
    living_area: float | None = None
    year_built: int | None = None
    last_renovation_year: int | None = None
    available_from: str | None = None
    has_commission: bool | None = None
    buyer_commission: str | None = None
    seller_commission: str | None = None
    energy_certificate_type: str | None = None
    energy_source: str | None = None
    energy_certificate_date: str | None = None
    energy_efficiency_class: str | None = None
    final_energy_kwh_m2a: float | None = None
    has_balcony_terrace: bool | None = None
    features: list[str] = Field(default_factory=list)
    location_text: str | None = None
    equipment_text: str | None = None
    description: str | None = None
    floor_plan_url: str | None = None
    energy_certificate_url: str | None = None
    website_url: str | None = None
    virtual_tour_url: str | None = None
    provider_name: str | None = None
    provider_address: str | None = None
    contact_name: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    media: list[MediaItem] = Field(default_factory=list)
    facts: dict[str, str] = Field(default_factory=dict)

    @property
    def photo_count(self) -> int:
        return sum(item.type == "PICTURE" for item in self.media)

    @property
    def floor_plan_count(self) -> int:
        return sum(item.type in ("FLOORPLAN", "DOCUMENT") for item in self.media)


def _int(value: object) -> int | None:
    number = parse_number(str(value)) if value is not None else None
    return int(number) if number is not None else None


_UG_RE = re.compile(r"\bug\b|untergeschoss|souterrain|kellergeschoss", re.IGNORECASE)


def _floor(value: object) -> int | None:
    """'4', 'EG' or '2. UG'. The exposé writes the ground floor as a word, not 0."""
    text = clean_text(str(value)) if value is not None else None
    if not text:
        return None
    if _UG_RE.search(text):
        match = re.search(r"\d+", text)
        return -int(match.group(0)) if match else -1
    if re.fullmatch(r"hochparterre", text, re.IGNORECASE):
        return 0
    # parse_floor knows "EG" / "3. OG"; a bare "4" is only a number.
    named = parse_floor(text)
    return named if named is not None else _int(text)


def _year(text: str | None) -> int | None:
    match = YEAR_RE.search(text or "")
    return int(match.group(0)) if match else None


def _lines(node: Node) -> list[str]:
    for child in node.css("svg, script, style"):
        child.decompose()
    parts = (clean_text(part) for part in node.text(separator="\n").split("\n"))
    return [part for part in parts if part]


def detail_items(tree: HTMLParser) -> list[tuple[str, str, str]]:
    """The exposé detail list as (scout id, German label, value) triples."""
    root = tree.css_first("#details") or tree
    items = []
    for node in root.css("li[data-scout-id]"):
        lines = _lines(node)
        if len(lines) < 2:
            continue
        items.append((node.attributes.get("data-scout-id") or "", lines[0], lines[1]))
    return items


def facts(tree: HTMLParser) -> dict[str, str]:
    """{'Preis/m²': '7.900 €', 'Zimmeranzahl': '3', ...} keyed by the visible label."""
    return {label: value for _, label, value in detail_items(tree)}


def section_text(tree: HTMLParser, anchor: str) -> str | None:
    """Body text of a section, without its heading."""
    node = tree.css_first(f"#{anchor}")
    if node is None:
        return None
    for heading in node.css("h2, h3"):
        heading.decompose()
    for button in node.css("button, script, style, svg"):
        button.decompose()
    return clean_text(node.text(separator="\n").replace("\n", " ")) or None


def documents(tree: HTMLParser) -> dict[str, str]:
    """Grundriss, Energieausweis, virtual tour, brochure and website links by heading."""
    section = tree.css_first("#dokumenteundlinks")
    if section is None:
        return {}
    found: dict[str, str] = {}
    heading = ""
    for node in section.css("h3, a[href]"):
        if node.tag == "h3":
            heading = clean_text(node.text()) or ""
            continue
        href = node.attributes.get("href")
        if not href:
            continue
        label = f"{heading} {clean_text(node.text()) or ''}"
        for field, pattern in DOCUMENT_GROUPS.items():
            if not pattern.search(label):
                continue
            if field == "website_url":
                if href.lower().split("?")[0].endswith(".pdf"):
                    field = "brochure_url"
                elif TOUR_RE.search(f"{href} {label}"):
                    field = "virtual_tour_url"
            found.setdefault(field, urljoin(BASE_URL, href))
            break
    return found


def gallery(tree: HTMLParser) -> list[MediaItem]:
    """Photos of the flat; the page also embeds unrelated logos and video stills.

    Every photo appears several times, as a thumbnail and as the full-size slide, which
    differ only in the resize suffix - hence the keying on the part before it.
    """
    section = tree.css_first("#bilder")
    if section is None:
        return []
    urls: dict[str, str] = {}
    for node in section.css("a[data-src], img[data-src], img[src]"):
        url = node.attributes.get("data-src") or node.attributes.get("src") or ""
        if PHOTO_PATH in url:
            urls.setdefault(url.split("/ORIG/")[0], url)
    return [MediaItem(type="PICTURE", url=url) for url in urls.values()]


def energy(tree: HTMLParser) -> dict[str, Any]:
    section = tree.css_first("#energienachweis")
    if section is None:
        return {}
    text = section.text(separator="\n")
    values: dict[str, Any] = {
        field: clean_text(match.group(1))
        for field, label in ENERGY_LABELS.items()
        if (match := re.search(rf"{label}\s*:\s*([^\n]+)", text))
    }
    if (holder := section.css_first(f"[{ENERGY_CLASS_ATTR}]")) is not None:
        values["energy_efficiency_class"] = (
            clean_text(holder.attributes.get(ENERGY_CLASS_ATTR)) or None
        )
        values["final_energy_kwh_m2a"] = parse_number(holder.attributes.get(ENERGY_DEMAND_ATTR))
    if values.get("energy_source", "").lower().startswith("keine angabe"):
        values["energy_source"] = None
    return values


def provider(tree: HTMLParser) -> dict[str, str | None]:
    section = tree.css_first("#anbieter")
    if section is None:
        return {}
    name_node = section.css_first(".nbk-project-developer__inner-wrapper h5") or section.css_first(
        "h5"
    )
    address_node = name_node.next if name_node is not None else None
    while address_node is not None and address_node.tag != "p":
        address_node = address_node.next
    text = section.text(separator="\n")
    email = EMAIL_RE.search(text)
    phone = PHONE_RE.search(text)
    contact = None
    if match := re.search(r"Ansprechpartner\s*\n+\s*([^\n]+)", text):
        contact = clean_text(match.group(1))
    return {
        "provider_name": clean_text(name_node.text()) if name_node else None,
        "provider_address": clean_text(address_node.text()) if address_node else None,
        "contact_name": contact,
        "contact_phone": clean_text(phone.group(0)) if phone else None,
        "contact_email": email.group(0) if email else None,
    }


def features(tree: HTMLParser) -> list[str]:
    section = tree.css_first("#merkmale")
    if section is None:
        return []
    return [text for node in section.css("li") if (text := clean_text(node.text()))]


def parse_unit(html: str, url: str | None = None) -> Unit:
    tree = HTMLParser(html)
    by_scout = {scout: value for scout, _, value in detail_items(tree) if scout}
    by_label = facts(tree)
    docs = documents(tree)
    feature_list = features(tree)

    title_node = tree.css_first("h1")
    project_link = tree.css_first('a[href*="/neubau/"][href$=".html"]')
    project_url = (
        urljoin(BASE_URL, project_link.attributes["href"]) if project_link is not None else None
    )
    project_name = clean_text(project_link.text()) if project_link is not None else None

    unit_id = by_scout.get("unitId") or by_label.get("ID")
    project_id = None
    if match := UNIT_URL_RE.search(url or project_url or ""):
        project_id, unit_id = match.group(1), unit_id or match.group(2)

    commission = by_scout.get("hasCourtage") or by_label.get("Provision")
    balcony = any("balkon" in item.lower() or "terrasse" in item.lower() for item in feature_list)

    unit = Unit(
        unit_id=unit_id,
        object_number=by_scout.get("realEstate.objectNumber") or by_label.get("Objektnummer"),
        project_id=project_id,
        project_name=project_name,
        project_url=project_url,
        title=clean_text(title_node.text()) if title_node else None,
        address=by_label.get("Adresse"),
        unit_type=by_scout.get("unitType"),
        price=parse_number(by_scout.get("purchasePrice")),
        price_per_m2=parse_number(by_scout.get("calculatedPricePerSqm")),
        floor=_floor(by_scout.get("floor")),
        total_floors=_int(by_scout.get("numberOfFloors")),
        rooms=parse_number(by_scout.get("numberOfRooms")),
        bedrooms=parse_number(by_scout.get("numberOfBedRooms")),
        bathrooms=parse_number(by_scout.get("numberOfBathRooms")),
        living_area=parse_number(by_scout.get("livingSpace")),
        year_built=_year(by_scout.get("constructionYear")),
        last_renovation_year=_year(by_scout.get("lastRefurbishment")),
        available_from=by_scout.get("freeFrom"),
        has_commission=commission.lower().startswith("ja") if commission else None,
        buyer_commission=by_label.get("Provision für Käufer"),
        seller_commission=by_label.get("Provision für Verkäufer"),
        has_balcony_terrace=balcony or None,
        features=feature_list,
        floor_plan_url=docs.get("floor_plan_url"),
        energy_certificate_url=docs.get("energy_certificate_url"),
        website_url=docs.get("website_url"),
        virtual_tour_url=docs.get("virtual_tour_url"),
        media=gallery(tree),
        facts=by_label,
        **energy(tree),
        **provider(tree),
    )
    for field, anchor in SECTIONS.items():
        setattr(unit, field, section_text(tree, anchor))
    if unit.floor_plan_url:
        unit.media.append(MediaItem(caption="Grundriss", type="FLOORPLAN", url=unit.floor_plan_url))
    if unit.energy_certificate_url:
        unit.media.append(
            MediaItem(caption="Energieausweis", type="DOCUMENT", url=unit.energy_certificate_url)
        )
    if brochure := docs.get("brochure_url"):
        unit.media.append(MediaItem(caption="Expose", type="DOCUMENT", url=brochure))
    return unit


def _media_url(item: MediaItem, size: str) -> str:
    """Photos are served as '<base>.jpg/ORIG/resize/950x1200>/format/jpg'."""
    if item.type != "PICTURE" or "/ORIG/" not in item.url:
        return item.url
    return item.url.split("/ORIG/")[0] if size == "original" else item.url


def _filename(index: int, item: MediaItem, url: str) -> str:
    # A resized photo is re-encoded, so "/format/jpg" wins over the original ".png".
    if match := FORMAT_RE.search(url):
        suffix = f".{match.group(1)}"
    else:
        suffix = Path(url.split("?")[0].split("/ORIG/")[0]).suffix or ".jpg"
    caption = re.sub(r"[^\w-]+", "_", item.caption or "").strip("_")[:40]
    prefix = {"FLOORPLAN": "grundriss", "DOCUMENT": "dokument"}.get(item.type, "foto")
    return f"{prefix}_{index:02d}{'_' + caption if caption else ''}{suffix}"


def download_media(unit: Unit, settings: Settings, fetch_bytes) -> Path | None:
    """Save photos, floor plan and Energieausweis; files that already exist are kept.

    Photos live behind the same bot protection as the pages, so `fetch_bytes` has to be
    backed by the browser session rather than a plain HTTP client.
    """
    if settings.neubaukompass_photo_size == "none" or not unit.unit_id or not unit.media:
        return None
    folder = settings.media_dir / "neubaukompass" / unit.unit_id
    folder.mkdir(parents=True, exist_ok=True)
    for index, item in enumerate(unit.media, start=1):
        url = _media_url(item, settings.neubaukompass_photo_size)
        target = folder / _filename(index, item, url)
        item.path = str(target)
        if target.exists():
            continue
        try:
            target.write_bytes(fetch_bytes(url))
        except Exception as exc:
            log.warning("Could not download %s: %s", url, exc)
            item.path = None
    return folder
