"""LOR Planungsräume (2021 boundaries) and their population structure."""

import logging
import re
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert

from immo_scanner.config import get_settings
from immo_scanner.db.models import LorPlanungsraum
from immo_scanner.db.session import session_scope
from immo_scanner.reference.common import read_csv_flexible, read_geo, to_multipolygon, to_number
from immo_scanner.reference.download import download

log = logging.getLogger(__name__)

AGE_COLUMN_RE = re.compile(r"^E_E(\d{2})_(\d{2,3})$")


def load_lor_geometries(file: Path | None = None) -> int:
    settings = get_settings()
    path = file or download(settings.lor_wfs_url, settings.reference_dir / "lor_plr_2021.geojson")
    gdf = read_geo(path)
    rows = [
        {
            "plr_id": str(row["plr_id"]).zfill(8),
            "plr_name": row["plr_name"],
            "bzr_name": row.get("bzr_name"),
            "pgr_name": row.get("pgr_name"),
            "bezirk": re.sub(r"^\d+\s*-\s*", "", str(row.get("bez") or "")) or None,
            "geom": to_multipolygon(row.geometry),
        }
        for _, row in gdf.iterrows()
    ]
    statement = insert(LorPlanungsraum).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=["plr_id"],
        set_={
            name: statement.excluded[name]
            for name in ("plr_name", "bzr_name", "pgr_name", "bezirk", "geom")
        },
    )
    with session_scope() as session:
        session.execute(statement)
    return len(rows)


def _plr_ids(df) -> "list[str]":
    if "RAUMID" in df.columns:
        return df["RAUMID"].str.strip().str.zfill(8).tolist()
    parts = ["BEZ", "PGR", "BZR", "PLR"]
    if all(p in df.columns for p in parts):
        return (
            df[parts]
            .apply(lambda r: "".join(str(v).strip().zfill(2) for v in r), axis=1)
            .tolist()
        )
    raise ValueError(f"Cannot find a Planungsraum id column in {list(df.columns)}")


def load_population(file: Path | None = None) -> int:
    settings = get_settings()
    path = file or download(
        settings.population_csv_url, settings.reference_dir / "population_lor.csv"
    )
    df = read_csv_flexible(path)
    ids = _plr_ids(df)

    age_groups = {
        column: (int(m.group(1)), int(m.group(2)))
        for column in df.columns
        if (m := AGE_COLUMN_RE.match(column))
    }
    numeric = {c: to_number(df[c]) for c in df.columns if c.startswith("E_")}
    if "E_E" not in numeric:
        raise ValueError(f"Column E_E (total population) missing in {list(df.columns)}")

    under_18 = [c for c, (_, high) in age_groups.items() if high <= 18]
    over_65 = [c for c, (low, _) in age_groups.items() if low >= 65]
    middle = [c for c in age_groups if c not in under_18 and c not in over_65]

    updated = 0
    with session_scope() as session:
        for i, plr_id in enumerate(ids):
            total = numeric["E_E"].iloc[i]
            if total != total or total <= 0:  # NaN or empty area
                continue

            def share(columns: list[str], i: int = i, total: float = total) -> float | None:
                if not columns:
                    return None
                return round(float(sum(numeric[c].iloc[i] for c in columns)) / total * 100, 1)

            result = session.execute(
                update(LorPlanungsraum)
                .where(LorPlanungsraum.plr_id == plr_id)
                .values(
                    population=int(total),
                    share_under_18_pct=share(under_18),
                    share_18_to_64_pct=share(middle),
                    share_65_plus_pct=share(over_65),
                    population_date=str(df["ZEIT"].iloc[i]) if "ZEIT" in df.columns else None,
                    demographics={
                        c: (None if v.iloc[i] != v.iloc[i] else float(v.iloc[i]))
                        for c, v in numeric.items()
                    },
                )
            )
            updated += result.rowcount or 0
    if updated == 0:
        log.warning(
            "No Planungsraum matched. Load geometries first (`immo load-reference lor`). "
            "Sample ids from the CSV: %s",
            ids[:5],
        )
    return updated
