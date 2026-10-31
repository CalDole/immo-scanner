"""Average asking price per m² of each Planungsraum.

The mean is taken over active listings. ThinkImmo is left out, because those rows repeat
ads the portal crawlers already store. A listing whose title contains the word Rohling
is left out too. Each remaining listing counts once: its price divided by its living area.
"""

import logging

import pandas as pd
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from immo_scanner.db.session import get_engine
from immo_scanner.explore.data import titles_are_shell

log = logging.getLogger(__name__)

EXCLUDED_SOURCE = "thinkimmo"
HOVER_COLUMN = "avg_price_per_m2"
_MISSING = "–"

_COLUMNS = ["area_id", "value", "listings", "bezirk"]
_SAMPLE_COLUMNS = [
    "area_id",
    "value",
    "listings",
    "bezirk",
    "median_living_area",
    "median_year_built",
]

_SQL = """
SELECT source, title, price, living_area, year_built, plr_id, bezirk
FROM listing_features
WHERE status = 'active'
  AND price > 0
  AND living_area > 0
  AND plr_id IS NOT NULL
"""


def average_price_per_m2(listings: pd.DataFrame) -> pd.DataFrame:
    """Mean price per m² per Planungsraum.

    `value` is the unweighted mean of price / living area. `listings` is how many
    ads entered that mean. ThinkImmo and Rohling titles are dropped, as are rows
    without a positive price, a positive living area, or a Planungsraum.
    """
    qualifying = _qualifying(listings)
    if qualifying.empty:
        return pd.DataFrame(columns=_COLUMNS)
    grouped = qualifying.groupby("area_id", sort=False)
    result = grouped.agg(
        value=("sqm", "mean"),
        listings=("sqm", "count"),
        bezirk=("bezirk", "first"),
    ).reset_index()
    result["listings"] = result["listings"].astype(int)
    return result[_COLUMNS]


def area_listing_stats(listings: pd.DataFrame) -> pd.DataFrame:
    """Mean €/m² per Planungsraum, plus the median living area and year built.

    `value` and `listings` match `average_price_per_m2`. `median_year_built` uses only
    the ads that state a year; an ad without one still counts in the price and the
    living-area median. The same ThinkImmo and Rohling exclusions apply.
    """
    qualifying = _qualifying(listings)
    if qualifying.empty:
        return pd.DataFrame(columns=_SAMPLE_COLUMNS)
    grouped = qualifying.groupby("area_id", sort=False)
    result = grouped.agg(
        value=("sqm", "mean"),
        listings=("sqm", "count"),
        bezirk=("bezirk", "first"),
        median_living_area=("living_area", "median"),
        median_year_built=("year_built", "median"),
    ).reset_index()
    result["listings"] = result["listings"].astype(int)
    return result[_SAMPLE_COLUMNS]


def overall_mean(averages: pd.DataFrame) -> float | None:
    """Listing-weighted mean of Planungsraum averages.

    Weighting by `listings` makes this the same number as the mean of the
    individual listing prices per m².
    """
    if averages.empty or "value" not in averages.columns or "listings" not in averages.columns:
        return None
    values = pd.to_numeric(averages["value"], errors="coerce")
    weights = pd.to_numeric(averages["listings"], errors="coerce")
    mask = values.notna() & weights.notna() & (weights > 0)
    total = float(weights.loc[mask].sum())
    if not total:
        return None
    return float((values.loc[mask] * weights.loc[mask]).sum() / total)


def price_comparison(averages: pd.DataFrame, plr_id: str) -> dict[str, str] | None:
    """One neighbourhood row: this Planungsraum, its Bezirk, and Berlin."""
    if averages.empty or not plr_id or "area_id" not in averages.columns:
        return None
    area_id = str(plr_id).strip()
    match = averages.loc[averages["area_id"].astype(str).str.strip() == area_id]
    if match.empty or pd.isna(match.iloc[0]["value"]):
        return None
    bezirk_mean = None
    bezirk = match.iloc[0]["bezirk"] if "bezirk" in match.columns else None
    if isinstance(bezirk, str) and bezirk.strip() and "bezirk" in averages.columns:
        same = averages.loc[averages["bezirk"] == bezirk]
        bezirk_mean = overall_mean(same)
    return {
        "Indicator": "Asking price per m²",
        "Here": _eur_m2(match.iloc[0]["value"]),
        "Bezirk average": _eur_m2(bezirk_mean),
        "Berlin average": _eur_m2(overall_mean(averages)),
    }


