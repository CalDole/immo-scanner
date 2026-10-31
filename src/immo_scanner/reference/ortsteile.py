"""Official Ortsteile (ALKIS) so a listing can be placed in a locality."""

from pathlib import Path

import geopandas as gpd
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from immo_scanner.config import get_settings
from immo_scanner.db.models import Ortsteil
from immo_scanner.db.session import session_scope
from immo_scanner.reference.common import find_column, read_geo, to_multipolygon
from immo_scanner.reference.download import download


def ortsteil_rows(gdf: gpd.GeoDataFrame) -> list[dict]:
    """Map an ALKIS Ortsteil layer to table rows.

    The cadastre key is 12 digits (`110000` + Bezirk + four-digit Ortsteil number).
    The last four digits are the official Ortsteil id, e.g. `0202` for Kreuzberg.
    """
    name_col = find_column(gdf.columns, r"^nam$|^name$")
    key_col = find_column(gdf.columns, r"^sch$|schluessel")
    if name_col is None or key_col is None:
        raise ValueError(f"Cannot find Ortsteil name/key columns in {list(gdf.columns)}")
    rows = []
    for _, row in gdf.iterrows():
        key = str(row[key_col]).strip()
        name = str(row[name_col]).strip()
        if not key or key.lower() == "none" or not name or name.lower() == "none":
            continue
        rows.append({"id": key[-4:].zfill(4), "name": name, "geom": to_multipolygon(row.geometry)})
    return rows


def load_ortsteile(file: Path | None = None) -> int:
    settings = get_settings()
    path = file or download(
        settings.ortsteil_wfs_url, settings.reference_dir / "ortsteile.geojson"
    )
    rows = ortsteil_rows(read_geo(path))
    statement = insert(Ortsteil).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=["id"],
        set_={name: statement.excluded[name] for name in ("name", "geom")},
    )
    with session_scope() as session:
        session.execute(statement)
    with session_scope() as session:
        session.execute(text("REFRESH MATERIALIZED VIEW listing_features"))
    return len(rows)
