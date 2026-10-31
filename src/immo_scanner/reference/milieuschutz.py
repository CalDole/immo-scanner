"""Berlin Milieuschutz streets from the Berliner Mieterverein list.

The PDF names each protected street with the house numbers that belong to a
Milieuschutzgebiet. A listing is Yes when its house number falls in one of those
ranges, Closeby when only the street is listed, and No when the street is not.
In quarter means the address has no street and the Planungsraum overlaps a Gebiet.
"""

import logging
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import delete, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from immo_scanner.config import get_settings
from immo_scanner.db.models import Listing, MilieuschutzStreet
from immo_scanner.db.session import session_scope
from immo_scanner.reference.download import download

log = logging.getLogger(__name__)

YES = "Yes"
CLOSEBY = "Closeby"
NO = "No"
IN_QUARTER = "In quarter"
# A shared border is not enough. Same rule as the Demographics tab naming a Gebiet.
_QUARTER_OVERLAP_M2 = 500
PDF_NAME = "strassenliste-milieuschutzgebiete-berlin.pdf"
MAX_HOUSE_NUMBER = 999
_MAX_ADDRESS_SPAN = 40

# Folded PDF abbreviations and official Bezirk names, including the LOR "04 - Name" form.
BEZIRK_KEYS = {
    "charlottenburgwilmersdorf": "Charlottenburg-Wilmersdorf",
    "charlwilm": "Charlottenburg-Wilmersdorf",
    "friedrichshainkreuzberg": "Friedrichshain-Kreuzberg",
    "fhainkrzb": "Friedrichshain-Kreuzberg",
    "lichtenberg": "Lichtenberg",
    "marzahnhellersdorf": "Marzahn-Hellersdorf",
    "mitte": "Mitte",
    "neukolln": "Neukölln",
    "pankow": "Pankow",
    "reinickendorf": "Reinickendorf",
    "spandau": "Spandau",
    "steglitzzehlendorf": "Steglitz-Zehlendorf",
    "stgzehlend": "Steglitz-Zehlendorf",
    "tempelhofschoneberg": "Tempelhof-Schöneberg",
    "thfschonb": "Tempelhof-Schöneberg",
    "treptowkopenick": "Treptow-Köpenick",
    "treptkop": "Treptow-Köpenick",
}

