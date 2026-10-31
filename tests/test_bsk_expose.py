"""BSK exposé parser against saved pages (October 2026)."""

from pathlib import Path

from immo_scanner.details.bsk import parse_expose
from immo_scanner.details.portals import PARSERS

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://www.bsk-immobilien.de/expose/31684-charmante-3-zimmerwohnung-zw-dahlem"


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _page(rows: str, equipment: str = "", description: str = "") -> str:
    return f"""<html><body><div class="container">
<h1 class="h1">Testwohnung</h1><span>Objekt-ID</span><span>99</span>
<div class="shadow objektdetails"><div class="row"><div><h2>Objektdetails</h2></div>
<div><table class="table">{rows}</table></div></div>
<div class="row"><div><h2>Ausstattung</h2></div><div><div class="text"><p>{equipment}</p></div></div></div>
<div class="row"><div><h2>Objektbeschreibung</h2></div><div><div class="text"><p>{description}</p></div></div></div>
</div></div></body></html>"""


def test_objektdetails_of_31684():
    expose = parse_expose(_html("bsk_expose_31684.html"), URL)
    assert expose.object_id == "31684"
    assert expose.title == "Charmante 3-Zimmerwohnung zw. Dahlem, Lichterfelde und Zehlendorf"
    assert expose.property_type == "Etagenwohnung"
    assert expose.living_area == 71
    assert expose.total_floors == 4
    assert expose.floor == 1
    assert expose.rooms == 3
    assert expose.bedrooms == 2
    assert expose.bathrooms == 1
    assert expose.balcony_count == 1
    assert expose.has_balcony is True
    assert expose.price == 299_000
    assert expose.buyer_commission == "3,57 % inkl. MwSt."
    assert expose.hausgeld == 321
    assert expose.flooring == "Fliesen, Teppich"
    assert expose.heating_type == "Fernwärme"
    assert expose.available_from == "31.07.2027"
    assert expose.year_built == 1929
    assert expose.property_condition == "Teilweise renovierungsbedürftig"
    assert expose.energy_certificate_type == "Verbrauchsausweis"
    assert expose.energy_source == "Fernwärme"
    assert expose.final_energy_type == "Endenergieverbrauch"
    assert expose.final_energy_kwh_m2a == 116.1
    assert expose.energy_efficiency_class == "D"
    assert expose.energy_certificate_valid_until == "27.02.2028"
    assert expose.facts["Verfügbarkeit"] == "verfügbar 31.07.2027"
    assert expose.facts["Heizung"] == "Fernwärme Fernwärme"


def test_text_sections_and_flags_of_31684():
    expose = parse_expose(_html("bsk_expose_31684.html"), URL)
    assert expose.equipment_text.startswith("- 3-Zimmer-Wohnung im 1. Obergeschoss")
    assert "Kellerraum" in expose.equipment_text
    assert expose.description.startswith("Diese schöne 3-Zimmer-Wohnung")
    assert "Lichterfelde-West" in expose.location_text
    # "eigener Kellerraum" is only in the Ausstattung text.
    assert expose.has_cellar is True
    assert expose.has_elevator is None
    assert expose.is_rented is None


def test_media_of_31684():
    expose = parse_expose(_html("bsk_expose_31684.html"), URL)
    assert expose.photo_count == 10
    assert expose.floor_plan_count == 1
    photo = expose.media[0]
    assert photo.url.startswith("https://www.bsk-immobilien.de/files/uploads/objekt/_webp/")
    assert photo.caption == "Außenansicht"
    plan = next(item for item in expose.media if item.type == "FLOORPLAN")
    assert "Exposeplan" in plan.url


def test_project_unit_with_terrace():
    expose = parse_expose(_html("bsk_expose_29745-40.html"))
    assert expose.property_type == "EG-Wohnung"
    assert expose.terrace_count == 1
    assert expose.has_terrace is True
    assert expose.has_balcony is True
    assert expose.buyer_commission == "frei"
    assert expose.property_condition == "Erstbezug"
    assert expose.year_built == 2028


def test_merkmale_and_table_flags():
    expose = parse_expose(
        _page(
            "<tr><td>Ausstattung/Merkmale</td><th class='ausstatt-list'>"
            "<span>Einbauküche</span><span>Dusche, Tageslichtbad</span><span>Fahrstuhl</span>"
            "<span>Gäste-WC</span></th></tr>"
            "<tr><td>Anzahl Stellplätze</td><th>1 (Freiplatz)</th></tr>"
            "<tr><td>Anzahl Loggia</td><th>1</th></tr>"
            "<tr><td>Etage</td><th>EG</th></tr>"
            "<tr><td>Verfügbarkeit</td><th><span>Ja</span><span>verfügbar sofort</span></th></tr>"
            "<tr><td>Vermietet</td><th>Ja</th></tr>"
            "<tr><td>Mtl. Mieteinnahmen</td><th>€ 1.100,00</th></tr>"
            "<tr><td>Endenergiebedarf in kWh/(m²*a)</td><th>130.8</th></tr>"
        )
    )
    assert expose.object_id == "99"
    assert expose.features == ["Einbauküche", "Dusche, Tageslichtbad", "Fahrstuhl", "Gäste-WC"]
    assert expose.has_elevator is True
    assert expose.has_fitted_kitchen is True
    assert expose.has_guest_toilet is True
    assert expose.bathroom_features == "Dusche, Tageslichtbad"
    assert (expose.parking_spaces, expose.parking_type, expose.has_parking) == (
        1,
        "Freiplatz",
        True,
    )
    assert (expose.balcony_count, expose.has_balcony) == (1, True)
    assert expose.floor == 0
    assert expose.available_from == "sofort"
    assert expose.is_rented is True
    assert expose.monthly_rent == 1100
    assert (expose.final_energy_type, expose.final_energy_kwh_m2a) == ("Endenergiebedarf", 130.8)


def test_text_flags_and_negations():
    expose = parse_expose(
        _page(
            "<tr><td>Objekttyp</td><th>Etagenwohnung</th></tr>",
            equipment="- großzügige Dachterrasse<br>- Kellerabteil<br>- Tiefgaragenstellplatz",
            description="Das Haus hat keinen Aufzug. Das Gebäude steht unter Denkmalschutz. "
            "Monatliches Hausgeld von 412,50 €.",
        )
    )
    assert expose.has_terrace is True
    assert expose.has_cellar is True
    assert expose.has_parking is True
    assert expose.has_elevator is False
    assert expose.has_monument_protection is True
    assert expose.has_balcony is None
    assert expose.hausgeld == 412.5


def test_detail_data_for_listing_details():
    detail = PARSERS["bsk"](_html("bsk_expose_31684.html"))
    assert detail.year_built == 1929
    assert (detail.floor, detail.total_floors) == (1, 4)
    assert detail.heating_type == "Fernwärme"
    assert detail.has_balcony is True
    assert detail.balcony_count == 1
    assert detail.has_cellar is True
    assert "Kellerraum" in detail.description
