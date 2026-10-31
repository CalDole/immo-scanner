import logging
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from immo_scanner.explore.listing_copy import origin_facts
from immo_scanner.explore.origins import area_totals, kiez_summary, map_values, origin_catalog
from immo_scanner.reference.origins import (
    DIVERSITY_COUNTRIES,
    EU_COUNTRIES,
    OUTSIDE_LABEL,
    diversity,
    edition_rows,
    normalize_header,
    outside_eu_yugo,
    parse_t1,
    parse_t4,
    plr_ids,
    ref_date,
)

HEADER = "Bezirk\nPrognoseraum\nBezirksregion\nPlanungsraum"


def sheet(title: str, header_rows: list[list], data: list[list]) -> pd.DataFrame:
    """A raw report sheet: title, header block, Bezirk heading, data rows, footnote."""
    width = 4 + len(header_rows[0])
    blank = [None] * width
    lines = [[title] + blank[1:], blank]
    for index, cells in enumerate(header_rows):
        lines.append([HEADER if index == 0 else None, None, None, None] + list(cells))
    heading = blank[:]
    heading[4] = heading[-1] = "Mitte"
    lines += [heading, *data, ["¹ Footnote"] + blank[1:]]
    return pd.DataFrame(lines)


def t1_sheet(rows: list[tuple[list, object, object]]) -> pd.DataFrame:
    return sheet(
        "1  Einwohnerinnen und Einwohner ... in Berlin am 30. Juni 2021\n nach LOR",
        [
            ["Insgesamt", None, None, None],
            ["Anzahl", "% ", "darunter mit\nMigrationshintergrund (Sp.9+11)", None],
            [None, None, "Anzahl", "% Sp.1"],
            [1, 2, 3, 4],
        ],
        [codes + [residents, 100, mh, None] for codes, residents, mh in rows],
    )


T4_OLD = [
    "Ins-\ngesamt",
    "Darunter aus Herkunftsgebiet Euro-\npäische\nUnion\n(EU) ²",
    *["darunter Frank-\nreich", "Griechen-\nland", "Italien", "Öster-\nreich", "Spanien"],
    *["Polen", "Bulgarien", "Rumänien", "Kroatien", "Vereinigtes Königreich"],
    "dar. Bosnien und Herzegowina",
    "Darunter aus Herkunftsgebiet dar. Serbien",
    "darunter Russische Föderation",
    "Ukraine",
    "Kasachstan",
    "Islamische Länder (OIC) ⁵\neinschl. Syrien",
    "darunter Türkei",
    "Iran",
    "Arabische Länder ⁶ einschl. Syrien",
    "darunter Libanon",
    "Syrien",
    "Vietnam",
    "Vereinigte Staaten/ USA",
    "nicht eindeutig zuordenbar/ ohne Angabe ⁷",
    "Planungsraum",
]
T4_NEW_EXTRA = ["Afghani-stan", "Irak", "China", "Indien"]


def t4_sheet(headers: list[str], rows: list[tuple[list, list]]) -> pd.DataFrame:
    return sheet(
        "4  Einwohnerinnen und Einwohner mit Migrationshintergrund in Berlin am 31. Dezember "
        "2025 nach LOR-Planungsräumen",
        [headers],
        [codes + values + [None] for codes, values in rows],
    )


def test_normalize_header_strips_prefixes_footnotes_and_hyphenation():
    assert normalize_header("Darunter aus Herkunftsgebiet dar. Serbien") == "serbien"
    assert normalize_header("Afghani-stan") == "afghanistan"
    assert normalize_header("Euro-\npäische\nUnion\n(EU) 1") == "europäischeunioneu"
    assert normalize_header("Vereinigte Staaten/ USA") == "vereinigtestaatenusa"
    assert normalize_header("nicht eindeutig zuordenbar/ ohne Angabe ⁷") == (
        "nichteindeutigzuordenbarohneangabe"
    )
    assert normalize_header("Islamische Länder (OIC) 4 einschl. Syrien") != "syrien"


def test_plr_ids_from_mixed_code_cells():
    raw = pd.DataFrame(
        [
            ["01", 10.0, 1.0, "01", 5],
            [None, None, None, None, "Mitte"],
            ["12", 30, 7, "4", 1],
            ["Berlin", None, None, None, 9],
        ]
    )
    ids = [i if isinstance(i, str) else None for i in plr_ids(raw)]
    assert ids == ["01100101", None, "12300704", None]


