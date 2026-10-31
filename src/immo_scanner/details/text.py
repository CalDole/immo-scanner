"""Extract property attributes from free text (descriptions, page text).

Used to fill gaps left by the portals' structured fields. Matches are heuristics: a
feature is True if mentioned without a negation ("kein Aufzug"), False if only mentioned
negated, and None if not mentioned at all.
"""

import re
from datetime import date

from immo_scanner.crawlers.parsing import parse_number
from immo_scanner.details.model import DetailData

YEAR = r"((?:18|19|20)\d{2})"
GAP = r"[^\d\n]{0,40}?"
NEGATION_RE = re.compile(r"\b(?:kein|keine|keinen|keiner|ohne|nicht)\b[^.,;\n]{0,25}$", re.I)

LABELED = {
    "year_built": re.compile(r"Baujahr\s*[:|]?\s*(?:ca\.\s*)?" + YEAR, re.I),
    "floor": re.compile(
        r"\b(?:Etage|Geschoss|Stockwerk)\s*[:|]?\s*(\d{1,2})(?!\d)\.?"
        r"(?:\s*(?:von|/)\s*(\d{1,2})(?!\d))?",
        re.I,
    ),
    "heating_type": re.compile(r"Heizungsart\s*[:|]?\s*([^\n|:]{3,60})", re.I),
    "energy_source": re.compile(
        r"(?:Wesentliche\s+)?Energieträger\s*[:|]?\s*([^\n|:]{2,60})", re.I
    ),
    "last_renovation_year": re.compile(
        r"(?:Letzte\s+)?Modernisierung\s*(?:/\s*Sanierung)?\s*[:|]?\s*(?:zuletzt\s+)?" + YEAR,
        re.I,
    ),
}

BUILT_RES = (
    re.compile(r"(?:Baujahr|Bj\.|erbaut|errichtet|gebaut)" + GAP + YEAR, re.I),
    re.compile(YEAR + r"\s+(?:erbaut|errichtet|gebaut)", re.I),
)
RENOVATION_WORDS = (
    r"(?:kern)?(?:saniert|modernisiert|renoviert|Sanierung|Modernisierung|Renovierung)"
)
RENOVATION_RES = (
    re.compile(RENOVATION_WORDS + GAP + YEAR, re.I),
    re.compile(YEAR + r"[^\d\n]{0,25}?" + RENOVATION_WORDS, re.I),
)
ORDINAL_FLOOR_RE = re.compile(
    r"\b(\d{1,2})\.\s*(?:Obergeschoss|OG\b|Etage|Stock\b|Stockwerk)", re.I
)

