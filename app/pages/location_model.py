"""Location model: a reference €/m² per Planungsraum and what each flat adds to it."""

import dataclasses

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st
from demographics_maps import (
    choropleth_layer,
    deck,
    feature_collection,
    format_value,
    legend_html,
    outline_layer,
    quantile_bins,
)
from filter_memory import FilterMemory
from listing_filters import apply_listing_filters, hide_outdated_selected

from immo_scanner.explore.area_price import load_area_frame
from immo_scanner.explore.data import load_features
from immo_scanner.explore.demographics import load_areas
from immo_scanner.explore.location_model import (
    AGE_BANDS,
    AGE_LABELS,
    LocationModel,
    ReferenceFlat,
    attribute_importance,
    city_reference,
    fit_location_model,
    flat_contributions,
    quarter_contributions,
    quarter_reference,
)

alt.data_transformers.disable_max_rows()


@st.cache_data(ttl=600)
def loaded(active_only: bool) -> pd.DataFrame:
    return load_features(active_only=active_only)


@st.cache_data(ttl=600)
def area_frame() -> pd.DataFrame:
    return load_area_frame(every_area=True)


@st.cache_data(ttl=600)
def fitted(active_only: bool) -> LocationModel:
    return fit_location_model(loaded(active_only), area_frame())


@st.cache_data(ttl=600, max_entries=8)
def fitted_on(frame: pd.DataFrame) -> LocationModel:
    """The model refitted on a filtered set of listings."""
    return fit_location_model(frame, area_frame())


@st.cache_data(ttl=3600)
def areas(level: str) -> pd.DataFrame:
    return load_areas(level)


def _percent(value: float) -> float:
    return float(np.expm1(value) * 100.0)


def importance_chart(table: pd.DataFrame) -> alt.Chart:
    return (
        alt.Chart(table)
        .mark_bar()
        .encode(
            x=alt.X(
                "share:Q",
                title="Share of the spread in reference prices",
                axis=alt.Axis(format="%"),
            ),
            y=alt.Y("group:N", title=None, sort="-x"),
            tooltip=[
                alt.Tooltip("group:N", title="Group"),
                alt.Tooltip("share:Q", title="Share", format=".0%"),
                alt.Tooltip("sd_percent:Q", title="Typical size (±%)", format=".1f"),
            ],
        )
    )


def reference_map(quarters: pd.DataFrame) -> None:
    plr = areas("plr").copy()
    plr["area_id"] = plr["area_id"].map(lambda value: "" if pd.isna(value) else str(value).strip())
    bezirke = areas("bezirk")
    values = quarters[["area_id", "reference_per_m2"]].rename(columns={"reference_per_m2": "value"})
    edges = quantile_bins(values["value"])
    attributes_only = quarters.set_index("area_id")["from_attributes_only"].astype(bool)
    tooltips = {}
    for record in quarters.itertuples(index=False):
        count = int(record.listings)
        basis = (
            "No listings: from attributes only"
            if record.from_attributes_only
            else f"{count} listing{'s' if count != 1 else ''}"
        )
        tooltips[record.area_id] = (
            f"Reference {format_value(record.reference_per_m2, 'eur')}/m²<br/>"
            f"95% band {format_value(record.low, 'eur')} – {format_value(record.high, 'eur')}<br/>"
            f"{basis}"
        )
    layers = [
        choropleth_layer(
            feature_collection(
                plr,
                values,
                edges,
                "Oranges",
                0.85,
                "eur",
                "Reference",
                hide=attributes_only,
                tooltips=tooltips,
            ),
            "location_reference_listed",
        ),
        choropleth_layer(
            feature_collection(
                plr,
                values,
                edges,
                "Oranges",
                0.35,
                "eur",
                "Reference",
                hide=~attributes_only,
                tooltips=tooltips,
            ),
            "location_reference_attributes",
        ),
        outline_layer(bezirke, "location_reference_bezirke"),
    ]
    st.pydeck_chart(deck(layers), height=560, key="location_reference_map")
    st.html(legend_html(edges, "Oranges", "eur", "Reference price of the standard flat, €/m²"))


def _flat_link(value: object) -> str | None:
    number = pd.to_numeric(value, errors="coerce")
    if pd.isna(number):
        return None
    return f"/flat?listing={int(number)}"


