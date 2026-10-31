"""ThinkImmo crawler.

ThinkImmo aggregates listings from ~40 portals and adds a market value and rent
estimate, cash flow, macro and micro location data. Its web app reads a JSON API
that answers without login: `/immo` is the search (up to 50 records per page),
`/immo/{id}` the exposé with the full record. Records go to `thinkimmo_properties`,
not to `listings`.
"""

import logging
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Self

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from immo_scanner.config import Settings
from immo_scanner.crawlers.base import BlockedError, ListingData, _is_retryable
from immo_scanner.crawlers.criteria import Criteria

log = logging.getLogger(__name__)

PAGE_SIZE = 50
SOURCE = "thinkimmo"
# Portal ad URLs -> (listings.source, external_id) of the crawlers in this project.
PORTAL_URL_PATTERNS = (
    ("immoscout24", re.compile(r"immobilienscout24\.de/expose/(\d+)")),
    ("immowelt", re.compile(r"immowelt\.de/expose/([\w-]+)")),
    ("kleinanzeigen", re.compile(r"kleinanzeigen\.de/s-anzeige/[^/?#]+/(\d+)")),
    ("neubaukompass", re.compile(r"neubaukompass\.de/.*expose-(\d+)")),
)


@dataclass
class SearchResult:
    records: list[dict[str, Any]] = field(default_factory=list)
    total: int | None = None
    # True when every page up to `total` was fetched.
    complete: bool = False


class ThinkImmoClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.thinkimmo_api_url.rstrip("/")
        self._last_request = 0.0
        self.client = httpx.Client(
            headers={
                "User-Agent": settings.user_agent,
                "Accept": "application/json",
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.5",
                "Origin": "https://thinkimmo.com",
                "Referer": "https://thinkimmo.com/",
            },
            timeout=30,
        )

    def throttle(self) -> None:
        delay = random.uniform(self.settings.crawl_delay_min, self.settings.crawl_delay_max)
        wait = self._last_request + delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=5, max=120),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def get_json(self, path: str, query: str) -> Any:
        self.throttle()
        url = f"{self.base_url}{path}?{query}"
        log.debug("GET %s", url)
        response = self.client.get(url)
        if response.status_code == 403:
            raise BlockedError(f"thinkimmo: 403 for {url}")
        response.raise_for_status()
        # Unknown ids answer 200 with an empty body.
        return response.json() if response.content.strip() else None

    def search_page(self, offset: int, size: int = PAGE_SIZE) -> dict[str, Any]:
        query = "&".join(
            (
                Criteria.from_settings(self.settings).thinkimmo_query(
                    self.settings.thinkimmo_search_query
                ),
                self.settings.thinkimmo_finance_query,
                f"from={offset}&size={size}",
            )
        )
        return self.get_json("/immo", query) or {}

    def search(self, max_pages: int | None = None) -> SearchResult:
        result = SearchResult()
        seen: set[str] = set()
        page = 0
        while max_pages is None or page < max_pages:
            data = self.search_page(page * PAGE_SIZE)
            records = data.get("results") or []
            result.total = data.get("total", result.total)
            page += 1
            new = 0
            for record in records:
                if record.get("id") and record["id"] not in seen:
                    seen.add(record["id"])
                    result.records.append(record)
                    new += 1
            log.info(
                "thinkimmo page %d: %d records, %d new (%d of %s)",
                page, len(records), new, len(result.records), result.total,
            )
            if not records:
                result.complete = page > 1
                break
            if result.total is not None and page * PAGE_SIZE >= result.total:
                result.complete = True
                break
        return result

    def property(self, property_id: str) -> dict[str, Any] | None:
        return self.get_json(f"/immo/{property_id}", self.settings.thinkimmo_finance_query)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


_MISSING = object()


def _get(data: dict[str, Any], path: str) -> Any:
    value: Any = data
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return _MISSING
        value = value[key]
    return value


