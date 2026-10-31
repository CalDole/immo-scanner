from urllib.parse import parse_qsl

from immo_scanner.config import Settings
from immo_scanner.crawlers.base import ListingData
from immo_scanner.crawlers.criteria import Criteria

CRITERIA = Criteria(min_rooms=3, min_living_area=65, min_price=250_000, max_price=700_000)


def listing(**values) -> ListingData:
    return ListingData(source="test", external_id="1", url="https://example.com", **values)


def test_matches_single_values():
    assert CRITERIA.matches(listing(price=400_000, living_area=80, rooms=3))
    assert not CRITERIA.matches(listing(price=800_000, living_area=80, rooms=3))
    assert not CRITERIA.matches(listing(price=200_000, living_area=80, rooms=3))
    assert not CRITERIA.matches(listing(price=400_000, living_area=60, rooms=3))
    assert not CRITERIA.matches(listing(price=400_000, living_area=80, rooms=2.5))


def test_unknown_values_pass():
    assert CRITERIA.matches(listing())
    assert Criteria().matches(listing(price=1, living_area=1, rooms=1))


def test_exclude_rented_by_title():
    rented = listing(title="Kapitalanlage: vermietete 3-Zimmer-Wohnung")
    assert CRITERIA.matches(rented)
    criteria = Criteria(exclude_rented=True)
    assert not criteria.matches(rented)
    assert not criteria.matches(listing(title="Altbau, VERMIETET"))
    assert criteria.matches(listing(title="Bezugsfrei & unvermietet"))
    assert criteria.matches(listing(title="Helle Wohnung, nicht vermietet"))
    assert criteria.matches(listing(title=None))


def test_matches_project_ranges():
    too_small = listing(
        price=222_500,
        living_area=33,
        rooms=1,
        raw={"price_range": [222_500, 741_000], "area_range": [33, 101], "rooms_range": [1, 4]},
    )
    assert not CRITERIA.matches(too_small)
    fitting = listing(
        price=400_000,
        living_area=70,
        rooms=3,
        raw={"price_range": [400_000, 650_000], "area_range": [70, 110], "rooms_range": [3, 5]},
    )
    assert CRITERIA.matches(fitting)
    too_expensive = listing(
        price=500_000,
        living_area=80,
        rooms=3,
        raw={"price_range": [500_000, 800_000], "area_range": [80, 120], "rooms_range": [3, 4]},
    )
    assert not CRITERIA.matches(too_expensive)
    small = listing(
        price=200_000,
        raw={"price_range": [200_000, 240_000], "area_range": [25, 50], "rooms_range": [1, 2]},
    )
    assert not CRITERIA.matches(small)


def test_immoscout24_url():
    base = "https://www.immobilienscout24.de/Suche/de/berlin/berlin/wohnung-kaufen"
    assert CRITERIA.immoscout24_url(base) == (
        base + "?numberofrooms=3.0-&livingspace=65.0-&price=250000.0-700000.0"
    )
    assert Criteria(max_price=500_000).immoscout24_url(base) == base + "?price=-500000.0"
    assert CRITERIA.immoscout24_url(base + "?price=1-2").count("price=") == 1
    assert Criteria().immoscout24_url(base) == base
    assert Criteria(exclude_rented=True).immoscout24_url(base) == base + "?rented=false"


def test_kleinanzeigen_url():
    base = "https://www.kleinanzeigen.de/s-wohnung-kaufen/berlin/anzeige:angebote/c196l3331"
    assert CRITERIA.kleinanzeigen_url(base) == (
        "https://www.kleinanzeigen.de/s-wohnung-kaufen/berlin/anzeige:angebote/"
        "preis:250000:700000/c196l3331+wohnung_kaufen.qm_d:65%2C+wohnung_kaufen.zimmer_d:3%2C"
    )
    assert Criteria(max_price=500_000).kleinanzeigen_url(base).endswith("/preis::500000/c196l3331")
    assert Criteria().kleinanzeigen_url(base) == base


def test_immowelt_url():
    base = (
        "https://www.immowelt.de/classified-search?distributionTypes=Buy"
        "&estateTypes=Apartment&locations=AD08DE8634&order=DateDesc"
    )
    params = dict(parse_qsl(CRITERIA.immowelt_url(base).split("?")[1]))
    assert params == {
        "distributionTypes": "Buy",
        "estateTypes": "Apartment",
        "locations": "AD08DE8634",
        "order": "DateDesc",
        "numberOfRoomsMin": "3",
        "spaceMin": "65",
        "priceMin": "250000",
        "priceMax": "700000",
    }
    assert "numberOfRoomsMin=2.5" in Criteria(min_rooms=2.5).immowelt_url(base)
    assert "priceMax=500000" in Criteria(max_price=700_000).immowelt_url(base + "&priceMax=500000")
    assert Criteria().immowelt_url(base) == base