def prediction_chart(listings: pd.DataFrame, names: pd.Series) -> alt.LayerChart:
    """Asking against predicted price. A click opens the flat's detail page in a new tab."""
    points = listings.copy()
    points["title"] = points["title"].fillna("").astype(str)
    points["bezirk"] = points["bezirk"].fillna("").astype(str)
    points["planungsraum"] = points["area_id"].map(names).fillna("").astype(str)
    points["built"] = points["age_band"].map(AGE_LABELS).fillna("")
    points["flat_url"] = points["id"].map(_flat_link)
    low = float(min(points["price"].min(), points["predicted_price"].min()))
    high = float(max(points["price"].max(), points["predicted_price"].max()))
    scale = alt.Scale(type="log", domain=[low * 0.95, high * 1.05], nice=False)
    circles = (
        alt.Chart(points)
        .mark_circle(opacity=0.7, size=60, cursor="pointer")
        .encode(
            x=alt.X("predicted_price:Q", title="Predicted asking price (€)", scale=scale),
            y=alt.Y("price:Q", title="Asking price (€)", scale=scale),
            color=alt.Color(
                "built:N",
                title="Building age",
                sort=[AGE_LABELS[band] for band in AGE_BANDS],
            ),
            description=alt.Description("flat_url:N"),
            tooltip=[
                alt.Tooltip("title:N", title="Name"),
                alt.Tooltip("bezirk:N", title="Bezirk"),
                alt.Tooltip("planungsraum:N", title="Planungsraum"),
                alt.Tooltip("built:N", title="Building age"),
                alt.Tooltip("living_area:Q", title="Living area (m²)", format=",.0f"),
                alt.Tooltip("price:Q", title="Asking price (€)", format=",.0f"),
                alt.Tooltip("predicted_price:Q", title="Predicted (€)", format=",.0f"),
                alt.Tooltip("gap:Q", title="Asking − predicted (€)", format=",.0f"),
                alt.Tooltip("price_per_m2:Q", title="Asking €/m²", format=",.0f"),
                alt.Tooltip("predicted_per_m2:Q", title="Predicted €/m²", format=",.0f"),
            ],
        )
    )
    line = (
        alt.Chart(pd.DataFrame({"predicted_price": [low, high], "price": [low, high]}))
        .mark_line(color="#333333")
        .encode(x="predicted_price:Q", y="price:Q")
    )
    # SVG so a click lands on the point itself. The app script opens its flat page.
    return (circles + line).properties(height=520, usermeta={"embedOptions": {"renderer": "svg"}})


st.sidebar.header("Filters")
memory = FilterMemory.load()
active_only = hide_outdated_selected(memory)
raw = loaded(active_only)
if raw.empty:
    memory.write()
    st.info("No data yet. Run `immo crawl` and `immo enrich` first.")
    st.stop()
filtered = apply_listing_filters(raw, memory)
memory.write()
narrowed = len(filtered) < len(raw)
refit = st.sidebar.toggle(
    "Fit the model on the filtered listings",
    value=False,
    disabled=not narrowed,
    help=(
        "Off: the model is fitted on every listing, and the filters only choose which "
        "listings the chart and the listing picker show. On: the model is fitted on the "
        "filtered listings alone, so effects can shift and thin filters can leave too few."
    ),
)
st.sidebar.header("Standard flat")
ref_area = st.sidebar.number_input("Living area (m²)", 20, 250, 70, step=5)
ref_band = st.sidebar.selectbox(
    "Built",
    [band for band in AGE_BANDS if band != "unknown"],
    index=2,
    format_func=lambda band: AGE_LABELS[band],
)
ref_elevator = st.sidebar.checkbox("Elevator", value=False)
ref_balcony = st.sidebar.checkbox("Balcony", value=False)
reference = ReferenceFlat(
    living_area=float(ref_area),
    rooms=max(1.0, round(float(ref_area) / 28.0 * 2) / 2),
    age_band=ref_band,
    has_elevator=ref_elevator,
    has_balcony=ref_balcony,
)

base_model = fitted_on(filtered) if refit and narrowed else fitted(active_only)
model = base_model
if not base_model.params.empty and reference != base_model.reference:
    model = dataclasses.replace(
        base_model, reference=reference, quarters=quarter_reference(base_model, reference)
    )

