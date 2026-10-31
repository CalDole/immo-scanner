"""Multilevel model of asking price per m²: a Planungsraum reference price and a flat premium.

The outcome is log(asking price / living area). Flat features enter once, and
their Planungsraum means enter as well (the Mundlak terms). That way the flat
effects come only from differences inside a Planungsraum, as in a
fixed-effects model. Neither effect absorbs the other: the area effect does not
take on the building mix, and the flat effects do not take on location.

Each Planungsraum has a random intercept on top of its attributes. Its
reference price is the predicted €/m² of one standard flat (`ReferenceFlat`)
placed there. A flat's own component is what its features add to, or take off,
that reference.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats

from immo_scanner.explore.area_price import FACTORS
from immo_scanner.explore.price_model import (
    ENERGY_LEVELS,
    RENTAL_LEVELS,
    clean_energy_class,
    clean_rental,
    for_the_fit,
)

NUMERIC = ("log_living_area", "rooms", "bathrooms")
FLAGS = ("has_elevator", "has_balcony", "has_parking", "has_cellar")
AGE_BANDS = (
    "pre_1919",
    "1919_1948",
    "1949_1969",
    "1970_1989",
    "1990_2009",
    "2010_recent",
    "new_build",
    "unknown",
)
AGE_REFERENCE = "pre_1919"
ENERGY_REFERENCE = "D"
RENTAL_REFERENCE = "unrented"
AGE_LABELS = {
    "pre_1919": "Built before 1919",
    "1919_1948": "Built 1919–1948",
    "1949_1969": "Built 1949–1969",
    "1970_1989": "Built 1970–1989",
    "1990_2009": "Built 1990–2009",
    "2010_recent": "Built 2010 or later",
    "new_build": "New build",
    "unknown": "Year built not stated",
}
# Planungsraum attributes in the fit, as keys of area_price.FACTORS.
QUARTER_KEYS = (
    "unemployment",
    "low_wage",
    "agh_vote_gruene",
    "agh_vote_afd",
    "crime_rate",
    "incidents",
    "centre_km",
    "station_km",
    "milieuschutz",
    "school_type",
)
IMPORTANCE_GROUPS = {
    "Work": "Socio-economic",
    "Population": "Socio-economic",
    "Politics": "Politics",
    "Safety": "Safety",
    "Place": "Location and access",
    "Schools": "Schools",
}
BUILDING_STOCK = "Building stock"
UNEXPLAINED = "Unexplained location premium"
FLAT = "Flat"
_Z = 1.959963984540054
_NUMERIC_LABELS = {
    "log_living_area": "Living area (log)",
    "rooms": "Rooms",
    "bathrooms": "Bathrooms",
}
_FLAG_LABELS = {
    "has_elevator": "Elevator",
    "has_balcony": "Balcony",
    "has_parking": "Parking",
    "has_cellar": "Cellar",
}
_FACTORS = {factor.key: factor for factor in FACTORS}
_TERM_COLUMNS = [
    "key",
    "label",
    "group",
    "kind",
    "coefficient",
    "std_error",
    "ci_low",
    "ci_high",
    "p_value",
    "percent",
]


@dataclass(frozen=True)
class ReferenceFlat:
    """The standard flat whose €/m² is a Planungsraum's reference price."""

    living_area: float = 70.0
    rooms: float = 2.5
    bathrooms: float = 1.0
    age_band: str = "1949_1969"
    energy_class: str = "D"
    rental: str = "unrented"
    has_elevator: bool = False
    has_balcony: bool = False
    has_parking: bool = False
    has_cellar: bool = False


@dataclass(frozen=True)
class FlatSpec:
    """How the flat features are turned into model columns."""

    columns: tuple[str, ...]
    fills: dict[str, float]
    energy_reference: str
    rental_reference: str
    age_reference: str


@dataclass
class Prepared:
    n_input: int
    listings: pd.DataFrame
    design: pd.DataFrame
    spec: FlatSpec
    coverage: pd.DataFrame


@dataclass
class FixedEffects:
    """One intercept per Planungsraum. Flat effects come from differences inside areas."""

    terms: pd.DataFrame
    areas: pd.DataFrame
    naive: pd.DataFrame
    sigma2: float | None
    clustered: bool
    n_within: int
    error: str | None = None


@dataclass
class LocationModel:
    n_input: int
    n_used: int
    n_areas: int
    n_areas_3: int
    coverage: pd.DataFrame
    spec: FlatSpec
    reference: ReferenceFlat
    fixed: FixedEffects
    comparison: pd.DataFrame
    terms: pd.DataFrame
    params: pd.Series
    cov: pd.DataFrame
    sigma_e2: float | None
    sigma_u2: float | None
    icc: float | None
    explained_by_area_terms: float | None
    explained_by_attributes: float | None
    rmse: float | None
    converged: bool
    quarter_design: pd.DataFrame
    city_design: pd.Series
    premiums: pd.Series
    premium_variance: pd.Series
    area_info: pd.DataFrame
    quarters: pd.DataFrame
    listings: pd.DataFrame
    flat_design: pd.DataFrame
    notes: tuple[str, ...] = ()
    omitted: tuple[str, ...] = ()
    error: str | None = None
    quarter_keys: tuple[str, ...] = field(default_factory=tuple)


def age_band(year: object, current_year: int) -> str:
    """Building-age band for a year built. Blank and implausible years are unknown."""
    try:
        value = float(year)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "unknown"
    if np.isnan(value) or value < 1700:
        return "unknown"
    if value >= current_year - 1:
        return "new_build"
    if value < 1919:
        return "pre_1919"
    if value < 1949:
        return "1919_1948"
    if value < 1970:
        return "1949_1969"
    if value < 1990:
        return "1970_1989"
    if value < 2010:
        return "1990_2009"
    return "2010_recent"


