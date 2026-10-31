from pathlib import Path

import pandas as pd

from immo_scanner.reference.grundschulen import (
    apply_headcounts,
    assign_catchment_schools,
    catchment_rows,
    merge_offers,
    parse_portrait,
    portrait_ids,
    primary_school_rows,
    read_wfs,
    school_site_rows,
)

FIXTURES = Path(__file__).parent / "fixtures"

LIST_HTML = """
<a href="Schulportrait.aspx?IDSchulzweig=11"><b>Arkona</b></a><br />
<span id="DataListSchulen_lblSchulart_0">Grundschule</span>
<a href="Schulportrait.aspx?IDSchulzweig=12"><b>Gemeinschaft</b></a><br />
<span id="DataListSchulen_lblSchulart_1">Gemeinschaftsschule</span>
<a href="Schulportrait.aspx?IDSchulzweig=13"><b>Gymnasium</b></a><br />
<span id="DataListSchulen_lblSchulart_2">Gymnasien</span>
"""


def test_portrait_reads_bsn_ganztag_sesb_and_language():
    parsed = parse_portrait((FIXTURES / "grundschule_portrait.html").read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed["bsn"] == "01G01"
    assert parsed["portrait_name"] == "Grundschule am Arkonaplatz"
    assert parsed["ganztag"] == "offen"
    assert parsed["europa_schule"] is True
    assert parsed["partner_language"] == "Französisch"
    assert parsed["first_foreign_language"] == "Englisch"
    assert parsed["bilingual"] is True
    several = parse_portrait(
        "<span id='ContentPlaceHolderMenuListe_lblSchulname'>Schule - 02G01</span>"
        "<span id='ContentPlaceHolderMenuListe_lblAngebote'></span>"
        "<span id='ContentPlaceHolderMenuListe_lblSprachen'>"
        "1. Fremdsprache Englisch, 2. Fremdsprache Französisch</span>"
    )
    assert several is not None
    assert several["first_foreign_language"] == "Englisch"


def test_merge_offers_keeps_the_bound_all_day_branch():
    base = parse_portrait((FIXTURES / "grundschule_portrait.html").read_text(encoding="utf-8"))
    assert base is not None
    bound = {
        "ganztag": "gebunden",
        "europa_schule": False,
        "partner_language": None,
        "first_foreign_language": None,
        "bilingual": False,
    }
    merged = merge_offers(base, bound)
    assert merged["ganztag"] == "gebunden"
    assert merged["europa_schule"] is True
    assert merged["partner_language"] == "Französisch"


def test_portrait_list_keeps_primary_schools_only():
    assert portrait_ids(LIST_HTML) == [("11", "Grundschule"), ("12", "Gemeinschaftsschule")]


def test_wfs_mapping_drops_other_school_types_and_links_catchments():
    schools = read_wfs(FIXTURES / "grundschulen_wfs.geojson")
    rows = primary_school_rows(schools)
    by_bsn = {row["bsn"]: row for row in rows}
    assert set(by_bsn) == {"01G01", "01P01", "03G01"}
    arkona = by_bsn["01G01"]
    assert arkona["name"] == "Grundschule am Arkonaplatz"
    assert arkona["schulart"] == "Grundschule"
    assert arkona["traeger"] == "öffentlich"
    assert arkona["bezirk"] == "Mitte"
    assert arkona["address"] == "Ruppiner Straße 47, 10115 Mitte"
    assert arkona["school_year"] == "2025/26"
    assert arkona["geom"] is not None

    areas = assign_catchment_schools(read_wfs(FIXTURES / "einschulbereiche_wfs.geojson"), schools)
    linked = {row["esb"]: row["bsn"] for row in catchment_rows(areas, {"01G01": "2025/26"})}
    assert linked["0101"] == "01G01"
    assert linked["0102"] == "03G01"
    years = {row["esb"]: row["school_year"] for row in catchment_rows(areas, {"01G01": "2025/26"})}
    assert years["0101"] == "2025/26"
    assert years["0102"] is None


def test_school_sites_keep_every_type_and_the_first_point():
    schools = read_wfs(FIXTURES / "grundschulen_wfs.geojson")
    duplicate = schools.iloc[[0]].copy()
    duplicate.geometry = duplicate.geometry.translate(-13.4, -52.52)
    schools = pd.concat([schools, duplicate], ignore_index=True)
    rows = school_site_rows(schools)
    by_bsn = {row["bsn"]: row for row in rows}
    assert set(by_bsn) == {"01G01", "01P01", "03G01", "01B01"}
    assert by_bsn["01B01"]["name"] == "OSZ Banken"
    assert by_bsn["01B01"]["schulart"] == "Oberstufenzentrum"
    assert by_bsn["01B01"]["bezirk"] == "Mitte"
    assert by_bsn["01B01"]["ortsteil"] == "Moabit"
    assert by_bsn["01G01"]["name"] == "Grundschule am Arkonaplatz"
    assert by_bsn["01G01"]["geom"].data == "POINT(13.4 52.52)"


def test_headcounts_need_a_school_number_and_both_count_columns():
    rows = [{"bsn": "01G01", "students": None, "teachers": None}]
    assert apply_headcounts(rows, pd.DataFrame({"Name": ["Arkona"]})) == 0
    assert rows[0]["students"] is None
    updated = apply_headcounts(
        rows,
        pd.DataFrame({"BSN": ["01G01"], "Schüler": ["420"], "Lehrkräfte": ["28"]}),
    )
    assert updated == 1
    assert rows[0]["students"] == 420
    assert rows[0]["teachers"] == 28
