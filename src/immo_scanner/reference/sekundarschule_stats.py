"""Per-Sekundarschule statistics published as HTML tables on sekundarschulen-berlin.de.

The figures are stored one row per school. `bsn` comes from that site's school-number
list so a row can be joined to the Geoportal school site. Nothing here is copied
into `listing_features`.
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
from immo_scanner.db.models import SekundarschuleStatistic
from immo_scanner.db.session import session_scope
from immo_scanner.reference.gymnasium_stats import parse_stat_table

log = logging.getLogger(__name__)

COLUMNS = (
    "slug",
    "bsn",
    "name",
    "abi_average",
    "abi_year",
    "abi_pass_rate_pct",
    "abi_pass_rate_year",
    "abi_pass_rate_prev_pct",
    "admission_places",
    "first_choices",
    "first_choice_pct",
    "admissions_year",
    "migration_background_pct",
    "migration_background_prev_pct",
    "migration_year",
    "type_level",
)
INT_COLUMNS = {
    "admission_places",
    "first_choices",
    "type_level",
}
_BSN = re.compile(r"\b(\d{2}[A-Z]\d{2})\b")


@dataclass(frozen=True)
class StatPage:
    url: str
    cache_name: str
    year: str | None
    year_column: str | None
    columns: tuple[str, ...]


STAT_PAGES: tuple[StatPage, ...] = (
    StatPage(
        "https://www.sekundarschulen-berlin.de/abitur/2024",
        "abi_average.html",
        "2024",
        "abi_year",
        ("abi_average",),
    ),
    StatPage(
        "https://www.sekundarschulen-berlin.de/abi-erfolgsquote",
        "abi_pass_rate.html",
        "2024",
        "abi_pass_rate_year",
        ("abi_pass_rate_pct", "abi_pass_rate_prev_pct"),
    ),
    StatPage(
        "https://www.sekundarschulen-berlin.de/nachfrage",
        "admissions.html",
        "2025/26",
        "admissions_year",
        ("admission_places", "first_choices", "first_choice_pct"),
    ),
    StatPage(
        "https://www.sekundarschulen-berlin.de/migrationshintergrund",
        "migration.html",
        "2024/25",
        "migration_year",
        ("migration_background_pct", "migration_background_prev_pct"),
    ),
    StatPage(
        "https://www.sekundarschulen-berlin.de/belastungsstufen",
        "type_level.html",
        None,
        None,
        ("type_level",),
    ),
)
NUMBERS_URL = "https://www.sekundarschulen-berlin.de/schulnummer"
NUMBERS_CACHE = "schulnummer.html"


def parse_school_numbers(html: str) -> list[dict]:
    """Berlin school number, slug and name from the Schulnummern table."""
    tables = HTMLParser(html).css("table")
    if not tables:
        return []
    table = max(tables, key=lambda item: len(item.css("tr")))
    records = []
    for row in table.css("tr")[1:]:
        cells = row.css("td")
        if len(cells) < 2:
            continue
        match = _BSN.search(cells[0].text())
        link = cells[1].css_first("a")
        href = (link.attributes.get("href") if link else "") or ""
        if match is None or not href.startswith("/") or "order=" in href:
            continue
        slug = href.strip("/").split("/")[-1].split("?")[0]
        name = re.sub(r"\s+", " ", link.text(strip=True)).strip()
        if slug and name:
            records.append({"bsn": match.group(1), "slug": slug, "name": name})
    return records


def _blank_row() -> dict:
    return {column: None for column in COLUMNS}


def _remember(by_slug: dict[str, dict], slug: str, name: str) -> dict:
    row = by_slug.setdefault(slug, _blank_row())
    row["slug"] = slug
    if name and (row["name"] is None or len(name) > len(row["name"])):
        row["name"] = name
    if not row["name"]:
        row["name"] = slug
    return row


def build_rows(pages: dict[str, str]) -> list[dict]:
    """Merge every statistic page and the school-number list into one row per slug."""
    by_slug: dict[str, dict] = {}
    for page in STAT_PAGES:
        html = pages.get(page.cache_name)
        if not html:
            continue
        records = parse_stat_table(html)
        if not records:
            log.warning("No Sekundarschule rows in %s", page.cache_name)
        for record in records:
            row = _remember(by_slug, record["slug"], record["name"])
            for column, value in zip(page.columns, record["numbers"], strict=False):
                row[column] = int(round(value)) if column in INT_COLUMNS else value
            if page.year_column and page.year and record["numbers"]:
                row[page.year_column] = page.year
    numbers = pages.get(NUMBERS_CACHE)
    if numbers:
        records = parse_school_numbers(numbers)
        if not records:
            log.warning("No Sekundarschule rows in %s", NUMBERS_CACHE)
        for record in records:
            row = _remember(by_slug, record["slug"], record["name"])
            row["bsn"] = record["bsn"]
    return list(by_slug.values())


def _read_pages(cache_dir: Path) -> dict[str, str]:
    settings = get_settings()
    pages: dict[str, str] = {}
    cache_dir.mkdir(parents=True, exist_ok=True)
    sources = [(page.url, page.cache_name) for page in STAT_PAGES]
    sources.append((NUMBERS_URL, NUMBERS_CACHE))
    with httpx.Client(
        follow_redirects=True,
        timeout=60,
        headers={"User-Agent": settings.http_contact_agent},
    ) as client:
        for index, (url, cache_name) in enumerate(sources):
            path = cache_dir / cache_name
            if path.exists():
                log.info("Using cached %s", path)
                pages[cache_name] = path.read_text(encoding="utf-8")
                continue
            if index:
                time.sleep(1)
            log.info("Downloading %s", url)
            response = client.get(url)
            response.raise_for_status()
            path.write_text(response.text, encoding="utf-8")
            pages[cache_name] = response.text
    return pages


def load_sekundarschule_stats() -> int:
    settings = get_settings()
    rows = build_rows(_read_pages(settings.reference_dir / "sekundarschulen_berlin"))
    if not rows:
        log.warning("No Sekundarschule statistics to store")
        return 0
    statement = insert(SekundarschuleStatistic).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=["slug"],
        set_={column: statement.excluded[column] for column in COLUMNS if column != "slug"},
    )
    with session_scope() as session:
        session.execute(statement)
    log.info(
        "Sekundarschule stats: %d schools, abi %d, pass rate %d, admissions %d, "
        "migration %d, type level %d, school number %d",
        len(rows),
        sum(row["abi_average"] is not None for row in rows),
        sum(row["abi_pass_rate_pct"] is not None for row in rows),
        sum(row["admission_places"] is not None for row in rows),
        sum(row["migration_background_pct"] is not None for row in rows),
        sum(row["type_level"] is not None for row in rows),
        sum(row["bsn"] is not None for row in rows),
    )
    from immo_scanner.reference.gymnasium_education import load_gymnasium_education

    load_gymnasium_education()
    return len(rows)
