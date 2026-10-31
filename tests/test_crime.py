from pathlib import Path

import pandas as pd
import pytest
from openpyxl import Workbook

from immo_scanner.explore.crime import crime_trend
from immo_scanner.reference.crime import OFFENSES, parse_sheet, workbook_rows, year_rows

HEADERS = [
    "LOR-Schlüssel (Bezirksregion)",
    "Bezeichnung (Bezirksregion)",
    "Straftaten \n-insgesamt-",
    "Raub",
    "Straßenraub,\nHandtaschen-raub",
    "Körper-verletzungen \n-insgesamt-",
    "Gefährl. und schwere Körper-verletzung",
    "Freiheits-beraubung, Nötigung,\nBedrohung, Nachstellung",
    "Diebstahl \n-insgesamt-",
    "Diebstahl von Kraftwagen",
    "Diebstahl \nan/aus Kfz",
    "Fahrrad-\ndiebstahl",
    "Wohnraum-\neinbruch",
    "Branddelikte \n-insgesamt-",
    "Brand-\nstiftung",
    "Sach-beschädigung -insgesamt-",
    "Sach-beschädigung durch Graffiti",
    "Rauschgift-delikte",
    "Kieztaten",
]


def _row(code, name, total, robbery):
    values = [code, name, total, robbery] + [0] * (len(HEADERS) - 4)
    return values


def _sheet(*rows) -> pd.DataFrame:
    data = [["Kriminalitätsatlas"], [None] * len(HEADERS), HEADERS, *rows]
    return pd.DataFrame(data)


def test_parse_sheet_keeps_regions_and_drops_unlocated_cases():
    table = _sheet(
        _row(11001, "Tiergarten Süd", 5198, 65),
        _row("010000", "Mitte", 79167, 854),
        _row("019900", "Bezirk (Mi), nicht zuzuordnen", 2521, 36),
        _row("999900", "Stadtgebiet Berlin, nicht zuzuordnen", 100, 1),
        _row(None, "Berlin (PKS gesamt)", 13881, 139),
    )
    parsed = parse_sheet(table)
    areas = set(zip(parsed["level"], parsed["area_id"], strict=True))
    assert ("bzr", "011001") in areas
    assert ("bezirk", "Mitte") in areas
    assert ("city", "berlin") in areas
    assert not parsed["name"].str.contains("zuzuordnen").any()
    total = parsed[parsed["offense"] == "total"].set_index("area_id")["value"]
    assert total["011001"] == 5198
    assert total["berlin"] == 13881
    assert set(parsed["offense"]) == {key for key, _label, _header in OFFENSES}


def test_year_rows_join_cases_and_rates_and_skip_dashes():
    cases = _sheet(_row("011001", "Tiergarten Süd", 100, 4))
    rates = _sheet(_row("011001", "Tiergarten Süd", "-", 12.5))
    rows = year_rows(cases, rates, 2025)
    total = next(row for row in rows if row["offense"] == "total")
    robbery = next(row for row in rows if row["offense"] == "robbery")
    assert total["cases"] == 100
    assert total["rate"] is None
    assert total["label"] == "All offences"
    assert robbery["cases"] == 4
    assert robbery["rate"] == 12.5
    assert robbery["year"] == 2025
    assert robbery["level"] == "bzr"


def test_workbook_rows_reads_both_sheets(tmp_path: Path):
    path = tmp_path / "atlas.xlsx"
    book = Workbook()
    cases = book.active
    cases.title = "Fallzahlen_2024"
    rates = book.create_sheet("HZ_2024")
    for sheet, total in ((cases, 10), (rates, 20.5)):
        sheet.append(["title"])
        sheet.append(HEADERS)
        sheet.append(_row("011003", "Alexanderplatz", total, 1))
    book.save(path)
    rows = workbook_rows(path)
    assert len(rows) == len(OFFENSES)
    total = next(row for row in rows if row["offense"] == "total")
    assert total["cases"] == 10
    assert total["rate"] == 20.5
    assert total["area_id"] == "011003"


def test_crime_trend_lines_up_region_bezirk_and_berlin():
    stats = pd.DataFrame(
        [
            {
                "level": "bzr",
                "area_id": "011001",
                "year": 2024,
                "offense": "theft",
                "rate": 1,
                "cases": 10,
            },
            {
                "level": "bzr",
                "area_id": "011001",
                "year": 2025,
                "offense": "theft",
                "rate": 2,
                "cases": 20,
            },
            {
                "level": "bezirk",
                "area_id": "Mitte",
                "year": 2025,
                "offense": "theft",
                "rate": 3,
                "cases": 30,
            },
            {
                "level": "city",
                "area_id": "berlin",
                "year": 2025,
                "offense": "theft",
                "rate": 4,
                "cases": 40,
            },
            {
                "level": "bzr",
                "area_id": "011001",
                "year": 2025,
                "offense": "robbery",
                "rate": 9,
                "cases": 9,
            },
        ]
    )
    trend = crime_trend(stats, "theft", "011001", "Mitte")
    latest = trend[trend["year"] == 2025].set_index("scope")
    assert latest.loc["Bezirksregion", "rate"] == 2
    assert latest.loc["Bezirk", "cases"] == 30
    assert latest.loc["Berlin", "rate"] == 4
    assert set(trend["scope"]) <= set(["Bezirksregion", "Bezirk", "Berlin"])


REAL = Path("data/probe/fallzahlen_hz-2016-2025.xlsx")


@pytest.mark.skipif(not REAL.exists(), reason="crime atlas workbook not downloaded")
def test_published_workbook_covers_every_bezirksregion():
    rows = workbook_rows(REAL)
    latest = [
        row
        for row in rows
        if row["year"] == 2025 and row["offense"] == "neighbourhood" and row["level"] == "bzr"
    ]
    assert len(latest) == 143
    assert all(row["rate"] is not None and row["cases"] is not None for row in latest)
    berlin_2023 = next(
        row
        for row in rows
        if row["year"] == 2023 and row["offense"] == "total" and row["level"] == "city"
    )
    assert berlin_2023["rate"] == pytest.approx(13881)
    assert not any("zuzuordnen" in row["name"] for row in rows)
