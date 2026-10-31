"""Scoring dashboard: how each score is built, its weights, and price against score."""

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st
from listing_detail import open_from_chart
from listing_filters import apply_listing_filters
from listing_tooltip import CHART_FIELDS, attach_listing_hover

from immo_scanner.explore.data import load_features, load_plr_hover_stats
from immo_scanner.explore.prices import load_average_price_per_m2, merge_hover_prices
from immo_scanner.explore.score_weights import load_weights, save_weights, weights_version
from immo_scanner.explore.scoring import (
    GROUPS,
    SUBSCORES,
    Weights,
    apply_weights,
    effective_shares,
    subscores_of,
)

alt.data_transformers.disable_max_rows()

SCORES = (
    ("flat_score", "Flat score"),
    ("building_score", "Building score"),
    ("flat_and_building", "Flat and building"),
    ("total_score", "Total score"),
    ("distance_score", "Distance score"),
    ("neighborhood_score", "Neighbourhood score"),
    ("school_type_score", "Schultypisierung"),
    ("crime_score", "Crime score"),
    ("incident_score", "Racist incidents"),
)
PRICES = (
    ("price", "Price (€)"),
    ("price_per_m2", "€/m²"),
)
COLOURS = (
    ("bezirk", "Bezirk"),
    ("source", "Source"),
)
SIZE_SCORES = frozenset({"flat_score", "flat_and_building"})
FLAT_CAPTION = (
    "The line is the straight fit. Points under it cost less than this score usually does. "
    "Flat score rises with rooms and living area (floor area score is living area / 75, uncapped), "
    "so price against flat score mostly restates size. "
    "€/m² is the comparison that asks whether the score is priced per square metre."
)
OTHER_CAPTION = (
    "The line is the straight fit. Points under it cost less than this score usually does."
)
WEIGHT_MAX = 5.0
WEIGHT_STEP = 0.1
PREVIEW_ROWS = 15
SAVED_FLAG = "score_weights_saved"


@st.cache_data(ttl=600)
def features(active_only: bool, weights: str) -> pd.DataFrame:
    return load_features(active_only=active_only)


@st.cache_data(ttl=600)
def plr_hover_stats() -> pd.DataFrame:
    return merge_hover_prices(load_plr_hover_stats(), load_average_price_per_m2())


def flat_and_building(frame: pd.DataFrame, weights: Weights) -> pd.Series:
    """Weighted mean of the two group scores, skipping a missing one."""
    pairs = [
        (pd.to_numeric(frame[column], errors="coerce"), weights.group(column))
        for column in ("flat_score", "building_score")
        if weights.group(column) > 0
    ]
    if not pairs:
        return pd.Series(np.nan, index=frame.index)
    numerator = sum(values.fillna(0.0) * weight for values, weight in pairs)
    denominator = sum(values.notna() * weight for values, weight in pairs)
    return numerator / denominator.where(denominator > 0)


def fit_line(scores: pd.Series, prices: pd.Series) -> tuple[float, float] | None:
    """Slope and intercept of price on score. None when the score does not vary."""
    if len(scores) < 2 or scores.nunique(dropna=True) < 2:
        return None
    slope, intercept = np.polyfit(scores.to_numpy(dtype=float), prices.to_numpy(dtype=float), 1)
    if not np.isfinite(slope) or not np.isfinite(intercept):
        return None
    return float(slope), float(intercept)


def trend_frame(scores: pd.Series, slope: float, intercept: float) -> pd.DataFrame:
    low = float(scores.min())
    high = float(scores.max())
    return pd.DataFrame(
        {
            "score": [low, high],
            "price_value": [intercept + slope * low, intercept + slope * high],
        }
    )


def _x(score_label: str) -> alt.X:
    return alt.X("score:Q", title=score_label, scale=alt.Scale(zero=False))


def _y(price_label: str) -> alt.Y:
    return alt.Y("price_value:Q", title=price_label, scale=alt.Scale(zero=False))


def _listing_tooltip(axis_title: str | None) -> list[alt.Tooltip]:
    items: list[alt.Tooltip] = []
    for column, title in CHART_FIELDS:
        items.append(alt.Tooltip(f"{column}:N", title=title))
        if column == "score_label" and axis_title:
            items.append(alt.Tooltip("score:Q", title=axis_title, format=".2f"))
    return items


