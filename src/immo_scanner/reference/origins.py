"""Residents by origin per Planungsraum and date (Statistischer Bericht A I 16, from 2021).

Each half-yearly edition has T1 (residents with/without migration background) and T4
(residents with migration background by origin). Shares are percent of all residents. The
Gini-Simpson diversity index uses the same origin categories on every date, so the change in
T4's country list at 31.12.2025 does not shift it.
"""

import logging
import re
import time
from datetime import date
from pathlib import Path

import httpx
import pandas as pd
from sqlalchemy import delete

from immo_scanner.config import get_settings
from immo_scanner.db.models import PlrOriginStat
from immo_scanner.db.session import session_scope

log = logging.getLogger(__name__)

SOURCE = "A I 16"
FIRST_YEAR = 2021
MIN_RESIDENTS = 500
FILE_RE = re.compile(r"SB_A01-16-00_(\d{4})h0([12])_BE\.xlsx")

LIBRARY_URL = "https://www.statistischebibliothek.de/mir/"
LIBRARY_SERIES = "BBSerie_mods_00001151"

EU_COUNTRIES = [
    "france",
    "greece",
    "italy",
    "austria",
    "spain",
    "poland",
    "bulgaria",
    "romania",
    "croatia",
]
# Countries listed in every edition since 2021; the diversity categories.
DIVERSITY_COUNTRIES = EU_COUNTRIES + [
    "uk",
    "russia",
    "ukraine",
    "kazakhstan",
    "turkey",
    "iran",
    "lebanon",
    "syria",
    "vietnam",
    "usa",
]

# Normalised T4 header -> (origin_key, kind, label)
T4_COLUMNS: dict[str, tuple[str, str, str]] = {
    "insgesamt": ("mh_t4", "total", "Migration background (T4)"),
    "europäischeunioneu": ("eu", "group", "European Union"),
    "frankreich": ("france", "country", "France"),
    "griechenland": ("greece", "country", "Greece"),
    "italien": ("italy", "country", "Italy"),
    "österreich": ("austria", "country", "Austria"),
    "spanien": ("spain", "country", "Spain"),
    "polen": ("poland", "country", "Poland"),
    "bulgarien": ("bulgaria", "country", "Bulgaria"),
    "rumänien": ("romania", "country", "Romania"),
    "kroatien": ("croatia", "country", "Croatia"),
    "vereinigteskönigreich": ("uk", "country", "United Kingdom"),
    "ehemaligesjugoslawien": ("yugo", "group", "Former Yugoslavia"),
    "bosnienundherzegowina": ("bosnia", "country", "Bosnia and Herzegovina"),
    "serbien": ("serbia", "country", "Serbia"),
    "ehemaligesowjetunion": ("soviet", "group", "Former Soviet Union"),
    "russischeföderation": ("russia", "country", "Russia"),
    "ukraine": ("ukraine", "country", "Ukraine"),
    "kasachstan": ("kazakhstan", "country", "Kazakhstan"),
    "islamischeländeroiceinschlsyrien": ("oic", "group", "Islamic countries (OIC)"),
    "türkei": ("turkey", "country", "Turkey"),
    "afghanistan": ("afghanistan", "country", "Afghanistan"),
    "iran": ("iran", "country", "Iran"),
    "arabischeländereinschlsyrien": ("arab", "group", "Arab countries"),
    "libanon": ("lebanon", "country", "Lebanon"),
    "syrien": ("syria", "country", "Syria"),
    "irak": ("iraq", "country", "Iraq"),
    "china": ("china", "country", "China"),
    "indien": ("india", "country", "India"),
    "vietnam": ("vietnam", "country", "Vietnam"),
    "vereinigtestaatenusa": ("usa", "country", "United States"),
    "nichteindeutigzuordenbarohneangabe": ("unassigned", "country", "Not assignable"),
}
IGNORED_HEADERS = {"", "planungsraum"}
TOTAL_LABELS = {"residents": "Residents", "mh": "Migration background"}
DIVERSITY_LABEL = "Origin diversity (Gini-Simpson)"
# Migration background minus the EU, former Yugoslavia, the UK and the US.
OUTSIDE_KEY = "mh_outside"
OUTSIDE_LABEL = "Non-Western"
MONTHS = {"juni": 6, "dezember": 12}


