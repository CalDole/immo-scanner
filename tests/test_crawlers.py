"""Parser tests against trimmed copies of real result-page markup (October 2026)."""

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from immo_scanner.config import Settings
from immo_scanner.crawlers import (
    bsk,
    engelvoelkers,
    immoscout24,
    immowelt,
    kleinanzeigen,
    neubaukompass,
)
from immo_scanner.crawlers.base import BlockedError

KLEINANZEIGEN_HTML = """
<ul>
<li data-clickable="card">
<article class="flex justify-between p-medium" data-adid="3502463334"
  data-href="/s-anzeige/kreuzberg-unter-500k-sanierte-3-zimmer-wohnung/3502463334-196-3378">
  <div><script type="application/ld+json">{"title": "ignored 999.000 €"}</script></div>
  <div class="z-raised">
    <div><div><svg data-title="locationOutline"></svg><span>10965 Kreuzberg</span></div></div>
    <div class="flex flex-col">
      <h3><a href="/s-anzeige/x/3502463334-196-3378" name="3502463334">Kreuzberg unter 500k –
        sanierte 3-Zimmer-Wohnung mit privatem Garten</a></h3>
      <p>Auf ca. 61 m² Wohnfläche ... Kaufpreis 480.000 € verhandelbar</p>
      <p class="font-strong">61 m² · 3 Zi.</p>
      <div class="flex"><p class="my-xsmall">499.000 €</p></div>
    </div>
  </div>
</article>
</li>
</ul>
<a href="/s-wohnung-kaufen/berlin/anzeige:angebote/seite:2/c196l3331" aria-label="Nächste">›</a>
"""

IMMOWELT_HTML = """
<div data-testid="serp-core-classified-card-testid">
  <a href="https://www.immowelt.de/expose/884bed59-77fb-4db2-aea5-202a1157f076"
     title="Wohnung zum Kauf - Berlin - 449.901€ - 3 Zimmer, 78,4 m², 3. Geschoss"
     data-testid="card-mfe-covering-link-testid"></a>
  <div data-testid="cardmfe-price-testid" aria-label="449901 €">449.901€ <span>5.740€/m²</span></div>
  <div data-testid="cardmfe-keyfacts-testid">
    <div>3 Zimmer</div><div>·</div><div>78,4 m²</div><div>·</div><div>3. Geschoss</div>
  </div>
  <div data-testid="cardmfe-description-box-address">Steglitz, Berlin (12163)</div>
  <span data-testid="cardmfe-tag-testid">Neubau</span>
</div>
<nav aria-label="pagination navigation">
  <button aria-label="aktuelle seite, seite 1">1</button>
  <button aria-label="zu seite 2">2</button>
  <button aria-label="zu seite 307">307</button>
</nav>
"""

NEUBAUKOMPASS_HTML = """
<div class="list">
  <div class="card">
    <a href="/neubau/baumschulenstrasse-27-29-berlin/"><img/></a>
    <h3>Baumschulenstraße 27/29</h3>
    <p>Neubau von 23 Wohneinheiten Baumschulenstraße 27/29, 12437 Berlin</p>
    <ul>
      <li>Preis <span>222.500 - 741.000 €</span></li>
      <li>Wohnfläche <span>33 - 101 m²</span></li>
      <li>Zimmer <span>1 - 4</span></li>
      <li>Bezugsfertig <span>07/2026</span></li>
    </ul>
  </div>
  <div class="card">
    <a href="/neubau/haus-11-berlin/"><img/></a>
    <h3>HAUS°11</h3>
    <p>Neubau von 9 Wohneinheiten Schönower Straße 11, 14165 Berlin</p>
    <ul>
      <li>Preis <span>1.180.745 - 1.935.662 €</span></li>
      <li>Wohnfläche <span>124 - 193 m²</span></li>
      <li>Zimmer <span>4 - 5</span></li>
      <li>Bezugsfertig <span>sofort</span></li>
    </ul>
  </div>
</div>
"""


