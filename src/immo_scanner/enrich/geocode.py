"""Geocoding with OpenStreetMap Nominatim, cached in the database.

The app talks to the local Nominatim started by docker compose. The public instance
at nominatim.openstreetmap.org still allows at most one request per second and
requires an identifying User-Agent (set IMMO_CONTACT_EMAIL).
"""

import logging
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any
from urllib.parse import urlparse

import httpx
from geoalchemy2.shape import to_shape
from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from immo_scanner.config import Settings, get_settings
from immo_scanner.db.models import GeocodeCache, Listing
from immo_scanner.db.repository import point

log = logging.getLogger(__name__)

PUBLIC_NOMINATIM_HOST = "nominatim.openstreetmap.org"
PUBLIC_MIN_INTERVAL_S = 1.1
RETRY_WAITS_S = (30.0, 60.0, 120.0, 300.0)
BERLIN_BBOX = (52.33, 13.08, 52.68, 13.77)  # south, west, north, east
STREET_RE = re.compile(
    r"(stra(ß|ss)e|str\.|weg\b|allee|platz|damm|ufer|ring\b|chaussee|steig|gasse|promenade|"
    r"zeile|pfad|markt|graben|anger|stieg|hof\b|esplanade|wiese\b)",
    re.IGNORECASE,
)
HYPHEN_STREET_RE = re.compile(
    r"([A-Za-zÄÖÜäöüß]+(?:-[A-Za-zÄÖÜäöüß]+)+)-Stra(?:ß|ss)e",
    re.IGNORECASE,
)


@dataclass
class GeocodeResult:
    lat: float
    lon: float
    precision: str


