from datetime import date
from pathlib import Path

import httpx
import pandas as pd
import pytest

from immo_scanner.enrich.incidents import Candidate, _with_retries, location_candidates
from immo_scanner.explore.incidents import incident_rates, unplaced_counts, window_years
from immo_scanner.reference.berliner_register import parse_page

FIXTURE = Path(__file__).parent / "fixtures" / "berliner_register_page.html"


def test_parse_page_reads_every_field():
    rows = {row["id"]: row for row in parse_page(FIXTURE.read_text(encoding="utf-8"))}
    assert len(rows) == 3
    first = rows["eb1cc8fe-e63b-49f4-91cc-79851ac6031e"]
    assert first == {
        "id": "eb1cc8fe-e63b-49f4-91cc-79851ac6031e",
        "incident_date": date(2026, 10, 6),
        "title": "Hakenkreuz in Wendenschloß",
        "bezirk": "Treptow-Köpenick",
        "description": "An einem Schild an der Kreuzung Wendenschloßstr./ Lienhardweg wurde "
        "ein Hakenkreuz entdeckt und entfernt.",
        "source": "Register Treptow-Köpenick",
    }


def test_parse_page_keeps_paragraphs_and_old_entries():
    rows = {row["id"]: row for row in parse_page(FIXTURE.read_text(encoding="utf-8"))}
    multi = rows["94e97112-79a6-4959-94c7-c56b95ca2ef6"]
    assert multi["description"].count("\n") >= 5
    assert multi["bezirk"] == "Lichtenberg"
    old = rows["b7f438dc-e53a-4270-b72e-1495d918cdcb"]
    assert old["incident_date"] == date(2002, 3, 31)
    assert "Kurfürstendamm" in old["description"]
    assert "\xa0" not in old["description"]
    assert old["source"] == "BZ 03.04.02; Infoportal CW"


def test_parse_page_without_list_is_empty():
    assert parse_page("<html><body><p>Keine Vorfälle</p></body></html>") == []


@pytest.mark.parametrize(
    ("title", "description", "expected"),
    [
        (
            "Neonazistischer Zahlencode in Baumschulenweg",
            "In der Kiefholzstr. wurde an einem Schild ein Zahlencode entdeckt.",
            [Candidate("Kiefholzstraße", "street"), Candidate("Baumschulenweg", "place")],
        ),
        (
            "Hakenkreuz in Wendenschloß",
            "An der Kreuzung Wendenschloßstr./ Lienhardweg wurde ein Hakenkreuz entdeckt.",
            [
                Candidate("Wendenschloßstraße", "street"),
                Candidate("Lienhardweg", "street"),
                Candidate("Wendenschloß", "place"),
            ],
        ),
        (
            "Extrem rechter Aufkleber in Niederschöneweide",
            "Am S-Bahnhof Schöneweide wurde ein Aufkleber entdeckt.",
            [Candidate("Bahnhof Schöneweide", "station"), Candidate("Niederschöneweide", "place")],
        ),
        (
            "NS-verherrlichender Aufkleber auf der Frankfurter Allee",
            None,
            [Candidate("Frankfurter Allee", "street")],
        ),
        (
            "Pöbelei in Mitte",
            "Der Viktoria-Luise-Platz und die Plesser Str. waren betroffen.",
            [
                Candidate("Viktoria-Luise-Platz", "street"),
                Candidate("Plesser Straße", "street"),
                Candidate("Mitte", "place"),
            ],
        ),
        (
            "Wolfsangel am S-Bahnhof Rummelsburg",
            "Auf dem Spielplatz und dem Gehweg wurden Aufkleber entdeckt.",
            [Candidate("Bahnhof Rummelsburg", "station")],
        ),
        ("Rassistische Beleidigung", "Eine Person wurde beleidigt.", []),
    ],
)
def test_location_candidates(title, description, expected):
    assert location_candidates(title, description) == expected


def test_location_candidates_are_capped():
    text = "In der Astraße, Bstraße, Cstraße, Dstraße und Estraße."
    assert len(location_candidates("Aufkleber in Pankow", text)) == 4