def averages_for(plr_ids: pd.Series, averages: pd.DataFrame) -> pd.Series:
    """Average €/m² aligned to `plr_ids`. Planungsräume without a price stay empty."""
    result = pd.Series(pd.NA, index=plr_ids.index, dtype="Float64")
    if averages.empty or "area_id" not in averages.columns or "value" not in averages.columns:
        return result
    lookup = averages.dropna(subset=["area_id"]).drop_duplicates("area_id").copy()
    lookup.index = lookup["area_id"].map(lambda value: str(value).strip())
    keys = plr_ids.map(lambda value: "" if pd.isna(value) else str(value).strip())
    mapped = pd.to_numeric(keys.map(lookup["value"]), errors="coerce")
    result.loc[keys != ""] = mapped.loc[keys != ""].astype("Float64")
    return result


def merge_hover_prices(stats: pd.DataFrame, averages: pd.DataFrame) -> pd.DataFrame:
    """Add `avg_price_per_m2` to Planungsraum hover stats, including areas only priced."""
    prices = _price_keys(averages)
    if stats.empty or "plr_id" not in stats.columns:
        return prices.reset_index(drop=True)
    left = stats.copy()
    left["_plr"] = left["plr_id"].map(lambda value: "" if pd.isna(value) else str(value).strip())
    blank = left.loc[left["_plr"] == ""].drop(columns="_plr")
    blank[HOVER_COLUMN] = pd.NA
    keyed = left.loc[left["_plr"] != ""].drop_duplicates("_plr")
    merged = keyed.merge(prices.rename(columns={"plr_id": "_plr"}), on="_plr", how="outer")
    missing_id = merged["plr_id"].isna() & merged["_plr"].fillna("").ne("")
    merged.loc[missing_id, "plr_id"] = merged.loc[missing_id, "_plr"]
    merged = merged.drop(columns="_plr")
    return pd.concat([merged, blank], ignore_index=True)


def local_price_levels(listings: pd.DataFrame) -> pd.DataFrame:
    """Planungsraum €/m² for each listing, and that rate times its living area.

    The rate uses the same ads as the map average: active listings, ThinkImmo
    excluded, and titles that contain the word Rohling excluded. An ad that
    entered the average is held out of its own rate, so it does not help predict
    itself. A Planungsraum with one qualifying ad has no rate for that ad.
    ThinkImmo, Rohling and removed ads stay out of the average and receive the
    full rate instead. `area_price_euros` is blank when the rate or the living
    area is blank.
    """
    rates = pd.Series(float("nan"), index=listings.index, dtype=float)
    if not listings.empty:
        qualifying = _qualifying(_active(listings))
        if not qualifying.empty:
            totals = qualifying.groupby("area_id", sort=False)["sqm"].agg(["sum", "count"])
            count = qualifying["area_id"].map(totals["count"])
            total = qualifying["area_id"].map(totals["sum"])
            held_out = pd.Series(float("nan"), index=qualifying.index, dtype=float)
            several = count >= 2
            held_out.loc[several] = ((total - qualifying["sqm"]) / (count - 1)).loc[several]
            rates.loc[qualifying.index] = held_out.to_numpy()
            outside = ~rates.index.isin(qualifying.index)
            keys = _area_keys(listings)
            means = totals["sum"] / totals["count"]
            usable = outside & keys.isin(means.index)
            rates.loc[usable] = keys.loc[usable].map(means).to_numpy()
    if "living_area" in listings.columns:
        living = pd.to_numeric(listings["living_area"], errors="coerce")
    else:
        living = pd.Series(float("nan"), index=listings.index, dtype=float)
    euros = (rates * living).where(living > 0)
    return pd.DataFrame({"area_price_per_m2": rates, "area_price_euros": euros})