def score_chart(
    points: pd.DataFrame,
    trend: pd.DataFrame | None,
    score_label: str,
    price_label: str,
    colour_name: str,
    axis_title: str | None,
) -> alt.LayerChart | alt.Chart:
    # fields=["id"] so a click on this layered chart returns the listing, not a
    # Vega row number, and a click on the trend line selects nothing.
    picked = alt.selection_point("listing", fields=["id"], empty=False, toggle=False)
    circles = (
        alt.Chart(points)
        .mark_circle(opacity=0.75, size=64)
        .encode(
            x=_x(score_label),
            y=_y(price_label),
            color=alt.Color("colour:N", title=colour_name),
            size=alt.condition(picked, alt.value(140), alt.value(64)),
            opacity=alt.condition(picked, alt.value(1), alt.value(0.75)),
            tooltip=_listing_tooltip(axis_title),
        )
        .add_params(picked)
    )
    if trend is None:
        return circles
    line = alt.Chart(trend).mark_line(color="#333333").encode(x=_x(score_label), y=_y(price_label))
    return circles + line


# Weights in session state --------------------------------------------------------


def _group_key(column: str) -> str:
    return f"weight_group_{column}"


def _sub_key(column: str) -> str:
    return f"weight_sub_{column}"


def _set_state(weights: Weights) -> None:
    for group in GROUPS:
        st.session_state[_group_key(group.column)] = weights.group(group.column)
    for sub in SUBSCORES:
        st.session_state[_sub_key(sub.column)] = weights.subscore(sub.column)


def _init_state(saved: Weights) -> None:
    for group in GROUPS:
        st.session_state.setdefault(_group_key(group.column), saved.group(group.column))
    for sub in SUBSCORES:
        st.session_state.setdefault(_sub_key(sub.column), saved.subscore(sub.column))


def _draft() -> Weights:
    return Weights(
        groups={
            group.column: round(float(st.session_state[_group_key(group.column)]), 2)
            for group in GROUPS
        },
        subscores={
            sub.column: round(float(st.session_state[_sub_key(sub.column)]), 2)
            for sub in SUBSCORES
        },
    )


def _save() -> None:
    save_weights(_draft())
    st.session_state[SAVED_FLAG] = True


def _slider(label: str, key: str, help_text: str | None = None) -> None:
    st.slider(label, 0.0, WEIGHT_MAX, step=WEIGHT_STEP, key=key, help=help_text)


# Dashboard sections --------------------------------------------------------------


def _formula(draft: Weights) -> str:
    terms = [(draft.group(group.column), group.label) for group in GROUPS]
    used = [(weight, label) for weight, label in terms if weight > 0]
    if not used:
        return r"\text{total} = \text{undefined (all group weights are 0)}"
    top = " + ".join(rf"{weight:g} \cdot \text{{{label}}}" for weight, label in used)
    bottom = " + ".join(f"{weight:g}" for weight, _ in used)
    return rf"\text{{total}} = \frac{{{top}}}{{{bottom}}}"


def _group_share_chart(draft: Weights) -> alt.Chart:
    total = sum(draft.group(group.column) for group in GROUPS)
    frame = pd.DataFrame(
        [
            {
                "Group": group.label,
                "Share": draft.group(group.column) / total if total > 0 else 0.0,
            }
            for group in GROUPS
        ]
    )
    return (
        alt.Chart(frame)
        .mark_bar(color="#f6c400")
        .encode(
            y=alt.Y("Group:N", sort=None, title=None),
            x=alt.X("Share:Q", title="Share of total", axis=alt.Axis(format="%")),
            tooltip=["Group", alt.Tooltip("Share:Q", format=".0%")],
        )
        .properties(height=160)
    )


