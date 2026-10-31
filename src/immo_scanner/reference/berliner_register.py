"""Incidents from the Berliner Register chronicle (berliner-register.de/vorfalls-chronik).

The chronicle lists 50 incidents per page, newest first, back to 2002. Each entry has a
title, date, Bezirk, free-text description and source, but no coordinates or categories.
"""

import logging
import re
import time
from datetime import datetime

import httpx
from selectolax.parser import HTMLParser, Node
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from immo_scanner.config import get_settings
from immo_scanner.db.models import RegisterIncident
from immo_scanner.db.session import session_scope

log = logging.getLogger(__name__)

CHRONICLE_URL = "https://berliner-register.de/vorfalls-chronik/"
PAGE_SIZE = 50
REQUEST_PAUSE_S = 1.0
UPDATED_COLUMNS = ("incident_date", "title", "bezirk", "description", "source")


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def _description(node: Node | None) -> str | None:
    if node is None:
        return None
    paragraphs = [_clean(p.text()) for p in node.css("p")]
    text = "\n".join(p for p in paragraphs if p) if paragraphs else _clean(node.text())
    return text or None


def parse_page(html: str) -> list[dict]:
    """Incidents on one chronicle page as RegisterIncident rows (without geocoding)."""
    rows = []
    for article in HTMLParser(html).css("#vorfalls-liste article"):
        heading = article.css_first("h3[id]")
        when = article.css_first("span.date")
        if heading is None or when is None:
            continue
        try:
            incident_date = datetime.strptime(_clean(when.text()), "%d.%m.%Y").date()
        except ValueError:
            log.warning(
                "Skipping incident %s with date %r", heading.attributes.get("id"), when.text()
            )
            continue
        meta = article.css_first("div.uppercase")
        bezirk = None
        if meta is not None:
            match = re.search(r"Bezirk:\s*(.+)", _clean(meta.text()))
            bezirk = match.group(1).strip() if match else None
        body = source = None
        for block in article.css("div"):
            classes = (block.attributes.get("class") or "").split()
            if "mt-2.5" not in classes or "uppercase" in classes:
                continue
            if "text-sm" in classes:
                source = re.sub(r"^Quelle:\s*", "", _clean(block.text())) or None
            elif body is None:
                body = block
        rows.append(
            {
                "id": heading.attributes["id"],
                "incident_date": incident_date,
                "title": _clean(heading.text()),
                "bezirk": bezirk,
                "description": _description(body),
                "source": source,
            }
        )
    return rows


def _save(rows: list[dict]) -> None:
    statement = insert(RegisterIncident).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=["id"],
        set_={name: statement.excluded[name] for name in UPDATED_COLUMNS},
    )
    with session_scope() as session:
        session.execute(statement)


def _known_ids(ids: list[str]) -> set[str]:
    with session_scope() as session:
        return set(
            session.scalars(select(RegisterIncident.id).where(RegisterIncident.id.in_(ids)))
        )


def load_berliner_register(full: bool = False, max_pages: int | None = None) -> int:
    """Scrape the chronicle into register_incidents and return the number of rows saved.

    Stops at the first page whose incidents are all stored already, unless `full`.
    """
    settings = get_settings()
    saved = 0
    page = 0
    with httpx.Client(
        headers={"User-Agent": settings.http_contact_agent}, timeout=60, follow_redirects=True
    ) as client:
        while max_pages is None or page < max_pages:
            if page:
                time.sleep(REQUEST_PAUSE_S)
            page += 1
            response = client.get(CHRONICLE_URL, params={"page": page})
            response.raise_for_status()
            rows = parse_page(response.text)
            if not rows:
                break
            known = _known_ids([row["id"] for row in rows])
            _save(rows)
            saved += len(rows)
            if page % 25 == 0:
                log.info(
                    "Berliner Register: page %d, %d incidents, back to %s",
                    page,
                    saved,
                    rows[-1]["incident_date"],
                )
            if not full and len(known) == len(rows):
                log.info("Berliner Register: page %d already stored, stopping", page)
                break
            if len(rows) < PAGE_SIZE:
                break
    log.info("Berliner Register: %d incidents from %d pages", saved, page)
    return saved