HEATING_TYPES = (
    ("Fernwärme", r"Fernwärme|Fernheizung"),
    ("Fußbodenheizung", r"Fu(?:ß|ss)bodenheizung"),
    ("Zentralheizung", r"Zentralheizung"),
    ("Etagenheizung", r"Etagenheizung|Gastherme"),
    ("Wärmepumpe", r"Wärmepumpe"),
    ("Blockheizkraftwerk", r"Blockheizkraftwerk|\bBHKW\b"),
    ("Ofenheizung", r"Ofenheizung|Kachelofen"),
    ("Nachtspeicherofen", r"Nachtspeicher"),
)
ENERGY_SOURCES = (
    ("Gas", r"\bGas(?:heizung|therme|etagenheizung|zentralheizung)?\b|Erdgas"),
    ("Öl", r"\bÖl(?:heizung)?\b|Heizöl"),
    ("Fernwärme", r"Fernwärme"),
    ("Strom", r"Nachtspeicher|Elektroheizung"),
    ("Pellets", r"Pellet"),
    ("Erdwärme", r"Erdwärme|Geothermie"),
    ("Luftwärme", r"Luft[- ]?(?:/\s*)?Wasser[- ]?Wärmepumpe|Luftwärmepumpe"),
    ("Solar", r"Solarthermie"),
)
FEATURES = {
    "has_elevator": re.compile(r"Aufzug|Fahrstuhl|Personenaufzug|\bLift\b"),
    "has_cellar": re.compile(r"\bKeller(?!geschoss)"),
    "has_parking": re.compile(
        r"Tiefgarage|\bGarage|(?<![Ff]ahrrad)(?<![Ff]ahrrad-)(?<!Kinderwagen)[Ss]tellpl[aä]tz|Carport"
    ),
    "has_balcony": re.compile(r"[Bb]alkon|Loggia"),
}
GUEST_TOILET_RE = re.compile(r"G(?:ä|ae)ste[- ]?(?:WC|Toilette)", re.I)
# Flags the brokers' prose is searched for when their structured data is silent.
TEXT_FEATURES = {
    **FEATURES,
    "has_terrace": re.compile(r"[Tt]errasse(?!nwohnung)"),
    "has_garden": re.compile(
        r"\bGarten\b|Gartenanteil|Gartennutzung|Privatgarten|Mietergarten|Vorgarten"
    ),
    "has_fitted_kitchen": re.compile(r"Einbauküche|\bEBK\b"),
    "has_guest_toilet": GUEST_TOILET_RE,
    "has_monument_protection": re.compile(r"Denkmalschutz|denkmalgeschützt|Baudenkmal"),
}
# Broker prose uses non-breaking hyphens ("3‑Zimmer‑Wohnung") and spaces.
TYPOGRAPHY = str.maketrans({"\u2011": "-", "\u00ad": None, "\u00a0": " ", "\u202f": " "})
NUMBER_WORDS = {"zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "2": 2, "3": 3, "4": 4, "5": 5}
BALCONY_COUNT_RE = re.compile(
    r"\b(zwei|drei|vier|fünf|[2-5])\s+(?:[\wäöüß-]+\s+){0,2}?(?:Balkone|Balkonen|Loggien)\b",
    re.I,
)
PLURAL_BALCONY_RE = re.compile(r"Balkone\b|Balkonen\b|Loggien\b", re.I)


def parse_kwh(value: str | None) -> float | None:
    """Energy values use a decimal point ('116.1'), unlike the German prices."""
    if value and re.fullmatch(r"\d+\.\d+", value.strip()):
        return float(value)
    return parse_number(value)


def feature(text: str, pattern: re.Pattern[str]) -> bool | None:
    found = None
    for match in pattern.finditer(text):
        if NEGATION_RE.search(text[max(0, match.start() - 40) : match.start()]):
            found = False if found is None else found
        else:
            return True
    return found


def _names(text: str, table: tuple[tuple[str, str], ...]) -> str | None:
    names = [name for name, pattern in table if re.search(pattern, text, re.I)]
    return ", ".join(names) or None


def _plausible_year(value: str, latest: int) -> int | None:
    year = int(value)
    return year if 1800 <= year <= latest else None


def extract_labeled(text: str) -> DetailData:
    """Label/value pairs as shown in fact lists: 'Baujahr 1900', 'Etage 3 von 5'."""
    this_year = date.today().year
    data = DetailData()
    if match := LABELED["year_built"].search(text):
        data.year_built = _plausible_year(match.group(1), this_year + 5)
    if match := LABELED["floor"].search(text):
        data.floor = int(match.group(1))
        data.total_floors = int(match.group(2)) if match.group(2) else None
    if match := LABELED["heating_type"].search(text):
        data.heating_type = match.group(1).strip(" -")
    if match := LABELED["energy_source"].search(text):
        data.energy_source = match.group(1).strip(" -")
    if match := LABELED["last_renovation_year"].search(text):
        data.last_renovation_year = _plausible_year(match.group(1), this_year + 1)
    return data


def extract_from_text(text: str | None) -> DetailData:
    if not text:
        return DetailData()
    this_year = date.today().year
    data = extract_labeled(text)

    if data.year_built is None:
        years = [
            year
            for pattern in BUILT_RES
            for match in pattern.finditer(text)
            if (year := _plausible_year(match.group(1), this_year + 5))
        ]
        data.year_built = min(years) if years else None
    if data.last_renovation_year is None:
        years = [
            year
            for pattern in RENOVATION_RES
            for match in pattern.finditer(text)
            if (year := _plausible_year(match.group(1), this_year + 1))
            and (data.year_built is None or year > data.year_built)
        ]
        data.last_renovation_year = max(years) if years else None
    if data.floor is None and (match := ORDINAL_FLOOR_RE.search(text)):
        data.floor = int(match.group(1))

    data.heating_type = data.heating_type or _names(text, HEATING_TYPES)
    data.energy_source = data.energy_source or _names(text, ENERGY_SOURCES)
    for name, pattern in FEATURES.items():
        setattr(data, name, feature(text, pattern))

    data.raw["balcony_plural"] = bool(PLURAL_BALCONY_RE.search(text))
    if match := BALCONY_COUNT_RE.search(text):
        data.balcony_count = NUMBER_WORDS[match.group(1).lower()]
    elif data.has_balcony and not data.raw["balcony_plural"]:
        data.balcony_count = 1
    elif data.has_balcony is False:
        data.balcony_count = 0
    return data