def normalize_header(value: str) -> str:
    """`Darunter aus Herkunftsgebiet Euro- päische Union (EU) ²` -> `europäischeunioneu`."""
    value = re.sub(r"(\w)-\s*(?=[a-zäöüß])", r"\1", value)
    value = value.lower().replace("darunter aus herkunftsgebiet", "")
    value = re.sub(r"^\s*(darunter\b|dar\.)", "", value)
    value = re.sub(r"[¹²³⁴⁵⁶⁷⁸⁹⁰]|\b\d+\b", "", value)
    return re.sub(r"[^a-zäöüß]", "", value)


def _code(value: object) -> str | None:
    if isinstance(value, int | float) and not pd.isna(value) and float(value).is_integer():
        value = int(value)
    text_value = str(value).strip()
    return text_value.zfill(2) if re.fullmatch(r"\d{1,2}", text_value) else None


def plr_ids(raw: pd.DataFrame) -> pd.Series:
    """8-digit PLR id from the four code columns, None for heading and footnote rows."""

    def row_id(row) -> str | None:
        codes = [_code(v) for v in row]
        return "".join(codes) if all(codes) else None

    return raw.iloc[:, :4].apply(row_id, axis=1)


def _numbers(series: pd.Series) -> pd.Series:
    """Counts; `–` (no residents) and empty cells become missing."""
    return pd.to_numeric(series, errors="coerce")


def _headers(raw: pd.DataFrame, ids: pd.Series) -> list[str]:
    """Each column's header cells, joined, from the `Bezirk` header row to the first data row.

    The Bezirk name heading just above the first data row is skipped, and so are the column
    numbers T1 prints under its headers.
    """
    start = raw.index[raw[0].astype(str).str.startswith("Bezirk")][0]
    first = ids.first_valid_index()
    block = raw.loc[start : first - 1]
    cells = block.iloc[:, 4:]
    last = cells.index[cells.notna().any(axis=1)][-1]
    if cells.loc[last].dropna().astype(str).nunique() == 1:
        block = block.drop(index=last)
    return [
        " ".join(str(v) for v in block[col] if pd.notna(v) and not isinstance(v, int | float))
        for col in raw.columns
    ]


def ref_date(raw: pd.DataFrame, path: Path | None = None) -> date:
    """Reference date from the sheet title (`... am 30. Juni 2021 ...`) or the file name."""
    title = str(raw.iat[0, 0])
    if m := re.search(r"am\s+(\d{1,2})\.\s*(Juni|Dezember)\s+(\d{4})", title, re.IGNORECASE):
        return date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))
    if path and (m := FILE_RE.search(path.name)):
        year, half = int(m.group(1)), int(m.group(2))
        return date(year, 6, 30) if half == 1 else date(year, 12, 31)
    raise ValueError(f"No reference date in sheet title {title!r}")


def parse_t1(raw: pd.DataFrame) -> pd.DataFrame:
    """T1 -> residents and residents with migration background, indexed by plr_id."""
    ids = plr_ids(raw)
    headers = [normalize_header(h) for h in _headers(raw, ids)]
    if "insgesamt" not in headers[4] or "migrationshintergrund" not in headers[6]:
        raise ValueError(f"Unexpected T1 layout: {headers[4]!r}, {headers[6]!r}")
    data = raw[ids.notna()]
    result = pd.DataFrame({"residents": _numbers(data[4]), "mh": _numbers(data[6])})
    result.index = pd.Index(ids[data.index], name="plr_id")
    return result


def parse_t4(raw: pd.DataFrame) -> pd.DataFrame:
    """T4 -> one count column per origin_key (plus `mh_t4`, T4's own total), by plr_id."""
    ids = plr_ids(raw)
    data = raw[ids.notna()]
    columns = {}
    for col, header in zip(raw.columns[4:], _headers(raw, ids)[4:], strict=True):
        key = normalize_header(header)
        if key in IGNORED_HEADERS:
            continue
        if key not in T4_COLUMNS:
            log.warning("Unknown T4 column %r, skipped", header)
            continue
        columns[T4_COLUMNS[key][0]] = _numbers(data[col])
    result = pd.DataFrame(columns)
    result.index = pd.Index(ids[data.index], name="plr_id")
    return result