def flat_label(column: str) -> str:
    if column in _NUMERIC_LABELS:
        return _NUMERIC_LABELS[column]
    if column in _FLAG_LABELS:
        return _FLAG_LABELS[column]
    prefix, _, rest = column.partition(":")
    if prefix == "missing":
        base = _NUMERIC_LABELS.get(rest) or _FLAG_LABELS.get(rest, rest)
        return f"{base.removesuffix(' (log)')} not stated"
    if prefix == "energy":
        return "Energy class not stated" if rest == "unknown" else f"Energy class {rest}"
    if prefix == "rental":
        return "Rental status not stated" if rest == "unknown" else rest.capitalize()
    if prefix == "age":
        return AGE_LABELS.get(rest, rest)
    return column


def feature_key(column: str) -> str:
    """The flat feature a column belongs to. A not-stated dummy belongs to its feature."""
    prefix, _, rest = column.partition(":")
    if not rest:
        return column
    if prefix == "missing":
        return rest
    return prefix


def _term_info(column: str) -> tuple[str, str, str]:
    """Label, group and kind of a model column."""
    if column == "intercept":
        return "Intercept", "Baseline", "intercept"
    if column.startswith("mean:"):
        base = column.split(":", 1)[1]
        noun = "mean" if base in NUMERIC else "share"
        return f"Area {noun}: {flat_label(base)}", BUILDING_STOCK, "building_stock"
    if column.startswith(("z:", "zmissing:")):
        key = column.split(":", 1)[1]
        factor = _FACTORS.get(key)
        label = factor.label if factor else key
        group = IMPORTANCE_GROUPS.get(factor.group, factor.group) if factor else "Other"
        if column.startswith("zmissing:"):
            label = f"{label} not stated"
        return label, group, "quarter"
    return flat_label(column), FLAT, "flat"


def _numeric(frame: pd.DataFrame, key: str) -> pd.Series:
    if key not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[key], errors="coerce").astype(float)


def _flag(series: pd.Series) -> pd.Series:
    values: list[float] = []
    for value in series:
        try:
            missing = bool(pd.isna(value))
        except (TypeError, ValueError):
            missing = False
        values.append(np.nan if missing else (1.0 if bool(value) else 0.0))
    return pd.Series(values, index=series.index, dtype=float)


def _text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def clean_listings(features: pd.DataFrame, current_year: int | None = None) -> pd.DataFrame:
    """One row per listing with the cleaned inputs. Nothing is dropped here."""
    year_now = current_year or date.today().year
    frame = pd.DataFrame(index=features.index)
    if "plr_id" in features.columns:
        frame["area_id"] = features["plr_id"].map(_text)
    else:
        frame["area_id"] = ""
    frame["price"] = _numeric(features, "price")
    frame["living_area"] = _numeric(features, "living_area")
    area = frame["living_area"].where(frame["living_area"] > 0)
    frame["log_living_area"] = np.log(area)
    frame["rooms"] = _numeric(features, "rooms").where(lambda s: s > 0)
    frame["bathrooms"] = _numeric(features, "bathrooms").where(lambda s: s >= 0)
    for key in FLAGS:
        if key in features.columns:
            frame[key] = _flag(features[key])
        else:
            frame[key] = np.nan
    year = year_now - _numeric(features, "building_age_years")
    year = year.fillna(_numeric(features, "year_built"))
    frame["year_built"] = year
    frame["age_band"] = year.map(lambda value: age_band(value, year_now))
    if "energy_efficiency_class" in features.columns:
        frame["energy_class"] = features["energy_efficiency_class"].map(clean_energy_class)
    else:
        frame["energy_class"] = "unknown"
    if "is_rented" in features.columns:
        frame["rental"] = features["is_rented"].map(clean_rental)
    else:
        frame["rental"] = "unknown"
    for column in ("id", "title", "url", "bezirk", "source"):
        frame[column] = features[column] if column in features.columns else np.nan
    return frame


def _encode(
    clean: pd.DataFrame, spec: FlatSpec, columns: list[str] | tuple[str, ...]
) -> pd.DataFrame:
    out: dict[str, pd.Series] = {}
    for key in (*NUMERIC, *FLAGS):
        values = pd.to_numeric(clean[key], errors="coerce").astype(float)
        out[key] = values.fillna(spec.fills.get(key, 0.0))
        out[f"missing:{key}"] = values.isna().astype(float)
    for prefix, column, levels in (
        ("age", "age_band", AGE_BANDS),
        ("energy", "energy_class", ENERGY_LEVELS),
        ("rental", "rental", RENTAL_LEVELS),
    ):
        for level in levels:
            out[f"{prefix}:{level}"] = (clean[column] == level).astype(float)
    frame = pd.DataFrame(out, index=clean.index)
    return frame.reindex(columns=list(columns), fill_value=0.0)


def _baseline(values: pd.Series, preferred: str, order: tuple[str, ...]) -> str:
    counts = values.value_counts()
    if preferred in counts.index:
        return preferred
    present = [level for level in order if level in counts.index]
    if not present:
        return preferred
    return max(present, key=lambda level: int(counts[level]))


