"""Which Planungsraum factors move with the average asking price per m²."""

import altair as alt
import pandas as pd
import streamlit as st
from demographics_maps import (
    choropleth_layer,
    deck,
    diverging_bins,
    feature_collection,
    format_value,
    legend_html,
    outline_layer,
)

from immo_scanner.explore.area_price import (
    FACTORS,
    GROUP_ORDER,
    MIN_LISTINGS,
    PRICE,
    AreaPriceModel,
    fit_area_price,
    load_area_frame,
)
from immo_scanner.explore.demographics import load_areas

alt.data_transformers.disable_max_rows()

_LABELS = {factor.key: factor.label for factor in FACTORS}
_AXES = {factor.key: factor.axis for factor in FACTORS}
_EXTREMES = 10


@st.cache_data(ttl=600)
def loaded() -> pd.DataFrame:
    return load_area_frame()


@st.cache_data(ttl=600)
def fitted() -> AreaPriceModel:
    return fit_area_price(loaded())


@st.cache_data(ttl=3600)
def areas(level: str) -> pd.DataFrame:
    return load_areas(level)


def role(row: pd.Series) -> str:
    if bool(row["in_model"]):
        return "In the fit"
    if bool(row["fit_factor"]):
        return "Left out"
    return "Correlation only"


def correlation_chart(corr: pd.DataFrame) -> alt.Chart:
    ordered = corr.sort_values("abs_r", ascending=False, na_position="last")
    labels = ordered["label"].tolist()
    return (
        alt.Chart(corr)
        .mark_bar()
        .encode(
            x=alt.X("r:Q", title="Weighted correlation with €/m²"),
            y=alt.Y("label:N", title=None, sort=list(reversed(labels))),
            color=alt.Color(
                "role:N",
                title=None,
                scale=alt.Scale(
                    domain=["In the fit", "Correlation only", "Left out"],
                    range=["#3182bd", "#bdbdbd", "#e6550d"],
                ),
            ),
            tooltip=[
                alt.Tooltip("label:N", title="Factor"),
                alt.Tooltip("group:N", title="Group"),
                alt.Tooltip("r:Q", title="Correlation", format="+.2f"),
                alt.Tooltip("areas:Q", title="Areas", format=",.0f"),
                alt.Tooltip("role:N", title="Role"),
            ],
        )
        .properties(height=max(320, 22 * len(corr) + 24))
    )


def scatter_chart(points: pd.DataFrame, key: str) -> alt.Chart:
    axis = _AXES.get(key, _LABELS.get(key, key))
    return (
        alt.Chart(points)
        .mark_circle(opacity=0.75, color="#3182bd")
        .encode(
            x=alt.X("factor:Q", title=axis, scale=alt.Scale(zero=False)),
            y=alt.Y("price_per_m2:Q", title="Asking price (€/m²)", scale=alt.Scale(zero=False)),
            size=alt.Size("listings:Q", title="Listings", scale=alt.Scale(range=[40, 400])),
            tooltip=[
                alt.Tooltip("name:N", title="Planungsraum"),
                alt.Tooltip("bezirk:N", title="Bezirk"),
                alt.Tooltip("listings:Q", title="Listings", format=",.0f"),
                alt.Tooltip("factor:Q", title=_LABELS.get(key, key), format=",.2f"),
                alt.Tooltip("price_per_m2:Q", title="€/m²", format=",.0f"),
            ],
        )
        .properties(height=420)
    )