def _subscore_table(df: pd.DataFrame, group_column: str, draft: Weights) -> pd.DataFrame:
    shares = effective_shares(draft)
    subs = subscores_of(group_column)
    in_group = sum(draft.subscore(sub.column) for sub in subs)
    rows = []
    for sub in subs:
        values = (
            pd.to_numeric(df[sub.column], errors="coerce")
            if sub.column in df.columns
            else pd.Series(dtype=float)
        )
        known = values.dropna()
        weight = draft.subscore(sub.column)
        rows.append(
            {
                "Sub-score": sub.label,
                "Rule": sub.rule,
                "Weight": weight,
                "Share of group": weight / in_group if in_group > 0 else 0.0,
                "Share of total": shares[sub.column],
                "Coverage": len(known) / len(df) if len(df) else None,
                "Mean": known.mean() if not known.empty else None,
                "Min": known.min() if not known.empty else None,
                "Max": known.max() if not known.empty else None,
                "Scale": "uncapped" if sub.max_value is None else f"0–{sub.max_value:g}",
            }
        )
    return pd.DataFrame(rows)


def _histograms(df: pd.DataFrame, group_column: str) -> alt.FacetChart | None:
    frames = []
    for sub in subscores_of(group_column):
        if sub.column not in df.columns:
            continue
        values = pd.to_numeric(df[sub.column], errors="coerce").dropna()
        if not values.empty:
            frames.append(pd.DataFrame({"Sub-score": sub.label, "Value": values.to_numpy()}))
    if not frames:
        return None
    long = pd.concat(frames, ignore_index=True)
    return (
        alt.Chart(long)
        .mark_bar(color="#f6c400")
        .encode(
            x=alt.X("Value:Q", bin=alt.Bin(maxbins=20), title=None),
            y=alt.Y("count():Q", title="Listings"),
        )
        .properties(width=220, height=120)
        .facet(facet=alt.Facet("Sub-score:N", sort=None, title=None), columns=3)
        .resolve_scale(x="independent", y="independent")
    )


def _scale_warning(df: pd.DataFrame, draft: Weights) -> None:
    lines = []
    for sub in SUBSCORES:
        if (sub.max_value is not None and sub.max_value <= 1) or draft.subscore(sub.column) <= 0:
            continue
        observed = (
            pd.to_numeric(df[sub.column], errors="coerce").max()
            if sub.column in df.columns
            else np.nan
        )
        cap = "no cap" if sub.max_value is None else f"up to {sub.max_value:g}"
        seen = "" if pd.isna(observed) else f", highest now {observed:.2f}"
        lines.append(f"- **{sub.label}** ({cap}{seen})")
    if lines:
        st.warning(
            "These sub-scores can go above 1, so they pull their group harder than "
            "their weight alone suggests:\n" + "\n".join(lines)
        )


def _ranking_preview(df: pd.DataFrame, draft: Weights) -> None:
    """`df` carries the saved scores; the draft ones are computed here."""
    scored = apply_weights(df, draft)
    frame = pd.DataFrame(
        {
            "id": df["id"],
            "title": df["title"].fillna("") if "title" in df else "",
            "bezirk": df["bezirk"].fillna("") if "bezirk" in df else "",
            "draft": pd.to_numeric(scored["total_score"], errors="coerce"),
            "saved": pd.to_numeric(df["total_score"], errors="coerce"),
        }
    )
    frame["draft_rank"] = frame["draft"].rank(ascending=False, method="min")
    frame["saved_rank"] = frame["saved"].rank(ascending=False, method="min")
    frame["change"] = frame["saved_rank"] - frame["draft_rank"]
    top = frame.dropna(subset=["draft"]).sort_values("draft", ascending=False).head(PREVIEW_ROWS)
    saved_top = set(frame.dropna(subset=["saved"]).nlargest(PREVIEW_ROWS, "saved")["id"])
    entered = len(set(top["id"]) - saved_top)
    correlation = frame["draft_rank"].corr(frame["saved_rank"])

    metrics = st.columns(2)
    metrics[0].metric(f"New in the top {PREVIEW_ROWS}", f"{entered}")
    metrics[1].metric(
        "Rank correlation with saved",
        "–" if pd.isna(correlation) else f"{correlation:.2f}",
        help="Spearman correlation between the ranking with these weights and the saved one.",
    )
    st.dataframe(
        top[["title", "bezirk", "draft", "saved", "draft_rank", "change"]],
        hide_index=True,
        width="stretch",
        column_config={
            "title": st.column_config.TextColumn("Title"),
            "bezirk": st.column_config.TextColumn("Bezirk"),
            "draft": st.column_config.NumberColumn("Score with these weights", format="%.2f"),
            "saved": st.column_config.NumberColumn("Saved score", format="%.2f"),
            "draft_rank": st.column_config.NumberColumn("Rank", format="%d"),
            "change": st.column_config.NumberColumn(
                "Places gained", format="%+d", help="Saved rank minus rank with these weights."
            ),
        },
    )