def test_parse_t1_reads_totals_and_treats_dash_as_missing():
    raw = t1_sheet([(["01", 10.0, 1.0, "01"], 3436, 2092), (["01", 10.0, 1.0, "02"], "–", "–")])
    table = parse_t1(raw)
    assert list(table.index) == ["01100101", "01100102"]
    assert table.loc["01100101", "residents"] == 3436
    assert table.loc["01100101", "mh"] == 2092
    assert table.loc["01100102"].isna().all()


def test_parse_t4_old_layout_matches_labels_exactly():
    values = list(range(100, 100 + len(T4_OLD) - 1))
    table = parse_t4(t4_sheet(T4_OLD, [(["01", 10.0, 1.0, "01"], values)]))
    row = table.loc["01100101"]
    keys = list(table.columns)
    assert keys[:3] == ["mh_t4", "eu", "france"]
    assert {"bosnia", "serbia", "oic", "arab", "syria", "unassigned"} <= set(keys)
    assert row["oic"] == 100 + T4_OLD.index("Islamische Länder (OIC) ⁵\neinschl. Syrien")
    assert row["syria"] == 100 + T4_OLD.index("Syrien")
    assert row["serbia"] == 100 + T4_OLD.index("Darunter aus Herkunftsgebiet dar. Serbien")


def test_parse_t4_new_layout_has_new_countries(caplog):
    headers = [h for h in T4_OLD if "Bosnien" not in h and "Serbien" not in h]
    headers = [h for h in headers if "OIC" not in h and "Arabische" not in h]
    headers = headers[:-1] + T4_NEW_EXTRA + ["Planungsraum"]
    values = [1] * (len(headers) - 1)
    with caplog.at_level(logging.WARNING):
        table = parse_t4(t4_sheet(headers, [(["01", 10.0, 1.0, "01"], values)]))
    assert {"afghanistan", "iraq", "china", "india"} <= set(table.columns)
    assert not {"bosnia", "serbia", "oic", "arab"} & set(table.columns)
    assert "Unknown T4 column" not in caplog.text


def test_parse_t4_warns_about_unknown_columns(caplog):
    with caplog.at_level(logging.WARNING):
        table = parse_t4(t4_sheet(["Insgesamt", "Atlantis"], [(["01", 1, 1, "01"], [5, 2])]))
    assert list(table.columns) == ["mh_t4"]
    assert "Atlantis" in caplog.text


def test_ref_date_from_title_or_file_name():
    assert ref_date(t1_sheet([])) == date(2021, 6, 30)
    untitled = pd.DataFrame([["Tabelle"]])
    assert ref_date(untitled, Path("SB_A01-16-00_2025h02_BE.xlsx")) == date(2025, 12, 31)
    with pytest.raises(ValueError):
        ref_date(untitled)


def counts(**overrides) -> pd.DataFrame:
    """One area: 1000 residents, 400 MH, 100 EU (60 in the nine listed), 10 per country."""
    base = {"residents": 1000, "mh": 400, "eu": 100, "unassigned": 40}
    base.update({key: 10 for key in DIVERSITY_COUNTRIES})
    base.update({key: 60 / len(EU_COUNTRIES) for key in EU_COUNTRIES})
    base.update(overrides)
    return pd.DataFrame([base], index=["01100101"])


def test_diversity_uses_fixed_categories_and_excludes_unassigned():
    frame = counts()
    countries = frame[DIVERSITY_COUNTRIES].iloc[0]
    other_eu = 100 - 60
    base = 400 - 40
    other = base - countries.sum() - other_eu
    shares = [*(countries / base), other_eu / base, other / base]
    expected = 1 - sum(p * p for p in shares)
    assert diversity(frame).iloc[0] == pytest.approx(expected)
    # Extra countries (Bosnia, China, ...) fall into "other" and leave the index unchanged.
    assert diversity(counts(bosnia=30, china=20)).iloc[0] == pytest.approx(expected)


def test_diversity_is_missing_for_small_or_unknown_areas():
    assert pd.isna(diversity(counts(residents=499)).iloc[0])
    assert pd.isna(diversity(counts(mh=float("nan"))).iloc[0])


def test_diversity_floors_negative_residuals():
    frame = counts(mh=100, unassigned=0)
    value = diversity(frame).iloc[0]
    assert 0 <= value <= 1


