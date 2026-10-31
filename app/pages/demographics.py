"""Demographics of Berlin's Planungsräume and Bezirke: thematic maps and a combined map."""

import html
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta

import altair as alt
import pandas as pd
import streamlit as st
from demographics_maps import (
    LAYER_RAMPS,
    OTHER_PARTY_COLOR,
    PARTY_COLORS,
    RAMPS,
    area_id_from_query,
    category_collection,
    category_legend_html,
    choropleth_layer,
    deck,
    diverging_bins,
    feature_collection,
    focus_layer,
    focus_view,
    format_value,
    heatmap_layer,
    intersect,
    legend_html,
    outline_layer,
    party_label,
    quantile_bins,
    reversed_ramp,
    scatter_layer,
    threshold_hide,
)

from immo_scanner.explore.crime import crime_trend, load_crime
from immo_scanner.explore.demographics import (
    indicator_catalog,
    load_age,
    load_areas,
    load_indicators,
    to_plr,
)
from immo_scanner.explore.education import LEVELS, load_planungsraum_education
from immo_scanner.explore.elections import (
    load_election_districts,
    load_plr_weights,
    party_shares_long,
    plr_party_shares,
    strongest_party,
    tolerance_scores,
)
from immo_scanner.explore.incidents import (
    MIN_FOREIGN_POPULATION,
    MIN_POPULATION,
    RATE_PRECISIONS,
    incident_date_range,
    incident_rates,
    load_foreign_citizens,
    load_incidents,
    load_migration_background,
    precision_counts,
    unplaced_counts,
    window_years,
)
from immo_scanner.explore.milieuschutz import load_coverage, load_gebiete
from immo_scanner.explore.origins import (
    area_totals,
    load_origin_stats,
    map_values,
    origin_catalog,
    profile_countries,
    time_series,
)
from immo_scanner.explore.prices import load_average_price_per_m2, overall_mean
from immo_scanner.reference.crime import OFFENSES
from immo_scanner.reference.origins import MIN_RESIDENTS

COMBINED_MAP_STATE_KEY = "demographics_combined_map_snapshot"


@dataclass(frozen=True)
class Theme:
    key: str
    title: str
    ramp: str
    description: str
    hint: str
    defaults: tuple[str, ...] = ()


THEMES = [
    Theme(
        "migration",
        "Migration background",
        "Oranges",
        "Residents who are foreign citizens, naturalised, or have a parent who immigrated.",
        "Run `immo load-reference mss`.",
    ),
    Theme(
        "citizenship",
        "German citizens",
        "Blues",
        "Citizenship from the population register (MSS) and the Zensus 2022 grid.",
        "Run `immo load-reference mss` and `immo load-reference citizenship`.",
        ("german_share_mss", "german_share", "german_zensus"),
    ),
    Theme(
        "religion",
        "Religion",
        "Purples",
        "Church membership from Zensus 2022. Islam, Orthodox and Jewish only appear if you "
        "load your own Bezirk table: no official source counts them.",
        "Run `immo load-reference religion` (and optionally `immo load-reference "
        "religion-bezirk --file ...`).",
    ),
    Theme(
        "age",
        "Age",
        "Greens",
        "Age groups from the population register.",
        "Run `immo load-reference lor` and `immo load-reference population`.",
    ),
    Theme(
        "unemployment",
        "Unemployment",
        "Reds",
        "Unemployed residents receiving basic income support (SGB II).",
        "Run `immo load-reference mss`.",
    ),
    Theme(
        "income",
        "Income",
        "Greens",
        "Low-wage share from MSS; median gross monthly pay if you load the AfS table.",
        "Run `immo load-reference mss` (low-wage share) or `immo load-reference income "
        "--file ...` (median pay).",
        ("median_gross_pay",),
    ),
    Theme(
        "education",
        "Education",
        "Blues",
        "Education certificates of residents (Mikrozensus, Bezirk level) and the current "
        "Abitur average of the six nearest Gymnasiums or Sekundarschulen per Planungsraum, "
        "exponentially weighted by straight-line distance. Sekundarschule grades are shifted "
        "by -0.3 to offset the Berlin means (2.5 versus 2.2). A lower result is stronger.",
        "Run `immo load-reference gymnasium-stats`, `immo load-reference sekundarschule-stats` "
        "or `immo load-reference education`.",
        ("nearby_school_abi_average",),
    ),
    Theme(
        "school_load",
        "Schultypisierung",
        "Reds",
        "Schultypisierungsstufe of the six nearest Gymnasiums or Sekundarschulen per "
        "Planungsraum, exponentially weighted by straight-line distance. A higher Stufe "
        "means a heavier structural load, not a weaker school.",
        "Run `immo load-reference gymnasium-stats` or `immo load-reference sekundarschule-stats`.",
        ("nearby_school_type_level",),
    ),
]


@dataclass
class Overlay:
    key: str
    title: str
    label: str
    unit: str
    level: str
    values: pd.DataFrame
    caption: str
    ramp: str


@st.cache_data(ttl=3600)
def areas(level: str) -> pd.DataFrame:
    return load_areas(level)


@st.cache_data(ttl=3600)
def indicators() -> pd.DataFrame:
    frames = [load_indicators(), load_age()]
    return pd.concat([f for f in frames if not f.empty], ignore_index=True)


plr_areas = areas("plr")
if plr_areas.empty:
    st.info("No Planungsräume loaded. Run `immo load-reference lor` first.")
    st.stop()
bezirk_areas = areas("bezirk")
data = indicators()
catalog = indicator_catalog(data)

st.title("Demographics")
st.caption(
    "Shares of residents per Planungsraum (542 areas) or, where only coarser data exists, per "
    "Bezirk. Hover an area for its value. Grey areas have no published value (too few "
    "residents or suppressed)."
)
# Applied before the origin profile widget exists, and only when the linked area changes,
# so a later choice in that menu is kept.
focus_plr = area_id_from_query(st.query_params.get("plr"), set(plr_areas["area_id"]))
if focus_plr and st.session_state.get("demographics_focus_plr") != focus_plr:
    st.session_state["demographics_focus_plr"] = focus_plr
    st.session_state["origins_plr"] = focus_plr
    parent = plr_areas.loc[plr_areas["area_id"] == focus_plr, "bzr_id"]
    if not parent.empty and pd.notna(parent.iloc[0]):
        st.session_state["crime_bzr"] = parent.iloc[0]
if focus_plr:
    focused = plr_areas.loc[plr_areas["area_id"] == focus_plr].iloc[0]
    st.caption(f"Planungsraum {focused['name']} · {focused['bezirk']}")
combined_slot = st.container()
st.divider()


def _values(topic: str, indicator: str) -> pd.DataFrame:
    mask = (data["topic"] == topic) & (data["indicator"] == indicator)
    return data.loc[mask, ["area_id", "value"]].reset_index(drop=True)


def _summed(topic: str, indicators_: list[str]) -> pd.DataFrame:
    """Sum of several share indicators per area; missing if any part is missing."""
    frame = data[(data["topic"] == topic) & data["indicator"].isin(indicators_)]
    wide = frame.pivot_table(index="area_id", columns="indicator", values="value", aggfunc="first")
    total = wide.sum(axis=1, min_count=len(indicators_))
    return total.rename("value").reset_index()


def _caption(rows: pd.DataFrame) -> str:
    parts = []
    for source, group in rows.groupby("source", sort=False):
        dates = ", ".join(sorted({str(d) for d in group["ref_date"] if pd.notna(d) and d}))
        level = "Bezirk level" if (group["level"] == "bezirk").all() else "Planungsraum level"
        parts.append(f"{source}" + (f" ({dates})" if dates else "") + f" · {level}")
    return "Source: " + "; ".join(parts)


def _option_label(row: pd.Series) -> str:
    return row["label"] + (" (Bezirk level)" if row["level"] == "bezirk" else "")


def _pick_indicator(theme: Theme, rows: pd.DataFrame) -> Overlay:
    options = {_option_label(row): row for _, row in rows.iterrows()}
    available = list(rows["indicator"])
    default = next((available.index(d) for d in theme.defaults if d in available), 0)
    key = "education_indicator_v2" if theme.key == "education" else f"{theme.key}_indicator"
    choice = st.selectbox("Show", list(options), index=default, key=key)
    row = options[choice]
    ramp = "diverging" if row["unit"] == "pp" else theme.ramp
    if row["indicator"] == "nearby_school_abi_average":
        ramp = f"{theme.ramp}_r"
    return Overlay(
        theme.key,
        theme.title,
        row["label"],
        row["unit"],
        row["level"],
        _values(theme.key, row["indicator"]),
        _caption(rows[rows["indicator"] == row["indicator"]]),
        ramp,
    )


def _migration(theme: Theme, rows: pd.DataFrame) -> Overlay:
    overlay = _pick_indicator(theme, rows[~rows["indicator"].str.startswith("origin_")])
    st.caption("Origin groups and countries per Planungsraum since 2021: see the Origins tab.")
    return overlay


