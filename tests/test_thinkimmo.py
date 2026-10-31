from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx

from immo_scanner.config import Settings
from immo_scanner.crawlers.thinkimmo import (
    ThinkImmoClient,
    listing_data,
    portal_listing_keys,
    property_columns,
)

SEARCH_RECORD = {
    "id": "f89a4c117f387e4e0887b208a121dd12",
    "title": "Wohnung in Berlin (Moabit)",
    "zip": "10555",
    "buyingPrice": 330000,
    "rooms": 3,
    "squareMeter": 76,
    "comission": None,
    "platforms": [
        {"name": "is24", "id": "is1", "url": "https://is24.example/1", "active": False},
        {"name": "DeinNeuesZuhause", "id": "dnz1", "url": "https://wohnglueck.de/property/1",
         "active": True},
    ],
    "spPricePerSqm": 5592.1,
    "metaSpPricePerSqm": {"model": "05, 2026", "range": {"min": 4565.8, "max": 7184.2}},
    "rentPrice": 1306.0000000000002,
    "rentPricePerSqm": 17.18,
    "metaSpRentPricePerSqm": {"range": {"min": 13.5, "max": 23.5}},
    "rentPriceCurrent": None,
    "address": {
        "city": "Berlin",
        "borough": "Mitte",
        "suburb": "Moabit",
        "postcode": "10555",
        "lat": 52.5215243,
        "lon": 13.3355118,
    },
    "foreClosure": True,
    "locationFactor": {"score": 67, "population": 3755251},
    "grossReturn": 4.75,
    "constructionYear": 1900,
    "apartmentType": "NO_INFORMATION",
    "lift": False,
    "floor": None,
    "rented": False,
    "publishDate": "2026-10-05T12:22:36.993Z",
    "aggregations": {
        "district": {"name": "Moabit", "pricePerSqm": 5750},
        "location": {"name": "Berlin", "pricePerSqm": 4900},
        "similarListing": {"pricePerSqm": 5592.1},
    },
    "priceInMarket": 77.6,
    "oAddress": {"DeinNeuesZuhause": {"postcode": "10555"}},
    "images": [
        {"id": "a", "originalUrl": "https://img/1.jpg"},
        {"id": "b", "originalUrl": "https://img/2.jpg", "floorPlan": True},
    ],
    "buyingPriceHistory": [{"buyingPrice": 330000, "platformName": "DeinNeuesZuhause"}],
    "cashFlow": -500.92,
    "ownCapitalReturn": -3.5,
}

EXPOSE_RECORD = {
    **SEARCH_RECORD,
    "description": "  Helle Wohnung.  ",
    "heatingType": "CENTRAL_HEATING",
    "builtInKitchen": True,
    "numberOfViews": 18,
    "locationFactor": {
        "score": 67,
        "microLocation": {"schools": [{"name": "Hansa-Grundschule", "distance": 0.2}]},
    },
    "offerPrices": {"10555": {"docCount": 190, "pricePerSqm": 5379.3}},
    "cashFlowCalc": {"creditCost": 1480.4, "interestRate": 4},
    "changeLog": [],
}


def test_search_record_columns():
    values = property_columns(SEARCH_RECORD)
    assert (values["buying_price"], values["living_area"], values["rooms"]) == (330000, 76, 3)
    assert values["postcode"] == "10555"
    assert (values["borough"], values["suburb"]) == ("Mitte", "Moabit")
    assert (values["lat"], values["lon"]) == (52.5215243, 13.3355118)
    assert values["published_at"] == datetime(2026, 10, 5, 12, 22, 36, 993000, tzinfo=UTC)
    assert values["market_price_per_m2_min"] == 4565.8
    assert values["estimated_rent_per_m2_max"] == 23.5
    assert values["district_price_per_m2"] == 5750
    assert values["city_price_per_m2"] == 4900
    assert values["location_score"] == 67
    assert values["is_foreclosure"] is True
    assert values["has_elevator"] is False
    assert values["year_built"] == 1900
    # Present but null in the API.
    assert values["commission_pct"] is None
    assert values["floor"] is None
    assert values["current_rent"] is None
    assert values["portal_addresses"] == {"DeinNeuesZuhause": {"postcode": "10555"}}