def _independent(frame: pd.DataFrame, columns: list[str]) -> list[str]:
    """Columns that raise the rank of the design, in order."""
    keep: list[str] = []
    current: np.ndarray | None = None
    for column in columns:
        values = frame[column].to_numpy(dtype=float).reshape(-1, 1)
        candidate = values if current is None else np.column_stack([current, values])
        width = 0 if current is None else current.shape[1]
        if np.linalg.matrix_rank(candidate) > width:
            keep.append(column)
            current = candidate
    return keep


def _coverage(clean: pd.DataFrame) -> pd.DataFrame:
    rows = [("Living area", int(clean["log_living_area"].notna().sum()))]
    rows += [(_NUMERIC_LABELS[key], int(clean[key].notna().sum())) for key in NUMERIC[1:]]
    rows.append(("Year built", int((clean["age_band"] != "unknown").sum())))
    rows += [(_FLAG_LABELS[key], int(clean[key].notna().sum())) for key in FLAGS]
    rows.append(("Energy class", int((clean["energy_class"] != "unknown").sum())))
    rows.append(("Rental status", int((clean["rental"] != "unknown").sum())))
    total = len(clean)
    return pd.DataFrame(
        [
            {"feature": label, "stated": stated, "not_stated": total - stated}
            for label, stated in rows
        ]
    )


def prepare_listings(features: pd.DataFrame, current_year: int | None = None) -> Prepared:
    """Clean and encode the listings. Those without price, area or Planungsraum drop out.

    ThinkImmo and unfinished-shell titles are left out, as in the euro price model.
    A blank number gets the average of the stated values plus a not-stated dummy,
    and a blank flag gets a stated no plus a not-stated dummy. Year built, energy
    class and rental status keep a blank as their own level.
    """
    kept = for_the_fit(features)
    clean = clean_listings(kept, current_year)
    usable = (clean["price"] > 0) & clean["log_living_area"].notna() & (clean["area_id"] != "")
    clean = clean.loc[usable].copy()
    clean["y"] = np.log(clean["price"] / clean["living_area"])

    fills: dict[str, float] = {}
    base: list[str] = []
    missing: list[str] = []
    for key in (*NUMERIC, *FLAGS):
        stated = clean[key].notna()
        if not stated.any():
            continue
        base.append(key)
        if key in NUMERIC:
            fills[key] = float(clean.loc[stated, key].mean())
        if (~stated).any():
            missing.append(f"missing:{key}")
    references = {
        "age": _baseline(clean["age_band"], AGE_REFERENCE, AGE_BANDS),
        "energy": _baseline(clean["energy_class"], ENERGY_REFERENCE, ENERGY_LEVELS),
        "rental": _baseline(clean["rental"], RENTAL_REFERENCE, RENTAL_LEVELS),
    }
    categorical: list[str] = []
    for prefix, column, levels in (
        ("age", "age_band", AGE_BANDS),
        ("energy", "energy_class", ENERGY_LEVELS),
        ("rental", "rental", RENTAL_LEVELS),
    ):
        present = set(clean[column].unique())
        categorical += [
            f"{prefix}:{level}"
            for level in levels
            if level in present and level != references[prefix]
        ]
    spec = FlatSpec(
        columns=(),
        fills=fills,
        energy_reference=references["energy"],
        rental_reference=references["rental"],
        age_reference=references["age"],
    )
    candidates = [*base, *categorical, *missing]
    encoded = _encode(clean, spec, candidates)
    encoded.insert(0, "intercept", 1.0)
    columns = (
        [c for c in _independent(encoded, ["intercept", *candidates]) if c != "intercept"]
        if len(clean)
        else []
    )
    spec = FlatSpec(
        columns=tuple(columns),
        fills=fills,
        energy_reference=spec.energy_reference,
        rental_reference=spec.rental_reference,
        age_reference=spec.age_reference,
    )
    return Prepared(
        n_input=len(kept),
        listings=clean,
        design=_encode(clean, spec, spec.columns),
        spec=spec,
        coverage=_coverage(clean_listings(kept, current_year)),
    )


def _term_frame(names: list[str], params: np.ndarray, cov: np.ndarray) -> pd.DataFrame:
    se = np.sqrt(np.clip(np.diag(cov), 0.0, None))
    with np.errstate(divide="ignore", invalid="ignore"):
        zscores = np.where(se > 0, params / se, np.nan)
    pvalues = 2 * stats.norm.sf(np.abs(zscores))
    records = []
    for name, coefficient, error, p_value in zip(names, params, se, pvalues, strict=True):
        label, group, kind = _term_info(name)
        records.append(
            {
                "key": name,
                "label": label,
                "group": group,
                "kind": kind,
                "coefficient": float(coefficient),
                "std_error": float(error),
                "ci_low": float(coefficient - _Z * error),
                "ci_high": float(coefficient + _Z * error),
                "p_value": float(p_value),
                "percent": _percent(name, float(coefficient)),
            }
        )
    return pd.DataFrame(records, columns=_TERM_COLUMNS)


def _percent(column: str, coefficient: float) -> float:
    """Percentage change in €/m². For log living area, the change when the area doubles."""
    if column == "intercept":
        return float("nan")
    base = column.split(":", 1)[1] if column.startswith("mean:") else column
    if base == "log_living_area":
        return (2.0**coefficient - 1.0) * 100.0
    return (np.exp(coefficient) - 1.0) * 100.0


