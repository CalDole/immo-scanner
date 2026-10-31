import pandas as pd
import pytest

from immo_scanner.explore.demographics import age_rows, indicator_catalog, to_plr
from immo_scanner.reference import demographics
from immo_scanner.reference.demographics import (
    grid_rows,
    income_rows,
    match_bezirk,
    migration_rows,
    mss_rows,
    parse_mss_sheet,
    tidy_rows,
    wide_bezirk_rows,
)

BEZIRKE = ["Charlottenburg-Wilmersdorf", "Mitte", "Neukölln", "Pankow"]


def _values(rows: list[dict], indicator: str) -> dict[str, float]:
    return {r["area_id"]: r["value"] for r in rows if r["indicator"] == indicator}


def test_parse_mss_sheet_normalises_codes_and_skips_non_plr_rows():
    raw = pd.DataFrame(
        [
            ["Monitoring Soziale Stadtentwicklung 2025", None, None, None, None, None],
            ["Planungsraum", "Name", "EW", "S1", "K04", "K 16"],
            [None, None, None, "%", "%", "%"],
            ["01100101", "Stülerstraße", 3000, 3.118, 83.9, 44.1],
            [1100102, "Großer Tiergarten", 1200, 0.942, None, 53.7],
            ["Berlin", None, 3_900_000, 5.1, 60.0, 24.0],
        ]
    )
    table = parse_mss_sheet(raw)
    assert list(table.index) == ["01100101", "01100102"]
    assert set(table.columns) == {"S1", "K 04", "K 16"}
    assert table.loc["01100101", "S1"] == pytest.approx(3.118)
    assert pd.isna(table.loc["01100102", "K 04"])


def test_mss_rows_derive_german_share_from_foreign_share():
    table = pd.DataFrame({"K 16": [44.0], "S1": [3.1]}, index=["01100101"])
    rows = mss_rows(table)
    assert _values(rows, "german_share_mss") == {"01100101": 56.0}
    assert _values(rows, "sgb2_share") == {"01100101": 3.1}
    assert {r["topic"] for r in rows} == {"citizenship", "unemployment"}


def test_migration_rows_shares_of_total():
    df = pd.DataFrame(
        {
            "ZEIT": ["202512", "202512"],
            "RAUMID": ["1100101", "01100102"],
            "E_E": ["2.000", "0"],
            "MH_E": ["1.000", "0"],
            "HK_Turk": ["200", "0"],
            "E_A": ["500", "0"],
        }
    )
    rows = migration_rows(df, "AfS register")
    assert _values(rows, "total")["01100101"] == 50.0
    assert _values(rows, "origin_turk")["01100101"] == 10.0
    assert _values(rows, "german_share")["01100101"] == 75.0
    # An empty area has no share rather than a division error.
    assert _values(rows, "total")["01100102"] is None
    origin = next(r for r in rows if r["indicator"] == "origin_turk")
    assert origin["label"] == "Origin: Turkey"
    assert origin["ref_date"] == "202512"


def test_migration_rows_rejects_files_without_migration_columns():
    df = pd.DataFrame({"RAUMID": ["01100101"], "E_E": ["10"], "E_EM": ["5"]})
    with pytest.raises(ValueError, match="--file"):
        migration_rows(df, "AfS register")


def test_grid_rows_treat_suppressed_cells_as_zero():
    cells = pd.DataFrame(
        {
            "plr_id": ["A", "A", "B"],
            "Roemisch_katholisch": ["3", "–", "0"],
            "Evangelisch": ["1", "4", "0"],
            "Sonstige_keine_ohneAngabe": ["6", "6", "0"],
        }
    )
    rows = grid_rows(
        cells,
        demographics.RELIGION_GRID,
        ["Roemisch_katholisch", "Evangelisch", "Sonstige_keine_ohneAngabe"],
        "religion",
        demographics.RELIGION_LABELS,
        "Zensus 2022",
    )
    assert _values(rows, "catholic")["A"] == 15.0
    assert _values(rows, "member")["A"] == 40.0
    assert _values(rows, "other_none")["A"] == 60.0
    assert _values(rows, "catholic")["B"] is None


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Mitte", "Mitte"),
        ("01 Mitte", "Mitte"),
        ("Charlottenburg-Wilm.", "Charlottenburg-Wilmersdorf"),
        ("Neukoelln", None),
        ("Neukölln", "Neukölln"),
        ("Mittelwert", None),
        ("Berlin", None),
    ],
)
def test_match_bezirk(label, expected):
    assert match_bezirk(label, BEZIRKE) == expected


def test_wide_bezirk_rows_turn_counts_into_shares():
    raw = pd.DataFrame(
        [
            ["Tabelle 7.1", None, None, None, None],
            [
                "Bezirk",
                "Insgesamt",
                "Haupt-(Volks-)schulabschluss",
                "ohne allgemeinen Schulabschluss",
                "Fachhochschul- oder Hochschulreife",
            ],
            ["Mitte", 300.0, 60.0, 30.0, 150.0],
            ["Pankow", 400.0, 40.0, "/", 240.0],
            ["Neukölln", 250.0, 75.0, 25.0, 100.0],
            ["Berlin", 3000.0, 500.0, 200.0, 1500.0],
        ]
    )
    rows = wide_bezirk_rows(
        raw, demographics.EDUCATION_COLUMNS, "education", "Mikrozensus", BEZIRKE
    )
    assert _values(rows, "school_haupt") == {"Mitte": 20.0, "Pankow": 10.0, "Neukölln": 30.0}
    assert _values(rows, "school_none")["Mitte"] == 10.0
    assert _values(rows, "school_none")["Pankow"] is None
    assert _values(rows, "school_abitur")["Pankow"] == 60.0
    assert {r["level"] for r in rows} == {"bezirk"}


