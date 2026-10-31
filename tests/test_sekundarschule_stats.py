from pathlib import Path

from immo_scanner.reference.sekundarschule_stats import build_rows, parse_school_numbers

ABI_HTML = """
<table>
  <tr><th></th><th>Schule</th><th>Ø</th></tr>
  <tr>
    <td>1</td>
    <td><a href="/nelson-mandela-schule">Nelson-Mandela-Schule</a></td>
    <td>1,93</td>
  </tr>
</table>
"""

PASS_HTML = """
<table>
  <tr><th>Schule</th><th>2024</th><th>2023</th></tr>
  <tr>
    <td><a href="/nelson-mandela-schule">Nelson-Mandela-Schule</a></td>
    <td>99</td><td>100</td>
  </tr>
  <tr>
    <td><a href="/b-traven-oberschule">B.-Traven-Gemeinschaftsschule</a></td>
    <td>100</td><td></td>
  </tr>
</table>
"""

ADMISSIONS_HTML = """
<table>
  <tr><th>Schule</th><th>Plätze</th><th>1. Wünsche</th><th>in %</th></tr>
  <tr>
    <td><a href="/friedensburg-schule">Friedensburg-Schule</a></td>
    <td>130</td><td>449</td><td>345</td>
  </tr>
</table>
"""

NUMBERS_HTML = """
<table>
  <tr><th>Nr.</th><th>Schule</th></tr>
  <tr><td>04K04</td><td><a href="/nelson-mandela-schule">Nelson-Mandela-Schule</a></td></tr>
  <tr><td>11K17</td><td><a href="/zukunftscampus-karlshorst">Sekundarschule Karlshorst</a></td></tr>
</table>
"""

CACHE = Path(__file__).parents[1] / "data" / "reference" / "sekundarschulen_berlin"


def test_school_numbers_read_the_bsn_and_slug():
    rows = parse_school_numbers(NUMBERS_HTML)
    assert rows[0] == {
        "bsn": "04K04",
        "slug": "nelson-mandela-schule",
        "name": "Nelson-Mandela-Schule",
    }


def test_build_rows_keeps_both_years_and_the_school_number():
    rows = {
        row["slug"]: row
        for row in build_rows(
            {
                "abi_average.html": ABI_HTML,
                "abi_pass_rate.html": PASS_HTML,
                "admissions.html": ADMISSIONS_HTML,
                "migration.html": "<table><tr><td>empty</td></tr></table>",
                "type_level.html": "<table><tr><td>empty</td></tr></table>",
                "schulnummer.html": NUMBERS_HTML,
            }
        )
    }
    nelson = rows["nelson-mandela-schule"]
    assert nelson["bsn"] == "04K04"
    assert nelson["abi_average"] == 1.93
    assert nelson["abi_year"] == "2024"
    assert nelson["abi_pass_rate_pct"] == 99
    assert nelson["abi_pass_rate_prev_pct"] == 100
    assert nelson["abi_pass_rate_year"] == "2024"
    traven = rows["b-traven-oberschule"]
    assert traven["abi_pass_rate_pct"] == 100
    assert traven["abi_pass_rate_prev_pct"] is None
    assert traven["bsn"] is None
    demand = rows["friedensburg-schule"]
    assert demand["admission_places"] == 130
    assert demand["first_choices"] == 449
    assert demand["first_choice_pct"] == 345
    assert demand["admissions_year"] == "2025/26"
    assert rows["zukunftscampus-karlshorst"]["bsn"] == "11K17"
    assert rows["zukunftscampus-karlshorst"]["abi_average"] is None


def test_cached_pages_cover_the_published_schools():
    pages = {path.name: path.read_text(encoding="utf-8") for path in CACHE.glob("*.html")}
    rows = {row["slug"]: row for row in build_rows(pages)}
    assert len(rows) == 133
    assert all(row["bsn"] for row in rows.values())
    nelson = rows["nelson-mandela-schule"]
    assert nelson["bsn"] == "04K04"
    assert nelson["abi_average"] == 1.93
    assert nelson["abi_pass_rate_pct"] == 100
    assert nelson["type_level"] == 2
    flatow = rows["flatow-oberschule"]
    assert flatow["type_level"] == 1
    assert flatow["abi_average"] == 2.18
    assert rows["friedensburg-schule"]["first_choice_pct"] == 345