def test_incident_rates_per_thousand_residents_per_year():
    plr_areas = pd.DataFrame(
        {"area_id": ["a", "b", "c"], "population": [2000, 4000, 100]}
    )
    incidents = pd.DataFrame(
        {
            "plr_id": ["a", "a", "b", "b", "c", None],
            "geo_precision": ["street", "station", "street", "place", "street", "bezirk"],
            "bezirk": ["X", "X", "X", "X", "Y", "Y"],
        }
    )
    rates = incident_rates(incidents, plr_areas, years=2.0).set_index("area_id")
    assert rates.loc["a", "count"] == 2
    assert rates.loc["a", "value"] == pytest.approx(0.5)
    assert rates.loc["b", "count"] == 1
    assert rates.loc["b", "value"] == pytest.approx(0.125)
    assert rates.loc["c", "count"] == 1
    assert pd.isna(rates.loc["c", "value"])
    assert pd.isna(rates.loc["a", "value_mh"])
    assert unplaced_counts(incidents).to_dict() == {"X": 1, "Y": 1}


def test_incident_rates_per_migration_background():
    plr_areas = pd.DataFrame({"area_id": ["a", "b", "c"], "population": [2000, 4000, 3000]})
    incidents = pd.DataFrame(
        {
            "plr_id": ["a", "a", "b", "c"],
            "geo_precision": ["street", "street", "street", "place"],
            "bezirk": ["X", "X", "X", "Y"],
        }
    )
    mh = pd.Series({"a": 500, "b": 100, "c": 800})
    rates = incident_rates(incidents, plr_areas, years=2.0, mh_population=mh).set_index("area_id")
    assert rates.loc["a", "value"] == pytest.approx(0.5)
    assert rates.loc["a", "value_mh"] == pytest.approx(2.0)
    assert rates.loc["a", "mh_population"] == 500
    assert rates.loc["b", "value"] == pytest.approx(0.125)
    assert pd.isna(rates.loc["b", "value_mh"])
    assert rates.loc["c", "count"] == 0
    assert rates.loc["c", "value_mh"] == pytest.approx(0.0)


def test_incident_rates_per_non_german_citizens():
    plr_areas = pd.DataFrame(
        {"area_id": ["a", "b", "c"], "population": [2000, 4000, 2000]}
    )
    incidents = pd.DataFrame(
        {
            "plr_id": ["a", "a", "b", "c"],
            "geo_precision": ["street", "street", "street", "street"],
            "bezirk": ["X", "X", "X", "Y"],
        }
    )
    foreign = pd.Series({"a": 400, "b": 150, "c": 99})
    rates = incident_rates(
        incidents, plr_areas, years=2.0, foreign_population=foreign
    ).set_index("area_id")
    assert rates.loc["a", "value_foreign"] == pytest.approx(2.5)
    assert rates.loc["a", "foreign_population"] == 400
    assert pd.isna(rates.loc["a", "value_mh"])
    assert rates.loc["b", "value_foreign"] == pytest.approx(1 / 150 * 1000 / 2)
    assert pd.isna(rates.loc["c", "value_foreign"])


def test_with_retries_survives_dropped_connections(monkeypatch):
    monkeypatch.setattr("immo_scanner.enrich.geocode.time.sleep", lambda _: None)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")

    _with_retries(flaky, waits=(1, 1, 1))
    assert len(calls) == 3


def test_with_retries_gives_up_and_skips_client_errors(monkeypatch):
    monkeypatch.setattr("immo_scanner.enrich.geocode.time.sleep", lambda _: None)

    def always_down():
        raise httpx.ConnectError("down")

    with pytest.raises(httpx.ConnectError):
        _with_retries(always_down, waits=(1, 1))
    request = httpx.Request("GET", "https://nominatim.example/search")
    forbidden = httpx.HTTPStatusError(
        "403", request=request, response=httpx.Response(403, request=request)
    )
    calls = []

    def blocked():
        calls.append(1)
        raise forbidden

    with pytest.raises(httpx.HTTPStatusError):
        _with_retries(blocked, waits=(1, 1))
    assert len(calls) == 1


def test_window_years():
    assert window_years(date(2025, 1, 1), date(2025, 12, 31)) == pytest.approx(365 / 365.25)
