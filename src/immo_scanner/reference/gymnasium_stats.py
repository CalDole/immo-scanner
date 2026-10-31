"""Per-Gymnasium statistics published as HTML tables on gymnasium-berlin.net.

The figures are stored one row per school so they can later be joined to the
OpenStreetMap schools used by the property overview. Nothing here is copied into
`listing_features` yet.
"""

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from selectolax.parser import HTMLParser
from sqlalchemy.dialects.postgresql import insert

from immo_scanner.config import get_settings
from immo_scanner.db.models import GymnasiumStatistic
from immo_scanner.db.session import session_scope

log = logging.getLogger(__name__)

COLUMNS = (
    "slug",
    "name",
    "abi_average",
    "abi_year",
    "abi_pass_rate_pct",
    "abi_pass_rate_year",
    "admission_places",
    "first_choices",
    "first_choice_pct",
    "admissions_year",
    "students",
    "students_year",
    "teachers",
    "teachers_year",
    "migration_background_pct",
    "migration_year",
    "type_level",
    "starts_year_5",
    "starts_year_7",
    "schnelllerner",
    "europa_schule",
    "profile_language",
    "profile_music_art",
    "profile_mint",
    "profile_sport",
)
BOOL_COLUMNS = (
    "starts_year_5",
    "starts_year_7",
    "schnelllerner",
    "europa_schule",
    "profile_language",
    "profile_music_art",
    "profile_mint",
    "profile_sport",
)
INT_COLUMNS = {
    "admission_places",
    "first_choices",
    "students",
    "teachers",
    "type_level",
}


@dataclass(frozen=True)
class StatPage:
    url: str
    cache_name: str
    year: str | None
    year_column: str | None
    columns: tuple[str, ...]


STAT_PAGES: tuple[StatPage, ...] = (
    StatPage(
        "https://www.gymnasium-berlin.net/abiturdaten/2025",
        "abi_average.html",
        "2025",
        "abi_year",
        ("abi_average",),
    ),
    StatPage(
        "https://www.gymnasium-berlin.net/abi-erfolgsquote",
        "abi_pass_rate.html",
        "2025",
        "abi_pass_rate_year",
        ("abi_pass_rate_pct",),
    ),
    StatPage(
        "https://www.gymnasium-berlin.net/nachfrage",
        "admissions.html",
        "2025",
        "admissions_year",
        ("admission_places", "first_choices", "first_choice_pct"),
    ),
    StatPage(
        "https://www.gymnasium-berlin.net/statistik",
        "students.html",
        "2024/25",
        "students_year",
        ("students",),
    ),
    StatPage(
        "https://www.gymnasium-berlin.net/statistik-lehrer",
        "teachers.html",
        "2024/25",
        "teachers_year",
        ("teachers",),
    ),
    StatPage(
        "https://www.gymnasium-berlin.net/migrationshintergrund",
        "migration.html",
        "2024/25",
        "migration_year",
        ("migration_background_pct",),
    ),
    StatPage(
        "https://www.gymnasium-berlin.net/schultypisierung",
        "type_level.html",
        None,
        None,
        ("type_level",),
    ),
)


@dataclass(frozen=True)
class MembershipPage:
    """A school is flagged when its slug appears in this page's largest table."""

    url: str
    cache_name: str
    column: str


MEMBERSHIP_PAGES: tuple[MembershipPage, ...] = (
    MembershipPage(
        "https://www.gymnasium-berlin.net/grundstaendige-gymnasien",
        "year5.html",
        "starts_year_5",
    ),
    MembershipPage(
        "https://www.gymnasium-berlin.net/schnelllerner",
        "schnelllerner.html",
        "schnelllerner",
    ),
    MembershipPage(
        "https://www.gymnasium-berlin.net/europaschulen",
        "europa.html",
        "europa_schule",
    ),
    MembershipPage(
        "https://www.gymnasium-berlin.net/sprachbetont",
        "profile_language.html",
        "profile_language",
    ),
    MembershipPage(
        "https://www.gymnasium-berlin.net/musikprofilierung",
        "profile_music.html",
        "profile_music_art",
    ),
    MembershipPage(
        "https://www.gymnasium-berlin.net/kunstprofilierung",
        "profile_art.html",
        "profile_music_art",
    ),
    MembershipPage(
        "https://www.gymnasium-berlin.net/mathematisch-naturwissenschaftlich",
        "profile_mint.html",
        "profile_mint",
    ),
    MembershipPage(
        "https://www.gymnasium-berlin.net/profil-informationstechnik",
        "profile_it.html",
        "profile_mint",
    ),
    MembershipPage(
        "https://www.gymnasium-berlin.net/sportbetont",
        "profile_sport.html",
        "profile_sport",
    ),
)


