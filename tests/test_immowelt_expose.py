import json

from immo_scanner.details.immowelt import parse_expose
from immo_scanner.details.portals import parse_immowelt

CLASSIFIED = {
    "id": "256Q14GY2JL7",
    "sections": {
        "hardFacts": {
            "facts": [
                {"type": "numberOfRooms", "splitValue": "4,5"},
                {"type": "livingSpace", "splitValue": "78,4"},
                {"type": "numberOfFloors", "splitValue": "3."},
            ],
            "price": {"ariaLabel": "999 €"},
        },
        "features": {
            "preview": [{"icon": "cellar", "value": "Keller"}],
            "details": {
                "categories": [
                    {
                        "elements": [
                            {"icon": "availability", "value": "Bezug: ab sofort"},
                            {"icon": "floors", "value": "2. Geschoss/5 Geschossen"},
                            {"icon": "rented", "value": "vermietet"},
                            {"icon": "heritage-protected", "value": "Denkmalschutz-Afa"},
                        ]
                    },
                    {"elements": [{"icon": "elevator", "value": "Kein Personenaufzug"}]},
                    {
                        "elements": [
                            {"icon": "cellar", "value": "Keller (gesamt 15 m²)"},
                            {"icon": "kitchen", "value": "Einbauküche"},
                            {
                                "icon": "bathroom-amenities",
                                "value": "Badezimmer: Badewanne, Bad mit Fenster",
                            },
                            {"icon": "floor-covering", "value": "Bodenbelag: Fliesen, Laminat"},
                        ]
                    },
                    {
                        "elements": [
                            {"icon": "balcony", "value": "Balkon"},
                            {"icon": "terrace", "value": "Terrasse"},
                            {"icon": "parking-lots", "value": "Stellplatz"},
                        ]
                    },
                ]
            },
        },
        "energy": {
            "features": [
                {"type": "yearOfConstruction", "value": "1927"},
                {"type": "state", "value": "Gepflegt"},
                {"type": "heatingSystem", "value": "Zentralheizung"},
                {"type": "energySource", "value": "Gas"},
            ],
            "certificates": [
                {
                    "features": [
                        {"type": "basedOn", "value": "Verbrauchsausweis"},
                        {"type": "primaryEnergySource", "value": "FERN"},
                    ],
                    "scales": [
                        {
                            "efficiencyClass": {"rating": "D"},
                            "values": [{"label": "Energieverbrauch", "value": "105 kWh/(m²·a)"}],
                        }
                    ],
                }
            ],
        },
        "price": {
            "base": {"commissionFee": {"value": "3,57 % inkl. 19 % MwSt."}},
            "additional": [
                {
                    "details": [
                        {
                            "label": {"main": "Hausgeld"},
                            "value": {"main": {"ariaLabel": "365.94 €"}},
                        }
                    ]
                }
            ],
            "breakdown": {"total": {"ariaLabel": "485894 €"}},
        },
        "priceComparison": {"data": {"low": "3.622", "high": "6.477", "value": 4867}},
        "key": {
            "keys": [
                {"label": "Online-ID", "value": "256Q14GY2JL7"},
                {"label": "Referenznummer", "value": "IK-PS-110"},
            ]
        },
        "location": {
            "isAddressPublished": False,
            "address": {"city": "Berlin", "zipCode": "12163", "district": "Steglitz"},
        },
        "mainDescription": {
            "headline": "Einziehen & wohlfühlen",
            "description": "Wohnen mit Charakter<br><br>Zweite Zeile",
        },
        "extendedInfoDescription": {
            "description": (
                "Stichworte\nAnzahl der Schlafzimmer: 1, Anzahl der Badezimmer: 2, "
                "Anzahl Balkone: 2, 4 Etagen, modernisiert: 2026"
            )
        },
    },
    "tracking": {"av_items": [{"price": 449901}]},
}

GROUND = {
    "sections": {
        "hardFacts": {"facts": [{"type": "numberOfFloors", "splitValue": "EG "}]},
        "features": {
            "preview": [
                {"icon": "floors", "value": "Erdgeschoss"},
                {"icon": "elevator", "value": "Personenaufzug"},
            ]
        },
        "extendedInfoDescription": {"description": "Stichworte\n5 Etagen"},
        "energy": {
            "features": [{"type": "yearOfConstruction", "value": "1955"}],
            "certificates": [
                {
                    "features": [{"type": "basedOn", "value": "Bedarfsausweis"}],
                    "scales": [
                        {
                            "efficiencyClass": {"rating": "B"},
                            "values": [{"label": "Energiebedarf", "value": "61 kWh/(m²·a)"}],
                        }
                    ],
                }
            ],
        },
    }
}


