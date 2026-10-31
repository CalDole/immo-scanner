from immo_scanner.reference.party_education import COLUMNS, ROWS


def test_party_education_shares():
    assert COLUMNS == (
        "party_key",
        "name",
        "hauptschule_pct",
        "mittlere_reife_pct",
        "abitur_pct",
        "hochschule_pct",
    )
    assert [row[0] for row in ROWS] == [
        "gruene",
        "volt",
        "linke",
        "fdp",
        "cdu",
        "spd",
        "bsw",
        "afd",
        "nichtwaehler",
    ]
    assert [row[1] for row in ROWS] == [
        "Bündnis 90/Die Grünen",
        "Volt (Schätzung)",
        "Die Linke",
        "FDP",
        "CDU/CSU",
        "SPD",
        "BSW",
        "AfD",
        "Nichtwähler",
    ]
    assert all(sum(row[2:]) == 100 for row in ROWS)
