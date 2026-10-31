"""Per-portal parsers for listing detail pages."""

import re
from collections.abc import Callable

from selectolax.parser import HTMLParser

from immo_scanner.crawlers.parsing import clean_text
from immo_scanner.details import bsk
from immo_scanner.details import engelvoelkers as ev
from immo_scanner.details import immoscout24 as is24
from immo_scanner.details import immowelt as iw
from immo_scanner.details import kleinanzeigen as ka
from immo_scanner.details import neubaukompass as nbk
from immo_scanner.details.model import DetailData
from immo_scanner.details.text import extract_from_text

IS24_HEATING = {
    "central_heating": "Zentralheizung",
    "self_contained_central_heating": "Etagenheizung",
    "district_heating": "Fernwärme",
    "floor_heating": "Fußbodenheizung",
    "gas_heating": "Gasheizung",
    "oil_heating": "Ölheizung",
    "heat_pump": "Wärmepumpe",
    "stove_heating": "Ofenheizung",
    "night_storage_heater": "Nachtspeicherofen",
    "combined_heat_and_power_plant": "Blockheizkraftwerk",
    "electric_heating": "Elektroheizung",
    "solar_heating": "Solarheizung",
    "wood_pellet_heating": "Pelletheizung",
}
NEUBAUKOMPASS_SECTIONS = ("Projektdetails", "Merkmale", "Projektbeschreibung", "Ausstattung")
YES_NO = {"y": True, "n": False, "true": True, "false": False}
YEAR_RE = re.compile(r"(?:18|19|20)\d{2}")


def _int(value: object) -> int | None:
    try:
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return None


def _page_text(tree: HTMLParser) -> str:
    for node in tree.css("script, style, noscript, svg"):
        node.decompose()
    body = tree.body
    return body.text(separator="\n", strip=True) if body else ""


def _sections(tree: HTMLParser, heading_selector: str, wanted: tuple[str, ...]) -> str:
    """Text of the elements containing headings that start with one of `wanted`."""
    texts = []
    for heading in tree.css(heading_selector):
        title = clean_text(heading.text()) or ""
        if not title.startswith(wanted):
            continue
        node = heading.parent
        for _ in range(3):
            if node is None or len(node.text(strip=True)) > len(title) + 50:
                break
            node = node.parent
        if node is not None:
            texts.append(node.text(separator="\n", strip=True))
    return "\n\n".join(texts)


def parse_immoscout24(html: str) -> DetailData:
    tree = HTMLParser(html)
    kv = is24.keyvalues(html)
    facts = is24.facts(tree)
    description = "\n\n".join(
        f"{title}\n{text}" for title, text in is24.sections(tree).items()
    )

    floor, total = None, None
    if etage := facts.get("etage"):
        numbers = re.findall(r"-?\d+", etage)
        floor = _int(numbers[0]) if numbers else None
        total = _int(numbers[1]) if len(numbers) > 1 else None
    renovation = YEAR_RE.search(facts.get("modernisierung-sanierung") or "")

    data = DetailData(
        year_built=_int(kv.get("obj_yearConstructed")) or _int(facts.get("baujahr")),
        last_renovation_year=_int(kv.get("obj_lastRefurbish"))
        or (_int(renovation.group(0)) if renovation else None),
        floor=_int(kv.get("obj_floor")) if kv.get("obj_floor") is not None else floor,
        total_floors=_int(kv.get("obj_numberOfFloors")) or total,
        heating_type=facts.get("heizungsart") or IS24_HEATING.get(kv.get("obj_heatingType")),
        energy_source=facts.get("wesentliche-energietraeger"),
        has_elevator=YES_NO.get(str(kv.get("obj_lift")).lower()),
        has_cellar=YES_NO.get(str(kv.get("obj_cellar")).lower()),
        has_balcony=YES_NO.get(str(kv.get("obj_balcony")).lower()),
        has_parking=is24.parking(kv, facts)[0],
        description=description or None,
        raw={"keyValues": {k: v for k, v in kv.items() if k.startswith("obj_")}, "facts": facts},
    )
    return data.fill_from(extract_from_text(description))