def _age(theme: Theme, rows: pd.DataFrame) -> Overlay:
    broad = rows[rows["indicator"].isin(["under_18", "18_to_64", "65_plus"])]
    bands = rows[~rows.index.isin(broad.index)]
    mode = st.radio(
        "Groups",
        ["Children / adults / pensioners", "Custom age range"],
        horizontal=True,
        key="age_mode",
    )
    if mode == "Custom age range" and not bands.empty:
        labels = list(bands["label"])
        low, high = st.select_slider(
            "Age bands", options=labels, value=(labels[0], labels[2]), key="age_bands"
        )
        chosen = bands.iloc[labels.index(low) : labels.index(high) + 1]
        label = f"Age {low}" + (f" … {high}" if low != high else "")
        return Overlay(
            theme.key,
            theme.title,
            label,
            "pct",
            "plr",
            _summed(theme.key, list(chosen["indicator"])),
            _caption(bands),
            theme.ramp,
        )
    return _pick_indicator(theme, broad)


def _religion(theme: Theme, rows: pd.DataFrame) -> Overlay:
    overlay = _pick_indicator(theme, rows)
    if not (rows["level"] == "bezirk").any():
        st.caption(
            "Islam, Orthodox and Jewish: no official count exists in Germany (Zensus 2022 only "
            "records church tax membership). Load your own table with "
            "`immo load-reference religion-bezirk --file ...`."
        )
    return overlay


def _edges(overlay: Overlay) -> list[float]:
    if overlay.ramp == "diverging":
        return diverging_bins(overlay.values["value"])
    return quantile_bins(overlay.values["value"])


def render_theme(theme: Theme, show_map: bool) -> Overlay | None:
    st.subheader(theme.title)
    st.caption(theme.description)
    rows = catalog[catalog["topic"] == theme.key]
    if rows.empty:
        st.info(f"No data yet. {theme.hint}")
        return None
    picker = {"migration": _migration, "age": _age, "religion": _religion}.get(
        theme.key, _pick_indicator
    )
    overlay = picker(theme, rows)
    if not show_map:
        return overlay
    level_areas = plr_areas if overlay.level == "plr" else bezirk_areas
    edges = _edges(overlay)
    layers = [
        choropleth_layer(
            feature_collection(
                level_areas, overlay.values, edges, overlay.ramp, 0.75, overlay.unit, overlay.label
            ),
            f"{theme.key}_fill",
            stroked=overlay.level == "plr",
        ),
        outline_layer(bezirk_areas, f"{theme.key}_bezirke"),
    ]
    st.pydeck_chart(deck(layers), height=430, key=f"{theme.key}_map")
    st.html(legend_html(edges, overlay.ramp, overlay.unit, overlay.label))
    st.caption(overlay.caption)
    return overlay


overlays: list[Overlay] = []


def render_themes(keys: list[str], show_map: bool) -> None:
    themes = [t for t in THEMES if t.key in keys]
    for start in range(0, len(themes), 2):
        for column, theme in zip(st.columns(2), themes[start : start + 2], strict=False):
            with column, st.container(border=True):
                overlay = render_theme(theme, show_map)
                if overlay is not None:
                    overlays.append(overlay)


@st.cache_data(ttl=3600)
def election_districts() -> pd.DataFrame:
    return load_election_districts()


@st.cache_data(ttl=3600)
def election_plr_shares() -> pd.DataFrame:
    return plr_party_shares(load_plr_weights(), party_shares_long(election_districts()))


def _district_tooltips(districts: pd.DataFrame, parties: list[str]) -> dict[str, str]:
    """Top parties and turnout per district."""
    tooltips = {}
    for record in districts.itertuples(index=False):
        shares = record.party_shares
        lines = [f"{party_label(p)}: {format_value(shares.get(p), 'pct')}" for p in parties[:7]]
        lines.append(f"Turnout: {format_value(record.turnout_pct, 'pct')}")
        tooltips[record.area_id] = "<br/>".join(lines)
    return tooltips


def _party_overlay(key: str, title: str, party: str, scope: str, election: str) -> Overlay:
    shares = election_plr_shares()
    values = shares.loc[(shares["scope"] == scope) & (shares["party"] == party)]
    return Overlay(
        key,
        title,
        f"{party_label(party)} ({election})",
        "pct",
        "plr",
        values[["area_id", "value"]].reset_index(drop=True),
        f"Source: {election} · Planungsraum values estimated from overlapping districts, "
        "weighted by valid votes",
        f"party_{party}" if party in PARTY_COLORS else "Greys",
    )


def render_party_map(
    districts: pd.DataFrame,
    long: pd.DataFrame,
    party: str,
    tooltips: dict[str, str],
    election: str,
    map_key: str,
) -> None:
    values = (
        long.loc[long["party"] == party, ["district_id", "share"]]
        .rename(columns={"district_id": "area_id", "share": "value"})
        .reset_index(drop=True)
    )
    ramp = f"party_{party}" if party in PARTY_COLORS else "Greys"
    edges = quantile_bins(values["value"])
    label = f"{party_label(party)} share of Zweitstimmen"
    collection = feature_collection(
        districts, values, edges, ramp, 0.8, "pct", label, tooltips=tooltips
    )
    layers = [
        choropleth_layer(collection, f"{map_key}_fill", stroked=False),
        outline_layer(bezirk_areas, f"{map_key}_bezirke"),
    ]
    st.pydeck_chart(deck(layers), height=430, key=map_key)
    st.html(legend_html(edges, ramp, "pct", label))
    st.caption(f"Source: {election} · voting district level")


TOLERANCE_CAPTION = (
    "1 / (weighted party shares / 8.7), weights AfD 0.29, BSW 0.15, CDU 0.06, Linke 0.05, "
    "FDP 0.05, SPD 0.04, Grüne 0.02, Volt 0.01 (see docs/scoring.md). Higher is more "
    "tolerant; the listing scores use the Abgeordnetenhaus 2026 result."
)


def _tolerance_overlay(scope: str, election: str) -> Overlay:
    shares = election_plr_shares()
    values = tolerance_scores(shares[shares["scope"] == scope], "area_id")
    return Overlay(
        "election_tolerance",
        "Election: tolerance score",
        f"Tolerance score ({election})",
        "score",
        "plr",
        values,
        f"Source: {election} · Planungsraum values from vote-weighted party shares of "
        "overlapping districts",
        "Greens",
    )


def render_tolerance_map(
    districts: pd.DataFrame, long: pd.DataFrame, tooltips: dict[str, str], election: str
) -> None:
    values = tolerance_scores(long, "district_id")
    edges = quantile_bins(values["value"])
    label = "Tolerance score"
    scores = values.set_index("area_id")["value"]
    combined_tooltips = {
        area: f"<b>{label}: {format_value(scores.get(area), 'score')}</b><br/>{text}"
        for area, text in tooltips.items()
    }
    collection = feature_collection(
        districts, values, edges, "Greens", 0.8, "score", label, tooltips=combined_tooltips
    )
    layers = [
        choropleth_layer(collection, "tolerance_fill", stroked=False),
        outline_layer(bezirk_areas, "tolerance_bezirke"),
    ]
    st.pydeck_chart(deck(layers), height=520, key="tolerance_map")
    st.html(legend_html(edges, "Greens", "score", label))
    st.caption(f"Source: {election} · voting district level")


def render_strongest_party(districts: pd.DataFrame, election: str) -> None:
    categories, tooltips, counts = {}, {}, {}
    for record in districts.itertuples(index=False):
        party, share, lead = strongest_party(record.party_shares)
        categories[record.area_id] = party
        if party is None:
            continue
        counts[party] = counts.get(party, 0) + 1
        tooltips[record.area_id] = (
            f"Strongest: {party_label(party)} {format_value(share, 'pct')}"
            f"<br/>Lead over runner-up: {format_value(lead, 'pp')}"
        )
    collection = category_collection(districts, categories, PARTY_COLORS, 0.8, tooltips)
    layers = [
        choropleth_layer(collection, "strongest_fill", stroked=False),
        outline_layer(bezirk_areas, "strongest_bezirke"),
    ]
    st.pydeck_chart(deck(layers), height=430, key="strongest_map")
    items = [
        (party_label(p), PARTY_COLORS.get(p, OTHER_PARTY_COLOR), counts[p])
        for p in sorted(counts, key=counts.get, reverse=True)
    ]
    st.html(category_legend_html(items, "Strongest party (districts won)"))
    st.caption(f"Source: {election} · voting district level")


