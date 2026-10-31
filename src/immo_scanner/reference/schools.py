"""Gymnasiums and Montessori kindergartens from OpenStreetMap (Overpass API)."""

import logging
import re
import time

import httpx
from sqlalchemy import delete

from immo_scanner.config import get_settings
from immo_scanner.db.models import School
from immo_scanner.db.repository import point
from immo_scanner.db.session import session_scope
from immo_scanner.enrich.features import refresh_features
from immo_scanner.reference.stations import BBOX, FALLBACK_OVERPASS_URLS

log = logging.getLogger(__name__)

QUERY = f"""
[out:json][timeout:180];
(
  nwr["amenity"="school"]["name"~"Gymnasium",i]({BBOX});
  nwr["amenity"="school"]["school:de"="gymnasium"]({BBOX});
  nwr["amenity"="kindergarten"]["name"~"Montessori",i]({BBOX});
  nwr["amenity"="kindergarten"]["operator"~"Montessori",i]({BBOX});
);
out tags center;
"""

_PARENTHETICAL = re.compile(r"\s*\([^)]*\)")
_HOUSE = re.compile(r"\s+Haus\s+\S+$", re.IGNORECASE)
_GYMNASIUM = re.compile(r"gymnasium", re.IGNORECASE)
_MONTESSORI = re.compile(r"montessori", re.IGNORECASE)


def display_name(name: str) -> str:
    """One label per school: drop annexes, address lines and '(Haus 3)' suffixes."""
    lines = []
    for line in name.splitlines():
        cleaned = re.sub(r"\s+", " ", line).strip()
        if cleaned and not cleaned.lower().startswith("berlin"):
            lines.append(cleaned)
    cleaned = _PARENTHETICAL.sub("", " ".join(lines))
    return _HOUSE.sub("", cleaned).strip()


def _is_montessori(tags: dict[str, str]) -> bool:
    return any(_MONTESSORI.search(tags.get(key) or "") for key in ("name", "operator"))


def _is_hort_only(name: str) -> bool:
    """After-school care tagged as a kindergarten, not a Kita."""
    lowered = name.lower()
    if "hort" not in lowered:
        return False
    return not any(
        word in lowered for word in ("kita", "kinderhaus", "kindergarten", "kinderladen")
    )


def classify(tags: dict[str, str]) -> str | None:
    """Return 'gymnasium', 'montessori_kindergarten', or None."""
    amenity = tags.get("amenity")
    name = tags.get("name") or ""
    if amenity == "school" and (tags.get("school:de") == "gymnasium" or _GYMNASIUM.search(name)):
        return "gymnasium"
    if amenity == "kindergarten" and _is_montessori(tags) and not _is_hort_only(name):
        return "montessori_kindergarten"
    return None


def _coords(element: dict) -> tuple[float, float] | None:
    if "lon" in element and "lat" in element:
        return element["lon"], element["lat"]
    center = element.get("center") or {}
    if "lon" in center and "lat" in center:
        return center["lon"], center["lat"]
    return None


def fetch_schools() -> list[dict]:
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


def load_schools() -> int:
    schools = []
    seen: set[tuple[str, int]] = set()
    for element in fetch_schools():
        key = (element["type"], element["id"])
        if key in seen:
            continue
        tags = element.get("tags", {})
        kind = classify(tags)
        coords = _coords(element)
        name = display_name(tags.get("name") or "")
        if kind is None or coords is None or not name:
            continue
        seen.add(key)
        schools.append(
            School(
                osm_type=element["type"],
                osm_id=element["id"],
                name=name,
                kind=kind,
                geom=point(coords[0], coords[1]),
            )
        )
    with session_scope() as session:
        session.execute(delete(School))
        session.add_all(schools)
        session.flush()
        refresh_features(session)
    log.info(
        "Schools: %d Gymnasiums, %d Montessori kindergartens",
        sum(s.kind == "gymnasium" for s in schools),
        sum(s.kind == "montessori_kindergarten" for s in schools),
    )
    return len(schools)
