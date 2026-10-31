"""Flat listings: map, filters and price history."""

from collections.abc import Mapping

import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st
from demographics_maps import choropleth_layer, outline_layer
from filter_memory import FilterMemory
from listing_detail import open_from_map
from listing_filters import apply_listing_filters
from listing_tooltip import MAP_TOOLTIP_HTML, attach_listing_hover

from immo_scanner.explore.data import (
    load_features,
    load_plr_hover_stats,
    load_price_history,
    load_stations,
    load_workplaces,
)
from immo_scanner.explore.listing_copy import street_address
from immo_scanner.explore.prices import (
    averages_for,
    load_average_price_per_m2,
    merge_hover_prices,
)
from immo_scanner.explore.score_weights import weights_version
from immo_scanner.explore.scoring import avg_commute_minutes

COMBINED_MAP_STATE_KEY = "demographics_combined_map_snapshot"


@st.cache_data(ttl=600)
def features(active_only: bool, weights: str) -> pd.DataFrame:
    """Active listings, with district vote and turnout blended into education shares."""
    return load_features(active_only=active_only)


@st.cache_data(ttl=3600)
def stations() -> pd.DataFrame:
    return load_stations()


@st.cache_data(ttl=3600)
def workplaces() -> pd.DataFrame:
    return load_workplaces()


@st.cache_data(ttl=600)
def asking_prices() -> pd.DataFrame:
    return load_average_price_per_m2()


@st.cache_data(ttl=600)
def plr_hover_stats() -> pd.DataFrame:
    return merge_hover_prices(load_plr_hover_stats(), asking_prices())


def rented_labels(frame: pd.DataFrame) -> pd.Series:
    def one(value: object) -> str:
        if pd.isna(value):
            return ""
        return "Vermietet" if bool(value) else "Unvermietet"

    if "is_rented" not in frame.columns:
        return pd.Series("", index=frame.index)
    return frame["is_rented"].map(one)


def color_bounds(values: pd.Series) -> tuple[float, float]:
    """5th and 95th percentile; the map clips the colour scale to these."""
    finite = values.astype(float).replace([np.inf, -np.inf], np.nan).dropna()
    if finite.empty:
        return float("nan"), float("nan")
    return float(np.percentile(finite, 5)), float(np.percentile(finite, 95))


_GREEN = (40, 176, 48)
_YELLOW = (246, 196, 0)
_RED = (214, 40, 40)
_MISSING = (140, 140, 140)
_ALPHA = 190

# Sidebar label → column, whether high values are good, legend wording.
COLOR_MODES = {
    "Price per m²": {
        "column": "price_per_m2",
        "higher_is_better": False,
        "format": "{:,.0f} €/m²",
        "missing": "no €/m²",
        "caption": (
            "Listing colour: price per m², from green (cheap) through yellow to red "
            "(expensive), relative to the listings shown."
        ),
    },
    "Score": {
        "column": "total_score",
        "higher_is_better": True,
        "format": "{:.2f}",
        "missing": "no score",
        "caption": (
            "Listing colour: total score, from green (high) through yellow to red "
            "(low), relative to the listings shown."
        ),
    },
    "Commute": {
        "column": "avg_commute_minutes",
        "higher_is_better": False,
        "format": "{:.0f} min",
        "missing": "no commute",
        "caption": (
            "Listing colour: average commute to work / work 2, from green (short) "
            "through yellow to red (long), relative to the listings shown."
        ),
    },
}


def _mix(start: tuple[int, int, int], end: tuple[int, int, int], share: float) -> list[int]:
    return [int(start[i] + (end[i] - start[i]) * share) for i in range(3)] + [_ALPHA]


def _traffic(position: float) -> list[int]:
    """Green at 0, yellow at 0.5, red at 1."""
    if position <= 0.5:
        return _mix(_GREEN, _YELLOW, position / 0.5)
    return _mix(_YELLOW, _RED, (position - 0.5) / 0.5)


