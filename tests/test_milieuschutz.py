"""Milieuschutz status from street and house number, without the PDF."""

from datetime import UTC, datetime

from immo_scanner.crawlers.base import ListingData
from immo_scanner.db.models import Listing
from immo_scanner.db.repository import save_listings
from immo_scanner.reference.milieuschutz import (
    CLOSEBY,
    IN_QUARTER,
    NO,
    YES,
    StreetIndex,
    parse_number_spec,
    segments_from_row,
    with_quarter,
)


def _index(*rows: tuple[str, str, str]) -> StreetIndex:
    records = []
    for street, bezirk, gebiet in rows:
        records.extend(segments_from_row(street, bezirk, gebiet))
    return StreetIndex.from_records(records)


WESER = ("Weserstraße: 1-31, 189-217", "Neukölln", "Reuterplatz")
WESER_ALL = ("Weserstraße: 8-48 fortlaufend, alle Nummern", "Fhain-Krzb.", "Boxhagener Platz")
EVEN = ("Afrikanische Straße: 2-6 gerade", "Mitte", "Seestraße")
ADOLF_MITTE = ("Adolfstraße: 1-27 A fortlaufend", "Mitte", "Leopoldplatz")
ADOLF_STEG = ("Adolfstraße: alle Nummern", "Stg-Zehlend.", "Mittelstraße")
TWO = (
    "Anna- Mungunda- Allee: alle Hausnummern / Maji- Maji- Allee: 20",
    "Mitte",
    "Müllerstraße Nord",
)
PLACE = ("Am Bahnhof Westend", "Charl.-Wilm.", "Klausenerplatz")
ODD_EVEN = ("Wittekindstraße: 1-37 (ungerade) 2-26 (gerade)", "Thf-Schönb.", "Tempelhof")
ABBREV = ("Wenckebachstr. 1-15; 17-21", "Thf-Schönb.", "Tempelhof")
ONLY_28 = ("Beispielstraße: 28", "Pankow", "Arnimplatz")


def test_even_numbers_and_plain_ranges():
    streets = _index(EVEN, WESER)
    assert streets.status("Afrikanische Straße 4, 13353 Berlin") == YES
    assert streets.status("Afrikanische Straße 3, 13353 Berlin") == CLOSEBY
    assert streets.status("Afrikanische Straße 5, 13353 Berlin") == CLOSEBY
    assert streets.status("Afrikanische Straße 8, 13353 Berlin") == CLOSEBY
    assert streets.status("Weserstraße 10, 12047 Berlin") == YES
    assert streets.status("Weserstraße 100, 12051 Berlin") == CLOSEBY
    assert streets.status("Weserstraße, 12047 Berlin") == CLOSEBY


def test_all_numbers_cover_a_missing_house_number():
    streets = _index(WESER_ALL)
    assert streets.status("Weserstraße, 10247 Berlin") == YES
    assert streets.status("Weserstraße 99, 10247 Berlin") == YES


def test_same_street_in_two_bezirke():
    streets = _index(ADOLF_MITTE, ADOLF_STEG)
    assert streets.status("Adolfstraße 10, 13357 Berlin", district="Mitte") == YES
    assert streets.status("Adolfstraße 40, 13357 Berlin", district="Mitte") == CLOSEBY
    assert streets.status("Adolfstraße 40, 12163 Berlin", district="Steglitz-Zehlendorf") == YES
    assert streets.status("Adolfstraße 40, 13357 Berlin") == CLOSEBY
    assert streets.status("Adolfstraße 10, 13357 Berlin") == YES
    assert streets.status("Adolfstraße 10, 13187 Berlin", district="Pankow") == NO


def test_parenthetical_bezirk_and_two_streets_in_one_cell():
    streets = _index(ADOLF_MITTE, ADOLF_STEG, TWO)
    address = "Adolfstraße 40, 12163 Berlin / Steglitz (Steglitz-Zehlendorf)"
    assert streets.status(address) == YES
    assert streets.status("Maji-Maji-Allee 20, 13353 Berlin") == YES
    assert streets.status("Maji-Maji-Allee 21, 13353 Berlin") == CLOSEBY
    assert streets.status("Anna-Mungunda-Allee 5, 13353 Berlin") == YES


def test_house_number_span_abbreviation_and_place_without_numbers():
    streets = _index(ONLY_28, ABBREV, PLACE)
    assert streets.status("Beispielstraße 27/29, 10437 Berlin") == YES
    assert streets.status("Beispielstraße 30-34, 10437 Berlin") == CLOSEBY
    assert streets.status("Wenckebachstraße 3, 12103 Berlin") == YES
    assert streets.status("Wenckebachstraße 16, 12103 Berlin") == CLOSEBY
    assert streets.status("Am Bahnhof Westend, 14059 Berlin") == YES