def test_kleinanzeigen_parser():
    listings, has_next = kleinanzeigen.parse_search_page(KLEINANZEIGEN_HTML)
    assert has_next
    assert len(listings) == 1
    item = listings[0]
    assert item.external_id == "3502463334"
    assert item.url.startswith("https://www.kleinanzeigen.de/s-anzeige/")
    assert item.price == 499_000
    assert item.living_area == 61
    assert item.rooms == 3
    assert item.postcode == "10965"
    assert item.district == "Kreuzberg"


def test_kleinanzeigen_filtered_page_url():
    url = (
        "https://www.kleinanzeigen.de/s-wohnung-kaufen/berlin/anzeige:angebote/"
        "preis::700000/c196l3331+wohnung_kaufen.qm_d:60%2C+wohnung_kaufen.zimmer_d:3%2C"
    )
    assert kleinanzeigen.page_url(url, 2) == (
        "https://www.kleinanzeigen.de/s-wohnung-kaufen/berlin/anzeige:angebote/"
        "preis::700000/seite:2/c196l3331+wohnung_kaufen.qm_d:60%2C+wohnung_kaufen.zimmer_d:3%2C"
    )


def test_kleinanzeigen_page_url():
    url = "https://www.kleinanzeigen.de/s-wohnung-kaufen/berlin/anzeige:angebote/c196l3331"
    assert kleinanzeigen.page_url(url, 1) == url
    assert kleinanzeigen.page_url(url, 3) == (
        "https://www.kleinanzeigen.de/s-wohnung-kaufen/berlin/anzeige:angebote/seite:3/c196l3331"
    )


def test_immowelt_parser():
    listings, last_page = immowelt.parse_search_page(IMMOWELT_HTML)
    assert last_page == 307
    item = listings[0]
    assert item.external_id == "884bed59-77fb-4db2-aea5-202a1157f076"
    assert item.price == 449_901
    assert item.rooms == 3
    assert item.living_area == 78.4
    assert item.floor == 3
    assert item.postcode == "12163"
    assert item.district == "Steglitz"
    assert item.is_new_build


def test_immowelt_district():
    assert immowelt._split_address("Treptow-Köpenick (12557)") == "Treptow-Köpenick"
    assert immowelt._split_address("Musterstr. 1, Moabit, Berlin (10557)") == "Moabit"


def test_immowelt_last_page_counts_the_current_page():
    html = """
    <nav aria-label="pagination navigation" data-testid="serp-pagination-testid"><ul>
      <li><button aria-label="zu seite 1">1</button></li>
      <li><button aria-label="zu seite 81">81</button></li>
      <li><button aria-label="aktuelle seite, seite 82" aria-current="page">82</button></li>
    </ul></nav>
    """
    assert immowelt.parse_search_page(html) == ([], 82)


def test_immowelt_page_url():
    url = "https://www.immowelt.de/classified-search?locations=AD08DE8634&priceMax=700000"
    assert immowelt.page_url(url, 1) == url
    assert immowelt.page_url(url, 3) == url + "&page=3"
    assert immowelt.page_url(url + "&page=3", 4) == url + "&page=4"


def test_immowelt_blocked():
    captcha = '<iframe src="https://geo.captcha-delivery.com/captcha/?x=1"></iframe>'
    assert immowelt.is_blocked(captcha)
    assert not immowelt.is_blocked(IMMOWELT_HTML + captcha)
    assert not immowelt.is_blocked("<p>Keine Ergebnisse</p>")


def _immowelt_page(page: int, last: int) -> str:
    card = f"""
    <div data-testid="serp-core-classified-card-testid">
      <a data-testid="card-mfe-covering-link-testid" href="https://www.immowelt.de/expose/id-{page}"></a>
    </div>"""
    links = "".join(f'<button aria-label="zu seite {n}">{n}</button>' for n in range(1, last + 1) if n != page)
    return f'{card}<nav><button aria-label="aktuelle seite, seite {page}">{page}</button>{links}</nav>'


