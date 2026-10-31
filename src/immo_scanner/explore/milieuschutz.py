"""How much of each Planungsraum lies inside a Milieuschutzgebiet."""

import json
import logging

import pandas as pd
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from immo_scanner.db.session import get_engine

log = logging.getLogger(__name__)

# A shared border is not enough to name a Gebiet on a Planungsraum.
MIN_OVERLAP_M2 = 500

_COVERAGE_COLUMNS = [
    "area_id",
    "name",
    "bezirk",
    "population",
    "value",
    "area_ha",
    "plr_area_ha",
    "gebiete",
]
_GEBIET_COLUMNS = ["schluessel", "name", "bezirk", "in_force", "area_ha", "geometry"]

_COVERAGE_SQL = """
WITH unioned AS (
    SELECT ST_UnaryUnion(ST_Collect(geom)) AS geom
    FROM milieuschutz_gebiete
),
overlap AS (
    SELECT
        p.plr_id,
        p.plr_name,
        p.bezirk,
        p.population,
        p.geom,
        ST_Intersection(ST_MakeValid(p.geom), u.geom) AS hit
    FROM lor_planungsraeume p
    CROSS JOIN unioned u
    WHERE u.geom IS NOT NULL
)
SELECT
    o.plr_id AS area_id,
    o.plr_name AS name,
    o.bezirk,
    o.population,
    ROUND((
        ST_Area(o.hit::geography) / NULLIF(ST_Area(o.geom::geography), 0) * 100
    )::numeric, 1)::float8 AS value,
    ROUND((ST_Area(o.hit::geography) / 10000)::numeric, 1)::float8 AS area_ha,
    ROUND((ST_Area(o.geom::geography) / 10000)::numeric, 1)::float8 AS plr_area_ha,
    (
        SELECT string_agg(g.name, ', ' ORDER BY g.name)
        FROM milieuschutz_gebiete g
        WHERE ST_Intersects(g.geom, o.geom)
          AND ST_Area(ST_Intersection(g.geom, o.geom)::geography) >= :min_overlap
    ) AS gebiete
FROM overlap o
"""

_GEBIETE_SQL = """
SELECT schluessel, name, bezirk, in_force, area_ha,
       ST_AsGeoJSON(ST_SimplifyPreserveTopology(geom, 0.00005), 5) AS geometry
FROM milieuschutz_gebiete
ORDER BY bezirk, name
"""


def load_coverage() -> pd.DataFrame:
    """One row per Planungsraum: area share inside the union of the Gebiete.

    `value` is that share in percent. `area_ha` is the overlapping hectares.
    `gebiete` lists names whose overlap is at least 500 m². An empty frame means
    the Gebiete table is missing or has no polygons.
    """
    frame = _read(_COVERAGE_SQL, {"min_overlap": MIN_OVERLAP_M2})
    if frame is None:
        return pd.DataFrame(columns=_COVERAGE_COLUMNS)
    for column in ("value", "area_ha", "plr_area_ha"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["area_id"] = frame["area_id"].astype(str)
    return frame


def load_gebiete() -> pd.DataFrame:
    """Simplified Gebiet polygons plus name, Bezirk, in-force date and hectares."""
    frame = _read(_GEBIETE_SQL)
    if frame is None:
        return pd.DataFrame(columns=_GEBIET_COLUMNS)
    frame["geometry"] = frame["geometry"].map(
        lambda raw: json.loads(raw) if isinstance(raw, str) else None
    )
    return frame[frame["geometry"].map(lambda geom: isinstance(geom, dict))].reset_index(drop=True)


def _read(query: str, params: dict | None = None) -> pd.DataFrame | None:
    try:
        with get_engine().connect() as conn:
            return pd.read_sql(text(query), conn, params=params or {})
    except ProgrammingError as exc:
        if not _missing_gebiete_table(exc):
            raise
        log.info("Milieuschutz Gebiete are not loaded")
        return None


def _missing_gebiete_table(exc: ProgrammingError) -> bool:
    """True when Postgres reports that `milieuschutz_gebiete` itself is absent."""
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate != "42P01":
        return False
    diag = getattr(orig, "diag", None)
    primary = getattr(diag, "message_primary", None) or str(orig or "")
    return 'relation "milieuschutz_gebiete"' in primary
