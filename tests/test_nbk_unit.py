from selectolax.parser import HTMLParser

from immo_scanner.config import Settings
from immo_scanner.crawlers.base import ListingData
from immo_scanner.crawlers.neubaukompass import parse_unit_cards, project_address
from immo_scanner.details.neubaukompass import MediaItem, _filename, _floor, _media_url, parse_unit
from immo_scanner.details.portals import parse_neubaukompass

PROJECT_HTML = """
<html><body>
<ul><li data-scout-id=""><div><svg><path d="M1"/></svg></div>
  <div><span>Adresse</span><span>Baumschulenstr. 27-29, 12437 Berlin</span></div></li></ul>
<div class="nbk-project-residential-units">
  <a class="nbk-project-residential-units__item"
     href="/neubau/baumschulenstrasse-27-29-berlin/127686/expose-421893/">
    <span class="nbk-project-residential-units__item-teaser">Charmante 1-Zimmer-Wohnung</span>
    <div class="nbk-project-residential-units__item-feature">
      <span class="nbk-project-residential-units__item-feature-label">Preis</span>
      <span class="nbk-project-residential-units__item-feature-value">222.507 &euro;</span></div>
    <div class="nbk-project-residential-units__item-feature">
      <span class="nbk-project-residential-units__item-feature-label">Wohnfl&auml;che</span>
      <span class="nbk-project-residential-units__item-feature-value">33,21 m&sup2;</span></div>
    <div class="nbk-project-residential-units__item-feature">
      <span class="nbk-project-residential-units__item-feature-label">Zimmer</span>
      <span class="nbk-project-residential-units__item-feature-value">1 Zi.</span></div>
  </a>
  <a class="nbk-project-residential-units__item"
     href="/neubau/baumschulenstrasse-27-29-berlin/127686/expose-421894/">
    <span class="nbk-project-residential-units__item-teaser">Gro&szlig;z&uuml;gige Maisonette</span>
    <div class="nbk-project-residential-units__item-feature">
      <span class="nbk-project-residential-units__item-feature-label">Preis</span>
      <span class="nbk-project-residential-units__item-feature-value">700.177 &euro;</span></div>
    <div class="nbk-project-residential-units__item-feature">
      <span class="nbk-project-residential-units__item-feature-label">Wohnfl&auml;che</span>
      <span class="nbk-project-residential-units__item-feature-value">126,83 m&sup2;</span></div>
    <div class="nbk-project-residential-units__item-feature">
      <span class="nbk-project-residential-units__item-feature-label">Zimmer</span>
      <span class="nbk-project-residential-units__item-feature-value">4 Zi.</span></div>
  </a>
  <a class="nbk-project-residential-units__item" href="/neubau/anderes-projekt/99.html">Projekt</a>
</div>
</body></html>
"""

PROJECT = ListingData(
    source="neubaukompass",
    external_id="127686.html",
    url="https://www.neubaukompass.de/neubau/baumschulenstrasse-27-29-berlin/127686.html",
    title="Baumschulenstra&szlig;e 27-29",
    address="Baumschulenstr. 27, Berlin",
    raw={"ready": "2026"},
)


def _detail(scout_id: str, label: str, value: str) -> str:
    return (
        f'<li data-scout-id="{scout_id}"><div><svg><path d="M1"/></svg></div>'
        f"<div><span>{label}</span><span>{value}</span></div></li>"
    )


