"""Listing scores: one 0–1-ish number per theme, then a weighted total.

See docs/scoring.md for the formulas and the assumptions where the spec was blank.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from statistics import fmean
from typing import Any

import pandas as pd

ENERGY_CLASS_SCORE = {
    "A+": 1.25,
    "A": 1.25,
    "B": 1.00,
    "C": 0.8,
    "D": 0.33,
}
HEATING_CAP_KWH = 220.0
COMMUTE_CAP_MIN = 60.0
HAUSGELD_GOOD = 300.0
HAUSGELD_BAD = 800.0
ROOMS_SCORE = {2: 0.0, 3: 1.0, 4: 1.5, 5: 2.0}
FLOOR_AREA_REF_M2 = 75.0
TOLERANCE_WEIGHTS = {
    "afd": 0.29,
    "cdu": 0.06,
    "spd": 0.04,
    "gruene": 0.02,
    "linke": 0.05,
    "fdp": 0.05,
    "bsw": 0.15,
    "volt": 0.01,
}
TOLERANCE_SCALE = 8.7
# Fixed anchors from the Planungsraum distribution, not recomputed when the data moves.
# Diversity index (0–100): 78 → 0, 88 → 1. German citizens: 76% → 1, 14 points away → 0.
# OIC share of residents: 5% → 1, 25% → 0.
ORIGIN_DIVERSITY_LOW = 78.0
ORIGIN_DIVERSITY_HIGH = 88.0
GERMAN_CITIZEN_MEDIAN = 76.0
GERMAN_CITIZEN_SPAN = 14.0
OIC_SHARE_ONE = 5.0
OIC_SHARE_ZERO = 25.0
# Neighbourhood offences per 100,000 residents, 2025 Bezirksregionen.
# 10th percentile 1,379 → 1,400; 90th percentile 4,572 → 4,600. Nearest 100.
CRIME_RATE_ONE = 1400.0
CRIME_RATE_ZERO = 4600.0
# Berliner Register incidents per 1,000 residents per year.
# 90th percentile of Planungsräume with at least 200 residents is 0.608 → 0.6.
INCIDENT_RATE_ZERO = 0.6
INCIDENT_MIN_POPULATION = 200
INCIDENT_WINDOW_YEARS = 3.0
# Schultypisierungsstufe of nearby secondary schools. Stufe 1 → 1, Stufe 7 → 0.
SCHOOL_TYPE_BEST = 1.0
SCHOOL_TYPE_WORST = 7.0


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if value != value:  # NaN
            return None
    except Exception:
        return None
    return float(value)


def avg_commute_minutes(work1: Any, work2: Any) -> float | None:
    a, b = _num(work1), _num(work2)
    if a is None and b is None:
        return None
    if b is None:
        return a
    if a is None:
        return b
    return (3 * a + 2 * b) / 5


def commute_score(work1: Any, work2: Any) -> float | None:
    """Shorter is better. 0 min → 1, 60 min or more → 0."""
    avg = avg_commute_minutes(work1, work2)
    if avg is None:
        return None
    return max(0.0, min(1.0, (COMMUTE_CAP_MIN - avg) / COMMUTE_CAP_MIN))


def energy_class_score(label: str | None) -> float | None:
    if not label:
        return None
    key = re.sub(r"[^A-G+]", "", str(label).upper())
    if not key:
        return None
    if key in ENERGY_CLASS_SCORE:
        return ENERGY_CLASS_SCORE[key]
    letter = key[0]
    if letter in ENERGY_CLASS_SCORE:
        return ENERGY_CLASS_SCORE[letter]
    if letter in {"E", "F", "G", "H"}:
        return 0.0
    return None


def hausgeld_score(euros_per_month: Any) -> float | None:
    """Lower Hausgeld is better. ≤ 300 € → 1, ≥ 800 € → 0."""
    amount = _num(euros_per_month)
    if amount is None:
        return None
    return max(0.0, min(1.0, (HAUSGELD_BAD - amount) / (HAUSGELD_BAD - HAUSGELD_GOOD)))


def building_age_score(age_years: Any) -> float | None:
    age = _num(age_years)
    if age is None:
        return None
    return max(0.0, 1.0 - max(0.0, age) / 100.0)


def building_age_class_score(climate_risk_score: Any) -> float | None:
    value = _num(climate_risk_score)
    if value is None:
        return None
    return max(0.0, min(1.0, value / 100.0))


def heating_demand_score(typical_kwh: Any) -> float | None:
    demand = _num(typical_kwh)
    if demand is None:
        return None
    return max(0.0, 1.0 - demand / HEATING_CAP_KWH)


def rooms_score(rooms: Any) -> float | None:
    value = _num(rooms)
    if value is None:
        return None
    if value <= 2:
        return 0.0
    if value <= 3:
        return value - 2
    if value <= 4:
        return 1.0 + (value - 3) * 0.5
    if value <= 5:
        return 1.5 + (value - 4) * 0.5
    return 2.0


def floor_area_score(living_area: Any) -> float | None:
    area = _num(living_area)
    if area is None:
        return None
    return area / FLOOR_AREA_REF_M2


def basement_score(has_cellar: bool | None) -> float | None:
    if has_cellar is None:
        return None
    return 1.0 if has_cellar else 0.8


def parking_score(has_parking: bool | None) -> float | None:
    if has_parking is None:
        return None
    return 1.0 if has_parking else 0.0


def balconies_score(count: Any, has_balcony: bool | None = None) -> float | None:
    n = _num(count)
    if n is None:
        if has_balcony is True:
            n = 1.0
        elif has_balcony is False:
            n = 0.0
        else:
            return None
    if n >= 2:
        return 1.5
    if n >= 1:
        return 1.0
    return 0.0


def bathrooms_score(bathrooms: Any) -> float | None:
    value = _num(bathrooms)
    if value is None:
        return None
    if value >= 2:
        return 1.0
    if value >= 1:
        return 0.5
    return 0.0


def kitchen_score(has_fitted_kitchen: bool | None) -> float | None:
    if has_fitted_kitchen is None:
        return None
    return 1.0 if has_fitted_kitchen else 0.95


def education_score(abi_averages: list[float | None]) -> float | None:
    values = [value for value in (_num(item) for item in abi_averages) if value is not None]
    if not values:
        return None
    return 1.0 - fmean(values) / 6.0


def origin_diversity_score(index_0_100: Any) -> float | None:
    """Gini-Simpson index on a 0–100 scale. 78 → 0, 88 → 1."""
    value = _num(index_0_100)
    if value is None:
        return None
    span = ORIGIN_DIVERSITY_HIGH - ORIGIN_DIVERSITY_LOW
    return max(0.0, min(1.0, (value - ORIGIN_DIVERSITY_LOW) / span))


def citizenship_score(german_share_pct: Any) -> float | None:
    """Distance from the median German-citizen share. 76% → 1; 14 points either way → 0."""
    value = _num(german_share_pct)
    if value is None:
        return None
    return max(0.0, 1.0 - abs(value - GERMAN_CITIZEN_MEDIAN) / GERMAN_CITIZEN_SPAN)


def oic_score(share_pct: Any) -> float | None:
    """Share of residents with a migration background from OIC countries. 5% → 1, 25% → 0."""
    value = _num(share_pct)
    if value is None:
        return None
    span = OIC_SHARE_ZERO - OIC_SHARE_ONE
    return max(0.0, min(1.0, (OIC_SHARE_ZERO - value) / span))


def crime_score(rate_per_100k: Any) -> float | None:
    """Neighbourhood offences per 100,000 residents. 1,400 → 1, 4,600 → 0."""
    value = _num(rate_per_100k)
    if value is None:
        return None
    span = CRIME_RATE_ZERO - CRIME_RATE_ONE
    return max(0.0, min(1.0, (CRIME_RATE_ZERO - value) / span))


def incident_score(rate_per_1000: Any) -> float | None:
    """Berliner Register incidents per 1,000 residents per year. 0 → 1, 0.6 → 0."""
    value = _num(rate_per_1000)
    if value is None:
        return None
    return max(0.0, min(1.0, 1.0 - value / INCIDENT_RATE_ZERO))


def school_type_score(stufe: Any) -> float | None:
    """Distance-weighted Schultypisierungsstufe. Stufe 1 → 1, Stufe 7 → 0."""
    value = _num(stufe)
    if value is None:
        return None
    span = SCHOOL_TYPE_WORST - SCHOOL_TYPE_BEST
    return max(0.0, min(1.0, (SCHOOL_TYPE_WORST - value) / span))


def tolerance_score(shares: dict[str, float | None]) -> float | None:
    """`shares` are Zweitstimmen in percent (0–100). Missing parties count as 0."""
    if not any(_num(shares.get(party)) is not None for party in TOLERANCE_WEIGHTS):
        return None
    weighted = sum(
        TOLERANCE_WEIGHTS[party] * (_num(shares.get(party)) or 0.0) for party in TOLERANCE_WEIGHTS
    )
    scaled = weighted / TOLERANCE_SCALE
    if scaled <= 0:
        return None
    return 1.0 / scaled


@dataclass(frozen=True)
class SubScore:
    column: str
    group: str
    label: str
    rule: str
    # Highest value the rule can give; None when it has no cap.
    max_value: float | None = 1.0


@dataclass(frozen=True)
class Group:
    column: str
    label: str


GROUPS: tuple[Group, ...] = (
    Group("distance_score", "Distance"),
    Group("building_score", "Building"),
    Group("flat_score", "Flat"),
    Group("neighborhood_score", "Neighbourhood"),
)

SUBSCORES: tuple[SubScore, ...] = (
    SubScore(
        "commute_score",
        "distance_score",
        "Commute",
        f"Weighted 3:2 over both workplaces. 0 min = 1, {COMMUTE_CAP_MIN:.0f} min or more = 0",
    ),
    SubScore(
        "energy_class_score",
        "building_score",
        "Energy class",
        "A+ and A = 1.25, B = 1.0, C = 0.8, D = 0.33, E or worse = 0",
        1.25,
    ),
    SubScore(
        "hausgeld_score",
        "building_score",
        "Hausgeld",
        f"{HAUSGELD_GOOD:.0f} € or less = 1, {HAUSGELD_BAD:.0f} € or more = 0",
    ),
    SubScore(
        "building_age_score",
        "building_score",
        "Building age",
        "1 − age / 100, so new = 1 and 100 years or older = 0",
    ),
    SubScore(
        "building_age_class_score",
        "building_score",
        "Age class",
        "Climate risk score of the construction period / 100",
    ),
    SubScore(
        "heating_demand_score",
        "building_score",
        "Heating demand",
        f"1 − typical kWh/m²a of the construction period / {HEATING_CAP_KWH:.0f}",
    ),
    SubScore(
        "rooms_score",
        "flat_score",
        "Rooms",
        "2 rooms = 0, 3 = 1, 4 = 1.5, 5 or more = 2, in between interpolated",
        2.0,
    ),
    SubScore(
        "floor_area_score",
        "flat_score",
        "Floor area",
        f"Living area / {FLOOR_AREA_REF_M2:.0f} m², no cap",
        None,
    ),
    SubScore("basement_score", "flat_score", "Cellar", "Cellar = 1, no cellar = 0.8"),
    SubScore("parking_score", "flat_score", "Parking", "Garage or parking space = 1, none = 0"),
    SubScore(
        "balconies_score", "flat_score", "Balconies", "2 or more = 1.5, 1 = 1, none = 0", 1.5
    ),
    SubScore("bathrooms_score", "flat_score", "Bathrooms", "2 or more = 1, 1 = 0.5, none = 0"),
    SubScore("kitchen_score", "flat_score", "Fitted kitchen", "Fitted kitchen = 1, none = 0.95"),
    SubScore(
        "education_score",
        "neighborhood_score",
        "Education",
        "1 − mean Abitur average of the three nearest Gymnasien / 6",
    ),
    SubScore(
        "school_type_score",
        "neighborhood_score",
        "Schultypisierung",
        f"Distance-weighted Schultypisierungsstufe of the six nearest Gymnasiums or "
        f"Sekundarschulen. Stufe {SCHOOL_TYPE_BEST:.0f} = 1, "
        f"Stufe {SCHOOL_TYPE_WORST:.0f} = 0",
    ),
    SubScore(
        "tolerance_score",
        "neighborhood_score",
        "Tolerance",
        f"1 / (weighted AGH 2026 party shares / {TOLERANCE_SCALE}); AfD weighs most, then BSW",
        None,
    ),
    SubScore(
        "origin_diversity_score",
        "neighborhood_score",
        "Origin diversity",
        f"({ORIGIN_DIVERSITY_LOW:.0f} → 0, {ORIGIN_DIVERSITY_HIGH:.0f} → 1) "
        "from the Gini-Simpson index of migration-background origins",
    ),
    SubScore(
        "citizenship_score",
        "neighborhood_score",
        "Citizenship",
        f"1 − |German-citizen share − {GERMAN_CITIZEN_MEDIAN:.0f}| / {GERMAN_CITIZEN_SPAN:.0f}; "
        f"{GERMAN_CITIZEN_MEDIAN:.0f}% → 1",
    ),
    SubScore(
        "oic_score",
        "neighborhood_score",
        "OIC origin",
        f"({OIC_SHARE_ONE:.0f}% → 1, {OIC_SHARE_ZERO:.0f}% → 0) "
        "share of residents with an OIC migration background",
    ),
    SubScore(
        "crime_score",
        "neighborhood_score",
        "Crime",
        f"Neighbourhood offences per 100,000 in the Bezirksregion, latest year. "
        f"{CRIME_RATE_ONE:,.0f} or fewer = 1, {CRIME_RATE_ZERO:,.0f} or more = 0",
    ),
    SubScore(
        "incident_score",
        "neighborhood_score",
        "Racist incidents",
        f"Berliner Register incidents per 1,000 residents per year over "
        f"{INCIDENT_WINDOW_YEARS:.0f} years, placed at a street or station. "
        f"0 = 1, {INCIDENT_RATE_ZERO:g} or more = 0. The register counts what is reported. "
        f"Areas under {INCIDENT_MIN_POPULATION} residents are left out",
    ),
)

SUBSCORE_BY_COLUMN = {sub.column: sub for sub in SUBSCORES}


def subscores_of(group_column: str) -> list[SubScore]:
    return [sub for sub in SUBSCORES if sub.group == group_column]


@dataclass
class Weights:
    """Relative weights. Only the ratios inside a group, and between groups, matter."""

    groups: dict[str, float] = field(
        default_factory=lambda: {group.column: 1.0 for group in GROUPS}
    )
    subscores: dict[str, float] = field(
        default_factory=lambda: {sub.column: 1.0 for sub in SUBSCORES}
    )

    def group(self, column: str) -> float:
        return self.groups.get(column, 1.0)

    def subscore(self, column: str) -> float:
        return self.subscores.get(column, 1.0)

    def to_dict(self) -> dict[str, dict[str, float]]:
        return {"groups": dict(self.groups), "subscores": dict(self.subscores)}

    @classmethod
    def from_dict(cls, raw: Any) -> Weights:
        """Unknown keys are dropped, missing ones are 1, negative values are 0."""
        weights = cls()
        if not isinstance(raw, dict):
            return weights
        for target, key in ((weights.groups, "groups"), (weights.subscores, "subscores")):
            values = raw.get(key)
            if not isinstance(values, dict):
                continue
            for column in target:
                value = _num(values.get(column))
                if value is not None:
                    target[column] = max(0.0, value)
        return weights


DEFAULT_WEIGHTS = Weights()


def _weighted_mean(pairs: list[tuple[float | None, float]]) -> float | None:
    present = [(value, weight) for value, weight in pairs if value is not None and weight > 0]
    total_weight = sum(weight for _, weight in present)
    if total_weight <= 0:
        return None
    return sum(value * weight for value, weight in present) / total_weight


def group_scores(
    parts: dict[str, float | None], weights: Weights | None = None
) -> dict[str, float | None]:
    """Weighted mean per group, then over the groups. Missing inputs are left out."""
    weights = weights or DEFAULT_WEIGHTS
    result: dict[str, float | None] = {}
    for group in GROUPS:
        result[group.column] = _weighted_mean(
            [
                (_num(parts.get(sub.column)), weights.subscore(sub.column))
                for sub in subscores_of(group.column)
            ]
        )
    result["total_score"] = _weighted_mean(
        [(result[group.column], weights.group(group.column)) for group in GROUPS]
    )
    return result


def _weighted_frame_mean(frame: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    numerator = pd.Series(0.0, index=frame.index)
    denominator = pd.Series(0.0, index=frame.index)
    for column, weight in weights.items():
        if weight <= 0:
            continue
        values = pd.to_numeric(frame[column], errors="coerce")
        numerator += values.fillna(0.0) * weight
        denominator += values.notna() * weight
    return (numerator / denominator.where(denominator > 0)).astype(float)


def apply_weights(df: pd.DataFrame, weights: Weights | None = None) -> pd.DataFrame:
    """Recompute the group scores and `total_score` from the sub-score columns.

    A group whose sub-score columns are all absent keeps the column it came with.
    """
    weights = weights or DEFAULT_WEIGHTS
    if df.empty:
        return df
    df = df.copy()
    for group in GROUPS:
        present = {
            sub.column: weights.subscore(sub.column)
            for sub in subscores_of(group.column)
            if sub.column in df.columns
        }
        if present:
            df[group.column] = _weighted_frame_mean(df, present)
    groups = {group.column: weights.group(group.column) for group in GROUPS if group.column in df}
    if groups:
        df["total_score"] = _weighted_frame_mean(df, groups)
    return df


def effective_shares(weights: Weights) -> dict[str, float]:
    """Each sub-score's share of the total when every input is present."""
    group_total = sum(weights.group(group.column) for group in GROUPS)
    shares: dict[str, float] = {}
    for group in GROUPS:
        subs = subscores_of(group.column)
        sub_total = sum(weights.subscore(sub.column) for sub in subs)
        group_share = weights.group(group.column) / group_total if group_total > 0 else 0.0
        for sub in subs:
            sub_share = weights.subscore(sub.column) / sub_total if sub_total > 0 else 0.0
            shares[sub.column] = group_share * sub_share
    return shares


