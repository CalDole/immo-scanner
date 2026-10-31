import pandas as pd
from listing_tooltip import attach_listing_hover, incidents_per_1000_mh, listing_hover


def _row() -> dict:
    return {
        "price": 450_000,
        "living_area": 72,
        "price_per_m2": 6250,
        "avg_price_per_m2": 5800,
        "rooms": 3,
        "commute_minutes": 12,
        "commute_workplace2_minutes": 20,
        "commute_citycenter_minutes": 8.4,
        "total_score": 0.821,
        "nearby_school_abi_average": 2.154,
        "nearby_school_type_level": 3.4,
        "german_citizen_share": 78.24,
        "mh_share": 32.14,
        "oic_share": 12.36,
        "incident_count": 6,
        "mh_population": 1000,
        "nearest_sbahn": "Ostkreuz",
        "nearest_sbahn_m": 400,
        "nearest_ubahn": "Frankfurter Tor",
        "nearest_ubahn_m": 250.2,
        "nearest_gymnasium": "Gymnasium zum Grauen Kloster",
        "grundschule_name": "Grundschule am Planetarium",
        "available_from": "2026-11-01",
    }


def test_listing_hover_lists_the_requested_facts_and_drops_schools():
    hover = listing_hover(_row())

    assert hover["hover"].splitlines() == [
        "450,000 € · 72 m² · 6,250 €/m² · 3 Zi",
        "Planungsraum Ø 5,800 €/m²",
        "12 min to work · 20 min to work 2 · 8 min to city centre",
        "Score 0.82",
        "Gewichtete Abiturnote 2.15 · Gewichtete Schultypisierung 3.40",
        "Deutsche Staatsbürger 78.2%",
        "Migrationshintergrund 32.1%",
        "Islamische Länder 12.4%",
        "Incidents / 1,000 Migrationshintergrund: 2.00 / Jahr",
        "S: Ostkreuz (400 m)",
        "U: Frankfurter Tor (250 m)",
        "Available: 2026-11-01",
    ]
    assert "Gymnasium" not in hover["hover"]
    assert "Grundschule" not in hover["hover"]
    assert hover["abi_label"] == "2.15"
    assert hover["incident_mh_label"] == "2.00 / Jahr"
    assert hover["rooms_label"] == "3 Zi"


def test_listing_hover_omits_unknown_facts_and_keeps_half_rooms():
    hover = listing_hover({"rooms": 2.5, "commute_minutes": 15, "nearest_ubahn": "Alex"})

    assert hover["hover"].splitlines() == [
        "2.5 Zi",
        "15 min to work",
        "U: Alex",
    ]
    assert hover["price_label"] == "–"
    assert hover["sbahn_label"] == "–"
    assert hover["available_label"] == ""


def test_incidents_per_1000_mh_needs_enough_residents():
    assert incidents_per_1000_mh(6, 1000) == 2.0
    assert incidents_per_1000_mh(0, 1000) == 0.0
    assert incidents_per_1000_mh(6, 199) is None
    assert incidents_per_1000_mh(None, 1000) is None


def test_attach_listing_hover_reads_stats_for_the_planungsraum():
    frame = pd.DataFrame(
        [
            {"id": 1, "plr_id": "01100101", "price": 100_000, "incident_count": 3},
            {"id": 2, "plr_id": None, "price": 200_000, "incident_count": 9},
        ]
    )
    stats = pd.DataFrame(
        [
            {
                "plr_id": "01100101",
                "nearby_school_abi_average": 1.8,
                "nearby_school_type_level": 2,
                "mh_share": 20,
                "mh_population": 500,
                "avg_price_per_m2": 4500,
            }
        ]
    )

    attached = attach_listing_hover(frame, stats)

    assert attached.loc[0, "abi_label"] == "1.80"
    assert attached.loc[0, "mh_label"] == "20.0%"
    assert attached.loc[0, "sqm_area_label"] == "4,500 €/m²"
    assert "Planungsraum Ø 4,500 €/m²" in attached.loc[0, "hover"]
    assert "Incidents / 1,000 Migrationshintergrund: 2.00 / Jahr" in attached.loc[0, "hover"]
    assert "Gewichtete Abiturnote" not in attached.loc[1, "hover"]
    assert list(attached["id"]) == [1, 2]