def metric_colors(values: pd.Series, *, higher_is_better: bool) -> list[list[int]]:
    """Green = good, red = bad, clipped at the 5th/95th percentile of the shown listings."""
    low, high = color_bounds(values)
    missing = [[*_MISSING, _ALPHA]]
    if not np.isfinite(low):
        return missing * len(values)
    if high == low:
        return [missing[0] if pd.isna(value) else _traffic(0.0) for value in values]
    scaled = ((values.astype(float) - low) / (high - low)).clip(0, 1)
    # position 0 = green (good), 1 = red (bad)
    if higher_is_better:
        scaled = 1 - scaled
    return [missing[0] if pd.isna(item) else _traffic(float(item)) for item in scaled]


def avg_commute_series(frame: pd.DataFrame) -> pd.Series:
    empty = pd.Series(np.nan, index=frame.index)
    work1 = frame["commute_minutes"] if "commute_minutes" in frame.columns else empty
    work2 = (
        frame["commute_workplace2_minutes"]
        if "commute_workplace2_minutes" in frame.columns
        else empty
    )
    return pd.Series(
        [
            avg_commute_minutes(first, second)
            for first, second in zip(work1, work2, strict=True)
        ],
        index=frame.index,
        dtype=float,
    )


SBAHN_COLOR = (0, 110, 60)
UBAHN_COLOR = (0, 90, 170)


def _mark(size: int, color: str, *, square: bool = False) -> str:
    radius = "0" if square else "50%"
    return (
        f"<span style='display:inline-block;width:{size}px;height:{size}px;"
        f"border-radius:{radius};background:{color};vertical-align:middle'></span>"
    )


def _station_layer(frame: pd.DataFrame, rgb: tuple[int, int, int], layer_id: str) -> pdk.Layer:
    """S-Bahn and U-Bahn as filled squares. A tuple keeps the glyph in the font atlas."""
    data = frame.copy()
    data["marker"] = "■"
    return pdk.Layer(
        "TextLayer",
        data=data,
        id=layer_id,
        pickable=False,
        get_position=["lon", "lat"],
        get_text="marker",
        get_size=8,
        get_color=[*rgb, 255],
        get_text_anchor="'middle'",
        get_alignment_baseline="'center'",
        font_family="'Segoe UI Symbol'",
        character_set=("■",),
        size_units="'pixels'",
    )


def combined_overlay_layers(snapshot: object) -> list[pdk.Layer]:
    """Rebuild a non-interactive copy of the Demographics combined-map layers."""
    if not isinstance(snapshot, Mapping) or snapshot.get("version") != 1:
        return []
    layers = []
    for index, spec in enumerate(snapshot.get("layers", [])):
        if not isinstance(spec, Mapping) or not isinstance(spec.get("collection"), dict):
            continue
        layers.append(
            choropleth_layer(
                spec["collection"],
                f"flat_demographics_{index}",
                stroked=bool(spec.get("stroked", False)),
                pickable=False,
            )
        )
    outline_areas = snapshot.get("outline_areas")
    if isinstance(outline_areas, pd.DataFrame) and "geometry" in outline_areas:
        layers.append(outline_layer(outline_areas, "flat_demographics_bezirke"))
    return layers


