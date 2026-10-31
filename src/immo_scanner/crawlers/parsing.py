"""Helpers for turning German listing text into numbers."""

import re

# Berlin postcodes are 10115-14199; 144xx+ is Potsdam and surroundings.
POSTCODE_RE = re.compile(r"\b(1[0-3]\d{3}|14[01]\d{2})\b")
NUMBER_RE = r"(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?)"
PRICE_RE = re.compile(NUMBER_RE + r"\s*(?:€|EUR)", re.IGNORECASE)
AREA_RE = re.compile(NUMBER_RE + r"\s*(?:m²|m2|qm)", re.IGNORECASE)
ROOMS_RE = re.compile(NUMBER_RE + r"[\s-]*(?:Zi\.|Zi\b|Zimmer)", re.IGNORECASE)
FLOOR_RE = re.compile(r"(\d{1,2})\.\s*(?:Geschoss|Etage|OG|Obergeschoss)", re.IGNORECASE)
GROUND_FLOOR_RE = re.compile(r"\b(?:Erdgeschoss|EG)\b")
YEAR_BUILT_RE = re.compile(r"(?:Baujahr|Bj\.?)\s*:?\s*((?:18|19|20)\d{2})", re.IGNORECASE)
HAUSGELD_RE = re.compile(r"Hausgeld\D{0,20}" + NUMBER_RE, re.IGNORECASE)


def parse_number(text: str | None) -> float | None:
    """Parse '1.250.000', '65,5' or '78.4' into a float."""
    if not text:
        return None
    match = re.search(NUMBER_RE, text)
    if not match:
        return None
    raw = match.group(1)
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):
        raw = raw.replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return None


def _first(pattern: re.Pattern[str], text: str | None) -> float | None:
    if not text:
        return None
    match = pattern.search(text)
    return parse_number(match.group(1)) if match else None


def parse_price(text: str | None) -> float | None:
    return _first(PRICE_RE, text)


def parse_area(text: str | None) -> float | None:
    return _first(AREA_RE, text)


def parse_rooms(text: str | None) -> float | None:
    return _first(ROOMS_RE, text)


def parse_floor(text: str | None) -> int | None:
    if not text:
        return None
    match = FLOOR_RE.search(text)
    if match:
        return int(match.group(1))
    return 0 if GROUND_FLOOR_RE.search(text) else None


def parse_year_built(text: str | None) -> int | None:
    if not text:
        return None
    match = YEAR_BUILT_RE.search(text)
    return int(match.group(1)) if match else None


def parse_hausgeld(text: str | None) -> float | None:
    return _first(HAUSGELD_RE, text)


def parse_postcode(text: str | None) -> str | None:
    if not text:
        return None
    match = POSTCODE_RE.search(text)
    return match.group(1) if match else None


def clean_text(text: str | None) -> str | None:
    if text is None:
        return None
    text = re.sub(r"\s+", " ", text).strip()
    return text or None