class FakeImmoweltBrowser:
    def __init__(self, pages: dict[int, str]) -> None:
        self.pages = pages
        self.visited: list[int] = []
        self.page = self

    def __call__(self, settings, profile_name):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def goto(self, url: str, wait_for: str | None = None) -> str:
        page = int(parse_qs(urlsplit(url).query).get("page", ["1"])[0])
        self.visited.append(page)
        self.html = self.pages.get(page, "")
        return self.html

    def content(self) -> str:
        return self.html

    def wait_for_human(self, check, message: str) -> bool:
        return check()

    def wait_for_timeout(self, ms: int) -> None:
        pass


def _crawl_immowelt(monkeypatch, pages: dict[int, str], max_pages: int):
    browser = FakeImmoweltBrowser(pages)
    monkeypatch.setattr(immowelt, "Browser", browser)
    settings = Settings(_env_file=None, min_rooms=3)
    with immowelt.ImmoweltCrawler(settings) as crawler:
        return crawler.crawl(max_pages=max_pages), browser.visited


def test_immowelt_crawl_reaches_last_page(monkeypatch):
    pages = {n: _immowelt_page(n, 3) for n in (1, 2, 3)}
    result, visited = _crawl_immowelt(monkeypatch, pages, max_pages=10)
    assert result.complete
    assert visited == [1, 2, 3]
    assert [item.external_id for item in result.listings] == ["id-1", "id-2", "id-3"]


def test_immowelt_crawl_stopped_by_page_limit_is_incomplete(monkeypatch):
    pages = {n: _immowelt_page(n, 3) for n in (1, 2, 3)}
    result, visited = _crawl_immowelt(monkeypatch, pages, max_pages=2)
    assert not result.complete
    assert visited == [1, 2]


def test_immowelt_crawl_blocked_keeps_partial_results(monkeypatch):
    pages = {1: _immowelt_page(1, 3), 2: '<script src="https://captcha-delivery.com/c.js"></script>'}
    result, visited = _crawl_immowelt(monkeypatch, pages, max_pages=10)
    assert not result.complete
    assert visited == [1, 2]
    assert [item.external_id for item in result.listings] == ["id-1"]


def test_immowelt_crawl_blocked_on_first_page_raises(monkeypatch):
    pages = {1: '<script src="https://captcha-delivery.com/c.js"></script>'}
    with pytest.raises(BlockedError):
        _crawl_immowelt(monkeypatch, pages, max_pages=10)


def test_neubaukompass_parser():
    listings = neubaukompass.parse_projects(NEUBAUKOMPASS_HTML, "https://www.neubaukompass.de/x/")
    by_id = {item.external_id: item for item in listings}
    assert set(by_id) == {"baumschulenstrasse-27-29-berlin", "haus-11-berlin"}
    item = by_id["baumschulenstrasse-27-29-berlin"]
    assert item.price == 222_500
    assert item.living_area == 33
    assert item.rooms == 1
    assert item.postcode == "12437"
    assert item.address == "Baumschulenstraße 27/29, 12437 Berlin"
    assert item.raw["units"] == 23
    assert item.raw["price_range"] == [222_500, 741_000]
    assert item.raw["ready"] == "07/2026"
    assert by_id["haus-11-berlin"].price == 1_180_745


def test_immoscout24_json_parser():
    payload = {
        "searchResponseModel": {
            "resultlist.resultlist": {
                "paging": {"pageNumber": 1, "numberOfPages": 12},
                "resultlistEntries": [
                    {
                        "resultlistEntry": [
                            {
                                "@id": "151234567",
                                "resultlist.realEstate": {
                                    "@xsi.type": "search:ApartmentBuy",
                                    "title": "Altbau mit Balkon",
                                    "address": {
                                        "street": "Musterstraße",
                                        "houseNumber": "5",
                                        "postcode": "10437",
                                        "city": "Berlin",
                                        "quarter": "Prenzlauer Berg (Prenzlauer Berg)",
                                        "wgs84Coordinate": {"latitude": 52.54, "longitude": 13.41},
                                    },
                                    "price": {"value": 520000.0, "currency": "EUR"},
                                    "livingSpace": 72.5,
                                    "numberOfRooms": 2.5,
                                },
                            }
                        ]
                    }
                ],
            }
        }
    }
    listings, pages = immoscout24.parse_json_results(payload)
    assert pages == 12
    item = listings[0]
    assert item.external_id == "151234567"
    assert item.url == "https://www.immobilienscout24.de/expose/151234567"
    assert item.price == 520_000
    assert item.living_area == 72.5
    assert item.rooms == 2.5
    assert (item.lat, item.lon) == (52.54, 13.41)
    assert item.address == "Musterstraße 5, 10437 Berlin"


