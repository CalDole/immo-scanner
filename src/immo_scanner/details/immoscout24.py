"""Full ImmoScout24 exposé: all facts, texts, and local copies of photos and floor plans."""

import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field
from selectolax.parser import HTMLParser

from immo_scanner.config import Settings
from immo_scanner.crawlers.parsing import clean_text, parse_number
from immo_scanner.details.text import FEATURES, GUEST_TOILET_RE, feature

log = logging.getLogger(__name__)

KEYVALUES_RE = re.compile(r"keyValues\s*=\s*(\{.*?\})\s*[;,\n]", re.S)
YEAR_RE = re.compile(r"(?:18|19|20)\d{2}")
EFFICIENCY_CLASS_RE = re.compile(r"A\+{0,3}|[B-H]")
INTERNET_RE = re.compile(
    r"Internet\s*Geschwindigkeit.{0,600}?(bis zu\s*[\d.,]+\s*[MG]Bit/s)", re.S | re.I
)
TERRACE_RE = re.compile(r"Terrasse")
YES_NO = {"y": True, "n": False, "true": True, "false": False}
# obj_parkingSpace; "no_information" means the seller left it empty.
PARKING_TYPES = {
    "garage": "Garage",
    "underground_garage": "Tiefgarage",
    "outside": "Außenstellplatz",
    "carport": "Carport",
    "duplex": "Duplex",
    "car_park": "Parkhaus",
}
SECTION_HEADINGS = ("Objektbeschreibung", "Ausstattung", "Sonstiges")
LARGE_SUFFIX = "/ORIG/resize/1106x830%3E/format/webp/quality/73"


def keyvalues(html: str) -> dict[str, Any]:
    match = KEYVALUES_RE.search(html)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}


def facts(tree: HTMLParser) -> dict[str, str]:
    """The exposé fact list: {'wohnflaeche-ca': '71 m²', 'energieeffizienzklasse': 'D', ...}."""
    result = {}
    for node in tree.css("dd[class*=is24qa-]"):
        for popover in node.css(".popover-icon-anchor"):
            popover.decompose()
        for cls in (node.attributes.get("class") or "").split():
            if cls.startswith("is24qa-"):
                text = clean_text(node.text(separator=" ")) or ""
                img = node.css_first("img[alt]")
                if not text and img is not None:
                    text = img.attributes.get("alt") or ""
                result[cls.removeprefix("is24qa-")] = text
    return result


def features(tree: HTMLParser) -> list[str]:
    """Feature tags above the fact list: ['lift', 'guestToilet', 'builtInKitchen', ...]."""
    names = []
    for node in tree.css("span[data-qa^=is24qa-]"):
        qa = node.attributes.get("data-qa") or ""
        if qa.endswith("-label"):
            names.append(qa.removeprefix("is24qa-").removesuffix("-label"))
    return list(dict.fromkeys(names))


def parking(kv: dict[str, Any], fact: dict[str, str]) -> tuple[bool | None, str | None]:
    """(True, '1 Tiefgaragen-Stellplatz') from the fact list, else the type from keyValues."""
    label = fact.get("garage-stellplatz") or PARKING_TYPES.get(str(kv.get("obj_parkingSpace")))
    return (True, label) if label else (None, None)


def sections(tree: HTMLParser) -> dict[str, str]:
    """Description blocks below the headings 'Objektbeschreibung', 'Ausstattung', 'Sonstiges'."""
    result = {}
    for heading in tree.css("h3"):
        title = clean_text(heading.text()) or ""
        if title not in SECTION_HEADINGS:
            continue
        body = heading.next
        while body is not None and body.tag == "-text":
            body = body.next
        if body is not None:
            result[title] = body.text(separator="\n", strip=True)
    return result


def gallery(html: str) -> dict[str, Any]:
    start = html.find("galleryData:")
    if start < 0:
        return {}
    try:
        data, _ = json.JSONDecoder().raw_decode(html[start + len("galleryData:") :].lstrip())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _int(value: object) -> int | None:
    number = parse_number(str(value)) if value is not None else None
    return int(number) if number is not None else None


def _efficiency_class(value: str | None) -> str | None:
    """'A_PLUS' / 'A_PLUS_PLUS' / 'D' -> 'A+' / 'A++' / 'D'; 'NO_INFORMATION' -> None."""
    value = (value or "").strip().upper().replace("_PLUS", "+").replace("_", "")
    return value if EFFICIENCY_CLASS_RE.fullmatch(value) else None


