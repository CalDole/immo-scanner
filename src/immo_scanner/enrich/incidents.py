"""Place Berliner Register incidents on the map from the streets and places in their text.

Each incident gets at most one point: the first candidate that Nominatim finds inside a
Planungsraum of the reported Bezirk. Lookups share the listing geocode cache.
"""

import logging
import re
from dataclasses import dataclass
from functools import partial

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from immo_scanner.db.models import GeocodeCache, RegisterIncident
from immo_scanner.db.repository import point
from immo_scanner.enrich.geocode import Geocoder, _with_retries

log = logging.getLogger(__name__)

MAX_CANDIDATES = 4
CITYWIDE = {"berlinweit", "berlin", "überregional"}

_SUFFIXES = (
    r"straße|strasse|str\.|weg|allee|platz|damm|ufer|ring|chaussee|steig|gasse|promenade|"
    r"zeile|pfad|markt|graben|anger|stieg|park|brücke"
)
STREET_RE = re.compile(
    r"(?:\b(?!(?:Unter|Hinter|Über|Oder|Aber|Hier|Wieder|Immer|Später|Weiter|Jeder|Einer|"
    r"Dieser|Jener|Mehrere|Andere|Unsere|Ihrer)\b)[A-ZÄÖÜ]\w{2,}(?:er|sche[nm]?)\s+)?"
    rf"\b(?=[A-ZÄÖÜ])[\w\-]*?(?i:{_SUFFIXES})(?:(?<=\.)|\b)"
)
STATION_RE = re.compile(
    r"\b(?:[SU]-?\s?)?(?:Bahnhof|Bhf\.?)\s+([A-ZÄÖÜ][\w\-]*)(?:\s+([A-ZÄÖÜ][\w\-]*))?"
)
LOCALITY_PREFIX_RE = re.compile(r"\b(?:in|im|nach|aus)\s+$")
TITLE_PLACE_RE = re.compile(
    r"\b(?:in|im|am|bei|beim|an der|auf der|auf dem)\s+(?:der\s+|dem\s+|den\s+)?"
    r"([A-ZÄÖÜ][\w\-]*(?:\s+[A-ZÄÖÜ][\w\-]*){0,2})\s*$"
)
# Words that end like a street but name no particular place.
GENERIC_PLACES = {
    "weg", "platz", "park", "markt", "ring", "brücke", "straße", "allee", "ufer", "damm",
    "spielplatz", "sportplatz", "parkplatz", "marktplatz", "schulhofplatz", "supermarkt",
    "wochenmarkt", "flohmarkt", "bahnsteig", "bürgersteig", "gehweg", "fußweg", "radweg",
    "fahrradweg", "wanderweg", "heimweg", "nachhauseweg", "rückweg", "schulweg", "arbeitsweg",
    "uferweg", "hauptstraße", "nebenstraße", "seitenstraße", "landstraße", "autobahnbrücke",
    "fußgängerbrücke", "hinterhofplatz", "dorfplatz", "stadtpark", "wasserweg",
    "landschaftspark", "volkspark", "skatepark", "freizeitpark", "kinderspielplatz",
    "bolzplatz", "festplatz", "vorplatz", "bahnhofsvorplatz", "kreisverkehr",
}
# Places that are not more precise than the Bezirk itself.
NOT_A_PLACE = {"berlin", "bezirk", "deutschland"}
# Nominatim results that only name a whole Bezirk or larger.
COARSE_TYPES = {"city", "borough", "city_district", "state", "county", "municipality"}


@dataclass(frozen=True)
class Candidate:
    query: str
    precision: str  # street | station | place


def _street_query(match: str) -> str:
    """'Kiefholzstr.' -> 'Kiefholzstraße', 'Plesser Str.' -> 'Plesser Straße'."""
    return re.sub(r"(S|s)tr\.$", lambda m: f"{m.group(1)}traße", match.strip())