def _view_body(sql: str) -> str:
    prefix = "CREATE MATERIALIZED VIEW listing_features AS"
    sql = sql.strip()
    if not sql.startswith(prefix):
        raise RuntimeError("listing_features SQL does not start with CREATE MATERIALIZED VIEW")
    body = sql[len(prefix) :].strip()
    return re.sub(r"\s*WITH NO DATA\s*$", "", body, flags=re.IGNORECASE)


def wrap_listing_features_sql(inner_sql: str) -> str:
    """Wrap the existing listing_features SELECT and append the score columns."""
    body = _view_body(inner_sql)
    return f"""
CREATE MATERIALIZED VIEW listing_features AS
SELECT
    base.*,
    GREATEST(0.0, LEAST(1.0,
        (60.0 - (
            CASE
                WHEN base.commute_minutes IS NULL AND base.commute_workplace2_minutes IS NULL
                    THEN NULL
                WHEN base.commute_workplace2_minutes IS NULL THEN base.commute_minutes
                WHEN base.commute_minutes IS NULL THEN base.commute_workplace2_minutes
                ELSE (3 * base.commute_minutes + 2 * base.commute_workplace2_minutes) / 5.0
            END
        )) / 60.0
    )) AS commute_score,
    CASE
        WHEN energy.cls IN ('A+', 'A') THEN 1.25
        WHEN energy.cls = 'B' THEN 1.00
        WHEN energy.cls = 'C' THEN 0.8
        WHEN energy.cls = 'D' THEN 0.33
        WHEN energy.cls IN ('E', 'F', 'G', 'H') THEN 0
    END AS energy_class_score,
    GREATEST(0.0, LEAST(1.0, (800.0 - base.hausgeld) / 500.0)) AS hausgeld_score,
    GREATEST(0.0, 1.0 - GREATEST(0.0, base.building_age_years) / 100.0) AS building_age_score,
    GREATEST(0.0, LEAST(1.0, bac.climate_risk_score / 100.0)) AS building_age_class_score,
    GREATEST(0.0, 1.0 - bac.typical_heating_demand_kwh / 220.0) AS heating_demand_score,
    CASE
        WHEN base.rooms IS NULL THEN NULL
        WHEN base.rooms <= 2 THEN 0.0
        WHEN base.rooms <= 3 THEN base.rooms - 2
        WHEN base.rooms <= 4 THEN 1.0 + (base.rooms - 3) * 0.5
        WHEN base.rooms <= 5 THEN 1.5 + (base.rooms - 4) * 0.5
        ELSE 2.0
    END AS rooms_score,
    base.living_area / 75.0 AS floor_area_score,
    CASE
        WHEN base.has_cellar IS TRUE THEN 1.0
        WHEN base.has_cellar IS FALSE THEN 0.8
    END AS basement_score,
    CASE
        WHEN base.has_parking IS TRUE THEN 1.0
        WHEN base.has_parking IS FALSE THEN 0.0
    END AS parking_score,
    CASE
        WHEN COALESCE(base.balcony_count,
                      CASE WHEN base.has_balcony IS TRUE THEN 1
                           WHEN base.has_balcony IS FALSE THEN 0 END) >= 2 THEN 1.5
        WHEN COALESCE(base.balcony_count,
                      CASE WHEN base.has_balcony IS TRUE THEN 1
                           WHEN base.has_balcony IS FALSE THEN 0 END) = 1 THEN 1.0
        WHEN COALESCE(base.balcony_count,
                      CASE WHEN base.has_balcony IS TRUE THEN 1
                           WHEN base.has_balcony IS FALSE THEN 0 END) = 0 THEN 0.0
    END AS balconies_score,
    CASE
        WHEN base.bathrooms IS NULL THEN NULL
        WHEN base.bathrooms >= 2 THEN 1.0
        WHEN base.bathrooms >= 1 THEN 0.5
        ELSE 0.0
    END AS bathrooms_score,
    CASE
        WHEN base.has_fitted_kitchen IS TRUE THEN 1.0
        WHEN base.has_fitted_kitchen IS FALSE THEN 0.95
    END AS kitchen_score,
    1.0 - (
        (COALESCE(gym_abi_1.abi_average, 0)
         + COALESCE(gym_abi_2.abi_average, 0)
         + COALESCE(gym_abi_3.abi_average, 0))
        / NULLIF(
            (gym_abi_1.abi_average IS NOT NULL)::int
            + (gym_abi_2.abi_average IS NOT NULL)::int
            + (gym_abi_3.abi_average IS NOT NULL)::int,
            0
        )
    ) / 6.0 AS education_score,
    1.0 / NULLIF((
        COALESCE(base.agh_vote_afd, 0) * 0.29
        + COALESCE(base.agh_vote_cdu, 0) * 0.06
        + COALESCE(base.agh_vote_spd, 0) * 0.04
        + COALESCE(base.agh_vote_gruene, 0) * 0.02
        + COALESCE(base.agh_vote_linke, 0) * 0.05
        + COALESCE(base.agh_vote_fdp, 0) * 0.05
        + COALESCE(base.agh_vote_bsw, 0) * 0.15
        + COALESCE(
            NULLIF(base.agh_party_shares ->> 'voltdeutschland', '')::float,
            NULLIF(base.agh_party_shares ->> 'volt', '')::float,
            0
        ) * 0.01
    ) / 8.7, 0) AS tolerance_score
FROM ({body}) base
LEFT JOIN building_age_classes bac
    ON base.year_built IS NOT NULL
   AND (bac.year_from IS NULL OR base.year_built >= bac.year_from)
   AND (bac.year_to IS NULL OR base.year_built <= bac.year_to)
LEFT JOIN LATERAL (
    SELECT gs.abi_average
    FROM gymnasium_statistics gs
    WHERE base.nearest_gymnasium IS NOT NULL
      AND (gs.name = base.nearest_gymnasium
           OR gs.name ILIKE '%' || base.nearest_gymnasium || '%'
           OR base.nearest_gymnasium ILIKE '%' || gs.name || '%')
    ORDER BY (gs.name = base.nearest_gymnasium) DESC, gs.abi_average
    LIMIT 1
) gym_abi_1 ON true
LEFT JOIN LATERAL (
    SELECT gs.abi_average
    FROM gymnasium_statistics gs
    WHERE base.nearest_gymnasium_2 IS NOT NULL
      AND (gs.name = base.nearest_gymnasium_2
           OR gs.name ILIKE '%' || base.nearest_gymnasium_2 || '%'
           OR base.nearest_gymnasium_2 ILIKE '%' || gs.name || '%')
    ORDER BY (gs.name = base.nearest_gymnasium_2) DESC, gs.abi_average
    LIMIT 1
) gym_abi_2 ON true
LEFT JOIN LATERAL (
    SELECT gs.abi_average
    FROM gymnasium_statistics gs
    WHERE base.nearest_gymnasium_3 IS NOT NULL
      AND (gs.name = base.nearest_gymnasium_3
           OR gs.name ILIKE '%' || base.nearest_gymnasium_3 || '%'
           OR base.nearest_gymnasium_3 ILIKE '%' || gs.name || '%')
    ORDER BY (gs.name = base.nearest_gymnasium_3) DESC, gs.abi_average
    LIMIT 1
) gym_abi_3 ON true
CROSS JOIN LATERAL (
    SELECT UPPER(REGEXP_REPLACE(COALESCE(base.energy_efficiency_class, ''), '[^A-G+]', '', 'g'))
        AS cls
) energy
"""


