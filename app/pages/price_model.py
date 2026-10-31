"""Asking-price model: what each feature is worth, in euros."""

import altair as alt
import pandas as pd
import streamlit as st

from immo_scanner.explore.data import load_features
from immo_scanner.explore.price_model import (
    FEATURES,
    PriceModel,
    contributions,
    fit_price_model,
    formula_lines,
    missing_rates_by_source,
)

alt.data_transformers.disable_max_rows()


@st.cache_data(ttl=600)
def loaded(active_only: bool) -> pd.DataFrame:
    return load_features(active_only=active_only)


@st.cache_data(ttl=600)
def fitted(active_only: bool) -> PriceModel:
    """Fit asking price on the listings currently loaded."""
    return fit_price_model(loaded(active_only))


def omitted_label(key: str) -> str:
    if key.startswith("energy:"):
        return f"Energy class {key.split(':', 1)[1]}"
    if key.startswith("rental:"):
        return key.split(":", 1)[1].capitalize()
    if key.startswith("missing:"):
        key = key.split(":", 1)[1]
        suffix = " not stated"
    else:
        suffix = ""
    labels = {feature.key: feature.label for feature in FEATURES}
    return labels.get(key, key) + suffix


def _flat_link(value: object) -> str | None:
    number = pd.to_numeric(value, errors="coerce")
    if pd.isna(number):
        return None
    return f"/flat?listing={int(number)}"


def prediction_chart(listings: pd.DataFrame) -> alt.LayerChart:
    points = listings.copy()
    points["title"] = points["title"].fillna("").astype(str)
    points["bezirk"] = points["bezirk"].fillna("").astype(str)
    points["flat_url"] = points["id"].map(_flat_link)
    low = float(min(points["price"].min(), points["predicted"].min()))
    high = float(max(points["price"].max(), points["predicted"].max()))
    circles = (
        alt.Chart(points)
        .mark_circle(opacity=0.75, size=64, cursor="pointer")
        .encode(
            x=alt.X("predicted:Q", title="Predicted asking price (€)", scale=alt.Scale(zero=False)),
            y=alt.Y("price:Q", title="Asking price (€)", scale=alt.Scale(zero=False)),
            color=alt.Color("energy_class:N", title="Energy class"),
            href=alt.Href("flat_url:N"),
            tooltip=[
                alt.Tooltip("title:N", title="Name"),
                alt.Tooltip("bezirk:N", title="Bezirk"),
                alt.Tooltip("energy_class:N", title="Energy class"),
                alt.Tooltip("living_area:Q", title="Living area (m²)", format=",.0f"),
                alt.Tooltip("price:Q", title="Asking price (€)", format=",.0f"),
                alt.Tooltip("predicted:Q", title="Predicted (€)", format=",.0f"),
                alt.Tooltip("gap:Q", title="Asking − predicted (€)", format=",.0f"),
            ],
        )
    )
    line = (
        alt.Chart(pd.DataFrame({"predicted": [low, high], "price": [low, high]}))
        .mark_line(color="#333333")
        .encode(x="predicted:Q", y="price:Q")
    )
    return circles + line


st.sidebar.header("Sample")
include_removed = st.sidebar.checkbox("Include removed listings", value=False)
active_only = not include_removed
model = fitted(active_only)

st.title("Price model")
st.markdown(
    """
This is a straight-line model of the **asking price**. Each number is the euro
difference associated with that feature while the others stay fixed. Building age
stands in for year built: age is the current year minus the year built, so the two
are the same fact. Energy class D is the baseline when the sample includes it;
a missing certificate stays in as “unknown”. Unrented is the baseline when the
sample includes it; rented and a blank rental status each have their own euro
amount. ThinkImmo is left out, because those rows repeat ads the portal
crawlers already store. A title that contains the word Rohling, Baugenehmigung,
Unausgebaut, Unausgebautes, or Bauvorbescheid is left out too.
An amenity coefficient is the difference between yes and a stated no.
"""
)
st.caption(
    "The target is the asking price on the listing. "
    "An extra room is valued at the same floor area, so its euro effect can be small. "
    "The Planungsraum figure is this flat's living area priced at the area's average €/m², "
    "leaving this flat out of that average. The other features are the premium on top "
    "of that local price."
)

if model.n_input == 0:
    st.info("No data yet. Run `immo crawl` and `immo enrich` first.")
    st.stop()

metrics = st.columns(5)
metrics[0].metric("Listings used", f"{model.n_used:,}")
metrics[1].metric("Left out", f"{model.n_input - model.n_used:,}")
metrics[2].metric("R²", "–" if model.rsquared is None else f"{model.rsquared:.2f}")
metrics[3].metric("Typical miss", "–" if model.rmse is None else f"{model.rmse:,.0f} €")
metrics[4].metric(
    "Median miss",
    "–" if model.median_abs_error is None else f"{model.median_abs_error:,.0f} €",
)
st.caption(
    "R² is the share of asking-price differences the features account for. "
    "Typical miss is the root-mean-square error, in euros. "
    "Median miss is the middle absolute error and is less pulled by a few very expensive flats."
)

if model.error:
    st.info(model.error)

