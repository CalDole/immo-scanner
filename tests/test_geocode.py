import httpx

from immo_scanner.db.models import Listing
from immo_scanner.enrich.geocode import (
    GeocodeResult,
    address_queries,
    geocode_listings,
    min_request_interval,
    place_queries,
    street_names,
    usable_result,
)

REQUEST = httpx.Request("GET", "https://nominatim.example/search")


def _status_error(code: int) -> httpx.HTTPStatusError:
    return httpx.HTTPStatusError(
        str(code), request=REQUEST, response=httpx.Response(code, request=REQUEST)
    )


class FakeSession:
    def __init__(self, listings: list[Listing]) -> None:
        self.listings = listings
        self.commits = 0

    def scalars(self, _query):
        return self

    def all(self) -> list[Listing]:
        return self.listings

    def commit(self) -> None:
        self.commits += 1


class StubGeocoder:
    """Answers per listing id: a result, None (nothing found) or an exception to raise."""

    def __init__(self, answers: dict[int, object]) -> None:
        self.answers = answers
        self.calls: list[int] = []

    def geocode_listing(self, listing: Listing):
        self.calls.append(listing.id)
        answer = self.answers[listing.id]
        if isinstance(answer, Exception):
            raise answer
        if answer is None:
            return None, "q:nowhere"
        return answer, "q:somewhere"


def _listings(*ids: int) -> list[Listing]:
    return [Listing(id=listing_id, status="active") for listing_id in ids]


def test_geocode_listings_skips_a_listing_whose_lookup_fails():
    listings = _listings(1, 2, 3)
    geocoder = StubGeocoder(
        {1: GeocodeResult(52.5, 13.4, "exact"), 2: _status_error(400), 3: None}
    )
    placed = geocode_listings(FakeSession(listings), waits=(), geocoder=geocoder)
    assert placed == 1
    assert geocoder.calls == [1, 2, 3]
    assert listings[0].geom is not None and listings[0].geo_precision == "exact"
    assert listings[1].geocoded_query is None
    assert listings[2].geocoded_query == "q:nowhere"


def test_geocode_listings_stops_when_nominatim_stays_unavailable(monkeypatch):
    monkeypatch.setattr("immo_scanner.enrich.geocode.time.sleep", lambda _: None)
    listings = _listings(1, 2, 3)
    geocoder = StubGeocoder(
        {1: GeocodeResult(52.5, 13.4, "exact"), 2: _status_error(429), 3: None}
    )
    placed = geocode_listings(FakeSession(listings), waits=(1, 1), geocoder=geocoder)
    assert placed == 1
    assert geocoder.calls == [1, 2, 2, 2]
    assert listings[1].geocoded_query is None
    assert listings[2].geocoded_query is None


def test_only_the_public_nominatim_is_rate_limited():
    assert min_request_interval("https://nominatim.openstreetmap.org/search") == 1.1
    assert min_request_interval("http://localhost:8080") == 0.0
    assert min_request_interval("http://127.0.0.1:8080") == 0.0


def test_incidents_share_the_retry_helper():
    from immo_scanner.enrich import geocode, incidents

    assert incidents._with_retries is geocode._with_retries

PAULE = "Paule-Panke-Straße 30-34, 13187 Berlin / Pankow (Pankow)"
CORNER = (
    "Gravensteinstraße Ecke An der Streuobstwiese, 13127 Berlin / "
    "Französisch Buchholz (Pankow)"
)


def test_address_queries_drop_district_suffix_and_house_range():
    queries = address_queries(PAULE)
    assert queries[0] == "Paule-Panke-Straße 30-34, 13187 Berlin"
    assert "Paule-Panke-Straße 30, 13187 Berlin" in queries
    assert "Paule-Panke-Straße, 13187 Berlin" in queries


def test_place_query_uses_the_quartier_name():
    assert place_queries(PAULE) == ["Paule Panke, 13187 Berlin"]


def test_corner_address_is_split_into_both_streets():
    queries = address_queries(CORNER)
    assert "Gravensteinstraße, 13127 Berlin" in queries
    assert "An der Streuobstwiese, 13127 Berlin" in queries


def test_street_names_for_sibling_lookup():
    assert street_names(PAULE) == ["Paule-Panke-Straße"]
    assert street_names("Esplanade 13A, 13187 Berlin / Pankow (Pankow)") == ["Esplanade"]
    assert street_names(CORNER) == ["Gravensteinstraße", "An der Streuobstwiese"]


def test_usable_result_rejects_city_centroid_for_postcode():
    berlin_city = {
        "addresstype": "city",
        "type": "administrative",
        "display_name": "Berlin, Deutschland",
    }
    assert not usable_result(berlin_city)
    assert not usable_result(berlin_city, forced_precision="postcode")
    postcode = {
        "addresstype": "postcode",
        "type": "postal_code",
        "display_name": "12557, Köpenick, Treptow-Köpenick, Berlin, Deutschland",
    }
    assert usable_result(postcode, forced_precision="postcode")
    street = {"addresstype": "road", "type": "residential"}
    assert usable_result(street)