UNIT_HTML = (
    """
<html><body>
<h1>Gro&szlig;z&uuml;gige Maisonette-Wohnung mit Dachterrasse</h1>
<a href="/neubau/baumschulenstrasse-27-29-berlin/127686.html">Baumschulenstra&szlig;e 27-29</a>
<section id="details"><ul>
"""
    + _detail("", "Adresse", "Baumschulenstr. 27-29, 12437 Berlin")
    + _detail("unitType", "Objekttyp", "Maisonette")
    + _detail("purchasePrice", "Preis", "700.177 &euro;")
    + _detail("calculatedPricePerSqm", "Preis/m&sup2;", "5.521 &euro;")
    + _detail("floor", "Etage", "4")
    + _detail("numberOfFloors", "Etagenzahl", "5")
    + _detail("numberOfRooms", "Zimmeranzahl", "4")
    + _detail("numberOfBedRooms", "Schlafzimmer", "2")
    + _detail("numberOfBathRooms", "Badezimmer", "2")
    + _detail("livingSpace", "Wohnfl&auml;che", "126,83 m&sup2;")
    + _detail("lastRefurbishment", "Letzte Modernisierung", "2025")
    + _detail("freeFrom", "Bezugsfertig ab", "01.07.2026")
    + _detail("hasCourtage", "Provision", "Ja")
    + _detail("", "Provision f&uuml;r K&auml;ufer", "3,57% inkl. MwSt.")
    + _detail("", "Provision f&uuml;r Verk&auml;ufer", "3,57% inkl. MwSt.")
    + _detail("unitId", "ID", "421894")
    + _detail("realEstate.objectNumber", "Objektnummer", "BS-27-W12")
    + """
</ul></section>
<section id="merkmale"><ul><li>Dachterrasse</li><li>Aufzug</li><li>Fu&szlig;bodenheizung</li></ul></section>
<section id="bilder">
  <a data-src="https://www.neubaukompass.de/pictures/listings/a-1.jpg/ORIG/resize/950x1200%3E/format/jpg">
    <img src="https://www.neubaukompass.de/pictures/listings/a-1.jpg/ORIG/resize/300x200%3E/format/jpg"></a>
  <a data-src="https://www.neubaukompass.de/pictures/listings/a-2.jpg/ORIG/resize/950x1200%3E/format/jpg"></a>
  <img src="https://www.neubaukompass.de/assets/logo.svg">
</section>
<section id="energienachweis">
  Energieausweistyp : Bedarfsausweis <br>
  Wesentlicher Energietr&auml;ger : Keine Angabe <br>
  Erstellungsdatum: ab dem 01.05.2014 <br>
  <div data-components--molecules--energy--energy-energy-efficiency-class-value="A+"
       data-components--molecules--energy--energy-energy-demand-value="28,4"></div>
</section>
<section id="lage"><h2>Lage</h2><p>Ruhige Seitenstra&szlig;e in Baumschulenweg.</p></section>
<section id="ausstattungimueberblick"><h2>Ausstattung im &Uuml;berblick</h2>
  <p>Eichenparkett, Fu&szlig;bodenheizung.</p><button>Mehr</button></section>
<section id="beschreibung"><h2>Beschreibung</h2><p>Baujahr 1905, kernsaniert.</p></section>
<section id="dokumenteundlinks">
  <h3>Grundriss</h3><a href="https://cdn.example.com/grundriss.pdf">Grundriss ansehen</a>
  <h3>Energieausweis</h3><a href="https://cdn.example.com/energie.pdf">Energieausweis</a>
  <h3>Links zu Internetseiten / virtuellen Rundg&auml;ngen etc.</h3>
  <a href="https://kuula.co/share/collection/7M2g0">360&deg;-Rundgang</a>
  <a href="https://cdn.example.com/expose.pdf">Expos&eacute;</a>
  <a href="https://bauherr.example/projekt">Projektseite</a>
</section>
<section id="anbieter"><div class="nbk-project-developer__inner-wrapper">
  <h5>Musterbau GmbH</h5><p>Hauptstr. 1, 10115 Berlin</p>
  <div>Ansprechpartner
    Erika Mustermann
    Telefon +49 30 1234567
    erika@musterbau.example</div>
</div></section>
</body></html>
"""
)


def test_project_address_and_unit_cards():
    assert project_address(PROJECT_HTML) == "Baumschulenstr. 27-29, 12437 Berlin"
    units = parse_unit_cards(PROJECT_HTML, PROJECT.url, PROJECT)
    assert [u.external_id for u in units] == ["421893", "421894"]
    first, second = units
    assert first.url.endswith("/127686/expose-421893/")
    assert first.title == "Charmante 1-Zimmer-Wohnung"
    # The project page's own address wins over the coarser one from the search card.
    assert (first.address, first.postcode) == ("Baumschulenstr. 27-29, 12437 Berlin", "12437")
    assert (first.price, first.living_area, first.rooms) == (222_507, 33.21, 1)
    assert (second.price, second.living_area, second.rooms) == (700_177, 126.83, 4)
    assert second.raw["project_id"] == "127686"
    assert second.raw["project_url"] == PROJECT.url
    assert second.is_new_build is True


def test_unit_cards_fall_back_to_the_project_address():
    html = PROJECT_HTML.replace("Adresse", "Bauweise")
    units = parse_unit_cards(html, PROJECT.url, PROJECT)
    assert units[0].address == PROJECT.address


