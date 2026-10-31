"""Straight-line model of asking price.

Each coefficient is the euro difference associated with that feature while the
others stay fixed. Energy class D is the baseline when it appears in the sample.
An unknown certificate stays in the fit as its own class. Standard errors are
heteroscedasticity-robust (HC1) when the sample is large enough for that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NamedTuple

import numpy as np
import pandas as pd
import statsmodels.api as sm

from immo_scanner.explore.data import titles_are_shell
from immo_scanner.explore.prices import EXCLUDED_SOURCE, local_price_levels

_ENERGY_KEY = re.compile(r"[^A-H+]")
ENERGY_LEVELS = ("A+", "A", "B", "C", "D", "E", "F", "G", "H", "unknown")
ENERGY_REFERENCE = "D"
RENTAL_LEVELS = ("unrented", "rented", "unknown")
RENTAL_REFERENCE = "unrented"


class Feature(NamedTuple):
    key: str
    label: str
    group: str
    phrase: str
    missing_label: str


FEATURES: tuple[Feature, ...] = (
    Feature("living_area", "Living area", "Size", "per m² of living area", "Living area"),
    Feature("rooms", "Rooms", "Size", "per room", "Rooms"),
    Feature("bathrooms", "Bathrooms", "Size", "per bathroom", "Bathrooms"),
    Feature(
        "building_age_years",
        "Building age",
        "Building",
        "per year of building age",
        "Building age",
    ),
    Feature(
        "area_price_euros",
        "Planungsraum average",
        "Neighbourhood",
        "per euro of this living area at the Planungsraum's average €/m²",
        "Planungsraum average",
    ),
    Feature("has_elevator", "Elevator", "Amenities", "if the flat has an elevator", "Elevator"),
    Feature("has_balcony", "Balcony", "Amenities", "if the flat has a balcony", "Balcony"),
    Feature("has_parking", "Parking", "Amenities", "if the flat has parking", "Parking"),
    Feature("has_cellar", "Cellar", "Amenities", "if the flat has a cellar", "Cellar"),
)

# A feature missing on fewer listings than this drops those listings. Missing at
# least this often, the listings stay in and get their own euro adjustment.
_KEEP_IF_MISSING = 20
_FLAGS = frozenset({"has_elevator", "has_balcony", "has_parking", "has_cellar"})
_GROUP_ORDER = ("Size", "Building", "Energy class", "Tenancy", "Neighbourhood", "Amenities")
_BY_KEY = {feature.key: feature for feature in FEATURES}
SOURCE_LABELS = {
    "neubaukompass": "Neubaukompass",
    "immoscout24": "ImmoScout24",
    "immowelt": "Immowelt",
    "kleinanzeigen": "Kleinanzeigen",
    "bsk": "BSK Immobilien",
    "engelvoelkers": "Engel & Völkers",
}
# Price, the model features, then the energy certificate. Blank means the source did not state it.
GAP_FIELDS: tuple[tuple[str, str], ...] = (
    ("price", "Price"),
    *[(feature.key, feature.missing_label) for feature in FEATURES],
    ("energy_efficiency_class", "Energy class"),
)


@dataclass
class PriceModel:
    """Fitted asking-price model, plus the listings it was fitted on."""

    n_input: int
    n_used: int
    missing: pd.DataFrame
    coverage: pd.DataFrame
    by_source: pd.DataFrame
    notes: tuple[str, ...]
    fills: dict[str, float]
    reference: str
    rental_reference: str
    omitted: tuple[str, ...]
    robust: bool
    rsquared: float | None
    rmse: float | None
    median_abs_error: float | None
    terms: pd.DataFrame
    listings: pd.DataFrame
    error: str | None


def clean_energy_class(value: object) -> str:
    """Map a certificate label onto A+–H. Blank and unreadable labels are unknown."""
    if value is None:
        return "unknown"
    try:
        if pd.isna(value):
            return "unknown"
    except TypeError:
        pass
    key = _ENERGY_KEY.sub("", str(value).upper())
    if not key:
        return "unknown"
    if key in ENERGY_LEVELS:
        return key
    if key.startswith("A+"):
        return "A+"
    letter = key[0]
    if letter in "ABCDEFGH":
        return letter
    return "unknown"


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _flag(series: pd.Series) -> pd.Series:
    values: list[float] = []
    for value in series:
        try:
            missing = pd.isna(value)
        except TypeError:
            missing = False
        if missing:
            values.append(np.nan)
        else:
            values.append(1.0 if bool(value) else 0.0)
    return pd.Series(values, index=series.index, dtype=float)


def _column(frame: pd.DataFrame, key: str) -> pd.Series:
    if key not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    if key in _FLAGS:
        return _flag(frame[key])
    return _numeric(frame[key])


def clean_rental(value: object) -> str:
    """Map a rental flag onto rented, unrented, or unknown. A blank is unknown."""
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "rented" if value else "unrented"
    try:
        if pd.isna(value):
            return "unknown"
    except TypeError:
        pass
    if isinstance(value, np.bool_):
        return "rented" if bool(value) else "unrented"
    text = str(value).strip().lower()
    if text in {"true", "yes", "rented", "1"}:
        return "rented"
    if text in {"false", "no", "unrented", "0"}:
        return "unrented"
    return "unknown"


def _category_reference(classes: pd.Series, preferred: str, order: tuple[str, ...]) -> str:
    """`preferred` when the sample has it. Otherwise the most common level."""
    present = set(classes.unique())
    if preferred in present:
        return preferred
    counts = classes.value_counts()
    ordered = [level for level in order if level in counts.index]
    return max(ordered, key=lambda level: int(counts[level]))


def _reference(classes: pd.Series) -> str:
    """Class D when the sample has it. Otherwise the most common class."""
    return _category_reference(classes, ENERGY_REFERENCE, ENERGY_LEVELS)


def _design(
    prepared: pd.DataFrame, reference: str, rental_reference: str, extra: list[str]
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    columns: dict[str, pd.Series] = {"intercept": pd.Series(1.0, index=prepared.index)}
    for feature in FEATURES:
        if feature.key in prepared.columns:
            columns[feature.key] = prepared[feature.key]
    for key in extra:
        columns[key] = prepared[key]
    for level in ENERGY_LEVELS:
        if level == reference:
            continue
        if (prepared["energy_class"] == level).any():
            columns[f"energy:{level}"] = (prepared["energy_class"] == level).astype(float)
    for level in RENTAL_LEVELS:
        if level == rental_reference:
            continue
        if (prepared["rental"] == level).any():
            columns[f"rental:{level}"] = (prepared["rental"] == level).astype(float)
    design = pd.DataFrame(columns)
    # Feature slopes and energy classes go in first. A not-stated adjustment that
    # repeats a pattern already in the model is left out, so the euro effects stay separable.
    ordered = [
        column
        for column in design.columns
        if column != "intercept" and not str(column).startswith("missing:")
    ]
    ordered += [column for column in design.columns if str(column).startswith("missing:")]
    keep = ["intercept"]
    dropped: list[str] = []
    current = design[["intercept"]].to_numpy(dtype=float)
    for column in ordered:
        candidate = np.column_stack([current, design[column].to_numpy(dtype=float)])
        if np.linalg.matrix_rank(candidate) > current.shape[1]:
            keep.append(column)
            current = candidate
        else:
            dropped.append(column)
    return design[keep], tuple(dropped)


def _missing_phrase(feature: Feature) -> str:
    verb = "were" if feature.label.endswith("s") else "was"
    if feature.key in _FLAGS:
        return f"if {feature.label.lower()} {verb} left blank, compared with a stated no"
    return f"if {feature.label.lower()} {verb} left blank, compared with the average"


def _term_label(key: str) -> tuple[str, str, str]:
    """Label, group and unit phrase for a design-matrix column."""
    if key == "intercept":
        return "Intercept", "Baseline", ""
    if key.startswith("energy:"):
        level = key.split(":", 1)[1]
        return f"Energy class {level}", "Energy class", ""
    if key.startswith("rental:"):
        level = key.split(":", 1)[1]
        return level.capitalize(), "Tenancy", ""
    if key.startswith("missing:"):
        feature = _BY_KEY[key.split(":", 1)[1]]
        return f"{feature.label} not stated", feature.group, _missing_phrase(feature)
    feature = _BY_KEY[key]
    return feature.label, feature.group, feature.phrase


def _term_order(key: str) -> tuple[int, int, int, int]:
    if key == "intercept":
        return (0, 0, 0, 0)
    if key.startswith("energy:"):
        level = key.split(":", 1)[1]
        return (1, _GROUP_ORDER.index("Energy class"), ENERGY_LEVELS.index(level), 0)
    if key.startswith("rental:"):
        level = key.split(":", 1)[1]
        return (1, _GROUP_ORDER.index("Tenancy"), RENTAL_LEVELS.index(level), 0)
    missing = key.startswith("missing:")
    base = key.split(":", 1)[1] if missing else key
    feature = _BY_KEY[base]
    return (
        1,
        _GROUP_ORDER.index(feature.group),
        FEATURES.index(feature),
        1 if missing else 0,
    )


def _signed_euro(value: float, decimals: int = 0) -> str:
    sign = "+" if value >= 0 else "−"
    return f"{sign}{abs(value):,.{decimals}f} €"


def formula_lines(model: PriceModel) -> list[str]:
    """The fitted formula, one effect per line."""
    if model.error or model.terms.empty:
        return []
    intercept = float(model.terms.loc[model.terms["key"] == "intercept", "coefficient"].iloc[0])
    lines = [f"Asking price ≈ {intercept:,.0f} €"]
    for row in model.terms.itertuples(index=False):
        if row.key == "intercept":
            continue
        if str(row.key).startswith("energy:"):
            level = str(row.key).split(":", 1)[1]
            lines.append(
                f"{_signed_euro(row.coefficient)} when the energy class is {level} "
                f"rather than {model.reference}"
            )
            continue
        if str(row.key).startswith("rental:"):
            level = str(row.key).split(":", 1)[1]
            lines.append(
                f"{_signed_euro(row.coefficient)} when the flat is {level} "
                f"rather than {model.rental_reference}"
            )
            continue
        if str(row.key).startswith("missing:"):
            lines.append(f"{_signed_euro(row.coefficient)} {row.phrase}")
            continue
        phrase = _BY_KEY[row.key].phrase
        decimals = 2 if str(row.key) == "area_price_euros" else 0
        lines.append(f"{_signed_euro(row.coefficient, decimals)} {phrase}")
    lines.append(f"Energy class {model.reference} is the baseline (0 €).")
    lines.append(f"{model.rental_reference.capitalize()} is the baseline (0 €).")
    return lines


def _value_text(key: str, value: float, reference: str, energy_class: str) -> str:
    if key == "intercept":
        return "—"
    if key.startswith("energy:"):
        level = key.split(":", 1)[1]
        if energy_class == level:
            return f"{level}, rather than {reference}"
        return "—"
    if key in _FLAGS:
        return "Yes" if value >= 0.5 else "No"
    if key == "living_area":
        return f"{value:,.0f} m²"
    if key == "building_age_years":
        return f"{value:,.0f} years"
    if key in {"rooms", "bathrooms"}:
        text = f"{value:,.0f}" if float(value).is_integer() else f"{value:,.1f}"
        return text
    if key == "area_price_euros":
        return f"{value:,.0f} €"
    return f"{value:,.1f}"


def _energy_record(row: pd.Series, coef: dict[str, float], reference: str) -> dict[str, object]:
    energy_class = str(row["energy_class"])
    if energy_class == reference:
        return {
            "term": f"Energy class {energy_class}",
            "input": f"{energy_class} (baseline)",
            "euros": 0.0,
        }
    key = f"energy:{energy_class}"
    return {
        "term": f"Energy class {energy_class}",
        "input": f"{energy_class}, rather than {reference}",
        "euros": float(coef[key]) if key in coef else 0.0,
    }


def _rental_record(row: pd.Series, coef: dict[str, float], reference: str) -> dict[str, object]:
    status = str(row["rental"])
    if status == reference:
        return {"term": status.capitalize(), "input": f"{status} (baseline)", "euros": 0.0}
    key = f"rental:{status}"
    return {
        "term": status.capitalize(),
        "input": f"{status}, rather than {reference}",
        "euros": float(coef[key]) if key in coef else 0.0,
    }


def contributions(model: PriceModel, listing_index: object) -> pd.DataFrame:
    """Euro contribution of each term for one fitted listing. They sum to the prediction."""
    row = model.listings.loc[listing_index]
    coef = dict(zip(model.terms["key"], model.terms["coefficient"], strict=True))
    energy_class = str(row["energy_class"])
    records: list[dict[str, object]] = []
    energy_shown = False
    rental_shown = False
    for term in model.terms.itertuples(index=False):
        key = str(term.key)
        if key == "intercept":
            records.append({"term": "Intercept", "input": "—", "euros": float(term.coefficient)})
            continue
        if key.startswith("energy:"):
            if not energy_shown:
                records.append(_energy_record(row, coef, model.reference))
                energy_shown = True
            continue
        if key.startswith("rental:"):
            if not rental_shown:
                records.append(_rental_record(row, coef, model.rental_reference))
                rental_shown = True
            continue
        if key.startswith("missing:"):
            base = key.split(":", 1)[1]
            if float(row[f"{base}_missing"]) < 0.5:
                continue
            amount = float(term.coefficient)
            if base in coef:
                amount += float(coef[base]) * float(row[base])
            records.append({"term": term.label, "input": "not stated", "euros": amount})
            continue
        if f"{key}_missing" in row.index and float(row[f"{key}_missing"]) >= 0.5:
            continue
        value = float(row[key])
        records.append(
            {
                "term": term.label,
                "input": _input_text(row, key, value, model.reference, energy_class),
                "euros": float(term.coefficient) * value,
            }
        )
    if not energy_shown:
        records.append(_energy_record(row, coef, model.reference))
    if not rental_shown:
        records.append(_rental_record(row, coef, model.rental_reference))
    total = float(sum(float(record["euros"]) for record in records))
    records.append({"term": "Predicted price", "input": "", "euros": total})
    return pd.DataFrame(records)


def source_label(name: str) -> str:
    return SOURCE_LABELS.get(name, name)


def _blank_mask(series: pd.Series) -> pd.Series:
    blank = series.isna()
    if series.dtype == object or str(series.dtype) == "string":
        text = series.astype("string").str.strip()
        blank = blank | text.eq("") | text.str.lower().isin(["none", "nan"])
    return blank


def for_the_fit(features: pd.DataFrame) -> pd.DataFrame:
    """Drop ThinkImmo and unfinished-shell titles.

    ThinkImmo repeats ads the portal crawlers already store. A title that
    contains Rohling, Baugenehmigung, Unausgebaut, Unausgebautes, or
    Bauvorbescheid is an unfinished shell. Compounds without a word boundary stay in.
    """
    if features.empty:
        return features
    kept = features
    if "source" in kept.columns:
        source = kept["source"].fillna("").astype(str).str.strip().str.lower()
        kept = kept.loc[source != EXCLUDED_SOURCE]
    if "title" in kept.columns:
        kept = kept.loc[~titles_are_shell(kept["title"])]
    return kept.copy()


def missing_rates_by_source(features: pd.DataFrame) -> pd.DataFrame:
    """Percent of each source's listings where a field is blank.

    Columns are sources, largest first. The listing count is in the column name.
    ThinkImmo is left out, as is a title for an unfinished shell.
    """
    features = for_the_fit(features)
    if features.empty or "source" not in features.columns:
        return pd.DataFrame()
    source = features["source"].fillna("Unknown").astype(str)
    counts = source.value_counts()
    ordered = sorted(counts.index, key=lambda name: (-int(counts[name]), name))
    rows = []
    for key, label in GAP_FIELDS:
        row: dict[str, object] = {"information": label}
        if key in features.columns:
            series = features[key]
        else:
            series = pd.Series(np.nan, index=features.index)
        blank = _blank_mask(series)
        for name in ordered:
            group = source == name
            n = int(group.sum())
            share = 100.0 * float(blank[group].sum()) / n if n else float("nan")
            row[f"{source_label(name)} ({n})"] = share
        rows.append(row)
    return pd.DataFrame(rows)


def _source_notes(
    features: pd.DataFrame, prepared: pd.DataFrame, keys: list[str]
) -> tuple[str, ...]:
    """Name a source when none of its listings state a feature."""
    if "source" not in features.columns or not keys:
        return ()
    source = features["source"].fillna("Unknown").astype(str)
    notes: list[str] = []
    for key in keys:
        blank = [
            str(name)
            for name, index in source.groupby(source).groups.items()
            if len(index) >= _KEEP_IF_MISSING and int(prepared.loc[index, key].notna().sum()) == 0
        ]
        if blank:
            names = ", ".join(blank)
            notes.append(f"{_BY_KEY[key].label} is blank for every listing from {names}.")
    return tuple(notes)


def _by_source(features: pd.DataFrame, complete_index: pd.Index) -> pd.DataFrame:
    if "source" not in features.columns or features.empty:
        return pd.DataFrame(columns=["source", "listings", "used"])
    frame = pd.DataFrame(
        {
            "source": features["source"].fillna("Unknown").astype(str),
            "used": features.index.isin(complete_index),
        }
    )
    grouped = (
        frame.groupby("source", dropna=False)
        .agg(listings=("used", "size"), used=("used", "sum"))
        .reset_index()
        .sort_values(["used", "listings"], ascending=False, kind="mergesort")
    )
    grouped["listings"] = grouped["listings"].astype(int)
    grouped["used"] = grouped["used"].astype(int)
    return grouped.reset_index(drop=True)


def _coverage_row(label: str, stated: int, total: int, handling: str) -> dict[str, object]:
    return {
        "feature": label,
        "stated": stated,
        "not_stated": total - stated,
        "handling": handling,
    }


def _input_text(
    row: pd.Series, key: str, value: float, reference: str, energy_class: str
) -> str:
    """How one listing's value of a feature is shown next to its euro contribution."""
    if key == "area_price_euros" and "area_price_per_m2" in row.index:
        rate = row["area_price_per_m2"]
        area = row["living_area"] if "living_area" in row.index else float("nan")
        if pd.notna(rate) and pd.notna(area):
            return f"{float(rate):,.0f} €/m² × {float(area):,.0f} m²"
    return _value_text(key, value, reference, energy_class)


