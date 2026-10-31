"""Berlin Grundschulen and Gemeinschaftsschulen, plus their Einschulbereiche.

Sites and catchments come from the Geoportal WFS `schulen`. Offers (Ganztag,
Europa-Schule, first foreign language) come from the SenBJF Schulporträts.
The catchment code is not the Berlin school number, so a polygon is linked to
the public school that sits inside it.
"""

import logging
import re
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd
from selectolax.parser import HTMLParser
from sqlalchemy import delete, text

from immo_scanner.config import get_settings
from immo_scanner.db.models import Einschulbereich, Grundschule, Schule
from immo_scanner.db.repository import point
from immo_scanner.db.session import session_scope
from immo_scanner.reference.common import find_column, to_multipolygon, to_number
from immo_scanner.reference.download import download

log = logging.getLogger(__name__)

PRIMARY_ARTS = {"Grundschule", "Gemeinschaftsschule"}
LIST_URL = "https://www.bildung.berlin.de/Schulverzeichnis/SchulListe.aspx"
PORTRAIT_URL = "https://www.bildung.berlin.de/Schulverzeichnis/Schulportrait.aspx?IDSchulzweig={}"
PORTRAIT_PAUSE_S = 0.4
# A site that sits just outside its polygon, in the same Bezirk, still belongs to it.
CATCHMENT_EDGE_M = 400

_BSN = re.compile(r"\b(\d{2}[A-Z]\d{2})\b")
_LIST_ROW = re.compile(
    r'href="Schulportrait\.aspx\?IDSchulzweig=(\d+)".*?'
    r'<span id="DataListSchulen_lblSchulart_\d+">([^<]*)</span>',
    re.S,
)
_GGB = re.compile(r"Gebundener Ganztagbetrieb|\bGGB\b", re.I)
_OGB = re.compile(r"Offene Ganztagbetreuung|\bOGB\b", re.I)
_SESB = re.compile(
    r"Staatliche Europa-Schule Berlin(?:\s+Deutsch\s*[/\-]\s*([A-Za-zÄÖÜäöüß]+))?",
    re.I,
)
_BILINGUAL = re.compile(r"bilingual|zweisprachig", re.I)
_FOREIGN = re.compile(r"(?:1\.\s*)?Fremdsprache\s+([^,]+)", re.I)
_STUDENTS = re.compile(r"schüler|schueler", re.I)
_TEACHERS = re.compile(r"lehrkr|lehrer", re.I)


def read_wfs(path: Path) -> gpd.GeoDataFrame:
    """Read a Geoportal GeoJSON. Coordinates are longitude/latitude."""
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs(4326)
    return gdf.to_crs(4326)


def _text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"none", "<na>"} else text


def _address(row: pd.Series) -> str | None:
    street = " ".join(
        part for part in (_text(row.get("strasse")), _text(row.get("hausnr"))) if part
    ).strip()
    place = " ".join(
        part for part in (_text(row.get("plz")), _text(row.get("ortsteil"))) if part
    ).strip()
    joined = ", ".join(part for part in (street, place) if part)
    return joined or None


def is_public(traeger: object) -> bool:
    text = _text(traeger).lower()
    return text.startswith("öff") or text.startswith("oeff")


def school_site_rows(gdf: gpd.GeoDataFrame) -> list[dict]:
    """One row per Geoportal school site with a Berlin school number and a point.

    A repeated school number keeps the first point, matching the Sekundarschulen join.
    """
    rows = []
    seen: set[str] = set()
    for _, row in gdf.iterrows():
        bsn = _text(row.get("bsn"))
        geom = row.geometry
        if not bsn or geom is None or geom.is_empty or geom.geom_type != "Point":
            continue
        if bsn in seen:
            continue
        seen.add(bsn)
        rows.append(
            {
                "bsn": bsn,
                "name": _text(row.get("schulname")) or None,
                "schulart": _text(row.get("schulart")) or None,
                "bezirk": _text(row.get("bezirk")) or None,
                "ortsteil": _text(row.get("ortsteil")) or None,
                "geom": point(float(geom.x), float(geom.y)),
            }
        )
    return rows