KLEINANZEIGEN_TAGS = {
    "Keller": "has_cellar",
    "Garage/Stellplatz": "has_parking",
}


def parse_kleinanzeigen(html: str) -> DetailData:
    ad = ka.parse_ad(html)
    data = DetailData(
        year_built=ad.year_built,
        floor=ad.floor,
        has_elevator=ad.has_elevator,
        has_balcony=ad.has_balcony,
        description=ad.description,
        raw={"facts": ad.facts, "tags": ad.features},
    )
    for tag in ad.features:
        if field := KLEINANZEIGEN_TAGS.get(tag):
            setattr(data, field, True)
    return data.fill_from(extract_from_text(ad.description))


def parse_immowelt(html: str) -> DetailData:
    """Structured classified object; page text only when that object is missing."""
    expose = iw.parse_expose(html)
    if expose.parsed:
        return expose.to_detail()
    return parse_generic(html)


def parse_bsk(html: str) -> DetailData:
    return bsk.parse_expose(html).to_detail()


def parse_engelvoelkers(html: str) -> DetailData:
    return ev.parse_expose(html).to_detail()


def parse_generic(html: str) -> DetailData:
    """Pages without known structure: label/value pairs and heuristics on the page text."""
    text = _page_text(HTMLParser(html))
    data = extract_from_text(text)
    data.description = text[:20_000] or None
    return data


_NO_PARKING_RE = re.compile(r"^(?:kein(?:e|en)?|nein|ohne)\b", re.IGNORECASE)


def _nbk_parking(facts: dict[str, str]) -> bool | None:
    """A garage line or a parking-space price means the flat has a Stellplatz."""
    value = facts.get("Garage/ Stellplatz") or facts.get("Garage/Stellplatz")
    if value:
        return False if _NO_PARKING_RE.match(value) else True
    if facts.get("Stellplatzpreis"):
        return True
    return None


def parse_neubaukompass(html: str) -> DetailData:
    """A single flat ("Wohnbeispiel"); falls back to the project page if that is what we got."""
    tree = HTMLParser(html)
    if not tree.css_first("#details"):
        return _parse_neubaukompass_project(tree)

    unit = nbk.parse_unit(html)
    prose = "\n\n".join(
        part for part in (unit.description, unit.equipment_text, unit.location_text) if part
    )
    # Merkmale name Aufzug and Keller; the garage is a fact line the prose often omits.
    # The energy carrier lives in the certificate block, which that prose does not include.
    data = DetailData(
        year_built=unit.year_built,
        last_renovation_year=unit.last_renovation_year,
        floor=unit.floor,
        total_floors=unit.total_floors,
        energy_source=unit.energy_source,
        has_parking=_nbk_parking(unit.facts),
        has_balcony=unit.has_balcony_terrace,
        description=prose or None,
        raw={"facts": unit.facts, "features": unit.features},
    ).fill_from(extract_from_text(f"{prose}\n\n{', '.join(unit.features)}"))
    # Where no Baujahr is given, a new build is "built" when it is handed over; a
    # refurbished Altbau is not, so its handover year says nothing about its age.
    if data.year_built is None and unit.last_renovation_year is None:
        match = YEAR_RE.search(unit.available_from or "")
        data.year_built = int(match.group(0)) if match else None
    return data


def _parse_neubaukompass_project(tree: HTMLParser) -> DetailData:
    """Project pages: attributes apply to the whole project, not a single flat."""
    text = _sections(tree, "h2", NEUBAUKOMPASS_SECTIONS)
    data = extract_from_text(text)
    data.description = text or None
    if match := re.search(r"(?:Bezugsfertig|Fertigstellung)\s*[^\n]{0,40}?(20\d{2})", text):
        data.year_built = int(match.group(1))
    data.last_renovation_year = None
    data.floor = None
    data.total_floors = None
    return data


PARSERS: dict[str, Callable[[str], DetailData]] = {
    "immoscout24": parse_immoscout24,
    "kleinanzeigen": parse_kleinanzeigen,
    "immowelt": parse_immowelt,
    "neubaukompass": parse_neubaukompass,
    "bsk": parse_bsk,
    "engelvoelkers": parse_engelvoelkers,
}