st.title("Location model")
st.markdown(
    """
This model splits the asking price per m² into two parts. The **reference price** of a
Planungsraum is what one standard flat (set in the sidebar) would be listed at there.
The **flat component** is what a particular flat's size, age, amenities and energy class
add to or take off that reference.

The flat effects are estimated only from flats that share a Planungsraum. Areas full of
old buildings therefore do not inflate the Altbau premium, and the Altbau premium does
not leak into the area price. The area's own mix of buildings enters separately, as
**building stock**. Each area also gets an unexplained premium that its attributes don't
account for. Areas with few listings are pulled towards what their attributes predict.
"""
)
st.caption(
    "The outcome is the log of the asking price per m², so effects are percentages. "
    "ThinkImmo and unfinished-shell titles are left out, as in the euro price model."
)

if model.n_input == 0:
    st.info("No data yet. Run `immo crawl` and `immo enrich` first.")
    st.stop()

metrics = st.columns(5)
metrics[0].metric("Listings used", f"{model.n_used:,}")
metrics[1].metric("Planungsräume with listings", f"{model.n_areas:,}")
metrics[2].metric("With 3 or more", f"{model.n_areas_3:,}")
metrics[3].metric("Share between areas", "–" if model.icc is None else f"{model.icc:.0%}")
metrics[4].metric("Typical miss", "–" if model.rmse is None else f"±{_percent(model.rmse):.0f}%")
st.caption(
    "Share between areas is how much of the spread in €/m² lies between Planungsräume "
    "rather than between flats in the same one, before any feature is used. "
    "Typical miss is the root-mean-square error of the fit, as a percentage of €/m²."
)

if model.error:
    st.info(model.error)
    st.stop()
for note in model.notes:
    st.caption(note)

with st.expander("Which features the listings state"):
    st.dataframe(
        model.coverage,
        use_container_width=True,
        hide_index=True,
        column_config={
            "feature": st.column_config.TextColumn("Feature"),
            "stated": st.column_config.NumberColumn("Stated", format="%d"),
            "not_stated": st.column_config.NumberColumn("Not stated", format="%d"),
        },
    )
    st.caption(
        "A blank number gets the average plus its own not-stated adjustment. A blank "
        "amenity counts as a stated no plus a not-stated adjustment. Blank year built, "
        "energy class and rental status are their own category."
    )

st.subheader("Flat effects")
spec = model.spec
st.caption(
    "Percent change in €/m² compared with a flat "
    f"{AGE_LABELS.get(spec.age_reference, spec.age_reference).lower()}, "
    f"energy class {spec.energy_reference}, {spec.rental_reference}, with a stated no "
    "for each amenity. Living area is the change when the area doubles. "
    "**Pooled** ignores location. **Within areas** compares flats inside the same "
    "Planungsraum. **Multilevel** is the model used below. Within areas and multilevel "
    "should agree; a flag means they don't, which would point to confounding left over."
)
st.dataframe(
    model.comparison[["label", "naive_percent", "fixed_percent", "mundlak_percent", "flagged"]],
    use_container_width=True,
    hide_index=True,
    column_config={
        "label": st.column_config.TextColumn("Feature"),
        "naive_percent": st.column_config.NumberColumn("Pooled", format="%+.1f%%"),
        "fixed_percent": st.column_config.NumberColumn("Within areas", format="%+.1f%%"),
        "mundlak_percent": st.column_config.NumberColumn("Multilevel", format="%+.1f%%"),
        "flagged": st.column_config.CheckboxColumn("Gap"),
    },
)
flat_terms = model.terms.loc[model.terms["kind"] == "flat"].copy()
with st.expander("Multilevel flat effects with 95% intervals"):
    flat_terms["low_pct"] = flat_terms["ci_low"].map(_percent)
    flat_terms["high_pct"] = flat_terms["ci_high"].map(_percent)
    st.dataframe(
        flat_terms[["label", "percent", "low_pct", "high_pct", "p_value"]],
        use_container_width=True,
        hide_index=True,
        column_config={
            "label": st.column_config.TextColumn("Feature"),
            "percent": st.column_config.NumberColumn("Effect", format="%+.1f%%"),
            "low_pct": st.column_config.NumberColumn("Low", format="%+.1f%%"),
            "high_pct": st.column_config.NumberColumn("High", format="%+.1f%%"),
            "p_value": st.column_config.NumberColumn("p-value", format="%.3f"),
        },
    )
    st.caption("The living-area interval is per log unit, not per doubling.")