def fit_fixed_effects(prepared: Prepared) -> FixedEffects:
    """Flat effects from differences inside Planungsräume, plus each area's own intercept.

    This is OLS with one dummy per Planungsraum, computed by demeaning within areas.
    Standard errors are clustered by Planungsraum. A pooled OLS without area terms
    comes back alongside it, to show how much location distorts the flat effects.
    """
    listings, design = prepared.listings, prepared.design
    empty = FixedEffects(
        terms=pd.DataFrame(columns=_TERM_COLUMNS),
        areas=pd.DataFrame(columns=["area_id", "listings", "alpha", "alpha_se"]),
        naive=pd.DataFrame(columns=_TERM_COLUMNS),
        sigma2=None,
        clustered=False,
        n_within=0,
    )
    if listings.empty or design.shape[1] == 0:
        empty.error = "No listing has the inputs the model needs."
        return empty

    empty.naive = _naive(listings["y"], design)
    groups = listings["area_id"]
    counts = groups.map(groups.value_counts())
    within = counts >= 2
    y_dm = listings["y"] - listings["y"].groupby(groups).transform("mean")
    x_dm = design - design.groupby(groups).transform("mean")
    y_w, x_w, g_w = y_dm[within], x_dm[within], groups[within]
    varying = [c for c in design.columns if float(x_w[c].abs().max() if len(x_w) else 0.0) > 1e-10]
    keep = _independent(x_w, varying) if varying else []
    n_groups = int(g_w.nunique())
    dof = len(y_w) - n_groups - len(keep)
    empty.n_within = int(len(y_w))
    if not keep or dof <= 0:
        empty.error = (
            "Too few Planungsräume have two or more listings to compare flats inside an area."
        )
        return empty

    x = x_w[keep].to_numpy(dtype=float)
    y = y_w.to_numpy(dtype=float)
    fitted = sm.OLS(y, x).fit()
    ssr = float(fitted.ssr)
    sigma2 = ssr / dof
    clustered = n_groups >= 2 and ssr > 1e-12
    if clustered:
        codes = pd.factorize(g_w)[0]
        cov = np.asarray(
            sm.OLS(y, x).fit(cov_type="cluster", cov_kwds={"groups": codes}).cov_params()
        )
    else:
        cov = np.asarray(fitted.cov_params()) * (len(y) - len(keep)) / dof
    beta = np.asarray(fitted.params, dtype=float)

    means = design[keep].groupby(groups).mean()
    y_means = listings["y"].groupby(groups).mean()
    sizes = groups.value_counts()
    alpha = y_means - means.to_numpy(dtype=float) @ beta
    quad = np.einsum("ij,jk,ik->i", means.to_numpy(dtype=float), cov, means.to_numpy(dtype=float))
    alpha_se = np.sqrt(sigma2 / sizes.reindex(means.index).to_numpy(dtype=float) + quad)
    areas = pd.DataFrame(
        {
            "area_id": means.index.astype(str),
            "listings": sizes.reindex(means.index).to_numpy(dtype=int),
            "alpha": alpha.to_numpy(dtype=float),
            "alpha_se": alpha_se,
        }
    ).reset_index(drop=True)
    return FixedEffects(
        terms=_term_frame(keep, beta, cov),
        areas=areas,
        naive=empty.naive,
        sigma2=sigma2,
        clustered=clustered,
        n_within=int(len(y_w)),
    )


def _naive(y: pd.Series, design: pd.DataFrame) -> pd.DataFrame:
    frame = design.copy()
    frame.insert(0, "intercept", 1.0)
    keep = _independent(frame, list(frame.columns))
    if len(y) <= len(keep) + 1:
        return pd.DataFrame(columns=_TERM_COLUMNS)
    x = frame[keep].to_numpy(dtype=float)
    fitted = sm.OLS(y.to_numpy(dtype=float), x).fit()
    if float(fitted.ssr) > 1e-12:
        fitted = sm.OLS(y.to_numpy(dtype=float), x).fit(cov_type="HC1")
    return _term_frame(keep, np.asarray(fitted.params), np.asarray(fitted.cov_params()))


def _quarter_attributes(
    areas: pd.DataFrame, all_ids: pd.Index, sample_ids: pd.Index, keys: tuple[str, ...]
) -> tuple[pd.DataFrame, list[str], list[str]]:
    """Standardised attributes per area (z:key) and their not-stated dummies (zmissing:key).

    Means and standard deviations come from the Planungsräume that have listings
    in the fit. A blank becomes the mean (zero) plus a not-stated dummy.
    """
    out = pd.DataFrame(index=all_ids)
    used: list[str] = []
    notes: list[str] = []
    if areas is None or areas.empty or "area_id" not in areas.columns:
        note = (
            "No Planungsraum attributes are available, so only the building stock "
            "and the random intercept describe areas."
        )
        return out, used, [note]
    table = areas.copy()
    table["area_id"] = table["area_id"].map(_text)
    table = table.loc[table["area_id"] != ""].drop_duplicates("area_id").set_index("area_id")
    for key in keys:
        label = _FACTORS[key].label if key in _FACTORS else key
        if key not in table.columns:
            notes.append(f"{label} is not available, so it stays out of the fit.")
            continue
        raw = pd.to_numeric(table[key], errors="coerce").reindex(all_ids)
        stated = raw.reindex(sample_ids).dropna()
        if len(stated) < 3 or float(stated.std(ddof=0)) < 1e-12:
            notes.append(f"{label} is blank or constant in the sampled areas, so it stays out.")
            continue
        mean, scale = float(stated.mean()), float(stated.std(ddof=0))
        out[f"z:{key}"] = ((raw - mean) / scale).fillna(0.0)
        if raw.reindex(sample_ids).isna().any():
            out[f"zmissing:{key}"] = raw.isna().astype(float)
        used.append(key)
    return out, used, notes