def add_group_score_sql(scored_sql: str) -> str:
    """Wrap the already-scored SELECT so group averages can use the sub-score aliases."""
    body = _view_body(scored_sql)
    return f"""
CREATE MATERIALIZED VIEW listing_features AS
SELECT
    s.*,
    s.commute_score AS distance_score,
    (COALESCE(s.energy_class_score, 0) + COALESCE(s.hausgeld_score, 0)
        + COALESCE(s.building_age_score, 0) + COALESCE(s.building_age_class_score, 0)
        + COALESCE(s.heating_demand_score, 0))
    / NULLIF(
        (s.energy_class_score IS NOT NULL)::int
        + (s.hausgeld_score IS NOT NULL)::int
        + (s.building_age_score IS NOT NULL)::int
        + (s.building_age_class_score IS NOT NULL)::int
        + (s.heating_demand_score IS NOT NULL)::int,
        0
    ) AS building_score,
    (COALESCE(s.rooms_score, 0) + COALESCE(s.floor_area_score, 0)
        + COALESCE(s.basement_score, 0) + COALESCE(s.parking_score, 0)
        + COALESCE(s.balconies_score, 0) + COALESCE(s.bathrooms_score, 0)
        + COALESCE(s.kitchen_score, 0))
    / NULLIF(
        (s.rooms_score IS NOT NULL)::int
        + (s.floor_area_score IS NOT NULL)::int
        + (s.basement_score IS NOT NULL)::int
        + (s.parking_score IS NOT NULL)::int
        + (s.balconies_score IS NOT NULL)::int
        + (s.bathrooms_score IS NOT NULL)::int
        + (s.kitchen_score IS NOT NULL)::int,
        0
    ) AS flat_score,
    (COALESCE(s.education_score, 0) + COALESCE(s.tolerance_score, 0))
    / NULLIF(
        (s.education_score IS NOT NULL)::int + (s.tolerance_score IS NOT NULL)::int,
        0
    ) AS neighborhood_score
FROM ({body}) s
"""


