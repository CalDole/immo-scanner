"""Demographic indicators per Planungsraum or Bezirk for the demographics maps.

Every loader turns its source into long-format rows of `area_indicators` and replaces the
rows previously loaded from the same source. Values are shares of residents in percent
unless the unit says otherwise.
"""

import logging
import re
import zipfile
from collections.abc import Iterable
from pathlib import Path

import geopandas as gpd
import pandas as pd
from sqlalchemy import delete, select, text

from immo_scanner.config import get_settings
from immo_scanner.db.models import AreaIndicator, LorPlanungsraum
from immo_scanner.db.session import get_engine, session_scope
from immo_scanner.reference.common import read_csv_flexible, to_number
from immo_scanner.reference.download import download
from immo_scanner.reference.lor import _plr_ids

log = logging.getLogger(__name__)

PLR_ID_RE = re.compile(r"^\d{8}$")

MSS_SOURCE = "MSS 2025"
MSS_DATE = "31.12.2024"
# MSS code -> (topic, indicator, label, unit)
MSS_INDICATORS: dict[str, tuple[str, str, str, str]] = {
    "S1": ("unemployment", "sgb2_share", "Unemployed (SGB II), share of ages 15-67", "pct"),
    "D1": ("unemployment", "sgb2_change", "Change in unemployed share 2022-2024", "pp"),
    "K 01": ("unemployment", "youth_sgb2_share", "Youth unemployment (SGB II, under 25)", "pct"),
    "K 20": ("income", "low_wage_share", "Full-time employees in the low-wage band", "pct"),
    "K 05": ("migration", "mss_total", "Migration background (all residents)", "pct"),
    "K 04": ("migration", "mss_under_18", "Migration background (under 18)", "pct"),
    "K 16": ("citizenship", "foreign_share_mss", "Foreign citizens", "pct"),
    "K 06": ("citizenship", "foreign_change", "Change in foreign share 2022-2024", "pp"),
    "K 17": ("citizenship", "non_eu_of_foreign", "Non-EU citizens among foreign citizens", "pct"),
}

ORIGIN_LABELS = {
    "EU15": "EU-15 states",
    "EU28": "EU states joined since 2004",
    "POLEN": "Poland",
    "EHEJUG": "Former Yugoslavia",
    "EHESU": "Former Soviet Union",
    "TURK": "Turkey",
    "ARAB": "Arab states",
    "SONST": "Other states",
    "NZORD": "Not assignable",
}

ZENSUS_DATE = "15.05.2022"
RELIGION_GRID = {
    "member": "Roemisch_katholisch|Evangelisch",
    "catholic": "Roemisch_katholisch",
    "protestant": "Evangelisch",
    "other_none": "Sonstige_keine_ohneAngabe",
}
RELIGION_LABELS = {
    "member": "Catholic or Protestant church member",
    "catholic": "Roman Catholic",
    "protestant": "Protestant (EKD)",
    "other_none": "Other, none or not stated",
}
CITIZENSHIP_GRID = {
    "german_zensus": "Deutschland",
    "foreign_zensus": "Ausland_Sonstige",
    "eu27": "EU27_Land",
    "other_europe": "Sonstiges_Europa",
    "rest_of_world": "Sonstige_Welt",
}
CITIZENSHIP_LABELS = {
    "german_zensus": "German citizens (Zensus)",
    "foreign_zensus": "Foreign citizens (Zensus)",
    "eu27": "EU-27 citizens",
    "other_europe": "Other European citizens",
    "rest_of_world": "Citizens of non-European states",
}