def coefficient_chart(terms: pd.DataFrame) -> alt.LayerChart:
    effects = terms.loc[terms["key"] != "intercept"].reset_index(drop=True)
    labels = effects["label"].tolist()
    y = alt.Y("label:N", title=None, sort=list(reversed(labels)))
    base = alt.Chart(effects)
    bars = base.mark_bar().encode(
        x=alt.X("coefficient:Q", title="€/m² per standard deviation"),
        y=y,
        color=alt.Color("group:N", title="Group", sort=list(GROUP_ORDER)),
        tooltip=[
            alt.Tooltip("label:N", title="Factor"),
            alt.Tooltip("group:N", title="Group"),
            alt.Tooltip("coefficient:Q", title="€/m²", format=",.0f"),
            alt.Tooltip("ci_low:Q", title="Low", format=",.0f"),
            alt.Tooltip("ci_high:Q", title="High", format=",.0f"),
            alt.Tooltip("p_value:Q", title="p-value", format=".3f"),
        ],
    )
    intervals = base.mark_rule(color="#222222").encode(x="ci_low:Q", x2="ci_high:Q", y=y)
    zero = (
        alt.Chart(pd.DataFrame({"coefficient": [0]}))
        .mark_rule(color="#666666")
        .encode(x="coefficient:Q")
    )
    return (bars + intervals + zero).properties(height=max(280, 24 * len(effects) + 24))


def residual_table(model: AreaPriceModel) -> pd.DataFrame:
    fitted_areas = model.areas.dropna(subset=["residual"]).copy()
    if fitted_areas.empty:
        return fitted_areas
    high = fitted_areas.nlargest(_EXTREMES, "residual")
    low = fitted_areas.nsmallest(_EXTREMES, "residual")
    show = (
        pd.concat([high, low], ignore_index=True)
        .drop_duplicates("area_id")
        .sort_values("residual", ascending=False)
    )
    show["name"] = show["name"].fillna(show["area_id"])
    return show


def render_correlations(model: AreaPriceModel, frame: pd.DataFrame) -> None:
    corr = model.correlations.copy()
    if corr.empty:
        return
    corr["role"] = corr.apply(role, axis=1)
    corr["abs_r"] = corr["r"].abs()
    st.subheader("Correlations")
    st.caption(
        "Weighted Pearson correlation with the average asking price per m². "
        f"Each Planungsraum with at least {MIN_LISTINGS} listings counts in proportion "
        "to its listings. Grey factors are there to compare; they stay out of the fit "
        "because they move with a factor the fit already uses. Orange factors were "
        "dropped from this sample because too many areas leave them blank."
    )
    st.altair_chart(correlation_chart(corr), use_container_width=True)
    table = corr.sort_values("abs_r", ascending=False, na_position="last")
    st.dataframe(
        table[["label", "group", "r", "areas", "role"]],
        hide_index=True,
        use_container_width=True,
        column_config={
            "label": st.column_config.TextColumn("Factor"),
            "group": st.column_config.TextColumn("Group"),
            "r": st.column_config.NumberColumn("Correlation", format="%+.2f"),
            "areas": st.column_config.NumberColumn("Areas", format="%d"),
            "role": st.column_config.TextColumn("Role"),
        },
    )
    ranked = table.dropna(subset=["r"])
    if ranked.empty:
        return
    choice = st.selectbox(
        "Factor",
        ranked["key"].tolist(),
        format_func=_LABELS.get,
    )
    eligible = frame.loc[pd.to_numeric(frame["listings"], errors="coerce") >= MIN_LISTINGS].copy()
    points = eligible.dropna(subset=[choice, PRICE]).copy()
    points["factor"] = pd.to_numeric(points[choice], errors="coerce")
    points["name"] = points["name"].fillna(points["area_id"]).astype(str)
    points["bezirk"] = points["bezirk"].fillna("").astype(str)
    st.altair_chart(scatter_chart(points, choice), use_container_width=True)
    st.caption(
        f"Each point is a Planungsraum with at least {MIN_LISTINGS} listings and a value "
        "for this factor. The size is the listing count. The correlation uses the same areas, "
        "weighted by that count."
    )


