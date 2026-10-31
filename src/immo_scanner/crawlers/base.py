import logging
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Self

import httpx
from pydantic import BaseModel, Field
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from immo_scanner.config import Settings

log = logging.getLogger(__name__)

SNAPSHOT_FIELDS = ("price", "living_area", "rooms", "floor", "year_built", "hausgeld")


class ListingData(BaseModel):
    """Normalized listing as produced by every crawler."""

    source: str
    external_id: str
    url: str
    title: str | None = None
    address: str | None = None
    postcode: str | None = None
    district: str | None = None
    lat: float | None = None
    lon: float | None = None
    price: float | None = None
    living_area: float | None = None
    rooms: float | None = None
    floor: int | None = None
    year_built: int | None = None
    hausgeld: float | None = None
    is_new_build: bool = False
    raw: dict[str, Any] = Field(default_factory=dict)

    def snapshot_values(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in SNAPSHOT_FIELDS}


@dataclass
class CrawlResult:
    listings: list[ListingData] = field(default_factory=list)
    # True when the crawler reached the last result page, so unseen listings can be
    # marked as removed. False when stopped early (page limit, block, error).
    complete: bool = False


class BlockedError(RuntimeError):
    """The site served a captcha or bot-protection page."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return False


class BaseCrawler(ABC):
    source: ClassVar[str]

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._last_request = 0.0
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                headers={
                    "User-Agent": self.settings.user_agent,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "de-DE,de;q=0.9,en;q=0.5",
                },
                follow_redirects=True,
                timeout=30,
            )
        return self._client

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
    def fetch(self, url: str, **params: Any) -> str:
        self.throttle()
        log.debug("GET %s %s", url, params or "")
        response = self.client.get(url, params=params or None)
        if response.status_code == 403:
            raise BlockedError(f"{self.source}: 403 for {url}")
        response.raise_for_status()
        return response.text

    @abstractmethod
    def crawl(self, max_pages: int) -> CrawlResult: ...

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def dedupe(listings: list[ListingData]) -> list[ListingData]:
    seen: dict[str, ListingData] = {}
    for item in listings:
        seen.setdefault(item.external_id, item)
    return list(seen.values())
