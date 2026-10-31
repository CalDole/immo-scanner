"""Match published Gymnasium statistics to OpenStreetMap school locations."""

import re
import unicodedata

import pandas as pd

_TITLE = re.compile(r"\b(gymnasium|oberschule|schule)\b", re.IGNORECASE)
_NON_WORD = re.compile(r"[^a-z0-9]+")


def normalize_school_name(name: str) -> str:
    """Fold a school name so 'Käthe-Kollwitz-Gymnasium' matches the Oberschule label."""
    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    folded = _TITLE.sub(" ", folded.lower())
    return " ".join(_NON_WORD.sub(" ", folded).split())


def attach_locations(stats: pd.DataFrame, schools: pd.DataFrame) -> pd.DataFrame:
    """Left-join each statistic row to one school point on the normalized name.

    OpenStreetMap sometimes lists the same Gymnasium twice. One point is kept.
    Schools with no exact name match stay in the frame without coordinates.
    """
    located = stats.copy()
    located["_key"] = located["name"].map(normalize_school_name)
    points = schools.copy()
    points["_key"] = points["name"].map(normalize_school_name)
    points = points.sort_values(["_key", "name"]).drop_duplicates("_key", keep="first")
    merged = located.merge(points.drop(columns=["name"]), on="_key", how="left")
    merged = merged.drop(columns=["_key"])
    teachers = pd.to_numeric(merged["teachers"], errors="coerce")
    students = pd.to_numeric(merged["students"], errors="coerce")
    merged["students_per_teacher"] = students / teachers.where(teachers > 0)
    return merged