# Ordered: the first matching pattern wins, so "ohne" is tested before the certificates.
EDUCATION_COLUMNS: list[tuple[str, str, str]] = [
    (r"ohne\s+(allg|schul)|no school", "school_none", "No school certificate"),
    (r"ohne\s+(beruf|abschluss)|no vocational", "voc_none", "No vocational qualification"),
    (r"haupt|volksschul", "school_haupt", "Hauptschule / Volksschule"),
    (r"polytechn|realschul|mittler", "school_realschule", "Realschule / Mittlere Reife / POS"),
    (r"hochschulreife|abitur|fachhochschulreife", "school_abitur", "Abitur / Fachhochschulreife"),
    (r"promotion|doctor", "voc_doctorate", "Doctorate"),
    (r"lehre|berufsausbildung|apprentice", "voc_apprenticeship", "Apprenticeship"),
    (r"fachschul|meister|techniker", "voc_fachschule", "Fachschule / Meister / Techniker"),
    (
        r"hochschulabschluss|akadem|bachelor|master|diplom|universit|academic",
        "voc_academic",
        "University degree",
    ),
]
FAITH_COLUMNS: list[tuple[str, str, str]] = [
    (r"konfessionslos|keine|ohne|none|no religion", "none", "No religion"),
    (r"islam|muslim", "islam", "Islam"),
    (r"orthodox", "orthodox", "Orthodox Christian"),
    (r"j[uü]d|jew", "jewish", "Jewish"),
    (r"kathol|catholic", "catholic", "Roman Catholic"),
    (r"evangel|protestant", "protestant", "Protestant"),
]
TOTAL_COLUMN_RE = re.compile(r"insgesamt|zusammen|total|gesamt", re.IGNORECASE)


def _number(series: pd.Series) -> pd.Series:
    """Numbers from Excel cells stay as they are; strings are parsed as German numbers."""
    native = pd.to_numeric(
        series.where(series.map(lambda v: isinstance(v, int | float))), errors="coerce"
    )
    cleaned = series.astype(str).str.replace(r"[()/–]|^\s*-\s*$", "", regex=True)
    return native.fillna(to_number(cleaned))


def _row(level, area_id, topic, indicator, label, value, unit, ref_date, source) -> dict:
    return {
        "level": level,
        "area_id": area_id,
        "topic": topic,
        "indicator": indicator,
        "label": label,
        "value": None if value is None or pd.isna(value) else round(float(value), 2),
        "unit": unit,
        "ref_date": ref_date,
        "source": source,
    }


def save_indicators(rows: list[dict], sources: Iterable[str]) -> int:
    """Replace all rows previously loaded from `sources` with `rows`."""
    sources = set(sources)
    with session_scope() as session:
        session.execute(delete(AreaIndicator).where(AreaIndicator.source.in_(sources)))
        for start in range(0, len(rows), 5000):
            session.execute(AreaIndicator.__table__.insert(), rows[start : start + 5000])
    return len(rows)


# --- MSS (Monitoring Soziale Stadtentwicklung) ----------------------------------------------


def _mss_code(value: object) -> str:
    """`K04`, `K 04` and `K4` all become `K 04`; index codes like `S1` stay as they are."""
    text_value = re.sub(r"\s+", "", str(value)).upper()
    if m := re.fullmatch(r"K(\d{1,2})", text_value):
        return f"K {int(m.group(1)):02d}"
    return text_value


def parse_mss_sheet(raw: pd.DataFrame) -> pd.DataFrame:
    """MSS sheet (read with header=None) -> DataFrame indexed by plr_id with one column per code.

    The header row has "Planungsraum" in the first column and the indicator codes next to it.
    """
    header_rows = raw.index[raw[0].astype(str).str.strip().str.lower() == "planungsraum"]
    if len(header_rows) == 0:
        raise ValueError("MSS sheet has no 'Planungsraum' header row")
    codes = {
        col: _mss_code(raw.at[header_rows[0], col])
        for col in raw.columns[1:]
        if _mss_code(raw.at[header_rows[0], col]) in MSS_INDICATORS
    }
    ids = raw[0].astype(str).str.strip().str.replace(r"\.0$", "", regex=True).str.zfill(8)
    data = raw[ids.str.fullmatch(PLR_ID_RE.pattern)]
    result = pd.DataFrame({code: _number(data[col]) for col, code in codes.items()})
    result.index = ids[data.index]
    result.index.name = "plr_id"
    return result


def mss_rows(table: pd.DataFrame) -> list[dict]:
    rows = []
    for code, (topic, indicator, label, unit) in MSS_INDICATORS.items():
        if code not in table.columns:
            continue
        for plr_id, value in table[code].items():
            rows.append(
                _row("plr", plr_id, topic, indicator, label, value, unit, MSS_DATE, MSS_SOURCE)
            )
    if "K 16" in table.columns:
        for plr_id, value in table["K 16"].items():
            rows.append(
                _row(
                    "plr",
                    plr_id,
                    "citizenship",
                    "german_share_mss",
                    "German citizens",
                    100 - value,
                    "pct",
                    MSS_DATE,
                    MSS_SOURCE,
                )
            )
    return rows


