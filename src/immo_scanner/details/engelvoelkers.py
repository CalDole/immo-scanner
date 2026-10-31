"""Engel & Völkers exposé: the listing record embedded in the page's `__NEXT_DATA__`.

Codes like `heatingMaterial.longDistance` are mapped to the German labels the other
portals use; unknown codes keep their suffix. Amenities come from the `features` flags
first and from the description otherwise. Hausgeld and the rented status are not in the
record, so they come from the text.
"""

import logging
import re
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from immo_scanner.config import Settings
from immo_scanner.crawlers.criteria import rented_from_title
from immo_scanner.crawlers.engelvoelkers import (
    address_parts,
    expose_url,
    next_data,
    query_data,
)
from immo_scanner.crawlers.parsing import parse_hausgeld
from immo_scanner.details.model import DetailData
from immo_scanner.details.text import TEXT_FEATURES, TYPOGRAPHY, extract_from_text, feature

log = logging.getLogger(__name__)

IMAGE_URL = "https://uploadcare.engelvoelkers.com/{id}/-/resize/1600x/"
# heatingMaterial mixes the heating system and the fuel.
HEATING_TYPES = {
    "central": "Zentralheizung",
    "deCentral": "Etagenheizung",
    "floor": "Fußbodenheizung",
    "underfloor": "Fußbodenheizung",
    "stove": "Ofenheizung",
    "heatPump": "Wärmepumpe",
    "blockHeatingPowerPlant": "Blockheizkraftwerk",
}
ENERGY_SOURCES = {
    "longDistance": "Fernwärme",
    "gas": "Gas",
    "oil": "Öl",
    "electric": "Strom",
    "electricity": "Strom",
    "pellet": "Pellets",
    "wood": "Holz",
    "geothermal": "Erdwärme",
    "solar": "Solar",
}
HEATING_SYSTEMS = {"radiator": "Heizkörper", "floor": "Fußbodenheizung"}
CONDITIONS = {
    "top": "Neuwertig",
    "new": "Erstbezug",
    "good": "Gepflegt",
    "modernized": "Modernisiert",
    "renovated": "Renoviert",
    "needsRehabilitation": "Renovierungsbedürftig",
    "needsRenovation": "Renovierungsbedürftig",
}
# energyCertType -> (certificate, kind of the final energy value)
CERTIFICATE_TYPES = {
    "USINGEIGENVALUE": ("Verbrauchsausweis", "Endenergieverbrauch"),
    "consumption": ("Verbrauchsausweis", "Endenergieverbrauch"),
    "ENDDEMAND": ("Bedarfsausweis", "Endenergiebedarf"),
    "demand": ("Bedarfsausweis", "Endenergiebedarf"),
}
JSON_FLAGS = {
    "hasBalcony": "has_balcony",
    "hasBasement": "has_cellar",
    "hasBuiltInKitchen": "has_fitted_kitchen",
    "hasTerrace": "has_terrace",
    "hasPatio": "has_terrace",
    "hasElevator": "has_elevator",
    "hasCoveredParking": "has_parking",
    "hasGarden": "has_garden",
    "hasFirePlace": "has_fireplace",
}
VACANT_RE = re.compile(r"\bbezugsfrei|\bleerstehend|\bunvermietet|nicht\s+vermietet", re.I)
COMMISSION_FREE_RE = re.compile(r"provisionsfrei|courtagefrei|keine\s+(?:Käufer)?provision", re.I)
# Dropped from `raw`: images are in `media`; the rest is shop, consent and tracking data.
RAW_SKIP = {
    "propertyImages",
    "floorPlanImages",
    "uploadCareImages",
    "uploadCareImageIds",
    "trackingData",
    "agent",
    "countryConsent",
    "shopConsent",
    "financeConsent",
    "shopProfilePicture",
    "shopProfilePictureUploadcareId",
    "shopProfilePictureAiGenerated",
    "autogenPageText",
    "autogenPageUrl",
    "languages",
    "imprintUrl",
    "privacyPolicyUrl",
}


class MediaItem(BaseModel):
    caption: str | None = None
    type: str = "PICTURE"
    url: str
    path: str | None = None