def add_total_score_sql(grouped_sql: str) -> str:
    body = _view_body(grouped_sql)
    return f"""
CREATE MATERIALIZED VIEW listing_features AS
SELECT
    g.*,
    (COALESCE(g.distance_score, 0) + COALESCE(g.building_score, 0)
        + COALESCE(g.flat_score, 0) + COALESCE(g.neighborhood_score, 0))
    / NULLIF(
        (g.distance_score IS NOT NULL)::int
        + (g.building_score IS NOT NULL)::int
        + (g.flat_score IS NOT NULL)::int
        + (g.neighborhood_score IS NOT NULL)::int,
        0
    ) AS total_score
FROM ({body}) g
"""


_NEIGHBORHOOD_BEFORE = """\
    (COALESCE(s.education_score, 0) + COALESCE(s.tolerance_score, 0))
    / NULLIF(
        (s.education_score IS NOT NULL)::int + (s.tolerance_score IS NOT NULL)::int,
        0
    ) AS neighborhood_score"""

_NEIGHBORHOOD_AFTER = """\
    (COALESCE(s.education_score, 0) + COALESCE(s.tolerance_score, 0)
        + COALESCE(s.origin_diversity_score, 0) + COALESCE(s.citizenship_score, 0)
        + COALESCE(s.oic_score, 0))
    / NULLIF(
        (s.education_score IS NOT NULL)::int
        + (s.tolerance_score IS NOT NULL)::int
        + (s.origin_diversity_score IS NOT NULL)::int
        + (s.citizenship_score IS NOT NULL)::int
        + (s.oic_score IS NOT NULL)::int,
        0
    ) AS neighborhood_score"""

