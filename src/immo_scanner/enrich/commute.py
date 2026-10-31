"""Public-transport travel times to the workplace, computed locally with r5py.

Requires the optional `commute` extra (`uv sync --extra commute`) and a Java 21 runtime.
Inputs are the Berlin OpenStreetMap extract and the VBB GTFS timetable.
"""

import logging
import os
import shutil
import time
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import geopandas as gpd
from geoalchemy2.shape import to_shape
from shapely.geometry import Point
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from immo_scanner.config import get_settings
from immo_scanner.db.models import CommuteTime, Listing, Place
from immo_scanner.enrich.features import DESTINATIONS
from immo_scanner.reference.download import download

log = logging.getLogger(__name__)

GTFS_MAX_AGE = timedelta(days=14)
# Listings per travel-time run and insert. PostgreSQL takes at most 65,535 parameters
# per statement; 500 listings x 3 places x 7 columns stays far below.
ORIGIN_BATCH = 500


class CommuteUnavailable(RuntimeError):
    pass


def _input_paths() -> tuple[Path, Path]:
    folder = get_settings().data_dir / "commute"
    return folder / "berlin-latest.osm.pbf", folder / "vbb-gtfs.zip"


def download_inputs(force: bool = False) -> list[Path]:
    settings = get_settings()
    osm_path, gtfs_path = _input_paths()
    gtfs_stale = (
        gtfs_path.exists()
        and time.time() - gtfs_path.stat().st_mtime > GTFS_MAX_AGE.total_seconds()
    )
    return [
        download(settings.osm_pbf_url, osm_path, force=force),
        download(settings.gtfs_url, gtfs_path, force=force or gtfs_stale),
    ]


def next_weekday_departure(hour: int) -> datetime:
    """Next Tuesday at `hour` - avoids weekends and most Monday holidays."""
    today = datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0)
    days_ahead = (1 - today.weekday()) % 7 or 7
    return today + timedelta(days=days_ahead)


def _configured_places(session: Session) -> list[Place]:
    return [
        place
        for name, _, _ in DESTINATIONS
        if (place := session.get(Place, name)) is not None
    ]


def _pending(session: Session, place_names: list[str]) -> list[tuple[int, float, float]]:
    """Listings missing a time for any destination, or whose coordinates moved.

    Rented flats stay in the app when removed listings are included, so they get a
    time too. Removed listings do not.
    """
    lon = func.ST_X(Listing.geom)
    lat = func.ST_Y(Listing.geom)
    listings = session.execute(
        select(Listing.id, lon, lat).where(
            Listing.geom.is_not(None), Listing.status.in_(("active", "rented"))
        )
    ).all()
    stored = session.execute(
        select(
            CommuteTime.listing_id,
            CommuteTime.place_name,
            CommuteTime.origin_lon,
            CommuteTime.origin_lat,
        ).where(CommuteTime.place_name.in_(place_names))
    ).all()
    by_listing: dict[int, dict[str, tuple[float, float]]] = {}
    for listing_id, place_name, origin_lon, origin_lat in stored:
        by_listing.setdefault(listing_id, {})[place_name] = (origin_lon, origin_lat)

    needed = set(place_names)
    pending: list[tuple[int, float, float]] = []
    for listing_id, x, y in listings:
        rows = by_listing.get(listing_id, {})
        moved = any(
            abs(old_x - x) > 1e-6 or abs(old_y - y) > 1e-6 for old_x, old_y in rows.values()
        )
        if needed - rows.keys() or moved:
            pending.append((listing_id, x, y))
    return pending


def clean_gtfs(path: Path) -> Path:
    """Copy the feed without header-only tables, which R5 rejects (e.g. frequencies.txt)."""
    target = path.with_name(path.stem + "-clean.zip")
    if target.exists() and target.stat().st_mtime >= path.stat().st_mtime:
        return target
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            with src.open(info) as fh:
                fh.readline()
                has_rows = bool(fh.readline().strip())
            if has_rows:
                dst.writestr(info, src.read(info))
            else:
                log.info("Dropping empty GTFS table %s", info.filename)
    return target


