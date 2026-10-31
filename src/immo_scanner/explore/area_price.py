"""Area asking-price model: which Planungsraum factors move with €/m².

The outcome is the mean asking price per m². Correlations use every factor.
The fit keeps the shorter list, standardizes each one, and weights areas by
how many listings they contribute. A coefficient is the €/m² difference
associated with one weighted standard deviation, the other factors held fixed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import NamedTuple

import numpy as np
import pandas as pd
import statsmodels.api as sm
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from immo_scanner.db.session import get_engine
from immo_scanner.explore.crime import load_crime
from immo_scanner.explore.demographics import load_age, load_areas, load_indicators
from immo_scanner.explore.education import load_planungsraum_education
from immo_scanner.explore.elections import (
    load_election_districts,
    load_plr_weights,
    party_shares_long,
    plr_party_shares,
    tolerance_scores,
)
from immo_scanner.explore.incidents import incident_date_range, incident_rates, load_incidents
from immo_scanner.explore.milieuschutz import load_coverage
from immo_scanner.explore.origins import load_origin_stats, map_values
from immo_scanner.explore.prices import load_area_listing_stats
from immo_scanner.explore.scoring import INCIDENT_WINDOW_YEARS

log = logging.getLogger(__name__)

MIN_LISTINGS = 3
# A fit factor blank on more than this share of eligible areas stays out.
MISSING_LIMIT = 0.25
PRICE = "price_per_m2"
# Alexanderplatz, the centre the distances are measured from.
ALEXANDERPLATZ_LAT = 52.521918
ALEXANDERPLATZ_LON = 13.413244

_CENTRE_SQL = """
SELECT plr_id AS area_id,
       ST_Distance(
           ST_Centroid(geom)::geography,
           ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography
       ) / 1000.0 AS value
FROM lor_planungsraeume
"""

_STATION_SQL = """
SELECT p.plr_id AS area_id,
       MIN(ST_Distance(ST_Centroid(p.geom)::geography, s.geom::geography)) / 1000.0 AS value
