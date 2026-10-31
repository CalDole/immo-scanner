import pandas as pd

from immo_scanner.explore.gymnasiums import attach_locations, normalize_school_name


def test_normalize_school_name_ignores_title_and_umlauts():
    assert normalize_school_name("Käthe-Kollwitz-Gymnasium") == normalize_school_name(
        "Käthe-Kollwitz-Oberschule"
    )


def test_attach_locations_matches_one_point_and_leaves_unknowns():
    stats = pd.DataFrame(
        [
            {
                "slug": "kaethe",
                "name": "Käthe-Kollwitz-Gymnasium",
                "students": 943,
                "teachers": 85,
            },
            {
                "slug": "hannah",
                "name": "Hannah-Arendt-Gymnasium",
                "students": 800,
                "teachers": 80,
            },
            {
                "slug": "scharfenberg",
                "name": "Schulfarm Insel Scharfenberg",
                "students": 200,
                "teachers": 20,
            },
        ]
    )
    schools = pd.DataFrame(
        [
            {
                "name": "Käthe-Kollwitz-Oberschule",
                "lat": 52.55,
                "lon": 13.42,
                "bezirk": "Pankow",
                "ortsteil": "Prenzlauer Berg",
            },
            {
                "name": "Hannah-Arendt-Gymnasium",
                "lat": 52.51,
                "lon": 13.40,
                "bezirk": "Mitte",
                "ortsteil": "Moabit",
            },
            {
                "name": "Hannah-Arendt- Gymnasium",
                "lat": 52.52,
                "lon": 13.41,
                "bezirk": "Mitte",
                "ortsteil": "Moabit",
            },
        ]
    )
    out = attach_locations(stats, schools)
    assert len(out) == 3

    kathe = out.loc[out["slug"] == "kaethe"].iloc[0]
    assert kathe["lat"] == 52.55
    assert kathe["bezirk"] == "Pankow"
    assert kathe["ortsteil"] == "Prenzlauer Berg"
    assert kathe["students_per_teacher"] == 943 / 85

    hannah = out.loc[out["slug"] == "hannah"]
    assert len(hannah) == 1
    assert hannah.iloc[0]["lat"] in (52.51, 52.52)

    missing = out.loc[out["slug"] == "scharfenberg"].iloc[0]
    assert pd.isna(missing["lat"])
    assert pd.isna(missing["lon"])
    assert pd.isna(missing["bezirk"])