class Expose(BaseModel):
    listing_uuid: str | None = None
    display_id: str | None = None
    external_id: str | None = None
    parent_id: str | None = None
    url: str = ""
    status: str | None = None
    title: str | None = None
    property_type: str | None = None
    district: str | None = None
    ortsteil: str | None = None
    lat: float | None = None
    lon: float | None = None
    price: float | None = None
    living_area: float | None = None
    rooms: float | None = None
    bedrooms: float | None = None
    bathrooms: float | None = None
    floor: int | None = None
    total_floors: int | None = None
    year_built: int | None = None
    property_condition: str | None = None
    heating_type: str | None = None
    energy_source: str | None = None
    energy_certificate_type: str | None = None
    final_energy_type: str | None = None
    final_energy_kwh_m2a: float | None = None
    energy_efficiency_class: str | None = None
    is_rented: bool | None = None
    available_from: str | None = None
    hausgeld: float | None = None
    commission_percent: float | None = None
    buyer_commission: str | None = None
    flooring: str | None = None
    balcony_count: int | None = None
    has_balcony: bool | None = None
    has_terrace: bool | None = None
    has_cellar: bool | None = None
    has_elevator: bool | None = None
    has_parking: bool | None = None
    has_fitted_kitchen: bool | None = None
    has_garden: bool | None = None
    has_guest_toilet: bool | None = None
    has_monument_protection: bool | None = None
    has_fireplace: bool | None = None
    description: str | None = None
    location_text: str | None = None
    commission_text: str | None = None
    published_at: str | None = None
    updated_at: str | None = None
    media: list[MediaItem] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def photo_count(self) -> int:
        return sum(item.type == "PICTURE" for item in self.media)

    @property
    def floor_plan_count(self) -> int:
        return sum(item.type == "FLOORPLAN" for item in self.media)

    def to_detail(self) -> DetailData:
        text = "\n\n".join(text for text in (self.description, self.location_text) if text)
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
            raw={"display_id": self.display_id, "features": self.raw.get("features")},
        )


def listing_record(html: str) -> dict[str, Any]:
    return query_data(next_data(html)).get("listing") or {}


def _min(value: Any) -> Any:
    return value.get("min") if isinstance(value, dict) else value


def _int(value: Any) -> int | None:
    value = _min(value)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _code(value: str | None) -> str | None:
    """'heatingMaterial.longDistance' -> 'longDistance'."""
    return value.rpartition(".")[2] if value else None


def _codes(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value] if value else []
    return [code for item in values if (code := _code(item))]


def _labels(codes: list[str], table: dict[str, str]) -> str | None:
    return ", ".join(dict.fromkeys(table[code] for code in codes if code in table)) or None


def _heating(record: dict[str, Any]) -> tuple[str | None, str | None]:
    materials = _codes(record.get("heatingMaterial"))
    systems = _codes(record.get("heatingType"))
    known = HEATING_TYPES.keys() | ENERGY_SOURCES.keys()
    heating = _labels(materials, HEATING_TYPES) or _labels(systems, HEATING_SYSTEMS)
    source = ", ".join(
        dict.fromkeys(
            [ENERGY_SOURCES[code] for code in materials if code in ENERGY_SOURCES]
            + [code for code in materials if code not in known]
        )
    )
    return heating, source or None


def _text(value: str | None) -> str | None:
    return value.translate(TYPOGRAPHY).strip() or None if value else None


def _date(value: str | None) -> str | None:
    """ISO timestamps -> 'YYYY-MM-DD'."""
    return value[:10] if value else None


def _commission(percent: float | None, passus: str | None) -> str | None:
    if percent is not None:
        return f"{percent:.2f}".rstrip("0").rstrip(".").replace(".", ",") + " %"
    if passus and COMMISSION_FREE_RE.search(passus):
        return "provisionsfrei"
    return None


def _media(record: dict[str, Any]) -> list[MediaItem]:
    media = []
    for key, kind in (("propertyImages", "PICTURE"), ("floorPlanImages", "FLOORPLAN")):
        images = sorted(record.get(key) or [], key=lambda image: image.get("position") or 0)
        media.extend(
            MediaItem(
                caption=image.get("title"),
                type="FLOORPLAN" if image.get("type") == "floor_plan" else kind,
                url=IMAGE_URL.format(id=image["id"]),
            )
            for image in images
            if image.get("id")
        )
    return media


def _rented(title: str | None, prose: str) -> bool | None:
    if rented_from_title(title) or rented_from_title(prose):
        return True
    return False if VACANT_RE.search(prose) else None


