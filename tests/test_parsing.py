import pytest

from immo_scanner.crawlers.parsing import (
    parse_area,
    parse_floor,
    parse_number,
    parse_postcode,
    parse_price,
    parse_rooms,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.250.000", 1_250_000),
        ("499.000 €", 499_000),
        ("65,5", 65.5),
        ("78,4 m²", 78.4),
        ("3", 3),
        ("", None),
        (None, None),
    ],
)
def test_parse_number(text, expected):
    assert parse_number(text) == expected


def test_parse_facts():
    text = "3 Zimmer · 78,4 m² · 3. Geschoss"
    assert parse_rooms(text) == 3
    assert parse_area(text) == 78.4
    assert parse_floor(text) == 3


def test_parse_kleinanzeigen_facts():
    assert parse_area("61 m² · 3 Zi.") == 61
    assert parse_rooms("61 m² · 2,5 Zi.") == 2.5
    assert parse_rooms("sanierte 3-Zimmer-Wohnung") == 3


def test_parse_price_prefers_first_amount():
    assert parse_price("449.901€ 5.740€/m²") == 449_901


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("10965 Kreuzberg", "10965"),
        ("Steglitz, Berlin (12163)", "12163"),
        ("14195 Dahlem", "14195"),
        ("14467 Potsdam", None),
        ("no postcode", None),
    ],
)
def test_parse_postcode(text, expected):
    assert parse_postcode(text) == expected