def load_mss(index_file: Path | None = None, context_file: Path | None = None) -> int:
    """Unemployment, low-wage share, migration background and foreign citizens per PLR."""
    settings = get_settings()
    index_path = index_file or download(
        settings.mss_index_url, settings.reference_dir / "mss2025_index_plr.xlsx"
    )
    context_path = context_file or download(
        settings.mss_context_url, settings.reference_dir / "mss2025_context_plr.xlsx"
    )
    tables = [
        parse_mss_sheet(pd.read_excel(path, header=None)) for path in (index_path, context_path)
    ]
    return save_indicators(mss_rows(pd.concat(tables, axis=1)), [MSS_SOURCE])


# --- Einwohnerregister: foreigners and migration background ---------------------------------


def migration_rows(df: pd.DataFrame, source: str) -> list[dict]:
    """Shares from an AfS LOR matrix with `MH_E`, `HK_*` and/or `E_A` count columns."""
    ids = _plr_ids(df)
    total_col = next((c for c in ("E_E", "MH_EE", "E_A_E") if c in df.columns), None)
    mh_col = next((c for c in ("MH_E", "E_MH") if c in df.columns), None)
    foreign_col = next((c for c in ("E_A", "A_E") if c in df.columns), None)
    origin_cols = [c for c in df.columns if re.fullmatch(r"HK_\w+", c)]
    if total_col is None or not (mh_col or foreign_col or origin_cols):
        raise ValueError(
            "Expected a total (E_E) and migration (MH_E, HK_*) or foreigner (E_A) columns, "
            f"got {list(df.columns)}. Pass the matching AfS file with --file."
        )
    date = str(df["ZEIT"].iloc[0]) if "ZEIT" in df.columns else None
    total = to_number(df[total_col])

    def shares(column: str) -> pd.Series:
        return to_number(df[column]) / total.where(total > 0) * 100

    rows = []
    if mh_col:
        for plr_id, value in zip(ids, shares(mh_col), strict=True):
            rows.append(
                _row(
                    "plr",
                    plr_id,
                    "migration",
                    "total",
                    "Migration background (all residents)",
                    value,
                    "pct",
                    date,
                    source,
                )
            )
    for column in origin_cols:
        key = column.removeprefix("HK_").upper()
        label = f"Origin: {ORIGIN_LABELS.get(key, key.title())}"
        for plr_id, value in zip(ids, shares(column), strict=True):
            rows.append(
                _row(
                    "plr",
                    plr_id,
                    "migration",
                    f"origin_{key.lower()}",
                    label,
                    value,
                    "pct",
                    date,
                    source,
                )
            )
    if foreign_col:
        for plr_id, value in zip(ids, shares(foreign_col), strict=True):
            rows.append(
                _row(
                    "plr",
                    plr_id,
                    "citizenship",
                    "foreign_share",
                    "Foreign citizens (register)",
                    value,
                    "pct",
                    date,
                    source,
                )
            )
            rows.append(
                _row(
                    "plr",
                    plr_id,
                    "citizenship",
                    "german_share",
                    "German citizens (register)",
                    None if pd.isna(value) else 100 - value,
                    "pct",
                    date,
                    source,
                )
            )
    return rows


def load_migration(file: Path | None = None) -> int:
    settings = get_settings()
    path = file or download(
        settings.migration_csv_url, settings.reference_dir / "migration_lor.csv"
    )
    source = "AfS register"
    return save_indicators(migration_rows(read_csv_flexible(path), source), [source])


# --- Zensus 2022 100 m grid --------------------------------------------------------------------


def _plr_polygons() -> gpd.GeoDataFrame:
    query = "SELECT plr_id, geom FROM lor_planungsraeume"
    with get_engine().connect() as conn:
        gdf = gpd.read_postgis(text(query), conn, geom_col="geom")
    if gdf.empty:
        raise RuntimeError("No Planungsräume loaded. Run `immo load-reference lor` first.")
    return gdf.to_crs(3035)


