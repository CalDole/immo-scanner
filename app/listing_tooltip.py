"""Hover text for a listing on the Flats map and the Scores chart."""

import html
import math
from collections.abc import Mapping
from typing import Any

import pandas as pd

from immo_scanner.explore.incidents import MIN_POPULATION
from immo_scanner.explore.scoring import INCIDENT_WINDOW_YEARS

COMMUTE_LABELS = (
    ("commute_minutes", "to work"),
    ("commute_workplace2_minutes", "to work 2"),
    ("commute_citycenter_minutes", "to city centre"),
)
AREA_COLUMNS = (
    "nearby_school_abi_average",
    "nearby_school_type_level",
    "mh_share",
    "mh_population",
    "avg_price_per_m2",
)
# Column, chart title. Values are already formatted.
CHART_FIELDS = (
    ("title", "Title"),
    ("bezirk", "Bezirk"),
    ("price_label", "Preis"),
    ("area_label", "m²"),
    ("sqm_price_label", "€/m²"),
    ("sqm_area_label", "Ø €/m² Planungsraum"),
    ("rooms_label", "Zimmer"),
    ("commute_label", "Distanzen"),
    ("score_label", "Score"),
    ("abi_label", "Gewichtete Abiturnote"),
    ("type_label", "Gewichtete Schultypisierung"),
    ("german_label", "Deutsche Staatsbürger"),
    ("mh_label", "Migrationshintergrund"),
    ("oic_label", "Islamische Länder"),
    ("incident_mh_label", "Incidents / 1,000 Migrationshintergrund"),
    ("sbahn_label", "S-Bahn"),
    ("ubahn_label", "U-Bahn"),
)
_HOVER_INPUTS = (
    "price",
    "living_area",
    "price_per_m2",
    "avg_price_per_m2",
    "rooms",
    "commute_minutes",
    "commute_workplace2_minutes",
    "commute_citycenter_minutes",
    "total_score",
    "nearby_school_abi_average",
    "nearby_school_type_level",
    "german_citizen_share",
    "mh_share",
    "oic_share",
    "incident_count",
    "mh_population",
    "nearest_sbahn",
    "nearest_sbahn_m",
    "nearest_ubahn",
    "nearest_ubahn_m",
    "available_from",
)
MAP_TOOLTIP_HTML = (
    "<b>{title}</b><br/>"
    "{bezirk} · {ortsteil}<br/>{prognoseraum} · {kiez}<br/>"
    "{hover}"
)


def _missing(value: object) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _num(value: object) -> float | None:
    if _missing(value):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _text(value: object) -> str | None:
    if _missing(value):
        return None
    text = str(value).strip()
    return text or None


def _plr_key(values: pd.Series) -> pd.Series:
    return values.map(lambda value: "" if _missing(value) else str(value).strip())


def incidents_per_1000_mh(count: object, population: object) -> float | None:
    """Street- and station-placed incidents per 1,000 residents with a migration background.

    `count` is the listing's incident count over the same window as `incident_rate`.
    The rate is per year. Areas with fewer than MIN_POPULATION such residents are left out.
    """
    incidents = _num(count)
    people = _num(population)
    if incidents is None or people is None or people < MIN_POPULATION:
        return None
    return incidents / people * 1000.0 / INCIDENT_WINDOW_YEARS


def _euro(value: object) -> str | None:
    number = _num(value)
    if number is None:
        return None
    return f"{number:,.0f} €"


def _sqm(value: object) -> str | None:
    number = _num(value)
    if number is None:
        return None
    return f"{number:,.0f} €/m²"


def _area(value: object) -> str | None:
    number = _num(value)
    if number is None:
        return None
    return f"{number:.0f} m²"


def _rooms(value: object) -> str | None:
    number = _num(value)
    if number is None:
        return None
    if number.is_integer():
        return f"{int(number)} Zi"
    return f"{number:.1f} Zi"


def _grade(value: object) -> str | None:
    number = _num(value)
    if number is None:
        return None
    return f"{number:.2f}"


def _percent(value: object) -> str | None:
    number = _num(value)
    if number is None:
        return None
    return f"{number:.1f}%"


def _commute(row: Mapping[str, Any]) -> str | None:
    parts = []
    for column, label in COMMUTE_LABELS:
        number = _num(row.get(column))
        if number is not None:
            parts.append(f"{number:.0f} min {label}")
    return " · ".join(parts) or None