def location_candidates(title: str, description: str | None) -> list[Candidate]:
    """Streets, stations and the title's place, in the order they are mentioned."""
    body = f"{title}\n{description or ''}"
    found: list[tuple[int, Candidate]] = []
    for match in STREET_RE.finditer(body):
        street = _street_query(match.group(0))
        if street.lower() in GENERIC_PLACES:
            continue
        # "in Baumschulenweg" without an article names the Ortsteil, not the street.
        if match.start() < len(title) and LOCALITY_PREFIX_RE.search(body[: match.start()]):
            continue
        found.append((match.start(), Candidate(street, "street")))
    for match in STATION_RE.finditer(body):
        name = match.group(1)
        if match.group(2) and name.endswith("er"):
            name = f"{name} {match.group(2)}"
        found.append((match.start(), Candidate(f"Bahnhof {name}", "station")))
    found.sort(key=lambda item: item[0])
    candidates: list[Candidate] = []
    for _, candidate in found:
        if candidate.query.casefold() not in {c.query.casefold() for c in candidates}:
            candidates.append(candidate)
    place = TITLE_PLACE_RE.search(title.strip())
    if place:
        name = place.group(1)
        known = {c.query.casefold() for c in candidates}
        is_station = re.search(r"(?i)bahnhof|bhf\.?", name) is not None
        if not is_station and name.casefold() not in NOT_A_PLACE | known:
            candidates.append(Candidate(name, "place"))
    return candidates[:MAX_CANDIDATES]


def _normalize(name: str | None) -> str:
    return re.sub(r"\s+", "", (name or "").casefold())


def _plr_at(session: Session, lat: float, lon: float) -> tuple[str, str] | None:
    row = session.execute(
        text(
            "SELECT plr_id, bezirk FROM lor_planungsraeume "
            "WHERE ST_Contains(geom, ST_SetSRID(ST_Point(:lon, :lat), 4326)) LIMIT 1"
        ),
        {"lat": lat, "lon": lon},
    ).first()
    return (row.plr_id, row.bezirk) if row else None


def _too_coarse(session: Session, key: str) -> bool:
    cached = session.get(GeocodeCache, key)
    raw = cached.raw if cached is not None else None
    return bool(raw) and (raw.get("addresstype") or "").lower() in COARSE_TYPES


def _locate(
    geocoder: Geocoder, incident: RegisterIncident, candidate: Candidate
) -> tuple[float, float, str, str] | None:
    """(lat, lon, plr_id, cache key) when the candidate lies in the incident's Bezirk."""
    queries = [f"{candidate.query}, {incident.bezirk}, Berlin"]
    if candidate.precision != "place":
        queries.append(f"{candidate.query}, Berlin")
    for query in queries:
        key = f"q:{query.lower()}"
        result = geocoder.lookup(key, {"q": query})
        if result is None or _too_coarse(geocoder.session, key):
            continue
        plr = _plr_at(geocoder.session, result.lat, result.lon)
        if plr and _normalize(plr[1]) == _normalize(incident.bezirk):
            return result.lat, result.lon, plr[0], key
    return None


def geocode_incident(geocoder: Geocoder, incident: RegisterIncident) -> None:
    if not incident.bezirk or incident.bezirk.casefold() in CITYWIDE:
        incident.geo_precision = "berlin"
        incident.geocoded_query = "citywide"
        return
    for candidate in location_candidates(incident.title, incident.description):
        located = _locate(geocoder, incident, candidate)
        if located is None:
            continue
        lat, lon, plr_id, key = located
        incident.geom = point(lon, lat)
        incident.plr_id = plr_id
        incident.geo_precision = candidate.precision
        incident.geocoded_query = key
        return
    incident.geo_precision = "bezirk"
    incident.geocoded_query = "no-location"


def geocode_incidents(session: Session, limit: int | None = None, retry: bool = False) -> int:
    """Geocode incidents not tried yet, newest first; returns how many got a point.

    `retry` also tries incidents that found no point before (cached lookups are free).
    """
    query = select(RegisterIncident).order_by(RegisterIncident.incident_date.desc())
    if retry:
        query = query.where(RegisterIncident.geom.is_(None))
    else:
        query = query.where(RegisterIncident.geocoded_query.is_(None))
    if limit:
        query = query.limit(limit)
    incidents = session.scalars(query).all()
    geocoder = Geocoder(session)
    located = 0
    try:
        for i, incident in enumerate(incidents, start=1):
            _with_retries(partial(geocode_incident, geocoder, incident))
            located += incident.geom is not None
            if i % 25 == 0:
                session.commit()
                log.info("Geocoded %d/%d incidents (%d placed)", i, len(incidents), located)
    finally:
        geocoder.close()
    return located