st.subheader("Sample")
st.caption(
    "A feature that is blank on only a few listings drops those listings. "
    "A feature that is blank often stays in the fit: its euro effect comes from the "
    "listings that state it, and the blank ones get their own adjustment. "
    "Unknown energy class is kept."
)
st.dataframe(
    model.coverage,
    use_container_width=True,
    hide_index=True,
    column_config={
        "feature": st.column_config.TextColumn("Feature"),
        "stated": st.column_config.NumberColumn("Stated", format="%d"),
        "not_stated": st.column_config.NumberColumn("Not stated", format="%d"),
        "handling": st.column_config.TextColumn("In the fit"),
    },
)
for note in model.notes:
    st.caption(note)
if not model.by_source.empty:
    st.dataframe(
        model.by_source,
        use_container_width=True,
        hide_index=True,
        column_config={
            "source": st.column_config.TextColumn("Source"),
            "listings": st.column_config.NumberColumn("Loaded", format="%d"),
            "used": st.column_config.NumberColumn("Used in the fit", format="%d"),
        },
    )

if not model.error and not model.terms.empty:
    if model.reference != "D":
        st.caption(
            f"This sample has no class D flats, so class {model.reference} is the baseline instead."
        )
    if model.omitted:
        names = ", ".join(omitted_label(key) for key in model.omitted)
        st.caption(
            "Left out of the fit because, in this sample, it repeats a pattern already "
            f"captured by the other features: {names}."
        )
    if not model.robust:
        st.caption(
            "The listings sit exactly on the line, so the intervals use ordinary standard errors."
        )

    st.subheader("Formula")
    st.markdown("\n".join(f"- {line}" for line in formula_lines(model)))
    fill_bits = []
    for key, value in model.fills.items():
        label = next(feature.label for feature in FEATURES if feature.key == key)
        if key == "area_price_euros":
            fill_bits.append(f"{label.lower()} {value:,.0f} €")
        else:
            fill_bits.append(f"{label.lower()} {value:,.0f}")
    fill_note = ""
    if fill_bits:
        fill_note = (
            " A blank number is set to the average of the stated values, then the not-stated "
            f"amount is added. Those averages are: {', '.join(fill_bits)}."
        )
    st.caption(
        "The intercept is the asking price when living area, age, rooms, bathrooms "
        "and the Planungsraum average are all zero, every amenity is a stated no, the "
        "energy class "
        f"is {model.reference}, and the flat is {model.rental_reference}. "
        "It anchors the formula. The worked example below applies the "
        f"same euros to one real listing.{fill_note}"
    )

    effects = model.terms.loc[model.terms["key"] != "intercept"].copy()
    st.subheader("Euro effects")
    interval_note = (
        "Intervals use heteroscedasticity-robust standard errors, because expensive flats "
        "miss by more euros than cheap ones."
        if model.robust
        else "Intervals use ordinary standard errors."
    )
    st.caption(f"95% confidence interval. {interval_note}")
    st.dataframe(
        effects,
        use_container_width=True,
        hide_index=True,
        column_config={
            "key": None,
            "phrase": None,
            "group": st.column_config.TextColumn("Group"),
            "label": st.column_config.TextColumn("Feature"),
            "coefficient": st.column_config.NumberColumn("Euro effect", format="%.2f €"),
            "ci_low": st.column_config.NumberColumn("Low", format="%.2f €"),
            "ci_high": st.column_config.NumberColumn("High", format="%.2f €"),
            "p_value": st.column_config.NumberColumn("p-value", format="%.3f"),
        },
    )

    st.subheader("Predicted and asking price")
    st.caption(
        "Each point is a listing used in the fit. The line is where the asking price "
        "equals the prediction. A point under the line is listed for less than the model expects. "
        "A point opens that flat’s detail page in a new tab."
    )
    st.altair_chart(prediction_chart(model.listings), use_container_width=True)

    st.subheader("One listing")
    listings = model.listings.copy()
    listings["_label"] = [
        (
            f"{title} ({identifier}) — {price:,.0f} €"
            if isinstance(title, str) and title.strip()
            else f"Listing {identifier} — {price:,.0f} €"
        )
        for title, identifier, price in zip(
            listings["title"], listings["id"], listings["price"], strict=True
        )
    ]
    choice = st.selectbox(
        "Listing",
        list(listings.index),
        format_func=listings["_label"].to_dict().get,
    )
    table = contributions(model, choice)
    chosen = listings.loc[choice]
    gap = float(chosen["price"]) - float(chosen["predicted"])
    figures = st.columns(3)
    figures[0].metric("Asking price", f"{float(chosen['price']):,.0f} €")
    figures[1].metric("Predicted", f"{float(chosen['predicted']):,.0f} €")
    figures[2].metric("Asking − predicted", f"{gap:,.0f} €")
    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
        column_config={
            "term": st.column_config.TextColumn("Term"),
            "input": st.column_config.TextColumn("This flat"),
            "euros": st.column_config.NumberColumn("Euros", format="%.0f €"),
        },
    )
    st.caption("The euros in this table add up to the predicted price.")

st.subheader("Missing information by source")
st.caption(
    "Share of that source's listings where the field is blank. "
    "The number in the column title is how many listings the source has."
)
rates = missing_rates_by_source(loaded(active_only))
if rates.empty:
    st.caption("No source is recorded on these listings.")
else:
    percent = {
        column: st.column_config.NumberColumn(column, format="%.1f%%")
        for column in rates.columns
        if column != "information"
    }
    st.dataframe(
        rates,
        use_container_width=True,
        hide_index=True,
        column_config={"information": st.column_config.TextColumn("Information"), **percent},
    )
