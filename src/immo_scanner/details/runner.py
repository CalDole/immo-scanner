"""Fetch detail pages for stored listings and save the parsed attributes."""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert

from immo_scanner.config import Settings
from immo_scanner.crawlers.browser import Browser
from immo_scanner.crawlers.kleinanzeigen import KleinanzeigenCrawler
from immo_scanner.crawlers.parsing import parse_postcode
from immo_scanner.db.models import (
    BskExpose,
    EngelvoelkersExpose,
    Immoscout24Expose,
    ImmoweltExpose,
    KleinanzeigenAd,
    Listing,
    ListingDetail,
    NeubaukompassUnit,
)
from immo_scanner.db.repository import point
from immo_scanner.db.session import session_scope
from immo_scanner.details import bsk
from immo_scanner.details import engelvoelkers as ev
from immo_scanner.details import immoscout24 as is24
from immo_scanner.details import immowelt as iw
from immo_scanner.details import kleinanzeigen as ka
from immo_scanner.details import neubaukompass as nbk
from immo_scanner.details.portals import PARSERS

log = logging.getLogger(__name__)

BROWSER_SOURCES = {"immoscout24", "immowelt", "neubaukompass"}
BLOCK_MARKERS = ("captcha-delivery.com", "Ich bin kein Roboter", "challenge-platform")
# Sources that store a full exposé next to the shared listing_details row.
EXPOSE_MODELS: dict[str, type] = {
    "immoscout24": Immoscout24Expose,
    "immowelt": ImmoweltExpose,
    "kleinanzeigen": KleinanzeigenAd,
    "neubaukompass": NeubaukompassUnit,
    "bsk": BskExpose,
    "engelvoelkers": EngelvoelkersExpose,
}


class DetailsBlocked(RuntimeError):
    pass


@dataclass
class Fetcher:
    """Retrieves detail pages and media, either through a browser or plain HTTP."""

    html: Callable[[str], str]
    bytes: Callable[[str], bytes]


def _pending(source: str, limit: int, refresh_days: int | None) -> list[tuple[int, str]]:
    with session_scope() as session:
        query = (
            select(Listing.id, Listing.url)
            .outerjoin(ListingDetail, ListingDetail.listing_id == Listing.id)
            .where(Listing.source == source, Listing.status == "active")
            .order_by(Listing.first_seen.desc())
            .limit(limit)
        )
        missing = [ListingDetail.listing_id.is_(None)]
        if expose := EXPOSE_MODELS.get(source):
            query = query.outerjoin(expose, expose.listing_id == Listing.id)
            missing.append(expose.listing_id.is_(None))
        if refresh_days is not None:
            cutoff = datetime.now(UTC) - timedelta(days=refresh_days)
            missing.append(ListingDetail.fetched_at < cutoff)
        query = query.where(or_(*missing))
        return [(row.id, row.url) for row in session.execute(query)]


def _upsert(model: type, values: dict[str, Any]) -> None:
    statement = insert(model).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=[model.listing_id],
        set_={k: statement.excluded[k] for k in values if k != "listing_id"},
    )
    with session_scope() as session:
        session.execute(statement)


def _save(listing_id: int, html: str, source: str) -> None:
    now = datetime.now(UTC)
    values = PARSERS[source](html).model_dump()
    _upsert(ListingDetail, values | {"listing_id": listing_id, "fetched_at": now})


def _save_is24_expose(
    listing_id: int, url: str, html: str, settings: Settings, client: httpx.Client
) -> None:
    expose = is24.parse_expose(html)
    folder = is24.download_media(expose, settings, client)
    values = expose.model_dump()
    values.update(
        listing_id=listing_id,
        fetched_at=datetime.now(UTC),
        url=url,
        photo_count=expose.photo_count,
        floor_plan_count=expose.floor_plan_count,
        media_dir=str(folder) if folder else None,
    )
    _upsert(Immoscout24Expose, values)


def _save_ka_ad(
    listing_id: int, url: str, html: str, settings: Settings, client: httpx.Client
) -> None:
    ad = ka.parse_ad(html, url)
    folder = ka.download_media(ad, settings, client)
    values = ad.model_dump()
    values.update(
        listing_id=listing_id,
        fetched_at=datetime.now(UTC),
        url=url,
        photo_count=ad.photo_count,
        media_dir=str(folder) if folder else None,
    )
    _upsert(KleinanzeigenAd, values)
    if not ad.address:
        return
    with session_scope() as session:
        listing = session.get(Listing, listing_id)
        if listing is None:
            return
        listing.address = ad.address
        if listing.postcode is None:
            listing.postcode = parse_postcode(ad.address)


def _save_immowelt(listing_id: int, url: str, html: str) -> None:
    expose = iw.parse_expose(html, url)
    values = expose.model_dump(exclude={"parsed"})
    values.update(listing_id=listing_id, fetched_at=datetime.now(UTC), url=url)
    _upsert(ImmoweltExpose, values)
    if not expose.parsed:
        return
    with session_scope() as session:
        listing = session.get(Listing, listing_id)
        if listing is None:
            return
        if expose.title:
            listing.title = expose.title
        if listing.postcode is None and expose.postcode:
            listing.postcode = expose.postcode
        if listing.district is None and expose.district:
            listing.district = expose.district