def render_weights(df: pd.DataFrame, saved: Weights) -> None:
    _init_state(saved)
    draft = _draft()
    shares = effective_shares(draft)

    st.markdown(
        "Every listing gets **sub-scores** from its data. The sub-scores of a category are "
        "combined into a **group score** by weighted mean, and the four group scores into "
        "the **total score** the same way. A missing input is left out and the remaining "
        "weights are rescaled; it does not count as 0. A weight of 0 switches an input off. "
        "Saved weights apply to every page: the Flats table and map, Flat detail and the price "
        "chart. Metabase reads the database directly and keeps the equal-weight total."
    )

    buttons = st.columns([1, 1, 1, 3])
    changed = draft.to_dict() != saved.to_dict()
    buttons[0].button("Save weights", type="primary", on_click=_save, disabled=not changed)
    buttons[1].button("Discard changes", on_click=_set_state, args=(saved,), disabled=not changed)
    buttons[2].button("Reset to equal weights", on_click=_set_state, args=(Weights(),))
    if changed:
        buttons[3].info("Unsaved changes. Other pages still use the saved weights.")

    st.subheader("Total score")
    st.latex(_formula(draft))
    left, right = st.columns([3, 2])
    with left:
        slider_columns = st.columns(len(GROUPS))
        for column, group in zip(slider_columns, GROUPS, strict=True):
            with column:
                _slider(group.label, _group_key(group.column))
    with right:
        st.altair_chart(_group_share_chart(draft), width="stretch")

    _scale_warning(df, draft)

    st.subheader("Categories")
    for group in GROUPS:
        group_share = sum(shares[sub.column] for sub in subscores_of(group.column))
        values = (
            pd.to_numeric(df[group.column], errors="coerce")
            if group.column in df
            else pd.Series(dtype=float)
        )
        mean = "–" if values.dropna().empty else f"{values.mean():.2f}"
        title = (
            f"{group.label} · weight {draft.group(group.column):g} · "
            f"{group_share:.0%} of total · saved mean {mean}"
        )
        with st.expander(title):
            subs = subscores_of(group.column)
            columns = st.columns(min(len(subs), 4))
            for index, sub in enumerate(subs):
                with columns[index % len(columns)]:
                    _slider(sub.label, _sub_key(sub.column), sub.rule)
            st.dataframe(
                _subscore_table(df, group.column, draft),
                hide_index=True,
                width="stretch",
                column_config={
                    "Weight": st.column_config.NumberColumn(format="%.1f"),
                    "Share of group": st.column_config.NumberColumn(format="percent"),
                    "Share of total": st.column_config.NumberColumn(
                        format="percent",
                        help="Share of the total when every input is known.",
                    ),
                    "Coverage": st.column_config.NumberColumn(
                        format="percent", help="Listings on this page with the input known."
                    ),
                    "Mean": st.column_config.NumberColumn(format="%.2f"),
                    "Min": st.column_config.NumberColumn(format="%.2f"),
                    "Max": st.column_config.NumberColumn(format="%.2f"),
                },
            )
            chart = _histograms(df, group.column)
            if chart is not None:
                st.altair_chart(chart)
    st.caption("Formulas and data sources in docs/scoring.md.")

    st.subheader("Ranking with these weights")
    if "id" not in df.columns:
        st.caption("No listing ids to rank.")
        return
    _ranking_preview(df, draft)