def parse_de_number(text: str) -> float | None:
    """Parse a German table number: '1,66' -> 1.66 and '1.358' -> 1358."""
    cleaned = text.replace("\xa0", "").replace(" ", "").strip()
    if not cleaned or not re.search(r"\d", cleaned):
        return None
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", cleaned):
        cleaned = cleaned.replace(".", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_school_links(html: str) -> list[dict]:
    """Unique schools in the largest table. An empty icon link keeps the named one."""
    tables = HTMLParser(html).css("table")
    if not tables:
        return []
    views = [table for table in tables if "views-table" in (table.attributes.get("class") or "")]
    table = max(views or tables, key=lambda item: len(item.css("tr")))
    by_slug: dict[str, str] = {}
    for link in table.css("a"):
        href = (link.attributes.get("href") or "").strip()
        if not href.startswith("/") or href.startswith("//") or "order=" in href:
            continue
        slug = href.strip("/").split("/")[-1].split("?")[0]
        if not slug or "/" in href.strip("/"):
            continue
        name = re.sub(r"\s+", " ", link.text(strip=True)).strip()
        current = by_slug.get(slug)
        if current is None or len(name) > len(current):
            by_slug[slug] = name
    return [{"slug": slug, "name": name} for slug, name in by_slug.items()]


def _blank_row() -> dict:
    row = {column: None for column in COLUMNS}
    for column in BOOL_COLUMNS:
        row[column] = False
    return row


def parse_stat_table(html: str) -> list[dict]:
    """Rows of the largest school table: slug, name and the numeric cells after the name."""
    tables = HTMLParser(html).css("table")
    if not tables:
        raise ValueError("No table found")
    table = max(tables, key=lambda item: len(item.css("tr")))
    records = []
    for row in table.css("tr")[1:]:
        cells = row.css("td")
        name_index = None
        link = None
        for index, cell in enumerate(cells):
            candidate = cell.css_first("a")
            href = (candidate.attributes.get("href") if candidate else "") or ""
            if href.startswith("/") and "order=" not in href:
                name_index = index
                link = candidate
                break
        if link is None or name_index is None:
            continue
        slug = link.attributes["href"].strip("/").split("/")[-1].split("?")[0]
        name = re.sub(r"\s+", " ", link.text(strip=True)).strip()
        if not slug or not name:
            continue
        numbers = [
            number
            for cell in cells[name_index + 1 :]
            if (number := parse_de_number(cell.text(strip=True))) is not None
        ]
        records.append({"slug": slug, "name": name, "numbers": numbers})
    return records


def _remember(by_slug: dict[str, dict], slug: str, name: str) -> dict:
    row = by_slug.setdefault(slug, _blank_row())
    row["slug"] = slug
    if name and (row["name"] is None or len(name) > len(row["name"])):
        row["name"] = name
    if not row["name"]:
        row["name"] = slug
    return row


def build_rows(pages: dict[str, str]) -> list[dict]:
    """Merge every statistic page into one dict per school slug."""
    by_slug: dict[str, dict] = {}
    for page in STAT_PAGES:
        html = pages.get(page.cache_name)
        if not html:
            continue
        records = parse_stat_table(html)
        if not records:
            log.warning("No Gymnasium rows in %s", page.cache_name)
        for record in records:
            row = _remember(by_slug, record["slug"], record["name"])
            for column, value in zip(page.columns, record["numbers"], strict=False):
                row[column] = int(round(value)) if column in INT_COLUMNS else value
            if page.year_column and page.year:
                row[page.year_column] = page.year
    admissions = pages.get("admissions.html")
    if admissions:
        for record in parse_school_links(admissions):
            _remember(by_slug, record["slug"], record["name"])["starts_year_7"] = True
    for page in MEMBERSHIP_PAGES:
        html = pages.get(page.cache_name)
        if not html:
            continue
        records = parse_school_links(html)
        if not records:
            log.warning("No Gymnasium rows in %s", page.cache_name)
        for record in records:
            _remember(by_slug, record["slug"], record["name"])[page.column] = True
    return list(by_slug.values())


def _read_pages(cache_dir: Path) -> dict[str, str]:
    settings = get_settings()
    pages: dict[str, str] = {}
    cache_dir.mkdir(parents=True, exist_ok=True)
    with httpx.Client(
        follow_redirects=True,
        timeout=60,
        headers={"User-Agent": settings.http_contact_agent},
    ) as client:
        sources = (*STAT_PAGES, *MEMBERSHIP_PAGES)
        for index, page in enumerate(sources):
            path = cache_dir / page.cache_name
            if path.exists():
                log.info("Using cached %s", path)
                pages[page.cache_name] = path.read_text(encoding="utf-8")
                continue
            if index:
                time.sleep(1)
            log.info("Downloading %s", page.url)
            response = client.get(page.url)
            response.raise_for_status()
            path.write_text(response.text, encoding="utf-8")
            pages[page.cache_name] = response.text
    return pages


def load_gymnasium_stats() -> int:
    settings = get_settings()
    rows = build_rows(_read_pages(settings.reference_dir / "gymnasium_berlin"))
    statement = insert(GymnasiumStatistic).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=["slug"],
        set_={column: statement.excluded[column] for column in COLUMNS if column != "slug"},
    )
    with session_scope() as session:
        session.execute(statement)
    log.info(
        "Gymnasium stats: %d schools, abi %d, pass rate %d, admissions %d, "
        "students %d, teachers %d, migration %d, type level %d, "
        "year 5 %d, year 7 %d, schnelllerner %d, europa %d",
        len(rows),
        sum(row["abi_average"] is not None for row in rows),
        sum(row["abi_pass_rate_pct"] is not None for row in rows),
        sum(row["admission_places"] is not None for row in rows),
        sum(row["students"] is not None for row in rows),
        sum(row["teachers"] is not None for row in rows),
        sum(row["migration_background_pct"] is not None for row in rows),
        sum(row["type_level"] is not None for row in rows),
        sum(row["starts_year_5"] for row in rows),
        sum(row["starts_year_7"] for row in rows),
        sum(row["schnelllerner"] for row in rows),
        sum(row["europa_schule"] for row in rows),
    )
    # This derived PLR indicator needs the statistics committed above plus school and LOR
    # geometries. A missing prerequisite is reported but does not make this loader fail.
    from immo_scanner.reference.gymnasium_education import load_gymnasium_education

    load_gymnasium_education()
    return len(rows)