def test_odd_and_even_ranges_on_one_line():
    streets = _index(ODD_EVEN)
    assert streets.status("Wittekindstraße 3, 12101 Berlin") == YES
    assert streets.status("Wittekindstraße 4, 12101 Berlin") == YES
    assert streets.status("Wittekindstraße 28, 12101 Berlin") == CLOSEBY
    assert streets.status("Wittekindstraße 38, 12101 Berlin") == CLOSEBY
    parsed = {
        (item.start, item.end, item.parity)
        for item in parse_number_spec("1-37 (ungerade) 2-26 (gerade)")[1]
    }
    assert (1, 37, "odd") in parsed
    assert (2, 26, "even") in parsed


def test_numbers_written_before_the_colon_stay_with_the_street_name():
    noisy = segments_from_row("Gößnerweg fortlaufend: alle Nummern", "Neukölln", "Rixdorf")
    assert noisy[0]["street"] == "Gößnerweg"
    assert noisy[0]["covers_all"] is True
    colbe = segments_from_row(
        "Colbestraße 1-35: fortlaufend, alle Nummern", "Fhain-Krzb.", "Boxhagener Platz"
    )
    assert colbe[0]["street"] == "Colbestraße"
    assert colbe[0]["covers_all"] is True
    platz = segments_from_row("Mathilde-Jacob-Platz 1: alle Nummern", "Mitte", "Birkenstraße")
    assert platz[0]["street"] == "Mathilde-Jacob-Platz"
    assert platz[0]["covers_all"] is True
    numbered = segments_from_row("Straße 7: 1- 9 fortlaufend", "Thf-Schönb.", "Mariendorf")
    assert numbered[0]["street"] == "Straße 7"
    assert numbered[0]["covers_all"] is False
    assert {(item["start"], item["end"]) for item in numbered[0]["ranges"]} == {(1, 9)}
    weg = segments_from_row("Tharsanderweg 1: 33-49 (ungerade)", "Spandau", "Wilhelmstadt")
    assert weg[0]["street"] == "Tharsanderweg"
    covered = {(item["start"], item["end"], item["parity"]) for item in weg[0]["ranges"]}
    assert (1, 1, "all") in covered
    assert (33, 49, "odd") in covered
    streets = StreetIndex.from_records(numbered)
    assert streets.status("Straße 7 4, 12107 Berlin") == YES
    assert streets.status("Straße 7 12, 12107 Berlin") == CLOSEBY


def test_unknown_street_is_no_and_a_quarter_name_stays_empty():
    streets = _index(WESER)
    assert streets.status("Nirgendwostraße 1, 10115 Berlin") == NO
    assert streets.status("Berlin") is None
    assert streets.status("Prenzlauer Berg") is None
    assert streets.status(None) is None


def test_quarter_does_not_replace_a_street_status():
    streets = _index(WESER)
    assert with_quarter(YES, True) == YES
    assert with_quarter(NO, True) == NO
    assert with_quarter(CLOSEBY, False) == CLOSEBY
    assert with_quarter(None, True) == IN_QUARTER
    assert with_quarter(None, False) is None
    assert with_quarter(streets.status("Prenzlauer Berg"), True) == IN_QUARTER
    assert with_quarter(streets.status("Weserstraße 10, 12047 Berlin"), True) == YES


def test_corner_uses_either_street():
    streets = _index(WESER)
    assert streets.status("Weserstraße 10 Ecke Nirgendwostraße, 12047 Berlin") == YES
    assert streets.status("Nirgendwostraße Ecke Weserstraße, 12047 Berlin") == CLOSEBY


def test_save_listings_sets_milieuschutz(monkeypatch):
    streets = _index(WESER)

    class MemorySession:
        def __init__(self) -> None:
            self.added: list[object] = []

        def scalar(self, _query: object) -> None:
            return None

        def add(self, obj: object) -> None:
            self.added.append(obj)

        def flush(self) -> None:
            for obj in self.added:
                if isinstance(obj, Listing) and obj.id is None:
                    obj.id = 1

    monkeypatch.setattr("immo_scanner.reference.milieuschutz.load_index", lambda _session: streets)
    session = MemorySession()
    save_listings(
        session,
        "immoscout24",
        [
            ListingData(
                source="immoscout24",
                external_id="1",
                url="https://example.test/1",
                address="Weserstraße 10, 12047 Berlin",
            )
        ],
        datetime.now(UTC),
    )
    listing = next(obj for obj in session.added if isinstance(obj, Listing))
    assert listing.milieuschutz == YES


def test_listing_features_selects_milieuschutz():
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "src/immo_scanner/db/alembic/versions/0038_milieuschutz.py"
    )
    spec = importlib.util.spec_from_file_location("milieuschutz_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sql = module._listing_features_sql()
    assert sql.count("l.milieuschutz") == 1
