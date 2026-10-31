"""Approximate shares by school-leaving qualification, one row per party or group.

Figures were published as "ca. N %". `party_key` matches the keys stored on
`wahlbezirke.party_shares`; CDU/CSU is stored as `cdu` and Volt as `volt`
(the loader writes `voltdeutschland`). `nichtwaehler` is not a party result:
its weight is the local share of eligible voters who did not vote.
"""

# (party_key, name, hauptschule_pct, mittlere_reife_pct, abitur_pct, hochschule_pct)
ROWS: list[tuple] = [
    ("gruene", "Bündnis 90/Die Grünen", 4.0, 24.0, 27.0, 45.0),
    ("volt", "Volt (Schätzung)", 4.0, 24.0, 27.0, 45.0),
    ("linke", "Die Linke", 6.0, 29.0, 29.0, 36.0),
    ("fdp", "FDP", 7.0, 34.0, 33.0, 26.0),
    ("cdu", "CDU/CSU", 21.0, 41.0, 18.0, 20.0),
    ("spd", "SPD", 23.0, 43.0, 16.0, 18.0),
    ("bsw", "BSW", 17.0, 45.0, 24.0, 14.0),
    ("afd", "AfD", 19.0, 71.0, 6.0, 4.0),
    ("nichtwaehler", "Nichtwähler", 38.0, 48.0, 9.0, 5.0),
]

NEW_KEYS = ("volt", "bsw", "nichtwaehler")

COLUMNS = (
    "party_key",
    "name",
    "hauptschule_pct",
    "mittlere_reife_pct",
    "abitur_pct",
    "hochschule_pct",
)