def render_fit(model: AreaPriceModel) -> None:
    st.subheader("€/m² per standard deviation")
    for note in model.notes:
        st.caption(note)
    if model.omitted:
        names = ", ".join(_LABELS.get(key, key) for key in model.omitted)
        st.caption(
            "Left out of the fit because, in this sample, it repeats a pattern already "
            f"captured by the other factors: {names}."
        )
    if model.error:
        st.info(model.error)
        return
    if not model.robust:
        st.caption(
            "The areas sit exactly on the line, so the intervals use ordinary standard errors."
        )
    st.caption(
        "Each bar is the €/m² difference associated with a one-standard-deviation higher "
        "factor, the others held fixed. The line is the 95% confidence interval. "
        + (
            "Intervals use heteroscedasticity-robust standard errors."
            if model.robust
            else "Intervals use ordinary standard errors."
        )
    )
    st.altair_chart(coefficient_chart(model.terms), use_container_width=True)
    st.dataframe(
        model.terms,
        hide_index=True,
        use_container_width=True,
        column_config={
            "key": None,
            "label": st.column_config.TextColumn("Factor"),
            "group": st.column_config.TextColumn("Group"),
            "coefficient": st.column_config.NumberColumn("€/m² per SD", format="%.0f"),
            "std_error": st.column_config.NumberColumn("Standard error", format="%.1f"),
            "ci_low": st.column_config.NumberColumn("Low", format="%.0f"),
            "ci_high": st.column_config.NumberColumn("High", format="%.0f"),
            "p_value": st.column_config.NumberColumn("p-value", format="%.3f"),
            "step": st.column_config.TextColumn("Step"),
            "scale": None,
            "per_unit": st.column_config.NumberColumn("€/m² per step", format="%.1f"),
            "per_unit_low": None,
            "per_unit_high": None,
            "pct_of_average": st.column_config.NumberColumn("% of average", format="%.2f"),
            "pct_low": st.column_config.NumberColumn("% low", format="%.2f"),
            "pct_high": st.column_config.NumberColumn("% high", format="%.2f"),
        },
    )
    st.caption(
        "A step is one extra percentage point, or one point, grade, Stufe, incident, "
        "offence, kilometre, square metre or year, as named. €/m² per step is the "
        "standard-deviation effect divided by that factor's standard deviation. "
        "The percent is that euro step divided by the intercept, so it is the change "
        "at the average price. The same euro amount is a smaller share of a more "
        "expensive area. The other factors stay fixed. % low and % high are the "
        "confidence bounds divided the same way."
    )
    intercept = model.terms.loc[model.terms["key"] == "intercept", "coefficient"]
    if not intercept.empty:
        mean = f"{float(intercept.iloc[0]):,.0f} €/m²"
        st.caption(
            "The intercept is the average €/m² when every factor is at its weighted mean: "
            f"{mean}. A lower Abiturnote is a stronger school result, so a positive "
            "coefficient there is the premium for a weaker average."
        )