def _variance_components(
    y: np.ndarray, exog: pd.DataFrame, groups: np.ndarray
) -> tuple[float, float] | None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = sm.MixedLM(y, exog, groups=groups).fit(reml=True)
    except (np.linalg.LinAlgError, ValueError):
        return None
    return float(np.asarray(result.cov_re)[0, 0]), float(result.scale)


def _area_info(areas: pd.DataFrame | None, listings: pd.DataFrame) -> pd.DataFrame:
    frames = []
    if areas is not None and not areas.empty and "area_id" in areas.columns:
        info = areas.loc[:, [c for c in ("area_id", "name", "bezirk") if c in areas.columns]].copy()
        info["area_id"] = info["area_id"].map(_text)
        frames.append(info)
    from_listings = (
        listings.groupby("area_id", sort=False)["bezirk"].first().reset_index()
        if not listings.empty
        else pd.DataFrame(columns=["area_id", "bezirk"])
    )
    frames.append(from_listings)
    info = pd.concat(frames, ignore_index=True)
    info = info.loc[info["area_id"] != ""].drop_duplicates("area_id")
    for column in ("name", "bezirk"):
        if column not in info.columns:
            info[column] = np.nan
    info["name"] = info["name"].fillna(info["area_id"])
    return info.set_index("area_id")[["name", "bezirk"]]


def fit_location_model(
    features: pd.DataFrame,
    areas: pd.DataFrame | None = None,
    reference: ReferenceFlat | None = None,
    quarter_keys: tuple[str, ...] = QUARTER_KEYS,
    current_year: int | None = None,
) -> LocationModel:
    """Fit the fixed-effects benchmark and the Mundlak multilevel model.

    `areas` has one row per Planungsraum (area_id plus the attribute columns of
    `area_price.load_area_frame(every_area=True)`). Areas there without listings
    still get a reference price, from their attributes alone.
    """
    reference = reference or ReferenceFlat()
    prepared = prepare_listings(features, current_year)
    fixed = fit_fixed_effects(prepared)
    listings, design, spec = prepared.listings, prepared.design, prepared.spec
    area_info = _area_info(areas, listings)
    groups = listings["area_id"]
    sizes = groups.value_counts()
    model = LocationModel(
        n_input=prepared.n_input,
        n_used=len(listings),
        n_areas=int(len(sizes)),
        n_areas_3=int((sizes >= 3).sum()),
        coverage=prepared.coverage,
        spec=spec,
        reference=reference,
        fixed=fixed,
        comparison=pd.DataFrame(),
        terms=pd.DataFrame(columns=_TERM_COLUMNS),
        params=pd.Series(dtype=float),
        cov=pd.DataFrame(),
        sigma_e2=None,
        sigma_u2=None,
        icc=None,
        explained_by_area_terms=None,
        explained_by_attributes=None,
        rmse=None,
        converged=False,
        quarter_design=pd.DataFrame(),
        city_design=pd.Series(dtype=float),
        premiums=pd.Series(dtype=float),
        premium_variance=pd.Series(dtype=float),
        area_info=area_info,
        quarters=pd.DataFrame(),
        listings=listings,
        flat_design=design,
        quarter_keys=(),
    )
    if listings.empty or design.shape[1] == 0:
        model.error = "No listing has a price, a living area and a Planungsraum."
        return model
    if len(sizes) < 3:
        model.error = "The listings fall in fewer than three Planungsräume."
        return model

    sample_ids = pd.Index(sizes.index.astype(str))
    all_ids = sample_ids.union(pd.Index(area_info.index.astype(str)))
    zframe, used_keys, notes = _quarter_attributes(
        areas if areas is not None else pd.DataFrame(), all_ids, sample_ids, quarter_keys
    )
    means = design.groupby(groups).mean()
    city_means = design.mean()
    mundlak = means.reindex(all_ids)
    mundlak = mundlak.fillna(city_means)
    mundlak.columns = [f"mean:{c}" for c in mundlak.columns]
    quarter = pd.concat([mundlak, zframe], axis=1)

    exog_all = pd.concat(
        [
            pd.DataFrame({"intercept": 1.0}, index=listings.index),
            design,
            quarter.reindex(groups.to_numpy()).set_index(listings.index),
        ],
        axis=1,
    )
    columns = _independent(exog_all, list(exog_all.columns))
    omitted = tuple(c for c in exog_all.columns if c not in columns)
    exog = exog_all[columns]
    y = listings["y"].to_numpy(dtype=float)
    codes = groups.to_numpy()
    if len(y) <= exog.shape[1] + 2:
        model.error = f"{len(y)} listings are too few to separate {exog.shape[1]} effects."
        return model

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = sm.MixedLM(y, exog, groups=codes).fit(reml=True)
    except (np.linalg.LinAlgError, ValueError) as exc:
        model.error = f"The multilevel fit failed: {exc}"
        return model

    k = exog.shape[1]
    params = pd.Series(np.asarray(result.fe_params, dtype=float), index=columns)
    cov = pd.DataFrame(np.asarray(result.cov_params())[:k, :k], index=columns, columns=columns)
    sigma_e2 = float(result.scale)
    sigma_u2 = float(np.asarray(result.cov_re)[0, 0])
    premiums = pd.Series(
        {str(g): float(np.asarray(v)[0]) for g, v in result.random_effects.items()}, dtype=float
    )
    premium_variance = pd.Series(
        {str(g): float(np.asarray(v)[0, 0]) for g, v in result.random_effects_cov.items()},
        dtype=float,
    )
    converged = bool(getattr(result, "converged", True))
    if not converged:
        notes.append("The multilevel fit did not fully converge. Treat the estimates with care.")

    empty_fit = _variance_components(y, exog[["intercept"]], codes)
    no_z = [c for c in columns if not c.startswith(("z:", "zmissing:"))]
    no_z_fit = _variance_components(y, exog[no_z], codes) if len(no_z) < len(columns) else None
    icc = explained_area = explained_z = None
    if empty_fit is not None:
        total = empty_fit[0] + empty_fit[1]
        icc = empty_fit[0] / total if total > 0 else None
        if empty_fit[0] > 0:
            explained_area = 1.0 - sigma_u2 / empty_fit[0]
    if no_z_fit is not None and no_z_fit[0] > 0:
        explained_z = 1.0 - sigma_u2 / no_z_fit[0]

    predicted_log = (
        exog.to_numpy(dtype=float) @ params.to_numpy() + groups.map(premiums).fillna(0.0).to_numpy()
    )
    smear = float(np.exp(sigma_e2 / 2.0))
    listings = listings.copy()
    listings["price_per_m2"] = listings["price"] / listings["living_area"]
    listings["predicted_log"] = predicted_log
    listings["predicted_per_m2"] = np.exp(predicted_log) * smear
    listings["predicted_price"] = listings["predicted_per_m2"] * listings["living_area"]
    listings["gap"] = listings["price"] - listings["predicted_price"]

    quarter_used = quarter[[c for c in columns if c in quarter.columns]]
    city = pd.Series(0.0, index=quarter_used.columns)
    for column in quarter_used.columns:
        if column.startswith("mean:"):
            city[column] = float(city_means[column.split(":", 1)[1]])

    model.terms = _term_frame(columns, params.to_numpy(), cov.to_numpy())
    model.params = params
    model.cov = cov
    model.sigma_e2 = sigma_e2
    model.sigma_u2 = sigma_u2
    model.icc = icc
    model.explained_by_area_terms = explained_area
    model.explained_by_attributes = explained_z
    model.rmse = float(np.sqrt(np.mean((y - predicted_log) ** 2)))
    model.converged = converged
    model.quarter_design = quarter_used
    model.city_design = city
    model.premiums = premiums
    model.premium_variance = premium_variance
    model.listings = listings
    model.notes = tuple(notes)
    model.omitted = omitted
    model.quarter_keys = tuple(used_keys)
    model.comparison = compare_flat_effects(model)
    model.quarters = quarter_reference(model)
    return model


