from immo_scanner.config import Settings
from immo_scanner.details.immoscout24 import MediaItem, _filename, _media_url, parse_expose

EXPOSE_HTML = """
<html><body>
<script>
var keyValues = {"obj_scoutId":"165716599","obj_rented":"n","obj_balcony":"y","obj_cellar":"n",
"obj_parkingSpace":"underground_garage","obj_noParkSpaces":"1","obj_hasKitchen":"n"};
var config = {
  galleryData: {"images":[
    {"id":"1","caption":"Frontansicht","type":"PICTURE",
     "fullSizePictureUrl":"https://pictures.immobilienscout24.de/listings/abc-1.jpg/ORIG/resize/1106x830%3E/format/webp/quality/73"},
    {"id":"2","caption":"Grundriss","type":"FLOORPLAN",
     "fullSizePictureUrl":"https://pictures.immobilienscout24.de/listings/abc-2.jpg/ORIG/resize/1106x830%3E/format/webp/quality/73"}],
   "documents":[{"title":"Grundriss","url":"https://cdn.example.com/plan.pdf"}]},
  other: 1
};
</script>
<h1 id="expose-title">Sofort einziehen!</h1>
<div data-qa="is24-expose-address"> Zehlendorf, 14165 Berlin</div>
<div class="scoutId"><span>Scout-ID: 165716599</span> <span>|</span> <span>Objekt-ID.: 1009625#yv5Vwr</span></div>
<div class="criteriagroup">
  <span data-qa="is24qa-lift-label"><div><span>Personenaufzug</span></div></span>
  <span data-qa="is24qa-builtInKitchen-label"><div><span>Einbauküche</span></div></span>
  <span data-qa="is24qa-guestToilet-label"><div><span>Gäste-WC</span></div></span>
</div>
<h4 data-qa="is24qa-kosten-label">Kosten</h4>
<dl>
  <dd class="is24qa-typ grid-item">Dachgeschoss</dd>
  <dd class="is24qa-wohnflaeche-ca grid-item">71  m²</dd>
  <dd class="is24qa-nutzflaeche-ca grid-item">12,5 m²</dd>
  <dd class="is24qa-bezugsfrei-ab grid-item">sofort</dd>
  <dd class="is24qa-zimmer grid-item">3</dd>
  <dd class="is24qa-schlafzimmer grid-item">2</dd>
  <dd class="is24qa-badezimmer grid-item">1</dd>
  <dd class="is24qa-kaufpreis grid-item">295.000 €</dd>
  <dd class="is24qa-preism² grid-item">4.155 €/m²
    <span class="popover-icon-anchor print-hide"><button aria-label="Informationen"></button>
      <div class="popover-content"><div class="description">Kalkuliert von ImmoScout24</div></div>
    </span></dd>
  <dd class="is24qa-garage-stellplatz grid-item">1 Tiefgaragen-Stellplatz</dd>
  <dd class="is24qa-garage-stellplatz-kaufpreis grid-item">35.000 €</dd>
  <dd class="is24qa-provision grid-item">2,38% inkl. MwSt.</dd>
  <dd class="is24qa-baujahr grid-item">1962</dd>
  <dd class="is24qa-objektzustand grid-item">Neuwertig</dd>
  <dd class="is24qa-qualitaet-der-ausstattung grid-item">Gehobene Qualität</dd>
  <dd class="is24qa-heizungsart grid-item">Zentralheizung</dd>
  <dd class="is24qa-wesentliche-energietraeger grid-item">Öl</dd>
  <dd class="is24qa-energieausweis grid-item">liegt vor</dd>
  <dd class="is24qa-energieausweistyp grid-item">Verbrauchsausweis</dd>
  <dd class="is24qa-endenergieverbrauch grid-item">113,6 kWh/(m²*a)</dd>
  <dd class="is24qa-energieeffizienzklasse grid-item"><img alt="D" src="/D.svg"></dd>
  <dd class="is24qa-baujahr-laut-energieausweis grid-item">1962
    <span class="popover-icon-anchor print-hide">
      <div class="popover-content"><div class="description">Das Feld entspricht ...</div></div>
      <div class="popover-content"><div class="description">Das Feld entspricht ...</div></div>
    </span></dd>
</dl>
<div><h3>Objektbeschreibung</h3><span class="expose-description-body">Gepflegte Wohnung.</span></div>
<div><h3>Ausstattung</h3><span class="expose-description-body">Kellerabteil, Terrasse</span></div>
<div class="expose-location-description">Ruhige Anliegerstraße.</div>
<div><span>Internet Geschwindigkeit</span><div>Hinweis</div><span>Bis zu 250 MBit/s</span></div>
</body></html>
"""