def render_elections(show_map: bool) -> None:
    districts_all = election_districts()
    if districts_all.empty:
        st.info(
            "No election results yet. Run `immo load-reference elections` and "
            "`immo load-reference agh-elections`."
        )
        return
    elections = (
        districts_all[["scope", "election"]]
        .drop_duplicates()
        .sort_values("election", ascending=False)
    )
    labels = dict(zip(elections["election"], elections["scope"], strict=True))
    election = st.selectbox("Election", list(labels), key="election_choice")
    scope = labels[election]
    st.caption(
        "Zweitstimmen per postal-vote district (Briefwahlbezirk), so postal votes are "
        "included. Hover a district for the main parties and turnout."
    )
    districts = districts_all[districts_all["scope"] == scope].reset_index(drop=True)
    long = party_shares_long(districts)
    parties = list(long.groupby("party")["share"].mean().sort_values(ascending=False).index)
    tooltips = _district_tooltips(districts, parties) if show_map else {}

    top_left, top_right = st.columns(2)
    with top_left, st.container(border=True):
        st.subheader("Results by party")
        party = st.selectbox("Party", parties, format_func=party_label, key="election_party")
        if show_map:
            render_party_map(districts, long, party, tooltips, election, "party_map")
        overlays.append(
            _party_overlay("election_party", "Election: selected party", party, scope, election)
        )
    with top_right, st.container(border=True):
        st.subheader("Strongest party")
        st.caption("The party with the most Zweitstimmen in each district.")
        if show_map:
            render_strongest_party(districts, election)

    bottom_left, bottom_right = st.columns(2)
    for column, party in ((bottom_left, "afd"), (bottom_right, "gruene")):
        with column, st.container(border=True):
            st.subheader(party_label(party))
            if party not in parties:
                st.info(f"No {party_label(party)} result in this election.")
                continue
            if show_map:
                render_party_map(districts, long, party, tooltips, election, f"{party}_map")
            overlays.append(
                _party_overlay(
                    f"election_{party}", f"Election: {party_label(party)}", party, scope, election
                )
            )

    with st.container(border=True):
        st.subheader("Tolerance score")
        st.caption(TOLERANCE_CAPTION)
        if show_map:
            render_tolerance_map(districts, long, tooltips, election)
        overlays.append(_tolerance_overlay(scope, election))


ORIGIN_KINDS = {"total": "Total", "group": "Group", "diversity": "Diversity", "country": "Country"}
PROFILE_SCOPES = ["Planungsraum", "Bezirk", "Berlin"]
PROFILE_COLORS = ["#e6550d", "#9e9ac8", "#969696"]


@st.cache_data(ttl=3600)
def origin_stats() -> pd.DataFrame:
    """Stored origin counts, including the derived Non-Western group."""
    return load_origin_stats()


@st.cache_data(ttl=3600)
def origin_totals() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Weighted origin shares per Bezirk and for all of Berlin, including derived groups."""
    stats = origin_stats()
    bezirke = plr_areas.set_index("area_id")["bezirk"]
    return area_totals(stats, bezirke), area_totals(stats, pd.Series("Berlin", bezirke.index))


def _date_label(value) -> str:
    return value.strftime("%d.%m.%Y")


def _published(dates: list) -> str:
    return f"{_date_label(dates[0])} – {_date_label(dates[-1])}"


def _plr_from_click(selection: object, layer_id: str) -> str | None:
    """PLR id from a pydeck single-object selection on the layer `layer_id`."""
    if not isinstance(selection, Mapping):
        return None
    objects = selection.get("objects")
    if not isinstance(objects, Mapping):
        return None
    rows = objects.get(layer_id)
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], Mapping):
        return None
    properties = rows[0].get("properties", rows[0])
    return properties.get("area_id") if isinstance(properties, Mapping) else None


def _origin_tooltips(stats: pd.DataFrame, key: str, when, label: str, is_index: bool) -> dict:
    shares = map_values(stats, key, when, "share").set_index("area_id")["value"]
    counts = map_values(stats, key, when, "count").set_index("area_id")["value"]
    tooltips = {}
    for area in plr_areas["area_id"]:
        text = format_value(shares.get(area), "index" if is_index else "pct")
        if not is_index and pd.notna(counts.get(area)):
            text += f" ({format_value(counts.get(area), 'count')} residents)"
        tooltips[area] = f"{html.escape(label)}: {text}"
    return tooltips


def _origin_source(when) -> str:
    return (
        f"Source: Statistischer Bericht A I 16, {_date_label(when)} · Planungsraum level · "
        f"grey: no data or fewer than {MIN_RESIDENTS} residents"
    )


def _clickable_map(
    values: pd.DataFrame, ramp: str, unit: str, label: str, tooltips: dict, key: str
) -> None:
    """Choropleth whose clicked area becomes the kiez profile's Planungsraum."""
    edges = quantile_bins(values["value"])
    collection = feature_collection(
        plr_areas, values, edges, ramp, 0.75, unit, label, tooltips=tooltips
    )
    layers = [
        choropleth_layer(collection, f"{key}_fill"),
        outline_layer(bezirk_areas, f"{key}_bezirke"),
    ]
    event = st.pydeck_chart(
        deck(layers),
        height=480,
        on_select="rerun",
        selection_mode="single-object",
        key=f"{key}_map",
    )
    clicked = _plr_from_click(event.selection, f"{key}_fill")
    if clicked and clicked != st.session_state.get(f"{key}_click"):
        st.session_state[f"{key}_click"] = clicked
        st.session_state["origins_plr"] = clicked
    st.html(legend_html(edges, ramp, unit, label))


def render_diversity_map(show_map: bool) -> Overlay | None:
    st.subheader("Origin diversity")
    st.caption(
        "Gini–Simpson index: the chance that two residents with an assignable migration "
        "background come from different origins (19 listed countries, rest of EU, other). "
        "0 = everyone from one origin, higher = more mixed. Same categories on every date."
    )
    stats = origin_stats()
    if stats.empty:
        st.info("No origin data yet. Run `immo load-reference origins`.")
        return None
    dates = sorted(stats["ref_date"].unique())
    when = st.select_slider(
        "Date", options=dates, value=dates[-1], format_func=_date_label, key="diversity_date"
    )
    label = "Origin diversity, 0–100"
    values = map_values(stats, "simpson", when)
    title = f"{label}, {_date_label(when)}"
    caption = _origin_source(when)
    overlay = Overlay(
        "origins_diversity", "Origin diversity", title, "index", "plr", values, caption, "Purples"
    )
    if show_map:
        tooltips = _origin_tooltips(stats, "simpson", when, "Origin diversity", True)
        _clickable_map(values, "Purples", "index", label, tooltips, "diversity")
    st.caption(caption)
    return overlay


def render_oic_map(show_map: bool) -> Overlay | None:
    st.subheader("Origin: Islamic countries (OIC)")
    st.caption(
        "Residents with a migration background from countries in the OIC origin group. "
        "This is an origin statistic, not a count of Muslims or religious affiliation."
    )
    stats = origin_stats()
    if stats.empty:
        st.info("No origin data yet. Run `immo load-reference origins`.")
        return None
    dates = sorted(stats.loc[stats["origin_key"] == "oic", "ref_date"].dropna().unique())
    if not dates:
        st.info("No Islamic countries (OIC) origin data is available.")
        return None
    when = st.select_slider(
        "Date", options=dates, value=dates[-1], format_func=_date_label, key="oic_date"
    )
    label = "Islamic countries (OIC) (% of residents)"
    values = map_values(stats, "oic", when)
    caption = _origin_source(when)
    overlay = Overlay(
        "origins_oic",
        "Origin: Islamic countries (OIC)",
        f"{label}, {_date_label(when)}",
        "pct",
        "plr",
        values,
        caption,
        "Oranges",
    )
    if show_map:
        tooltips = _origin_tooltips(stats, "oic", when, "Islamic countries (OIC)", False)
        _clickable_map(values, "Oranges", "pct", label, tooltips, "oic")
    st.caption(caption)
    return overlay


def render_origin_map(stats: pd.DataFrame, catalog: pd.DataFrame, when, show_map: bool) -> tuple:
    """Origin picker and choropleth. Returns the chosen (key, catalog row)."""
    st.subheader("Origin map")
    st.caption("Residents by origin group or country.")
    info = catalog[catalog["kind"] != "diversity"].set_index("origin_key")

    def option_label(key: str) -> str:
        row = info.loc[key]
        text = f"{ORIGIN_KINDS[row.kind]} · {row.label}"
        return text if when in row.dates else f"{text} (not published on this date)"

    keys = list(info.index)
    key = st.selectbox(
        "Show",
        keys,
        index=keys.index("mh") if "mh" in keys else 0,
        format_func=option_label,
        key="origins_key",
    )
    row = info.loc[key]
    measure = st.radio(
        "Measure", ["Share of residents", "Count"], horizontal=True, key="origins_measure"
    )
    column = "count" if measure == "Count" else "share"
    unit = column.replace("share", "pct")
    label = row.label + (" (residents)" if column == "count" else " (% of residents)")
    caption = _origin_source(when)
    if when not in row.dates:
        st.warning(f"{row.label} is only published for {_published(row.dates)}.")
        return key, row
    values = map_values(stats, key, when, column)
    title = f"{label}, {_date_label(when)}"
    overlays.append(Overlay("origins", "Origins", title, unit, "plr", values, caption, "Oranges"))
    if show_map:
        tooltips = _origin_tooltips(stats, key, when, row.label, False)
        _clickable_map(values, "Oranges", unit, label, tooltips, "origins")
    st.caption(caption)
    return key, row


