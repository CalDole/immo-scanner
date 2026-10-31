"""Residents by origin per Planungsraum and date (table plr_origin_stats) for the app."""

import pandas as pd
from sqlalchemy import text

from immo_scanner.db.session import get_engine
from immo_scanner.reference.origins import MIN_RESIDENTS, diversity

STAT_COLUMNS = ["plr_id", "ref_date", "origin_key", "kind", "count", "share", "label"]
KIND_ORDER = {"total": 0, "group": 1, "diversity": 2, "country": 3}
GROUP_ORDER = ["mh_outside", "eu", "oic", "arab", "soviet", "yugo"]


def load_origin_stats(plr_id: str | None = None) -> pd.DataFrame:
    query = f"SELECT {', '.join(STAT_COLUMNS)} FROM plr_origin_stats"
    params = {}
    if plr_id:
        query += " WHERE plr_id = :plr_id"
        params["plr_id"] = plr_id
    with get_engine().connect() as conn:
        stats = pd.read_sql(text(query), conn, params=params)
    stats["ref_date"] = pd.to_datetime(stats["ref_date"]).dt.date
    return stats


def origin_catalog(stats: pd.DataFrame) -> pd.DataFrame:
    """One row per origin_key (except `residents`): kind, label and the dates it exists on.

    Order: totals, groups, diversity, then countries by label with `unassigned` last.
    """
    rows = stats[stats["origin_key"] != "residents"]
    catalog = (
        rows.groupby("origin_key")
        .agg(
            kind=("kind", "first"),
            label=("label", "first"),
            dates=("ref_date", lambda d: sorted(set(d))),
        )
        .reset_index()
    )
    catalog["_kind"] = catalog["kind"].map(KIND_ORDER)
    catalog["_group"] = (
        catalog["origin_key"]
        .map({key: i for i, key in enumerate(GROUP_ORDER)})
        .fillna(len(GROUP_ORDER))
    )
    catalog["_last"] = catalog["origin_key"] == "unassigned"
    return (
        catalog.sort_values(["_kind", "_group", "_last", "label"])
        .drop(columns=["_kind", "_group", "_last"])
        .reset_index(drop=True)
    )


def _small_areas(stats: pd.DataFrame, when) -> set[str]:
    residents = stats[(stats["origin_key"] == "residents") & (stats["ref_date"] == when)]
    small = residents["count"].isna() | (residents["count"] < MIN_RESIDENTS)
    return set(residents.loc[small, "plr_id"])


def map_values(stats: pd.DataFrame, key: str, when, measure: str = "share") -> pd.DataFrame:
    """area_id, value for one origin on one date; areas below MIN_RESIDENTS are missing."""
    rows = stats[(stats["origin_key"] == key) & (stats["ref_date"] == when)]
    values = rows[["plr_id", measure]].rename(columns={"plr_id": "area_id", measure: "value"})
    values["value"] = values["value"].astype(float)
    values.loc[values["area_id"].isin(_small_areas(stats, when)), "value"] = None
    return values.reset_index(drop=True)


def area_totals(stats: pd.DataFrame, groups: pd.Series) -> pd.DataFrame:
    """Origin counts summed over groups of PLRs (e.g. Bezirke), with weighted shares.

    `groups` maps plr_id -> group name. Shares are summed counts over summed residents, and
    the diversity index is computed from the summed counts. Returns group, ref_date,
    origin_key, count, share.
    """
    counts = stats[stats["kind"] != "diversity"].assign(group=stats["plr_id"].map(groups))
    counts = counts.dropna(subset=["group"])
    sums = counts.groupby(["group", "ref_date", "origin_key"])["count"].sum(min_count=1)
    wide = sums.unstack("origin_key")
    shares = wide.div(wide["residents"].where(wide["residents"] > 0), axis=0) * 100
    result = pd.concat([sums.rename("count"), shares.stack().rename("share")], axis=1).reset_index()
    if {"eu", "unassigned"} <= set(wide.columns):
        index = diversity(wide) * 100
        simpson = index.rename("share").reset_index().assign(origin_key="simpson", count=None)
        result = pd.concat([result, simpson[result.columns]], ignore_index=True)
    return result


def profile_countries(
    stats: pd.DataFrame,
    plr_id: str,
    bezirk_totals: pd.DataFrame,
    berlin_totals: pd.DataFrame,
    bezirk: str,
    when,
    top: int = 10,
) -> pd.DataFrame:
    """Country shares of one PLR next to its Bezirk and Berlin, the PLR's top countries first.

    Long format: label, scope (Planungsraum | Bezirk | Berlin), share.
    """
    countries = stats[
        (stats["plr_id"] == plr_id)
        & (stats["ref_date"] == when)
        & (stats["kind"] == "country")
        & (stats["origin_key"] != "unassigned")
    ].nlargest(top, "share")
    labels = countries.set_index("origin_key")["label"]

    def scope_shares(totals: pd.DataFrame, group: str, scope: str) -> pd.DataFrame:
        rows = totals[
            (totals["group"] == group)
            & (totals["ref_date"] == when)
            & totals["origin_key"].isin(labels.index)
        ]
        return pd.DataFrame(
            {"origin_key": rows["origin_key"], "scope": scope, "share": rows["share"]}
        )

    frames = [
        countries[["origin_key", "share"]].assign(scope="Planungsraum"),
        scope_shares(bezirk_totals, bezirk, "Bezirk"),
        scope_shares(berlin_totals, "Berlin", "Berlin"),
    ]
    result = pd.concat(frames, ignore_index=True)
    result["label"] = result["origin_key"].map(labels)
    return result[["label", "scope", "share"]]


def time_series(
    stats: pd.DataFrame,
    key: str,
    plr_id: str,
    bezirk_totals: pd.DataFrame,
    berlin_totals: pd.DataFrame,
    bezirk: str,
) -> pd.DataFrame:
    """ref_date, scope, share of one origin over time for a PLR, its Bezirk and Berlin.

    Dates where the origin is not published have no row, so lines stop there.
    """
    own = stats[(stats["plr_id"] == plr_id) & (stats["origin_key"] == key)]
    frames = [own[["ref_date", "share"]].assign(scope="Planungsraum")]
    for totals, group, scope in (
        (bezirk_totals, bezirk, "Bezirk"),
        (berlin_totals, "Berlin", "Berlin"),
    ):
        rows = totals[(totals["group"] == group) & (totals["origin_key"] == key)]
        frames.append(rows[["ref_date", "share"]].assign(scope=scope))
    return pd.concat(frames, ignore_index=True).dropna(subset=["share"])


def kiez_summary(stats: pd.DataFrame, top: int = 5) -> dict | None:
    """Latest residents, MH share, diversity, top countries and Vietnam for one PLR's rows."""
    if stats.empty:
        return None
    when = stats["ref_date"].max()
    latest = stats[stats["ref_date"] == when].set_index("origin_key")

    def value(key: str, column: str):
        if key not in latest.index or pd.isna(latest.at[key, column]):
            return None
        return latest.at[key, column]

    residents = value("residents", "count")
    countries = latest[(latest["kind"] == "country") & (latest.index != "unassigned")]
    small = residents is None or residents < MIN_RESIDENTS
    ranked = (
        []
        if small
        else [
            (row.label, row.share)
            for row in countries.dropna(subset=["share"]).nlargest(top, "share").itertuples()
        ]
    )
    return {
        "ref_date": when,
        "residents": None if residents is None else int(residents),
        "mh_share": value("mh", "share"),
        "diversity": value("simpson", "share"),
        "top": ranked,
        "vietnam": None if small else value("vietnam", "share"),
    }