def map_legend(
    low: float,
    high: float,
    *,
    mode: dict[str, object],
    stations: bool,
    workplace: bool,
    has_missing: bool,
) -> None:
    """Explain point colour for the selected metric and what each glyph is."""
    fmt = str(mode["format"])
    higher_is_better = bool(mode["higher_is_better"])
    if np.isfinite(low) and np.isfinite(high):
        # Gradient is always green (good) → red (bad) left to right.
        left = fmt.format(high if higher_is_better else low)
        right = fmt.format(low if higher_is_better else high)
    else:
        left, right = "good", "bad"
    missing = ""
    if has_missing:
        missing = (
            f"<span style='margin-left:1rem'>{_mark(12, 'rgb(140,140,140)')} "
            f"{mode['missing']}</span>"
        )
    kinds = f"<span style='margin-right:1rem'>{_mark(14, 'rgb(246,196,0)')} Listing</span>"
    if stations:
        kinds += (
            f"<span style='margin-right:1rem'>{_mark(8, 'rgb(0,110,60)', square=True)} "
            "S-Bahn</span>"
            f"<span style='margin-right:1rem'>{_mark(8, 'rgb(0,90,170)', square=True)} "
            "U-Bahn only</span>"
        )
    if workplace:
        kinds += f"<span>{_mark(18, 'rgb(30,30,30)')} Workplace</span>"
    st.markdown(
        f"""
        <div style="font-size:0.9rem;margin:0.2rem 0 0.6rem">
          <div style="display:flex;align-items:center;gap:0.6rem;max-width:420px">
            <span>{left}</span>
            <div style="flex:1;height:12px;border-radius:6px;background:linear-gradient(
              to right, rgb(40,176,48), rgb(246,196,0), rgb(214,40,40))"></div>
            <span>{right}</span>
          </div>
          <div style="color:#444;margin-top:0.15rem">{mode["caption"]}</div>
          <div style="margin-top:0.35rem">{kinds}{missing}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


st.sidebar.header("Filters")
memory = FilterMemory.load()
include_removed = memory.checkbox("Include removed listings", "include_removed", False)
df = features(active_only=not include_removed, weights=weights_version())

if df.empty:
    memory.write()
    st.info("No data yet. Run `immo crawl` and `immo enrich` first.")
    st.stop()

df = apply_listing_filters(df, memory)
memory.write()

st.title("Berlin property scanner")
cols = st.columns(5)
cols[0].metric("Listings", f"{len(df):,}")
cols[1].metric(
    "Median price", f"{df['price'].median():,.0f} €" if df["price"].notna().any() else "-"
)
cols[2].metric(
    "Median €/m²",
    f"{df['price_per_m2'].median():,.0f}" if df["price_per_m2"].notna().any() else "-",
)
cols[3].metric(
    "Median commute",
    f"{df['commute_minutes'].median():.0f} min" if df["commute_minutes"].notna().any() else "-",
)
cols[4].metric(
    "Median days listed",
    f"{df['days_on_market'].median():.0f}" if df["days_on_market"].notna().any() else "-",
)

map_df = df.dropna(subset=["lat", "lon"]).copy()
map_df["avg_commute_minutes"] = avg_commute_series(map_df)
color_mode_name = st.sidebar.radio(
    "Colour markers by",
    list(COLOR_MODES),
    index=0,
    help="Green is always better for the chosen metric among the listings currently shown.",
)
color_mode = COLOR_MODES[color_mode_name]
color_column = str(color_mode["column"])
combined_snapshot = st.session_state.get(COMBINED_MAP_STATE_KEY)
has_combined_snapshot = bool(combined_overlay_layers(combined_snapshot))
show_combined_overlays = st.sidebar.toggle(
    "Show Demographics combined-map overlays",
    value=False,
    key="flat_show_combined_overlays",
    disabled=not has_combined_snapshot,
    help="Uses the current overlay selection, mode, thresholds, colours and opacity from "
    "the Combined map on the Demographics page.",
)
if not has_combined_snapshot:
    st.sidebar.caption("Configure the Combined map on the Demographics page first.")
if color_column not in map_df.columns:
    map_df[color_column] = np.nan
color_values = map_df[color_column]
color_low, color_high = color_bounds(color_values)
map_df["color"] = (
    metric_colors(color_values, higher_is_better=bool(color_mode["higher_is_better"]))
    if not map_df.empty
    else []
)
for column in ("bezirk", "ortsteil", "prognoseraum", "kiez"):
    if column in map_df.columns:
        map_df[column] = map_df[column].fillna("")
map_df = attach_listing_hover(map_df, plr_hover_stats())

layers = combined_overlay_layers(combined_snapshot) if show_combined_overlays else []
layers.append(
    pdk.Layer(
        "ScatterplotLayer",
        data=map_df,
        id="listings",
        get_position=["lon", "lat"],
        get_fill_color="color",
        get_radius=78,
        radius_min_pixels=5.2,
        pickable=True,
        auto_highlight=True,
    )
)
st_df = stations()
show_stations = not st_df.empty and st.sidebar.checkbox("Show S/U-Bahn stations", value=True)
if show_stations:
    sbahn = st_df[st_df["is_sbahn"]]
    ubahn = st_df[~st_df["is_sbahn"]]
    if not sbahn.empty:
        layers.append(_station_layer(sbahn, SBAHN_COLOR, "sbahn"))
    if not ubahn.empty:
        layers.append(_station_layer(ubahn, UBAHN_COLOR, "ubahn"))
wp = workplaces()
map_legend(
    color_low,
    color_high,
    mode=color_mode,
    stations=show_stations,
    workplace=not wp.empty,
    has_missing=bool(color_values.isna().any()) if not map_df.empty else False,
)
if show_combined_overlays and isinstance(combined_snapshot, Mapping):
    labels = combined_snapshot.get("labels", [])
    mode = combined_snapshot.get("mode", "Combined map")
    if isinstance(labels, list):
        st.caption(f"Demographics · {mode}: " + " · ".join(str(label) for label in labels))
    for legend in combined_snapshot.get("legends", []):
        if isinstance(legend, str):
            st.html(legend)
if not wp.empty:
    layers.append(
        pdk.Layer(
            "ScatterplotLayer",
            data=wp,
            id="workplaces",
            pickable=False,
            get_position=["lon", "lat"],
            get_fill_color=[30, 30, 30, 255],
            get_radius=150,
            radius_min_pixels=8,
        )
    )

st.caption("Click a listing to open its details.")
# Every layer has an id so the clicked listing survives the rerun that opens it.
map_click = st.pydeck_chart(
    pdk.Deck(
        layers=layers,
        initial_view_state=pdk.ViewState(latitude=52.515, longitude=13.40, zoom=10.3),
        map_style="light",
        tooltip={"html": MAP_TOOLTIP_HTML},
    ),
    use_container_width=True,
    on_select="rerun",
    selection_mode="single-object",
    key="flat-map",
)
open_from_map(df, map_click.selection)

df = df.copy()
df["rented_label"] = rented_labels(df)
df["avg_commute_minutes"] = avg_commute_series(df)
area_prices = asking_prices()
if "plr_id" in df.columns and not area_prices.empty:
    df["avg_price_per_m2"] = averages_for(df["plr_id"], area_prices)
table_columns = [
    "total_score", "price", "living_area", "building_age_years", "avg_commute_minutes",
    "address", "milieuschutz", "title",
    "available_from", "rented_label",
    "distance_score", "building_score", "flat_score", "parking_score",
    "neighborhood_score", "commute_score", "energy_class_score", "hausgeld_score",
    "building_age_score", "building_age_class_score", "heating_demand_score",
    "rooms_score", "floor_area_score", "basement_score", "balconies_score",
    "bathrooms_score", "kitchen_score", "education_score", "school_type_score",
    "tolerance_score",
    "origin_diversity_score", "citizenship_score", "oic_score",
    "crime_score", "incident_score",
    "source", "bezirk", "ortsteil", "prognoseraum", "kiez",
    "rooms", "bedrooms", "bathrooms", "apartment_type", "floor", "total_floors", "year_built",
    "last_renovation_year", "property_condition",
    "provision", "posted_on",
    "has_balcony", "has_terrace", "has_elevator", "has_parking", "has_cellar",
    "has_fitted_kitchen", "balcony_count", "heating_type", "energy_source",
    "energy_efficiency_class", "final_energy_kwh_m2a", "hausgeld", "flooring",
    "bathroom_features", "has_monument_protection",
    "thinkimmo_price_to_market_pct", "thinkimmo_market_price_per_m2", "thinkimmo_estimated_rent",
    "thinkimmo_gross_return", "thinkimmo_cash_flow", "thinkimmo_views", "is_rohling",
    "is_foreclosure",
    "thinkimmo_platforms",
    "price_per_m2",
    "avg_price_per_m2",
    "price_change_pct", "days_on_market", "commute_minutes", "commute_workplace2_minutes",
    "commute_citycenter_minutes", "nearest_rail_m", "nearest_sbahn", "nearest_ubahn",
    "grundschule_name", "grundschule_bsn",
    "nearest_grundschule", "nearest_grundschule_m", "nearest_grundschule_2",
    "nearest_grundschule_2_m", "nearest_grundschule_3", "nearest_grundschule_3_m",
    "nearest_gymnasium", "nearest_gymnasium_m", "nearest_gymnasium_2", "nearest_gymnasium_2_m",
    "nearest_gymnasium_3", "nearest_gymnasium_3_m", "nearest_montessori_kita",
    "nearest_montessori_kita_m", "lor_population", "share_65_plus_pct",
    "est_abitur_pct", "est_hochschule_pct", "agh_vote_linke",
    "agh_vote_spd", "agh_vote_gruene", "agh_vote_cdu", "agh_vote_afd",
    "geo_precision", "url", "id",
]
table = df[[c for c in table_columns if c in df.columns]].sort_values(
    "total_score", ascending=False, na_position="last"
)
if "address" in table.columns:
    table["address"] = table["address"].map(street_address)
st.dataframe(
    table,
    use_container_width=True,
    hide_index=True,
    column_config={
        "total_score": st.column_config.NumberColumn("Score", format="%.2f"),
        "living_area": st.column_config.NumberColumn("Flat Size", format="%.0f m²"),
        "building_age_years": st.column_config.NumberColumn("Building Age", format="%.0f"),
        "avg_commute_minutes": st.column_config.NumberColumn(
            "Average commute", format="%.0f min"
        ),
        "title": st.column_config.TextColumn("Name"),
        "address": st.column_config.TextColumn("Address", width="large"),
        "milieuschutz": st.column_config.TextColumn("Milieuschutz"),
        "distance_score": st.column_config.NumberColumn("Distance score", format="%.2f"),
        "building_score": st.column_config.NumberColumn("Building score", format="%.2f"),
        "flat_score": st.column_config.NumberColumn("Flat score", format="%.2f"),
        "parking_score": st.column_config.NumberColumn("Parking score", format="%.2f"),
        "neighborhood_score": st.column_config.NumberColumn("Neighbourhood score", format="%.2f"),
        "school_type_score": st.column_config.NumberColumn("Schultypisierung", format="%.2f"),
        "origin_diversity_score": st.column_config.NumberColumn(
            "Origin diversity score", format="%.2f"
        ),
        "citizenship_score": st.column_config.NumberColumn("Citizenship score", format="%.2f"),
        "oic_score": st.column_config.NumberColumn("OIC score", format="%.2f"),
        "crime_score": st.column_config.NumberColumn("Crime score", format="%.2f"),
        "incident_score": st.column_config.NumberColumn("Racist incidents", format="%.2f"),
        "url": st.column_config.LinkColumn("Link", display_text="open"),
        "price": st.column_config.NumberColumn("Price", format="%d €"),
        "price_per_m2": st.column_config.NumberColumn("€/m²", format="%d"),
        "avg_price_per_m2": st.column_config.NumberColumn("Ø €/m² Planungsraum", format="%.0f"),
        "bedrooms": st.column_config.NumberColumn("Bedrooms", format="%.0f"),
        "bathrooms": st.column_config.NumberColumn("Bathrooms", format="%.0f"),
        "apartment_type": st.column_config.TextColumn("Type"),
        "floor": st.column_config.NumberColumn("Floor", format="%d"),
        "total_floors": st.column_config.NumberColumn("Floors", format="%d"),
        "year_built": st.column_config.NumberColumn("Year built", format="%d"),
        "last_renovation_year": st.column_config.NumberColumn("Modernised", format="%d"),
        "property_condition": st.column_config.TextColumn("Condition"),
        "available_from": st.column_config.TextColumn("Available from"),
        "rented_label": st.column_config.TextColumn("Rented"),
        "provision": st.column_config.TextColumn("Provision"),
        "posted_on": st.column_config.DateColumn("Posted"),
        "has_balcony": st.column_config.CheckboxColumn("Balcony"),
        "has_terrace": st.column_config.CheckboxColumn("Terrace"),
        "has_elevator": st.column_config.CheckboxColumn("Elevator"),
        "has_parking": st.column_config.CheckboxColumn("Parking"),
        "has_cellar": st.column_config.CheckboxColumn("Cellar"),
        "has_fitted_kitchen": st.column_config.CheckboxColumn("Fitted kitchen"),
        "balcony_count": st.column_config.NumberColumn("Balconies", format="%d"),
        "heating_type": st.column_config.TextColumn("Heating"),
        "energy_source": st.column_config.TextColumn("Energy source"),
        "energy_efficiency_class": st.column_config.TextColumn("Energy class"),
        "final_energy_kwh_m2a": st.column_config.NumberColumn("Energy use", format="%.0f kWh"),
        "hausgeld": st.column_config.NumberColumn("Hausgeld", format="%.2f €"),
        "flooring": st.column_config.TextColumn("Flooring"),
        "bathroom_features": st.column_config.TextColumn("Bathroom"),
        "has_monument_protection": st.column_config.CheckboxColumn("Monument"),
        "thinkimmo_price_to_market_pct": st.column_config.NumberColumn(
            "% of market value", format="%.0f%%"
        ),
        "thinkimmo_market_price_per_m2": st.column_config.NumberColumn(
            "Market €/m²", format="%.0f"
        ),
        "thinkimmo_estimated_rent": st.column_config.NumberColumn("Est. rent", format="%.0f €"),
        "thinkimmo_gross_return": st.column_config.NumberColumn("Gross return", format="%.2f%%"),
        "thinkimmo_cash_flow": st.column_config.NumberColumn("Cash flow", format="%.0f €"),
        "thinkimmo_views": st.column_config.NumberColumn("ThinkImmo views", format="%d"),
        "is_rohling": st.column_config.CheckboxColumn("Rohling"),
        "is_foreclosure": st.column_config.CheckboxColumn("Foreclosure"),
        "thinkimmo_platforms": st.column_config.TextColumn("Portals"),
        "commute_minutes": st.column_config.NumberColumn("Commute (work)", format="%.0f min"),
        "commute_workplace2_minutes": st.column_config.NumberColumn(
            "Commute (work 2)", format="%.0f min"
        ),
        "commute_citycenter_minutes": st.column_config.NumberColumn(
            "Commute (city centre)", format="%.0f min"
        ),
        "grundschule_name": st.column_config.TextColumn("Assigned Grundschule"),
        "grundschule_bsn": st.column_config.TextColumn("Assigned BSN"),
        "nearest_grundschule": st.column_config.TextColumn("Grundschule 1"),
        "nearest_grundschule_m": st.column_config.NumberColumn("Grundschule 1 (m)", format="%.0f"),
        "nearest_grundschule_2": st.column_config.TextColumn("Grundschule 2"),
        "nearest_grundschule_2_m": st.column_config.NumberColumn(
            "Grundschule 2 (m)", format="%.0f"
        ),
        "nearest_grundschule_3": st.column_config.TextColumn("Grundschule 3"),
        "nearest_grundschule_3_m": st.column_config.NumberColumn(
            "Grundschule 3 (m)", format="%.0f"
        ),
        "nearest_gymnasium": st.column_config.TextColumn("Gymnasium 1"),
        "nearest_gymnasium_m": st.column_config.NumberColumn("Gymnasium 1 (m)", format="%.0f"),
        "nearest_gymnasium_2": st.column_config.TextColumn("Gymnasium 2"),
        "nearest_gymnasium_2_m": st.column_config.NumberColumn("Gymnasium 2 (m)", format="%.0f"),
        "nearest_gymnasium_3": st.column_config.TextColumn("Gymnasium 3"),
        "nearest_gymnasium_3_m": st.column_config.NumberColumn("Gymnasium 3 (m)", format="%.0f"),
        "nearest_montessori_kita": st.column_config.TextColumn("Montessori Kita"),
        "nearest_montessori_kita_m": st.column_config.NumberColumn(
            "Montessori Kita (m)", format="%.0f"
        ),
        "est_abitur_pct": st.column_config.NumberColumn(
            "Abitur / Fachabi (est.)", format="%.1f%%"
        ),
        "est_hochschule_pct": st.column_config.NumberColumn(
            "Hochschule / Uni (est.)", format="%.1f%%"
        ),
    },
)

st.subheader("Price history")
titles = dict(zip(df["id"], df["title"].fillna(""), strict=True))
ids = st.multiselect("Listings", list(titles), format_func=lambda i: titles[i] or str(i))
if ids:
    history = load_price_history(ids)
    st.line_chart(history, x="scraped_at", y="price", color="title")