def render_kiez_profile(stats: pd.DataFrame, key: str, row: pd.Series) -> None:
    st.subheader("Kiez profile")
    names = plr_areas.sort_values(["bezirk", "name"])
    options = list(names["area_id"])
    labels = dict(zip(names["area_id"], names["name"] + " · " + names["bezirk"], strict=True))
    if st.session_state.get("origins_plr") not in options:
        st.session_state["origins_plr"] = options[0]
    plr_id = st.selectbox(
        "Planungsraum",
        options,
        format_func=labels.get,
        key="origins_plr",
        help="Or click an area on this map, or on the origin diversity map on the People tab.",
    )
    bezirk = names.set_index("area_id").at[plr_id, "bezirk"]
    bezirk_totals, berlin_totals = origin_totals()
    latest = stats["ref_date"].max()
    own = stats[(stats["plr_id"] == plr_id) & (stats["ref_date"] == latest)]
    own = own.set_index("origin_key")

    def own_value(origin: str, column: str):
        return own.at[origin, column] if origin in own.index else None

    residents = own_value("residents", "count")
    st.caption(
        f"{_date_label(latest)}: {format_value(residents, 'count')} residents · "
        f"migration background {format_value(own_value('mh', 'share'), 'pct')} · "
        f"diversity {format_value(own_value('simpson', 'share'), 'index')}"
    )
    if residents is None or pd.isna(residents) or residents < MIN_RESIDENTS:
        st.info(f"Fewer than {MIN_RESIDENTS} residents: no origin profile.")
        return

    bars = profile_countries(stats, plr_id, bezirk_totals, berlin_totals, bezirk, latest)
    order = list(bars.loc[bars["scope"] == "Planungsraum", "label"])
    colors = alt.Scale(domain=PROFILE_SCOPES, range=PROFILE_COLORS)
    legend = None
    st.html(
        "<div style='font-size:0.85rem'>"
        + "".join(
            f"<span style='display:inline-flex;align-items:center;margin-right:12px'>"
            f"<span style='width:12px;height:12px;background:{color};margin-right:4px;"
            f"display:inline-block'></span>{scope}</span>"
            for scope, color in zip(PROFILE_SCOPES, PROFILE_COLORS, strict=True)
        )
        + "</div>"
    )
    bar_column, origin_column, diversity_column = st.columns(3)
    with bar_column:
        st.markdown(f"**Top countries of origin** · {_date_label(latest)}")
        st.altair_chart(
            alt.Chart(bars)
            .mark_bar()
            .encode(
                y=alt.Y("label:N", sort=order, title=None),
                x=alt.X("share:Q", title="% of residents"),
                yOffset=alt.YOffset("scope:N", sort=PROFILE_SCOPES),
                color=alt.Color("scope:N", scale=colors, legend=legend),
                tooltip=[
                    alt.Tooltip("label:N", title="Origin"),
                    alt.Tooltip("scope:N", title="Area"),
                    alt.Tooltip("share:Q", title="% of residents", format=".1f"),
                ],
            )
            .properties(height=alt.Step(7)),
            width="stretch",
        )

    def trend(origin: str, title: str, axis_title: str) -> None:
        series = time_series(stats, origin, plr_id, bezirk_totals, berlin_totals, bezirk)
        series["ref_date"] = pd.to_datetime(series["ref_date"])
        st.markdown(f"**{title} over time**")
        st.altair_chart(
            alt.Chart(series)
            .mark_line(point=True)
            .encode(
                x=alt.X("ref_date:T", title=None, axis=alt.Axis(format="%m/%Y")),
                y=alt.Y("share:Q", title=axis_title, scale=alt.Scale(zero=False)),
                color=alt.Color("scope:N", scale=colors, legend=legend),
                tooltip=[
                    alt.Tooltip("ref_date:T", title="Date", format="%d.%m.%Y"),
                    alt.Tooltip("scope:N", title="Area"),
                    alt.Tooltip("share:Q", title=axis_title, format=".1f"),
                ],
            )
            .properties(height=260),
            width="stretch",
        )

    with origin_column:
        trend(key, row.label, "% of residents")
        if len(row.dates) < stats["ref_date"].nunique():
            st.caption(f"{row.label} is only published for {_published(row.dates)}.")
    with diversity_column:
        trend("simpson", "Origin diversity", "Diversity (0–100)")
    st.caption("Bezirk and Berlin values are weighted by residents.")


def render_origins(show_map: bool) -> None:
    stats = origin_stats()
    if stats.empty:
        st.info("No origin data yet. Run `immo load-reference origins`.")
        return
    st.caption(
        "Residents with a migration background by origin, per Planungsraum, half-yearly since "
        "2021. Shares are of all residents. Groups overlap with the countries in them (the EU "
        "includes Poland; Islamic and Arab countries both include Syria). The report changed "
        "its country list on 31.12.2025: groups other than the EU, Bosnia and Serbia stop "
        "there; Afghanistan, Iraq, China and India start. Non-Western is migration background "
        "outside the EU, former Yugoslavia, the UK and the US, through 30.06.2025. Croatia "
        "is in both the EU and former Yugoslavia, so it is subtracted once. Slovenia is too, "
        "but is not published on its own, so it is subtracted twice. Residents with no "
        "assignable origin stay in the remainder."
    )
    catalog = origin_catalog(stats)
    dates = sorted(stats["ref_date"].unique())
    when = st.select_slider(
        "Date", options=dates, value=dates[-1], format_func=_date_label, key="origins_date"
    )
    with st.container(border=True):
        key, row = render_origin_map(stats, catalog, when, show_map)
    with st.container(border=True):
        render_kiez_profile(stats, key, row)


def _append_theme(key: str, show_map: bool) -> None:
    theme = next(item for item in THEMES if item.key == key)
    overlay = render_theme(theme, show_map)
    if overlay is not None:
        overlays.append(overlay)


def render_people(show_map: bool) -> None:
    left, right = st.columns(2)
    with left, st.container(border=True):
        _append_theme("migration", show_map)
    with right, st.container(border=True):
        diversity = render_diversity_map(show_map)
    render_themes(["citizenship", "age"], show_map)
    left, right = st.columns(2)
    with left, st.container(border=True):
        _append_theme("religion", show_map)
    with right, st.container(border=True):
        oic = render_oic_map(show_map)
    # After the other people maps, so the combined map still defaults to the first two of those.
    if oic is not None:
        overlays.append(oic)
    if diversity is not None:
        overlays.append(diversity)


CRIME_ATLAS_URL = "https://www.berlin.de/polizei/service/kriminalitaetsatlas/"
CRIME_INTRO = (
    "Recorded offences from the Berlin crime atlas (Polizei Berlin, police crime statistics) "
    "for each Bezirksregion, 2016–2025. The frequency is cases per 100,000 residents "
    "registered on 30 June of that year. Cases that could not be located are left off the "
    "map; they stay in the Bezirk and Berlin totals. A high frequency in a busy area often "
    "counts visitors and shops, not only the people who live there."
)
CRIME_NEIGHBOURHOOD = (
    "Neighbourhood offences sum robbery, assault, threats, coercion, deprivation of liberty, "
    "mistreatment of children, burglary of homes and cellars, damage to cars and attacks on "
    "officers. Shoplifting and fare evasion are left out, so the figure follows the area "
    "people live in more than its visitors."
)
CRIME_SCOPES = ["Bezirksregion", "Bezirk", "Berlin"]
CRIME_COLORS = ["#e6550d", "#9e9ac8", "#969696"]


@st.cache_data(ttl=3600)
def crime_frame() -> pd.DataFrame:
    return load_crime()


def _crime_values(stats: pd.DataFrame, offense: str, year: int, column: str) -> pd.DataFrame:
    mask = (stats["level"] == "bzr") & (stats["offense"] == offense) & (stats["year"] == year)
    return stats.loc[mask, ["area_id", column]].rename(columns={column: "value"}).reset_index(
        drop=True
    )


def _crime_tooltips(stats: pd.DataFrame, offense: str, year: int, label: str, column: str) -> dict:
    mask = (stats["level"] == "bzr") & (stats["offense"] == offense) & (stats["year"] == year)
    unit = "count" if column == "cases" else "rate"
    tooltips = {}
    for record in stats.loc[mask].itertuples(index=False):
        value = getattr(record, column)
        text = format_value(value, unit)
        if column == "rate":
            text += " per 100,000"
            if pd.notna(record.cases):
                text += f" ({format_value(record.cases, 'count')} cases)"
        elif pd.notna(record.rate):
            text += f" cases ({format_value(record.rate, 'rate')} per 100,000)"
        tooltips[record.area_id] = f"{html.escape(label)}: {text}"
    return tooltips


