from immo_scanner.details.model import DetailData
from immo_scanner.details.portals import parse_immoscout24, parse_kleinanzeigen
from immo_scanner.details.text import extract_from_text

DESCRIPTION = """
Diese attraktive 3-Zimmer-Wohnung befindet sich im 3. Obergeschoss des Vorderhauses.
Das um 1900 errichtete Gebäude wurde zwischen 1999 und 2001 umfassend saniert.
Die Wohnung verfügt über zwei Balkone zum Hof. Ein Kellerabteil gehört dazu.
Beheizt wird über eine Gas-Zentralheizung. Fahrradstellplätze im Hof. Kein Aufzug.
"""


def test_extract_from_text():
    data = extract_from_text(DESCRIPTION)
    assert data.year_built == 1900
    assert data.last_renovation_year == 2001
    assert data.floor == 3
    assert data.balcony_count == 2
    assert data.has_balcony is True
    assert data.has_cellar is True
    assert data.has_elevator is False
    assert data.has_parking is None
    assert data.heating_type == "Zentralheizung"
    assert data.energy_source == "Gas"


def test_extract_labeled_and_single_balcony():
    data = extract_from_text("Baujahr: 1965\nEtage 4 von 6\nSonniger Balkon, Tiefgaragenstellplatz")
    assert (data.year_built, data.floor, data.total_floors) == (1965, 4, 6)
    assert data.balcony_count == 1
    assert data.has_parking is True


def test_fill_from_prefers_structured_but_accepts_positive_text():
    structured = DetailData(floor=2, has_elevator=False, has_balcony=True)
    text = DetailData(floor=5, has_elevator=True, balcony_count=2, heating_type="Fernwärme")
    merged = structured.fill_from(text)
    assert merged.floor == 2
    assert merged.has_elevator is True
    assert merged.balcony_count == 2
    assert merged.heating_type == "Fernwärme"


IS24_HTML = """
<html><body>
<script>var keyValues = {"obj_floor":"2","obj_numberOfFloors":"5","obj_yearConstructed":"1972",
"obj_lastRefurbish":"2018","obj_heatingType":"district_heating","obj_lift":"y",
"obj_cellar":"y","obj_balcony":"n","obj_parkingSpace":"no_information"};
</script>
<dl><dd class="is24qa-wesentliche-energietraeger grid-item">Fernwärme</dd></dl>
<div><h3>Objektbeschreibung</h3><span class="expose-description-body">Helle Wohnung.</span></div>
</body></html>
"""


def test_parse_immoscout24():
    data = parse_immoscout24(IS24_HTML)
    assert (data.floor, data.total_floors, data.year_built) == (2, 5, 1972)
    assert data.last_renovation_year == 2018
    assert data.heating_type == "Fernwärme"
    assert data.energy_source == "Fernwärme"
    assert (data.has_elevator, data.has_cellar, data.has_balcony) == (True, True, False)
    assert data.balcony_count == 0
    assert data.has_parking is None
    assert data.description.startswith("Objektbeschreibung")


def test_parse_immoscout24_parking_type_means_parking():
    data = parse_immoscout24(IS24_HTML.replace("no_information", "underground_garage"))
    assert data.has_parking is True


KLEINANZEIGEN_HTML = """
<ul>
  <li class="addetailslist--detail">Etage<span class="addetailslist--detail--value">3</span></li>
  <li class="addetailslist--detail">Baujahr<span class="addetailslist--detail--value">1910</span></li>
</ul>
<li class="checktag">Balkon</li><li class="checktag">Keller</li>
<p id="viewad-description-text">Altbau mit Fernwärme, 2015 kernsaniert.</p>
"""


def test_parse_kleinanzeigen():
    data = parse_kleinanzeigen(KLEINANZEIGEN_HTML)
    assert (data.floor, data.year_built, data.last_renovation_year) == (3, 1910, 2015)
    assert (data.has_balcony, data.has_cellar, data.has_elevator) == (True, True, None)
    assert data.balcony_count == 1
    assert data.heating_type == "Fernwärme"


AD_HTML = """
<html><body>
<h1 id="viewad-title">Altbau mit Balkon</h1>
<span id="viewad-locality">10965 Berlin - Kreuzberg</span>
<div id="viewad-extra-info"><span>22.09.2026</span></div>
<article id="viewad-product">
  <div class="galleryimage-element">
    <img data-imgsrc="https://img.kleinanzeigen.de/api/v1/prod-ads/images/aa/one?rule=$_59.AUTO" alt="Wohnzimmer">
  </div>
  <div class="galleryimage-element">
    <img data-imgsrc="https://img.kleinanzeigen.de/api/v1/prod-ads/images/aa/one?rule=$_59.JPG" alt="Wohnzimmer">
  </div>
  <div class="galleryimage-element">
    <img data-imgsrc="https://img.kleinanzeigen.de/api/v1/prod-ads/images/bb/two?rule=$_59.AUTO" alt="Küche">
  </div>
</article>
<ul>
  <li class="addetailslist--detail">Wohnfläche<span class="addetailslist--detail--value">86,59 m²</span></li>
  <li class="addetailslist--detail">Zimmer<span class="addetailslist--detail--value">3</span></li>
  <li class="addetailslist--detail">Schlafzimmer<span class="addetailslist--detail--value">2</span></li>
  <li class="addetailslist--detail">Badezimmer<span class="addetailslist--detail--value">1</span></li>
  <li class="addetailslist--detail">Etage<span class="addetailslist--detail--value">3</span></li>
  <li class="addetailslist--detail">Wohnungstyp<span class="addetailslist--detail--value">Etagenwohnung</span></li>
  <li class="addetailslist--detail">Baujahr<span class="addetailslist--detail--value">1998</span></li>
  <li class="addetailslist--detail">Provision<span class="addetailslist--detail--value">Mit Provision</span></li>
</ul>
<li class="checktag">Balkon</li>
<li class="checktag">Terrasse</li>
<li class="checktag">Einbauküche</li>
<li class="checktag">Aufzug</li>
<p id="viewad-description-text">Helle Wohnung mit Balkon.</p>
</body></html>
"""


def test_parse_kleinanzeigen_ad():
    from datetime import date

    from immo_scanner.details.kleinanzeigen import parse_ad

    ad = parse_ad(
        AD_HTML,
        "https://www.kleinanzeigen.de/s-anzeige/altbau/3520291974-196-25915",
    )
    assert ad.ad_id == "3520291974"
    assert ad.address == "10965 Berlin - Kreuzberg"
    assert ad.posted_on == date(2026, 9, 22)
    assert (ad.living_area, ad.rooms, ad.bedrooms, ad.bathrooms) == (86.59, 3, 2, 1)
    assert (ad.floor, ad.year_built) == (3, 1998)
    assert ad.apartment_type == "Etagenwohnung"
    assert ad.provision == "Mit Provision"
    assert ad.description == "Helle Wohnung mit Balkon."
    assert (ad.has_balcony, ad.has_terrace, ad.has_fitted_kitchen, ad.has_elevator) == (
        True,
        True,
        True,
        True,
    )
    assert [item.url for item in ad.media] == [
        "https://img.kleinanzeigen.de/api/v1/prod-ads/images/aa/one?rule=$_57.AUTO",
        "https://img.kleinanzeigen.de/api/v1/prod-ads/images/bb/two?rule=$_57.AUTO",
    ]