def outside_eu_yugo(counts: pd.DataFrame) -> pd.Series | None:
    """Migration background from outside the EU, former Yugoslavia, the UK and the US.

    Former Yugoslavia includes Croatia and Slovenia, which the EU total includes as
    well (report footnote). Croatia is published on its own, so it is taken out of
    the Yugoslavia total and removed only once. Slovenia is not published separately,
    so it stays in both totals and is removed twice. None when this edition has no
    former-Yugoslavia column (dropped after 30.06.2025).
    """
    needed = ("mh", "eu", "yugo", "uk", "usa", "croatia")
    if any(column not in counts.columns for column in needed):
        return None
    yugo_outside_eu = (counts["yugo"].fillna(0) - counts["croatia"].fillna(0)).clip(lower=0)
    value = (
        counts["mh"]
        - counts["eu"].fillna(0)
        - counts["uk"].fillna(0)
        - counts["usa"].fillna(0)
        - yugo_outside_eu
    )
    return value.where(counts["mh"].notna())


def diversity(counts: pd.DataFrame) -> pd.Series:
    """Gini-Simpson index (0-1) among residents with an assignable migration background.

    Categories: the countries listed in every edition, the rest of the EU and everything else.
    `unassigned` is left out of categories and base. NULL below MIN_RESIDENTS residents.
    """
    countries = counts[DIVERSITY_COUNTRIES]
    other_eu = (counts["eu"] - counts[EU_COUNTRIES].sum(axis=1)).clip(lower=0)
    base = counts["mh"] - counts["unassigned"].fillna(0)
    other = (base - countries.sum(axis=1) - other_eu).clip(lower=0)
    categories = pd.concat([countries, other_eu.rename("other_eu"), other.rename("other")], axis=1)
    total = categories.sum(axis=1)
    index = 1 - (categories.div(total.where(total > 0), axis=0) ** 2).sum(axis=1, min_count=1)
    valid = (counts["residents"] >= MIN_RESIDENTS) & counts["mh"].notna() & (total > 0)
    return index.where(valid)


def _check_totals(t1: pd.DataFrame, t4: pd.DataFrame, when: date) -> None:
    if set(t1.index) != set(t4.index):
        log.warning("%s: T1 and T4 list different Planungsräume", when)
    diff = (t4["mh_t4"] - t1["mh"].reindex(t4.index)).dropna()
    off = diff[diff != 0]
    if not off.empty:
        example = off.index[0]
        log.warning(
            "%s: T4 total differs from T1 migration background in %d Planungsräume "
            "(e.g. %s: T4 %d, T1 %d); using T1",
            when,
            len(off),
            example,
            t4.at[example, "mh_t4"],
            t1.at[example, "mh"],
        )


def edition_rows(t1: pd.DataFrame, t4: pd.DataFrame, when: date) -> list[dict]:
    _check_totals(t1, t4, when)
    counts = t1.join(t4.drop(columns="mh_t4"), how="left")
    residents = counts["residents"].where(counts["residents"] > 0)
    labels = {key: (kind, label) for key, kind, label in T4_COLUMNS.values()}
    labels.update({key: ("total", label) for key, label in TOTAL_LABELS.items()})
    rows = []
    for key in counts.columns:
        kind, label = labels[key]
        shares = counts[key] / residents * 100
        for plr_id, count, share in zip(counts.index, counts[key], shares, strict=True):
            rows.append(_row(plr_id, when, key, kind, label, count, share))
    for plr_id, value in diversity(counts).items():
        share = None if pd.isna(value) else value * 100
        rows.append(_row(plr_id, when, "simpson", "diversity", DIVERSITY_LABEL, None, share))
    outside = outside_eu_yugo(counts)
    if outside is not None:
        shares = outside / residents * 100
        for plr_id, count, share in zip(counts.index, outside, shares, strict=True):
            rows.append(_row(plr_id, when, OUTSIDE_KEY, "group", OUTSIDE_LABEL, count, share))
    return rows