def uses_public_nominatim(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() == PUBLIC_NOMINATIM_HOST


def min_request_interval(url: str) -> float:
    """Pause required between lookups. Only the public instance enforces one."""
    return PUBLIC_MIN_INTERVAL_S if uses_public_nominatim(url) else 0.0


def _transient(exc: Exception) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and (
        exc.response.status_code == 429 or exc.response.status_code >= 500
    )


def _with_retries(step, waits: tuple[float, ...] = RETRY_WAITS_S) -> None:
    """Run `step`, waiting and retrying when Nominatim drops a connection or is overloaded."""
    for wait in (*waits, None):
        try:
            step()
            return
        except httpx.HTTPError as exc:
            if wait is None or not _transient(exc):
                raise
            log.warning("Nominatim request failed (%s), retrying in %.0f s", exc, wait)
            time.sleep(wait)


def _in_berlin(lat: float, lon: float) -> bool:
    south, west, north, east = BERLIN_BBOX
    return south <= lat <= north and west <= lon <= east


def normalize_address(address: str) -> str:
    """Drop a trailing ' / Ortsteil (Bezirk)' as published by Neubaukompass."""
    return re.sub(r"\s*/\s*.*$", "", address).strip()


def _remember(queries: list[str], query: str) -> None:
    query = re.sub(r"\s+,", ",", query)
    query = re.sub(r"\s+", " ", query).strip(" ,")
    if query and query.casefold() not in {item.casefold() for item in queries}:
        queries.append(query)


def address_queries(address: str) -> list[str]:
    """Nominatim queries, most specific first.

    House-number ranges (30-34) and 'Straße Ecke Straße' are not in OSM as written,
    so the single streets are tried as well.
    """
    cleaned = normalize_address(address)
    if "berlin" not in cleaned.casefold():
        cleaned = f"{cleaned}, Berlin"
    queries: list[str] = []
    _remember(queries, cleaned)
    _remember(queries, re.sub(r"(\d+)\s*[-–]\s*\d+[A-Za-z]?", r"\1", cleaned, count=1))
    _remember(queries, re.sub(r"\s*\d+\s*[-–]\s*\d+[A-Za-z]?", "", cleaned, count=1))
    parts = re.split(r"\s+[Ee]cke\s+", cleaned, maxsplit=1)
    if len(parts) == 2:
        city = ", " + cleaned.split(",", 1)[1].strip() if "," in cleaned else ", Berlin"
        for part in parts:
            street = re.sub(r"\b\d+[A-Za-z]?\b", "", part.split(",")[0])
            street = re.sub(r"\s+", " ", street).strip(" ,")
            if street:
                _remember(queries, f"{street}{city}")
    return queries


def place_queries(address: str) -> list[str]:
    """'Paule-Panke-Straße' is a new street; the quartier 'Paule Panke' is already mapped."""
    cleaned = normalize_address(address)
    match = HYPHEN_STREET_RE.search(cleaned)
    if match is None:
        return []
    place = match.group(1).replace("-", " ")
    city = cleaned.split(",", 1)[1].strip() if "," in cleaned else "Berlin"
    return [f"{place}, {city}"]


def street_names(address: str) -> list[str]:
    """Street names used to reuse coordinates from a listing on the same street."""
    cleaned = normalize_address(address)
    names: list[str] = []
    for part in re.split(r"\s+[Ee]cke\s+", cleaned):
        head = re.sub(r"\b\d+\s*[-–]\s*\d+[A-Za-z]?\b", "", part.split(",")[0])
        head = re.sub(r"\b\d+[A-Za-z]?\b", "", head)
        head = re.sub(r"\s+", " ", head).strip(" ,")
        if head and STREET_RE.search(head) and head.casefold() not in {n.casefold() for n in names}:
            names.append(head)
    return names


def precision_of(result: dict[str, Any]) -> str:
    address = result.get("address") or {}
    if "house_number" in address:
        return "exact"
    if result.get("addresstype") == "road" or result.get("category") == "highway":
        return "street"
    if result.get("addresstype") == "postcode" or result.get("type") == "postcode":
        return "postcode"
    return "district"


def usable_result(result: dict[str, Any], forced_precision: str | None = None) -> bool:
    """Reject Nominatim hits that are only 'Berlin' as a city.

    An older postcode fallback cached those as successful `postcode:*` lookups, so
    hundreds of listings ended up at the city centre and got Bezirk Mitte.
    """
    addresstype = (result.get("addresstype") or "").lower()
    result_type = (result.get("type") or "").lower()
    if forced_precision == "postcode":
        return addresstype == "postcode" or result_type in {"postcode", "postal_code"}
    # A city centroid cannot place a flat in the right Bezirk.
    return addresstype != "city"


class Geocoder:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.client = httpx.Client(
            base_url=self.settings.nominatim_url,
            headers={"User-Agent": self.settings.http_contact_agent},
            timeout=30,
        )
        self._last_request = 0.0
        self._min_interval = min_request_interval(self.settings.nominatim_url)
        if self._min_interval and not self.settings.contact_email:
            log.warning("IMMO_CONTACT_EMAIL is not set; Nominatim may reject requests")

    def _request(self, params: dict[str, str]) -> dict[str, Any] | None:
        wait = self._last_request + self._min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()
        response = self.client.get(
            "/search",
            params={
                "format": "jsonv2",
                "limit": "1",
                "addressdetails": "1",
                "countrycodes": "de",
                **params,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data[0] if data else None

    def lookup(self, key: str, params: dict[str, str], forced_precision: str | None = None):
        cached = self.session.get(GeocodeCache, key)
        if cached is None:
            result = self._request(params)
            values: dict[str, Any] = {"query": key, "raw": result}
            if result is not None and usable_result(result, forced_precision):
                lat, lon = float(result["lat"]), float(result["lon"])
                if _in_berlin(lat, lon):
                    values.update(
                        lat=lat, lon=lon, precision=forced_precision or precision_of(result)
                    )
            # Another geocoding run may have stored the same query meanwhile; keep its row.
            self.session.execute(
                pg_insert(GeocodeCache).values(**values).on_conflict_do_nothing(
                    index_elements=["query"]
                )
            )
            cached = self.session.get(GeocodeCache, key)
        if cached is None or cached.lat is None or cached.lon is None:
            return None
        return GeocodeResult(cached.lat, cached.lon, cached.precision or "district")

    def geocode_address(self, address: str) -> GeocodeResult | None:
        query = address if "berlin" in address.lower() else f"{address}, Berlin"
        return self.lookup(f"q:{query.lower()}", {"q": query})

    def geocode_listing(self, listing: Listing) -> tuple[GeocodeResult | None, str | None]:
        attempts: list[tuple[str, dict[str, str], str | None]] = []
        if listing.address:
            for query in address_queries(listing.address):
                if STREET_RE.search(query) or re.search(r"\d", query):
                    attempts.append((f"q:{query.lower()}", {"q": query}, None))
        for key, params, precision in attempts:
            result = self.lookup(key, params, precision)
            if result is not None:
                return result, key

        sibling = self._sibling_result(listing)
        if sibling is not None:
            return sibling

        if listing.address:
            for query in place_queries(listing.address):
                result = self.lookup(f"q:{query.lower()}", {"q": query})
                if result is not None:
                    return result, f"q:{query.lower()}"

        fallback: list[tuple[str, dict[str, str], str | None]] = []
        if listing.postcode:
            # Structured postalcode+city+country searches come back empty; free text works.
            query = f"{listing.postcode} Berlin"
            fallback.append((f"q:{query.lower()}", {"q": query}, "postcode"))
        if listing.district:
            query = f"{listing.district}, Berlin"
            fallback.append((f"district:{query.lower()}", {"q": query}, "district"))
        for key, params, precision in fallback:
            result = self.lookup(key, params, precision)
            if result is not None:
                return result, key
        last = attempts or fallback
        return None, last[-1][0] if last else None

    def _sibling_result(self, listing: Listing) -> tuple[GeocodeResult, str] | None:
        """New streets are missing from OSM; a neighbour with a portal pin is the same block."""
        if not listing.postcode or not listing.address:
            return None
        for street in street_names(listing.address):
            escaped = street.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
            pattern = f"%{escaped}%"
            geom = self.session.scalar(
                select(Listing.geom)
                .where(
                    Listing.id != listing.id,
                    Listing.geom.is_not(None),
                    Listing.postcode == listing.postcode,
                    Listing.address.ilike(pattern, escape="\\"),
                    Listing.geo_precision.in_(("source", "exact", "street")),
                )
                .order_by(
                    case(
                        (Listing.geo_precision == "source", 0),
                        (Listing.geo_precision == "exact", 1),
                        else_=2,
                    ),
                    Listing.id,
                )
                .limit(1)
            )
            if geom is None:
                continue
            found = to_shape(geom)
            log.info(
                "Listing %s has no geocoder hit; using another listing on %s", listing.id, street
            )
            return GeocodeResult(found.y, found.x, "street"), f"sibling:{street.lower()}"
        return None

    def close(self) -> None:
        self.client.close()


# Nominatim sometimes answered free-text "12557" with the city centroid of Berlin.
# Those cache keys and the listings that used them all share this point.
_FAKE_BERLIN_CENTRE = (52.5173885, 13.3951309)


def reset_bad_postcode_geocodes(session: Session) -> int:
    """Drop obsolete `postcode:*` cache hits and clear listings pinned to Berlin centre.

    Returns how many listings were reset so `geocode_listings` can try again.
    """
    fake_lat, fake_lon = _FAKE_BERLIN_CENTRE
    deleted = session.execute(
        GeocodeCache.__table__.delete().where(GeocodeCache.query.like("postcode:%"))
    ).rowcount
    listings = session.scalars(
        select(Listing).where(
            or_(
                Listing.geocoded_query.like("postcode:%"),
                and_(
                    Listing.geo_precision == "postcode",
                    Listing.geom.is_not(None),
                    func.abs(func.ST_Y(Listing.geom) - fake_lat) < 0.00001,
                    func.abs(func.ST_X(Listing.geom) - fake_lon) < 0.00001,
                ),
            )
        )
    ).all()
    for listing in listings:
        listing.geom = None
        listing.geo_precision = None
        listing.geocoded_query = None
    log.info(
        "Cleared %d obsolete postcode cache rows and reset %d listings for re-geocoding",
        deleted,
        len(listings),
    )
    return len(listings)


def expire_failed_geocodes(session: Session, days: int) -> int:
    """Forget lookups that found nothing more than `days` ago, so Nominatim is asked again.

    OpenStreetMap gains new streets; a miss cached forever would never pick them up.
    `days` of 0 or less keeps every miss.
    """
    if days <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=days)
    result = session.execute(
        delete(GeocodeCache).where(GeocodeCache.lat.is_(None), GeocodeCache.created_at < cutoff)
    )
    return result.rowcount or 0


def _geocode_one(geocoder: Geocoder, listing: Listing, session: Session, index: object) -> None:
    from immo_scanner.reference.milieuschutz import stamp_listing

    result, key = geocoder.geocode_listing(listing)
    listing.geocoded_query = key or "no-location"
    if result is not None:
        listing.geom = point(result.lon, result.lat)
        listing.geo_precision = result.precision
        stamp_listing(session, listing, index, result.lat, result.lon)


def geocode_listings(
    session: Session,
    limit: int | None = None,
    waits: tuple[float, ...] = RETRY_WAITS_S,
    geocoder: Geocoder | None = None,
) -> int:
    """Geocode active listings without coordinates; returns how many got a point.

    Listings never tried come first. Those the geocoder gave up on are tried again,
    which costs nothing while their misses are cached. A listing whose lookup fails
    keeps `geocoded_query` empty for the next run; when Nominatim stays unavailable
    the run stops and keeps what it has.
    """
    query = (
        select(Listing)
        .where(Listing.geom.is_(None), Listing.status == "active")
        .order_by(Listing.geocoded_query.is_not(None), Listing.first_seen.desc())
    )
    if limit:
        query = query.limit(limit)
    from immo_scanner.reference.milieuschutz import load_index

    listings = session.scalars(query).all()
    index = load_index(session)
    own_geocoder = geocoder is None
    geocoder = geocoder or Geocoder(session)
    done = 0
    try:
        for i, listing in enumerate(listings, start=1):
            try:
                _with_retries(partial(_geocode_one, geocoder, listing, session, index), waits)
            except httpx.HTTPError as exc:
                if _transient(exc):
                    log.warning(
                        "Nominatim unavailable (%s), stopping after %d of %d listings",
                        exc, i - 1, len(listings),
                    )
                    break
                log.warning("Geocoding listing %s failed: %s", listing.id, exc)
                continue
            done += listing.geom is not None
            if i % 25 == 0:
                session.commit()
                log.info("Geocoded %d/%d", i, len(listings))
    finally:
        if own_geocoder:
            geocoder.close()
    return done