def primary_school_rows(gdf: gpd.GeoDataFrame) -> list[dict]:
    """One row per Grundschule or Gemeinschaftsschule with a site."""
    rows = []
    seen: set[str] = set()
    for _, row in gdf.iterrows():
        schulart = _text(row.get("schulart"))
        bsn = _text(row.get("bsn"))
        name = _text(row.get("schulname"))
        geom = row.geometry
        if schulart not in PRIMARY_ARTS or not bsn or not name or geom is None or geom.is_empty:
            continue
        if bsn in seen:
            continue
        seen.add(bsn)
        rows.append(
            {
                "bsn": bsn,
                "name": name,
                "schulart": schulart,
                "traeger": _text(row.get("traeger")) or None,
                "address": _address(row),
                "bezirk": _text(row.get("bezirk")) or None,
                "ortsteil": _text(row.get("ortsteil")) or None,
                "school_year": _text(row.get("schuljahr")) or None,
                "ganztag": None,
                "europa_schule": False,
                "partner_language": None,
                "first_foreign_language": None,
                "bilingual": False,
                "students": None,
                "teachers": None,
                "geom": point(float(geom.x), float(geom.y)),
                "_point": geom,
            }
        )
    return rows


def portrait_ids(html: str) -> list[tuple[str, str]]:
    """Portrait id and Schulart from the Schulverzeichnis list."""
    found = []
    seen: set[str] = set()
    for portrait_id, schulart in _LIST_ROW.findall(html):
        art = re.sub(r"\s+", " ", schulart).strip()
        if art not in PRIMARY_ARTS or portrait_id in seen:
            continue
        seen.add(portrait_id)
        found.append((portrait_id, art))
    return found


def _span(tree: HTMLParser, element_id: str) -> str:
    node = tree.css_first(f"#{element_id}")
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.text(separator=" ", strip=True)).strip()


def parse_offers(offers: str, languages: str) -> dict:
    """Ganztag, Europa-Schule and the first foreign language from a portrait."""
    ganztag = None
    if _GGB.search(offers):
        ganztag = "gebunden"
    elif _OGB.search(offers):
        ganztag = "offen"
    sesb = _SESB.search(offers)
    partner = sesb.group(1) if sesb and sesb.group(1) else None
    if partner:
        partner = partner.strip(" -/")
    foreign = None
    match = _FOREIGN.search(languages)
    if match:
        foreign = match.group(1).strip(" -") or None
    europa = sesb is not None
    return {
        "ganztag": ganztag,
        "europa_schule": europa,
        "partner_language": partner,
        "first_foreign_language": foreign,
        "bilingual": europa or _BILINGUAL.search(offers) is not None,
    }


def parse_portrait(html: str) -> dict | None:
    """BSN and offers from one Schulporträt. None when the page has no school number."""
    tree = HTMLParser(html)
    title = _span(tree, "ContentPlaceHolderMenuListe_lblSchulname")
    match = _BSN.search(title)
    if match is None:
        return None
    offers = parse_offers(
        _span(tree, "ContentPlaceHolderMenuListe_lblAngebote"),
        _span(tree, "ContentPlaceHolderMenuListe_lblSprachen"),
    )
    name = title[: match.start()].strip(" -")
    return {"bsn": match.group(1), "portrait_name": name or None, **offers}


def merge_offers(base: dict, extra: dict) -> dict:
    """Combine branches of one school. A bound all-day branch wins over an open one."""
    if extra.get("ganztag") == "gebunden" or base.get("ganztag") == "gebunden":
        if extra.get("ganztag") or base.get("ganztag"):
            base["ganztag"] = "gebunden"
    elif extra.get("ganztag") == "offen":
        base["ganztag"] = "offen"
    if extra.get("europa_schule"):
        base["europa_schule"] = True
    if extra.get("partner_language") and not base.get("partner_language"):
        base["partner_language"] = extra["partner_language"]
    if extra.get("bilingual"):
        base["bilingual"] = True
    if extra.get("first_foreign_language") and not base.get("first_foreign_language"):
        base["first_foreign_language"] = extra["first_foreign_language"]
    return base