def _bsk_card(expose: str, tagline: str, geo: str, title: str, rows: str) -> str:
    return f"""
<div class="col" data-objektid="1">
<div class="immo-entry   shadow h-100">
  <div role="button" data-href="https://www.bsk-immobilien.de//expose/{expose}"></div>
  <div class="d-flex flex-column">
    <div class="immo-cat d-flex">
      <div class="tagline fw-bold bg-bestand"><div>{tagline}</div></div>
      <div class="geo d-flex"><div><span class="fw-bold d-block">Berlin</span>
        <span class="d-block small">{geo}</span></div></div>
    </div>
    <div class="immo-data p-4">
      <h4 class="immo-title font-body fw-normal">{title}</h4>
      <table class="table">{rows}</table>
      <a href="/expose/{expose}" class="btn">Zum Objekt</a>
    </div>
  </div>
</div>
</div>"""


BSK_HTML = (
    _bsk_card(
        "31171-familienfreundliche-dachgeschosswohnung",
        "DG-Wohnung",
        "Steglitz-Zehlendorf / Zehlendorf (OT)",
        "Familienfreundliche Dachgeschosswohnung",
        "<tr><td>Wohnfläche:</td><th> 105 m<sup>2</sup></th></tr>"
        "<tr><td>Zimmer:</td><th>4,0</th></tr>"
        "<tr><td>Kaufpreis:</td><th>€ 579.000</th></tr>",
    )
    + _bsk_card(
        "30477-vermietete-3-zimmer-wohnung",
        "Etagenwohnung",
        "Marzahn-Hellersdorf",
        "Vermietete 3-Zimmer-Wohnung",
        "<tr><td>Wohnfläche:</td><th>85 m<sup>2</sup></th></tr>"
        "<tr><td>Zimmer:</td><th>3,0</th></tr>"
        "<tr><td>Vermietet:</td><th>Ja</th></tr>"
        "<tr><td>Kaufpreis:</td><th>€ 345.000</th></tr>",
    )
    # BSK lists some flats twice on one page.
    + _bsk_card(
        "31171-familienfreundliche-dachgeschosswohnung",
        "DG-Wohnung",
        "Steglitz-Zehlendorf / Zehlendorf (OT)",
        "Familienfreundliche Dachgeschosswohnung",
        "<tr><td>Kaufpreis:</td><th>€ 579.000</th></tr>",
    )
    + """
<div class="col">
<div class="immo-entry projekt  shadow h-100">
  <div role="button" data-href="https://www.bsk-immobilien.de//expose/steueroptimiertes-investment"></div>
  <div class="immo-cat d-flex">
    <div class="tagline fw-bold bg-green"><div>Neubau-Projekt</div></div>
    <div class="geo"><span class="d-block small">Steglitz-Zehlendorf / Lichterfelde</span></div>
  </div>
  <div class="immo-data p-4">
    <h4 class="immo-title">Steueroptimiertes Investment</h4>
    <table class="table">
      <tr><td>Wohnfläche:</td><th>ab32 m2</th></tr>
      <tr><th>Objekttyp</th><th>Fläche</th><th>Zimmer</th><th>Kaufpreis</th><th></th></tr>
      <tr><td data-id="29745-GE1">Büro-/Praxisfläche</td><td>232 m<sup>2</sup></td><td>2,0</td>
        <td>€ 1.042.020</td><td><a href="/expose/29745-ge1-gewerbeeinheit">Details</a></td></tr>
      <tr><td data-id="29745-40">EG-Wohnung</td><td>48 m<sup>2</sup></td><td>2,0</td>
        <td>€ 403.546</td><td><a href="/expose/29745-40-moderne-2-zimmer-wohnung">Details</a></td></tr>
      <tr><td data-id="29745-12">Etagenwohnung</td><td>36 m<sup>2</sup></td><td>1,0</td>
        <td>€ 325.592</td><td>verkauft</td></tr>
    </table>
  </div>
</div>
</div>
<a href="https://www.bsk-immobilien.de/kaufen/suche/mitte--etage--entry_date--desc/P15">2</a>
"""
)