def _grid_csv(path: Path) -> pd.DataFrame:
    """Read a Zensus grid CSV (or the zip around it) in chunks, keeping only Berlin's bbox."""
    plr = _plr_polygons()
    minx, miny, maxx, maxy = plr.total_bounds
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
            name = next((n for n in names if "100m" in n), names[0])
            handle = archive.open(name)
    else:
        handle = path.open("rb")
    chunks = []
    with handle:
        reader = pd.read_csv(handle, sep=";", dtype=str, encoding="latin-1", chunksize=500_000)
        for chunk in reader:
            x = pd.to_numeric(chunk["x_mp_100m"], errors="coerce")
            y = pd.to_numeric(chunk["y_mp_100m"], errors="coerce")
            inside = x.between(minx, maxx) & y.between(miny, maxy)
            chunks.append(chunk[inside].assign(x=x[inside], y=y[inside]))
    cells = pd.concat(chunks, ignore_index=True)
    points = gpd.GeoDataFrame(cells, geometry=gpd.points_from_xy(cells.x, cells.y), crs=3035)
    return gpd.sjoin(points, plr, predicate="within")


def grid_rows(
    cells: pd.DataFrame,
    categories: dict[str, str],
    denominator: list[str],
    topic: str,
    labels: dict[str, str],
    source: str,
) -> list[dict]:
    """Sum grid cells per PLR and turn categories into shares of the denominator columns.

    `categories` maps indicator -> column, or several columns joined by `|`. Suppressed cells
    ("–") count as zero, so the denominator is the sum of the published categories rather than
    the published total.
    """
    parts = {c for spec in categories.values() for c in spec.split("|")}
    columns = sorted(parts | set(denominator))
    values = cells[columns].apply(lambda s: pd.to_numeric(s, errors="coerce")).fillna(0)
    sums = values.assign(plr_id=cells["plr_id"].values).groupby("plr_id").sum()
    base = sums[denominator].sum(axis=1)
    rows = []
    for indicator, spec in categories.items():
        share = sums[spec.split("|")].sum(axis=1) / base.where(base > 0) * 100
        for plr_id, value in share.items():
            rows.append(
                _row(
                    "plr",
                    plr_id,
                    topic,
                    indicator,
                    labels[indicator],
                    value,
                    "pct",
                    ZENSUS_DATE,
                    source,
                )
            )
    return rows


def load_religion(file: Path | None = None) -> int:
    """Catholic, Protestant and other/none per PLR from the Zensus 2022 grid."""
    settings = get_settings()
    path = file or download(
        settings.zensus_religion_grid_url, settings.reference_dir / "zensus2022_religion.zip"
    )
    rows = grid_rows(
        _grid_csv(path),
        RELIGION_GRID,
        ["Roemisch_katholisch", "Evangelisch", "Sonstige_keine_ohneAngabe"],
        "religion",
        RELIGION_LABELS,
        "Zensus 2022",
    )
    return save_indicators(rows, ["Zensus 2022"])


def load_citizenship(file: Path | None = None) -> int:
    """German and foreign citizens by world region per PLR from the Zensus 2022 grid."""
    settings = get_settings()
    path = file or download(
        settings.zensus_citizenship_grid_url,
        settings.reference_dir / "zensus2022_citizenship_groups.zip",
    )
    source = "Zensus 2022 citizenship"
    rows = grid_rows(
        _grid_csv(path),
        CITIZENSHIP_GRID,
        ["Deutschland", "Ausland_Sonstige"],
        "citizenship",
        CITIZENSHIP_LABELS,
        source,
    )
    return save_indicators(rows, [source])


# --- User-supplied files (no machine-readable official download) -------------------------------


def _normalize_name(value: object) -> str:
    return re.sub(r"[^a-zäöüß]", "", str(value).lower())


def _bezirk_names() -> list[str]:
    with session_scope() as session:
        names = session.scalars(select(LorPlanungsraum.bezirk).distinct()).all()
    return sorted(n for n in names if n)


def match_bezirk(value: object, bezirke: list[str]) -> str | None:
    """Match a Bezirk label like `Charlottenburg-Wilm.` or `07 Tempelhof-Schöneberg`."""
    raw = re.sub(r"^\s*\d+\s*", "", str(value)).strip()
    key = _normalize_name(raw)
    if len(key) < 4:
        return None
    for name in bezirke:
        normalized = _normalize_name(name)
        if normalized == key or (raw.endswith(".") and normalized.startswith(key)):
            return name
    return None


def read_table(path: Path) -> list[pd.DataFrame]:
    """All sheets of an Excel file (header=None) or the CSV as a single headed table."""
    if path.suffix.lower() in (".xlsx", ".xls"):
        return list(pd.read_excel(path, sheet_name=None, header=None).values())
    return [read_csv_flexible(path)]