def test_exclude_rented_by_card_flag():
    rented = listing(title="Helle 3-Zimmer-Wohnung", raw={"rented": True})
    assert CRITERIA.matches(rented)
    assert not Criteria(exclude_rented=True).matches(rented)


def test_bsk_url():
    base = (
        "https://www.bsk-immobilien.de/kaufen/suche/mitte-oder-pankow--etage-oder-maisonette"
        "--neubau-egal--barrierefrei-egal--vermietet-egal--kaufpreis-bis-750000"
        "--wohnflaeche-ab-60--zimmer-ab-3--alle-ids--entry_date--desc"
    )
    assert CRITERIA.bsk_url(base) == base.replace(
        "kaufpreis-bis-750000--wohnflaeche-ab-60--zimmer-ab-3",
        "kaufpreis-bis-700000--wohnflaeche-ab-65--zimmer-ab-3",
    )
    assert Criteria().bsk_url(base) == base.replace(
        "kaufpreis-bis-750000--wohnflaeche-ab-60--zimmer-ab-3",
        "beliebiger-kaufpreis--belibiege-wohnflaeche--belibige-zimmeranzahl",
    )
    unlimited = Criteria().bsk_url(base)
    assert Criteria(min_rooms=2.5, max_price=500_000).bsk_url(unlimited) == base.replace(
        "kaufpreis-bis-750000--wohnflaeche-ab-60--zimmer-ab-3",
        "kaufpreis-bis-500000--belibiege-wohnflaeche--zimmer-ab-2.5",
    )
    assert CRITERIA.bsk_url("https://example.com/x") == "https://example.com/x"


def test_bsk_default_url_matches_its_own_criteria():
    raw = Settings.model_fields["bsk_search_url"].default
    assert Criteria(min_rooms=3, min_living_area=60, max_price=750_000).bsk_url(raw) == raw


EV_BASE = "https://www.engelvoelkers.com/de/de/propertysearch"


def test_engelvoelkers_url_sets_bands_and_keeps_bare_keys():
    url = (
        f"{EV_BASE}?businessArea[]=residential&page=3&placeName=Berlin%2C%20Deutschland"
        "&price.max=900000&propertyTypeSubType.apartment[]&rooms.min=2&sortingOptionId=SMART_SORT"
    )
    assert CRITERIA.engelvoelkers_url(url) == (
        f"{EV_BASE}?businessArea[]=residential&placeName=Berlin%2C%20Deutschland"
        "&price.max=700000&propertyTypeSubType.apartment[]&rooms.min=3"
        "&sortingOptionId=SMART_SORT&price.min=250000&totalSurface.min=65"
    )


def test_engelvoelkers_url_drops_unset_bands():
    url = f"{EV_BASE}?price.min=1&price.max=2&rooms.min=3&totalSurface.min=4&currency=EUR"
    assert Criteria().engelvoelkers_url(url) == f"{EV_BASE}?currency=EUR"


def test_engelvoelkers_default_url_matches_its_own_criteria():
    raw = Settings.model_fields["engelvoelkers_search_url"].default
    criteria = Criteria(min_rooms=3, min_living_area=60, max_price=700_000)
    assert criteria.engelvoelkers_url(raw) == raw


def test_thinkimmo_query_uses_the_shared_bands():
    copied = (
        "active=true&buyingPriceTo=600000&sqmFrom=65&sqmTo=150"
        "&roomsFrom=3&roomsTo=6&rented=false"
    )
    params = dict(
        parse_qsl(
            Criteria(
                min_rooms=3, min_living_area=60, min_price=250_000, max_price=700_000
            ).thinkimmo_query(copied)
        )
    )
    assert params == {
        "active": "true",
        "rented": "false",
        "buyingPriceFrom": "250000",
        "buyingPriceTo": "700000",
        "sqmFrom": "60",
        "roomsFrom": "3",
    }


def test_thinkimmo_query_omits_unset_bands():
    params = dict(parse_qsl(Criteria().thinkimmo_query("active=true&sqmTo=150&roomsTo=6")))
    assert params == {"active": "true"}


def test_thinkimmo_default_query_has_no_own_bands():
    raw = Settings.model_fields["thinkimmo_search_query"].default
    keys = {key for key, _ in parse_qsl(raw)}
    assert keys.isdisjoint(
        {"buyingPriceFrom", "buyingPriceTo", "sqmFrom", "sqmTo", "roomsFrom", "roomsTo"}
    )
    assert "rented" in keys