def fit_price_model(features: pd.DataFrame) -> PriceModel:
    """Fit asking price. Often-blank features stay in, with a not-stated adjustment.

    ThinkImmo rows are left out, as is a title for an unfinished shell.
    """
    features = for_the_fit(features)
    n_input = len(features)
    levels = local_price_levels(features)
    prepared = pd.DataFrame(index=features.index)
    missing_rows = []
    prepared["price"] = _column(features, "price")
    prepared["area_price_per_m2"] = levels["area_price_per_m2"]
    missing_rows.append({"feature": "Price", "missing": int(prepared["price"].isna().sum())})
    for feature in FEATURES:
        if feature.key == "area_price_euros":
            prepared[feature.key] = levels["area_price_euros"]
        else:
            prepared[feature.key] = _column(features, feature.key)
        missing_rows.append(
            {"feature": feature.missing_label, "missing": int(prepared[feature.key].isna().sum())}
        )
    if "energy_efficiency_class" in features.columns:
        prepared["energy_class"] = features["energy_efficiency_class"].map(clean_energy_class)
    else:
        prepared["energy_class"] = "unknown"
    if "is_rented" in features.columns:
        prepared["rental"] = features["is_rented"].map(clean_rental)
    else:
        prepared["rental"] = "unknown"
    missing = pd.DataFrame(missing_rows)

    require = ["price"]
    indicate: list[str] = []
    no_data: list[str] = []
    coverage_rows = [
        _coverage_row(
            "Price",
            int(prepared["price"].notna().sum()),
            n_input,
            "Listings without a price are left out",
        )
    ]
    for feature in FEATURES:
        stated = int(prepared[feature.key].notna().sum())
        missing_n = n_input - stated
        if stated == 0:
            no_data.append(feature.key)
            handling = "No data, so it is not in the model"
        elif missing_n == 0:
            handling = "Used"
        elif missing_n < _KEEP_IF_MISSING:
            require.append(feature.key)
            handling = "The few listings without it are left out"
        else:
            indicate.append(feature.key)
            handling = "Kept, with a not-stated adjustment"
        coverage_rows.append(_coverage_row(feature.missing_label, stated, n_input, handling))
    coverage = pd.DataFrame(coverage_rows)
    notes = _source_notes(features, prepared, indicate)

    sample = prepared.dropna(subset=require).copy()
    fills: dict[str, float] = {}
    extra: list[str] = []
    for key in indicate:
        if int(sample[key].notna().sum()) == 0 or int(sample[key].isna().sum()) == 0:
            continue
        extra.append(f"missing:{key}")
        sample[f"{key}_missing"] = sample[key].isna().astype(float)
        sample[f"missing:{key}"] = sample[f"{key}_missing"]
        if key in _FLAGS:
            sample[key] = sample[key].fillna(0.0)
        else:
            fills[key] = float(sample[key].mean())
            sample[key] = sample[key].fillna(fills[key])
    for key in no_data:
        if key in sample.columns:
            sample = sample.drop(columns=[key])

    by_source = _by_source(features, sample.index)
    empty = PriceModel(
        n_input=n_input,
        n_used=len(sample),
        missing=missing,
        coverage=coverage,
        by_source=by_source,
        notes=notes,
        fills=fills,
        reference=ENERGY_REFERENCE,
        rental_reference=RENTAL_REFERENCE,
        omitted=tuple(no_data),
        robust=False,
        rsquared=None,
        rmse=None,
        median_abs_error=None,
        terms=pd.DataFrame(),
        listings=pd.DataFrame(),
        error=None,
    )
    if sample.empty or sample["price"].notna().sum() == 0:
        empty.error = "No listing has a price."
        return empty

    reference = _reference(sample["energy_class"])
    rental_reference = _category_reference(sample["rental"], RENTAL_REFERENCE, RENTAL_LEVELS)
    design, dropped = _design(sample, reference, rental_reference, extra)
    omitted = tuple([*no_data, *dropped])
    if design.shape[1] < 2 or len(sample) <= design.shape[1] + 1:
        empty.reference = reference
        empty.rental_reference = rental_reference
        empty.omitted = omitted
        empty.error = (
            f"{len(sample)} listings have a price. "
            "That is too few to separate a euro effect for each feature."
        )
        return empty

    y = sample["price"].to_numpy(dtype=float)
    x = design.to_numpy(dtype=float)
    if np.linalg.matrix_rank(x) < x.shape[1]:
        empty.reference = reference
        empty.rental_reference = rental_reference
        empty.omitted = omitted
        empty.error = "The features overlap too much to separate their euro effects."
        return empty

    fitted = sm.OLS(y, x).fit()
    robust = False
    if float(fitted.ssr) > 1e-6:
        fitted = sm.OLS(y, x).fit(cov_type="HC1")
        robust = True

    conf = fitted.conf_int()
    records = []
    for position, key in enumerate(design.columns):
        label, group, phrase = _term_label(str(key))
        records.append(
            {
                "key": str(key),
                "label": label,
                "group": group,
                "phrase": phrase,
                "coefficient": float(fitted.params[position]),
                "ci_low": float(conf[position, 0]),
                "ci_high": float(conf[position, 1]),
                "p_value": float(fitted.pvalues[position]),
            }
        )
    terms = pd.DataFrame(records)
    terms["_order"] = terms["key"].map(_term_order)
    terms = (
        terms.sort_values("_order", kind="mergesort").drop(columns="_order").reset_index(drop=True)
    )

    predicted = pd.Series(fitted.fittedvalues, index=sample.index)
    listings = sample.copy()
    listings["predicted"] = predicted
    listings["gap"] = listings["price"] - listings["predicted"]
    for column in ("id", "title", "url", "bezirk"):
        if column in features.columns:
            listings[column] = features.loc[sample.index, column]
        else:
            listings[column] = np.nan

    resid = listings["price"] - listings["predicted"]
    return PriceModel(
        n_input=n_input,
        n_used=len(listings),
        missing=missing,
        coverage=coverage,
        by_source=by_source,
        notes=notes,
        fills=fills,
        reference=reference,
        rental_reference=rental_reference,
        omitted=omitted,
        robust=robust,
        rsquared=float(fitted.rsquared),
        rmse=float(np.sqrt(np.mean(np.square(resid)))),
        median_abs_error=float(np.median(np.abs(resid))),
        terms=terms,
        listings=listings,
        error=None,
    )
