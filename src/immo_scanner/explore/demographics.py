"""Demographic indicators per Planungsraum or Bezirk for the demographics page."""

import json

import pandas as pd
from sqlalchemy import text

from immo_scanner.db.session import get_engine

INDICATOR_COLUMNS = [
    "level",
    "area_id",
    "topic",
    "indicator",
    "label",
    "value",
    "unit",
    "ref_date",
    "source",
]

# (indicator, label, population JSONB columns summed for the share)
AGE_GROUPS: list[tuple[str, str, list[str]]] = [
    ("under_6", "Under 6", ["E_EU1", "E_E1U6"]),
    ("6_to_15", "6 to 14", ["E_E6U15"]),
    ("15_to_18", "15 to 17", ["E_E15U18"]),
    ("18_to_25", "18 to 24", ["E_E18U25"]),
    ("25_to_55", "25 to 54", ["E_E25U55"]),
    ("55_to_65", "55 to 64", ["E_E55U65"]),
    ("65_to_80", "65 to 79", ["E_E65U80"]),
    ("80_plus", "80 and over", ["E_E80U110"]),
]


def load_areas(level: str = "plr") -> pd.DataFrame:
    """Simplified area polygons as GeoJSON dicts: area_id, name, bezirk, population, geometry.

    Planungsraum rows also carry `bzr_id`, the 6-digit Bezirksregion key.
    """
    if level == "plr":
        query = """
            SELECT plr_id AS area_id, plr_name AS name, LEFT(plr_id, 6) AS bzr_id, bezirk,
                   population,
                   ST_AsGeoJSON(ST_SimplifyPreserveTopology(geom, 0.00008), 5) AS geometry
            FROM lor_planungsraeume
        """
    elif level == "bzr":
        query = """
            SELECT LEFT(plr_id, 6) AS area_id, MIN(bzr_name) AS name, MIN(bezirk) AS bezirk,
                   SUM(population) AS population,
                   ST_AsGeoJSON(ST_SimplifyPreserveTopology(ST_Union(geom), 0.00012), 5)
                       AS geometry
            FROM lor_planungsraeume
            GROUP BY LEFT(plr_id, 6)
        """
    elif level == "bezirk":
        query = """
            SELECT bezirk AS area_id, bezirk AS name, bezirk, SUM(population) AS population,
                   ST_AsGeoJSON(ST_SimplifyPreserveTopology(ST_Union(geom), 0.0002), 5)
                       AS geometry
            FROM lor_planungsraeume
            WHERE bezirk IS NOT NULL
            GROUP BY bezirk
        """
    else:
        raise ValueError(f"Unknown level {level!r}")
    with get_engine().connect() as conn:
        df = pd.read_sql(text(query), conn)
    df["geometry"] = df["geometry"].map(json.loads)
    return df


def load_indicators(topic: str | None = None) -> pd.DataFrame:
    query = "SELECT * FROM area_indicators"
    params = {}
    if topic:
        query += " WHERE topic = :topic"
        params["topic"] = topic
    with get_engine().connect() as conn:
        return pd.read_sql(text(query), conn, params=params)


def age_rows(lor: pd.DataFrame) -> pd.DataFrame:
    """Age shares in long format from lor_planungsraeume (population + demographics JSONB)."""
    rows = []
    for record in lor.itertuples(index=False):
        bands = record.demographics or {}
        total = bands.get("E_E") or record.population
        base = {
            "level": "plr",
            "area_id": record.plr_id,
            "topic": "age",
            "unit": "pct",
            "ref_date": record.population_date,
            "source": "Einwohnerregister",
        }
        for indicator, label, value in (
            ("under_18", "Children (under 18)", record.share_under_18_pct),
            ("18_to_64", "Adults (18 to 64)", record.share_18_to_64_pct),
            ("65_plus", "Pensioners (65 and over)", record.share_65_plus_pct),
        ):
            rows.append({**base, "indicator": indicator, "label": label, "value": value})
        for indicator, label, columns in AGE_GROUPS:
            counts = [bands.get(c) for c in columns]
            value = (
                round(sum(counts) / total * 100, 2)
                if total and all(c is not None for c in counts)
                else None
            )
            rows.append({**base, "indicator": indicator, "label": label, "value": value})
    return pd.DataFrame(rows, columns=INDICATOR_COLUMNS)


def load_age() -> pd.DataFrame:
    query = """
        SELECT plr_id, population, share_under_18_pct, share_18_to_64_pct, share_65_plus_pct,
               population_date, demographics
        FROM lor_planungsraeume
        WHERE population IS NOT NULL
    """
    with get_engine().connect() as conn:
        lor = pd.read_sql(text(query), conn)
    return age_rows(lor)


def _expand_to_plr(
    rows: pd.DataFrame, plr_areas: pd.DataFrame, key: str, plr_columns: pd.Index
) -> pd.DataFrame:
    lookup = plr_areas[["area_id", key]].rename(columns={"area_id": "plr_id"})
    expanded = (
        rows.merge(lookup, left_on="area_id", right_on=key)
        .drop(columns=["area_id", key])
        .rename(columns={"plr_id": "area_id"})
        .assign(level="plr", coarse=True)
    )
    return expanded[plr_columns]


def to_plr(indicators: pd.DataFrame, plr_areas: pd.DataFrame) -> pd.DataFrame:
    """Copy coarser rows onto every Planungsraum of that area and flag them coarse.

    PLR rows pass through with coarse=False. Bezirk rows are copied onto the PLRs of that
    Bezirk. Bezirksregion rows (`level` `bzr`, `area_id` the 6-digit LOR key) are copied
    onto the PLRs whose `bzr_id` matches, when `plr_areas` has that column.
    """
    plr = indicators[indicators["level"] == "plr"].assign(coarse=False)
    frames = [plr]
    bezirk = indicators[indicators["level"] == "bezirk"]
    if not bezirk.empty:
        frames.append(_expand_to_plr(bezirk, plr_areas, "bezirk", plr.columns))
    bzr = indicators[indicators["level"] == "bzr"]
    if not bzr.empty and "bzr_id" in plr_areas.columns:
        frames.append(_expand_to_plr(bzr, plr_areas, "bzr_id", plr.columns))
    return pd.concat(frames, ignore_index=True)


def indicator_catalog(indicators: pd.DataFrame) -> pd.DataFrame:
    """One row per (topic, indicator) with its label, unit, level, source and date."""
    if indicators.empty:
        return pd.DataFrame(
            columns=["topic", "indicator", "label", "unit", "level", "source", "ref_date", "areas"]
        )
    return (
        indicators.groupby(["topic", "indicator"], sort=False)
        .agg(
            label=("label", "first"),
            unit=("unit", "first"),
            level=("level", "first"),
            source=("source", "first"),
            ref_date=("ref_date", "first"),
            areas=("value", "count"),
        )
        .reset_index()
    )


def indicator_values(indicators: pd.DataFrame, topic: str, indicator: str) -> pd.DataFrame:
    """area_id, value (and coarse if present) for one indicator."""
    mask = (indicators["topic"] == topic) & (indicators["indicator"] == indicator)
    columns = ["area_id", "value"] + (["coarse"] if "coarse" in indicators.columns else [])
    return indicators.loc[mask, columns].reset_index(drop=True)