_TOLERANCE_END = ") AS tolerance_score\nFROM (WITH latest AS ("

_ENERGY_END = ") energy) s) g"


def _origin_score_columns() -> str:
    diversity_span = ORIGIN_DIVERSITY_HIGH - ORIGIN_DIVERSITY_LOW
    oic_span = OIC_SHARE_ZERO - OIC_SHARE_ONE
    return f"""\
    origin_diversity.share AS origin_diversity_index,
    GREATEST(0.0, LEAST(1.0,
        (origin_diversity.share - {ORIGIN_DIVERSITY_LOW}) / {diversity_span}
    )) AS origin_diversity_score,
    german_citizens.value AS german_citizen_share,
    GREATEST(0.0, 1.0 - ABS(german_citizens.value - {GERMAN_CITIZEN_MEDIAN}) / {GERMAN_CITIZEN_SPAN})
        AS citizenship_score,
    origin_oic.share AS oic_share,
    GREATEST(0.0, LEAST(1.0, ({OIC_SHARE_ZERO} - origin_oic.share) / {oic_span})) AS oic_score
"""


_ORIGIN_JOINS = """
LEFT JOIN LATERAL (
    SELECT share
    FROM plr_origin_stats
    WHERE plr_id = base.plr_id AND origin_key = 'simpson'
    ORDER BY ref_date DESC
    LIMIT 1
) origin_diversity ON true
LEFT JOIN LATERAL (
    SELECT share
    FROM plr_origin_stats
    WHERE plr_id = base.plr_id AND origin_key = 'oic'
    ORDER BY ref_date DESC
    LIMIT 1
) origin_oic ON true
LEFT JOIN area_indicators german_citizens
    ON german_citizens.level = 'plr'
   AND german_citizens.area_id = base.plr_id
   AND german_citizens.indicator = 'german_share_mss'
"""