st.subheader("What sets the reference price")
cols = st.columns(3)
cols[0].metric(
    "Area spread explained",
    "–" if model.explained_by_area_terms is None else f"{model.explained_by_area_terms:.0%}",
)
cols[1].metric(
    "Of that, by attributes",
    "–" if model.explained_by_attributes is None else f"{model.explained_by_attributes:.0%}",
)
cols[2].metric(
    "Unexplained premium",
    "–" if model.sigma_u2 is None else f"±{_percent(float(np.sqrt(model.sigma_u2))):.1f}%",
)
st.caption(
    "Area spread explained is how much smaller the spread between Planungsräume gets "
    "once the flats, the building stock and the attributes are in. Of that, by attributes "
    "is the part the attributes add on top of the flats and the building stock. "
    "The unexplained premium is the typical size of what is left."
)
importance = attribute_importance(model)
if not importance.empty:
    st.altair_chart(importance_chart(importance), use_container_width=True)
    st.caption(
        "Each bar is the group's share of the differences in reference price between "
        "Planungsräume with listings. The shares add up to 100%. Attributes move "
        "together, so a share is how much the group lines up with the total, not what "
        "would change if only that group changed."
    )
quarter_terms = model.terms.loc[model.terms["kind"] == "quarter"].copy()
quarter_terms["low_pct"] = quarter_terms["ci_low"].map(_percent)
quarter_terms["high_pct"] = quarter_terms["ci_high"].map(_percent)
st.dataframe(
    quarter_terms[["group", "label", "percent", "low_pct", "high_pct", "p_value"]],
    use_container_width=True,
    hide_index=True,
    column_config={
        "group": st.column_config.TextColumn("Group"),
        "label": st.column_config.TextColumn("Attribute"),
        "percent": st.column_config.NumberColumn("Per standard deviation", format="%+.1f%%"),
        "low_pct": st.column_config.NumberColumn("Low", format="%+.1f%%"),
        "high_pct": st.column_config.NumberColumn("High", format="%+.1f%%"),
        "p_value": st.column_config.NumberColumn("p-value", format="%.3f"),
    },
)
st.caption(
    "Each attribute is standardised over the Planungsräume with listings, so the effect "
    "is for one standard deviation, with the others held fixed. A not-stated row is the "
    "adjustment for areas where that attribute is blank. These are associations: an "
    "area's prices also shape who lives there and how it votes."
)
with st.expander("Building stock terms"):
    stock = model.terms.loc[model.terms["kind"] == "building_stock"]
    st.dataframe(
        stock[["label", "coefficient", "std_error", "p_value"]],
        use_container_width=True,
        hide_index=True,
        column_config={
            "label": st.column_config.TextColumn("Term"),
            "coefficient": st.column_config.NumberColumn("Log points per unit", format="%+.3f"),
            "std_error": st.column_config.NumberColumn("Std. error", format="%.3f"),
            "p_value": st.column_config.NumberColumn("p-value", format="%.3f"),
        },
    )
    st.caption(
        "The listing mix of the Planungsraum, beyond each flat's own features. A share "
        "going from 0 to 1 moves the area's reference by the coefficient in log points. "
        "This is where, for example, an Altbau streetscape shows up as a neighbourhood effect."
    )

st.subheader("Reference price map")
st.caption(
    f"The standard flat across Berlin: {city_reference(model, reference):,.0f} €/m² in an "
    "average Planungsraum. Faded areas have no listings, so their reference comes from "
    "their attributes alone and its band is wider."
)
reference_map(model.quarters)