def apply_headcounts(rows: list[dict], frame: pd.DataFrame) -> int:
    """Fill students and teachers when the workbook has a school number and both counts.

    Returns how many schools were updated. A directory workbook without those
    columns updates nothing.
    """
    bsn_col = find_column(frame.columns, r"^bsn$|schulnummer|schul.?nr")
    students_col = find_column(frame.columns, _STUDENTS.pattern, exclude=r"pro|je |quote")
    teachers_col = find_column(frame.columns, _TEACHERS.pattern, exclude=r"pro|je |quote")
    if bsn_col is None or students_col is None or teachers_col is None:
        log.info(
            "Headcount workbook has no BSN, student and teacher columns (%s)",
            list(frame.columns),
        )
        return 0
    by_bsn = {row["bsn"]: row for row in rows}
    updated = 0
    students = to_number(frame[students_col])
    teachers = to_number(frame[teachers_col])
    for index, bsn in enumerate(frame[bsn_col].map(_text)):
        row = by_bsn.get(bsn)
        if row is None:
            continue
        student_count = students.iloc[index]
        teacher_count = teachers.iloc[index]
        if pd.isna(student_count) and pd.isna(teacher_count):
            continue
        if pd.notna(student_count):
            row["students"] = int(round(float(student_count)))
        if pd.notna(teacher_count):
            row["teachers"] = int(round(float(teacher_count)))
        updated += 1
    return updated