# A house-number letter (12A, 153E) is one character, not the start of "gerade".
_LETTER = r"(?:[A-Za-z](?![A-Za-z]))?"
_RANGE_RE = re.compile(rf"(\d+)\s*{_LETTER}\s*[-–—]\s*(\d+)\s*{_LETTER}")
_SLASH_RE = re.compile(rf"(\d+)\s*{_LETTER}\s*/\s*(\d+)\s*{_LETTER}")
_SINGLE_RE = re.compile(rf"(\d+)\s*{_LETTER}")
# Neubaukompass appends " / Ortsteil (Bezirk)". A house span "27/29" has no spaces.
_DISTRICT_SUFFIX = re.compile(r"\s+/\s+.*$")
_NUMBERED_STREET = re.compile(r"(?i)^straße\s+\d+[a-z]?$")
_TRAILING_RANGE = re.compile(r"^(.*\D)\s+(\d+\s*[-–—].*)$")
_TRAILING_SINGLE = re.compile(r"^(.*\D)\s+(\d+\s*[a-z]?)$", re.IGNORECASE)
_ADDRESS_NUMBERED = re.compile(r"(?i)^(straße)\s+(\d+[a-z]?)(?:\s+(\d.*))?$")
_COVERS_ALL_RE = re.compile(
    r"alle\s+(?:haus)?nummern|ohne\s+nummern|keine\s+hausnummern",
)
_TRAILING_SPEC_RE = re.compile(
    r"(?i)\s+(?:fortlaufend\s+)?(?:alle\s+(?:haus)?nummern|ohne\s+nummern|"
    r"keine\s+hausnummern|fortlaufend)\s*$"
)
_BEZIRK_IN_TEXT_RE = re.compile(
    r"(Charl\.\s*-?\s*Wilm\.|Fhain\s*-?\s*Krzb\.|Lichtenberg|"
    r"Marzahn\s*-?\s*Hellersdorf|Neukölln|Neukoelln|Pankow|Reinickendorf|"
    r"Thf\.?\s*-?\s*Schönb\.|Spandau|Trept\.\s*-?\s*Köp\.|"
    r"Stg\s*-?\s*Zehlend\.?|Steglitz\s*-?\s*Zehlendorf|"
    r"Tempelhof\s*-?\s*Schöneberg|Treptow\s*-?\s*Köpenick|"
    r"Charlottenburg\s*-?\s*Wilmersdorf|Friedrichshain\s*-?\s*Kreuzberg|"
    r"(?<![A-Za-zÄÖÜäöüß])Mitte(?![A-Za-zÄÖÜäöüß]))",
    re.IGNORECASE,
)
_BEZIRK_AT_SQL = text(
    "SELECT bezirk FROM lor_planungsraeume "
    "WHERE ST_Contains(geom, ST_SetSRID(ST_Point(:lon, :lat), 4326)) LIMIT 1"
)
_LISTING_BEZIRKE_SQL = text(
    """
    SELECT DISTINCT ON (l.id) l.id, p.bezirk
    FROM listings l
    JOIN lor_planungsraeume p ON ST_Contains(p.geom, l.geom)
    WHERE l.geom IS NOT NULL AND p.bezirk IS NOT NULL
    ORDER BY l.id
    """
)
_QUARTER_AT_SQL = text(
    """
    SELECT 1
    FROM lor_planungsraeume p
    WHERE ST_Contains(p.geom, ST_SetSRID(ST_Point(:lon, :lat), 4326))
      AND EXISTS (
        SELECT 1 FROM milieuschutz_gebiete g
        WHERE ST_Intersects(p.geom, g.geom)
          AND ST_Area(ST_Intersection(g.geom, p.geom)::geography) >= :min_overlap
      )
    LIMIT 1
    """
)
_QUARTER_LISTINGS_SQL = text(
    """
    SELECT l.id
    FROM listings l
    JOIN lor_planungsraeume p ON ST_Contains(p.geom, l.geom)
    WHERE l.geom IS NOT NULL
      AND EXISTS (
        SELECT 1 FROM milieuschutz_gebiete g
        WHERE ST_Intersects(p.geom, g.geom)
          AND ST_Area(ST_Intersection(g.geom, p.geom)::geography) >= :min_overlap
      )
    """
)

_list_unavailable = False


@dataclass(frozen=True)
class NumberRange:
    start: int
    end: int
    parity: str = "all"  # all | even | odd

    def contains(self, number: int) -> bool:
        if number < self.start or number > self.end:
            return False
        if self.parity == "even":
            return number % 2 == 0
        if self.parity == "odd":
            return number % 2 == 1
        return True


@dataclass(frozen=True)
class StreetSegment:
    street: str
    street_key: str
    bezirk: str
    gebiet: str | None
    covers_all: bool
    ranges: tuple[NumberRange, ...]
    raw_numbers: str | None

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "StreetSegment":
        raw_ranges = record.get("ranges") or []
        ranges = tuple(
            item if isinstance(item, NumberRange) else NumberRange(
                int(item["start"]), int(item["end"]), item.get("parity") or "all"
            )
            for item in raw_ranges
        )
        return cls(
            street=record["street"],
            street_key=record["street_key"],
            bezirk=record["bezirk"],
            gebiet=record.get("gebiet"),
            covers_all=bool(record.get("covers_all")),
            ranges=ranges,
            raw_numbers=record.get("raw_numbers"),
        )


class StreetIndex:
    """Street segments keyed by the normalized street name."""

    def __init__(self, by_key: dict[str, tuple[StreetSegment, ...]]) -> None:
        self.by_key = by_key

    def __bool__(self) -> bool:
        return bool(self.by_key)

    @classmethod
    def from_records(cls, records: Iterable[Mapping[str, Any]]) -> "StreetIndex":
        grouped: dict[str, list[StreetSegment]] = defaultdict(list)
        for record in records:
            segment = StreetSegment.from_record(record)
            grouped[segment.street_key].append(segment)
        return cls({key: tuple(items) for key, items in grouped.items()})

    def status(
        self,
        address: str | None,
        district: str | None = None,
        lor_bezirk: str | None = None,
    ) -> str | None:
        """Yes, Closeby, No, or None when the address has no street (or the list is empty)."""
        if not self.by_key or not address:
            return None
        parts = address_parts(address)
        if not parts:
            return None
        bezirk = resolve_bezirk(district, address, lor_bezirk)
        found: list[str] = []
        for street, numbers, looks_like_street in parts:
            segments = self.by_key.get(street_key(street))
            if not segments:
                if looks_like_street:
                    found.append(NO)
                continue
            if bezirk:
                scoped = tuple(item for item in segments if item.bezirk == bezirk)
                if not scoped:
                    found.append(NO)
                    continue
                found.append(_status_for(scoped, numbers, bezirk_known=True))
            else:
                found.append(_status_for(segments, numbers, bezirk_known=False))
        if not found:
            return None
        if YES in found:
            return YES
        if CLOSEBY in found:
            return CLOSEBY
        return NO