def compare_flat_effects(model: LocationModel) -> pd.DataFrame:
    """Pooled, fixed-effects and Mundlak flat effects side by side.

    `flagged` marks a Mundlak effect that differs from the fixed-effects one by
    more than their combined standard error, a sign of confounding left over.
    """
    naive = model.fixed.naive.set_index("key") if not model.fixed.naive.empty else pd.DataFrame()
    fe = model.fixed.terms.set_index("key") if not model.fixed.terms.empty else pd.DataFrame()
    mixed = model.terms.set_index("key") if not model.terms.empty else pd.DataFrame()
    rows = []
    for column in model.spec.columns:
        row: dict[str, object] = {
            "key": column,
            "label": flat_label(column),
            "feature": feature_key(column),
        }
        for prefix, table in (("naive", naive), ("fixed", fe), ("mundlak", mixed)):
            if column in getattr(table, "index", []):
                row[f"{prefix}_coefficient"] = float(table.loc[column, "coefficient"])
                row[f"{prefix}_se"] = float(table.loc[column, "std_error"])
                row[f"{prefix}_percent"] = float(table.loc[column, "percent"])
            else:
                row[f"{prefix}_coefficient"] = np.nan
                row[f"{prefix}_se"] = np.nan
                row[f"{prefix}_percent"] = np.nan
        gap = row["mundlak_coefficient"] - row["fixed_coefficient"]  # type: ignore[operator]
        combined = float(np.hypot(row["mundlak_se"], row["fixed_se"]))  # type: ignore[arg-type]
        row["gap"] = gap
        row["flagged"] = bool(np.isfinite(gap) and np.isfinite(combined) and abs(gap) > combined)
        rows.append(row)
    return pd.DataFrame(rows)


def reference_design(model: LocationModel, reference: ReferenceFlat | None = None) -> pd.Series:
    """The reference flat encoded as model columns."""
    flat = reference or model.reference
    row = pd.DataFrame(
        {
            "log_living_area": [np.log(flat.living_area)],
            "rooms": [flat.rooms],
            "bathrooms": [flat.bathrooms],
            **{key: [1.0 if getattr(flat, key) else 0.0] for key in FLAGS},
            "age_band": [flat.age_band],
            "energy_class": [flat.energy_class],
            "rental": [flat.rental],
        }
    )
    return _encode(row, model.spec, model.spec.columns).iloc[0]


def _quarter_levels(
    model: LocationModel, reference: ReferenceFlat | None = None
) -> tuple[pd.Series, pd.Series]:
    """Log reference €/m² per area and its standard error."""
    params = model.params
    x_ref = reference_design(model, reference)
    flat_part = float(
        params.get("intercept", 0.0)
        + sum(float(params[c]) * float(x_ref[c]) for c in x_ref.index if c in params.index)
    )
    q = model.quarter_design
    fixed = flat_part + q.to_numpy(dtype=float) @ params.reindex(q.columns).to_numpy(dtype=float)
    premiums = model.premiums.reindex(q.index).fillna(0.0)
    level = pd.Series(fixed, index=q.index) + premiums

    names = list(params.index)
    contrast = pd.DataFrame(0.0, index=q.index, columns=names)
    if "intercept" in contrast.columns:
        contrast["intercept"] = 1.0
    for column in x_ref.index:
        if column in contrast.columns:
            contrast[column] = float(x_ref[column])
    for column in q.columns:
        contrast[column] = q[column]
    c = contrast.to_numpy(dtype=float)
    fixed_var = np.einsum("ij,jk,ik->i", c, model.cov.to_numpy(dtype=float), c)
    sigma_u2 = model.sigma_u2 or 0.0
    random_var = model.premium_variance.reindex(q.index).fillna(sigma_u2).to_numpy(dtype=float)
    se = pd.Series(np.sqrt(np.clip(fixed_var + random_var, 0.0, None)), index=q.index)
    return level, se


