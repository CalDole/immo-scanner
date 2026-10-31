"""Blank crawler fields on the merged listing and on each portal's own table.

Scores, elections, demographics and commute are enrichment. They are left out
here so a gap points at a crawler or a detail fetch.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import JSON, DateTime, String, text
from sqlalchemy.sql.elements import TextClause
from sqlalchemy.sql.schema import Column

from immo_scanner.db.models import (
    BskExpose,
    EngelvoelkersExpose,
    Immoscout24Expose,
    ImmoweltExpose,
    KleinanzeigenAd,
    NeubaukompassUnit,
    ThinkimmoProperty,
)
from immo_scanner.db.session import get_engine
from immo_scanner.explore.listing_copy import source_label

# Values the list crawl or the detail fetch stores. Columns missing from an
# older database are skipped by the callers.
CRAWLER_FIELDS: tuple[tuple[str, str], ...] = (
    ("title", "Title"),
    ("address", "Address"),
    ("postcode", "Postcode"),
    ("district", "District"),
    ("price", "Price"),
    ("living_area", "Living area"),
    ("rooms", "Rooms"),
    ("bedrooms", "Bedrooms"),
    ("bathrooms", "Bathrooms"),
    ("apartment_type", "Type"),
    ("floor", "Floor"),
    ("total_floors", "Total floors"),
    ("flooring", "Flooring"),
    ("bathroom_features", "Bathroom"),
    ("year_built", "Year built"),
    ("last_renovation_year", "Last renovation"),
    ("property_condition", "Condition"),
    ("interior_quality", "Interior quality"),
    ("hausgeld", "Hausgeld"),
    ("heating_type", "Heating"),
    ("energy_source", "Energy source"),
    ("energy_efficiency_class", "Energy class"),
    ("final_energy_kwh_m2a", "Final energy"),
    ("has_elevator", "Elevator"),
    ("has_parking", "Parking"),
    ("parking_type", "Parking type"),
    ("parking_price", "Parking price"),
    ("has_cellar", "Cellar"),
    ("has_balcony", "Balcony"),
    ("has_terrace", "Terrace"),
    ("has_fitted_kitchen", "Fitted kitchen"),
    ("has_guest_toilet", "Guest toilet"),
    ("has_monument_protection", "Monument"),
    ("balcony_count", "Balcony count"),
    ("provision", "Provision"),
    ("available_from", "Available from"),
    ("posted_on", "Posted"),
    ("is_rented", "Rented"),
)

# Portal table for each crawler, then ThinkImmo. Column gaps count rows that
# exist. `without_expose` counts active listings of that source with no row.
EXPOSE_SOURCES: tuple[tuple[str, type], ...] = (
    ("kleinanzeigen", KleinanzeigenAd),
    ("immowelt", ImmoweltExpose),
    ("neubaukompass", NeubaukompassUnit),
    ("immoscout24", Immoscout24Expose),
    ("bsk", BskExpose),
    ("engelvoelkers", EngelvoelkersExpose),
    ("thinkimmo", ThinkimmoProperty),
)

GAP_COLUMNS = ["key", "field", "filled", "missing", "missing_pct"]
LISTING_COLUMNS = ["id", "title", "url", "external_id"]
_BLANK_TEXT = ("none", "nan")


@dataclass(frozen=True)
class ExposeCoverage:
    """How complete one crawler's own table is."""

    source: str
    label: str
    listings: int
    without_expose: int
    rows: int
    gaps: pd.DataFrame
    # ThinkImmo creates the row during search. This counts rows whose exposé
    # fetch has not run. Other crawlers leave this empty: no row means no fetch.
    awaiting_detail: int | None = None


def column_label(name: str) -> str:
    return name.replace("_", " ").capitalize()


def _is_blank(series: pd.Series) -> pd.Series:
    """Null, empty text, or the words none and nan. Boolean false is present."""
    blank = series.isna()
    # StringDtype reports itself as "str", which is neither object nor "string".
    if series.dtype == object or pd.api.types.is_string_dtype(series):
        text_values = series.astype("string").str.strip()
        blank = blank | text_values.eq("") | text_values.str.lower().isin(_BLANK_TEXT)
    return blank


def _sources(features: pd.DataFrame) -> pd.Series:
    source = features["source"].fillna("Unknown").astype(str).str.strip()
    return source.mask(source.eq(""), "Unknown")


def source_order(features: pd.DataFrame) -> list[str]:
    """Source keys, largest sample first."""
    if features.empty or "source" not in features.columns:
        return []
    counts = _sources(features).value_counts()
    return sorted(counts.index, key=lambda name: (-int(counts[name]), str(name)))


def listings_from(features: pd.DataFrame, source: str) -> pd.DataFrame:
    """Rows whose source is `source`, with a blank source treated as Unknown."""
    if features.empty or "source" not in features.columns:
        return features.iloc[0:0]
    return features.loc[_sources(features) == source]