def _catchment_frame(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    frame = gdf.copy()
    if "esb" not in frame.columns:
        raise ValueError(f"Einschulbereiche layer has no esb column ({list(frame.columns)})")
    frame["esb"] = frame["esb"].map(_text)
    frame["bezirk"] = frame["bezname"].map(_text) if "bezname" in frame.columns else None
    frame = frame[frame["esb"].ne("") & frame.geometry.notna() & ~frame.geometry.is_empty]
    return frame.drop_duplicates("esb")


def assign_catchment_schools(
    catchments: gpd.GeoDataFrame, schools: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    """Link each polygon to the public primary school inside it.

    A site just outside the boundary, in the same Bezirk, is used when nothing
    lies inside. Each school is used at most once; the smaller polygon wins.
    """
    areas = _catchment_frame(catchments)
    areas["bsn"] = None
    candidates = schools
    if "schulart" in candidates.columns:
        candidates = candidates[candidates["schulart"].isin(PRIMARY_ARTS)]
    public = candidates[
        candidates["traeger"].map(is_public) & candidates["bsn"].map(_text).ne("")
    ].copy()
    if public.empty or areas.empty:
        return areas
    public["bsn"] = public["bsn"].map(_text)
    if "bezirk" not in public.columns:
        public["bezirk"] = ""
    public["bezirk"] = public["bezirk"].map(_text)
    public_m = public.to_crs(25833)
    areas_m = areas.to_crs(25833)
    covered = gpd.sjoin(
        areas_m[["geometry"]],
        public_m[["geometry", "bsn"]],
        predicate="covers",
        how="inner",
    )
    hits: list[tuple[float, float, object, str]] = []
    for c_idx, row in covered.iterrows():
        school_geom = public_m.at[row["index_right"], "geometry"]
        dist = float(areas_m.at[c_idx, "geometry"].representative_point().distance(school_geom))
        hits.append((float(areas_m.at[c_idx, "geometry"].area), dist, c_idx, _text(row["bsn"])))
    hits.sort()
    used: set[str] = set()
    taken: set[object] = set()
    for _, _, c_idx, bsn in hits:
        if not bsn or c_idx in taken or bsn in used:
            continue
        areas.at[c_idx, "bsn"] = bsn
        used.add(bsn)
        taken.add(c_idx)
    unmatched = areas_m.loc[~areas_m.index.isin(taken)]
    remaining = public_m.loc[~public_m["bsn"].isin(used)]
    if not unmatched.empty and not remaining.empty:
        nearest = gpd.sjoin_nearest(
            unmatched[["geometry"]],
            remaining[["geometry", "bsn", "bezirk"]],
            how="inner",
            max_distance=CATCHMENT_EDGE_M,
            distance_col="dist",
        ).sort_values("dist")
        for c_idx, row in nearest.iterrows():
            bsn = _text(row["bsn"])
            if not bsn or c_idx in taken or bsn in used:
                continue
            bezirk = _text(areas.at[c_idx, "bezirk"])
            school_bezirk = _text(row["bezirk"])
            if bezirk and school_bezirk and school_bezirk != bezirk:
                continue
            areas.at[c_idx, "bsn"] = bsn
            used.add(bsn)
            taken.add(c_idx)
    return areas


def catchment_rows(
    areas: gpd.GeoDataFrame, school_year_by_bsn: dict[str, str | None]
) -> list[dict]:
    rows = []
    for _, row in areas.iterrows():
        bsn = _text(row.get("bsn")) or None
        rows.append(
            {
                "esb": _text(row.get("esb")),
                "bsn": bsn,
                "bezirk": _text(row.get("bezirk")) or None,
                "school_year": school_year_by_bsn.get(bsn) if bsn else None,
                "geom": to_multipolygon(row.geometry),
            }
        )
    return rows


def _offers_by_bsn(cache_dir: Path) -> dict[str, dict]:
    import httpx

    settings = get_settings()
    cache_dir.mkdir(parents=True, exist_ok=True)
    list_path = cache_dir / "schulliste.html"
    if list_path.exists():
        log.info("Using cached %s", list_path)
        listing = list_path.read_text(encoding="utf-8")
    else:
        log.info("Downloading %s", LIST_URL)
        response = httpx.get(
            LIST_URL,
            follow_redirects=True,
            timeout=120,
            headers={"User-Agent": settings.http_contact_agent},
        )
        response.raise_for_status()
        listing = response.text
        list_path.write_text(listing, encoding="utf-8")
    wanted = portrait_ids(listing)
    log.info("Schulporträts to read: %d", len(wanted))
    offers: dict[str, dict] = {}
    with httpx.Client(
        follow_redirects=True,
        timeout=60,
        headers={"User-Agent": settings.http_contact_agent},
    ) as client:
        for index, (portrait_id, _) in enumerate(wanted):
            path = cache_dir / f"{portrait_id}.html"
            if path.exists():
                html = path.read_text(encoding="utf-8")
            else:
                if index:
                    time.sleep(PORTRAIT_PAUSE_S)
                url = PORTRAIT_URL.format(portrait_id)
                try:
                    response = client.get(url)
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    log.warning("Schulporträt %s failed (%s)", portrait_id, exc)
                    continue
                html = response.text
                path.write_text(html, encoding="utf-8")
            parsed = parse_portrait(html)
            if parsed is None:
                continue
            current = offers.get(parsed["bsn"])
            offers[parsed["bsn"]] = parsed if current is None else merge_offers(current, parsed)
    return offers


def _apply_offers(rows: list[dict], offers: dict[str, dict]) -> int:
    matched = 0
    for row in rows:
        extra = offers.get(row["bsn"])
        if extra is None:
            continue
        merge_offers(row, extra)
        matched += 1
    return matched


def load_grundschulen(file: Path | None = None) -> int:
    """Replace Grundschulen, their catchments and every school site, then refresh listing features.

    `file`, when it is a spreadsheet, supplies students and teachers. The WFS
    download is cached either way. A workbook without those columns is ignored.
    `schulen` keeps one point per school number for the Sekundarschulen map.
    """
    settings = get_settings()
    schools_path = download(settings.schulen_wfs_url, settings.reference_dir / "schulen.geojson")
    catchments_path = download(
        settings.einschulbereiche_wfs_url, settings.reference_dir / "einschulbereiche.geojson"
    )
    school_gdf = read_wfs(schools_path)
    site_rows = school_site_rows(school_gdf)
    rows = primary_school_rows(school_gdf)
    offers = _offers_by_bsn(settings.reference_dir / "grundschulen")
    matched = _apply_offers(rows, offers)
    if file is not None and file.suffix.lower() in {".xls", ".xlsx", ".ods"}:
        apply_headcounts(rows, pd.read_excel(file, dtype=str))
    elif file is not None:
        log.info("Ignoring %s for headcounts; expected a spreadsheet", file)

    points = school_gdf.loc[school_gdf["bsn"].map(_text).isin({row["bsn"] for row in rows})].copy()
    points["traeger"] = points["traeger"].map(_text)
    points["bezirk"] = points["bezirk"].map(_text)
    areas = assign_catchment_schools(read_wfs(catchments_path), points)
    year_by_bsn = {row["bsn"]: row["school_year"] for row in rows}
    areas_rows = catchment_rows(areas, year_by_bsn)

    stored = [{key: value for key, value in row.items() if key != "_point"} for row in rows]
    with session_scope() as session:
        session.execute(delete(Einschulbereich))
        session.execute(delete(Grundschule))
        session.execute(delete(Schule))
        session.add_all(Grundschule(**row) for row in stored)
        session.add_all(Einschulbereich(**row) for row in areas_rows)
        session.add_all(Schule(**row) for row in site_rows)
        session.flush()
        session.execute(text("REFRESH MATERIALIZED VIEW listing_features"))
    linked = sum(row["bsn"] is not None for row in areas_rows)
    log.info(
        "Grundschulen: %d schools (%d with a portrait), %d catchments (%d linked), %d school sites",
        len(stored),
        matched,
        len(areas_rows),
        linked,
        len(site_rows),
    )
    return len(stored)