def quarter_reference(model: LocationModel, reference: ReferenceFlat | None = None) -> pd.DataFrame:
    """Reference €/m² of the standard flat in every Planungsraum.

    An area without listings has no premium of its own (`from_attributes_only`),
    so its reference rests on its attributes and the city-wide building stock.
    The fixed-effects reference is shown alongside for areas with listings.
    """
    if model.params.empty:
        return pd.DataFrame()
    level, se = _quarter_levels(model, reference)
    smear = float(np.exp((model.sigma_e2 or 0.0) / 2.0))
    counts = model.listings["area_id"].value_counts()
    asking = (
        model.listings.groupby("area_id")["price_per_m2"].mean()
        if "price_per_m2" in model.listings
        else pd.Series(dtype=float)
    )
    table = pd.DataFrame(index=level.index)
    table.index.name = "area_id"
    table = table.join(model.area_info, how="left")
    table["listings"] = counts.reindex(level.index).fillna(0).astype(int)
    table["from_attributes_only"] = table["listings"] == 0
    table["log_level"] = level
    table["reference_per_m2"] = np.exp(level) * smear
    table["low"] = np.exp(level - _Z * se) * smear
    table["high"] = np.exp(level + _Z * se) * smear
    table["premium"] = model.premiums.reindex(level.index).fillna(0.0)
    table["asking_per_m2"] = asking.reindex(level.index)
    table["fe_reference_per_m2"] = _fe_reference(model, reference).reindex(level.index)
    return table.reset_index()


def _fe_reference(model: LocationModel, reference: ReferenceFlat | None) -> pd.Series:
    fixed = model.fixed
    if fixed.terms.empty or fixed.areas.empty:
        return pd.Series(dtype=float)
    x_ref = reference_design(model, reference)
    beta = fixed.terms.set_index("key")["coefficient"]
    shift = float(sum(float(beta[c]) * float(x_ref.get(c, 0.0)) for c in beta.index))
    smear = float(np.exp((fixed.sigma2 or 0.0) / 2.0))
    areas = fixed.areas.set_index("area_id")
    return np.exp(areas["alpha"] + shift) * smear


def city_reference(model: LocationModel, reference: ReferenceFlat | None = None) -> float:
    """€/m² of the standard flat in an average Planungsraum with no premium of its own."""
    params = model.params
    x_ref = reference_design(model, reference)
    flat_part = float(
        params.get("intercept", 0.0)
        + sum(float(params[c]) * float(x_ref[c]) for c in x_ref.index if c in params.index)
    )
    city = model.city_design
    level = flat_part + float((city * params.reindex(city.index)).sum())
    return float(np.exp(level) * np.exp((model.sigma_e2 or 0.0) / 2.0))


def _quarter_terms(model: LocationModel) -> pd.DataFrame:
    """Log-point contribution of each quarter term, relative to the city baseline, per area."""
    q = model.quarter_design
    params = model.params.reindex(q.columns)
    contributions = (q - model.city_design.reindex(q.columns)) * params
    contributions["premium"] = model.premiums.reindex(q.index).fillna(0.0)
    return contributions


def quarter_contributions(model: LocationModel, area_id: str, detail: bool = True) -> pd.DataFrame:
    """Why one Planungsraum's reference differs from the city reference.

    Each attribute contributes its coefficient times its standardised value. Each
    building-stock term contributes its coefficient times the gap between the
    area's listing mix and the city's. The premium is what no term explains.
    Log points add up. Percentages are each term on its own. Without `detail`,
    the building-stock terms are summed into one row.
    """
    columns = ["label", "group", "log_points", "percent", "per_m2"]
    if model.params.empty or area_id not in model.quarter_design.index:
        return pd.DataFrame(columns=columns)
    terms = _quarter_terms(model).loc[area_id]
    if not detail:
        stock = [c for c in terms.index if str(c).startswith("mean:")]
        if stock:
            terms = pd.concat(
                [terms.drop(index=stock), pd.Series({"building_stock": terms[stock].sum()})]
            )
    city = city_reference(model)
    rows: list[dict[str, object]] = [
        {
            "label": "City reference",
            "group": "Baseline",
            "log_points": np.nan,
            "percent": np.nan,
            "per_m2": city,
        }
    ]
    body = []
    for column, value in terms.items():
        if column == "premium":
            label, group = UNEXPLAINED, UNEXPLAINED
        elif column == "building_stock":
            label, group = "Building stock in the listings", BUILDING_STOCK
        else:
            label, group, _ = _term_info(str(column))
        body.append(
            {
                "label": label,
                "group": group,
                "log_points": float(value),
                "percent": (np.exp(float(value)) - 1.0) * 100.0,
                "per_m2": np.nan,
            }
        )
    body.sort(key=lambda row: -abs(float(row["log_points"])))  # type: ignore[arg-type]
    rows += body
    total = float(terms.sum())
    rows.append(
        {
            "label": "This Planungsraum",
            "group": "Total",
            "log_points": total,
            "percent": (np.exp(total) - 1.0) * 100.0,
            "per_m2": city * float(np.exp(total)),
        }
    )
    return pd.DataFrame(rows, columns=columns)