def test_parse_expose():
    e = parse_expose(EXPOSE_HTML)
    assert (e.scout_id, e.object_id) == ("165716599", "1009625#yv5Vwr")
    assert e.title == "Sofort einziehen!"
    assert e.address == "Zehlendorf, 14165 Berlin"
    assert e.is_rented is False
    assert (e.living_area, e.usable_area, e.available_from) == (71, 12.5, "sofort")
    assert (e.rooms, e.bedrooms, e.bathrooms) == (3, 2, 1)
    assert (e.price, e.price_per_m2) == (295_000, 4_155)
    assert e.buyer_commission == "2,38% inkl. MwSt."
    assert (e.year_built, e.heating_type, e.energy_source) == (1962, "Zentralheizung", "Öl")
    assert (e.energy_certificate, e.energy_certificate_type) == ("liegt vor", "Verbrauchsausweis")
    assert (e.final_energy_kwh_m2a, e.final_energy_type) == (113.6, "Endenergieverbrauch")
    assert e.energy_efficiency_class == "D"
    assert e.energy_certificate_year_built == 1962
    assert e.facts["baujahr-laut-energieausweis"] == "1962"
    assert e.facts["preism²"] == "4.155 €/m²"
    assert (e.property_condition, e.interior_quality) == ("Neuwertig", "Gehobene Qualität")
    assert (e.has_balcony_terrace, e.has_terrace, e.has_cellar) == (True, True, True)
    assert (e.has_parking, e.parking_type) == (True, "1 Tiefgaragen-Stellplatz")
    assert (e.parking_spaces, e.parking_price) == (1, 35_000)
    assert e.features == ["lift", "builtInKitchen", "guestToilet"]
    assert (e.has_fitted_kitchen, e.has_guest_toilet) == (True, True)
    assert (e.internet_speed, e.internet_mbit) == ("Bis zu 250 MBit/s", 250)
    assert e.description == "Gepflegte Wohnung."
    assert e.location_text == "Ruhige Anliegerstraße."
    assert [m.type for m in e.media] == ["PICTURE", "FLOORPLAN", "DOCUMENT"]
    assert (e.photo_count, e.floor_plan_count) == (1, 2)


def test_parse_expose_without_tags_or_parking_facts():
    html = """
    <script>var keyValues = {"obj_parkingSpace":"outside","obj_hasKitchen":"n"};</script>
    <dl><dd class="is24qa-endenergiebedarf grid-item">59 kWh/(m²*a)</dd></dl>
    <div><h3>Ausstattung</h3><span>Bad mit Wanne, separates Gäste-WC</span></div>
    """
    e = parse_expose(html)
    assert (e.has_parking, e.parking_type, e.parking_price) == (True, "Außenstellplatz", None)
    assert (e.has_fitted_kitchen, e.has_guest_toilet) == (False, True)
    assert (e.final_energy_kwh_m2a, e.final_energy_type) == (59, "Endenergiebedarf")
    assert e.features == []

    bare = parse_expose('<script>var keyValues = {"obj_parkingSpace":"no_information"};</script>')
    assert (bare.has_parking, bare.parking_type, bare.has_guest_toilet) == (None, None, None)


def test_efficiency_class_placeholders_are_blank():
    def parsed(value: str) -> str | None:
        html = f'<script>var keyValues = {{"obj_energyEfficiencyClass":"{value}"}};</script>'
        return parse_expose(html).energy_efficiency_class

    assert parsed("NO_INFORMATION") is None
    assert (parsed("A_PLUS"), parsed("A_PLUS_PLUS"), parsed("H")) == ("A+", "A++", "H")


def test_listing_features_reads_is24_amenities():
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "src/immo_scanner/db/alembic/versions/0033_is24_amenities.py"
    )
    spec = spec_from_file_location("is24_amenities", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    sql = migration._listing_features_sql()
    assert "COALESCE(d.has_parking, is24.has_parking) AS has_parking" in sql
    assert "is24.has_fitted_kitchen" in sql
    assert "COALESCE(iw.property_condition, is24.property_condition)" in sql
    assert "is24.has_guest_toilet" in sql


def test_media_urls_and_names():
    photo = MediaItem(
        caption="Wohn-/Essbereich",
        type="PICTURE",
        url="https://pictures.immobilienscout24.de/listings/abc-1.jpg/ORIG/resize/1106x830%3E/format/webp/quality/73",
    )
    original = _media_url(photo, "original")
    assert original == "https://pictures.immobilienscout24.de/listings/abc-1.jpg"
    assert _filename(1, photo, original) == "foto_01_Wohn-_Essbereich.jpg"
    large = _media_url(photo, Settings().is24_photo_size)
    assert _filename(3, photo, large).endswith(".webp")
    doc = MediaItem(caption="Grundriss", type="DOCUMENT", url="https://cdn.example.com/plan.pdf")
    assert _filename(4, doc, _media_url(doc, "large")) == "grundriss_04_Grundriss.pdf"