def _float(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _int(value: Any) -> int | None:
    number = _float(value)
    return round(number) if number is not None else None


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


COLUMNS: dict[str, tuple[str, Callable[[Any], Any]]] = {
    "title": ("title", _text),
    "description": ("description", _text),
    "realtor_company": ("realtorCompany", _text),
    "is_private_offer": ("privateOffer", _bool),
    "is_active": ("active", _bool),
    "published_at": ("publishDate", _datetime),
    "source_updated_at": ("lastUpdatedAt", _datetime),
    "running_time_days": ("runningTime", _int),
    "buying_price": ("buyingPrice", _float),
    "price_per_m2": ("pricePerSqm", _float),
    "living_area": ("squareMeter", _float),
    "rooms": ("rooms", _float),
    "commission_pct": ("comission", _float),
    "hausgeld": ("houseMoney", _float),
    "price_reduced": ("priceReduced", _bool),
    "price_increased": ("priceIncreased", _bool),
    "apartment_type": ("apartmentType", _text),
    "condition": ("condition", _text),
    "interior_quality": ("interiorQuality", _text),
    "heating_type": ("heatingType", _text),
    "year_built": ("constructionYear", _int),
    "last_renovation_year": ("lastRefurbishment", _int),
    "floor": ("floor", _int),
    "total_floors": ("numberOfFloors", _int),
    "parking_spaces": ("numberOfParkingSpaces", _int),
    "has_elevator": ("lift", _bool),
    "has_cellar": ("cellar", _bool),
    "has_balcony": ("balcony", _bool),
    "has_garden": ("garden", _bool),
    "has_fitted_kitchen": ("builtInKitchen", _bool),
    "has_energy_certificate": ("energyCertificate", _bool),
    "energy_efficiency_class": ("energyEfficiencyClass", _text),
    "is_rented": ("rented", _bool),
    "is_leasehold": ("leasehold", _bool),
    "is_foreclosure": ("foreClosure", _bool),
    "postcode": ("zip", _text),
    "city": ("address.city", _text),
    "borough": ("address.borough", _text),
    "suburb": ("address.suburb", _text),
    "street": ("address.road", _text),
    "house_number": ("address.house_number", _text),
    "lat": ("address.lat", _float),
    "lon": ("address.lon", _float),
    "market_price_per_m2": ("spPricePerSqm", _float),
    "market_price_per_m2_min": ("metaSpPricePerSqm.range.min", _float),
    "market_price_per_m2_max": ("metaSpPricePerSqm.range.max", _float),
    "price_to_market_pct": ("priceInMarket", _float),
    "estimated_rent": ("rentPrice", _float),
    "estimated_rent_per_m2": ("rentPricePerSqm", _float),
    "estimated_rent_per_m2_min": ("metaSpRentPricePerSqm.range.min", _float),
    "estimated_rent_per_m2_max": ("metaSpRentPricePerSqm.range.max", _float),
    "current_rent": ("rentPriceCurrent", _float),
    "current_rent_per_m2": ("rentPriceCurrentPerSqm", _float),
    "gross_return": ("grossReturn", _float),
    "gross_return_current": ("grossReturnCurrent", _float),
    "cash_flow": ("cashFlow", _float),
    "own_capital_return": ("ownCapitalReturn", _float),
    "location_score": ("locationFactor.score", _float),
    "district_price_per_m2": ("aggregations.district.pricePerSqm", _float),
    "city_price_per_m2": ("aggregations.location.pricePerSqm", _float),
    "similar_price_per_m2": ("aggregations.similarListing.pricePerSqm", _float),
    "views": ("numberOfViews", _int),
    "clicks": ("numberOfClicks", _int),
    "favorites": ("numberOfFavorites", _int),
    "platforms": ("platforms", _list),
    "images": ("images", _list),
    "buying_price_history": ("buyingPriceHistory", _list),
    "change_log": ("changeLog", _list),
    "address": ("address", _dict),
    "original_address": ("originalAddress", _dict),
    "portal_addresses": ("oAddress", _dict),
    "location_factor": ("locationFactor", _dict),
    "aggregations": ("aggregations", _dict),
    "offer_prices": ("offerPrices", _dict),
    "cash_flow_calc": ("cashFlowCalc", _dict),
}


def property_columns(record: dict[str, Any]) -> dict[str, Any]:
    """Column values for the keys present in `record`.

    Search results lack some exposé fields (description, micro location, views), so
    absent keys are left out instead of being set to None.
    """
    values = {
        column: convert(value)
        for column, (path, convert) in COLUMNS.items()
        if (value := _get(record, path)) is not _MISSING
    }
    if "platforms" in values:
        platforms = [p for p in values["platforms"] if isinstance(p, dict)]
        first = next((p for p in platforms if p.get("active")), platforms[0] if platforms else {})
        values["primary_platform"] = _text(first.get("name"))
        values["url"] = _text(first.get("url"))
        values["platform_names"] = (
            ", ".join(name for p in platforms if (name := _text(p.get("name")))) or None
        )
    if "images" in values:
        images = [i for i in values["images"] if isinstance(i, dict)]
        values["image_count"] = len(images)
        values["floor_plan_count"] = sum(1 for i in images if i.get("floorPlan"))
    return values


def portal_listing_keys(platforms: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(source, external_id) pairs of the portals this project crawls itself."""
    keys = []
    for platform in platforms or []:
        url = platform.get("url") or ""
        for source, pattern in PORTAL_URL_PATTERNS:
            if (match := pattern.search(url)) and (source, match.group(1)) not in keys:
                keys.append((source, match.group(1)))
    return keys


def _known(value: str | None) -> str | None:
    return value if value and value != "NO_INFORMATION" else None


def listing_data(prop: Any) -> ListingData:
    """A `thinkimmo` listing for a ThinkimmoProperty that no own crawler has found."""
    street = " ".join(p for p in (prop.street, prop.house_number) if p) or None
    place = " ".join(p for p in (prop.postcode, prop.city) if p) or None
    address = ", ".join(p for p in (street, prop.suburb, place) if p) or None
    # Without a house number ThinkImmo's point is the postcode or neighbourhood centre;
    # leave those to the geocoder.
    exact = prop.house_number and prop.lat is not None and prop.lon is not None
    return ListingData(
        source=SOURCE,
        external_id=prop.id,
        url=prop.url or f"https://api.thinkimmo.com/immo/{prop.id}",
        title=prop.title,
        address=address,
        postcode=prop.postcode,
        district=prop.suburb,
        lat=prop.lat if exact else None,
        lon=prop.lon if exact else None,
        price=prop.buying_price,
        living_area=prop.living_area,
        rooms=prop.rooms,
        floor=prop.floor,
        year_built=prop.year_built,
        hausgeld=prop.hausgeld,
        is_new_build=_known(prop.condition) == "FIRST_TIME_USE",
        raw={
            "thinkimmo_id": prop.id,
            "platforms": prop.platform_names,
            "foreclosure": prop.is_foreclosure,
        },
    )