def _page(classified: dict) -> str:
    life = {"app_demand_referral_cldp": {"data": {"classified": classified}}}
    encoded = json.dumps(json.dumps(life, ensure_ascii=False), ensure_ascii=False)
    inner = encoded[1:-1]
    return (
        '<html><script id="__UFRN_LIFECYCLE_SERVERREQUEST__">'
        f'window["__UFRN_LIFECYCLE_SERVERREQUEST__"]=JSON.parse("{inner}");'
        "</script></html>"
    )


def test_parse_structured_expose():
    expose = parse_expose(_page(CLASSIFIED), "https://www.immowelt.de/expose/x")
    assert expose.parsed
    assert expose.url == "https://www.immowelt.de/expose/x"
    assert (expose.online_id, expose.reference_number) == ("256Q14GY2JL7", "IK-PS-110")
    assert expose.title == "Einziehen & wohlfühlen"
    assert expose.description == "Wohnen mit Charakter\n\nZweite Zeile"
    assert (expose.price, expose.rooms, expose.living_area) == (449901, 4.5, 78.4)
    assert (expose.postcode, expose.district, expose.address_published) == (
        "12163",
        "Steglitz",
        False,
    )
    # The floor line wins over the card-style "3." and over "4 Etagen".
    assert (expose.floor, expose.total_floors) == (2, 5)
    assert (expose.year_built, expose.last_renovation_year) == (1927, 2026)
    assert expose.property_condition == "Gepflegt"
    assert (expose.heating_type, expose.energy_source) == ("Zentralheizung", "Gas")
    assert expose.energy_certificate_type == "Verbrauchsausweis"
    assert (expose.energy_efficiency_class, expose.final_energy_kwh_m2a) == ("D", 105)
    assert expose.facts["energy_value_label"] == "Energieverbrauch"
    assert expose.hausgeld == 365.94
    assert expose.buyer_commission == "3,57 % inkl. 19 % MwSt."
    assert expose.facts["estimated_total"] == "485894 €"
    assert expose.facts["price_comparison"]["low"] == "3.622"
    assert expose.is_rented is True
    assert expose.available_from == "ab sofort"
    assert (expose.bedrooms, expose.bathrooms, expose.balcony_count) == (1, 2, 2)
    assert expose.bathroom_features == "Badewanne, Bad mit Fenster"
    assert expose.flooring == "Fliesen, Laminat"
    assert expose.has_monument_protection is True
    assert (expose.has_elevator, expose.has_cellar, expose.has_balcony) == (False, True, True)
    assert (expose.has_terrace, expose.has_parking, expose.has_fitted_kitchen) == (True, True, True)

    detail = parse_immowelt(_page(CLASSIFIED))
    assert (detail.floor, detail.total_floors, detail.year_built) == (2, 5, 1927)
    assert detail.has_elevator is False
    assert detail.balcony_count == 2
    assert detail.energy_source == "Gas"


def test_ground_floor_storeys_and_demand_certificate():
    expose = parse_expose(_page(GROUND))
    assert (expose.floor, expose.total_floors) == (0, 5)
    assert expose.has_elevator is True
    assert expose.is_rented is None
    assert expose.energy_certificate_type == "Bedarfsausweis"
    assert (expose.energy_efficiency_class, expose.final_energy_kwh_m2a) == ("B", 61)
    assert expose.facts["energy_value_label"] == "Energiebedarf"


def test_falls_back_to_page_text_without_classified_object():
    html = "<html><body><p>Baujahr 1935\nKellerabteil, kein Aufzug</p></body></html>"
    expose = parse_expose(html)
    assert expose.parsed is False
    detail = parse_immowelt(html)
    assert detail.year_built == 1935
    assert detail.has_cellar is True
    assert detail.has_elevator is False


def test_listing_features_reads_immowelt_columns():
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "src/immo_scanner/db/alembic/versions/0019_immowelt_exposes.py"
    )
    spec = spec_from_file_location("iw_migration", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    sql = migration._listing_features_sql()
    assert "LEFT JOIN immowelt_exposes iw" in sql
    assert "COALESCE(s.hausgeld, iw.hausgeld) AS hausgeld" in sql
    assert "iw.is_rented" in sql
    assert "iw.energy_efficiency_class" in sql
    assert "iw.final_energy_kwh_m2a" in sql
    assert "iw.buyer_commission" in sql
    assert "iw.property_condition" in sql