FROM lor_planungsraeume p
JOIN stations s ON s.is_sbahn OR s.is_ubahn
GROUP BY p.plr_id
"""

# area_indicators name -> factor key. Age bands come from load_age, not this map.
_INDICATORS = {
    "german_share_mss": "german_share",
    "mss_total": "migration_background",
    "nearby_school_abi_average": "school_abi",
    "nearby_school_type_level": "school_type",
    "sgb2_share": "unemployment",
    "low_wage_share": "low_wage",
    "sgb2_change": "unemployment_change",
    "foreign_change": "foreign_change",
    "youth_sgb2_share": "youth_unemployment",
}
_AGE = {
    "under_18": "under_18",
    "25_to_55": "age_25_to_54",
    "65_plus": "age_65_plus",
}

_TERM_COLUMNS = [
    "key",
    "label",
    "group",
    "coefficient",
    "std_error",
    "ci_low",
    "ci_high",
    "p_value",
    "step",
    "scale",
    "per_unit",
    "per_unit_low",
    "per_unit_high",
    "pct_of_average",
    "pct_low",
    "pct_high",
]
_CORR_COLUMNS = ["key", "label", "group", "fit_factor", "in_model", "r", "areas"]
_AREA_COLUMNS = ["area_id", "name", "bezirk", "listings", PRICE, "predicted", "residual"]


class Factor(NamedTuple):
    key: str
    label: str
    group: str
    in_fit: bool
    axis: str
    step: str


GROUP_ORDER = ("Politics", "Population", "Schools", "Work", "Safety", "Place", "Listings")

# Shares and changes are stored in percentage points, so one step is 18% to 19%.
_POINT = "1 percentage point"

FACTORS: tuple[Factor, ...] = (
    Factor("agh_vote_afd", "AfD share", "Politics", True, "AfD share (%)", _POINT),
    Factor("agh_vote_gruene", "Greens share", "Politics", True, "Greens share (%)", _POINT),
    Factor("agh_turnout", "AGH turnout", "Politics", True, "AGH turnout (%)", _POINT),
    Factor("tolerance_score", "Tolerance score", "Politics", False, "Tolerance score", "1 point"),
    Factor("german_share", "German citizens", "Population", True, "German citizens (%)", _POINT),
    Factor(
        "origin_diversity",
        "Origin diversity",
        "Population",
        True,
        "Origin diversity (0–100)",
        "1 point",
    ),
    Factor(
        "oic_share",
        "Islamic countries (OIC)",
        "Population",
        True,
        "Islamic countries (%)",
        _POINT,
    ),
    Factor("under_18", "Under 18", "Population", True, "Under 18 (%)", _POINT),
    Factor("age_25_to_54", "Aged 25 to 54", "Population", True, "Aged 25 to 54 (%)", _POINT),
    Factor("age_65_plus", "Aged 65 and over", "Population", True, "Aged 65 and over (%)", _POINT),
    Factor(
        "est_abitur",
        "Estimated Abitur share",
        "Population",
        True,
        "Estimated Abitur share (%)",
        _POINT,
    ),
    Factor(
        "migration_background",
        "Migration background",
        "Population",
        False,
        "Migration background (%)",
        _POINT,
    ),
    Factor(
        "school_abi",
        "Average Abiturnote",
        "Schools",
        True,
        "Average Abiturnote",
        "1 grade point",
    ),
    Factor(
        "school_type",
        "Schultypisierung",
        "Schools",
        True,
        "Schultypisierung (Stufe)",
        "1 Stufe",
    ),
    Factor("unemployment", "Unemployment", "Work", True, "Unemployment (%)", _POINT),
    Factor("low_wage", "Low-wage share", "Work", True, "Low-wage share (%)", _POINT),
    Factor(
        "unemployment_change",
        "Unemployment change",
        "Work",
        True,
        "Unemployment change (pp)",
        _POINT,
    ),
    Factor(
        "foreign_change",
        "Foreign-citizen change",
        "Work",
        True,
        "Foreign-citizen change (pp)",
        _POINT,
    ),
    Factor(
        "youth_unemployment",
        "Youth unemployment",
        "Work",
        False,
        "Youth unemployment (%)",
        _POINT,
    ),
    Factor(
        "incidents",
        "Incidents per 1,000",
        "Safety",
        True,
        "Incidents per 1,000 residents per year",
        "1 per 1,000",
    ),
    Factor(
        "crime_rate",
        "Crime rate",
        "Safety",
        True,
        "Neighbourhood offences per 100,000",
        "1 per 100,000",
    ),
    Factor(
        "milieuschutz",
        "Milieuschutz coverage",
        "Place",
        True,
        "Milieuschutz coverage (%)",
        _POINT,
    ),
    Factor(
        "centre_km",
        "Distance to centre",
        "Place",
        True,
        "Distance to Alexanderplatz (km)",
        "1 km",
    ),
    Factor(
        "station_km",
        "Distance to station",
        "Place",
        True,
        "Distance to nearest S- or U-Bahn (km)",
        "1 km",
    ),
    Factor(
        "median_living_area",
        "Median living area",
        "Listings",
        True,
        "Median living area (m²)",
        "1 m²",
    ),
    Factor(
        "median_year_built",
        "Median year built",
        "Listings",
        True,
        "Median year built",
        "1 year",
    ),
)
_BY_KEY = {factor.key: factor for factor in FACTORS}
_FRAME_COLUMNS = [
    "area_id",
    "name",
    "bezirk",
    PRICE,
    "listings",
    *[factor.key for factor in FACTORS],
]


@dataclass
class AreaPriceModel:
    """Weighted correlations and the standardized fit on areas with enough listings."""

    n_eligible: int
    n_used: int
    correlations: pd.DataFrame
    notes: tuple[str, ...]
    omitted: tuple[str, ...]
    robust: bool
    rsquared: float | None
    rmse: float | None
    median_abs_error: float | None
    terms: pd.DataFrame
    areas: pd.DataFrame
    error: str | None


def plr_turnout(weights: pd.DataFrame, districts: pd.DataFrame) -> pd.DataFrame:
    """Vote-weighted Abgeordnetenhaus turnout per Planungsraum.

    `weights` is the election-helper frame (scope, district id, plr_id, weight).
    The weight is the district's valid votes falling in that Planungsraum.
    """
    empty = pd.DataFrame(columns=["area_id", "value"])
    needed_w = {"scope", "district_id", "plr_id", "weight"}
    needed_d = {"scope", "area_id", "turnout_pct"}
    if weights.empty or districts.empty:
        return empty
    if not needed_w <= set(weights.columns) or not needed_d <= set(districts.columns):
        return empty
    merged = weights.merge(
        districts[["scope", "area_id", "turnout_pct"]],
        left_on=["scope", "district_id"],
        right_on=["scope", "area_id"],
        how="inner",
    )
    merged = merged[(merged["scope"] == "abgeordnetenhaus") & (merged["weight"] > 0)]
    merged["turnout_pct"] = pd.to_numeric(merged["turnout_pct"], errors="coerce")
    merged = merged[merged["turnout_pct"].notna()]
    if merged.empty:
        return empty
    merged["weighted"] = merged["weight"] * merged["turnout_pct"]
    grouped = merged.groupby("plr_id", sort=False)[["weighted", "weight"]].sum()
    result = (grouped["weighted"] / grouped["weight"]).rename("value").reset_index()
    return result.rename(columns={"plr_id": "area_id"})


def eligible_areas(frame: pd.DataFrame) -> pd.DataFrame:
    """Planungsräume with a price and at least MIN_LISTINGS qualifying ads."""
    if frame.empty or PRICE not in frame.columns or "listings" not in frame.columns:
        return frame.iloc[0:0].copy()
    out = frame.copy()
    if "area_id" in out.columns:
        out = out.drop_duplicates("area_id", keep="first")
    out[PRICE] = pd.to_numeric(out[PRICE], errors="coerce")
    out["listings"] = pd.to_numeric(out["listings"], errors="coerce")
    mask = out[PRICE].notna() & out["listings"].notna() & (out["listings"] >= MIN_LISTINGS)
    return out.loc[mask].reset_index(drop=True)


def weighted_correlations(frame: pd.DataFrame) -> pd.DataFrame:
    """Weighted Pearson r of each present factor with €/m².

    Weights are listing counts. Areas with fewer than MIN_LISTINGS ads are left out.
    `in_model` stays false here; the fit marks the factors it keeps.
    """
    eligible = eligible_areas(frame)
    rows = []
    for factor in FACTORS:
        if factor.key not in frame.columns:
            continue
        values = pd.to_numeric(eligible[factor.key], errors="coerce")
        paired = eligible.loc[values.notna()]
        r = np.nan
        if len(paired) >= 2:
            r = _pearson(
                pd.to_numeric(paired[factor.key], errors="coerce").to_numpy(dtype=float),
                paired[PRICE].to_numpy(dtype=float),
                paired["listings"].to_numpy(dtype=float),
            )
        rows.append(
            {
                "key": factor.key,
                "label": factor.label,
                "group": factor.group,
                "fit_factor": factor.in_fit,
                "in_model": False,
                "r": r,
                "areas": int(len(paired)),
            }
        )
    if not rows:
        return pd.DataFrame(columns=_CORR_COLUMNS)
    return pd.DataFrame(rows, columns=_CORR_COLUMNS)


def _per_step(
    key: str,
    scales: dict[str, float],
    coefficient: float,
    ci_low: float,
    ci_high: float,
    intercept: float,
) -> dict[str, float | str]:
    """Turn one standardized coefficient into euros and percent per raw step.

    The intercept row stays blank. A step is one unit of the factor as stored,
    and the percent is that euro step divided by the average €/m².
    """
    blank: dict[str, float | str] = {
        "step": "",
        "scale": np.nan,
        "per_unit": np.nan,
        "per_unit_low": np.nan,
        "per_unit_high": np.nan,
        "pct_of_average": np.nan,
        "pct_low": np.nan,
        "pct_high": np.nan,
    }
    factor = _BY_KEY.get(key)
    scale = scales.get(key)
    if factor is None or scale is None:
        return blank
    per_unit = coefficient / scale
    per_unit_low = ci_low / scale
    per_unit_high = ci_high / scale
    if abs(intercept) < 1e-8:
        pct = pct_low = pct_high = np.nan
    else:
        pct = per_unit / intercept * 100
        pct_low = per_unit_low / intercept * 100
        pct_high = per_unit_high / intercept * 100
    return {
        "step": factor.step,
        "scale": scale,
        "per_unit": per_unit,
        "per_unit_low": per_unit_low,
        "per_unit_high": per_unit_high,
        "pct_of_average": pct,
        "pct_low": pct_low,
        "pct_high": pct_high,
    }


def fit_area_price(frame: pd.DataFrame) -> AreaPriceModel:
    """Correlate every factor, then fit the shorter list on complete areas.

    A fit factor blank on more than a quarter of the eligible areas is left out.
    The rest are listwise deleted. A column that repeats a pattern already in the
    design is left out too. Predictors are standardized on the fitting sample.
    """
    correlations = weighted_correlations(frame)
    eligible = eligible_areas(frame)
    notes: list[str] = []
    if eligible.empty:
        return _empty(
            0,
            correlations,
            (),
            (),
            f"No Planungsraum has at least {MIN_LISTINGS} qualifying listings.",
        )

    kept: list[Factor] = []
    for factor in FACTORS:
        if not factor.in_fit or factor.key not in eligible.columns:
            continue
        values = pd.to_numeric(eligible[factor.key], errors="coerce")
        blank = float(values.isna().mean())
        if blank > MISSING_LIMIT:
            notes.append(
                f"{factor.label} is blank on {blank:.0%} of areas with at least "
                f"{MIN_LISTINGS} listings, so it stays out of the fit."
            )
            continue
        kept.append(factor)

    if not kept:
        return _empty(
            len(eligible),
            correlations,
            tuple(notes),
            (),
            "Every factor is blank too often to include it in the fit.",
        )

    sample = eligible.dropna(subset=[factor.key for factor in kept]).copy()
    for factor in kept:
        sample[factor.key] = pd.to_numeric(sample[factor.key], errors="coerce")
    if sample.empty:
        return _empty(
            len(eligible),
            correlations,
            tuple(notes),
            (),
            "No area has all of the factors the fit uses.",
        )

    weights = sample["listings"].to_numpy(dtype=float)
    raw = {"intercept": np.ones(len(sample))}
    for factor in kept:
        raw[factor.key] = sample[factor.key].to_numpy(dtype=float)
    independent = _independent(raw)
    omitted = tuple(key for key in raw if key not in independent and key != "intercept")

    design: dict[str, np.ndarray] = {"intercept": raw["intercept"]}
    scales: dict[str, float] = {}
    for key in independent:
        if key == "intercept":
            continue
        mean = _weighted_mean(raw[key], weights)
        scale = _weighted_std(raw[key], weights)
        if scale < 1e-8:
            omitted = tuple([*omitted, key])
            continue
        scales[key] = scale
        design[key] = (raw[key] - mean) / scale

    if len(design) < 2 or len(sample) <= len(design) + 1:
        return _empty(
            len(eligible),
            correlations,
            tuple(notes),
            omitted,
            (
                f"{len(sample)} areas have every factor. "
                "That is too few to separate a €/m² effect for each one."
            ),
        )

    y = sample[PRICE].to_numpy(dtype=float)
    design_frame = pd.DataFrame(design, index=sample.index)
    if np.linalg.matrix_rank(design_frame.to_numpy(dtype=float)) < design_frame.shape[1]:
        return _empty(
            len(eligible),
            correlations,
            tuple(notes),
            omitted,
            "The factors overlap too much to separate their €/m² effects.",
        )

    fitted = sm.WLS(y, design_frame, weights=weights).fit()
    robust = False
    if float(fitted.ssr) > 1e-6:
        fitted = sm.WLS(y, design_frame, weights=weights).fit(cov_type="HC1")
        robust = True
    predicted = np.asarray(fitted.predict(design_frame), dtype=float)
    residual = y - predicted
    correlations = correlations.copy()
    correlations.loc[correlations["key"].isin(design_frame.columns), "in_model"] = True

    conf = fitted.conf_int()
    intercept = float(fitted.params["intercept"])
    records = []
    for key in design_frame.columns:
        factor = _BY_KEY.get(str(key))
        coefficient = float(fitted.params[key])
        ci_low = float(conf.loc[key, 0])
        ci_high = float(conf.loc[key, 1])
        per_step = _per_step(str(key), scales, coefficient, ci_low, ci_high, intercept)
        records.append(
            {
                "key": str(key),
                "label": "Intercept" if factor is None else factor.label,
                "group": "Baseline" if factor is None else factor.group,
                "coefficient": coefficient,
                "std_error": float(fitted.bse[key]),
                "ci_low": ci_low,
                "ci_high": ci_high,
                "p_value": float(fitted.pvalues[key]),
                **per_step,
            }
        )
    keep = [column for column in _AREA_COLUMNS if column in sample.columns]
    areas = sample.loc[:, keep].copy()
    for column in ("area_id", "name", "bezirk"):
        if column not in areas.columns and column in sample.columns:
            areas[column] = sample[column]
    areas["predicted"] = predicted
    areas["residual"] = residual
    areas = areas.reindex(columns=_AREA_COLUMNS).reset_index(drop=True)
    return AreaPriceModel(
        n_eligible=len(eligible),
        n_used=len(sample),
        correlations=correlations,
        notes=tuple(notes),
        omitted=omitted,
        robust=robust,
        rsquared=float(fitted.rsquared),
        rmse=float(np.sqrt(np.average(residual**2, weights=weights))),
        median_abs_error=float(np.median(np.abs(residual))),
        terms=pd.DataFrame(records, columns=_TERM_COLUMNS),
        areas=areas,
        error=None,
    )


def load_area_frame(every_area: bool = False) -> pd.DataFrame:
    """One row per priced Planungsraum, with the factors the page can fill in.

    With `every_area`, every Planungsraum gets a row. Those without a listing
    have a blank price and listing count.

    A missing reference table leaves its factors blank. The price, the listing
    count and the two medians still come back.
    """
    stats = load_area_listing_stats()
    areas = _call(lambda: load_areas("plr"), "Planungsräume", pd.DataFrame())
    known = not areas.empty and "area_id" in areas.columns
    if stats.empty and not (every_area and known):
        return pd.DataFrame(columns=_FRAME_COLUMNS)
    frame = stats.rename(columns={"value": PRICE}).copy()
    if frame.empty:
        frame = pd.DataFrame(columns=["area_id", PRICE, "listings", "bezirk"])
    frame["area_id"] = _ids(frame["area_id"])
    frame = frame.loc[frame["area_id"] != ""].drop_duplicates("area_id")
    if every_area and known:
        every = pd.DataFrame({"area_id": _ids(areas["area_id"])})
        every = every.loc[every["area_id"] != ""].drop_duplicates("area_id")
        frame = every.merge(frame, on="area_id", how="left")

    if known:
        areas = areas.copy()
        areas["area_id"] = _ids(areas["area_id"])
        keep = [
            column
            for column in ("area_id", "name", "bezirk", "bzr_id", "population")
            if column in areas.columns
        ]
        described = areas.loc[areas["area_id"] != "", keep].drop_duplicates("area_id")
        described = described.rename(columns={"bezirk": "area_bezirk"})
        frame = frame.merge(described, on="area_id", how="left")
        if "area_bezirk" in frame.columns:
            frame["bezirk"] = frame["area_bezirk"].fillna(frame.get("bezirk"))
            frame = frame.drop(columns=["area_bezirk"])
    if "bzr_id" not in frame.columns:
        frame["bzr_id"] = frame["area_id"].str.slice(0, 6)
    else:
        frame["bzr_id"] = frame["bzr_id"].fillna(frame["area_id"].str.slice(0, 6)).map(_text)

    for column, values in _factor_columns(areas).items():
        frame = _attach(frame, values, column)
    crime = _call(_crime_rates, "crime", pd.DataFrame(columns=["bzr_id", "value"]))
    frame = _attach_crime(frame, crime)

    for factor in FACTORS:
        if factor.key not in frame.columns:
            frame[factor.key] = np.nan
    return frame.reindex(columns=_FRAME_COLUMNS).reset_index(drop=True)


def _factor_columns(areas: pd.DataFrame) -> dict[str, pd.DataFrame]:
    columns: dict[str, pd.DataFrame] = {}
    indicators = _call(load_indicators, "area indicators", pd.DataFrame())
    for source, key in _INDICATORS.items():
        columns[key] = _indicator_values(indicators, source)
    age = _call(load_age, "age", pd.DataFrame())
    for source, key in _AGE.items():
        columns[key] = _indicator_values(age, source)

    election = _election_columns()
    columns.update(election)
    shares = election.get("_shares")
    if isinstance(shares, pd.DataFrame) and not shares.empty:
        columns["est_abitur"] = _call(
            lambda: _abitur_values(shares),
            "qualification estimates",
            pd.DataFrame(columns=["area_id", "value"]),
        )

    origins = _call(load_origin_stats, "origin stats", pd.DataFrame())
    columns["origin_diversity"] = _latest_origin(origins, "simpson")
    columns["oic_share"] = _latest_origin(origins, "oic")
    columns["incidents"] = _call(
        lambda: _incident_values(areas),
        "incidents",
        pd.DataFrame(columns=["area_id", "value"]),
    )
    coverage = load_coverage()
    columns["milieuschutz"] = (
        coverage.loc[:, ["area_id", "value"]]
        if not coverage.empty and {"area_id", "value"} <= set(coverage.columns)
        else pd.DataFrame(columns=["area_id", "value"])
    )
    centre = _read_sql(_CENTRE_SQL, {"lon": ALEXANDERPLATZ_LON, "lat": ALEXANDERPLATZ_LAT})
    station = _read_sql(_STATION_SQL, {})
    if centre is not None:
        columns["centre_km"] = centre
    if station is not None:
        columns["station_km"] = station
    return {key: value for key, value in columns.items() if key != "_shares"}


def _election_columns() -> dict[str, pd.DataFrame]:
    try:
        districts = load_election_districts()
        weights = load_plr_weights()
        shares = plr_party_shares(weights, party_shares_long(districts))
    except ProgrammingError as exc:
        if not _missing_relation(exc):
            raise
        log.info("Election results are not available")
        return {}
    agh = shares
    if "scope" in shares.columns:
        agh = shares.loc[shares["scope"] == "abgeordnetenhaus"]
    tolerance = (
        tolerance_scores(agh, "area_id")
        if not agh.empty and "party" in agh.columns
        else pd.DataFrame(columns=["area_id", "value"])
    )
    return {
        "agh_vote_afd": _party_values(agh, "afd"),
        "agh_vote_gruene": _party_values(agh, "gruene"),
        "agh_turnout": plr_turnout(weights, districts),
        "tolerance_score": tolerance,
        "_shares": shares,
    }


def _abitur_values(shares: pd.DataFrame) -> pd.DataFrame:
    estimates = load_planungsraum_education(shares)
    if estimates.empty or "level" not in estimates.columns:
        return pd.DataFrame(columns=["area_id", "value"])
    rows = estimates.loc[estimates["level"] == "abitur", ["area_id", "pct"]]
    return rows.rename(columns={"pct": "value"})


def _incident_values(areas: pd.DataFrame) -> pd.DataFrame:
    empty = pd.DataFrame(columns=["area_id", "value"])
    if areas.empty or "population" not in areas.columns:
        return empty
    span = incident_date_range()
    if span is None:
        return empty
    last = span[1]
    start = _years_before(last, int(INCIDENT_WINDOW_YEARS))
    incidents = load_incidents(start, last)
    if not incidents.empty:
        occurred = pd.to_datetime(incidents["incident_date"])
        incidents = incidents.loc[occurred > pd.Timestamp(start)]
    rates = incident_rates(incidents, areas, INCIDENT_WINDOW_YEARS)
    return rates.loc[:, ["area_id", "value"]]


def _crime_rates() -> pd.DataFrame:
    empty = pd.DataFrame(columns=["bzr_id", "value"])
    stats = load_crime()
    if stats.empty:
        return empty
    rows = stats[(stats["level"] == "bzr") & (stats["offense"] == "neighbourhood")].copy()
    rows["rate"] = pd.to_numeric(rows["rate"], errors="coerce")
    rows = rows.dropna(subset=["rate"])
    if rows.empty:
        return empty
    latest = rows.sort_values("year").groupby("area_id", sort=False).tail(1)
    return latest.rename(columns={"area_id": "bzr_id", "rate": "value"}).loc[:, ["bzr_id", "value"]]


def _latest_origin(stats: pd.DataFrame, key: str) -> pd.DataFrame:
    empty = pd.DataFrame(columns=["area_id", "value"])
    if stats.empty or "origin_key" not in stats.columns:
        return empty
    rows = stats.loc[stats["origin_key"] == key]
    if rows.empty:
        return empty
    return map_values(stats, key, max(rows["ref_date"]))


def _indicator_values(indicators: pd.DataFrame, name: str) -> pd.DataFrame:
    empty = pd.DataFrame(columns=["area_id", "value"])
    if indicators.empty or "indicator" not in indicators.columns:
        return empty
    rows = indicators
    if "level" in rows.columns:
        rows = rows.loc[rows["level"] == "plr"]
    rows = rows.loc[rows["indicator"] == name, ["area_id", "value"]]
    return rows.drop_duplicates("area_id")


def _party_values(shares: pd.DataFrame, party: str) -> pd.DataFrame:
    empty = pd.DataFrame(columns=["area_id", "value"])
    if shares.empty or "party" not in shares.columns:
        return empty
    return shares.loc[shares["party"] == party, ["area_id", "value"]]


def _attach(frame: pd.DataFrame, values: pd.DataFrame, column: str) -> pd.DataFrame:
    if values is None or values.empty or not {"area_id", "value"} <= set(values.columns):
        if column not in frame.columns:
            frame = frame.copy()
            frame[column] = np.nan
        return frame
    piece = values.loc[:, ["area_id", "value"]].copy()
    piece["area_id"] = _ids(piece["area_id"])
    piece["value"] = pd.to_numeric(piece["value"], errors="coerce")
    piece = piece.loc[piece["area_id"] != ""].drop_duplicates("area_id")
    piece = piece.rename(columns={"value": column})
    if column in frame.columns:
        frame = frame.drop(columns=[column])
    return frame.merge(piece, on="area_id", how="left")


def _attach_crime(frame: pd.DataFrame, rates: pd.DataFrame) -> pd.DataFrame:
    if rates is None or rates.empty or "bzr_id" not in frame.columns:
        frame = frame.copy()
        frame["crime_rate"] = np.nan
        return frame
    piece = rates.loc[:, ["bzr_id", "value"]].copy()
    piece["bzr_id"] = piece["bzr_id"].map(_text)
    piece["value"] = pd.to_numeric(piece["value"], errors="coerce")
    piece = piece.drop_duplicates("bzr_id").rename(columns={"value": "crime_rate"})
    frame = frame.drop(columns=["crime_rate"], errors="ignore")
    return frame.merge(piece, on="bzr_id", how="left")


def _years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def _read_sql(query: str, params: dict) -> pd.DataFrame | None:
    try:
        with get_engine().connect() as conn:
            return pd.read_sql(text(query), conn, params=params)
    except ProgrammingError as exc:
        if not _missing_relation(exc):
            raise
        log.info("A distance table is not available")
        return None


def _call(fn, label: str, fallback):
    try:
        return fn()
    except ProgrammingError as exc:
        if not _missing_relation(exc):
            raise
        log.info("%s is not available", label)
        return fallback


def _missing_relation(exc: ProgrammingError) -> bool:
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return sqlstate == "42P01"


def _ids(values: pd.Series) -> pd.Series:
    return values.map(lambda value: "" if pd.isna(value) else str(value).strip())


def _text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _pearson(x: np.ndarray, y: np.ndarray, weights: np.ndarray) -> float:
    sx = _weighted_std(x, weights)
    sy = _weighted_std(y, weights)
    if sx == 0 or sy == 0:
        return float("nan")
    x_mean = _weighted_mean(x, weights)
    y_mean = _weighted_mean(y, weights)
    cov = float(np.average((x - x_mean) * (y - y_mean), weights=weights))
    return cov / (sx * sy)


def _weighted_mean(values: np.ndarray, weights: np.ndarray) -> float:
    return float(np.average(values, weights=weights))


def _weighted_std(values: np.ndarray, weights: np.ndarray) -> float:
    mean = _weighted_mean(values, weights)
    return float(np.sqrt(np.average((values - mean) ** 2, weights=weights)))


def _independent(columns: dict[str, np.ndarray]) -> list[str]:
    """Column names that raise the rank of the design, in insertion order."""
    keep: list[str] = []
    current: np.ndarray | None = None
    for name, values in columns.items():
        candidate = values.reshape(-1, 1) if current is None else np.column_stack([current, values])
        rank = np.linalg.matrix_rank(candidate)
        width = 0 if current is None else current.shape[1]
        if rank > width:
            keep.append(name)
            current = candidate
    return keep


def _empty(
    n_eligible: int,
    correlations: pd.DataFrame,
    notes: tuple[str, ...],
    omitted: tuple[str, ...],
    error: str,
) -> AreaPriceModel:
    return AreaPriceModel(
        n_eligible=n_eligible,
        n_used=0,
        correlations=correlations,
        notes=notes,
        omitted=omitted,
        robust=False,
        rsquared=None,
        rmse=None,
        median_abs_error=None,
        terms=pd.DataFrame(columns=_TERM_COLUMNS),
        areas=pd.DataFrame(columns=_AREA_COLUMNS),
        error=error,
    )
