from immo_scanner.reference.building_age import COLUMNS, ROWS


def test_twelve_english_construction_periods():
    assert len(ROWS) == 12
    assert COLUMNS[:3] == ("class_code", "year_from", "year_to")
    assert [row[0] for row in ROWS] == list("BBCDEFGHIJKL")
    assert ROWS[0][1] is None and ROWS[0][2] == 1870
    assert ROWS[1][0] == "B" and ROWS[1][1] == 1871
    assert ROWS[-1][1] == 2016 and ROWS[-1][2] is None
    assert {row[8] for row in ROWS} == {"Very high", "High", "Medium", "Low", "Very low"}
    assert all(isinstance(row[7], int) and 0 <= row[7] <= 100 for row in ROWS)