def _save_bsk(
    listing_id: int, url: str, html: str, settings: Settings, client: httpx.Client
) -> None:
    expose = bsk.parse_expose(html, url)
    folder = bsk.download_media(expose, settings, client)
    with session_scope() as session:
        listing = session.get(Listing, listing_id)
        raw = (listing.snapshots[-1].raw if listing and listing.snapshots else None) or {}
    values = expose.model_dump()
    values.update(
        listing_id=listing_id,
        fetched_at=datetime.now(UTC),
        url=url,
        bezirk=raw.get("bezirk"),
        ortsteil=raw.get("ortsteil"),
        photo_count=expose.photo_count,
        floor_plan_count=expose.floor_plan_count,
        media_dir=str(folder) if folder else None,
    )
    _upsert(BskExpose, values)


def _save_engelvoelkers(
    listing_id: int, url: str, html: str, settings: Settings, client: httpx.Client
) -> None:
    expose = ev.parse_expose(html, url)
    folder = ev.download_media(expose, settings, client)
    values = expose.model_dump(exclude={"district"})
    values.update(
        listing_id=listing_id,
        fetched_at=datetime.now(UTC),
        url=url,
        bezirk=expose.district,
        photo_count=expose.photo_count,
        floor_plan_count=expose.floor_plan_count,
        media_dir=str(folder) if folder else None,
    )
    _upsert(EngelvoelkersExpose, values)
    if expose.lat is None or expose.lon is None:
        return
    with session_scope() as session:
        listing = session.get(Listing, listing_id)
        # The pin is moved by up to 150 m: better than a postcode or district centroid.
        if listing is not None and (
            listing.geom is None or listing.geo_precision in ("postcode", "district")
        ):
            listing.geom = point(expose.lon, expose.lat)
            listing.geo_precision = "source"
            listing.geocoded_query = None


def _save_nbk_unit(
    listing_id: int, url: str, html: str, settings: Settings, fetch_bytes: Callable[[str], bytes]
) -> None:
    unit = nbk.parse_unit(html, url)
    folder = nbk.download_media(unit, settings, fetch_bytes)
    values = unit.model_dump()
    values.update(
        listing_id=listing_id,
        fetched_at=datetime.now(UTC),
        url=url,
        photo_count=unit.photo_count,
        floor_plan_count=unit.floor_plan_count,
        media_dir=str(folder) if folder else None,
    )
    _upsert(NeubaukompassUnit, values)


def _mark_removed(listing_id: int) -> None:
    with session_scope() as session:
        listing = session.get(Listing, listing_id)
        if listing is not None:
            listing.status = "removed"


@contextmanager
def _http_fetcher(settings: Settings, client: httpx.Client) -> Iterator[Fetcher]:
    crawler = KleinanzeigenCrawler(settings)

    def download(url: str) -> bytes:
        response = client.get(url)
        response.raise_for_status()
        return response.content

    try:
        yield Fetcher(html=crawler.fetch, bytes=download)
    finally:
        crawler.close()


@contextmanager
def _browser_fetcher(settings: Settings, source: str) -> Iterator[Fetcher]:
    with Browser(settings, source) as browser:

        def fetch(url: str) -> str:
            html = browser.goto(url)
            if any(marker in html for marker in BLOCK_MARKERS):
                solved = browser.wait_for_human(
                    lambda: not any(m in browser.page.content() for m in BLOCK_MARKERS),
                    f"{source} shows a captcha",
                )
                if not solved:
                    raise DetailsBlocked(source)
                browser.page.wait_for_timeout(2_000)
            return browser.page.content()

        yield Fetcher(html=fetch, bytes=browser.fetch_bytes)


def fetch_details(
    settings: Settings, source: str, limit: int, refresh_days: int | None = None
) -> dict[str, int]:
    pending = _pending(source, limit, refresh_days)
    stats = {"pending": len(pending), "saved": 0, "gone": 0, "failed": 0}
    if not pending:
        return stats
    log.info("%s: fetching %d detail pages", source, len(pending))
    media_client = httpx.Client(timeout=60, follow_redirects=True)
    fetcher = (
        _browser_fetcher(settings, source)
        if source in BROWSER_SOURCES
        else _http_fetcher(settings, media_client)
    )
    with fetcher as fetch, media_client:
        for index, (listing_id, url) in enumerate(pending, start=1):
            try:
                html = fetch.html(url)
                _save(listing_id, html, source)
                if source == "immoscout24":
                    _save_is24_expose(listing_id, url, html, settings, media_client)
                elif source == "immowelt":
                    _save_immowelt(listing_id, url, html)
                elif source == "kleinanzeigen":
                    _save_ka_ad(listing_id, url, html, settings, media_client)
                elif source == "neubaukompass":
                    _save_nbk_unit(listing_id, url, html, settings, fetch.bytes)
                elif source == "bsk":
                    _save_bsk(listing_id, url, html, settings, media_client)
                elif source == "engelvoelkers":
                    _save_engelvoelkers(listing_id, url, html, settings, media_client)
                stats["saved"] += 1
            except DetailsBlocked:
                log.warning("%s: blocked, stopping after %d pages", source, index - 1)
                break
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (404, 410):
                    _mark_removed(listing_id)
                    stats["gone"] += 1
                else:
                    log.warning("%s: %s for %s", source, exc.response.status_code, url)
                    stats["failed"] += 1
            except Exception:
                log.exception("%s: failed to fetch details for %s", source, url)
                stats["failed"] += 1
            if index % 25 == 0:
                log.info("%s: %d/%d detail pages", source, index, len(pending))
    return stats