def test_parse_unit():
    u = parse_unit(UNIT_HTML, f"{PROJECT.url[:-5]}/expose-421894/")
    assert (u.unit_id, u.object_number) == ("421894", "BS-27-W12")
    assert (u.project_id, u.project_url) == ("127686", PROJECT.url)
    assert u.title == "Großzügige Maisonette-Wohnung mit Dachterrasse"
    assert u.address == "Baumschulenstr. 27-29, 12437 Berlin"
    assert (u.unit_type, u.price, u.price_per_m2) == ("Maisonette", 700_177, 5_521)
    assert (u.floor, u.total_floors) == (4, 5)
    assert (u.rooms, u.bedrooms, u.bathrooms, u.living_area) == (4, 2, 2, 126.83)
    assert (u.last_renovation_year, u.available_from) == (2025, "01.07.2026")
    assert u.has_commission is True
    assert u.buyer_commission == u.seller_commission == "3,57% inkl. MwSt."
    assert (u.energy_certificate_type, u.energy_certificate_date) == (
        "Bedarfsausweis",
        "ab dem 01.05.2014",
    )
    # "Keine Angabe" is not a value worth storing.
    assert u.energy_source is None
    assert (u.energy_efficiency_class, u.final_energy_kwh_m2a) == ("A+", 28.4)
    assert u.has_balcony_terrace is True
    assert u.features == ["Dachterrasse", "Aufzug", "Fußbodenheizung"]
    assert u.location_text == "Ruhige Seitenstraße in Baumschulenweg."
    assert u.equipment_text == "Eichenparkett, Fußbodenheizung."
    assert u.description == "Baujahr 1905, kernsaniert."
    assert u.floor_plan_url == "https://cdn.example.com/grundriss.pdf"
    assert u.energy_certificate_url == "https://cdn.example.com/energie.pdf"
    # Tour, brochure and website share one heading; each has to end up in its own place.
    assert u.website_url == "https://bauherr.example/projekt"
    assert u.virtual_tour_url == "https://kuula.co/share/collection/7M2g0"
    assert (u.provider_name, u.provider_address) == ("Musterbau GmbH", "Hauptstr. 1, 10115 Berlin")
    assert u.contact_name == "Erika Mustermann"
    assert (u.contact_phone, u.contact_email) == ("+49 30 1234567", "erika@musterbau.example")
    assert u.facts["Objektnummer"] == "BS-27-W12"
    # The thumbnail and the slide show the same photo at two sizes and count once.
    assert [m.type for m in u.media] == [
        "PICTURE",
        "PICTURE",
        "FLOORPLAN",
        "DOCUMENT",
        "DOCUMENT",
    ]
    assert u.media[0].url.endswith("a-1.jpg/ORIG/resize/950x1200%3E/format/jpg")
    # The brochure PDF is downloaded with the other documents rather than kept as a link.
    assert u.media[-1].url == "https://cdn.example.com/expose.pdf"
    assert (u.photo_count, u.floor_plan_count) == (2, 3)


def test_gallery_ignores_pictures_outside_the_viewer():
    tree = HTMLParser(UNIT_HTML)
    tree.css_first("#bilder").decompose()
    assert parse_unit(tree.html or "").photo_count == 0


def test_ground_floor_and_basement_words():
    assert _floor("EG") == 0
    assert _floor("Erdgeschoss") == 0
    assert _floor("2. UG") == -2
    assert _floor("UG") == -1
    assert _floor("4") == 4
    html = UNIT_HTML.replace(_detail("floor", "Etage", "4"), _detail("floor", "Etage", "EG"))
    assert parse_unit(html).floor == 0
    assert parse_neubaukompass(html).floor == 0


def test_certificate_energy_and_garage_reach_the_shared_details():
    html = UNIT_HTML.replace(
        "Wesentlicher Energietr&auml;ger : Keine Angabe",
        "Wesentlicher Energietr&auml;ger : Fernw&auml;rme",
    ).replace(
        _detail("realEstate.objectNumber", "Objektnummer", "BS-27-W12"),
        _detail("realEstate.objectNumber", "Objektnummer", "BS-27-W12")
        + _detail("", "Garage/ Stellplatz", "Tiefgarage"),
    )
    data = parse_neubaukompass(html)
    assert data.energy_source == "Fernwärme"
    assert data.has_parking is True


def test_detail_data_from_a_unit_page():
    data = parse_neubaukompass(UNIT_HTML)
    assert (data.floor, data.total_floors) == (4, 5)
    # A refurbished Altbau keeps its original Baujahr from the description.
    assert (data.year_built, data.last_renovation_year) == (1905, 2025)
    assert data.has_balcony is True
    assert data.has_elevator is True
    assert "Eichenparkett" in (data.description or "")


def test_new_builds_are_dated_by_their_handover():
    html = UNIT_HTML.replace(
        _detail("lastRefurbishment", "Letzte Modernisierung", "2025"), ""
    ).replace("Baujahr 1905, kernsaniert.", "Neubau in zentraler Lage.")
    assert parse_neubaukompass(html).year_built == 2026


def test_project_pages_still_parse():
    html = """<html><body><div><div><h2>Projektbeschreibung</h2>
      <p>Bezugsfertig ab Sommer 2027. Ein Aufzug erschlie&szlig;t alle Etagen, zu jeder
      Wohnung geh&ouml;rt ein Kellerabteil.</p></div></div></body></html>"""
    data = parse_neubaukompass(html)
    assert data.year_built == 2027
    assert (data.has_elevator, data.has_cellar) == (True, True)
    # Floors belong to a single flat, which a project page does not describe.
    assert (data.floor, data.total_floors, data.last_renovation_year) == (None, None, None)


def test_media_urls_and_names():
    photo = MediaItem(
        caption=None,
        type="PICTURE",
        url="https://www.neubaukompass.de/pictures/listings/a-1.png/ORIG/resize/950x1200%3E/format/jpg",
    )
    assert _media_url(photo, "large") == photo.url
    original = _media_url(photo, "original")
    assert original == "https://www.neubaukompass.de/pictures/listings/a-1.png"
    assert _filename(1, photo, original) == "foto_01.png"
    # The resized copy is re-encoded as JPEG even though the original is a PNG.
    assert _filename(2, photo, _media_url(photo, Settings().neubaukompass_photo_size)) == (
        "foto_02.jpg"
    )
    plan = MediaItem(caption="Grundriss", type="FLOORPLAN", url="https://cdn.example.com/plan.pdf")
    assert _filename(3, plan, plan.url) == "grundriss_03_Grundriss.pdf"