def fold(value: str) -> str:
    """Letters only, case- and umlaut-insensitive, so `Str.` and `Straße` share a key."""
    value = value.casefold().replace("ß", "ss")
    value = value.replace("ä", "a").replace("ö", "o").replace("ü", "u")
    return re.sub(r"[^a-z0-9]", "", value)


def clean_street(name: str) -> str:
    name = name.replace("\xa0", " ").replace("\n", " ")
    name = re.sub(r"\s*-\s*", "-", name)
    name = re.sub(r"\s+", " ", name).strip(" ,;:")
    name = re.sub(r"(?i)\s+for+t?laufend$", "", name)
    return re.sub(r"(?i)(?<=[A-Za-zÄÖÜäöüß])str\.(?=\s|$)", "straße", name)


def street_key(name: str) -> str:
    return fold(clean_street(name))


def canonical_bezirk(value: str | None) -> str | None:
    if value is None:
        return None
    text_value = re.sub(r"^\d+\s*[-–]?\s*", "", str(value).strip())
    if not text_value:
        return None
    return BEZIRK_KEYS.get(fold(text_value))


def parenthetical_bezirk(address: str | None) -> str | None:
    """Neubaukompass writes `street, postcode Berlin / Ortsteil (Bezirk)`."""
    if not address:
        return None
    for item in reversed(re.findall(r"\(([^)]*)\)", address)):
        found = canonical_bezirk(item)
        if found:
            return found
    return None


def resolve_bezirk(
    district: str | None,
    address: str | None,
    lor_bezirk: str | None,
) -> str | None:
    return (
        canonical_bezirk(lor_bezirk)
        or canonical_bezirk(district)
        or parenthetical_bezirk(address)
    )


def parse_number_spec(spec: str) -> tuple[bool, tuple[NumberRange, ...]]:
    """House-number ranges from one PDF cell. Empty or `alle Nummern` covers every number."""
    text_value = re.sub(r"\s+", " ", spec.replace("\xa0", " ")).strip(" ,;")
    folded = text_value.casefold().replace("ß", "ss")
    covers_all = bool(_COVERS_ALL_RE.search(folded))
    ranges: list[NumberRange] = []
    range_matches = list(_RANGE_RE.finditer(text_value))
    for index, match in enumerate(range_matches):
        start, end = int(match.group(1)), int(match.group(2))
        if start > end:
            start, end = end, start
        window_end = range_matches[index + 1].start() if index + 1 < len(range_matches) else len(
            text_value
        )
        ranges.append(NumberRange(start, end, _parity(text_value[match.end() : window_end])))
    covered = [(match.start(), match.end()) for match in range_matches]
    singles = list(_SINGLE_RE.finditer(text_value))
    for index, match in enumerate(singles):
        if any(start <= match.start() and match.end() <= end for start, end in covered):
            continue
        window_end = singles[index + 1].start() if index + 1 < len(singles) else len(text_value)
        number = int(match.group(1))
        if number > MAX_HOUSE_NUMBER:
            continue
        ranges.append(NumberRange(number, number, _parity(text_value[match.end() : window_end])))
    if not text_value or not re.search(r"\d", text_value):
        covers_all = True
    return covers_all, tuple(ranges)


def split_street_and_numbers(entry: str) -> tuple[str, str]:
    entry = re.sub(r"\s+", " ", entry.replace("\xa0", " ").replace("\n", " ")).strip(" ,;")
    if ":" in entry:
        street, numbers = entry.split(":", 1)
        street, numbers = street.strip(), numbers.strip()
    else:
        match = re.match(r"^(.*?)(?:\s+)(\d.*)$", entry)
        if match:
            street, numbers = match.group(1).strip(), match.group(2).strip()
        else:
            stripped = _TRAILING_SPEC_RE.sub("", entry).strip()
            if stripped and stripped != entry:
                street, numbers = stripped, entry[len(stripped) :].strip()
            else:
                street, numbers = entry, ""
    # "Colbestraße 1-35: alle Nummern" puts the range before the colon.
    # "Straße 7: 1-9" is a numbered street, so the 7 stays in the name.
    return _detach_leading_numbers(street, numbers)