def with_origin_neighborhood_scores(sql: str) -> str:
    """Add the three origin scores and include them in the neighbourhood mean.

    The joins read `plr_origin_stats` and `area_indicators`, which later migrations
    create, so this wraps a finished listing_features statement instead of the
    original score SQL.
    """
    if sql.count(_NEIGHBORHOOD_BEFORE) != 1:
        raise RuntimeError("listing_features SQL no longer has the two-input neighbourhood score")
    if sql.count(_TOLERANCE_END) != 1:
        raise RuntimeError("listing_features SQL no longer ends the tolerance score as expected")
    if sql.count(_ENERGY_END) != 1:
        raise RuntimeError("listing_features SQL no longer ends the score joins as expected")
    sql = sql.replace(_NEIGHBORHOOD_BEFORE, _NEIGHBORHOOD_AFTER, 1)
    sql = sql.replace(
        _TOLERANCE_END,
        ") AS tolerance_score,\n" + _origin_score_columns() + "FROM (WITH latest AS (",
        1,
    )
    return sql.replace(_ENERGY_END, ") energy" + _ORIGIN_JOINS + ") s) g", 1)


_NEIGHBORHOOD_WITH_SAFETY = """\
    (COALESCE(s.education_score, 0) + COALESCE(s.tolerance_score, 0)
        + COALESCE(s.origin_diversity_score, 0) + COALESCE(s.citizenship_score, 0)
        + COALESCE(s.oic_score, 0)
        + COALESCE(s.crime_score, 0) + COALESCE(s.incident_score, 0))
    / NULLIF(
        (s.education_score IS NOT NULL)::int
        + (s.tolerance_score IS NOT NULL)::int
        + (s.origin_diversity_score IS NOT NULL)::int
        + (s.citizenship_score IS NOT NULL)::int
        + (s.oic_score IS NOT NULL)::int
        + (s.crime_score IS NOT NULL)::int
        + (s.incident_score IS NOT NULL)::int,
        0
    ) AS neighborhood_score"""

