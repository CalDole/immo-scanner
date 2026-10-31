"""Fallback for ImmoScout24: parse saved-search alert emails.

Save the alert emails as .eml files (Outlook / Thunderbird / Gmail "Download message")
into IMMO_IS24_EMAIL_DIR and run `immo import-is24-emails`. Imported files are moved
into a `processed/` subfolder.
"""

import email
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.policy import default as default_policy
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import unquote

from selectolax.parser import HTMLParser, Node

from immo_scanner.crawlers.base import ListingData
from immo_scanner.crawlers.parsing import (
    clean_text,
    parse_area,
    parse_postcode,
    parse_price,
    parse_rooms,
)

log = logging.getLogger(__name__)

EXPOSE_RE = re.compile(r"immobilienscout24\.de/expose/(\d+)")
EXPOSE_URL = "https://www.immobilienscout24.de/expose/{}"


@dataclass
class EmailListings:
    path: Path
    received_at: datetime
    listings: list[ListingData]


def _expose_id(href: str) -> str | None:
    for _ in range(3):
        match = EXPOSE_RE.search(href)
        if match:
            return match.group(1)
        href = unquote(href)
    return None


def _expose_ids_in(node: Node) -> set[str]:
    ids = {_expose_id(a.attributes.get("href") or "") for a in node.css("a[href]")}
    return {i for i in ids if i}


def _card_for(anchor: Node, max_depth: int = 10) -> Node:
    current = anchor
    for _ in range(max_depth):
        parent = current.parent
        if parent is None or len(_expose_ids_in(parent)) > 1:
            # Stop before the container grows to include other listings.
            break
        current = parent
        text = current.text(separator=" ")
        if "€" in text and ("m²" in text or "Zimmer" in text):
            return current
    return current


def parse_html(html: str) -> list[ListingData]:
    tree = HTMLParser(html)
    listings: dict[str, ListingData] = {}
    for anchor in tree.css("a[href]"):
        expose_id = _expose_id(anchor.attributes.get("href") or "")
        if not expose_id or expose_id in listings:
            continue
        card = _card_for(anchor)
        text = clean_text(card.text(separator=" ")) or ""
        title = clean_text(anchor.text()) or None
        listings[expose_id] = ListingData(
            source="immoscout24",
            external_id=expose_id,
            url=EXPOSE_URL.format(expose_id),
            title=title if title and len(title) > 8 else None,
            postcode=parse_postcode(text),
            price=parse_price(text),
            living_area=parse_area(text),
            rooms=parse_rooms(text),
            raw={"parsed_from": "email"},
        )
    return list(listings.values())


def parse_text(body: str) -> list[ListingData]:
    ids = dict.fromkeys(EXPOSE_RE.findall(unquote(body)))
    return [
        ListingData(
            source="immoscout24",
            external_id=expose_id,
            url=EXPOSE_URL.format(expose_id),
            raw={"parsed_from": "email-text"},
        )
        for expose_id in ids
    ]


def parse_email_file(path: Path) -> EmailListings:
    message: EmailMessage = email.message_from_bytes(path.read_bytes(), policy=default_policy)
    try:
        received_at = parsedate_to_datetime(message["Date"])
    except (TypeError, ValueError):
        received_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=UTC)

    html_part = message.get_body(preferencelist=("html",))
    if html_part is not None:
        listings = parse_html(html_part.get_content())
    else:
        text_part = message.get_body(preferencelist=("plain",))
        listings = parse_text(text_part.get_content() if text_part else "")
    return EmailListings(path=path, received_at=received_at, listings=listings)


def parse_email_dir(folder: Path) -> list[EmailListings]:
    """Parse all .eml files, oldest first, so price history is recorded in order."""
    if not folder.exists():
        raise FileNotFoundError(f"{folder} does not exist")
    parsed = []
    for path in sorted(folder.glob("*.eml")):
        try:
            parsed.append(parse_email_file(path))
        except Exception:
            log.exception("Could not parse %s", path)
    return sorted(parsed, key=lambda item: item.received_at)


def mark_processed(item: EmailListings) -> None:
    target = item.path.parent / "processed"
    target.mkdir(exist_ok=True)
    item.path.replace(target / item.path.name)