def render_crime(show_map: bool) -> None:
    stats = crime_frame()
    if stats.empty:
        st.info("No crime data yet. Run `immo load-reference crime`.")
        return
    st.caption(CRIME_INTRO)
    st.caption(f"Source: [Berlin crime atlas]({CRIME_ATLAS_URL}), Polizei Berlin.")
    labels = {key: label for key, label, _header in OFFENSES}
    available = [key for key in labels if key in set(stats["offense"])]
    years = sorted(int(year) for year in stats["year"].unique())
    picker, year_picker = st.columns([2, 1])
    offense = picker.selectbox(
        "Offence",
        available,
        index=available.index("neighbourhood") if "neighbourhood" in available else 0,
        format_func=labels.get,
        key="crime_offense",
    )
    year = int(year_picker.select_slider("Year", options=years, value=years[-1], key="crime_year"))
    metric = st.radio(
        "Show", ["Per 100,000 residents", "Cases"], horizontal=True, key="crime_metric"
    )
    column = "cases" if metric == "Cases" else "rate"
    unit = "count" if column == "cases" else "rate"
    label = labels[offense]
    map_label = f"{label}, {year}" if column == "cases" else f"{label} per 100,000, {year}"
    if offense == "neighbourhood":
        st.caption(CRIME_NEIGHBOURHOOD)
    values = _crime_values(stats, offense, year, column)
    caption = (
        f"Source: Polizei Berlin, Kriminalitätsatlas · {year} · Bezirksregion level"
        + ("" if column == "cases" else " · cases per 100,000 residents")
    )
    overlays.append(
        Overlay("crime", "Crime", map_label, unit, "bzr", values, caption, "Reds")
    )
    if not show_map:
        return

    with st.container(border=True):
        edges = quantile_bins(values["value"])
        tooltips = _crime_tooltips(stats, offense, year, label, column)
        bzr_areas = areas("bzr")
        chosen = st.session_state.get("crime_bzr")
        known = set(bzr_areas["area_id"])
        focus_id = chosen if isinstance(chosen, str) and chosen in known else None
        layers = [
            choropleth_layer(
                feature_collection(
                    bzr_areas, values, edges, "Reds", 0.8, unit, map_label, tooltips=tooltips
                ),
                "crime_fill",
                stroked=True,
            ),
            outline_layer(bezirk_areas, "crime_bezirke"),
        ]
        outline = focus_layer(bzr_areas, focus_id, "crime_focus")
        if outline is not None:
            layers.append(outline)
        event = st.pydeck_chart(
            deck(layers),
            height=520,
            on_select="rerun",
            selection_mode="single-object",
            key="crime_map",
        )
        clicked = _plr_from_click(event.selection, "crime_fill")
        if clicked and clicked != st.session_state.get("crime_click"):
            st.session_state["crime_click"] = clicked
            st.session_state["crime_bzr"] = clicked
        st.html(legend_html(edges, "Reds", unit, map_label))
        st.caption(caption)

    with st.container(border=True):
        st.subheader("Over time")
        names = bzr_areas.sort_values(["bezirk", "name"])
        options = list(names["area_id"])
        region_labels = dict(
            zip(names["area_id"], names["name"] + " · " + names["bezirk"], strict=True)
        )
        if st.session_state.get("crime_bzr") not in options:
            st.session_state["crime_bzr"] = options[0]
        area_id = st.selectbox(
            "Bezirksregion",
            options,
            format_func=region_labels.get,
            key="crime_bzr",
            help="Or click a region on the map.",
        )
        bezirk = names.set_index("area_id").at[area_id, "bezirk"]
        trend = crime_trend(stats, offense, area_id, bezirk)
        trend["value"] = trend[column]
        colors = alt.Scale(domain=CRIME_SCOPES, range=CRIME_COLORS)
        axis = "Cases" if column == "cases" else "Cases per 100,000 residents"
        st.altair_chart(
            alt.Chart(trend)
            .mark_line(point=True)
            .encode(
                x=alt.X("year:O", title=None),
                y=alt.Y("value:Q", title=axis, scale=alt.Scale(zero=False)),
                color=alt.Color("scope:N", scale=colors, title=None, sort=CRIME_SCOPES),
                tooltip=[
                    alt.Tooltip("year:O", title="Year"),
                    alt.Tooltip("scope:N", title="Area"),
                    alt.Tooltip("value:Q", title=axis, format=",.0f"),
                ],
            )
            .properties(height=280),
            width="stretch",
        )
        st.caption("Bezirk and Berlin include cases that could not be placed in a region.")


REGISTER_URL = "https://berliner-register.de/vorfalls-chronik/"
INCIDENT_MAP_MODES = ["Rate per Planungsraum", "Points", "Heatmap"]
INCIDENT_PER_AREA = "Per area"
INCIDENT_PER_RESIDENTS = "Per 1,000 residents"
INCIDENT_PER_MH = "Per 1,000 with migration background"
INCIDENT_PER_FOREIGN = "Per 1,000 non-German citizens"
INCIDENT_METRICS = [
    INCIDENT_PER_AREA,
    INCIDENT_PER_RESIDENTS,
    INCIDENT_PER_MH,
    INCIDENT_PER_FOREIGN,
]
INCIDENT_UNIT = "per_1k"
PRECISION_COLORS = {"street": [203, 24, 29, 200], "station": [203, 24, 29, 200]}
PLACE_COLOR = [253, 141, 60, 170]


@st.cache_data(ttl=3600)
def incident_span():
    return incident_date_range()


@st.cache_data(ttl=3600)
def incidents_between(start, end) -> pd.DataFrame:
    return load_incidents(start, end)


@st.cache_data(ttl=3600)
def migration_background() -> pd.DataFrame:
    return load_migration_background()


@st.cache_data(ttl=3600)
def foreign_citizens() -> pd.DataFrame:
    return load_foreign_citizens()


def _incident_line(record) -> str:
    return f"{_date_label(record.incident_date)} {html.escape(str(record.title))}"


def _group_caption(
    frame: pd.DataFrame, who: str, source: str | None, empty: str, minimum: int = MIN_POPULATION
) -> str:
    """Caption fragment for a rate whose denominator is one group of residents."""
    if frame.empty:
        return f" · {empty}"
    when = frame["ref_date"].iloc[0]
    if hasattr(when, "strftime"):
        when = _date_label(when)
    named = source
    if named is None and "source" in frame.columns:
        named = str(frame["source"].iloc[0])
    note = f" · {who} on {when}"
    if named:
        note += f" ({named})"
    return note + f" · grey: fewer than {minimum} {who}"


def _incident_shown(rates: pd.DataFrame, metric: str) -> pd.DataFrame:
    """area_id, count and the value drawn for the chosen measure."""
    shown = rates[["area_id", "count"]].copy()
    column = {
        INCIDENT_PER_AREA: None,
        INCIDENT_PER_MH: "value_mh",
        INCIDENT_PER_FOREIGN: "value_foreign",
    }.get(metric, "value")
    if column is None:
        shown["value"] = shown["count"].astype(float)
    else:
        shown["value"] = rates[column].to_numpy()
    return shown


def _incident_tooltips(
    incidents: pd.DataFrame,
    shown: pd.DataFrame,
    label: str,
    unit: str,
    people: pd.Series | None = None,
    people_label: str = "",
) -> dict:
    """Chosen measure, count and the three newest titles per Planungsraum."""
    placed = incidents[incidents["geo_precision"].isin(RATE_PRECISIONS)]
    newest = {
        plr: "<br/>".join(_incident_line(r) for r in group.head(3).itertuples(index=False))
        for plr, group in placed.groupby("plr_id")
    }
    tooltips = {}
    for record in shown.itertuples(index=False):
        text = f"{html.escape(label)}: {format_value(record.value, unit)}"
        if unit != "count":
            text += f"<br/>{record.count} incidents"
        if people is not None:
            count = people.get(record.area_id)
            if pd.notna(count):
                text += f"<br/>{format_value(count, 'count')} {html.escape(people_label)}"
        if record.area_id in newest:
            text += f"<br/>{newest[record.area_id]}"
        tooltips[record.area_id] = text
    return tooltips


def _incident_edges(values: pd.Series) -> list[float]:
    """Quantile bins of the non-zero rates, so areas without incidents stay the lightest."""
    positive = values[values > 0]
    if positive.empty:
        return []
    return [0.0, *quantile_bins(positive, classes=4)]


def _incident_points(incidents: pd.DataFrame) -> pd.DataFrame:
    points = incidents.dropna(subset=["lat", "lon"]).copy()
    points["name"] = points["title"].map(lambda t: html.escape(str(t)))
    points["bezirk"] = points["bezirk"].map(lambda b: html.escape(str(b or "")))
    points["tooltip"] = [
        f"{_date_label(r.incident_date)} · located by {r.geo_precision}"
        f"<br/>Quelle: {html.escape(str(r.source or 'unknown'))}"
        for r in points.itertuples(index=False)
    ]
    points["color"] = points["geo_precision"].map(lambda p: PRECISION_COLORS.get(p, PLACE_COLOR))
    return points[["lon", "lat", "name", "bezirk", "tooltip", "color"]]