def test_bsk_parser():
    listings, has_next = bsk.parse_search_page(BSK_HTML)
    assert has_next
    by_id = {item.external_id: item for item in listings}
    assert list(by_id) == ["31171", "30477", "29745-40"]

    flat = by_id["31171"]
    assert flat.url == "https://www.bsk-immobilien.de/expose/31171-familienfreundliche-dachgeschosswohnung"
    assert flat.title == "Familienfreundliche Dachgeschosswohnung"
    assert (flat.price, flat.living_area, flat.rooms) == (579_000, 105, 4)
    assert flat.district == "Zehlendorf"
    assert flat.raw["bezirk"] == "Steglitz-Zehlendorf"
    assert flat.raw["kind"] == "DG-Wohnung"
    assert flat.raw["rented"] is None
    assert not flat.is_new_build

    rented = by_id["30477"]
    assert rented.raw["rented"] is True
    assert rented.district == "Marzahn-Hellersdorf"

    unit = by_id["29745-40"]
    assert unit.url == "https://www.bsk-immobilien.de/expose/29745-40-moderne-2-zimmer-wohnung"
    assert (unit.price, unit.living_area, unit.rooms) == (403_546, 48, 2)
    assert unit.is_new_build
    assert unit.district == "Lichterfelde"
    assert unit.title == "EG-Wohnung – Steueroptimiertes Investment"


def test_bsk_last_page_has_no_next_link():
    assert bsk.parse_search_page(BSK_HTML, offset=15)[1] is False


def test_bsk_page_url():
    url = "https://www.bsk-immobilien.de/kaufen/suche/mitte--etage--entry_date--desc"
    assert bsk.page_url(url, 1) == url
    assert bsk.page_url(url, 2) == url + "/P15"
    assert bsk.page_url(url, 4) == url + "/P45"
    assert bsk.page_url(url + "/P15", 3) == url + "/P30"


def test_bsk_crawl_stops_on_last_page(monkeypatch):
    pages = {1: BSK_HTML, 2: BSK_HTML.replace("/P15", "/P0")}
    visited = []

    def fetch(self, url, **params):
        page = 2 if url.endswith("/P15") else 1
        visited.append(page)
        return pages[page]

    monkeypatch.setattr(bsk.BskCrawler, "fetch", fetch)
    with bsk.BskCrawler(Settings(_env_file=None)) as crawler:
        result = crawler.crawl(max_pages=10)
    assert visited == [1, 2]
    assert result.complete
    assert len(result.listings) == 3


EV_FIXTURE = Path(__file__).parent / "fixtures" / "ev_search.html"
BERLIN = [
    {"placeType": "country", "text": "Deutschland"},
    {"placeType": "administrative_area_level_1", "text": "Berlin"},
    {"placeType": "sublocality_level_1", "text": "Pankow"},
]
POTSDAM = [
    {"placeType": "country", "text": "Deutschland"},
    {"placeType": "administrative_area_level_1", "text": "Brandenburg"},
    {"placeType": "locality", "text": "Potsdam"},
]