def _empty_gaps() -> pd.DataFrame:
    return pd.DataFrame(columns=GAP_COLUMNS)


def _sorted_gaps(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return _empty_gaps()
    return frame.sort_values(
        ["missing_pct", "field"],
        ascending=[False, True],
        na_position="last",
        kind="mergesort",
    ).reset_index(drop=True)


def column_gaps(features: pd.DataFrame) -> pd.DataFrame:
    """Filled and missing counts for each crawler field present on `features`."""
    if features.empty:
        return _empty_gaps()
    n = len(features)
    rows: list[dict[str, object]] = []
    for key, label in CRAWLER_FIELDS:
        if key not in features.columns:
            continue
        missing = int(_is_blank(features[key]).sum())
        rows.append(
            {
                "key": key,
                "field": label,
                "filled": n - missing,
                "missing": missing,
                "missing_pct": 100.0 * missing / n,
            }
        )
    return _sorted_gaps(pd.DataFrame(rows, columns=GAP_COLUMNS))


def gaps_by_source(features: pd.DataFrame) -> pd.DataFrame:
    """Percent blank per source, then a Total column for the whole sample.

    Columns are sources, largest first. The listing count is in the column name.
    Fields absent from `features` are left out.
    """
    if features.empty or "source" not in features.columns:
        return pd.DataFrame()
    present = [(key, label) for key, label in CRAWLER_FIELDS if key in features.columns]
    if not present:
        return pd.DataFrame()
    source = _sources(features)
    counts = source.value_counts()
    ordered = source_order(features)
    total_n = len(features)
    total_column = f"Total ({total_n})"
    rows: list[dict[str, object]] = []
    for key, label in present:
        blank = _is_blank(features[key])
        row: dict[str, object] = {
            "information": label,
            total_column: 100.0 * float(blank.sum()) / total_n if total_n else float("nan"),
        }
        for name in ordered:
            group = source == name
            n = int(counts[name])
            share = 100.0 * float(blank[group].sum()) / n if n else float("nan")
            row[f"{source_label(name)} ({n})"] = share
        rows.append(row)
    return pd.DataFrame(rows)


def missing_listings(
    features: pd.DataFrame, source: str, field: str, limit: int = 50
) -> tuple[pd.DataFrame, int]:
    """Listings of `source` where `field` is blank, newest id first.

    The frame holds at most `limit` rows. The int is the full blank count.
    """
    empty = pd.DataFrame(columns=LISTING_COLUMNS)
    if features.empty or "source" not in features.columns or field not in features.columns:
        return empty, 0
    mask = (_sources(features) == source) & _is_blank(features[field])
    total = int(mask.sum())
    if total == 0:
        return empty, 0
    wanted = [column for column in LISTING_COLUMNS if column in features.columns]
    picked = features.loc[mask, wanted]
    if "id" in picked.columns:
        picked = picked.sort_values("id", ascending=False, kind="mergesort")
    return picked.head(limit).reset_index(drop=True), total


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def tracked_columns(model: type) -> list[Column]:
    """Scalar columns a crawler can leave empty.

    Skips keys, timestamps, JSON blobs, and columns that are always stored
    (a non-null URL, for example).
    """
    columns: list[Column] = []
    for column in model.__table__.columns:
        if column.primary_key or column.foreign_keys:
            continue
        if isinstance(column.type, (DateTime, JSON)):
            continue
        if not column.nullable:
            continue
        columns.append(column)
    return columns


def filled_count_sql(model: type) -> TextClause:
    """One row: `n` plus the filled count of each tracked column.

    Text treats empty, none and nan as blank. Other types count non-null
    values, so a boolean false stays filled.
    """
    selects = ["count(*) AS n"]
    for column in tracked_columns(model):
        name = _quote(column.name)
        if isinstance(column.type, String):
            expr = (
                f"count(*) FILTER (WHERE {name} IS NOT NULL "
                f"AND btrim({name}) <> '' "
                f"AND lower(btrim({name})) NOT IN ('none', 'nan'))"
            )
        else:
            expr = f"count({name})"
        selects.append(f"{expr} AS {name}")
    table = _quote(model.__tablename__)
    return text(f"SELECT {', '.join(selects)} FROM {table}")


def counts_to_gaps(counts: Mapping[str, object]) -> pd.DataFrame:
    """Turn a filled-count row into the same frame as `column_gaps`."""
    n = int(counts.get("n") or 0)
    if n == 0:
        return _empty_gaps()
    rows: list[dict[str, object]] = []
    for key, filled in counts.items():
        if key == "n":
            continue
        filled_n = int(filled or 0)
        missing = n - filled_n
        rows.append(
            {
                "key": key,
                "field": column_label(str(key)),
                "filled": filled_n,
                "missing": missing,
                "missing_pct": 100.0 * missing / n,
            }
        )
    return _sorted_gaps(pd.DataFrame(rows, columns=GAP_COLUMNS))


def _active_listing_counts(conn, source: str, table: str) -> tuple[int, int]:
    quoted = _quote(table)
    query = text(
        f"""
        SELECT
            count(*) AS listings,
            count(*) FILTER (
                WHERE NOT EXISTS (
                    SELECT 1 FROM {quoted} t WHERE t.listing_id = listings.id
                )
            ) AS without_expose
        FROM listings
        WHERE listings.source = :source AND listings.status = 'active'
        """
    )
    row = conn.execute(query, {"source": source}).one()
    return int(row.listings), int(row.without_expose)


def _awaiting_detail(conn, model: type) -> int | None:
    if "detail_fetched_at" not in model.__table__.columns:
        return None
    table = _quote(model.__tablename__)
    query = text(f"SELECT count(*) FROM {table} WHERE detail_fetched_at IS NULL")
    return int(conn.execute(query).scalar_one())


def load_expose_coverage() -> list[ExposeCoverage]:
    """Missing rates on each crawler's table, plus listings with no row stored."""
    coverage: list[ExposeCoverage] = []
    with get_engine().connect() as conn:
        for source, model in EXPOSE_SOURCES:
            listings, without_expose = _active_listing_counts(conn, source, model.__tablename__)
            counts = conn.execute(filled_count_sql(model)).mappings().one()
            coverage.append(
                ExposeCoverage(
                    source=source,
                    label=source_label(source),
                    listings=listings,
                    without_expose=without_expose,
                    rows=int(counts["n"] or 0),
                    gaps=counts_to_gaps(counts),
                    awaiting_detail=_awaiting_detail(conn, model),
                )
            )
    return coverage


# Where a listing stands on the way to a Planungsraum. Neighbourhood data (population,
# indicators, origins) only appears for `located`.
LOCATION_STATES: tuple[tuple[str, str], ...] = (
    ("located", "In a Planungsraum"),
    ("never_tried", "Not geocoded yet"),
    ("not_found", "Geocoder found nothing"),
    ("not_in_view", "Point not in the view yet"),
    ("outside_lor", "Outside every Planungsraum"),
)

_LOCATION_STATE_SQL = """
CASE
    WHEN l.geom IS NULL AND l.geocoded_query IS NULL THEN 'never_tried'
    WHEN l.geom IS NULL THEN 'not_found'
    WHEN f.id IS NULL THEN 'not_in_view'
    WHEN f.plr_id IS NULL THEN 'outside_lor'
    ELSE 'located'
END
"""


def _location_where(active_only: bool) -> str:
    return "WHERE l.status = 'active'" if active_only else ""


def load_location_coverage(active_only: bool = True) -> pd.DataFrame:
    """source, state, listings: how many listings of each source are in each state."""
    query = f"""
        SELECT l.source, {_LOCATION_STATE_SQL} AS state, count(*) AS listings
        FROM listings l
        LEFT JOIN listing_features f ON f.id = l.id
        {_location_where(active_only)}
        GROUP BY 1, 2
    """
    with get_engine().connect() as conn:
        return pd.read_sql(text(query), conn)


def location_state_counts(rows: pd.DataFrame) -> pd.DataFrame:
    """One row per location state, one column per source (largest first), then Total.

    Every state is listed, with 0 where no listing is in it.
    """
    keys = [key for key, _ in LOCATION_STATES]
    labels = [label for _, label in LOCATION_STATES]
    if rows.empty:
        return pd.DataFrame({"key": keys, "state": labels, "Total": [0] * len(keys)})
    table = rows.pivot_table(
        index="state", columns="source", values="listings", aggfunc="sum", fill_value=0
    )
    totals = table.sum()
    sources = sorted(table.columns, key=lambda name: (-int(totals[name]), str(name)))
    table = table.reindex(index=keys, columns=sources, fill_value=0).astype(int)
    table["Total"] = table.sum(axis=1)
    table.columns.name = None
    table = table.reset_index(drop=True)
    table.insert(0, "state", labels)
    table.insert(0, "key", keys)
    return table


def unlocated_listings(
    source: str, state: str, active_only: bool = True, limit: int = 50
) -> tuple[pd.DataFrame, int]:
    """Listings of `source` in location `state`, newest id first, and the full count."""
    where = _location_where(active_only) or "WHERE true"
    query = f"""
        SELECT * FROM (
            SELECT l.id, l.title, l.address, l.url, l.geocoded_query,
                   {_LOCATION_STATE_SQL} AS state
            FROM listings l
            LEFT JOIN listing_features f ON f.id = l.id
            {where} AND l.source = :source
        ) s
        WHERE s.state = :state
        ORDER BY s.id DESC
    """
    with get_engine().connect() as conn:
        frame = pd.read_sql(text(query), conn, params={"source": source, "state": state})
    return frame.drop(columns="state").head(limit).reset_index(drop=True), len(frame)