def _importance_group(column: str) -> str:
    if column == "premium":
        return UNEXPLAINED
    return _term_info(column)[1]


def attribute_importance(model: LocationModel) -> pd.DataFrame:
    """How much each group of terms moves the reference price across sampled areas.

    `share` is the group's covariance with the total quarter level divided by the
    variance of that total. The shares add up to one. A negative share means the
    group offsets the others. `sd_log_points` is the spread of the group's own
    contribution.
    """
    columns = ["group", "share", "sd_log_points", "sd_percent"]
    if model.params.empty:
        return pd.DataFrame(columns=columns)
    terms = _quarter_terms(model)
    sampled = terms.loc[terms.index.isin(model.listings["area_id"].unique())]
    if len(sampled) < 2:
        return pd.DataFrame(columns=columns)
    grouped = sampled.T.groupby(lambda column: _importance_group(str(column))).sum().T
    total = grouped.sum(axis=1)
    variance = float(total.var(ddof=0))
    rows = []
    for group in grouped.columns:
        values = grouped[group]
        share = float(np.cov(values, total, ddof=0)[0, 1] / variance) if variance > 0 else np.nan
        sd = float(values.std(ddof=0))
        rows.append(
            {
                "group": group,
                "share": share,
                "sd_log_points": sd,
                "sd_percent": (np.exp(sd) - 1.0) * 100.0,
            }
        )
    table = pd.DataFrame(rows, columns=columns)
    return table.sort_values("share", ascending=False, kind="mergesort").reset_index(drop=True)


def _input_text(row: pd.Series, feature: str) -> str:
    if feature == "log_living_area":
        return f"{float(row['living_area']):,.0f} m²"
    if feature in ("rooms", "bathrooms"):
        value = row[feature]
        if pd.isna(value):
            return "not stated"
        return f"{float(value):g}"
    if feature in FLAGS:
        value = row[feature]
        if pd.isna(value):
            return "not stated"
        return "Yes" if float(value) >= 0.5 else "No"
    if feature == "age":
        year = row.get("year_built")
        band = AGE_LABELS.get(str(row["age_band"]), str(row["age_band"]))
        return band if pd.isna(year) else f"{band} ({int(year)})"
    if feature == "energy":
        return str(row["energy_class"])
    if feature == "rental":
        return str(row["rental"])
    return ""


def _reference_text(reference: ReferenceFlat, feature: str) -> str:
    if feature == "log_living_area":
        return f"{reference.living_area:,.0f} m²"
    if feature in ("rooms", "bathrooms"):
        return f"{getattr(reference, feature):g}"
    if feature in FLAGS:
        return "Yes" if getattr(reference, feature) else "No"
    if feature == "age":
        return AGE_LABELS.get(reference.age_band, reference.age_band)
    if feature == "energy":
        return reference.energy_class
    if feature == "rental":
        return reference.rental
    return ""


_FEATURE_LABELS = {
    "log_living_area": "Living area",
    "rooms": "Rooms",
    "bathrooms": "Bathrooms",
    **_FLAG_LABELS,
    "age": "Building age",
    "energy": "Energy class",
    "rental": "Rental status",
}


def flat_contributions(model: LocationModel, listing_index: object) -> pd.DataFrame:
    """What this flat's features add to the reference price of its Planungsraum.

    The first row is the area's reference €/m² times this flat's living area.
    Each feature then contributes its coefficient times the gap between this
    flat and the reference flat. Log points add up. The euros split the gap
    between reference and prediction in proportion to the log points, so they
    add up to the predicted price.
    """
    columns = ["term", "input", "reference", "log_points", "percent", "euros"]
    if model.params.empty or model.quarters.empty:
        return pd.DataFrame(columns=columns)
    row = model.listings.loc[listing_index]
    x_i = model.flat_design.loc[listing_index]
    x_ref = reference_design(model)
    params = model.params.reindex(x_i.index).fillna(0.0)
    points = (x_i - x_ref) * params
    by_feature = points.groupby(lambda column: feature_key(str(column)), sort=False).sum()
    quarters = model.quarters.set_index("area_id")
    base = float(quarters.loc[row["area_id"], "reference_per_m2"]) * float(row["living_area"])
    total = float(by_feature.sum())
    scale = 1.0 if abs(total) < 1e-12 else float(np.expm1(total) / total)
    records: list[dict[str, object]] = [
        {
            "term": "Planungsraum reference × living area",
            "input": f"{float(quarters.loc[row['area_id'], 'reference_per_m2']):,.0f} €/m²",
            "reference": "",
            "log_points": np.nan,
            "percent": np.nan,
            "euros": base,
        }
    ]
    for feature, value in by_feature.items():
        records.append(
            {
                "term": _FEATURE_LABELS.get(str(feature), str(feature)),
                "input": _input_text(row, str(feature)),
                "reference": _reference_text(model.reference, str(feature)),
                "log_points": float(value),
                "percent": float(np.expm1(float(value)) * 100.0),
                "euros": base * float(value) * scale,
            }
        )
    records.append(
        {
            "term": "Predicted price",
            "input": "",
            "reference": "",
            "log_points": total,
            "percent": float(np.expm1(total) * 100.0),
            "euros": base * float(np.exp(total)),
        }
    )
    return pd.DataFrame(records, columns=columns)