st.subheader("One Planungsraum")
quarters = model.quarters.copy()
quarters["_label"] = [
    f"{name} ({bezirk}) — {count} listing{'s' if count != 1 else ''}"
    if isinstance(bezirk, str) and bezirk
    else f"{name} — {count} listing{'s' if count != 1 else ''}"
    for name, bezirk, count in zip(
        quarters["name"], quarters["bezirk"], quarters["listings"], strict=True
    )
]
quarters = quarters.sort_values(["listings", "_label"], ascending=[False, True])
choice = st.selectbox(
    "Planungsraum",
    quarters["area_id"].tolist(),
    format_func=quarters.set_index("area_id")["_label"].to_dict().get,
)
row = quarters.set_index("area_id").loc[choice]
figures = st.columns(4)
figures[0].metric("Reference", f"{row['reference_per_m2']:,.0f} €/m²")
figures[1].metric("95% band", f"{row['low']:,.0f} – {row['high']:,.0f}")
figures[2].metric(
    "Within-area estimate",
    "–" if pd.isna(row["fe_reference_per_m2"]) else f"{row['fe_reference_per_m2']:,.0f} €/m²",
)
figures[3].metric(
    "Mean asking",
    "–" if pd.isna(row["asking_per_m2"]) else f"{row['asking_per_m2']:,.0f} €/m²",
)
st.caption(
    "The within-area estimate uses only this area's own listings, with no pull towards "
    "its attributes. Mean asking is the plain average of its listings, whatever their mix."
)
detail = st.checkbox("Show each building-stock term", value=False)
st.dataframe(
    quarter_contributions(model, choice, detail=detail),
    use_container_width=True,
    hide_index=True,
    column_config={
        "label": st.column_config.TextColumn("Term"),
        "group": st.column_config.TextColumn("Group"),
        "log_points": st.column_config.NumberColumn("Log points", format="%+.3f"),
        "percent": st.column_config.NumberColumn("Effect", format="%+.1f%%"),
        "per_m2": st.column_config.NumberColumn("€/m²", format="%.0f"),
    },
)
st.caption(
    "Starting from the city reference, each row moves this area's reference. The log "
    "points add up to the total; each percentage is that row on its own."
)

st.subheader("Predicted and asking price")
shown = model.listings.loc[model.listings.index.isin(filtered.index)]
if shown.empty:
    st.info("No listing in the fit matches these filters.")
    st.stop()
left_out = len(filtered) - len(shown)
st.caption(
    f"{len(shown):,} listings match the filters"
    + (
        f"; {left_out:,} more match but are not in the fit (no price, living area or "
        "Planungsraum, ThinkImmo, or an unfinished shell)"
        if left_out > 0
        else ""
    )
    + ". Both axes are logarithmic, so equal distances are equal percentages. "
    "A point under the line is listed for less than the model expects. "
    "A point opens that flat’s detail page in a new tab."
)
st.altair_chart(prediction_chart(shown, model.area_info["name"]), use_container_width=True)

st.subheader("One listing")
listings = shown.copy()
listings["_label"] = [
    f"{title} ({identifier}) — {price:,.0f} €"
    if isinstance(title, str) and title.strip()
    else f"Listing {identifier} — {price:,.0f} €"
    for title, identifier, price in zip(
        listings["title"], listings["id"], listings["price"], strict=True
    )
]
pick = st.selectbox(
    "Listing",
    list(listings.index),
    format_func=listings["_label"].to_dict().get,
)
chosen = listings.loc[pick]
table = flat_contributions(model, pick)
predicted = float(table["euros"].iloc[-1])
figures = st.columns(3)
figures[0].metric("Asking price", f"{float(chosen['price']):,.0f} €")
figures[1].metric("Predicted", f"{predicted:,.0f} €")
figures[2].metric("Asking − predicted", f"{float(chosen['price']) - predicted:,.0f} €")
st.dataframe(
    table,
    use_container_width=True,
    hide_index=True,
    column_config={
        "term": st.column_config.TextColumn("Term"),
        "input": st.column_config.TextColumn("This flat"),
        "reference": st.column_config.TextColumn("Standard flat"),
        "log_points": st.column_config.NumberColumn("Log points", format="%+.3f"),
        "percent": st.column_config.NumberColumn("Effect", format="%+.1f%%"),
        "euros": st.column_config.NumberColumn("Euros", format="%.0f €"),
    },
)
st.caption(
    "The first row is this flat's living area at its Planungsraum's reference rate. "
    "Each feature row is the difference from the standard flat. The euros add up to "
    "the predicted price."
)