def test_outside_origin_subtracts_eu_yugoslavia_uk_and_us_once():
    frame = counts(yugo=80, croatia=10, uk=12, usa=8)
    # Croatia is inside former Yugoslavia, so 80 - 10 is the part outside the EU.
    assert outside_eu_yugo(frame).iloc[0] == 400 - 100 - 12 - 8 - (80 - 10)
    t1 = frame[["residents", "mh"]]
    t4 = frame.drop(columns=["residents", "mh"]).assign(mh_t4=frame["mh"])
    rows = pd.DataFrame(edition_rows(t1, t4, date(2025, 6, 30))).set_index("origin_key")
    assert rows.at["mh_outside", "count"] == 210
    assert rows.at["mh_outside", "share"] == 21.0
    assert rows.at["mh_outside", "kind"] == "group"
    assert rows.at["mh_outside", "label"] == OUTSIDE_LABEL
    assert outside_eu_yugo(counts()) is None


def test_edition_rows_use_t1_totals_and_log_mismatch(caplog):
    t1 = pd.DataFrame({"residents": [1000.0, None], "mh": [400.0, None]}, index=["a", "b"])
    t4 = counts().drop(columns=["residents", "mh"]).assign(mh_t4=390)
    t4 = pd.concat([t4.rename(index={"01100101": "a"}), t4.rename(index={"01100101": "b"})])
    with caplog.at_level(logging.WARNING):
        rows = pd.DataFrame(edition_rows(t1, t4, date(2024, 6, 30)))
    assert "T4 total differs" in caplog.text
    a = rows[rows["plr_id"] == "a"].set_index("origin_key")
    assert a.at["mh", "count"] == 400
    assert a.at["mh", "share"] == 40.0
    assert a.at["turkey", "share"] == 1.0
    assert a.at["eu", "kind"] == "group"
    assert "mh_t4" not in a.index
    assert "mh_outside" not in a.index
    b = rows[rows["plr_id"] == "b"].set_index("origin_key")
    assert pd.isna(b.at["residents", "count"])
    assert pd.isna(b.at["turkey", "share"])
    assert pd.isna(b.at["simpson", "share"])


def stats_frame() -> pd.DataFrame:
    when = date(2025, 12, 31)
    rows = []
    for plr, residents, turkey in (("p1", 1000, 100), ("p2", 3000, 0), ("p3", 100, 50)):
        frame = counts(residents=residents, turkey=turkey).rename(index={"01100101": plr})
        t1 = frame[["residents", "mh"]]
        t4 = frame.drop(columns=["residents", "mh"]).assign(mh_t4=frame["mh"])
        rows += edition_rows(t1, t4, when)
    return pd.DataFrame(rows)


def test_area_totals_weight_by_residents():
    stats = stats_frame()
    totals = area_totals(stats, pd.Series({"p1": "X", "p2": "X", "p3": "Y"}))
    x = totals[totals["group"] == "X"].set_index("origin_key")
    assert x.at["turkey", "count"] == 100
    assert x.at["turkey", "share"] == pytest.approx(100 / 4000 * 100)
    assert x.at["simpson", "share"] > 0
    y = totals[totals["group"] == "Y"].set_index("origin_key")
    assert pd.isna(y.at["simpson", "share"])


def test_map_values_hide_small_areas():
    stats = stats_frame()
    values = map_values(stats, "turkey", date(2025, 12, 31)).set_index("area_id")["value"]
    assert values["p1"] == 10.0
    assert pd.isna(values["p3"])


def test_origin_catalog_order_and_dates():
    catalog = origin_catalog(stats_frame())
    keys = list(catalog["origin_key"])
    assert keys[:3] == ["mh", "eu", "simpson"]
    assert keys[-1] == "unassigned"
    assert "residents" not in keys
    assert catalog.iloc[0]["dates"] == [date(2025, 12, 31)]


def test_kiez_summary_and_origin_facts():
    stats = stats_frame()
    summary = kiez_summary(stats[stats["plr_id"] == "p1"])
    assert summary["residents"] == 1000
    assert summary["top"][0] == ("Turkey", 10.0)
    assert summary["vietnam"] == 1.0
    facts = dict(origin_facts(summary))
    assert facts["Migration background"] == "40.0%"
    assert facts["Turkey"] == "10.0%"
    assert facts["Vietnam"] == "1.0%"
    assert "Origin diversity" in facts
    outside_top = origin_facts({**summary, "top": [("Turkey", 10.0)]})
    assert [label for label, _value in outside_top].count("Vietnam") == 1
    assert dict(outside_top)["Vietnam"] == "1.0%"
    already_listed = origin_facts({**summary, "top": [("Vietnam", 1.0), ("Turkey", 10.0)]})
    assert [label for label, _value in already_listed].count("Vietnam") == 1
    small = kiez_summary(stats[stats["plr_id"] == "p3"])
    assert small["top"] == []
    assert small["vietnam"] is None
    assert origin_facts(None) == []
