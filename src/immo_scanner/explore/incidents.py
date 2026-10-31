"""Berliner Register incidents for the demographics page."""

from datetime import date

import pandas as pd
from sqlalchemy import text

from immo_scanner.db.session import get_engine

INCIDENT_COLUMNS = [
    "id",
    "incident_date",
    "title",
    "bezirk",
    "source",
    "geo_precision",
    "plr_id",
    "lat",
    "lon",
]
# Street and station hits sit inside one Planungsraum; a place name from the title
# only marks the middle of an Ortsteil or Kiez, so it is left out of the rates.
RATE_PRECISIONS = ("street", "station")
MIN_POPULATION = 200
MIN_FOREIGN_POPULATION = 100


def incident_date_range() -> tuple[date, date] | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT min(incident_date), max(incident_date) FROM register_incidents")
        ).first()
    return (row[0], row[1]) if row and row[0] else None


def load_incidents(start: date, end: date) -> pd.DataFrame:
    query = """
        SELECT id, incident_date, title, bezirk, source, geo_precision, plr_id,
               ST_Y(geom) AS lat, ST_X(geom) AS lon
        FROM register_incidents
        WHERE incident_date BETWEEN :start AND :end
        ORDER BY incident_date DESC
    """
    with get_engine().connect() as conn:
        df = pd.read_sql(text(query), conn, params={"start": start, "end": end})
    return df.reindex(columns=INCIDENT_COLUMNS)


def window_years(start: date, end: date) -> float:
    return max((end - start).days + 1, 1) / 365.25


def load_migration_background() -> pd.DataFrame:
    """Latest residents with a migration background per Planungsraum.

    Columns: area_id, mh_population, ref_date. Empty when origin stats are not loaded.
    The count is T1 from Statistischer Bericht A I 16 (`origin_key` `mh`).
    """
    query = """
        SELECT plr_id AS area_id, count AS mh_population, ref_date
        FROM plr_origin_stats
        WHERE origin_key = 'mh'
          AND ref_date = (
              SELECT MAX(ref_date) FROM plr_origin_stats WHERE origin_key = 'mh'
          )
    """
    with get_engine().connect() as conn:
        df = pd.read_sql(text(query), conn)
    if df.empty:
        return pd.DataFrame(columns=["area_id", "mh_population", "ref_date"])
    df["ref_date"] = pd.to_datetime(df["ref_date"]).dt.date
    return df


def load_foreign_citizens() -> pd.DataFrame:
    """Residents who are not German citizens, per Planungsraum.

    The MSS foreign-citizen share times the register population for the same area.
    Columns: area_id, foreign_population, ref_date, source. Empty when that share is
    not loaded.
    """
    query = """
        SELECT l.plr_id AS area_id,
               l.population * f.value / 100.0 AS foreign_population,
               f.ref_date,
               f.source
        FROM lor_planungsraeume l
        JOIN area_indicators f
          ON f.level = 'plr'
         AND f.area_id = l.plr_id
         AND f.indicator = 'foreign_share_mss'
        WHERE l.population IS NOT NULL AND f.value IS NOT NULL
    """
    with get_engine().connect() as conn:
        df = pd.read_sql(text(query), conn)
    if df.empty:
        return pd.DataFrame(columns=["area_id", "foreign_population", "ref_date", "source"])
    return df


def _denominator(area_id: pd.Series, population: pd.Series | None) -> pd.Series:
    if population is None:
        return pd.Series(pd.NA, index=area_id.index, dtype="Float64")
    return pd.to_numeric(area_id.map(population), errors="coerce")


def _per_thousand(count: pd.Series, people: pd.Series, years: float, minimum: int) -> pd.Series:
    return (count / people * 1000 / years).where(people >= minimum)


def incident_rates(
    incidents: pd.DataFrame,
    plr_areas: pd.DataFrame,
    years: float,
    mh_population: pd.Series | None = None,
    foreign_population: pd.Series | None = None,
) -> pd.DataFrame:
    """Rates for every Planungsraum.

    `count` is incidents placed at a street or station. `value` is that count per 1,000
    residents per year. `value_mh` uses residents with a migration background, and
    `value_foreign` uses residents who are not German citizens. Each population series
    maps area_id to that resident count. A rate is missing when its denominator is
    below MIN_POPULATION, or below MIN_FOREIGN_POPULATION for non-German citizens.
    """
    placed = incidents[incidents["geo_precision"].isin(RATE_PRECISIONS)]
    counts = placed.groupby("plr_id").size()
    out = plr_areas[["area_id", "population"]].copy()
    out["count"] = out["area_id"].map(counts).fillna(0).astype(int)
    population = pd.to_numeric(out["population"], errors="coerce")
    out["value"] = _per_thousand(out["count"], population, years, MIN_POPULATION)
    out["mh_population"] = _denominator(out["area_id"], mh_population)
    out["value_mh"] = _per_thousand(out["count"], out["mh_population"], years, MIN_POPULATION)
    out["foreign_population"] = _denominator(out["area_id"], foreign_population)
    out["value_foreign"] = _per_thousand(
        out["count"], out["foreign_population"], years, MIN_FOREIGN_POPULATION
    )
    return out[
        [
            "area_id",
            "count",
            "value",
            "value_mh",
            "mh_population",
            "value_foreign",
            "foreign_population",
        ]
    ].reset_index(drop=True)


def unplaced_counts(incidents: pd.DataFrame) -> pd.Series:
    """Incidents per Bezirk that are not in the rates (no street or station found)."""
    rest = incidents[~incidents["geo_precision"].isin(RATE_PRECISIONS)]
    return rest.groupby(rest["bezirk"].fillna("unknown")).size().sort_values(ascending=False)


def precision_counts(incidents: pd.DataFrame) -> dict[str, int]:
    return incidents["geo_precision"].fillna("not geocoded").value_counts().to_dict()
