import io
import re
from pathlib import Path

import geopandas as gpd
import pandas as pd
from geoalchemy2.shape import from_shape
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry


def read_csv_flexible(path: Path) -> pd.DataFrame:
    """Read the Amt für Statistik CSVs: unknown delimiter and UTF-8 or Windows-1252."""
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    if text.lstrip().lower().startswith(("<!doctype html", "<html")):
        raise ValueError(f"{path} is an HTML page, not a CSV - download it manually")
    df = pd.read_csv(io.StringIO(text), sep=None, engine="python", dtype=str)
    df.columns = [c.strip() for c in df.columns]
    return df


def read_geo(path: Path) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        # Berlin open data is usually ETRS89 / UTM 33N when no CRS is declared.
        gdf = gdf.set_crs(25833)
    return gdf.to_crs(4326)


def to_multipolygon(geom: BaseGeometry):
    if isinstance(geom, Polygon):
        geom = MultiPolygon([geom])
    elif not isinstance(geom, MultiPolygon):
        # e.g. a GeometryCollection left over from dissolving touching polygons
        parts = []
        for part in getattr(geom, "geoms", []):
            if isinstance(part, Polygon):
                parts.append(part)
            elif isinstance(part, MultiPolygon):
                parts.extend(part.geoms)
        geom = MultiPolygon(parts)
    return from_shape(geom, srid=4326)


def to_number(series: pd.Series) -> pd.Series:
    cleaned = series.astype(str).str.strip().str.replace(r"\.(?=\d{3}\b)", "", regex=True)
    cleaned = cleaned.str.replace(",", ".", regex=False)
    return pd.to_numeric(cleaned, errors="coerce")


def find_column(columns, pattern: str, exclude: str | None = None) -> str | None:
    for column in columns:
        if re.search(pattern, column, re.IGNORECASE) and not (
            exclude and re.search(exclude, column, re.IGNORECASE)
        ):
            return column
    return None