_OIC_SCORE_END = ") AS oic_score\nFROM (WITH latest AS ("

_GERMAN_CITIZENS_END = """\
   AND german_citizens.indicator = 'german_share_mss'
) s) g"""


def _safety_score_columns() -> str:
    span = CRIME_RATE_ZERO - CRIME_RATE_ONE
    years = INCIDENT_WINDOW_YEARS
    return f"""\
    crime.rate AS crime_rate,
    crime.year AS crime_year,
    GREATEST(0.0, LEAST(1.0, ({CRIME_RATE_ZERO} - crime.rate) / {span})) AS crime_score,
    CASE
        WHEN base.lor_population IS NULL OR base.lor_population < {INCIDENT_MIN_POPULATION:.0f}
            THEN NULL
        ELSE COALESCE(incident_counts.incidents, 0)
    END AS incident_count,
    CASE
        WHEN base.lor_population IS NULL OR base.lor_population < {INCIDENT_MIN_POPULATION:.0f}
            THEN NULL
        ELSE COALESCE(incident_counts.incidents, 0) / base.lor_population * 1000.0 / {years}
    END AS incident_rate,
    CASE
        WHEN base.lor_population IS NULL OR base.lor_population < {INCIDENT_MIN_POPULATION:.0f}
            THEN NULL
        ELSE GREATEST(0.0, LEAST(1.0, 1.0 - (
            COALESCE(incident_counts.incidents, 0) / base.lor_population * 1000.0 / {years}
        ) / {INCIDENT_RATE_ZERO}))
    END AS incident_score
"""


