"""Planungsraum education proxy from nearby schools' current Abitur averages."""

import logging
import math

import geopandas as gpd
import pandas as pd
from sqlalchemy import text

from immo_scanner.db.session import get_engine
from immo_scanner.explore.data import load_school_sites
from immo_scanner.explore.gymnasiums import attach_locations
from immo_scanner.explore.sekundarschulen import (
    attach_locations as attach_sekundarschule_locations,
)
from immo_scanner.reference.demographics import _row, save_indicators

log = logging.getLogger(__name__)

SOURCE = "School Abitur"
LEGACY_SOURCE = "Gymnasium Abitur"
INDICATOR = "nearby_school_abi_average"
TYPE_INDICATOR = "nearby_school_type_level"
NEAREST_SCHOOLS = 6
DISTANCE_DECAY_METRES = 3_000
BERLIN_ABI_AVERAGES = {"gymnasium": 2.2, "sekundarschule": 2.5}
REFERENCE_ABI_AVERAGE = BERLIN_ABI_AVERAGES["gymnasium"]


def weighted_abi_rows(
    areas: pd.DataFrame,
    schools: pd.DataFrame,
    *,
    nearest_schools: int = NEAREST_SCHOOLS,
    decay_metres: float = DISTANCE_DECAY_METRES,
) -> list[dict]:
    """Distance-weight school-type-adjusted Abitur averages of nearby schools.

    Results use the Gymnasium scale: a Sekundarschule result is shifted by -0.3,
    making Berlin's 2.5 Sekundarschule mean equivalent to the Gymnasium mean of 2.2.
    """
    if nearest_schools < 1 or decay_metres <= 0:
        raise ValueError("nearest_schools and decay_metres must be positive")
    usable = schools.dropna(subset=["x", "y", "abi_average", "school_type"]).copy()
    usable["adjusted_abi_average"] = usable.apply(
        lambda school: float(school["abi_average"])
        - BERLIN_ABI_AVERAGES[school["school_type"]]
        + REFERENCE_ABI_AVERAGE,
        axis=1,
    )
    rows = []
    for area in areas.itertuples(index=False):
        area_x, area_y = float(area.x), float(area.y)
        nearest = sorted(
            (
                (math.hypot(float(school.x) - area_x, float(school.y) - area_y), school)
                for school in usable.itertuples(index=False)
            ),
            key=lambda item: item[0],
        )[:nearest_schools]
        if not nearest:
            continue
        weights = [math.exp(-distance / decay_metres) for distance, _ in nearest]
        average = sum(
            weight * float(school.adjusted_abi_average)
            for weight, (_, school) in zip(weights, nearest, strict=True)
        ) / sum(weights)
        years = sorted({str(school.abi_year) for _, school in nearest if pd.notna(school.abi_year)})
        rows.append(
            _row(
                "plr",
                area.plr_id,
                "education",
                INDICATOR,
                f"Adjusted Abitur average of {len(nearest)} nearest secondary schools "
                "(distance-weighted)",
                average,
                "grade",
                ", ".join(years) or None,
                SOURCE,
            )
        )
    return rows


def weighted_type_level_rows(
    areas: pd.DataFrame,
    schools: pd.DataFrame,
    *,
    nearest_schools: int = NEAREST_SCHOOLS,
    decay_metres: float = DISTANCE_DECAY_METRES,
) -> list[dict]:
    """Distance-weight the raw Schultypisierungsstufe of nearby schools.

    Stufe 1–7 is the same scale for Gymnasien and Sekundarschulen, so unlike the
    Abitur average it is not shifted between school types. A higher Stufe is a
    heavier structural load.
    """
    if nearest_schools < 1 or decay_metres <= 0:
        raise ValueError("nearest_schools and decay_metres must be positive")
    usable = schools.dropna(subset=["x", "y", "type_level"])
    rows = []
    for area in areas.itertuples(index=False):
        area_x, area_y = float(area.x), float(area.y)
        nearest = sorted(
            (
                (math.hypot(float(school.x) - area_x, float(school.y) - area_y), school)
                for school in usable.itertuples(index=False)
            ),
            key=lambda item: item[0],
        )[:nearest_schools]
        if not nearest:
            continue
        weights = [math.exp(-distance / decay_metres) for distance, _ in nearest]
        average = sum(
            weight * float(school.type_level)
            for weight, (_, school) in zip(weights, nearest, strict=True)
        ) / sum(weights)
        rows.append(
            _row(
                "plr",
                area.plr_id,
                "school_load",
                TYPE_INDICATOR,
                f"Schultypisierungsstufe of {len(nearest)} nearest secondary schools "
                "(distance-weighted)",
                average,
                "grade",
                None,
                SOURCE,
            )
        )
    return rows


def load_gymnasium_education() -> int:
    """Calculate and store the nearby-school indicator for all Planungsräume."""
    areas_query = """
        SELECT plr_id,
               ST_X(ST_PointOnSurface(ST_Transform(geom, 25833))) AS x,
               ST_Y(ST_PointOnSurface(ST_Transform(geom, 25833))) AS y
        FROM lor_planungsraeume
    """
    gymnasium_stats_query = "SELECT * FROM gymnasium_statistics"
    sekundarschule_stats_query = "SELECT * FROM sekundarschule_statistics"
    schools_query = """
        SELECT name,
               ST_X(ST_Transform(geom, 25833)) AS x,
               ST_Y(ST_Transform(geom, 25833)) AS y
        FROM schools
        WHERE kind = 'gymnasium'
    """
    with get_engine().connect() as conn:
        areas = pd.read_sql(text(areas_query), conn)
        gymnasium_stats = pd.read_sql(text(gymnasium_stats_query), conn)
        sekundarschule_stats = pd.read_sql(text(sekundarschule_stats_query), conn)
        locations = pd.read_sql(text(schools_query), conn)
    gymnasiums = attach_locations(gymnasium_stats, locations).assign(school_type="gymnasium")

    sites = load_school_sites()
    sekundarschulen = attach_sekundarschule_locations(sekundarschule_stats, sites)
    if not sekundarschulen.empty:
        projected = gpd.GeoSeries(
            gpd.points_from_xy(sekundarschulen["lon"], sekundarschulen["lat"]), crs=4326
        ).to_crs(25833)
        sekundarschulen["x"] = projected.x
        sekundarschulen["y"] = projected.y
    sekundarschulen["school_type"] = "sekundarschule"
    schools = pd.concat([gymnasiums, sekundarschulen], ignore_index=True, sort=False)
    abi_rows = weighted_abi_rows(areas, schools)
    type_rows = weighted_type_level_rows(areas, schools)
    rows = [*abi_rows, *type_rows]
    if not rows:
        log.warning(
            "School education indicator skipped: load LOR areas, school locations and "
            "Abitur statistics first"
        )
        return 0
    count = save_indicators(rows, [SOURCE, LEGACY_SOURCE])
    log.info(
        "School education: %d Abitur areas, %d Schultypisierung areas, "
        "%d nearest schools, %.1f km distance decay",
        len(abi_rows),
        len(type_rows),
        NEAREST_SCHOOLS,
        DISTANCE_DECAY_METRES / 1000,
    )
    return count
