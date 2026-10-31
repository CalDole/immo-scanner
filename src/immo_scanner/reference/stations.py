"""S-Bahn and U-Bahn stations from OpenStreetMap (Overpass API)."""

import logging
import time

import httpx
from sqlalchemy import delete

from immo_scanner.config import get_settings
from immo_scanner.db.models import Station
from immo_scanner.db.repository import point
from immo_scanner.db.session import session_scope

log = logging.getLogger(__name__)

# Bounding box around Berlin with a margin, so flats near the city limits still find
# their closest station (e.g. Griebnitzsee).
BBOX = "52.30,12.95,52.72,13.85"

QUERY = f"""
[out:json][timeout:180];
(
  node["railway"~"^(station|halt)$"]({BBOX});
  node["public_transport"="station"]["subway"="yes"]({BBOX});
  node["public_transport"="station"]["light_rail"="yes"]({BBOX});
);
out body;
"""


def classify(tags: dict[str, str]) -> tuple[bool, bool]:
    """Return (is_sbahn, is_ubahn) for an OSM station node."""
    station = tags.get("station", "")
    network = tags.get("network", "")
    is_ubahn = station == "subway" or tags.get("subway") == "yes"
    is_sbahn = (
        station == "light_rail" or tags.get("light_rail") == "yes" or "S-Bahn" in network
    )
    return is_sbahn, is_ubahn


FALLBACK_OVERPASS_URLS = (
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)


def fetch_stations() -> list[dict]:
    """Query Overpass, trying public mirrors when the main instance is overloaded."""
    settings = get_settings()
    urls = [settings.overpass_url, *FALLBACK_OVERPASS_URLS]
    last_error: Exception | None = None
    for attempt, url in enumerate(urls * 2):
        try:
            response = httpx.post(
                url,
                data={"data": QUERY},
                timeout=240,
                headers={"User-Agent": settings.http_contact_agent},
            )
            response.raise_for_status()
            return response.json()["elements"]
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
            log.warning("Overpass %s failed (%s), trying next", url, exc)
            time.sleep(min(5 * (attempt + 1), 30))
    raise RuntimeError(f"All Overpass servers failed: {last_error}")


def load_stations() -> int:
    stations = []
    for element in fetch_stations():
        tags = element.get("tags", {})
        name = tags.get("name")
        is_sbahn, is_ubahn = classify(tags)
        if not name or not (is_sbahn or is_ubahn):
            continue
        stations.append(
            Station(
                osm_id=element["id"],
                name=name,
                is_sbahn=is_sbahn,
                is_ubahn=is_ubahn,
                lines=tags.get("route_ref") or tags.get("line"),
                geom=point(element["lon"], element["lat"]),
            )
        )
    with session_scope() as session:
        session.execute(delete(Station))
        session.add_all(stations)
    log.info(
        "Stations: %d S-Bahn, %d U-Bahn",
        sum(s.is_sbahn for s in stations),
        sum(s.is_ubahn for s in stations),
    )
    return len(stations)