def _year(text: str | None) -> int | None:
    match = YEAR_RE.search(text or "")
    return int(match.group(0)) if match else None


class MediaItem(BaseModel):
    caption: str | None = None
    type: str
    url: str
    path: str | None = None


class Expose(BaseModel):
    scout_id: str | None = None
    object_id: str | None = None
    title: str | None = None
    address: str | None = None
    property_type: str | None = None
    is_rented: bool | None = None
    living_area: float | None = None
    usable_area: float | None = None
    available_from: str | None = None
    rooms: float | None = None
    bedrooms: float | None = None
    bathrooms: float | None = None
    price: float | None = None
    price_per_m2: float | None = None
    buyer_commission: str | None = None
    year_built: int | None = None
    heating_type: str | None = None
    energy_source: str | None = None
    energy_certificate: str | None = None
    energy_certificate_type: str | None = None
    final_energy_kwh_m2a: float | None = None
    # "Endenergiebedarf" (calculated demand) or "Endenergieverbrauch" (measured use).
    final_energy_type: str | None = None
    energy_efficiency_class: str | None = None
    energy_certificate_year_built: int | None = None
    property_condition: str | None = None
    interior_quality: str | None = None
    has_balcony_terrace: bool | None = None
    has_terrace: bool | None = None
    has_cellar: bool | None = None
    has_parking: bool | None = None
    parking_type: str | None = None
    parking_spaces: int | None = None
    parking_price: float | None = None
    has_fitted_kitchen: bool | None = None
    has_guest_toilet: bool | None = None
    internet_speed: str | None = None
    internet_mbit: int | None = None
    description: str | None = None
    equipment_text: str | None = None
    other_text: str | None = None
    location_text: str | None = None
    media: list[MediaItem] = Field(default_factory=list)
    facts: dict[str, str] = Field(default_factory=dict)
    features: list[str] = Field(default_factory=list)

    @property
    def photo_count(self) -> int:
        return sum(item.type == "PICTURE" for item in self.media)

    @property
    def floor_plan_count(self) -> int:
        return sum(item.type == "FLOORPLAN" or item.type == "DOCUMENT" for item in self.media)