def _station(name: object, metres: object) -> str | None:
    station = _text(name)
    distance = _num(metres)
    if station and distance is not None:
        return f"{station} ({distance:.0f} m)"
    if station:
        return station
    if distance is not None:
        return f"{distance:.0f} m"
    return None


def _available(row: Mapping[str, Any]) -> str | None:
    text = _text(row.get("available_from"))
    if text is None:
        return None
    return f"Available: {text}"


def _join(parts: list[str | None]) -> str | None:
    kept = [part for part in parts if part]
    return " · ".join(kept) or None


def _hover(lines: list[str | None]) -> str:
    text = "\n".join(line for line in lines if line)
    return html.escape(text).replace("{", "&#123;").replace("}", "&#125;")


def listing_hover(row: Mapping[str, Any]) -> dict[str, str]:
    """Formatted hover fields. Missing facts are `–` in the chart columns and omitted on the map."""
    price = _euro(row.get("price"))
    area = _area(row.get("living_area"))
    sqm = _sqm(row.get("price_per_m2"))
    area_sqm = _sqm(row.get("avg_price_per_m2"))
    rooms = _rooms(row.get("rooms"))
    commute = _commute(row)
    score = _grade(row.get("total_score"))
    abi = _grade(row.get("nearby_school_abi_average"))
    type_level = _grade(row.get("nearby_school_type_level"))
    german = _percent(row.get("german_citizen_share"))
    migration = _percent(row.get("mh_share"))
    oic = _percent(row.get("oic_share"))
    rate = incidents_per_1000_mh(row.get("incident_count"), row.get("mh_population"))
    incident = f"{rate:.2f} / Jahr" if rate is not None else None
    sbahn = _station(row.get("nearest_sbahn"), row.get("nearest_sbahn_m"))
    ubahn = _station(row.get("nearest_ubahn"), row.get("nearest_ubahn_m"))
    available = _available(row)

    facts = {
        "price_label": price,
        "area_label": area,
        "sqm_price_label": sqm,
        "sqm_area_label": area_sqm,
        "rooms_label": rooms,
        "commute_label": commute,
        "score_label": score,
        "abi_label": abi,
        "type_label": type_level,
        "german_label": german,
        "mh_label": migration,
        "oic_label": oic,
        "incident_mh_label": incident,
        "sbahn_label": sbahn,
        "ubahn_label": ubahn,
    }
    lines = [
        _join([price, area, sqm, rooms]),
        f"Planungsraum Ø {area_sqm}" if area_sqm else None,
        commute,
        f"Score {score}" if score else None,
        _join(
            [
                f"Gewichtete Abiturnote {abi}" if abi else None,
                f"Gewichtete Schultypisierung {type_level}" if type_level else None,
            ]
        ),
        f"Deutsche Staatsbürger {german}" if german else None,
        f"Migrationshintergrund {migration}" if migration else None,
        f"Islamische Länder {oic}" if oic else None,
        f"Incidents / 1,000 Migrationshintergrund: {incident}" if incident else None,
        f"S: {sbahn}" if sbahn else None,
        f"U: {ubahn}" if ubahn else None,
        available,
    ]
    shown = {key: value or "–" for key, value in facts.items()}
    shown["hover"] = _hover(lines)
    shown["available_label"] = available or ""
    return shown


def attach_listing_hover(frame: pd.DataFrame, stats: pd.DataFrame) -> pd.DataFrame:
    """Copy `frame` with Planungsraum stats and the formatted hover columns."""
    enriched = _with_area_stats(frame, stats)
    present = [column for column in _HOVER_INPUTS if column in enriched.columns]
    records = enriched[present].to_dict(orient="records")
    labels = pd.DataFrame(
        [listing_hover(record) for record in records],
        index=enriched.index,
    )
    enriched = enriched.drop(columns=[column for column in labels.columns if column in enriched])
    return pd.concat([enriched, labels], axis=1)


def _with_area_stats(frame: pd.DataFrame, stats: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for column in AREA_COLUMNS:
        if column not in out.columns:
            out[column] = pd.NA
    if (
        "plr_id" not in out.columns
        or stats is None
        or stats.empty
        or "plr_id" not in stats.columns
    ):
        return out
    lookup = stats.dropna(subset=["plr_id"]).drop_duplicates("plr_id").copy()
    lookup.index = _plr_key(lookup["plr_id"])
    keys = _plr_key(out["plr_id"])
    for column in AREA_COLUMNS:
        if column not in lookup.columns:
            continue
        filled = keys.map(lookup[column])
        filled = filled.where(keys != "", pd.NA)
        current = out[column]
        out[column] = current.where(current.notna(), filled)
    return out