def test_wide_bezirk_rows_mikrozensus_layout():
    """Hyphenated multi-row headers, POS + Realschule summed, '/' suppressed values."""
    raw = pd.DataFrame(
        [
            ["7.1 Bevölkerung im Land Berlin 2025 ab 15 Jahren", None, None, None, None, None],
            ["Bezirk", "Allgemeiner Schulabschluss", None, None, None, None],
            [
                None,
                "insge-\nsamt¹",
                "Haupt-\n(Volks-)\nschulab-\nschluss",
                "Abschluss der\nPolytechnischen\nOberschule",
                "Realschul- oder\ngleich-\nwertiger Ab-\nschluss",
                "ohne allge-\nmeinen\nSchulab-\nschluss³",
            ],
            [None, "1 000", None, None, None, None],
            ["Land Berlin", 3146, 369, 270, 547, 232],
            ["Mitte", 310, 37, 11, 46, 33],
            ["Pankow", 355, 26, 59, 44, 8],
            ["Charlottenburg-Wilmersdorf", 284, 22, "/", 46, 17],
        ]
    )
    rows = wide_bezirk_rows(
        raw, demographics.EDUCATION_COLUMNS, "education", "Mikrozensus", BEZIRKE
    )
    real = _values(rows, "school_realschule")
    assert real["Pankow"] == pytest.approx(103 / 355 * 100, abs=0.01)
    assert real["Charlottenburg-Wilmersdorf"] == pytest.approx(46 / 284 * 100, abs=0.01)
    assert _values(rows, "school_none")["Mitte"] == pytest.approx(33 / 310 * 100, abs=0.01)
    assert {r["ref_date"] for r in rows} == {"2025"}
    assert "Land Berlin" not in {r["area_id"] for r in rows}


def test_tidy_rows_for_bezirk_file(monkeypatch):
    monkeypatch.setattr(demographics, "_bezirk_names", lambda: BEZIRKE)
    df = pd.DataFrame(
        {"Bezirk": ["Mitte", "Unknown"], "indicator": ["islam", "islam"], "value": ["12,5", "3"]}
    )
    rows = tidy_rows(df, "bezirk", "religion", "Religion per Bezirk")
    assert rows == [
        {
            "level": "bezirk",
            "area_id": "Mitte",
            "topic": "religion",
            "indicator": "islam",
            "label": "islam",
            "value": 12.5,
            "unit": "pct",
            "ref_date": None,
            "source": "Religion per Bezirk",
        }
    ]
    assert tidy_rows(pd.DataFrame({"a": [1]}), "bezirk", "religion", "x") is None


def test_income_rows_find_id_and_median_columns():
    raw = pd.DataFrame(
        [
            ["Planungsraum", "Name", "Beschäftigte", "Median Bruttoentgelt in EUR"],
            ["01100101", "Stülerstraße", 1200, 4210.0],
            ["01100102", "Großer Tiergarten", 800, 3990.0],
            ["01100103", "Lützowstraße", 950, "3.512"],
        ]
    )
    rows = income_rows(raw, "Median pay")
    assert _values(rows, "median_gross_pay") == {
        "01100101": 4210.0,
        "01100102": 3990.0,
        "01100103": 3512.0,
    }
    assert rows[0]["unit"] == "eur"


def test_to_plr_copies_bezirk_values_and_flags_them():
    plr_areas = pd.DataFrame(
        {"area_id": ["p1", "p2", "p3"], "bezirk": ["Mitte", "Mitte", "Pankow"]}
    )
    indicators = pd.DataFrame(
        [
            {"level": "plr", "area_id": "p1", "topic": "age", "indicator": "x", "value": 1.0},
            {"level": "bezirk", "area_id": "Mitte", "topic": "edu", "indicator": "y", "value": 7.0},
        ]
    )
    result = to_plr(indicators, plr_areas)
    edu = result[result["topic"] == "edu"].set_index("area_id")
    assert edu["value"].to_dict() == {"p1": 7.0, "p2": 7.0}
    assert edu["coarse"].all()
    assert not result.loc[result["topic"] == "age", "coarse"].any()
    assert set(result["level"]) == {"plr"}


def test_to_plr_copies_bezirksregion_values_onto_their_planungsraeume():
    plr_areas = pd.DataFrame(
        {
            "area_id": ["p1", "p2", "p3"],
            "bezirk": ["Mitte", "Mitte", "Pankow"],
            "bzr_id": ["011001", "011001", "030001"],
        }
    )
    indicators = pd.DataFrame(
        [{"level": "bzr", "area_id": "011001", "topic": "crime", "indicator": "x", "value": 4.0}]
    )
    result = to_plr(indicators, plr_areas).set_index("area_id")
    assert result["value"].to_dict() == {"p1": 4.0, "p2": 4.0}
    assert result["coarse"].all()


def test_age_rows_from_population_bands():
    lor = pd.DataFrame(
        [
            {
                "plr_id": "01100101",
                "population": 1000,
                "share_under_18_pct": 20.0,
                "share_18_to_64_pct": 60.0,
                "share_65_plus_pct": 20.0,
                "population_date": "202412",
                "demographics": {"E_E": 1000.0, "E_EU1": 10.0, "E_E1U6": 40.0, "E_E80U110": 50.0},
            }
        ]
    )
    rows = age_rows(lor).set_index("indicator")
    assert rows.loc["under_6", "value"] == 5.0
    assert rows.loc["80_plus", "value"] == 5.0
    assert rows.loc["under_18", "value"] == 20.0
    assert pd.isna(rows.loc["6_to_15", "value"])
    catalog = indicator_catalog(age_rows(lor))
    assert list(catalog["indicator"])[:3] == ["under_18", "18_to_64", "65_plus"]
