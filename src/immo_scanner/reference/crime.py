"""Berlin crime atlas: cases and frequency per Bezirksregion, 2016–2025.

Frequency is recorded cases per 100,000 residents registered on 30 June (Polizei Berlin,
PKS). Cases that could not be placed in a Bezirksregion are omitted; they remain inside
the Bezirk and Berlin totals published in the workbook.
"""

import logging
import re
from pathlib import Path

import pandas as pd
from sqlalchemy import delete

from immo_scanner.config import get_settings
from immo_scanner.db.models import CrimeStat
from immo_scanner.db.session import session_scope
from immo_scanner.reference.common import to_number
from immo_scanner.reference.download import download

log = logging.getLogger(__name__)

# (key, English label, header with spaces, hyphens and punctuation removed)
OFFENSES: list[tuple[str, str, str]] = [
    ("total", "All offences", "straftateninsgesamt"),
    ("robbery", "Robbery", "raub"),
    ("street_robbery", "Street and handbag robbery", "strassenraubhandtaschenraub"),
    ("assault", "Assault", "koerperverletzungeninsgesamt"),
    ("serious_assault", "Serious and dangerous assault", "gefaehrlundschwerekoerperverletzung"),
    (
        "coercion",
        "Deprivation of liberty, coercion, threats, stalking",
        "freiheitsberaubungnoetigungbedrohungnachstellung",
    ),
    ("theft", "Theft", "diebstahlinsgesamt"),
    ("car_theft", "Theft of motor vehicles", "diebstahlvonkraftwagen"),
    ("theft_from_car", "Theft from motor vehicles", "diebstahlanauskfz"),
    ("bicycle_theft", "Bicycle theft", "fahrraddiebstahl"),
    ("burglary", "Residential burglary", "wohnraumeinbruch"),
    ("arson_offences", "Fire offences", "branddelikteinsgesamt"),
    ("arson", "Arson", "brandstiftung"),
    ("damage", "Criminal damage", "sachbeschaedigunginsgesamt"),
    ("graffiti", "Criminal damage by graffiti", "sachbeschaedigungdurchgraffiti"),
    ("drugs", "Drug offences", "rauschgiftdelikte"),
    ("neighbourhood", "Neighbourhood offences", "kieztaten"),
]
OFFENSE_LABELS = {key: label for key, label, _header in OFFENSES}
_HEADER_KEYS = {
    "lorschluesselbezirksregion": "code",
    "bezeichnungbezirksregion": "name",
    **{header: key for key, _label, header in OFFENSES},
}
_SHEET_COLUMNS = ["level", "area_id", "name", "offense", "value"]
_MISSING = {"", "-", "–", "—"}


def _norm(value: object) -> str:
    text = str(value).lower()
    text = text.translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}))
    return re.sub(r"[^a-z]", "", text)


def _code(value: object) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    if re.fullmatch(r"\d+\.0", text):
        text = str(int(float(text)))
    if re.fullmatch(r"\d{1,6}", text):
        return text.zfill(6)
    return None