def parse_expose(html: str) -> Expose:
    tree = HTMLParser(html)
    kv = keyvalues(html)
    fact = facts(tree)
    tags = features(tree)
    texts = sections(tree)

    scout_id, object_id = kv.get("obj_scoutId"), kv.get("obj_objectnumber")
    if node := tree.css_first(".scoutId"):
        ids = clean_text(node.text()) or ""
        if match := re.search(r"Scout-ID\s*:\s*(\d+)", ids):
            scout_id = match.group(1)
        if match := re.search(r"Objekt-ID\.?\s*:\s*([^|]+)", ids):
            object_id = match.group(1).strip()

    title_node = tree.css_first("#expose-title") or tree.css_first("h1")
    address_node = tree.css_first("[data-qa=is24-expose-address]") or tree.css_first(
        ".address-block"
    )
    location_node = tree.css_first(".expose-location-description")
    location_text = location_node.text(separator="\n", strip=True) if location_node else None
    flat_text = "\n".join(texts.values())

    body = tree.body.text(separator="\n", strip=True) if tree.body else ""
    internet = INTERNET_RE.search(body)
    energy_type = next(
        (key for key in ("endenergieverbrauch", "endenergiebedarf") if fact.get(key)), None
    )
    energy = fact.get(energy_type) if energy_type else None

    images = gallery(html)
    media = [
        MediaItem(
            caption=image.get("caption"),
            type=image.get("type") or "PICTURE",
            url=image.get("fullSizePictureUrl") or image.get("galleryPictureUrl") or "",
        )
        for image in images.get("images", [])
        if image.get("fullSizePictureUrl") or image.get("galleryPictureUrl")
    ]
    media += [
        MediaItem(caption=doc.get("title"), type="DOCUMENT", url=doc["url"])
        for doc in images.get("documents", [])
        if doc.get("url")
    ]
    cellar_flag = YES_NO.get(str(kv.get("obj_cellar")).lower())
    cellar_text = feature(flat_text, FEATURES["has_cellar"])

    rented = YES_NO.get(str(kv.get("obj_rented")).lower())
    if "vermietet" in fact:
        rented = (fact["vermietet"] or "").lower().startswith("ja")
    has_parking, parking_type = parking(kv, fact)
    kitchen = YES_NO.get(str(kv.get("obj_hasKitchen")).lower())
    guest_toilet = True if "guestToilet" in tags else feature(flat_text, GUEST_TOILET_RE)

    return Expose(
        scout_id=str(scout_id) if scout_id else None,
        object_id=object_id,
        title=clean_text(title_node.text()) if title_node else None,
        address=clean_text(address_node.text()) if address_node else None,
        property_type=fact.get("typ"),
        is_rented=rented,
        living_area=parse_number(fact.get("wohnflaeche-ca")),
        usable_area=parse_number(fact.get("nutzflaeche-ca") or fact.get("nutzflaeche")),
        available_from=fact.get("bezugsfrei-ab"),
        rooms=parse_number(fact.get("zimmer")),
        bedrooms=parse_number(fact.get("schlafzimmer")),
        bathrooms=parse_number(fact.get("badezimmer")),
        price=parse_number(fact.get("kaufpreis")),
        price_per_m2=parse_number(fact.get("preism²")),
        buyer_commission=fact.get("provision"),
        year_built=_year(fact.get("baujahr")) or _int(kv.get("obj_yearConstructed")),
        heating_type=fact.get("heizungsart"),
        energy_source=fact.get("wesentliche-energietraeger"),
        energy_certificate=fact.get("energieausweis"),
        energy_certificate_type=fact.get("energieausweistyp"),
        final_energy_kwh_m2a=parse_number(energy),
        final_energy_type=energy_type.capitalize() if energy_type else None,
        energy_efficiency_class=_efficiency_class(
            fact.get("energieeffizienzklasse") or kv.get("obj_energyEfficiencyClass")
        ),
        energy_certificate_year_built=_year(fact.get("baujahr-laut-energieausweis")),
        property_condition=fact.get("objektzustand"),
        interior_quality=fact.get("qualitaet-der-ausstattung"),
        has_balcony_terrace=YES_NO.get(str(kv.get("obj_balcony")).lower()),
        has_terrace=feature(flat_text, TERRACE_RE),
        has_cellar=True if True in (cellar_flag, cellar_text) else cellar_flag,
        has_parking=has_parking,
        parking_type=parking_type,
        parking_spaces=_int(kv.get("obj_noParkSpaces")),
        parking_price=parse_number(fact.get("garage-stellplatz-kaufpreis")),
        has_fitted_kitchen=True if "builtInKitchen" in tags else kitchen,
        has_guest_toilet=guest_toilet,
        internet_speed=clean_text(internet.group(1)) if internet else None,
        internet_mbit=_int(internet.group(1)) if internet else None,
        description=texts.get("Objektbeschreibung") or None,
        equipment_text=texts.get("Ausstattung") or None,
        other_text=texts.get("Sonstiges") or None,
        location_text=location_text,
        media=media,
        facts=fact,
        features=tags,
    )


def _media_url(item: MediaItem, size: str) -> str:
    if item.type == "DOCUMENT" or "/ORIG/" not in item.url:
        return item.url
    base = item.url.split("/ORIG/")[0]
    return base if size == "original" else base + LARGE_SUFFIX


def _filename(index: int, item: MediaItem, url: str) -> str:
    caption = re.sub(r"[^\w-]+", "_", item.caption or item.type.lower()).strip("_")[:40]
    if item.type == "DOCUMENT":
        suffix = Path(url.split("?")[0]).suffix or ".pdf"
    elif url.endswith(LARGE_SUFFIX):
        suffix = ".webp"
    else:
        suffix = Path(url.split("?")[0]).suffix or ".jpg"
    prefix = "grundriss" if item.type in ("FLOORPLAN", "DOCUMENT") else "foto"
    return f"{prefix}_{index:02d}_{caption}{suffix}"


def download_media(expose: Expose, settings: Settings, client: httpx.Client) -> Path | None:
    """Download photos, floor plans and documents; files that already exist are kept."""
    if settings.is24_photo_size == "none" or not expose.scout_id or not expose.media:
        return None
    folder = settings.media_dir / "immoscout24" / expose.scout_id
    folder.mkdir(parents=True, exist_ok=True)
    for index, item in enumerate(expose.media, start=1):
        url = _media_url(item, settings.is24_photo_size)
        target = folder / _filename(index, item, url)
        item.path = str(target)
        if target.exists():
            continue
        try:
            response = client.get(url)
            response.raise_for_status()
            target.write_bytes(response.content)
        except httpx.HTTPError as exc:
            log.warning("Could not download %s: %s", url, exc)
            item.path = None
    return folder