_SAFETY_JOINS = f"""
LEFT JOIN LATERAL (
    SELECT rate, year
    FROM crime_stats
    WHERE level = 'bzr'
      AND area_id = left(base.plr_id, 6)
      AND offense = 'neighbourhood'
    ORDER BY year DESC
    LIMIT 1
) crime ON true
LEFT JOIN (
    SELECT plr_id, count(*)::float AS incidents
    FROM register_incidents
    WHERE geo_precision IN ('street', 'station')
      AND incident_date > (
          SELECT max(incident_date) - INTERVAL '{INCIDENT_WINDOW_YEARS:.0f} years'
          FROM register_incidents
      )
      AND incident_date <= (SELECT max(incident_date) FROM register_incidents)
    GROUP BY plr_id
) incident_counts ON incident_counts.plr_id = base.plr_id
"""


def with_safety_neighborhood_scores(sql: str) -> str:
    """Add crime and Berliner Register scores and include them in the neighbourhood mean.

    Expects the listing_features SQL that already includes the origin scores. The joins
    read `crime_stats` and `register_incidents`, created by later migrations.
    """
    if sql.count(_NEIGHBORHOOD_AFTER) != 1:
        raise RuntimeError("listing_features SQL no longer has the five-input neighbourhood score")
    if sql.count(_OIC_SCORE_END) != 1:
        raise RuntimeError("listing_features SQL no longer ends the OIC score as expected")
    if sql.count(_GERMAN_CITIZENS_END) != 1:
        raise RuntimeError("listing_features SQL no longer ends the origin joins as expected")
    sql = sql.replace(_NEIGHBORHOOD_AFTER, _NEIGHBORHOOD_WITH_SAFETY, 1)
    sql = sql.replace(
        _OIC_SCORE_END,
        ") AS oic_score,\n" + _safety_score_columns() + "FROM (WITH latest AS (",
        1,
    )
    return sql.replace(
        _GERMAN_CITIZENS_END,
        "   AND german_citizens.indicator = 'german_share_mss'" + _SAFETY_JOINS + ") s) g",
        1,
    )


_NEIGHBORHOOD_WITH_SCHOOL_TYPE = """\
    (COALESCE(s.education_score, 0) + COALESCE(s.tolerance_score, 0)
        + COALESCE(s.origin_diversity_score, 0) + COALESCE(s.citizenship_score, 0)
        + COALESCE(s.oic_score, 0)
        + COALESCE(s.crime_score, 0) + COALESCE(s.incident_score, 0)
        + COALESCE(s.school_type_score, 0))
    / NULLIF(
        (s.education_score IS NOT NULL)::int
        + (s.tolerance_score IS NOT NULL)::int
        + (s.origin_diversity_score IS NOT NULL)::int
        + (s.citizenship_score IS NOT NULL)::int
        + (s.oic_score IS NOT NULL)::int
        + (s.crime_score IS NOT NULL)::int
        + (s.incident_score IS NOT NULL)::int
        + (s.school_type_score IS NOT NULL)::int,
        0
    ) AS neighborhood_score"""

_INCIDENT_SCORE_END = "END AS incident_score\nFROM (WITH latest AS ("

_INCIDENT_JOIN_END = """) incident_counts ON incident_counts.plr_id = base.plr_id
) s) g"""


def _school_type_score_columns() -> str:
    span = SCHOOL_TYPE_WORST - SCHOOL_TYPE_BEST
    return f"""\
    school_type.value AS school_type_level,
    GREATEST(0.0, LEAST(1.0, ({SCHOOL_TYPE_WORST} - school_type.value) / {span}))
        AS school_type_score
"""


_SCHOOL_TYPE_JOIN = """
LEFT JOIN LATERAL (
    SELECT value
    FROM area_indicators
    WHERE level = 'plr'
      AND area_id = base.plr_id
      AND indicator = 'nearby_school_type_level'
    LIMIT 1
) school_type ON true
"""


def with_school_type_neighborhood_score(sql: str) -> str:
    """Add the Schultypisierung score and include it in the neighbourhood mean.

    Expects the listing_features SQL that already includes the safety scores. The join
    reads `area_indicators`, which an earlier migration creates.
    """
    if sql.count(_NEIGHBORHOOD_WITH_SAFETY) != 1:
        raise RuntimeError("listing_features SQL no longer has the seven-input neighbourhood score")
    if sql.count(_INCIDENT_SCORE_END) != 1:
        raise RuntimeError("listing_features SQL no longer ends the incident score as expected")
    if sql.count(_INCIDENT_JOIN_END) != 1:
        raise RuntimeError("listing_features SQL no longer ends the incident join as expected")
    sql = sql.replace(_NEIGHBORHOOD_WITH_SAFETY, _NEIGHBORHOOD_WITH_SCHOOL_TYPE, 1)
    sql = sql.replace(
        _INCIDENT_SCORE_END,
        "END AS incident_score,\n" + _school_type_score_columns() + "FROM (WITH latest AS (",
        1,
    )
    return sql.replace(
        _INCIDENT_JOIN_END,
        ") incident_counts ON incident_counts.plr_id = base.plr_id" + _SCHOOL_TYPE_JOIN + ") s) g",
        1,
    )