def _ev_item(listing_id: str, address=BERLIN, **extra) -> dict:
    return {
        "id": listing_id,
        "displayId": f"W-{listing_id}",
        "profile": {"title": f"Wohnung {listing_id}"},
        "area": {"livingSurface": {"min": 80, "max": 80}},
        "price": {"salesPrice": {"min": 500_000, "max": 500_000}},
        "rooms": {"min": 3, "max": 3},
        "addressComponents": address,
        "privacyLevel": "PUBLIC",
        **extra,
    }


def _ev_page(items: list[dict], total: int, page_size: int = 2) -> str:
    data = {
        "props": {
            "pageProps": {
                "initialOptions": {"pageSize": page_size},
                "dehydratedState": {
                    "queries": [
                        {
                            "state": {
                                "data": {
                                    "listings": [{"listing": item} for item in items],
                                    "listingsTotal": total,
                                }
                            }
                        }
                    ]
                },
            }
        }
    }
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'


def test_engelvoelkers_search_fixture():
    listings, ids, total, page_size = engelvoelkers.parse_search_page(
        EV_FIXTURE.read_text(encoding="utf-8")
    )
    assert (len(ids), total, page_size) == (23, 127, 23)
    first = listings[0]
    assert first.source == "engelvoelkers"
    assert first.external_id == "f841344b-775b-51ea-bfbb-309ceb43079f"
    assert first.url == (
        "https://www.engelvoelkers.com/de/de/exposes/f841344b-775b-51ea-bfbb-309ceb43079f"
    )
    assert (first.price, first.living_area, first.rooms) == (698_000, 105, 4)
    assert first.district == "Mitte"
    assert first.raw["display_id"] == "W-0462Z1"
    assert first.raw["berlin"] is True


def test_engelvoelkers_drops_fallback_and_restricted_results():
    html = _ev_page(
        [
            _ev_item("a"),
            _ev_item("b", address=POTSDAM),
            _ev_item("c", hasPriceOnRequest=True),
            _ev_item("d", price={"salesPrice": {"min": 400_000, "max": 650_000}}),
        ],
        total=4,
    )
    listings, ids, _, _ = engelvoelkers.parse_search_page(html)
    assert ids == ["a", "b", "c", "d"]
    assert [item.external_id for item in listings] == ["a", "d"]
    assert listings[1].price == 400_000
    assert listings[1].raw["price_range"] == [400_000, 650_000]


def test_engelvoelkers_page_url():
    url = "https://www.engelvoelkers.com/de/de/propertysearch?a[]=x&page=4&b.c[]"
    assert engelvoelkers.page_url(url, 2) == (
        "https://www.engelvoelkers.com/de/de/propertysearch?a[]=x&b.c[]&page=2"
    )


def _ev_crawl(monkeypatch, pages: dict[int, str]) -> tuple[list[int], object]:
    visited = []

    def fetch(self, url, **params):
        page = int(parse_qs(urlsplit(url).query)["page"][0])
        visited.append(page)
        return pages[min(page, max(pages))]

    monkeypatch.setattr(engelvoelkers.EngelVoelkersCrawler, "fetch", fetch)
    with engelvoelkers.EngelVoelkersCrawler(Settings(_env_file=None)) as crawler:
        return visited, crawler.crawl(max_pages=10)


def test_engelvoelkers_crawl_stops_on_total(monkeypatch):
    pages = {
        1: _ev_page([_ev_item("a"), _ev_item("b")], total=3),
        2: _ev_page([_ev_item("c")], total=3),
    }
    visited, result = _ev_crawl(monkeypatch, pages)
    assert visited == [1, 2]
    assert result.complete
    assert [item.external_id for item in result.listings] == ["a", "b", "c"]


def test_engelvoelkers_crawl_stops_on_repeated_page(monkeypatch):
    # A wrong total: page 3 repeats page 2, as E&V does past the last page.
    pages = {
        1: _ev_page([_ev_item("a"), _ev_item("b")], total=99),
        2: _ev_page([_ev_item("c")], total=99),
    }
    visited, result = _ev_crawl(monkeypatch, pages)
    assert visited == [1, 2, 3]
    assert result.complete
    assert len(result.listings) == 3
