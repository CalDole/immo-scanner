import logging

from geoalchemy2.shape import to_shape
from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from immo_scanner.config import Settings, get_settings
from immo_scanner.db.models import CommuteTime, Place
from immo_scanner.db.repository import point
from immo_scanner.enrich.geocode import Geocoder

log = logging.getLogger(__name__)

# (place name, settings attribute, whether lat/lon overrides the address)
DESTINATIONS: tuple[tuple[str, str, bool], ...] = (
    ("workplace", "workplace_address", True),
    ("workplace2", "workplace2_address", False),
    ("citycenter", "citycenter_address", False),
)


def ensure_places(session: Session) -> list[Place]:
    """Store configured destinations; clear a place's commute times when it moves."""
    settings = get_settings()
    geocoder: Geocoder | None = None
    places: list[Place] = []
    try:
        for name, address_attr, allow_coords in DESTINATIONS:
            place = _ensure_place(session, settings, name, address_attr, allow_coords, geocoder)
            geocoder = place[1]
            if place[0] is not None:
                places.append(place[0])
    finally:
        if geocoder is not None:
            geocoder.close()
    return places


def _ensure_place(
    session: Session,
    settings: Settings,
    name: str,
    address_attr: str,
    allow_coords: bool,
    geocoder: Geocoder | None,
) -> tuple[Place | None, Geocoder | None]:
    existing = session.get(Place, name)
    address = getattr(settings, address_attr)
    lat: float | None = None
    lon: float | None = None

    if allow_coords and settings.workplace_lat is not None and settings.workplace_lon is not None:
        lat, lon = settings.workplace_lat, settings.workplace_lon
    elif address:
        if existing is not None and existing.address == address:
            return existing, geocoder
        if geocoder is None:
            geocoder = Geocoder(session, settings)
        result = geocoder.geocode_address(address)
        if result is None:
            log.warning("Could not geocode %s %r", name, address)
            return existing, geocoder
        lat, lon = result.lat, result.lon
    else:
        if existing is None:
            if name == "workplace":
                log.warning("No workplace configured (IMMO_WORKPLACE_ADDRESS or _LAT/_LON)")
            else:
                log.warning("No address configured for %s", name)
        return existing, geocoder

    assert lat is not None and lon is not None
    return _store_place(session, existing, name, address, lon, lat), geocoder


def _store_place(
    session: Session,
    existing: Place | None,
    name: str,
    address: str | None,
    lon: float,
    lat: float,
) -> Place:
    if existing is not None:
        old = to_shape(existing.geom)
        if abs(old.x - lon) < 1e-6 and abs(old.y - lat) < 1e-6:
            return existing
        session.execute(delete(CommuteTime).where(CommuteTime.place_name == name))
        existing.geom = point(lon, lat)
        existing.address = address
        log.info("%s moved, its commute times will be recomputed", name)
        return existing
    place = Place(name=name, address=address, geom=point(lon, lat))
    session.add(place)
    return place


def refresh_features(session: Session) -> None:
    session.execute(text("REFRESH MATERIALIZED VIEW listing_features"))
