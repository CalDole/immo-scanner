"""Engel & Völkers exposé parser against saved pages (October 2026)."""

import json
from pathlib import Path

from immo_scanner.details.engelvoelkers import parse_expose
from immo_scanner.details.portals import PARSERS

FIXTURES = Path(__file__).parent / "fixtures"


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _page(**listing) -> str:
    data = {
        "props": {
            "pageProps": {
                "dehydratedState": {"queries": [{"state": {"data": {"listing": listing}}}]}
            }
        }
    }
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'


def test_regular_flat():
    expose = parse_expose(_html("ev_expose.html"))
    assert expose.listing_uuid == "f841344b-775b-51ea-bfbb-309ceb43079f"
    assert expose.display_id == "W-0462Z1"
    assert expose.district == "Mitte"
    assert (expose.price, expose.living_area, expose.rooms) == (698_000, 105, 4)
    assert (expose.bedrooms, expose.bathrooms) == (3, 1)
    assert (expose.floor, expose.total_floors, expose.year_built) == (5, 6, 1986)
    assert expose.heating_type == "Zentralheizung"
    assert expose.energy_source == "Fernwärme"
    assert expose.energy_efficiency_class == "F"
    assert expose.final_energy_kwh_m2a == 173
    assert expose.final_energy_type == "Endenergieverbrauch"
    assert expose.energy_certificate_type == "Verbrauchsausweis"
    assert expose.buyer_commission == "3,57 %"
    assert expose.has_balcony is True
    assert expose.has_elevator is True
    assert expose.has_fitted_kitchen is True
    assert expose.has_cellar is False
    assert expose.has_terrace is False
    assert round(expose.lat, 3) == 52.514
    assert "propertyImages" not in expose.raw
    assert "trackingData" not in expose.raw


def test_media_urls():
    expose = parse_expose(_html("ev_expose.html"))
    assert (expose.photo_count, expose.floor_plan_count) == (13, 1)
    assert expose.media[0].url == (
        "https://uploadcare.engelvoelkers.com/2d557953-5891-455e-9712-6dc99eabf402/-/resize/1600x/"
    )
    assert expose.media[0].caption == "Wohnzimmer"
    assert expose.media[-1].type == "FLOORPLAN"


def test_development_unit_uses_text_fallback():
    expose = parse_expose(_html("ev_expose_unit.html"))
    assert expose.parent_id == "e703a9e4-b13c-51ea-b8f2-b79cd6a54ce9"
    assert expose.ortsteil == "Prenzlauer Berg"
    assert expose.year_built == 1911
    assert expose.floor == 1  # "im ersten Obergeschoss" is not in the record
    assert expose.energy_source == "Gas"
    assert expose.final_energy_type == "Endenergiebedarf"
    assert expose.final_energy_kwh_m2a == 198.7
    assert expose.buyer_commission == "provisionsfrei"
    assert expose.is_rented is False  # "bezugsfrei"


def test_hausgeld_and_rented_from_text():
    expose = parse_expose(
        _page(
            id="x",
            profile={
                "title": "Vermietete 3-Zimmer-Wohnung",
                "description": "Das Hausgeld beträgt 412 € monatlich. Keller und Aufzug.",
            },
            features={"hasElevator": False},
            condition="condition.good",
            heatingMaterial=["heatingMaterial.unknownFuel"],
        )
    )
    assert expose.hausgeld == 412
    assert expose.is_rented is True
    assert expose.has_cellar is True
    assert expose.has_elevator is False  # the flag wins over the text
    assert expose.property_condition == "Gepflegt"
    assert expose.energy_source == "unknownFuel"


def test_registered_parser():
    detail = PARSERS["engelvoelkers"](_html("ev_expose.html"))
    assert (detail.floor, detail.total_floors, detail.year_built) == (5, 6, 1986)
    assert detail.has_balcony is True
    assert detail.has_cellar is False
    assert detail.description