def parse_expose(html: str, url: str | None = None) -> Expose:
    record = listing_record(html)
    profile = record.get("profile") or {}
    address = address_parts(record)
    heating, source = _heating(record)
    energy = _min(record.get("finalEnergyConsumption")) or _min(record.get("energyFinalDemand"))
    energy_class = _min(record.get("energyClassNormalized"))
    commission = _min(record.get("commissionPercent"))
    flooring = _codes(record.get("flooring"))
    condition = _code(record.get("condition"))
    certificate = record.get("energyCertType")
    certificate_type, energy_type = CERTIFICATE_TYPES.get(certificate, (certificate, None))
    listing_id = record.get("id")

    expose = Expose(
        listing_uuid=listing_id,
        display_id=record.get("displayId"),
        external_id=record.get("externalId"),
        parent_id=record.get("parentId"),
        url=url or (expose_url(listing_id) if listing_id else ""),
        status=record.get("statusNormalized"),
        title=_text(profile.get("title")),
        property_type=record.get("propertySubType") or record.get("propertyType"),
        district=address.get("sublocality_level_1"),
        ortsteil=address.get("sublocality_level_2"),
        lat=record.get("displayLat"),
        lon=record.get("displayLng"),
        price=_min((record.get("price") or {}).get("salesPrice")),
        living_area=_min((record.get("area") or {}).get("livingSurface")),
        rooms=_min(record.get("rooms")),
        bedrooms=_min(record.get("bedrooms")),
        bathrooms=_min(record.get("bathrooms")),
        floor=_int(record.get("floor")),
        total_floors=_int(record.get("floors")),
        year_built=_int(record.get("constructionYear")),
        property_condition=CONDITIONS.get(condition, condition) if condition else None,
        heating_type=heating,
        energy_source=source,
        energy_certificate_type=certificate_type,
        final_energy_type=energy_type if energy is not None else None,
        final_energy_kwh_m2a=float(energy) if energy is not None else None,
        energy_efficiency_class=energy_class,
        available_from=_date(record.get("availableFrom")),
        commission_percent=commission,
        buyer_commission=_commission(commission, profile.get("courtagePassus")),
        flooring=", ".join(flooring) or None,
        balcony_count=_int(record.get("balconyAmount")),
        description=_text(profile.get("description")),
        location_text=_text(profile.get("locationDescription")),
        commission_text=_text(profile.get("courtagePassus")),
        published_at=record.get("publishedAt"),
        updated_at=record.get("updatedAt"),
        media=_media(record),
        raw={key: value for key, value in record.items() if key not in RAW_SKIP},
    )

    flags = {**(record.get("features") or {})}
    for key in JSON_FLAGS:
        if key not in flags and isinstance(record.get(key), bool):
            flags[key] = record[key]
    for key, field in JSON_FLAGS.items():
        if isinstance(flags.get(key), bool):
            # Patio and terrace share a column; either one being True wins.
            setattr(expose, field, getattr(expose, field) or flags[key])

    prose = expose.description or ""
    for field, pattern in TEXT_FEATURES.items():
        if getattr(expose, field) is None and prose:
            setattr(expose, field, feature(prose, pattern))
    from_text = extract_from_text(prose)
    expose.floor = expose.floor if expose.floor is not None else from_text.floor
    expose.total_floors = expose.total_floors or from_text.total_floors
    expose.year_built = expose.year_built or from_text.year_built
    if expose.balcony_count is None and expose.has_balcony is not None:
        expose.balcony_count = from_text.balcony_count if expose.has_balcony else 0
    expose.hausgeld = parse_hausgeld(prose)
    expose.is_rented = _rented(expose.title, prose)
    return expose


def _filename(index: int, item: MediaItem) -> str:
    prefix = "grundriss" if item.type == "FLOORPLAN" else "foto"
    return f"{prefix}_{index:02d}.jpg"


def download_media(expose: Expose, settings: Settings, client: httpx.Client) -> Path | None:
    """Save each photo and floor plan; files that already exist are kept."""
    if settings.engelvoelkers_photo_size == "none" or not expose.display_id or not expose.media:
        return None
    # Development units have IDs like 'OI-BER-343/G12/E2/Eh1'.
    folder = settings.media_dir / "engelvoelkers" / re.sub(r"[^\w-]+", "_", expose.display_id)
    folder.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": settings.user_agent, "Referer": "https://www.engelvoelkers.com/"}
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