def segments_from_row(street_cell: str, bezirk_cell: str, gebiet_cell: str | None) -> list[dict]:
    """One PDF row, possibly two streets joined by ` / `, as table records."""
    bezirk = canonical_bezirk(bezirk_cell)
    if not bezirk or not street_cell:
        return []
    if "straße und hausnummern" in street_cell.casefold():
        return []
    gebiet = re.sub(r"\s+", " ", (gebiet_cell or "").replace("\n", " ")).strip() or None
    records = []
    for entry in re.split(r"\s+/\s+", street_cell.replace("\n", " ")):
        street, numbers = split_street_and_numbers(entry)
        street = clean_street(street)
        if len(street_key(street)) < 3:
            continue
        covers_all, ranges = parse_number_spec(numbers)
        records.append(
            {
                "street": street,
                "street_key": street_key(street),
                "bezirk": bezirk,
                "gebiet": gebiet,
                "covers_all": covers_all,
                "ranges": [
                    {"start": item.start, "end": item.end, "parity": item.parity}
                    for item in ranges
                ],
                "raw_numbers": numbers or None,
            }
        )
    return records


def rows_from_tables(tables: Iterable[Sequence[Sequence[object]]]) -> list[dict]:
    records = []
    for table in tables:
        for raw in table:
            cells = [_cell(value) for value in raw]
            if len(cells) < 3 or not cells[0]:
                continue
            records.extend(segments_from_row(cells[0], cells[1], cells[2]))
    return records


def rows_from_text(page_text: str) -> list[dict]:
    """Fallback when a page has no detected table: street, then a Bezirk token, then the area."""
    records = []
    for line in page_text.splitlines():
        line = re.sub(r"\s+", " ", line.replace("\xa0", " ")).strip()
        match = _BEZIRK_IN_TEXT_RE.search(line)
        if match is None or match.start() < 3:
            continue
        street = line[: match.start()].strip(" |-")
        gebiet = line[match.end() :].strip(" |-")
        if not street or "straße und hausnummern" in street.casefold():
            continue
        records.extend(segments_from_row(street, match.group(1), gebiet))
    return records


def read_pdf(path: Path) -> list[dict]:
    import pdfplumber

    records: list[dict] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables() or []
            page_rows = rows_from_tables(tables)
            if not page_rows:
                page_rows = rows_from_text(page.extract_text() or "")
            records.extend(page_rows)
    return records


def address_parts(address: str) -> list[tuple[str, tuple[int, ...], bool]]:
    """Street names and house numbers from a crawled address.

    `Ecke` yields both streets. A range such as `27/29` or `30-34` contributes every
    number in the span, so one protected number is enough for Yes.
    """
    from immo_scanner.enrich.geocode import STREET_RE

    cleaned = _DISTRICT_SUFFIX.sub("", address).strip()
    if not cleaned:
        return []
    head = cleaned.split(",")[0]
    parts = []
    for piece in re.split(r"\s+[Ee]cke\s+", head):
        numbered = _ADDRESS_NUMBERED.match(piece.strip())
        if numbered:
            street, numbers = f"Straße {numbered.group(2)}", numbered.group(3) or ""
        else:
            street, numbers = split_street_and_numbers(piece)
        street = clean_street(street)
        if len(street_key(street)) < 3:
            continue
        parts.append((street, house_numbers(numbers), STREET_RE.search(street) is not None))
    return parts


def house_numbers(spec: str) -> tuple[int, ...]:
    found: list[int] = []
    covered: list[tuple[int, int]] = []
    for match in _RANGE_RE.finditer(spec):
        _extend_span(found, int(match.group(1)), int(match.group(2)))
        covered.append((match.start(), match.end()))
    for match in _SLASH_RE.finditer(spec):
        if any(start <= match.start() and match.end() <= end for start, end in covered):
            continue
        _extend_span(found, int(match.group(1)), int(match.group(2)))
        covered.append((match.start(), match.end()))
    for match in _SINGLE_RE.finditer(spec):
        if any(start <= match.start() and match.end() <= end for start, end in covered):
            continue
        number = int(match.group(1))
        if number <= MAX_HOUSE_NUMBER:
            found.append(number)
    unique: list[int] = []
    for number in found:
        if number not in unique:
            unique.append(number)
    return tuple(unique)


