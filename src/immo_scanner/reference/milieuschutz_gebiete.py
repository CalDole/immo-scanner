"""Berlin Milieuschutzgebiete from the Senate WFS (Erhaltungsverordnungsgebiete).

The layer is § 172 Abs. 1 Nr. 2 BauGB, the areas that protect the composition of
the residential population. The street list used for listing status is separate.
"""

import json
import logging
from datetime import date, datetime
from pathlib import Path

import geopandas as gpd
import pandas as pd
from sqlalchemy import delete, text
from sqlalchemy.dialects.postgresql import insert

from immo_scanner.config import get_settings
from immo_scanner.db.models import MilieuschutzGebiet
from immo_scanner.db.session import session_scope
from immo_scanner.reference.common import read_geo, to_multipolygon
from immo_scanner.reference.download import download
from immo_scanner.reference.milieuschutz import apply_quarter_status

log = logging.getLogger(__name__)

_BLANK = {"", "none", "null", "nan"}


def gebiet_rows(gdf: gpd.GeoDataFrame) -> list[dict]:
    """Map the WFS layer `erhaltgeb_em` to table rows."""
    missing = [name for name in ("schluessel", "gebietsname") if name not in gdf.columns]
    if missing:
        raise ValueError(
            f"Milieuschutz Gebiete file is missing {missing}; columns are {list(gdf.columns)}"
        )
    rows = []
    for _, row in gdf.iterrows():
        key = _text(row.get("schluessel"))
        name = _text(row.get("gebietsname"))
        geom = row.geometry
        if not key or not name or geom is None or geom.is_empty:
            continue
        rows.append(
            {
                "schluessel": key,
                "name": name,
                "bezirk": _text(row.get("bezirk")),
                "in_force": _date(row.get("f_in_kraft")),
                "area_ha": _hectares(row.get("fl_ha")),
                "geom": to_multipolygon(geom),
            }
        )
    return rows


def require_complete(path: Path) -> None:
    """Refuse a GeoJSON page that returned fewer features than the WFS promised."""
    found, expected = _feature_counts(path)
    if found is not None and expected is not None and found < expected:
        raise RuntimeError(
            f"Milieuschutz download has {found} of {expected} Gebiete. The WFS page was truncated."
        )


def load_milieuschutz_gebiete(file: Path | None = None) -> int:
    """Download the Gebiet polygons and replace the table."""
    settings = get_settings()
    path = file or download(
        settings.milieuschutz_wfs_url,
        settings.reference_dir / "milieuschutz_gebiete.geojson",
    )
    try:
        require_complete(path)
    except RuntimeError:
        # A truncated page must not stay cached, or the next run would refuse it again.
        if file is None:
            path.unlink(missing_ok=True)
        raise
    rows = gebiet_rows(read_geo(path))
    if not rows:
        raise RuntimeError(f"No Milieuschutzgebiete parsed from {path}")
    with session_scope() as session:
        session.execute(delete(MilieuschutzGebiet))
        session.execute(insert(MilieuschutzGebiet), rows)
        updated = apply_quarter_status(session)
    log.info("Loaded %d Milieuschutzgebiete", len(rows))
    if updated:
        log.info("Updated In quarter on %d listings", updated)
        with session_scope() as session:
            session.execute(text("REFRESH MATERIALIZED VIEW listing_features"))
    return len(rows)


def _feature_counts(path: Path) -> tuple[int | None, int | None]:
    """Features in a GeoJSON file, and `numberMatched` when the WFS sent one."""
    if path.suffix.lower() not in {".json", ".geojson"}:
        return None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    features = payload.get("features")
    found = len(features) if isinstance(features, list) else None
    matched = payload.get("numberMatched")
    try:
        expected = int(matched) if matched is not None else None
    except (TypeError, ValueError):
        expected = None
    return found, expected


def _text(value: object) -> str | None:
    if _blank(value):
        return None
    text = str(value).strip()
    if text.casefold() in _BLANK:
        return None
    return text


def _date(value: object) -> date | None:
    text = _text(value)
    if text is None:
        return None
    for fmt, raw in (("%d.%m.%Y", text), ("%Y-%m-%d", text[:10])):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _hectares(value: object) -> float | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def _blank(value: object) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False
