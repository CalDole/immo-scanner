"""BSK Immobilien exposé: the Objektdetails table, text sections and photos.

Amenities come from the table first (Anzahl Balkone, Ausstattung/Merkmale, Anzahl
Stellplätze) and from the Ausstattung and Objektbeschreibung text otherwise.
"""

import logging
import re
from pathlib import Path
from urllib.parse import urljoin

import httpx
from pydantic import BaseModel, Field
from selectolax.parser import HTMLParser

from immo_scanner.config import Settings
from immo_scanner.crawlers.bsk import BASE_URL, expose_id
from immo_scanner.crawlers.parsing import clean_text, parse_hausgeld, parse_number
from immo_scanner.details.model import DetailData
from immo_scanner.details.text import (
    TEXT_FEATURES,
    TYPOGRAPHY,
    extract_from_text,
    feature,
    parse_kwh,
)

log = logging.getLogger(__name__)

OBJECT_ID_RE = re.compile(r"Objekt-ID\s*([\w-]+)")
YEAR_RE = re.compile(r"(?:18|19|20)\d{2}")
FLOOR_RE = re.compile(r"-?\d{1,2}")
GROUND_FLOOR_RE = re.compile(r"^(?:EG|Erdgeschoss|Hochparterre)\b", re.I)
# Values of the "Ausstattung/Merkmale" row.
MERKMALE_FLAGS = {
    "Fahrstuhl": "has_elevator",
    "Personenaufzug": "has_elevator",
    "Einbauküche": "has_fitted_kitchen",
    "Gäste-WC": "has_guest_toilet",
    "Gartennutzung": "has_garden",
    "barrierefrei": "is_barrier_free",
}
BATHROOM_WORDS = ("Badewanne", "Dusche", "Tageslichtbad")
SECTIONS = {
    "Ausstattung": "equipment_text",
    "Objektbeschreibung": "description",
    "Lage": "location_text",
}


class MediaItem(BaseModel):
    caption: str | None = None
    type: str = "PICTURE"
    url: str
    path: str | None = None


class Expose(BaseModel):
    object_id: str | None = None
    url: str = ""
    title: str | None = None
    property_type: str | None = None
    price: float | None = None
    living_area: float | None = None
    usable_area: float | None = None
    rooms: float | None = None
    bedrooms: float | None = None
    bathrooms: float | None = None
    floor: int | None = None
    total_floors: int | None = None
    year_built: int | None = None
    property_condition: str | None = None
    interior_quality: str | None = None
    heating_type: str | None = None
    energy_source: str | None = None
    energy_certificate_type: str | None = None
    final_energy_type: str | None = None
    final_energy_kwh_m2a: float | None = None
    energy_efficiency_class: str | None = None
    energy_certificate_valid_until: str | None = None
    is_rented: bool | None = None
    monthly_rent: float | None = None
    available_from: str | None = None
    hausgeld: float | None = None
    buyer_commission: str | None = None
    flooring: str | None = None
    bathroom_features: str | None = None
    balcony_count: int | None = None
    terrace_count: int | None = None
    parking_spaces: int | None = None
    parking_type: str | None = None
    has_balcony: bool | None = None
    has_terrace: bool | None = None
    has_cellar: bool | None = None
    has_elevator: bool | None = None
    has_parking: bool | None = None
    has_fitted_kitchen: bool | None = None
    has_garden: bool | None = None
    has_guest_toilet: bool | None = None
    has_monument_protection: bool | None = None
    is_barrier_free: bool | None = None
    equipment_text: str | None = None
    description: str | None = None
    location_text: str | None = None
    media: list[MediaItem] = Field(default_factory=list)
    facts: dict[str, str] = Field(default_factory=dict)
    features: list[str] = Field(default_factory=list)

    @property
    def photo_count(self) -> int:
        return sum(item.type == "PICTURE" for item in self.media)

    @property
    def floor_plan_count(self) -> int:
        return sum(item.type == "FLOORPLAN" for item in self.media)

    @property
    def prose(self) -> str:
        """Ausstattung and Objektbeschreibung; Lage describes the area, not the flat."""
        return "\n\n".join(text for text in (self.equipment_text, self.description) if text)

    def to_detail(self) -> DetailData:
        text = "\n\n".join(
            text for text in (self.equipment_text, self.description, self.location_text) if text
        )
        return DetailData(
            year_built=self.year_built,
            floor=self.floor,
            total_floors=self.total_floors,
            heating_type=self.heating_type,
            energy_source=self.energy_source,
            has_elevator=self.has_elevator,
            has_parking=self.has_parking,
            has_cellar=self.has_cellar,
            has_balcony=self.has_balcony,
            balcony_count=self.balcony_count,
            description=text or None,
            raw={"facts": self.facts, "features": self.features},
        )