def test_search_record_leaves_expose_fields_out():
    values = property_columns(SEARCH_RECORD)
    for column in ("description", "views", "offer_prices", "cash_flow_calc", "heating_type"):
        assert column not in values


def test_primary_platform_is_first_active_portal():
    values = property_columns(SEARCH_RECORD)
    assert values["primary_platform"] == "DeinNeuesZuhause"
    assert values["url"] == "https://wohnglueck.de/property/1"
    assert values["platform_names"] == "is24, DeinNeuesZuhause"


def test_image_counts():
    values = property_columns(SEARCH_RECORD)
    assert (values["image_count"], values["floor_plan_count"]) == (2, 1)


def test_expose_record_columns():
    values = property_columns(EXPOSE_RECORD)
    assert values["description"] == "Helle Wohnung."
    assert values["heating_type"] == "CENTRAL_HEATING"
    assert values["has_fitted_kitchen"] is True
    assert values["views"] == 18
    assert values["offer_prices"]["10555"]["docCount"] == 190
    assert values["cash_flow_calc"]["creditCost"] == 1480.4
    assert values["location_factor"]["microLocation"]["schools"][0]["name"] == "Hansa-Grundschule"
    assert values["change_log"] == []


def test_portal_listing_keys():
    platforms = [
        {"name": "is24", "id": "171302033",
         "url": "https://www.immobilienscout24.de/expose/171302033"},
        {"name": "immowelt", "id": "immowelt572e",
         "url": "https://www.immowelt.de/expose/b9a18ca6-b296-4091-8bbd-4e16a955cb40"},
        {"name": "ebk", "url": "https://www.kleinanzeigen.de/s-anzeige/schoene-wohnung/3408795915-196-3421"},
        {"name": "DeinNeuesZuhause", "url": "https://wohnglueck.de/property/1860335"},
        {"name": "is24", "url": "https://www.immobilienscout24.de/expose/171302033?ref=x"},
    ]
    assert portal_listing_keys(platforms) == [
        ("immoscout24", "171302033"),
        ("immowelt", "b9a18ca6-b296-4091-8bbd-4e16a955cb40"),
        ("kleinanzeigen", "3408795915"),
    ]


def _prop(**overrides) -> SimpleNamespace:
    values = {
        "id": "abc",
        "url": "https://wohnglueck.de/property/1",
        "title": "Wohnung in Berlin (Moabit)",
        "street": None,
        "house_number": None,
        "postcode": "10555",
        "city": "Berlin",
        "suburb": "Moabit",
        "lat": 52.52,
        "lon": 13.33,
        "buying_price": 330000.0,
        "living_area": 76.0,
        "rooms": 3.0,
        "floor": None,
        "year_built": 1900,
        "hausgeld": None,
        "condition": "NO_INFORMATION",
        "platform_names": "DeinNeuesZuhause",
        "is_foreclosure": True,
    }
    return SimpleNamespace(**{**values, **overrides})


def test_listing_data_without_house_number_leaves_geocoding_open():
    item = listing_data(_prop())
    assert (item.source, item.external_id) == ("thinkimmo", "abc")
    assert item.url == "https://wohnglueck.de/property/1"
    assert item.address == "Moabit, 10555 Berlin"
    assert (item.postcode, item.district) == ("10555", "Moabit")
    assert (item.lat, item.lon) == (None, None)
    assert (item.price, item.living_area, item.rooms, item.year_built) == (330000, 76, 3, 1900)
    assert item.is_new_build is False
    assert item.raw["foreclosure"] is True


def test_listing_data_with_house_number_uses_coordinates():
    item = listing_data(
        _prop(street="Stresemannstraße", house_number="34", condition="FIRST_TIME_USE", url=None)
    )
    assert item.address == "Stresemannstraße 34, Moabit, 10555 Berlin"
    assert (item.lat, item.lon) == (52.52, 13.33)
    assert item.is_new_build is True
    assert item.url == "https://api.thinkimmo.com/immo/abc"