def tidy_rows(df: pd.DataFrame, level: str, topic: str, source: str) -> list[dict] | None:
    """Rows from a tidy table with area, indicator and value columns, or None if not tidy.

    The area column is `plr_id` or `bezirk`. Optional columns: label, unit, ref_date.
    """
    columns = {c.strip().lower(): c for c in df.columns if isinstance(c, str)}
    area_key = "plr_id" if level == "plr" else "bezirk"
    if not {area_key, "indicator", "value"} <= set(columns):
        return None
    bezirke = _bezirk_names() if level == "bezirk" else []
    rows = []
    for _, record in df.iterrows():
        area = str(record[columns[area_key]]).strip()
        area = area.zfill(8) if level == "plr" else match_bezirk(area, bezirke)
        if not area:
            continue
        indicator = str(record[columns["indicator"]]).strip()
        rows.append(
            _row(
                level,
                area,
                topic,
                indicator,
                str(record[columns["label"]]) if "label" in columns else indicator,
                _number(pd.Series([record[columns["value"]]])).iloc[0],
                str(record[columns["unit"]]) if "unit" in columns else "pct",
                str(record[columns["ref_date"]]) if "ref_date" in columns else None,
                source,
            )
        )
    return rows


def _sheet_title(raw: pd.DataFrame) -> str:
    """Text of the first two cells of the first column, where report tables put their title."""
    if raw.empty:
        return ""
    return " ".join(str(v) for v in raw.iloc[:2, 0] if pd.notna(v))


def _clean_header(value: str) -> str:
    """Undo Excel line-break hyphenation (`insge- samt` -> `insgesamt`) and footnote marks."""
    value = re.sub(r"(\w)-\s+(?=[a-zäöüß])", r"\1", value)
    value = re.sub(r"[¹²³⁴⁵*]", "", value)
    return re.sub(r"\s+", " ", value).strip()


def locate_table(raw: pd.DataFrame, is_key) -> tuple[list[str], pd.DataFrame]:
    """Split a raw sheet into column headers and data rows whose first cell passes `is_key`.

    The key can sit in any of the first three columns. Headers are all non-empty cells above
    the first data row joined per column, which handles multi-row Excel headers.
    """
    for key_col in raw.columns[:3]:
        mask = raw[key_col].map(lambda v: bool(is_key(v)) if pd.notna(v) else False)
        if mask.sum() >= 3:
            break
    else:
        return [], raw.iloc[0:0]
    first = mask.idxmax()
    above = raw.loc[: first - 1] if first > 0 else raw.iloc[0:0]
    headers = [
        _clean_header(" ".join(str(v) for v in above[col] if pd.notna(v) and str(v).strip()))
        for col in raw.columns
    ]
    data = raw[mask].copy()
    data.insert(0, "_key", data[key_col])
    return headers, data


def wide_bezirk_rows(
    raw: pd.DataFrame,
    patterns: list[tuple[str, str, str]],
    topic: str,
    source: str,
    bezirke: list[str],
) -> list[dict]:
    """Bezirk rows of a wide sheet -> shares, using keyword-matched column headers.

    The first column whose header says insgesamt/zusammen is the total. Columns matching the
    same indicator are added up (e.g. POS and Realschule); a suppressed part ("/") counts as
    zero there. If the total looks like a count (above 100) values become shares of it.
    """
    headers, data = locate_table(raw, lambda v: match_bezirk(v, bezirke))
    if data.empty:
        return []
    matched: dict[str, list] = {}
    labels: dict[str, str] = {}
    total_col = None
    for col, header in zip(raw.columns, headers, strict=True):
        if not header:
            continue
        if total_col is None and TOTAL_COLUMN_RE.search(header):
            total_col = col
            continue
        for pattern, indicator, label in patterns:
            if re.search(pattern, header, re.IGNORECASE):
                matched.setdefault(indicator, []).append(col)
                labels[indicator] = label
                break
    values = {
        indicator: pd.concat([_number(data[c]) for c in cols], axis=1).sum(axis=1, min_count=1)
        for indicator, cols in matched.items()
    }
    if not values:
        return []
    total = _number(data[total_col]) if total_col is not None else None
    counts = total is not None and bool((total > 100).any())
    year = re.search(r"\b(19|20)\d{2}\b", _sheet_title(raw))
    rows = []
    for indicator, series in values.items():
        share = series / total.where(total > 0) * 100 if counts else series
        for key, value in zip(data["_key"], share, strict=True):
            rows.append(
                _row(
                    "bezirk",
                    match_bezirk(key, bezirke),
                    topic,
                    indicator,
                    labels[indicator],
                    value,
                    "pct",
                    year.group(0) if year else None,
                    source,
                )
            )
    return rows


