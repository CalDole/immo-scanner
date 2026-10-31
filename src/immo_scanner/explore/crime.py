"""Crime atlas figures for the demographics page."""

import pandas as pd
from sqlalchemy import text

from immo_scanner.db.session import get_engine

TREND_SCOPES = ["Bezirksregion", "Bezirk", "Berlin"]


def load_crime() -> pd.DataFrame:
    query = """
        SELECT level, area_id, year, offense, cases, rate, name, label
        FROM crime_stats
    """
    with get_engine().connect() as conn:
        return pd.read_sql(text(query), conn)


def crime_trend(stats: pd.DataFrame, offense: str, area_id: str, bezirk: str) -> pd.DataFrame:
    """Rate and cases over the years for one Bezirksregion, its Bezirk and Berlin."""
    masks = {
        "Bezirksregion": (stats["level"] == "bzr") & (stats["area_id"] == area_id),
        "Bezirk": (stats["level"] == "bezirk") & (stats["area_id"] == bezirk),
        "Berlin": stats["level"] == "city",
    }
    frames = []
    for scope, mask in masks.items():
        part = stats.loc[mask & (stats["offense"] == offense), ["year", "rate", "cases"]].copy()
        part["scope"] = scope
        frames.append(part)
    if not frames:
        return pd.DataFrame(columns=["year", "rate", "cases", "scope"])
    return pd.concat(frames, ignore_index=True)