def render_incidents(show_map: bool) -> None:
    st.subheader("Berliner Register incidents")
    st.caption(
        f"Discriminatory and far-right incidents reported to the [Berliner Register]"
        f"({REGISTER_URL}): propaganda, insults, threats and attacks. The chronicle has no "
        "coordinates, so each incident is placed at the first street or station in its text "
        "that lies in the reported Bezirk, else at the place named in its title."
    )
    span = incident_span()
    if span is None:
        st.info(
            "No incidents yet. Run `immo load-reference berliner-register`, then "
            "`immo geocode-incidents`."
        )
        return
    first, last = span
    start, end = st.slider(
        "Period",
        min_value=first,
        max_value=last,
        value=(max(first, last - timedelta(days=364)), last),
        format="DD.MM.YYYY",
        key="incidents_period",
    )
    incidents = incidents_between(start, end)
    background = migration_background()
    foreigners = foreign_citizens()
    mh_population = (
        background.set_index("area_id")["mh_population"] if not background.empty else None
    )
    foreign_population = (
        foreigners.set_index("area_id")["foreign_population"] if not foreigners.empty else None
    )
    rates = incident_rates(
        incidents,
        plr_areas,
        window_years(start, end),
        mh_population=mh_population,
        foreign_population=foreign_population,
    )
    metric = st.radio(
        "Show",
        INCIDENT_METRICS,
        index=INCIDENT_METRICS.index(INCIDENT_PER_RESIDENTS),
        horizontal=True,
        key="incidents_metric",
    )
    shown = _incident_shown(rates, metric)
    unit = "count" if metric == INCIDENT_PER_AREA else INCIDENT_UNIT
    labels = {
        INCIDENT_PER_AREA: "Incidents",
        INCIDENT_PER_MH: "Incidents per 1,000 residents with a migration background per year",
        INCIDENT_PER_FOREIGN: (
            "Incidents per 1,000 residents who are not German citizens per year"
        ),
    }
    label = labels.get(metric, "Incidents per 1,000 residents per year")
    period = f"{_date_label(start)} – {_date_label(end)}"
    caption = (
        f"Source: Berliner Register, {period} · Planungsraum level, incidents placed at a "
        "street or station"
    )
    if metric == INCIDENT_PER_RESIDENTS:
        caption += f" · grey: fewer than {MIN_POPULATION} residents"
    elif metric == INCIDENT_PER_MH:
        caption += _group_caption(
            background,
            "residents with a migration background",
            "A I 16",
            "no migration-background counts loaded",
        )
    elif metric == INCIDENT_PER_FOREIGN:
        caption += _group_caption(
            foreigners,
            "residents who are not German citizens",
            None,
            "no foreign-citizen shares loaded",
            MIN_FOREIGN_POPULATION,
        )
    overlays.append(
        Overlay(
            "incidents",
            "Berliner Register incidents",
            f"{label}, {period}",
            unit,
            "plr",
            shown[["area_id", "value"]],
            caption,
            "Reds",
        )
    )
    if metric == INCIDENT_PER_MH and background.empty:
        st.info("No migration-background counts yet. Run `immo load-reference origins`.")
    elif metric == INCIDENT_PER_FOREIGN and foreigners.empty:
        st.info("No foreign-citizen shares yet. Run `immo load-reference mss`.")
    mode = st.radio("Map", INCIDENT_MAP_MODES, horizontal=True, key="incidents_mode")
    if show_map:
        if mode == INCIDENT_MAP_MODES[0]:
            edges = _incident_edges(shown["value"])
            collection = feature_collection(
                plr_areas,
                shown[["area_id", "value"]],
                edges,
                "Reds",
                0.75,
                unit,
                label,
                tooltips=_incident_tooltips(
                    incidents,
                    shown,
                    label,
                    unit,
                    people={
                        INCIDENT_PER_MH: mh_population,
                        INCIDENT_PER_FOREIGN: foreign_population,
                    }.get(metric),
                    people_label={
                        INCIDENT_PER_MH: "residents with a migration background",
                        INCIDENT_PER_FOREIGN: "residents who are not German citizens",
                    }.get(metric, ""),
                ),
            )
            layers = [
                choropleth_layer(collection, "incidents_fill"),
                outline_layer(bezirk_areas, "incidents_bezirke"),
            ]
        else:
            points = _incident_points(incidents)
            layer = (
                scatter_layer(points, "incidents_points")
                if mode == "Points"
                else heatmap_layer(points, "incidents_heat")
            )
            layers = [outline_layer(bezirk_areas, "incidents_bezirke"), layer]
        st.pydeck_chart(deck(layers), height=560, key="incidents_map")
        if mode == INCIDENT_MAP_MODES[0]:
            st.html(legend_html(edges, "Reds", unit, label))
        elif mode == "Points":
            st.caption(
                "Red: placed at a street or station. Orange: placed at an Ortsteil or Kiez "
                "named in the title."
            )
    st.caption(caption)
    counts = precision_counts(incidents)
    placed = sum(counts.get(p, 0) for p in RATE_PRECISIONS)
    st.caption(
        f"{len(incidents):,} incidents in this period: {placed:,} at a street or station, "
        f"{counts.get('place', 0):,} at an Ortsteil or Kiez name, "
        f"{counts.get('bezirk', 0):,} only at Bezirk level, "
        f"{counts.get('berlin', 0):,} Berlin-wide, "
        f"{counts.get('not geocoded', 0):,} not geocoded yet. "
        "The register counts what is reported, so busy local register offices show more."
    )
    rest = unplaced_counts(incidents)
    if not rest.empty:
        with st.expander("Incidents not in the rates, per Bezirk"):
            st.dataframe(
                rest.rename("Incidents").rename_axis("Bezirk").reset_index(),
                hide_index=True,
            )


COVERAGE_EDGES = [0, 10, 25, 50, 75, 100]
GEBIET_LINE = [180, 80, 0, 220]


@st.cache_data(ttl=3600)
def milieuschutz_coverage() -> pd.DataFrame:
    return load_coverage()


@st.cache_data(ttl=3600)
def milieuschutz_gebiete() -> pd.DataFrame:
    return load_gebiete()


def _milieuschutz_tooltips(coverage: pd.DataFrame) -> dict[str, str]:
    tooltips = {}
    for record in coverage.itertuples(index=False):
        line = f"Milieuschutz: {format_value(record.value, 'pct')} of the area"
        names = record.gebiete
        if isinstance(names, str) and names.strip():
            line += f"<br/>{html.escape(names)}"
        elif not record.value:
            line += "<br/>Not inside a Milieuschutzgebiet"
        tooltips[record.area_id] = line
    return tooltips


def render_milieuschutz(show_map: bool) -> None:
    st.subheader("Milieuschutz")
    st.caption(
        "Share of each Planungsraum that lies inside a Milieuschutzgebiet "
        "(§ 172 Abs. 1 Nr. 2 BauGB). Orange lines are the Gebiet boundaries."
    )
    coverage = milieuschutz_coverage()
    gebiete = milieuschutz_gebiete()
    if coverage.empty:
        st.info("No Milieuschutzgebiete loaded. Run `immo load-reference milieuschutz-areas`.")
        return
    affected = coverage[coverage["value"] > 0]
    land = coverage["plr_area_ha"].sum()
    share = coverage["area_ha"].sum() / land * 100 if land else 0
    figures = st.columns(3)
    figures[0].metric("Planungsräume affected", f"{len(affected)} of {len(coverage)}")
    figures[1].metric("Berlin land area inside", f"{share:.1f} %")
    figures[2].metric("Milieuschutzgebiete", f"{len(gebiete)}")
    values = coverage[["area_id", "value"]]
    overlays.append(
        Overlay(
            "milieuschutz",
            "Milieuschutz",
            "Milieuschutz coverage",
            "pct",
            "plr",
            values,
            "Source: Geoportal Berlin, Erhaltungsverordnungsgebiete § 172 Abs. 1 Nr. 2 BauGB "
            "· Planungsraum level",
            "Oranges",
        )
    )
    if show_map:
        layers = [
            choropleth_layer(
                feature_collection(
                    plr_areas,
                    values,
                    COVERAGE_EDGES,
                    "Oranges",
                    0.75,
                    "pct",
                    "Milieuschutz coverage",
                    tooltips=_milieuschutz_tooltips(coverage),
                ),
                "milieuschutz_fill",
            ),
            outline_layer(gebiete, "milieuschutz_gebiete", color=GEBIET_LINE, width=1.6),
            outline_layer(bezirk_areas, "milieuschutz_bezirke"),
        ]
        focus = focus_layer(plr_areas, focus_plr, "milieuschutz_focus")
        if focus is not None:
            layers.append(focus)
        st.pydeck_chart(
            deck(layers, view_state=focus_view(plr_areas, focus_plr)),
            height=560,
            key="milieuschutz_map",
        )
        st.html(legend_html(COVERAGE_EDGES, "Oranges", "pct", "Share of the Planungsraum"))
    st.caption(
        "Source: Geoportal Berlin, Erhaltungsverordnungsgebiete § 172 Abs. 1 Nr. 2 BauGB "
        "· area share per Planungsraum"
    )
    only = st.checkbox("Only affected", value=True, key="milieuschutz_affected")
    shown = affected if only else coverage
    table = shown[["name", "bezirk", "value", "area_ha", "population", "gebiete"]].sort_values(
        ["value", "name"], ascending=[False, True]
    )
    st.dataframe(
        table,
        hide_index=True,
        use_container_width=True,
        column_config={
            "name": st.column_config.TextColumn("Planungsraum"),
            "bezirk": st.column_config.TextColumn("Bezirk"),
            "value": st.column_config.NumberColumn("Coverage %", format="%.1f"),
            "area_ha": st.column_config.NumberColumn("Hectares inside", format="%.1f"),
            "population": st.column_config.NumberColumn("Residents", format="%d"),
            "gebiete": st.column_config.TextColumn("Gebiete"),
        },
    )
    listing = gebiete.drop(columns="geometry", errors="ignore")
    with st.expander(f"The {len(gebiete)} Gebiete"):
        st.dataframe(
            listing[["name", "bezirk", "in_force", "area_ha"]].rename(
                columns={
                    "name": "Gebiet",
                    "bezirk": "Bezirk",
                    "in_force": "In force",
                    "area_ha": "Hectares",
                }
            ),
            hide_index=True,
            use_container_width=True,
        )