def _load_bezirk_file(
    file: Path, patterns, topic: str, source: str, sheet_title: str | None = None
) -> int:
    """Load a tidy CSV or the Bezirk tables of a workbook.

    `sheet_title` (a regex) limits a workbook to sheets whose first rows match it.
    """
    tables = read_table(file)
    rows = tidy_rows(tables[0], "bezirk", topic, source) if len(tables) == 1 else None
    if rows is None:
        if sheet_title:
            tables = [t for t in tables if re.search(sheet_title, _sheet_title(t))]
        bezirke = _bezirk_names()
        rows = []
        seen: set[str] = set()
        for raw in tables:
            for row in wide_bezirk_rows(raw, patterns, topic, source, bezirke):
                if (row["area_id"], row["indicator"]) not in seen:
                    seen.add((row["area_id"], row["indicator"]))
                    rows.append(row)
    if not rows:
        raise ValueError(
            f"No Bezirk rows recognised in {file}. Use a CSV with columns bezirk;indicator;value."
        )
    return save_indicators(rows, [source])


def load_education(file: Path | None = None) -> int:
    """School and vocational certificates per Bezirk (Mikrozensus report tables 7.1 and 7.2).

    Shares are of residents aged 15 and over.
    """
    settings = get_settings()
    path = file or download(
        settings.mikrozensus_xlsx_url, settings.reference_dir / "mikrozensus_be.xlsx"
    )
    return _load_bezirk_file(
        path,
        EDUCATION_COLUMNS,
        "education",
        "Mikrozensus",
        sheet_title=r"Schulabschluss|Bildungsabschluss",
    )


def load_religion_bezirk(file: Path | None = None) -> int:
    """Optional faith shares per Bezirk (Islam, Orthodox, Jewish, ...) from a user file."""
    if file is None:
        raise ValueError(
            "There is no official count of Muslim, Orthodox or Jewish residents per Bezirk. "
            "Pass your own table with --file (columns bezirk;indicator;value)."
        )
    return _load_bezirk_file(file, FAITH_COLUMNS, "religion", "Religion per Bezirk")


def income_rows(df: pd.DataFrame, source: str) -> list[dict]:
    """Median gross monthly pay per PLR from a table with a PLR id and a median column."""
    headers, data = locate_table(
        df, lambda v: bool(PLR_ID_RE.fullmatch(str(v).strip().removesuffix(".0").zfill(8)))
    )
    if data.empty:
        raise ValueError("No Planungsraum ids found in the income file")
    candidates = [
        col
        for col, header in zip(df.columns, headers, strict=True)
        if re.search(r"median|entgelt|brutto|gross|pay", header, re.IGNORECASE)
    ]
    if not candidates:
        numeric = {col: _number(data[col]) for col in df.columns}
        candidates = [col for col, s in numeric.items() if s.between(500, 20_000).mean() > 0.8]
    if not candidates:
        raise ValueError(f"No median pay column found among headers {headers}")
    values = _number(data[candidates[0]])
    return [
        _row(
            "plr",
            str(key).strip().removesuffix(".0").zfill(8),
            "income",
            "median_gross_pay",
            "Median gross monthly pay (full-time)",
            value,
            "eur",
            None,
            source,
        )
        for key, value in zip(data["_key"], values, strict=True)
    ]


def load_income(file: Path | None = None) -> int:
    if file is None:
        raise ValueError(
            "The median gross pay per Planungsraum is published by the Amt für Statistik on "
            "request only. Pass the table with --file (a PLR id column plus a median column)."
        )
    source = "Median pay"
    tables = read_table(file)
    rows = tidy_rows(tables[0], "plr", "income", source) if len(tables) == 1 else None
    if rows is None:
        frame = tables[0]
        if not isinstance(frame.columns[0], int):
            # A headed CSV: put the header back as the first row so locate_table sees it.
            frame = pd.concat([pd.DataFrame([frame.columns]), pd.DataFrame(frame.values)])
            frame = frame.reset_index(drop=True)
        rows = income_rows(frame, source)
    return save_indicators(rows, [source])