def render_residuals(model: AreaPriceModel) -> None:
    if model.error or model.areas.empty:
        return
    st.subheader("Left over after the model")
    st.caption(
        "Asking price per m² minus the fitted value. Blue areas are cheaper than the factors "
        "account for. Red areas are more expensive. Grey areas are missing a factor the fit "
        "uses, or they have fewer than three listings."
    )
    plr = areas("plr")
    bezirke = areas("bezirk")
    fitted_areas = model.areas.dropna(subset=["residual"]).copy()
    fitted_areas["area_id"] = fitted_areas["area_id"].map(
        lambda value: "" if pd.isna(value) else str(value).strip()
    )
    values = fitted_areas[["area_id", "residual"]].rename(columns={"residual": "value"})
    plr = plr.copy()
    plr["area_id"] = plr["area_id"].map(lambda value: "" if pd.isna(value) else str(value).strip())
    tooltips = {}
    for record in fitted_areas.itertuples(index=False):
        count = int(record.listings)
        noun = "listing" if count == 1 else "listings"
        tooltips[record.area_id] = (
            f"Asking {format_value(record.price_per_m2, 'eur')}/m²<br/>"
            f"Predicted {format_value(record.predicted, 'eur')}/m²<br/>"
            f"Asking − predicted {format_value(record.residual, 'eur')}/m²<br/>"
            f"{count} {noun}"
        )
    edges = diverging_bins(values["value"])
    layers = [
        choropleth_layer(
            feature_collection(
                plr,
                values,
                edges,
                "diverging",
                0.8,
                "eur",
                "Asking − predicted",
                tooltips=tooltips,
            ),
            "area_price_fill",
        ),
        outline_layer(bezirke, "area_price_bezirke"),
    ]
    st.pydeck_chart(deck(layers), height=560, key="area_price_residuals")
    st.html(legend_html(edges, "diverging", "eur", "Asking − predicted, €/m²"))
    show = residual_table(model)
    if show.empty:
        return
    st.caption(
        f"The {_EXTREMES} areas furthest above the model and the {_EXTREMES} furthest below."
    )
    st.dataframe(
        show[["name", "bezirk", "listings", PRICE, "predicted", "residual"]],
        hide_index=True,
        use_container_width=True,
        column_config={
            "name": st.column_config.TextColumn("Planungsraum"),
            "bezirk": st.column_config.TextColumn("Bezirk"),
            "listings": st.column_config.NumberColumn("Listings", format="%d"),
            PRICE: st.column_config.NumberColumn("€/m²", format="%.0f"),
            "predicted": st.column_config.NumberColumn("Predicted", format="%.0f"),
            "residual": st.column_config.NumberColumn("Asking − predicted", format="%+.0f"),
        },
    )


st.title("Area price model")
st.markdown(
    """
This relates the **average asking price per m²** of a Planungsraum to who lives there,
and to a few facts about the ads and the location. Each number in the fit is the €/m²
difference associated with a one-standard-deviation higher factor, while the others stay
fixed. Areas are weighted by how many listings they have.

The average is the one mapped on the Demographics page: active listings, ThinkImmo
excluded, and titles that contain the word Rohling excluded. Each ad counts once, as its
price divided by its living area.
"""
)
st.caption(
    "The Abitur share is estimated from national party education profiles and the local "
    "Abgeordnetenhaus vote mix. Distance to the centre is the straight-line distance from "
    "the area centroid to Alexanderplatz. Incidents are Berliner Register reports per 1,000 "
    "residents per year over the three years ending on the newest report. Crime is "
    "neighbourhood offences per 100,000 residents in the Bezirksregion, latest year. "
    "The coefficients still pick up anything this list leaves unmeasured."
)

frame = loaded()
if frame.empty or pd.to_numeric(frame.get(PRICE), errors="coerce").notna().sum() == 0:
    st.info("No priced listings yet. Run `immo crawl` and `immo enrich` first.")
    st.stop()

model = fitted()
metrics = st.columns(5)
metrics[0].metric("Areas used", f"{model.n_used:,}")
metrics[1].metric("Left out", f"{model.n_eligible - model.n_used:,}")
metrics[2].metric("R²", "–" if model.rsquared is None else f"{model.rsquared:.2f}")
metrics[3].metric("Typical miss", "–" if model.rmse is None else f"{model.rmse:,.0f} €/m²")
metrics[4].metric(
    "Median miss",
    "–" if model.median_abs_error is None else f"{model.median_abs_error:,.0f} €/m²",
)
st.caption(
    f"Left out counts Planungsräume with at least {MIN_LISTINGS} listings that are missing "
    "a factor the fit kept. Areas with fewer listings are excluded before that. "
    "R² is the share of €/m² differences the factors account for, weighted by listings. "
    "Typical miss is the listing-weighted root-mean-square error. "
    "Median miss is the middle absolute error across the areas in the fit."
)

if model.n_eligible == 0:
    st.info(f"No Planungsraum has at least {MIN_LISTINGS} qualifying listings.")
    st.stop()

render_correlations(model, frame)
render_fit(model)
render_residuals(model)