def facts(tree: HTMLParser) -> dict[str, str]:
    """Every Objektdetails row, label -> value text ('Etage' -> '1')."""
    rows: dict[str, str] = {}
    for row in tree.css(".objektdetails table tr"):
        label, value = row.css_first("td"), row.css_first("th")
        if label is None or value is None:
            continue
        spans = [text for span in value.css("span") if (text := clean_text(span.text()))]
        text = " ".join(spans) if spans else clean_text(value.text())
        if (name := clean_text(label.text())) and text:
            rows[name] = text
    return rows


def _features(tree: HTMLParser) -> list[str]:
    for row in tree.css(".objektdetails table tr"):
        label = row.css_first("td")
        if label is not None and clean_text(label.text()) == "Ausstattung/Merkmale":
            return [text for span in row.css("th span") if (text := clean_text(span.text()))]
    return []


def sections(tree: HTMLParser) -> dict[str, str]:
    """Text below the h2 headings, keyed by heading ('Ausstattung', 'Lage', ...)."""
    texts = {}
    for heading in tree.css("h2"):
        title = clean_text(heading.text())
        row = heading.parent.parent if heading.parent is not None else None
        body = row.css_first(".text") if row is not None else None
        if title in SECTIONS and body is not None:
            texts[title] = body.text(separator="\n", strip=True).translate(TYPOGRAPHY)
    return texts


def _int(value: str | None) -> int | None:
    number = parse_number(value)
    return int(number) if number is not None else None


def _floor(value: str | None) -> int | None:
    if not value:
        return None
    if GROUND_FLOOR_RE.match(value):
        return 0
    match = FLOOR_RE.search(value)
    return int(match.group(0)) if match else None


def _year(value: str | None) -> int | None:
    match = YEAR_RE.search(value or "")
    return int(match.group(0)) if match else None


def _heating(value: str | None) -> str | None:
    """'Fernwärme Fernwärme' -> 'Fernwärme'; the page prints type and fuel side by side."""
    if not value:
        return None
    words = list(dict.fromkeys(value.split()))
    return ", ".join(words)


def _available(value: str | None) -> str | None:
    """'Ja verfügbar 31.07.2027' -> '31.07.2027'."""
    if not value:
        return None
    text = re.sub(r"^(?:Ja\s+)?verfügbar\s*", "", value, flags=re.I)
    return clean_text(text) or None


def _bathroom(features: list[str]) -> str | None:
    words = [
        word for word in BATHROOM_WORDS if any(word in feature_text for feature_text in features)
    ]
    return ", ".join(words) or None


def _parking(value: str | None) -> tuple[int | None, str | None]:
    """'1 (Freiplatz)' -> (1, 'Freiplatz')."""
    if not value:
        return None, None
    kind = re.search(r"\(([^)]+)\)", value)
    return _int(value), kind.group(1) if kind else None


def _media(tree: HTMLParser) -> list[MediaItem]:
    seen: set[str] = set()
    media = []
    for link in tree.css("a[href*='/files/uploads/objekt/']"):
        href = urljoin(BASE_URL, link.attributes.get("href") or "")
        if href in seen:
            continue
        seen.add(href)
        title = clean_text(link.attributes.get("title"))
        image = link.css_first("img")
        if "_webp/" in href:
            caption = clean_text(image.attributes.get("alt")) if image is not None else title
            media.append(MediaItem(caption=caption, url=href))
        elif title and "grundriss" in title.lower():
            media.append(MediaItem(caption=title, type="FLOORPLAN", url=href))
    return media