def test_listing_features_joins_thinkimmo():
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "src/immo_scanner/db/alembic/versions/0021_thinkimmo_listings.py"
    )
    spec = spec_from_file_location("ti_migration", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    sql = migration._listing_features_sql()
    assert "SELECT * FROM thinkimmo_properties t WHERE t.listing_id = l.id" in sql
    assert "ti.is_foreclosure" in sql
    assert "ti.price_to_market_pct AS thinkimmo_price_to_market_pct" in sql
    assert sql.count("s.year_built, ti.year_built)") == 2
    assert "COALESCE(s.hausgeld, iw.hausgeld, ti.hausgeld) AS hausgeld" in sql


def test_listing_features_takes_thinkimmo_flags_only_when_true():
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "src/immo_scanner/db/alembic/versions/0022_thinkimmo_yes_only.py"
    )
    spec = spec_from_file_location("ti_yes_migration", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    sql = migration._listing_features_sql()
    for column in ("has_elevator", "has_cellar", "has_balcony", "has_fitted_kitchen"):
        assert f"NULLIF(ti.{column}, false)" in sql
        assert f", ti.{column})" not in sql
    rented = sql[sql.index("    COALESCE(\n        iw.is_rented") : sql.index(" AS is_rented,")]
    assert rented.index("l.title ~* 'vermietet'") < rented.index("NULLIF(ti.is_rented, false)")
    assert "        ti.is_rented,\n" not in sql


def _client(handler) -> ThinkImmoClient:
    settings = Settings(
        _env_file=None,
        crawl_delay_min=0,
        crawl_delay_max=0,
        thinkimmo_search_query="active=true&type=APARTMENTBUY",
        thinkimmo_finance_query="ownCapital=10",
    )
    client = ThinkImmoClient(settings)
    client.client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def test_search_sends_shared_bands_not_the_copied_ones():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(parse_qs(urlsplit(str(request.url)).query))
        return httpx.Response(200, json={"total": 0, "results": []})

    settings = Settings(
        _env_file=None,
        crawl_delay_min=0,
        crawl_delay_max=0,
        thinkimmo_search_query="active=true&buyingPriceTo=600000&sqmTo=150&roomsTo=6",
        thinkimmo_finance_query="ownCapital=10",
        min_rooms=3,
        min_living_area=60,
        min_price=250_000,
        max_price=700_000,
    )
    client = ThinkImmoClient(settings)
    client.client = httpx.Client(transport=httpx.MockTransport(handler))
    client.search(max_pages=1)
    assert seen["buyingPriceFrom"] == ["250000"]
    assert seen["buyingPriceTo"] == ["700000"]
    assert seen["sqmFrom"] == ["60"]
    assert seen["roomsFrom"] == ["3"]
    assert "sqmTo" not in seen
    assert "roomsTo" not in seen
    assert seen["ownCapital"] == ["10"]


def test_search_pages_until_total():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = parse_qs(urlsplit(str(request.url)).query)
        requests.append(query)
        offset = int(query["from"][0])
        count = min(50, 120 - offset)
        results = [{"id": f"id{offset + i}"} for i in range(count)]
        return httpx.Response(200, json={"total": 120, "results": results})

    result = _client(handler).search()
    assert result.complete
    assert result.total == 120
    assert len(result.records) == 120
    assert [q["from"][0] for q in requests] == ["0", "50", "100"]
    assert requests[0]["type"] == ["APARTMENTBUY"]
    assert requests[0]["ownCapital"] == ["10"]
    assert requests[0]["size"] == ["50"]


def test_search_stopped_by_page_limit_is_incomplete():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"total": 500, "results": [{"id": "x"}] * 50})

    result = _client(handler).search(max_pages=2)
    assert not result.complete
    assert len(result.records) == 1


def test_empty_first_page_is_incomplete():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"total": 0, "results": []})

    assert not _client(handler).search().complete


def test_unknown_property_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/immo/missing"
        return httpx.Response(200, content=b"")

    assert _client(handler).property("missing") is None