def _row(plr_id, when, key, kind, label, count, share) -> dict:
    return {
        "plr_id": plr_id,
        "ref_date": when,
        "origin_key": key,
        "kind": kind,
        "count": None if count is None or pd.isna(count) else int(count),
        "share": None if share is None or pd.isna(share) else round(float(share), 2),
        "label": label,
        "source": SOURCE,
    }


def workbook_rows(path: Path) -> list[dict]:
    sheets = pd.read_excel(path, sheet_name=["T1", "T4"], header=None)
    when = ref_date(sheets["T1"], path)
    return edition_rows(parse_t1(sheets["T1"]), parse_t4(sheets["T4"]), when)


# --- Download from the Statistische Bibliothek --------------------------------------------------


def _wanted(name: str) -> bool:
    m = FILE_RE.fullmatch(name)
    return bool(m) and int(m.group(1)) >= FIRST_YEAR


def _get(client: httpx.Client, url: str, **kwargs) -> httpx.Response:
    # The library's server drops connections now and then.
    for attempt in range(4):
        try:
            response = client.get(url, **kwargs)
            response.raise_for_status()
            return response
        except (httpx.TransportError, httpx.HTTPStatusError):
            if attempt == 3:
                raise
            time.sleep(2 + 3 * attempt)
    raise AssertionError


def _solr(client: httpx.Client, query: str, fields: str) -> list[dict]:
    params = {"q": query, "rows": 500, "fl": fields, "wt": "json"}
    return _get(client, LIBRARY_URL + "servlets/solr/select", params=params).json()["response"][
        "docs"
    ]


def download_reports(target: Path) -> None:
    """Fetch every A I 16 Excel edition from FIRST_YEAR on that is not in `target` yet.

    The AfS website only links the two latest editions; the Statistische Bibliothek archives
    all of them.
    """
    target.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": get_settings().http_contact_agent}
    with httpx.Client(timeout=300, follow_redirects=True, headers=headers) as client:
        for edition in _solr(client, f"parent:{LIBRARY_SERIES}", "id"):
            derivates = _solr(
                client, f"returnId:{edition['id']} AND objectKind:mycorederivate", "id,maindoc"
            )
            for derivate in derivates:
                name = derivate.get("maindoc") or ""
                if not _wanted(name) or (target / name).exists():
                    continue
                url = f"{LIBRARY_URL}servlets/MCRFileNodeServlet/{derivate['id']}/{name}"
                content = _get(client, url).content
                if not content.startswith(b"PK"):
                    log.warning("%s is not an Excel file, skipped", name)
                    continue
                (target / name).write_bytes(content)
                log.info("Downloaded %s", name)


def report_files(file: Path | None = None) -> list[Path]:
    """Workbooks to load: `file` (a workbook or a folder), else the downloaded editions."""
    if file is not None and file.is_file():
        return [file]
    folder = file or get_settings().reference_dir / "ewr_lor"
    if file is None:
        try:
            download_reports(folder)
        except httpx.HTTPError as exc:
            if not any(folder.glob("SB_A01-16-00_*_BE.xlsx")):
                raise
            log.warning("Statistische Bibliothek unreachable (%s), using cached editions", exc)
    paths = sorted(p for p in folder.glob("SB_A01-16-00_*_BE.xlsx") if _wanted(p.name))
    if not paths:
        raise FileNotFoundError(f"No A I 16 workbooks from {FIRST_YEAR} on in {folder}")
    return paths


def load_origins(file: Path | None = None) -> int:
    rows = []
    for path in report_files(file):
        edition = workbook_rows(path)
        log.info("%s: %d rows", path.name, len(edition))
        rows.extend(edition)
    with session_scope() as session:
        session.execute(delete(PlrOriginStat).where(PlrOriginStat.source == SOURCE))
        for start in range(0, len(rows), 5000):
            session.execute(PlrOriginStat.__table__.insert(), rows[start : start + 5000])
    return len(rows)