def _ensure_java_home() -> None:
    """r5py locates the JVM via JAVA_HOME; derive it from `java` on PATH if unset."""
    if os.environ.get("JAVA_HOME"):
        return
    java = shutil.which("java")
    if java:
        os.environ["JAVA_HOME"] = str(Path(java).resolve().parent.parent)


def _network(osm: Path, gtfs: Path):
    _ensure_java_home()
    import r5py

    return r5py.TransportNetwork(str(osm), [str(clean_gtfs(gtfs))])


def _travel_times(network, origins: gpd.GeoDataFrame, destinations: gpd.GeoDataFrame):
    import r5py

    kwargs = dict(
        origins=origins,
        destinations=destinations,
        departure=next_weekday_departure(get_settings().commute_departure_hour),
        transport_modes=[r5py.TransportMode.TRANSIT, r5py.TransportMode.WALK],
        max_time=timedelta(hours=2),
    )
    if hasattr(r5py, "TravelTimeMatrix"):
        return r5py.TravelTimeMatrix(network, **kwargs)
    return r5py.TravelTimeMatrixComputer(network, **kwargs).compute_travel_times()


def _as_minutes(value) -> float | None:
    if value is None or value != value:  # unreachable within max_time -> NaN
        return None
    return float(value)


def compute_commute_times(session: Session) -> int:
    _ensure_java_home()
    try:
        import r5py  # noqa: F401
    except ImportError as exc:
        raise CommuteUnavailable(
            "r5py is not installed - run `uv sync --extra commute` (needs Java 21)"
        ) from exc

    places = _configured_places(session)
    if not places:
        raise CommuteUnavailable("no destinations configured")
    pending = _pending(session, [place.name for place in places])
    if not pending:
        return 0

    osm, gtfs = download_inputs()
    destinations = gpd.GeoDataFrame(
        {"id": [place.name for place in places]},
        geometry=[to_shape(place.geom) for place in places],
        crs=4326,
    )
    log.info(
        "Computing transit times for %d listings to %d destinations", len(pending), len(places)
    )
    network = _network(osm, gtfs)
    stored = 0
    for start in range(0, len(pending), ORIGIN_BATCH):
        batch = pending[start : start + ORIGIN_BATCH]
        origins = gpd.GeoDataFrame(
            {"id": [p[0] for p in batch]},
            geometry=[Point(p[1], p[2]) for p in batch],
            crs=4326,
        )
        matrix = _travel_times(network, origins, destinations)
        stored += _store(session, batch, places, matrix)
        session.commit()
        log.info("Commute times: %d of %d listings", start + len(batch), len(pending))
    return stored


def commute_rows(
    batch: list[tuple[int, float, float]], places: list[Place], matrix
) -> list[dict]:
    minutes = {
        (int(origin), str(destination)): _as_minutes(value)
        for origin, destination, value in zip(
            matrix["from_id"], matrix["to_id"], matrix["travel_time"], strict=True
        )
    }
    now = datetime.now(UTC)
    return [
        {
            "listing_id": listing_id,
            "place_name": place.name,
            "minutes": minutes.get((listing_id, place.name)),
            "mode": "transit",
            "origin_lon": x,
            "origin_lat": y,
            "computed_at": now,
        }
        for listing_id, x, y in batch
        for place in places
    ]


def _store(session: Session, batch, places: list[Place], matrix) -> int:
    rows = commute_rows(batch, places, matrix)
    statement = insert(CommuteTime).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=["listing_id", "place_name"],
        set_={
            name: statement.excluded[name]
            for name in ("minutes", "mode", "origin_lon", "origin_lat", "computed_at")
        },
    )
    session.execute(statement)
    return len(rows)