def load_average_price_per_m2() -> pd.DataFrame:
    """Active listings aggregated to one average €/m² per Planungsraum.

    An empty frame means `listing_features` is not there yet, or no listing qualifies.
    """
    return average_price_per_m2(_load_listings())


def load_area_listing_stats() -> pd.DataFrame:
    """`area_listing_stats` for the active listings in `listing_features`.

    An empty frame means the view is not there yet, or no listing qualifies.
    """
    return area_listing_stats(_load_listings())


def _load_listings() -> pd.DataFrame:
    try:
        with get_engine().connect() as conn:
            return pd.read_sql(text(_SQL), conn)
    except ProgrammingError as exc:
        sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
        if sqlstate != "42P01":
            raise
        log.info("listing_features is not available")
        return pd.DataFrame()


def _active(listings: pd.DataFrame) -> pd.DataFrame:
    """Rows that can enter the map average. A frame without status counts as active."""
    if listings.empty or "status" not in listings.columns:
        return listings
    status = listings["status"].fillna("").astype(str).str.strip().str.lower()
    return listings.loc[status == "active"]


def _area_keys(listings: pd.DataFrame) -> pd.Series:
    if "plr_id" not in listings.columns:
        return pd.Series("", index=listings.index, dtype=str)
    return listings["plr_id"].map(lambda value: "" if pd.isna(value) else str(value).strip())


def _qualifying(listings: pd.DataFrame) -> pd.DataFrame:
    columns = ("source", "title", "price", "living_area", "plr_id")
    if listings.empty or any(column not in listings.columns for column in columns):
        return pd.DataFrame(columns=[*columns, "area_id", "sqm", "bezirk", "year_built"])
    optional = [column for column in ("bezirk", "year_built") if column in listings.columns]
    frame = listings.loc[:, [*columns, *optional]].copy()
    source = frame["source"].fillna("").astype(str).str.strip().str.lower()
    frame = frame.loc[source != EXCLUDED_SOURCE]
    frame = frame.loc[~titles_are_shell(frame["title"])]
    frame["price"] = pd.to_numeric(frame["price"], errors="coerce")
    frame["living_area"] = pd.to_numeric(frame["living_area"], errors="coerce")
    frame = frame.loc[(frame["price"] > 0) & (frame["living_area"] > 0) & frame["plr_id"].notna()]
    frame["area_id"] = frame["plr_id"].map(lambda value: str(value).strip())
    frame = frame.loc[frame["area_id"] != ""]
    frame["sqm"] = frame["price"] / frame["living_area"]
    if "bezirk" not in frame.columns:
        frame["bezirk"] = pd.NA
    if "year_built" in frame.columns:
        frame["year_built"] = pd.to_numeric(frame["year_built"], errors="coerce")
    else:
        frame["year_built"] = float("nan")
    return frame


def _price_keys(averages: pd.DataFrame) -> pd.DataFrame:
    empty = pd.DataFrame(columns=["plr_id", HOVER_COLUMN])
    if averages.empty or "area_id" not in averages.columns or "value" not in averages.columns:
        return empty
    prices = averages.loc[:, ["area_id", "value"]].rename(
        columns={"area_id": "plr_id", "value": HOVER_COLUMN}
    )
    prices = prices.dropna(subset=["plr_id"])
    prices["plr_id"] = prices["plr_id"].map(lambda value: str(value).strip())
    prices = prices.loc[prices["plr_id"] != ""].drop_duplicates("plr_id")
    return prices.reset_index(drop=True)


def _eur_m2(value: object) -> str:
    number = pd.to_numeric(value, errors="coerce")
    if pd.isna(number):
        return _MISSING
    return f"{float(number):,.0f} €/m²"
