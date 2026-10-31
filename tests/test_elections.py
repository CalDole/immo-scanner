import pandas as pd

from immo_scanner.reference.elections import (
    expand_party_codes,
    normalize_key,
    party_key,
    read_party_codes,
)
from immo_scanner.reference.stations import classify


def test_party_key():
    assert party_key("GRÜNE") == "gruene"
    assert party_key("Die Linke") == "linke"
    assert party_key("DIE LINKE") == "linke"
    assert party_key("AfD") == "afd"
    assert party_key("Volt") == "volt"


def test_party_key_full_names():
    """The Abgeordnetenhaus export spells party names out in full."""
    assert party_key("Christlich Demokratische Union Deutschlands") == "cdu"
    assert party_key("Sozialdemokratische Partei Deutschlands") == "spd"
    assert party_key("BÜNDNIS 90/DIE GRÜNEN") == "gruene"
    assert party_key("Alternative für Deutschland") == "afd"
    assert party_key("Freie Demokratische Partei") == "fdp"
    assert party_key("Bündnis Sahra Wagenknecht - Vernunft und Gerechtigkeit") == "bsw"
    # Unrelated parties keep their own slug rather than colliding with a big one.
    assert party_key("Ökologisch-Demokratische Partei") == "okologischdemokratischepartei"
    assert party_key("Sozialistische Gleichheitspartei") == "sozialistischegleichheitspartei"


DSB = """1. Allgemeines;
Feldname;Feldinhalt
Gueltig;gueltige Stimmen insgesamt
P01;Christlich Demokratische Union Deutschlands
P01p;CDU in Prozent
P02;Die Linke
P03;nicht besetzt, kein Ergebniseingang
"""


def test_read_party_codes(tmp_path):
    path = tmp_path / "dsb.csv"
    path.write_text(DSB, encoding="utf-8")
    assert read_party_codes(path) == {
        "P01": "Christlich Demokratische Union Deutschlands",
        "P02": "Die Linke",
    }


def test_expand_party_codes():
    results = pd.DataFrame(
        {
            "Wahlbezirk": ["101"],
            "Gueltig": ["435"],
            "Gueltigp": ["99,5"],
            "P01": ["76"],
            "P01p": ["17,5"],
            "P02": ["115"],
            "P02p": ["26,4"],
            "P03": ["0"],
            "P03p": ["0,0"],
        }
    )
    codes = {"P01": "Christlich Demokratische Union Deutschlands", "P02": "Die Linke"}
    frame, party_cols = expand_party_codes(results, codes)

    assert party_cols == ["Christlich Demokratische Union Deutschlands", "Die Linke"]
    # Percent columns and parties without a name are dropped.
    assert list(frame.columns) == ["Wahlbezirk", "Gueltig", *party_cols]


def test_expand_party_codes_without_codes():
    results = pd.DataFrame({"Wahlbezirk": ["101"], "Gueltig": ["435"], "CDU": ["76"]})
    frame, party_cols = expand_party_codes(results, {})
    assert party_cols is None
    assert frame is results


def test_normalize_key():
    assert normalize_key("01 001") == normalize_key("1001")
    assert normalize_key("101B") == "101B"


def test_station_classification():
    assert classify({"railway": "station", "station": "subway"}) == (False, True)
    assert classify({"railway": "station", "station": "light_rail"}) == (True, False)
    assert classify({"railway": "station", "light_rail": "yes", "subway": "yes"}) == (True, True)
    assert classify({"railway": "station", "network": "VBB"}) == (False, False)
