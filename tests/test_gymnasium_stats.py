from immo_scanner.reference.gymnasium_stats import (
    build_rows,
    parse_de_number,
    parse_school_links,
    parse_stat_table,
)

ABI_HTML = """
<table>
  <tr><th></th><th>Gymnasium</th><th>Ø</th></tr>
  <tr>
    <td>1</td>
    <td><a href="/kaethe-kollwitz-oberschule">Käthe-Kollwitz-Gymnasium Pankow</a></td>
    <td>1,66</td>
  </tr>
  <tr>
    <td>2</td>
    <td><a href="/franzoesisches-gymnasium">Französisches Gymnasium</a></td>
    <td>1,67</td>
  </tr>
</table>
"""

STUDENTS_HTML = """
<table>
  <tr><th>Gymnasium</th><th>2024/25</th></tr>
  <tr><td><a href="/felix-mendelssohn-bartholdy-gymnasium">Felix-Mendelssohn-Bartholdy-Gymnasium</a></td>
      <td>1.358</td></tr>
</table>
"""

ADMISSIONS_HTML = """
<table>
  <tr><th>Gymnasium</th><th>Plätze</th><th>1. Wünsche</th><th>in %</th></tr>
  <tr>
    <td><a href="/kaethe-kollwitz-oberschule">Käthe-Kollwitz-Gymnasium</a></td>
    <td>64</td><td>102</td><td>159</td>
  </tr>
</table>
"""


def test_parse_de_number():
    assert parse_de_number("1,66") == 1.66
    assert parse_de_number("1.358") == 1358
    assert parse_de_number("91,5") == 91.5
    assert parse_de_number("100") == 100
    assert parse_de_number("–") is None


def test_parse_stat_table_skips_the_rank_column():
    rows = parse_stat_table(ABI_HTML)
    assert rows[0] == {
        "slug": "kaethe-kollwitz-oberschule",
        "name": "Käthe-Kollwitz-Gymnasium Pankow",
        "numbers": [1.66],
    }


def test_build_rows_merges_pages_and_keeps_the_longer_name():
    rows = {
        row["slug"]: row
        for row in build_rows(
            {
                "abi_average.html": ABI_HTML,
                "abi_pass_rate.html": "<table><tr><td>empty</td></tr></table>",
                "admissions.html": ADMISSIONS_HTML,
                "students.html": STUDENTS_HTML,
                "teachers.html": "<table><tr><td>empty</td></tr></table>",
                "migration.html": "<table><tr><td>empty</td></tr></table>",
                "type_level.html": "<table><tr><td>empty</td></tr></table>",
            }
        )
    }
    school = rows["kaethe-kollwitz-oberschule"]
    assert school["name"] == "Käthe-Kollwitz-Gymnasium Pankow"
    assert school["abi_average"] == 1.66
    assert school["abi_year"] == "2025"
    assert school["admission_places"] == 64
    assert school["first_choices"] == 102
    assert school["first_choice_pct"] == 159
    assert school["admissions_year"] == "2025"
    assert school["starts_year_7"] is True
    assert school["starts_year_5"] is False
    assert rows["felix-mendelssohn-bartholdy-gymnasium"]["students"] == 1358
    assert rows["felix-mendelssohn-bartholdy-gymnasium"]["abi_average"] is None
    assert rows["felix-mendelssohn-bartholdy-gymnasium"]["starts_year_7"] is False


YEAR5_HTML = """
<table class="views-table">
  <tr><td><a href="/kaethe-kollwitz-oberschule"></a>
      <a href="/kaethe-kollwitz-oberschule">Käthe-Kollwitz-Gymnasium</a></td></tr>
  <tr><td><a href="/albrecht-duerer-gymnasium">Albrecht-Dürer-Gymnasium</a></td></tr>
</table>
<table>
  <tr><td><a href="/profile">Profile</a></td></tr>
  <tr><td><a href="/sprachen">Sprachen</a></td></tr>
  <tr><td><a href="/leistungskurse">Leistungskurse</a></td></tr>
</table>
"""


def test_membership_flags_ignore_the_smaller_sidebar_table():
    assert [row["slug"] for row in parse_school_links(YEAR5_HTML)] == [
        "kaethe-kollwitz-oberschule",
        "albrecht-duerer-gymnasium",
    ]
    rows = {
        row["slug"]: row
        for row in build_rows(
            {
                "abi_average.html": ABI_HTML,
                "admissions.html": ADMISSIONS_HTML,
                "year5.html": YEAR5_HTML,
                "schnelllerner.html": (
                    "<table><tr><td><a href=\"/albrecht-duerer-gymnasium\">Dürer</a></td></tr></table>"
                ),
                "profile_music.html": (
                    "<table><tr><td><a href=\"/kaethe-kollwitz-oberschule\">Käthe</a></td></tr></table>"
                ),
                "profile_art.html": (
                    "<table><tr><td><a href=\"/albrecht-duerer-gymnasium\">Dürer</a></td></tr></table>"
                ),
                "profile_it.html": (
                    "<table><tr><td><a href=\"/archenhold-gymnasium\">Archenhold</a></td></tr></table>"
                ),
                "europa.html": (
                    "<table><tr><td><a href=\"/gymnasium-steglitz\">Steglitz</a></td></tr></table>"
                ),
            }
        )
    }
    kathe = rows["kaethe-kollwitz-oberschule"]
    assert kathe["starts_year_5"] is True
    assert kathe["starts_year_7"] is True
    assert kathe["profile_music_art"] is True
    assert kathe["schnelllerner"] is False
    duerer = rows["albrecht-duerer-gymnasium"]
    assert duerer["starts_year_5"] is True
    assert duerer["schnelllerner"] is True
    assert duerer["profile_music_art"] is True
    assert duerer["starts_year_7"] is False
    assert rows["archenhold-gymnasium"]["profile_mint"] is True
    assert rows["gymnasium-steglitz"]["europa_schule"] is True
    assert "profile" not in rows
    assert "leistungskurse" not in rows
