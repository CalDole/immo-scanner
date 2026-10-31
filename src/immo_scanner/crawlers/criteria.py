"""Search criteria shared by all portals.

Portals that support it get the criteria in their search URL, so fewer pages need to be
crawled. ThinkImmo's price, area and room parameters are replaced with the same
criteria. Everything is filtered again with `Criteria.matches` before saving, because
some portals (Neubaukompass) can't filter on every criterion.
"""

import re
from dataclasses import dataclass
from typing import Self
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from immo_scanner.config import Settings
from immo_scanner.crawlers.base import ListingData

# Also used as PostgreSQL regexes (~*), so keep them to syntax both understand.
# "unvermietet" contains "vermietet", so the negative pattern is checked first.
RENTED_PATTERN = r"vermietet"
NOT_RENTED_PATTERN = r"unvermietet|nicht\s+vermietet"


def rented_from_title(title: str | None) -> bool | None:
    if not title or re.search(NOT_RENTED_PATTERN, title, re.I):
        return None
    return True if re.search(RENTED_PATTERN, title, re.I) else None


def _fmt(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else str(value)


@dataclass(frozen=True)
class Criteria:
    min_rooms: float | None = None
    min_living_area: float | None = None
    min_price: float | None = None
    max_price: float | None = None
    exclude_rented: bool = False

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(
            min_rooms=settings.min_rooms,
            min_living_area=settings.min_living_area,
            min_price=settings.min_price,
            max_price=settings.max_price,
            exclude_rented=settings.exclude_rented,
        )

    @property
    def has_price(self) -> bool:
        return self.min_price is not None or self.max_price is not None

    def matches(self, listing: ListingData) -> bool:
        """True unless a known value violates a criterion; unknown values pass.

        A Neubaukompass project is one row and stores the low end of each range. It
        matches only when that low end already meets the minima and no advertised
        price is above the cap.
        """

        def bounds(name: str, value: float | None) -> tuple[float | None, float | None]:
            low, high = (listing.raw.get(f"{name}_range") or [None, None])[:2]
            return (low if low is not None else value), (high if high is not None else value)

        price_low, price_high = bounds("price", listing.price)
        area_low, _ = bounds("area", listing.living_area)
        rooms_low, _ = bounds("rooms", listing.rooms)
        if self.min_price is not None and price_high is not None and price_high < self.min_price:
            return False
        if self.max_price is not None and (
            (price_low is not None and price_low > self.max_price)
            or (price_high is not None and price_high > self.max_price)
        ):
            return False
        if (
            self.min_living_area is not None
            and area_low is not None
            and area_low < self.min_living_area
        ):
            return False
        if self.min_rooms is not None and rooms_low is not None and rooms_low < self.min_rooms:
            return False
        if self.exclude_rented and (
            listing.raw.get("rented") is True or rented_from_title(listing.title)
        ):
            return False
        return True

    def immoscout24_url(self, url: str) -> str:
        """Add IS24 query filters (`numberofrooms=3.0-`) unless the URL already has them."""
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query))
        if self.min_rooms is not None:
            query.setdefault("numberofrooms", f"{float(self.min_rooms)}-")
        if self.min_living_area is not None:
            query.setdefault("livingspace", f"{float(self.min_living_area)}-")
        if self.has_price:
            low = f"{float(self.min_price)}" if self.min_price is not None else ""
            high = f"{float(self.max_price)}" if self.max_price is not None else ""
            query.setdefault("price", f"{low}-{high}")
        if self.exclude_rented:
            query.setdefault("rented", "false")
        return urlunsplit(parts._replace(query=urlencode(query, safe="-.")))

    def kleinanzeigen_url(self, url: str) -> str:
        """Kleinanzeigen encodes filters in the path: .../preis:250000:700000/c196l3331+..."""
        head, _, category = url.rstrip("/").rpartition("/")
        if self.has_price and "/preis:" not in url:
            head = f"{head}/preis:{_fmt(self.min_price)}:{_fmt(self.max_price)}"
        if self.min_living_area is not None and "qm_d:" not in category:
            category += f"+wohnung_kaufen.qm_d:{_fmt(self.min_living_area)}%2C"
        if self.min_rooms is not None and "zimmer_d:" not in category:
            category += f"+wohnung_kaufen.zimmer_d:{_fmt(self.min_rooms)}%2C"
        return f"{head}/{category}"

    def immowelt_url(self, url: str) -> str:
        """Add Immowelt classified-search filters (`numberOfRoomsMin=3`) unless already set."""
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query))
        for key, value in (
            ("numberOfRoomsMin", self.min_rooms),
            ("spaceMin", self.min_living_area),
            ("priceMin", self.min_price),
            ("priceMax", self.max_price),
        ):
            if value is not None:
                query.setdefault(key, _fmt(value))
        return urlunsplit(parts._replace(query=urlencode(query, safe=",")))

    def bsk_url(self, url: str) -> str:
        """Set BSK's price, area and room path groups (`--zimmer-ab-3--`) from these criteria.

        BSK has no minimum price; `matches` drops cheaper flats after the crawl.
        """
        head, sep, tail = url.rstrip("/").partition("/kaufen/suche/")
        if not sep:
            return url
        groups = tail.split("--")
        bands = {
            "kaufpreis": f"kaufpreis-bis-{_fmt(self.max_price)}"
            if self.max_price is not None
            else _BSK_ANY["kaufpreis"],
            "wohnflaeche": f"wohnflaeche-ab-{_fmt(self.min_living_area)}"
            if self.min_living_area is not None
            else _BSK_ANY["wohnflaeche"],
            "zimmer": f"zimmer-ab-{_fmt(self.min_rooms)}"
            if self.min_rooms is not None
            else _BSK_ANY["zimmer"],
        }
        for index, group in enumerate(groups):
            for name, value in bands.items():
                if group == _BSK_ANY[name] or group.startswith(f"{name}-"):
                    groups[index] = value
        return f"{head}{sep}{'--'.join(groups)}"

    def engelvoelkers_url(self, url: str) -> str:
        """Set E&V's `price.min`, `price.max`, `rooms.min` and `totalSurface.min`.

        Unset criteria drop their parameter and `page` is removed; the crawler adds it.
        Bare keys such as `propertyTypeSubType.apartment[]` keep their place.
        """
        parts = urlsplit(url)
        bands = {
            key: _fmt(value)
            for key, attr in _ENGELVOELKERS_BANDS.items()
            if (value := getattr(self, attr)) is not None
        }
        params = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if key == "page" or (key in _ENGELVOELKERS_BANDS and key not in bands):
                continue
            params.append((key, bands.pop(key, value)))
        params.extend(bands.items())
        query = "&".join(
            quote(key, safe="[].") + (f"={quote(value, safe='[].')}" if value else "")
            for key, value in params
        )
        return urlunsplit(parts._replace(query=query))

    def thinkimmo_query(self, query: str) -> str:
        """Drop any price, area or room params and set them from these criteria.

        The rest of the query (location, rented, apartment type, ...) stays as given.
        """
        params = [
            (key, value)
            for key, value in parse_qsl(query, keep_blank_values=True)
            if key not in _THINKIMMO_BAND_KEYS
        ]
        params.extend(
            (key, _fmt(value))
            for key, attr in _THINKIMMO_BANDS.items()
            if (value := getattr(self, attr)) is not None
        )
        return urlencode(params, safe=",")


# Price, area and room params. Upper bounds the shared criteria do not have are
# removed too, so a copied ThinkImmo alert cannot keep a second band.
_THINKIMMO_BAND_KEYS = frozenset(
    {"buyingPriceFrom", "buyingPriceTo", "sqmFrom", "sqmTo", "roomsFrom", "roomsTo"}
)
# BSK's "no limit" path groups, spelled as the site spells them.
_BSK_ANY = {
    "kaufpreis": "beliebiger-kaufpreis",
    "wohnflaeche": "belibiege-wohnflaeche",
    "zimmer": "belibige-zimmeranzahl",
}
_ENGELVOELKERS_BANDS = {
    "price.min": "min_price",
    "price.max": "max_price",
    "rooms.min": "min_rooms",
    "totalSurface.min": "min_living_area",
}
_THINKIMMO_BANDS = {
    "buyingPriceFrom": "min_price",
    "buyingPriceTo": "max_price",
    "sqmFrom": "min_living_area",
    "roomsFrom": "min_rooms",
}
