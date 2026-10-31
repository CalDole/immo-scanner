"""Immowelt exposé: the classified object embedded in the page, not the visible text.

Each exposé HTML page includes ``#__UFRN_LIFECYCLE_SERVERREQUEST__``, a JSON value with
typed fields (price, energy class, Hausgeld, feature icons). Display strings such as
"2. Geschoss/5 Geschossen" still need a small parse. Bedroom counts and a modernisation
year are read only from labeled keyword lines ("Anzahl der Schlafzimmer:", "modernisiert:"),
not from the marketing description.
"""

import html
import json
import re
from typing import Any

from pydantic import BaseModel, Field
from selectolax.parser import HTMLParser

from immo_scanner.crawlers.parsing import parse_number
from immo_scanner.details.model import DetailData

STORE_ID = "__UFRN_LIFECYCLE_SERVERREQUEST__"
CLASS_RE = re.compile(r"A\+|A|B|C|D|E|F|G|H")
CODE_RE = re.compile(r"[A-ZÄÖÜ]{2,8}")
NEGATION_RE = re.compile(r"^(?:kein(?:e|en)?|nein|ohne)\b", re.IGNORECASE)
YEAR_RE = re.compile(r"(?:18|19|20)\d{2}")
LABELED_NUMBER = {
    "bedrooms": re.compile(r"Anzahl(?:\s+der)?\s+Schlafzimmer\s*:\s*(\d+(?:[.,]\d+)?)", re.I),
    "bathrooms": re.compile(r"Anzahl(?:\s+der)?\s+Badezimmer\s*:\s*(\d+(?:[.,]\d+)?)", re.I),
    "balconies": re.compile(r"Anzahl(?:\s+der)?\s+Balkone\s*:\s*(\d+)", re.I),
}
STOREYS_RE = re.compile(r"(\d{1,2})\s+Etagen\b", re.I)
MODERNISED_RE = re.compile(r"modernisiert\s*:\s*((?:18|19|20)\d{2})", re.I)
BATHROOM_COUNT_RE = re.compile(r"(\d+)\s+Badezimmer\b", re.I)
FLOOR_NUMBER_RE = re.compile(r"(\d{1,2})\s*\.\s*geschoss", re.I)
FLOOR_TOTAL_RE = re.compile(r"/\s*(\d{1,2})\s*geschoss", re.I)
FLOOR_PAIR_RE = re.compile(r"geschoss\s*(\d{1,2})\s*/\s*(\d{1,2})", re.I)
GROUND_RE = re.compile(r"\b(?:erdgeschoss|eg|hochparterre)\b", re.I)
LOWER_RE = re.compile(r"\b(?:untergeschoss|souterrain)\b", re.I)
MONEY_RE = re.compile(r"(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)")


class Expose(BaseModel):
    parsed: bool = False
    online_id: str | None = None
    reference_number: str | None = None
    url: str = ""
    title: str | None = None
    postcode: str | None = None
    district: str | None = None
    address_published: bool | None = None
    price: float | None = None
    living_area: float | None = None
    rooms: float | None = None
    bedrooms: float | None = None
    bathrooms: float | None = None
    floor: int | None = None
    total_floors: int | None = None
    year_built: int | None = None
    last_renovation_year: int | None = None
    property_condition: str | None = None
    heating_type: str | None = None
    energy_source: str | None = None
    energy_certificate_type: str | None = None
    energy_efficiency_class: str | None = None
    final_energy_kwh_m2a: float | None = None
    is_rented: bool | None = None
    available_from: str | None = None
    hausgeld: float | None = None
    buyer_commission: str | None = None
    has_balcony: bool | None = None
    balcony_count: int | None = None
    has_terrace: bool | None = None
    has_cellar: bool | None = None
    has_elevator: bool | None = None
    has_parking: bool | None = None
    has_fitted_kitchen: bool | None = None
    has_garden: bool | None = None
    has_monument_protection: bool | None = None
    flooring: str | None = None
    bathroom_features: str | None = None
    description: str | None = None
    location_text: str | None = None
    further_text: str | None = None
    facts: dict[str, Any] = Field(default_factory=dict)

    def to_detail(self) -> DetailData:
        return DetailData(
            year_built=self.year_built,
            last_renovation_year=self.last_renovation_year,
            floor=self.floor,
            total_floors=self.total_floors,
            heating_type=self.heating_type,
            energy_source=self.energy_source,
            has_elevator=self.has_elevator,
            has_parking=self.has_parking,
            has_cellar=self.has_cellar,
            has_balcony=self.has_balcony,
            balcony_count=self.balcony_count,
            description=self.description,
        )