def load_index(session: Session) -> StreetIndex:
    """Segments currently stored. An empty index means the list was not loaded."""
    global _list_unavailable
    if getattr(session, "begin_nested", None) is None:
        return StreetIndex({})
    try:
        with session.begin_nested():
            rows = session.scalars(select(MilieuschutzStreet)).all()
    except Exception:
        if not _list_unavailable:
            log.warning("Milieuschutz street list is not available", exc_info=True)
            _list_unavailable = True
        return StreetIndex({})
    return StreetIndex.from_records(
        {
            "street": row.street,
            "street_key": row.street_key,
            "bezirk": row.bezirk,
            "gebiet": row.gebiet,
            "covers_all": row.covers_all,
            "ranges": row.ranges,
            "raw_numbers": row.raw_numbers,
        }
        for row in rows
    )


def bezirk_at(session: Session, lat: float, lon: float) -> str | None:
    if getattr(session, "begin_nested", None) is None:
        return None
    try:
        with session.begin_nested():
            row = session.execute(_BEZIRK_AT_SQL, {"lat": lat, "lon": lon}).first()
    except Exception:
        log.debug("Planungsraum lookup for Milieuschutz failed", exc_info=True)
        return None
    return canonical_bezirk(row.bezirk) if row else None


def with_quarter(street: str | None, in_affected_plr: bool) -> str | None:
    """Street status wins. In quarter only when the street lookup found nothing."""
    if street is not None:
        return street
    return IN_QUARTER if in_affected_plr else None


def stamp_listing(
    session: Session,
    listing: Listing,
    index: StreetIndex,
    lat: float | None = None,
    lon: float | None = None,
) -> None:
    """Set `listing.milieuschutz` from its address, or In quarter from the Planungsraum.

    Does nothing while the street list is empty. A failed Planungsraum lookup leaves
    an existing In quarter value in place.
    """
    if not index:
        return
    try:
        point = _stamp_point(lat, lon, listing.geom)
        lor_bezirk = bezirk_at(session, point[0], point[1]) if point else None
        street = index.status(listing.address, listing.district, lor_bezirk)
        if street is not None:
            listing.milieuschutz = street
            return
        if point is None:
            listing.milieuschutz = None
            return
        overlap = quarter_at(session, point[0], point[1])
        if overlap is None:
            return
        listing.milieuschutz = with_quarter(None, overlap)
    except Exception:
        log.warning(
            "Milieuschutz lookup failed for listing %s",
            getattr(listing, "id", None),
            exc_info=True,
        )


def backfill(session: Session, index: StreetIndex) -> int:
    """Recompute the status of every listing. Returns how many values changed."""
    if not index:
        return 0
    bezirke = _listing_bezirke(session)
    quarters = quarter_listing_ids(session)
    changed = 0
    for listing in session.scalars(select(Listing)).all():
        street = index.status(listing.address, listing.district, bezirke.get(listing.id))
        if street is None and quarters is None:
            continue
        value = with_quarter(street, quarters is not None and listing.id in quarters)
        if listing.milieuschutz != value:
            listing.milieuschutz = value
            changed += 1
    return changed


def apply_quarter_status(session: Session) -> int:
    """Fill or clear In quarter. Yes, Closeby and No are left alone.

    Returns how many values changed. Does nothing when the Gebiet polygons
    cannot be queried.
    """
    quarters = quarter_listing_ids(session)
    if quarters is None:
        return 0
    changed = 0
    rows = session.scalars(
        select(Listing).where(
            or_(Listing.milieuschutz.is_(None), Listing.milieuschutz == IN_QUARTER)
        )
    ).all()
    for listing in rows:
        value = with_quarter(None, listing.id in quarters)
        if listing.milieuschutz != value:
            listing.milieuschutz = value
            changed += 1
    return changed