def parse_expose(html: str, url: str | None = None) -> Expose:
    tree = HTMLParser(html)
    fact = facts(tree)
    features = _features(tree)
    texts = sections(tree)
    title_node = tree.css_first("h1")
    object_id = OBJECT_ID_RE.search(tree.body.text(separator=" ") if tree.body else "")

    energy_type, energy = next(
        (
            (kind, fact[label])
            for label, kind in (
                ("Energieverbrauchkennwert in kWh/(m²*a)", "Endenergieverbrauch"),
                ("Endenergiebedarf in kWh/(m²*a)", "Endenergiebedarf"),
            )
            if fact.get(label)
        ),
        (None, None),
    )
    balconies = _int(fact.get("Anzahl Balkone"))
    loggias = _int(fact.get("Anzahl Loggia"))
    if balconies is not None or loggias is not None:
        balconies = (balconies or 0) + (loggias or 0)
    terraces = _int(fact.get("Anzahl Terrassen"))
    parking_spaces, parking_type = _parking(fact.get("Anzahl Stellplätze"))
    rented = fact.get("Vermietet")
    is_rented = (
        rented.lower().startswith("ja")
        if rented
        else (True if fact.get("Mtl. Mieteinnahmen") else None)
    )

    expose = Expose(
        object_id=object_id.group(1) if object_id else expose_id(url or ""),
        url=url or "",
        title=clean_text(title_node.text()) if title_node else None,
        property_type=fact.get("Objekttyp"),
        price=parse_number(fact.get("Kaufpreis")),
        living_area=parse_number(fact.get("Wohnfläche")),
        usable_area=parse_number(fact.get("Nutzfläche")),
        rooms=parse_number(fact.get("Zimmer")),
        bedrooms=parse_number(fact.get("Anzahl Schlafzimmer")),
        bathrooms=parse_number(fact.get("Anzahl Badezimmer")),
        floor=_floor(fact.get("Etage")),
        total_floors=_int(fact.get("Anzahl Etagen")),
        year_built=_year(fact.get("Baujahr")),
        property_condition=fact.get("Zustand"),
        interior_quality=fact.get("Ausstattungs-Kategorie"),
        heating_type=_heating(fact.get("Heizung")),
        energy_source=fact.get("Wesentlicher Energieträger"),
        energy_certificate_type=fact.get("Energieausweistyp"),
        final_energy_type=energy_type,
        final_energy_kwh_m2a=parse_kwh(energy),
        energy_efficiency_class=fact.get("Energieeffizienzklasse"),
        energy_certificate_valid_until=fact.get("Energieausweis gültig bis"),
        is_rented=is_rented,
        monthly_rent=parse_number(fact.get("Mtl. Mieteinnahmen") or fact.get("Kaltmiete")),
        available_from=_available(fact.get("Verfügbarkeit")),
        hausgeld=parse_number(fact.get("Hausgeld")),
        buyer_commission=fact.get("Provision/Courtage"),
        flooring=fact.get("Bodenbelag"),
        bathroom_features=_bathroom(features),
        balcony_count=balconies,
        terrace_count=terraces,
        parking_spaces=parking_spaces,
        parking_type=parking_type,
        has_balcony=balconies > 0 if balconies is not None else None,
        has_terrace=terraces > 0 if terraces is not None else None,
        has_parking=True if parking_spaces else None,
        equipment_text=texts.get("Ausstattung"),
        description=texts.get("Objektbeschreibung"),
        location_text=texts.get("Lage"),
        media=_media(tree),
        facts=fact,
        features=features,
    )
    for tag, field in MERKMALE_FLAGS.items():
        if any(tag.lower() == text.lower() for text in features):
            setattr(expose, field, True)

    prose = expose.prose
    for field, pattern in TEXT_FEATURES.items():
        if getattr(expose, field) is None and prose:
            setattr(expose, field, feature(prose, pattern))
    if expose.has_balcony and expose.balcony_count is None:
        expose.balcony_count = extract_from_text(prose).balcony_count
    if expose.hausgeld is None:
        expose.hausgeld = parse_hausgeld(prose)
    return expose


def _filename(index: int, item: MediaItem) -> str:
    prefix = "grundriss" if item.type == "FLOORPLAN" else "foto"
    suffix = Path(item.url.split("?")[0]).suffix or ".jpg"
    return f"{prefix}_{index:02d}{suffix}"


def download_media(expose: Expose, settings: Settings, client: httpx.Client) -> Path | None:
    """Save each photo and floor plan; files that already exist are kept."""
    if settings.bsk_photo_size == "none" or not expose.object_id or not expose.media:
        return None
    folder = settings.media_dir / "bsk" / expose.object_id
    folder.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": settings.user_agent, "Referer": "https://www.bsk-immobilien.de/"}
    for index, item in enumerate(expose.media, start=1):
        target = folder / _filename(index, item)
        item.path = str(target)
        if target.exists():
            continue
        try:
            response = client.get(item.url, headers=headers)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("Could not download %s: %s", item.url, exc)
            item.path = None
            continue
        target.write_bytes(response.content)
    return folder