@st.cache_data(ttl=600)
def asking_prices() -> pd.DataFrame:
    return load_average_price_per_m2()


def _price_tooltips(prices: pd.DataFrame) -> dict[str, str]:
    tooltips = {}
    for record in prices.itertuples(index=False):
        count = int(record.listings)
        noun = "listing" if count == 1 else "listings"
        tooltips[record.area_id] = (
            f"Average price: {format_value(record.value, 'eur')}/m²<br/>{count} {noun}"
        )
    return tooltips


def render_prices(show_map: bool) -> None:
    st.subheader("Average price per m²")
    st.caption(
        "Mean asking price per m² of active listings in each Planungsraum. "
        "Each listing counts once, as its price divided by its living area. "
        "ThinkImmo is excluded, and so is any listing whose title contains the word Rohling."
    )
    prices = asking_prices()
    if prices.empty:
        st.info("No priced listings yet. Run `immo crawl` and `immo enrich` first.")
        return
    berlin = overall_mean(prices)
    figures = st.columns(3)
    figures[0].metric("Berlin average", format_value(berlin, "eur") + "/m²")
    figures[1].metric("Listings in the average", f"{int(prices['listings'].sum()):,}")
    figures[2].metric(
        "Planungsräume with a price",
        f"{len(prices)} of {len(plr_areas)}",
    )
    values = prices[["area_id", "value"]]
    overlays.append(
        Overlay(
            "asking_price",
            "Prices",
            "Average price per m²",
            "eur",
            "plr",
            values,
            "Active listings, excluding ThinkImmo and titles that contain the word Rohling "
            "· Planungsraum level",
            "Reds",
        )
    )
    if show_map:
        edges = quantile_bins(values["value"])
        layers = [
            choropleth_layer(
                feature_collection(
                    plr_areas,
                    values,
                    edges,
                    "Reds",
                    0.75,
                    "eur",
                    "Average price per m²",
                    tooltips=_price_tooltips(prices),
                ),
                "prices_fill",
            ),
            outline_layer(bezirk_areas, "prices_bezirke"),
        ]
        focus = focus_layer(plr_areas, focus_plr, "prices_focus")
        if focus is not None:
            layers.append(focus)
        st.pydeck_chart(
            deck(layers, view_state=focus_view(plr_areas, focus_plr)),
            height=560,
            key="prices_map",
        )
        st.html(legend_html(edges, "Reds", "eur", "Average price per m²"))
    st.caption(
        "Grey areas have no qualifying listing. A Planungsraum with one listing shows that "
        "listing's price per m²."
    )
    named = prices.drop(columns=["bezirk"], errors="ignore").merge(
        plr_areas[["area_id", "name", "bezirk"]],
        on="area_id",
        how="left",
    )
    table = named[["name", "bezirk", "value", "listings"]].sort_values(
        ["value", "name"], ascending=[False, True]
    )
    st.dataframe(
        table,
        hide_index=True,
        use_container_width=True,
        column_config={
            "name": st.column_config.TextColumn("Planungsraum"),
            "bezirk": st.column_config.TextColumn("Bezirk"),
            "value": st.column_config.NumberColumn("€/m²", format="%.0f"),
            "listings": st.column_config.NumberColumn("Listings", format="%d"),
        },
    )


@st.cache_data(ttl=3600)
def qualification_estimates() -> pd.DataFrame:
    """Planungsraum qualification shares, blended with local turnout."""
    return load_planungsraum_education(election_plr_shares())


def _qualification_tooltips(frame: pd.DataFrame) -> dict[str, str]:
    tooltips = {}
    for record in frame.itertuples(index=False):
        tooltips[record.area_id] = (
            f"{html.escape(record.label)}: {format_value(record.pct, 'pct')} of adults"
            f"<br/>{format_value(record.covered_vote_pct, 'pct')} of AGH votes have a profile"
        )
    return tooltips


def render_qualifications(show_map: bool) -> None:
    st.subheader("Qualifications")
    st.caption(
        "Estimated share of residents aged 18 and over in each Planungsraum, by "
        "school-leaving qualification. The local Abgeordnetenhaus vote mix is applied "
        "to the party profiles, then blended with the Nichtwähler profile by the local "
        "turnout. Parties without a profile are left out. This is not a census."
    )
    estimates = qualification_estimates()
    if estimates.empty:
        st.info("No Abgeordnetenhaus results or party education profiles are loaded.")
        return
    labels = {level: label for level, _, label in LEVELS}
    choice = st.selectbox(
        "Qualification",
        [level for level, _, _ in LEVELS],
        format_func=labels.get,
        key="qualification_level",
    )
    selected = estimates.loc[estimates["level"] == choice].copy()
    adults = selected["adults"].sum(min_count=1)
    people = selected["people"].sum(min_count=1)
    figures = st.columns(2)
    figures[0].metric("Estimated people in Berlin", format_value(people, "count"))
    share = people / adults * 100 if adults else None
    figures[1].metric("Share of adults", format_value(share, "pct"))
    values = selected[["area_id", "pct"]].rename(columns={"pct": "value"})
    overlays.append(
        Overlay(
            "qualification",
            "Qualifications",
            labels[choice],
            "pct",
            "plr",
            values,
            "Estimated from national party education profiles and the local Abgeordnetenhaus "
            "vote mix, for residents aged 18 and over. Not a census. Planungsraum level.",
            "Blues",
        )
    )
    if show_map:
        edges = quantile_bins(values["value"])
        layers = [
            choropleth_layer(
                feature_collection(
                    plr_areas,
                    values,
                    edges,
                    "Blues",
                    0.75,
                    "pct",
                    labels[choice],
                    tooltips=_qualification_tooltips(selected),
                ),
                "qualification_fill",
            ),
            outline_layer(bezirk_areas, "qualification_bezirke"),
        ]
        focus = focus_layer(plr_areas, focus_plr, "qualification_focus")
        if focus is not None:
            layers.append(focus)
        st.pydeck_chart(
            deck(layers, view_state=focus_view(plr_areas, focus_plr)),
            height=560,
            key="qualification_map",
        )
        st.html(legend_html(edges, "Blues", "pct", "% of adults"))
    table = selected[["name", "bezirk", "people", "pct", "covered_vote_pct"]].sort_values(
        ["people", "name"], ascending=[False, True], na_position="last"
    )
    table["pct"] = table["pct"].round(1)
    table["covered_vote_pct"] = table["covered_vote_pct"].round(0)
    st.dataframe(
        table,
        hide_index=True,
        use_container_width=True,
        column_config={
            "name": st.column_config.TextColumn("Planungsraum"),
            "bezirk": st.column_config.TextColumn("Bezirk"),
            "people": st.column_config.NumberColumn("People", format="%d"),
            "pct": st.column_config.NumberColumn("% of adults", format="%.1f"),
            "covered_vote_pct": st.column_config.NumberColumn(
                "AGH votes with a profile", format="%.0f%%"
            ),
        },
    )


# Every map holds two WebGL contexts and browsers drop the oldest beyond 16 per page, so
# only the open tab draws its maps. Controls render in all tabs to keep the overlays.
(
    people_tab,
    origins_tab,
    work_tab,
    elections_tab,
    crime_tab,
    incidents_tab,
    milieuschutz_tab,
    qualifications_tab,
    prices_tab,
) = st.tabs(
    [
        "People",
        "Origins",
        "Work and education",
        "Elections",
        "Crime",
        "Incidents",
        "Milieuschutz",
        "Qualifications",
        "Prices",
    ],
    on_change="rerun",
    key="map_tab",
)
with people_tab:
    render_people(bool(people_tab.open))
with origins_tab:
    render_origins(bool(origins_tab.open))
with work_tab:
    render_themes(
        ["unemployment", "income", "education", "school_load"], bool(work_tab.open)
    )