def _clean_name(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _metric(value: object) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip()
    if text in _MISSING:
        return None
    number = to_number(pd.Series([text])).iloc[0]
    return None if pd.isna(number) else float(number)


def _classify(code: str | None, name: str) -> tuple[str, str, str] | None:
    """(level, area_id, name) or None for cases that were not located."""
    folded = _norm(name)
    if "nichtzuzuordnen" in folded or code == "999900" or (code or "").endswith("9900"):
        return None
    if code == "999999" or "pksgesamt" in folded:
        return ("city", "berlin", "Berlin")
    if code and code.endswith("0000"):
        return ("bezirk", name, name)
    if code:
        return ("bzr", code, name)
    return None


def _header_index(table: pd.DataFrame) -> int:
    for index, row in table.iterrows():
        if any(_norm(cell).startswith("lorschluessel") for cell in row):
            return int(index)
    raise ValueError("Crime atlas sheet has no LOR-Schlüssel header row")


def _column_map(header: pd.Series) -> dict[int, str]:
    found: dict[int, str] = {}
    for index, cell in enumerate(header):
        key = _HEADER_KEYS.get(_norm(cell))
        if key and key not in found.values():
            found[index] = key
    missing = [key for key, _label, _header in OFFENSES if key not in found.values()]
    if "code" not in found.values() or "name" not in found.values() or missing:
        raise ValueError(f"Crime atlas sheet is missing columns: {', '.join(missing)}")
    return found


def parse_sheet(table: pd.DataFrame) -> pd.DataFrame:
    """One sheet (cases or frequency) to level, area_id, name, offense, value."""
    header_at = _header_index(table)
    columns = _column_map(table.iloc[header_at])
    code_at = next(index for index, key in columns.items() if key == "code")
    name_at = next(index for index, key in columns.items() if key == "name")
    records = []
    for _, row in table.iloc[header_at + 1 :].iterrows():
        area = _classify(_code(row.iloc[code_at]), _clean_name(row.iloc[name_at]))
        if area is None:
            continue
        level, area_id, name = area
        for index, offense in columns.items():
            if offense in {"code", "name"}:
                continue
            records.append(
                {
                    "level": level,
                    "area_id": area_id,
                    "name": name,
                    "offense": offense,
                    "value": _metric(row.iloc[index]),
                }
            )
    if not records:
        return pd.DataFrame(columns=_SHEET_COLUMNS)
    return pd.DataFrame(records, columns=_SHEET_COLUMNS)


def _number(value: object, digits: int | None) -> int | float | None:
    if value is None or pd.isna(value):
        return None
    number = float(value)
    if digits is None:
        return int(round(number))
    return round(number, digits)


def year_rows(cases: pd.DataFrame, rates: pd.DataFrame, year: int) -> list[dict]:
    """Join the case-count sheet and the frequency sheet for one year."""
    merged = parse_sheet(cases).merge(
        parse_sheet(rates),
        on=["level", "area_id", "offense"],
        how="outer",
        suffixes=("_cases", "_rate"),
    )
    rows = []
    for record in merged.itertuples(index=False):
        name = record.name_cases if isinstance(record.name_cases, str) else ""
        if not name and isinstance(record.name_rate, str):
            name = record.name_rate
        rows.append(
            {
                "level": record.level,
                "area_id": record.area_id,
                "year": year,
                "offense": record.offense,
                "cases": _number(record.value_cases, None),
                "rate": _number(record.value_rate, 2),
                "name": name or record.area_id,
                "label": OFFENSE_LABELS[record.offense],
            }
        )
    return rows


def workbook_rows(path: Path) -> list[dict]:
    book = pd.ExcelFile(path)
    years = sorted(
        int(match.group(1))
        for name in book.sheet_names
        if (match := re.fullmatch(r"Fallzahlen_(\d{4})", name))
    )
    if not years:
        raise ValueError(f"{path.name} has no Fallzahlen sheets")
    rows = []
    for year in years:
        rates_name = f"HZ_{year}"
        if rates_name not in book.sheet_names:
            raise ValueError(f"{path.name} has Fallzahlen_{year} but no {rates_name}")
        cases = pd.read_excel(book, f"Fallzahlen_{year}", header=None, dtype=object)
        rates = pd.read_excel(book, rates_name, header=None, dtype=object)
        edition = year_rows(cases, rates, year)
        log.info("%s: %d rows", year, len(edition))
        rows.extend(edition)
    return rows


def load_crime(file: Path | None = None) -> int:
    path = file or download(
        get_settings().crime_atlas_url,
        get_settings().reference_dir / "kriminalitaetsatlas.xlsx",
    )
    rows = workbook_rows(path)
    with session_scope() as session:
        session.execute(delete(CrimeStat))
        for start in range(0, len(rows), 5000):
            session.execute(CrimeStat.__table__.insert(), rows[start : start + 5000])
    return len(rows)
