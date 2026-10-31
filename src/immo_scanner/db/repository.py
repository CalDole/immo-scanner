import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from geoalchemy2 import WKTElement
from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from immo_scanner.crawlers.base import SNAPSHOT_FIELDS, CrawlResult, ListingData
from immo_scanner.crawlers.criteria import NOT_RENTED_PATTERN, RENTED_PATTERN
from immo_scanner.crawlers.thinkimmo import (
    PORTAL_URL_PATTERNS,
    listing_data,
    portal_listing_keys,
    property_columns,
)
from immo_scanner.crawlers.thinkimmo import SOURCE as thinkimmo_source
from immo_scanner.db.models import (
    BskExpose,
    EngelvoelkersExpose,
    Immoscout24Expose,
    ImmoweltExpose,
    Listing,
    ListingSnapshot,
    ThinkimmoProperty,
)

log = logging.getLogger(__name__)


def point(lon: float, lat: float) -> WKTElement:
    return WKTElement(f"POINT({lon} {lat})", srid=4326)


def _differs(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is not b
    if isinstance(a, int | float) and isinstance(b, int | float):
        return abs(float(a) - float(b)) > 0.005
    return a != b


def save_listings(
    session: Session, source: str, listings: list[ListingData], seen_at: datetime
) -> dict[str, int]:
    """Upsert listings and append a snapshot whenever a tracked value changes.

    Fields missing from `listings` (None) keep their previous value, so partial sources
    such as alert emails don't erase data captured by a full crawl.
    """
    from immo_scanner.reference.milieuschutz import load_index, stamp_listing

    stats = {"seen": 0, "new": 0, "snapshots": 0}
    index = load_index(session)
    for item in listings:
        stats["seen"] += 1
        listing = session.scalar(
            select(Listing).where(Listing.source == source, Listing.external_id == item.external_id)
        )
        if listing is None:
            listing = Listing(
                source=source,
                external_id=item.external_id,
                url=item.url,
                first_seen=seen_at,
                is_new_build=False,
            )
            session.add(listing)
            stats["new"] += 1

        location_changed = (item.address and item.address != listing.address) or (
            item.postcode and item.postcode != listing.postcode
        )
        listing.url = item.url
        for name in ("title", "address", "postcode", "district"):
            value = getattr(item, name)
            if value is not None:
                setattr(listing, name, value)
        listing.is_new_build = bool(listing.is_new_build or item.is_new_build)
        is_stale = listing.last_seen is not None and seen_at < listing.last_seen
        if not is_stale:
            listing.last_seen = seen_at
            if listing.status != "rented":
                listing.status = "active"
        if seen_at < listing.first_seen:
            listing.first_seen = seen_at

        if item.lat is not None and item.lon is not None:
            listing.geom = point(item.lon, item.lat)
            listing.geo_precision = "source"
            listing.geocoded_query = None
        elif location_changed and listing.geo_precision != "source":
            listing.geom = None
            listing.geo_precision = None
            listing.geocoded_query = None

        lat = item.lat if listing.geom is not None else None
        lon = item.lon if listing.geom is not None else None
        stamp_listing(session, listing, index, lat, lon)

        session.flush()
        latest = session.scalar(
            select(ListingSnapshot)
            .where(ListingSnapshot.listing_id == listing.id)
            .order_by(ListingSnapshot.scraped_at.desc())
            .limit(1)
        )
        if latest is not None and latest.scraped_at >= seen_at:
            continue
        values = item.snapshot_values()
        if latest is not None:
            values = {
                name: value if value is not None else getattr(latest, name)
                for name, value in values.items()
            }
        if latest is None or any(_differs(getattr(latest, n), values[n]) for n in SNAPSHOT_FIELDS):
            session.add(
                ListingSnapshot(listing_id=listing.id, scraped_at=seen_at, raw=item.raw, **values)
            )
            stats["snapshots"] += 1
    return stats


def mark_removed(session: Session, source: str, seen_before: datetime) -> int:
    result = session.execute(
        update(Listing)
        .where(
            Listing.source == source,
            Listing.status == "active",
            Listing.last_seen < seen_before,
        )
        .values(status="removed")
    )
    return result.rowcount or 0


def delete_listings(session: Session, source: str, external_ids: Iterable[str]) -> int:
    """Delete listings (snapshots and commute times cascade)."""
    ids = list(external_ids)
    if not ids:
        return 0
    result = session.execute(
        delete(Listing).where(Listing.source == source, Listing.external_id.in_(ids))
    )
    return result.rowcount or 0


def apply_rented_filter(session: Session, exclude: bool) -> int:
    """Set status "rented" on listings an exposé or title marks as rented; undo when off.

    Search results rarely say a flat is rented, so these listings are kept (with the
    sticky status) rather than deleted: a deleted one would be re-added by the next crawl
    and its detail page fetched again.
    """
    if not exclude:
        result = session.execute(
            update(Listing).where(Listing.status == "rented").values(status="active")
        )
        return result.rowcount or 0
    rented_expose = or_(
        *(
            select(model.listing_id)
            .where(model.listing_id == Listing.id, model.is_rented.is_(True))
            .exists()
            for model in (Immoscout24Expose, ImmoweltExpose, BskExpose, EngelvoelkersExpose)
        )
    )
    rented_title = Listing.title.op("~*")(RENTED_PATTERN) & ~Listing.title.op("~*")(
        NOT_RENTED_PATTERN
    )
    result = session.execute(
        update(Listing)
        .where(Listing.status == "active", or_(rented_expose, rented_title))
        .values(status="rented"),
        execution_options={"synchronize_session": False},
    )
    return result.rowcount or 0


def prune_listings(session: Session, keep: Callable[[ListingData], bool]) -> dict[str, int]:
    """Delete stored listings whose latest snapshot fails `keep`; returns counts per source."""
    rows = session.execute(
        select(Listing, ListingSnapshot)
        .join(ListingSnapshot, ListingSnapshot.listing_id == Listing.id)
        .order_by(ListingSnapshot.listing_id, ListingSnapshot.scraped_at.desc())
        .distinct(ListingSnapshot.listing_id)
    )
    doomed: dict[str, list[str]] = {}
    for listing, snapshot in rows:
        data = ListingData(
            source=listing.source,
            external_id=listing.external_id,
            url=listing.url,
            title=listing.title,
            price=snapshot.price,
            living_area=snapshot.living_area,
            rooms=snapshot.rooms,
            raw=snapshot.raw or {},
        )
        if not keep(data):
            doomed.setdefault(listing.source, []).append(listing.external_id)
    return {source: delete_listings(session, source, ids) for source, ids in doomed.items()}


def _apply_thinkimmo_record(prop: ThinkimmoProperty, record: dict[str, Any]) -> None:
    for column, value in property_columns(record).items():
        setattr(prop, column, value)
    # Search results lack the exposé-only keys; merging keeps them from the last exposé.
    prop.raw = {**(prop.raw or {}), **record}


def save_thinkimmo_properties(
    session: Session, records: list[dict[str, Any]], seen_at: datetime
) -> dict[str, int]:
    """Upsert ThinkImmo search results."""
    stats = {"seen": 0, "new": 0}
    for record in records:
        prop = session.get(ThinkimmoProperty, record["id"])
        if prop is None:
            prop = ThinkimmoProperty(id=record["id"], first_seen=seen_at)
            session.add(prop)
            stats["new"] += 1
        _apply_thinkimmo_record(prop, record)
        prop.last_seen = seen_at
        prop.status = "active"
        stats["seen"] += 1
    return stats


def save_thinkimmo_detail(
    session: Session, property_id: str, record: dict[str, Any] | None, fetched_at: datetime
) -> None:
    """Store an exposé; an empty answer means ThinkImmo no longer has the property."""
    prop = session.get(ThinkimmoProperty, property_id)
    if prop is None:
        return
    prop.detail_fetched_at = fetched_at
    if record is None:
        prop.status = "removed"
        return
    _apply_thinkimmo_record(prop, record)


def thinkimmo_ids_needing_details(
    session: Session, limit: int, refresh_days: int | None = None
) -> list[str]:
    """Active properties without an exposé, changed since it was fetched, or older than
    `refresh_days`. Missing exposés come first, then the newest listings."""
    conditions = [
        ThinkimmoProperty.detail_fetched_at.is_(None),
        ThinkimmoProperty.source_updated_at > ThinkimmoProperty.detail_fetched_at,
    ]
    if refresh_days is not None:
        cutoff = datetime.now(UTC) - timedelta(days=refresh_days)
        conditions.append(ThinkimmoProperty.detail_fetched_at < cutoff)
    return list(
        session.scalars(
            select(ThinkimmoProperty.id)
            .where(ThinkimmoProperty.status == "active", or_(*conditions))
            .order_by(
                ThinkimmoProperty.detail_fetched_at.asc().nulls_first(),
                ThinkimmoProperty.published_at.desc().nulls_last(),
            )
            .limit(limit)
        )
    )


def link_thinkimmo_listings(
    session: Session, seen_at: datetime, keep: Callable[[ListingData], bool]
) -> dict[str, int]:
    """Attach properties seen at `seen_at` to listings.

    A property whose portal ad was found by an own crawler links to that listing (and
    loses its `thinkimmo` listing, if it had one). Every other property gets or updates
    a `thinkimmo` listing, unless `keep` rejects it.
    """
    props = list(
        session.scalars(select(ThinkimmoProperty).where(ThinkimmoProperty.last_seen >= seen_at))
    )
    keys = {prop.id: portal_listing_keys(prop.platforms) for prop in props}
    portal_listings = {
        (source, external_id): listing_id
        for source, external_id, listing_id in session.execute(
            select(Listing.source, Listing.external_id, Listing.id).where(
                Listing.source.in_([source for source, _ in PORTAL_URL_PATTERNS])
            )
        )
    }

    stats = {"linked": 0, "own": 0, "rejected": 0}
    own: list[tuple[ThinkimmoProperty, ListingData]] = []
    obsolete: list[str] = []
    for prop in props:
        match = next((portal_listings[k] for k in keys[prop.id] if k in portal_listings), None)
        if match is not None:
            prop.listing_id = match
            obsolete.append(prop.id)
            stats["linked"] += 1
            continue
        item = listing_data(prop)
        if keep(item):
            own.append((prop, item))
        else:
            prop.listing_id = None
            obsolete.append(prop.id)
            stats["rejected"] += 1
    stats["duplicates_deleted"] = delete_listings(session, thinkimmo_source, obsolete)

    saved = save_listings(session, thinkimmo_source, [item for _, item in own], seen_at)
    stats["own"], stats["own_new"] = len(own), saved["new"]
    ids = dict(
        session.execute(
            select(Listing.external_id, Listing.id).where(Listing.source == thinkimmo_source)
        ).all()
    )
    for prop, _ in own:
        prop.listing_id = ids.get(prop.id)
    return stats


def mark_thinkimmo_removed(session: Session, seen_before: datetime) -> int:
    result = session.execute(
        update(ThinkimmoProperty)
        .where(ThinkimmoProperty.status == "active", ThinkimmoProperty.last_seen < seen_before)
        .values(status="removed")
    )
    return result.rowcount or 0


def save_crawl_result(
    session: Session, source: str, result: CrawlResult, started_at: datetime
) -> dict[str, int]:
    stats = save_listings(session, source, result.listings, started_at)
    if result.complete and result.listings:
        stats["removed"] = mark_removed(session, source, started_at)
    else:
        stats["removed"] = 0
        log.info("%s: crawl incomplete, not marking unseen listings as removed", source)
    return stats