def classified_from_html(html_text: str) -> dict[str, Any] | None:
    """The classified object from the server-request script, or None when it is absent."""
    node = HTMLParser(html_text).css_first(f"#{STORE_ID}")
    if node is None:
        return None
    text = node.text() or ""
    marker = 'JSON.parse("'
    start = text.find(marker)
    end = text.rfind('");')
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads('"' + text[start + len(marker) : end] + '"')
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None
    classified = data.get("app_demand_referral_cldp", {}).get("data", {}).get("classified")
    return classified if isinstance(classified, dict) else None


def parse_expose(html_text: str, url: str = "") -> Expose:
    classified = classified_from_html(html_text)
    if not classified:
        return Expose(url=url)
    expose = _from_classified(classified)
    expose.url = url
    expose.parsed = True
    return expose


def _from_classified(classified: dict[str, Any]) -> Expose:
    sections = classified.get("sections") or {}
    hard = sections.get("hardFacts") or {}
    facts = {item.get("type"): item for item in hard.get("facts") or [] if item.get("type")}
    features = _feature_values(sections.get("features") or {})
    energy = sections.get("energy") or {}
    energy_features = energy.get("features") or []
    certificate = (energy.get("certificates") or [{}])[0]
    cert_features = certificate.get("features") or []
    scale = (certificate.get("scales") or [{}])[0]
    energy_value = (scale.get("values") or [{}])[0]
    further = _plain(sections.get("extendedInfoDescription", {}).get("description"))
    floor, total = _floor(features.get("floors", []), facts.get("numberOfFloors"))
    if total is None:
        total = _labeled_int(further, STOREYS_RE)
    bathrooms = _bathrooms(features.get("bathroom-amenities", []), further)
    has_balcony = _flag(features.get("balcony", []) + features.get("loggia", []))
    balcony_count = _labeled_int(further, LABELED_NUMBER["balconies"])
    if balcony_count is None and has_balcony is not None:
        balcony_count = 1 if has_balcony else 0
    location = sections.get("location") or {}
    address = location.get("address") or {}
    key_items = (sections.get("key") or {}).get("keys") or []
    keys = {item.get("label"): item.get("value") for item in key_items}
    comparison = (sections.get("priceComparison") or {}).get("data") or {}
    main = sections.get("mainDescription") or {}
    price_section = sections.get("price") or {}
    tracking_items = (classified.get("tracking") or {}).get("av_items") or [{}]
    year = _year(_typed(energy_features, "yearOfConstruction"))
    renovated = _year(_typed(energy_features, "lastModernisation")) or _labeled_int(
        further, MODERNISED_RE
    )
    if renovated is not None and year is not None and renovated < year:
        renovated = None
    rating = (scale.get("efficiencyClass") or {}).get("rating")
    tracked_price = tracking_items[0].get("price") if tracking_items else None
    price = _decimal(str(tracked_price)) if tracked_price is not None else None
    if price is None:
        price = _money((hard.get("price") or {}).get("ariaLabel"))
    carrier = _typed(energy_features, "energySource") or _readable(
        _typed(cert_features, "primaryEnergySource")
    )
    efficiency = rating if isinstance(rating, str) and CLASS_RE.fullmatch(rating) else None
    commission = (price_section.get("base") or {}).get("commissionFee", {}).get("value")
    estimated = (price_section.get("breakdown") or {}).get("total", {}).get("ariaLabel")
    return Expose(
        online_id=classified.get("id") or keys.get("Online-ID"),
        reference_number=keys.get("Referenznummer"),
        title=_plain(main.get("headline")),
        postcode=address.get("zipCode"),
        district=address.get("district"),
        address_published=location.get("isAddressPublished"),
        price=price,
        living_area=_decimal((facts.get("livingSpace") or {}).get("splitValue")),
        rooms=_decimal((facts.get("numberOfRooms") or {}).get("splitValue")),
        bedrooms=_labeled_number(further, LABELED_NUMBER["bedrooms"]),
        bathrooms=bathrooms,
        floor=floor,
        total_floors=total,
        year_built=year,
        last_renovation_year=renovated,
        property_condition=_typed(energy_features, "state"),
        heating_type=_typed(energy_features, "heatingSystem"),
        energy_source=carrier,
        energy_certificate_type=_typed(cert_features, "basedOn"),
        energy_efficiency_class=efficiency,
        final_energy_kwh_m2a=parse_number(energy_value.get("value")),
        is_rented=_rented(features.get("rented", [])),
        available_from=_available(features.get("availability", [])),
        hausgeld=_first_money(_labeled_amounts(price_section, "Hausgeld")),
        buyer_commission=commission,
        has_balcony=has_balcony,
        balcony_count=balcony_count,
        has_terrace=_flag(features.get("terrace", [])),
        has_cellar=_flag(features.get("cellar", [])),
        has_elevator=_flag(features.get("elevator", [])),
        has_parking=_flag(features.get("parking-lots", [])),
        has_fitted_kitchen=_flag(features.get("kitchen", [])),
        has_garden=_flag(features.get("garden", [])),
        has_monument_protection=_flag(features.get("heritage-protected", [])),
        flooring=_after_colon(features.get("floor-covering", [])),
        bathroom_features=_after_colon(features.get("bathroom-amenities", [])),
        description=_plain(main.get("description")),
        location_text=_plain((sections.get("areaDescription") or {}).get("description")),
        further_text=further,
        facts={
            "features": features,
            "energy_value_label": energy_value.get("label"),
            "price_comparison": {
                "low": comparison.get("low"),
                "high": comparison.get("high"),
                "value": comparison.get("value"),
            }
            if comparison
            else None,
            "estimated_total": estimated,
        },
    )