def load_milieuschutz(file: Path | None = None) -> int:
    """Download the street list, replace the table, and restamp every listing."""
    settings = get_settings()
    path = file or download(settings.milieuschutz_pdf_url, settings.reference_dir / PDF_NAME)
    records = read_pdf(path)
    if not records:
        raise RuntimeError(f"No Milieuschutz streets parsed from {path}")
    index = StreetIndex.from_records(records)
    with session_scope() as session:
        session.execute(delete(MilieuschutzStreet))
        session.execute(insert(MilieuschutzStreet), records)
        updated = backfill(session, index)
    log.info("Updated Milieuschutz on %d listings", updated)
    with session_scope() as session:
        session.execute(text("REFRESH MATERIALIZED VIEW listing_features"))
    return len(records)


def _detach_leading_numbers(street: str, numbers: str) -> tuple[str, str]:
    if _NUMBERED_STREET.match(street):
        return street, numbers
    moved = _TRAILING_RANGE.match(street)
    if moved:
        street, extra = moved.group(1).strip(), moved.group(2).strip()
        numbers = f"{extra} {numbers}".strip()
    single = _TRAILING_SINGLE.match(street)
    if single and not re.fullmatch(r"(?i)straße", single.group(1).strip()):
        street = single.group(1).strip()
        numbers = f"{single.group(2).strip()} {numbers}".strip()
    return street, numbers


def _status_for(
    segments: Sequence[StreetSegment],
    numbers: Sequence[int],
    *,
    bezirk_known: bool,
) -> str:
    if bezirk_known and any(segment.covers_all for segment in segments):
        return YES
    if numbers and any(
        item.contains(number)
        for segment in segments
        for item in segment.ranges
        for number in numbers
    ):
        return YES
    # `alle Nummern` in only one Bezirk must not mark every house in the city as Yes.
    if all(segment.covers_all for segment in segments):
        return YES
    return CLOSEBY


def _parity(window: str) -> str:
    folded = window.casefold().replace("ß", "ss")
    if "ungerade" in folded:
        return "odd"
    if re.search(r"\bgerade\b", folded):
        return "even"
    return "all"


def _extend_span(found: list[int], start: int, end: int) -> None:
    if start > end:
        start, end = end, start
    if end - start > _MAX_ADDRESS_SPAN:
        found.extend(number for number in (start, end) if number <= MAX_HOUSE_NUMBER)
        return
    found.extend(number for number in range(start, end + 1) if number <= MAX_HOUSE_NUMBER)


def _cell(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\xa0", " ").replace("\n", " ")).strip()


def quarter_at(session: Session, lat: float, lon: float) -> bool | None:
    """Whether this point's Planungsraum overlaps a Gebiet. None if that cannot be queried."""
    if getattr(session, "begin_nested", None) is None:
        return None
    try:
        with session.begin_nested():
            row = session.execute(
                _QUARTER_AT_SQL,
                {"lat": lat, "lon": lon, "min_overlap": _QUARTER_OVERLAP_M2},
            ).first()
    except Exception:
        log.debug("Planungsraum Milieuschutz overlap failed", exc_info=True)
        return None
    return row is not None


def quarter_listing_ids(session: Session) -> set[int] | None:
    """Listings in an affected Planungsraum. None when the polygons cannot be queried."""
    if getattr(session, "begin_nested", None) is None:
        return None
    try:
        with session.begin_nested():
            rows = session.execute(
                _QUARTER_LISTINGS_SQL, {"min_overlap": _QUARTER_OVERLAP_M2}
            ).all()
    except Exception:
        log.info("Planungsraum Milieuschutz overlap is not available", exc_info=True)
        return None
    return {int(row.id) for row in rows}


def _stamp_point(lat: float | None, lon: float | None, geom: object) -> tuple[float, float] | None:
    if lat is not None and lon is not None:
        return lat, lon
    if geom is None:
        return None
    return _lat_lon(geom)


def _lat_lon(geom: object) -> tuple[float, float] | None:
    from geoalchemy2.shape import to_shape

    try:
        shape = to_shape(geom)
    except Exception:
        return None
    return float(shape.y), float(shape.x)


def _listing_bezirke(session: Session) -> dict[int, str]:
    try:
        with session.begin_nested():
            rows = session.execute(_LISTING_BEZIRKE_SQL).all()
    except Exception:
        log.info("No Planungsraum bezirk for Milieuschutz; matching streets without a district")
        return {}
    found = {}
    for row in rows:
        name = canonical_bezirk(row.bezirk)
        if name:
            found[row.id] = name
    return found