def render_prices(df: pd.DataFrame, saved: Weights) -> None:
    df = df.copy()
    df["flat_and_building"] = flat_and_building(df, saved)

    score_label = dict(SCORES)[score_column]
    price_label = dict(PRICES)[price_column]

    chart = df.dropna(subset=[score_column, price_column, "id"]).copy()
    chart["id"] = chart["id"].astype(int)
    chart["score"] = chart[score_column]
    chart["price_value"] = chart[price_column]
    chart["title"] = chart["title"].fillna("")
    chart["bezirk"] = chart["bezirk"].fillna("")
    chart["colour"] = chart[colour_column].fillna("Unknown").replace("", "Unknown")
    chart = attach_listing_hover(chart, plr_hover_stats())

    st.caption(FLAT_CAPTION if score_column in SIZE_SCORES else OTHER_CAPTION)

    if chart.empty:
        st.info("No listings have both this score and a price.")
        return

    fit = fit_line(chart["score"], chart["price_value"])
    trend = None
    if fit is not None:
        slope, intercept = fit
        chart["fitted"] = intercept + slope * chart["score"]
        chart["gap"] = chart["price_value"] - chart["fitted"]
        trend = trend_frame(chart["score"], slope, intercept)

    correlation = chart["score"].corr(chart["price_value"])
    metrics = st.columns(2)
    metrics[0].metric("Listings", f"{len(chart):,}")
    metrics[1].metric("Correlation", "–" if pd.isna(correlation) else f"{correlation:.2f}")
    st.caption(
        "Pearson correlation for the points on the chart. "
        "Listings missing this score or price are left off. Scores use the saved weights."
    )

    st.caption("Click a point to open the listing.")
    axis_title = None if score_column == "total_score" else score_label
    score_click = st.altair_chart(
        score_chart(
            chart, trend, score_label, price_label, dict(COLOURS)[colour_column], axis_title
        ),
        width="stretch",
        on_select="rerun",
        selection_mode="listing",
        key="score-scatter",
    )
    open_from_chart(df, score_click.selection)

    st.subheader("Cheaper than the trend")
    if trend is None:
        st.caption("A trend line needs at least two different scores.")
        return

    below = chart.loc[chart["gap"] < 0].sort_values("gap")
    if below.empty:
        st.caption("No listing is cheaper than the trend.")
        return

    gap_label = "Gap (€)" if price_column == "price" else "Gap (€/m²)"
    table = below[["title", "bezirk", "score", "price", "price_per_m2", "gap", "url"]]
    st.dataframe(
        table,
        width="stretch",
        hide_index=True,
        column_config={
            "title": st.column_config.TextColumn("Title"),
            "bezirk": st.column_config.TextColumn("Bezirk"),
            "score": st.column_config.NumberColumn(score_label, format="%.2f"),
            "price": st.column_config.NumberColumn("Price", format="%.0f €"),
            "price_per_m2": st.column_config.NumberColumn("€/m²", format="%.0f"),
            "gap": st.column_config.NumberColumn(
                gap_label,
                format="%.0f",
                help="Actual price minus the fitted price. "
                "More negative is cheaper for this score.",
            ),
            "url": st.column_config.LinkColumn("Link", display_text="open"),
        },
    )


if st.session_state.pop(SAVED_FLAG, False):
    st.toast("Weights saved. Every page now uses them.")

st.sidebar.header("Price chart")
score_column = st.sidebar.selectbox(
    "Score",
    [column for column, _ in SCORES],
    format_func=dict(SCORES).get,
)
price_column = st.sidebar.selectbox(
    "Price",
    [column for column, _ in PRICES],
    format_func=dict(PRICES).get,
)
colour_column = st.sidebar.selectbox(
    "Colour",
    [column for column, _ in COLOURS],
    format_func=dict(COLOURS).get,
)

st.sidebar.header("Filters")
include_removed = st.sidebar.checkbox("Include removed listings", value=False)
saved_weights = load_weights()
df = features(active_only=not include_removed, weights=weights_version())

st.title("Scores")

if df.empty:
    st.info("No data yet. Run `immo crawl` and `immo enrich` first.")
    st.stop()

missing = [
    column
    for column, _ in SCORES
    if column != "flat_and_building" and column not in df.columns
]
if missing or "price" not in df.columns or "price_per_m2" not in df.columns:
    st.info("Listing scores are not loaded yet. Refresh listing features after scoring.")
    st.stop()

df = apply_listing_filters(df)
if df.empty:
    st.info("No listings match these filters.")
    st.stop()

weights_tab, prices_tab = st.tabs(["Scoring and weights", "Scores and prices"])
with weights_tab:
    render_weights(df, saved_weights)
with prices_tab:
    render_prices(df, saved_weights)
