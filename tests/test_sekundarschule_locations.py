import pandas as pd

from immo_scanner.explore.sekundarschulen import attach_locations


def test_attach_locations_matches_the_school_number_and_keeps_unknowns():
    stats = pd.DataFrame(
        [
            {"slug": "nelson", "name": "Nelson-Mandela-Schule", "bsn": "04K04"},
            {"slug": "karlshorst", "name": "Sekundarschule Karlshorst", "bsn": "11K17"},
            {"slug": "plain", "name": "Ohne Nummer", "bsn": None},
        ]
    )
    sites = pd.DataFrame(
        [
            {
                "bsn": "04K04",
                "schulart": "Integrierte Sekundarschule",
                "bezirk": "Charlottenburg-Wilmersdorf",
                "ortsteil": "Wilmersdorf",
                "lat": 52.48,
                "lon": 13.32,
            },
            {
                "bsn": "04K04",
                "schulart": "duplicate",
                "bezirk": "other",
                "ortsteil": "other",
                "lat": 0,
                "lon": 0,
            },
        ]
    )
    out = attach_locations(stats, sites)
    assert len(out) == 3
    nelson = out.loc[out["slug"] == "nelson"].iloc[0]
    assert nelson["lat"] == 52.48
    assert nelson["bezirk"] == "Charlottenburg-Wilmersdorf"
    assert nelson["schulart"] == "Integrierte Sekundarschule"
    missing = out.loc[out["slug"] == "karlshorst"].iloc[0]
    assert pd.isna(missing["lat"])
    assert pd.isna(out.loc[out["slug"] == "plain", "lat"]).all()