def _feature_values(section: dict[str, Any]) -> dict[str, list[str]]:
    """Full Merkmale list when the page has one; otherwise the preview chips."""
    categories = (section.get("details") or {}).get("categories") or []
    elements = [item for category in categories for item in category.get("elements") or []]
    if not elements:
        elements = section.get("preview") or []
    grouped: dict[str, list[str]] = {}
    for item in elements:
        icon, value = item.get("icon"), item.get("value")
        if icon and value:
            grouped.setdefault(icon, []).append(str(value))
    return grouped


def _typed(items: list[dict[str, Any]], type_name: str) -> str | None:
    for item in items:
        if item.get("type") == type_name and item.get("value"):
            return str(item["value"]).strip()
    return None


def _readable(value: str | None) -> str | None:
    """Skip carrier codes such as 'FERN'; keep 'Gas' and 'Nah-/Fernwärme'."""
    if not value or CODE_RE.fullmatch(value):
        return None
    return value


def _flag(values: list[str]) -> bool | None:
    if not values:
        return None
    if any(not NEGATION_RE.match(value) for value in values):
        return True
    return False


def _rented(values: list[str]) -> bool | None:
    if not values:
        return None
    return not any(value.lower().startswith("unvermietet") for value in values)


def _available(values: list[str]) -> str | None:
    if not values:
        return None
    text = re.sub(r"^Bezug\s*:\s*", "", values[0], flags=re.I).strip()
    return text or values[0]


def _floor(phrases: list[str], fact: dict[str, Any] | None) -> tuple[int | None, int | None]:
    for phrase in phrases:
        floor, total = _floor_phrase(phrase)
        if floor is not None or total is not None:
            return floor, total
    if fact:
        return _floor_phrase(str(fact.get("splitValue") or fact.get("value") or ""))
    return None, None


def _floor_phrase(value: str) -> tuple[int | None, int | None]:
    total = int(match.group(1)) if (match := FLOOR_TOTAL_RE.search(value)) else None
    if match := FLOOR_PAIR_RE.search(value):
        return int(match.group(1)), int(match.group(2))
    if match := FLOOR_NUMBER_RE.search(value):
        return int(match.group(1)), total
    if GROUND_RE.search(value):
        return 0, total
    if LOWER_RE.search(value):
        return -1, total
    if re.fullmatch(r"(\d{1,2})\.?", value.strip()):
        return int(value.strip().rstrip(".")), total
    return None, total


def _bathrooms(phrases: list[str], further: str | None) -> float | None:
    for phrase in phrases:
        if match := BATHROOM_COUNT_RE.search(phrase):
            return float(match.group(1))
    return _labeled_number(further, LABELED_NUMBER["bathrooms"])


def _after_colon(phrases: list[str]) -> str | None:
    cleaned = []
    for phrase in phrases:
        text = phrase.split(":", 1)[1].strip() if ":" in phrase else phrase.strip()
        if text:
            cleaned.append(text)
    return ", ".join(cleaned) or None


def _labeled_number(text: str | None, pattern: re.Pattern[str]) -> float | None:
    if not text or not (match := pattern.search(text)):
        return None
    return _decimal(match.group(1))


def _labeled_int(text: str | None, pattern: re.Pattern[str]) -> int | None:
    if not text or not (match := pattern.search(text)):
        return None
    return int(match.group(1))


def _year(value: str | None) -> int | None:
    if value and (match := YEAR_RE.search(value)):
        return int(match.group(0))
    return None


def _decimal(value: str | None) -> float | None:
    """'4,5', '78,4' and '365.94' — a dot is a decimal point unless it groups thousands."""
    if not value:
        return None
    match = MONEY_RE.search(value)
    if not match:
        return None
    raw = match.group(1)
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):
        raw = raw.replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return None


def _money(value: str | None) -> float | None:
    return _decimal(value)


def _first_money(values: list[str]) -> float | None:
    for value in values:
        if (amount := _money(value)) is not None:
            return amount
    return None


def _labeled_amounts(node: Any, label: str) -> list[str]:
    found: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            lab = item.get("label")
            if isinstance(lab, dict) and lab.get("main") == label:
                aria = (item.get("value") or {}).get("main", {}).get("ariaLabel")
                if aria:
                    found.append(str(aria))
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(node)
    return found


def _plain(value: str | None) -> str | None:
    if not value:
        return None
    text = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None