with elections_tab:
    render_elections(bool(elections_tab.open))
with crime_tab:
    render_crime(bool(crime_tab.open))
with incidents_tab:
    render_incidents(bool(incidents_tab.open))
with milieuschutz_tab:
    render_milieuschutz(bool(milieuschutz_tab.open))
with qualifications_tab:
    render_qualifications(bool(qualifications_tab.open))
with prices_tab:
    render_prices(bool(prices_tab.open))


def _on_plr(overlay: Overlay) -> pd.DataFrame:
    """Overlay values on PLR polygons; coarser values are copied onto their PLRs."""
    return to_plr(overlay.values.assign(level=overlay.level), plr_areas)[
        ["area_id", "value", "coarse"]
    ]


def _overlay_controls(overlay: Overlay, index: int, mode: str) -> dict:
    values = overlay.values["value"].dropna()
    low, high = float(values.min()), float(values.max())
    if low == high:
        high = low + 1
    key = f"combined_{overlay.key}_{overlay.label}"
    level_note = {"bzr": " · Bezirksregion level", "bezirk": " · Bezirk level"}.get(
        overlay.level, ""
    )
    st.markdown(f"**{html.escape(overlay.label)}**" + level_note)
    settings: dict = {}
    if mode == "Stacked layers":
        left, right = st.columns(2)
        ramp_options = [
            r for r in RAMPS if r != "diverging" and not r.startswith(("party_", "diverging"))
        ]
        ramp_options = [r for r in ramp_options if not r.endswith("_r")]
        default_ramp = LAYER_RAMPS[index % len(LAYER_RAMPS)]
        ramp = left.selectbox(
            "Colour", ramp_options, index=ramp_options.index(default_ramp), key=f"{key}_ramp"
        )
        settings["opacity"] = right.slider("Opacity", 0.1, 1.0, 0.55, 0.05, key=f"{key}_opacity")
        invert = st.toggle(
            "Invert colours (higher values lighter)",
            key=f"{key}_invert",
            help="Off: the higher the value, the darker the area. On: the lower, the darker.",
        )
        settings["ramp"] = reversed_ramp(ramp, invert)
        settings["filter"] = st.checkbox("Only show areas past a threshold", key=f"{key}_filter")
    else:
        settings["filter"] = True
    settings["low"] = settings["high"] = None
    if settings["filter"]:
        direction, threshold = st.columns([1, 2])
        how = direction.radio(
            "Keep areas", ["at or above", "at or below", "in between"], key=f"{key}_dir"
        )
        step = (high - low) / 100
        if how == "in between":
            settings["low"], settings["high"] = threshold.slider(
                "Range",
                low,
                high,
                (float(values.quantile(0.25)), float(values.quantile(0.75))),
                step,
                key=f"{key}_range",
                format="%.1f",
            )
        else:
            value = threshold.slider(
                "Threshold",
                low,
                high,
                float(values.median()),
                step,
                key=f"{key}_threshold",
                format="%.1f",
            )
            settings["low" if how == "at or above" else "high"] = value
    return settings


def _combined_tooltips(selected: list[Overlay], plr_values: list[pd.DataFrame]) -> dict:
    lines: dict[str, list[str]] = {}
    for overlay, values in zip(selected, plr_values, strict=True):
        for record in values.itertuples(index=False):
            text = format_value(record.value, overlay.unit)
            if record.coarse and pd.notna(record.value):
                note = "Bezirksregion value" if overlay.level == "bzr" else "Bezirk value"
                text += f" ({note})"
            lines.setdefault(record.area_id, []).append(f"{html.escape(overlay.label)}: {text}")
    return {area: "<br/>".join(parts) for area, parts in lines.items()}


def render_combined() -> None:
    st.subheader("Combined map")
    if not overlays:
        st.session_state.pop(COMBINED_MAP_STATE_KEY, None)
        st.info("Load demographic data first; see the hints in the maps below.")
        return
    st.caption(
        "Pick overlays from the maps below. Each uses the indicator currently chosen in its map. "
        "Stacked layers draws them on top of each other; Intersection shows only the areas that "
        "pass every threshold."
    )
    names = [o.title for o in overlays]
    chosen = st.multiselect("Overlays", names, default=names[:2], key="combined_overlays")
    mode = st.radio(
        "Mode", ["Stacked layers", "Intersection"], horizontal=True, key="combined_mode"
    )
    selected = [o for o in overlays if o.title in chosen]
    if not selected:
        st.session_state.pop(COMBINED_MAP_STATE_KEY, None)
        st.info("Choose at least one overlay.")
        return

    control_columns = st.columns(min(len(selected), 3))
    settings = []
    for i, overlay in enumerate(selected):
        with control_columns[i % len(control_columns)], st.container(border=True):
            settings.append(_overlay_controls(overlay, i, mode))

    plr_values = [_on_plr(o) for o in selected]
    tooltips = _combined_tooltips(selected, plr_values)
    if mode == "Stacked layers":
        layers = []
        legends = []
        layer_specs = []
        for overlay, values, setting in zip(selected, plr_values, settings, strict=True):
            edges = quantile_bins(values["value"])
            hide = (
                threshold_hide(values, setting["low"], setting["high"])
                if setting["filter"]
                else None
            )
            collection = feature_collection(
                plr_areas,
                values,
                edges,
                setting["ramp"],
                setting["opacity"],
                overlay.unit,
                overlay.label,
                hide=hide,
                tooltips=tooltips,
            )
            layers.append(
                choropleth_layer(
                    collection,
                    f"combined_{overlay.key}",
                    stroked=False,
                )
            )
            layer_specs.append(
                {
                    "id": f"combined_{overlay.key}",
                    "collection": collection,
                    "stroked": False,
                }
            )
            legends.append(legend_html(edges, setting["ramp"], overlay.unit, overlay.label))
        layers.append(outline_layer(bezirk_areas, "combined_bezirke"))
        outline = focus_layer(plr_areas, focus_plr, "combined_focus")
        if outline is not None:
            layers.append(outline)
        st.session_state[COMBINED_MAP_STATE_KEY] = {
            "version": 1,
            "mode": mode,
            "labels": [overlay.label for overlay in selected],
            "layers": layer_specs,
            "outline_areas": bezirk_areas[["geometry"]].copy(),
            "legends": legends,
        }
        st.pydeck_chart(
            deck(layers, view_state=focus_view(plr_areas, focus_plr)),
            height=620,
            key="combined_map",
        )
        st.caption("Layers are drawn in the order selected; the last one is on top.")
        for legend in legends:
            st.html(legend)
        return

    matching = intersect(
        [(values, s["low"], s["high"]) for values, s in zip(plr_values, settings, strict=True)]
    )
    hit = pd.DataFrame({"area_id": plr_areas["area_id"]})
    hit["value"] = hit["area_id"].isin(matching).astype(float)
    collection = feature_collection(
        plr_areas,
        hit,
        [0.0, 0.5, 1.0],
        "Purples",
        0.8,
        "pct",
        "Match",
        hide=~hit.set_index("area_id")["value"].astype(bool),
        tooltips=tooltips,
    )
    st.session_state[COMBINED_MAP_STATE_KEY] = {
        "version": 1,
        "mode": mode,
        "labels": [overlay.label for overlay in selected],
        "layers": [{"id": "intersection", "collection": collection, "stroked": True}],
        "outline_areas": bezirk_areas[["geometry"]].copy(),
        "legends": [],
    }
    intersection_layers = [
        choropleth_layer(collection, "intersection"),
        outline_layer(bezirk_areas, "intersection_bezirke"),
    ]
    outline = focus_layer(plr_areas, focus_plr, "intersection_focus")
    if outline is not None:
        intersection_layers.append(outline)
    st.pydeck_chart(
        deck(intersection_layers, view_state=focus_view(plr_areas, focus_plr)),
        height=620,
        key="intersection_map",
    )
    matched = plr_areas[plr_areas["area_id"].isin(matching)]
    population = plr_areas["population"].sum()
    share = matched["population"].sum() / population * 100 if population else 0
    st.metric(
        "Matching Planungsräume",
        f"{len(matched)} of {len(plr_areas)}",
        f"{share:.1f} % of residents",
        delta_color="off",
    )
    if matched.empty:
        return
    table = matched[["area_id", "name", "bezirk", "population"]].copy()
    for overlay, values in zip(selected, plr_values, strict=True):
        table[overlay.label] = table["area_id"].map(values.set_index("area_id")["value"])
    st.dataframe(
        table.sort_values(["bezirk", "name"]),
        hide_index=True,
        use_container_width=True,
        column_config={
            "area_id": st.column_config.TextColumn("PLR id"),
            "name": st.column_config.TextColumn("Planungsraum"),
            "bezirk": st.column_config.TextColumn("Bezirk"),
            "population": st.column_config.NumberColumn("Residents", format="%d"),
            **{o.label: st.column_config.NumberColumn(o.label, format="%.1f") for o in selected},
        },
    )


with combined_slot:
    render_combined()
