"""Crawler fields that are blank, by column and by source."""

import altair as alt
import pandas as pd
import streamlit as st

from immo_scanner.explore.data import load_features
from immo_scanner.explore.gaps import (
    LOCATION_STATES,
    ExposeCoverage,
    column_gaps,
    gaps_by_source,
    listings_from,
    load_expose_coverage,
    load_location_coverage,
    location_state_counts,
    missing_listings,
    source_order,
    unlocated_listings,
)
from immo_scanner.explore.listing_copy import source_label

alt.data_transformers.disable_max_rows()


@st.cache_data(ttl=600)
def loaded(active_only: bool) -> pd.DataFrame:
    return load_features(active_only=active_only)


@st.cache_data(ttl=600)
def coverage() -> list[ExposeCoverage]:
    return load_expose_coverage()


@st.cache_data(ttl=600)
def locations(active_only: bool) -> pd.DataFrame:
    return location_state_counts(load_location_coverage(active_only))


@st.cache_data(ttl=600)
def unlocated(source: str, state: str, active_only: bool) -> tuple[pd.DataFrame, int]:
    return unlocated_listings(source, state, active_only)


def gap_chart(gaps: pd.DataFrame) -> alt.Chart:
    return (
        alt.Chart(gaps)
        .mark_bar()
        .encode(
            x=alt.X("missing_pct:Q", title="Missing (%)", scale=alt.Scale(domain=[0, 100])),
            y=alt.Y("field:N", sort="-x", title=None),
            tooltip=[
                alt.Tooltip("field:N", title="Field"),
                alt.Tooltip("filled:Q", title="Filled", format=",.0f"),
                alt.Tooltip("missing:Q", title="Missing", format=",.0f"),
                alt.Tooltip("missing_pct:Q", title="Missing (%)", format=".1f"),
            ],
        )
        .properties(height=max(160, 22 * len(gaps)))
    )


def gap_table(gaps: pd.DataFrame) -> None:
    st.dataframe(
        gaps,
        use_container_width=True,
        hide_index=True,
        column_config={
            "key": None,
            "field": st.column_config.TextColumn("Field"),
            "filled": st.column_config.NumberColumn("Filled", format="%d"),
            "missing": st.column_config.NumberColumn("Missing", format="%d"),
            "missing_pct": st.column_config.NumberColumn("Missing %", format="%.1f%%"),
        },
    )


def listing_table(rows: pd.DataFrame) -> None:
    shown = rows.copy()
    if "id" in shown.columns:
        shown["flat"] = shown["id"].map(lambda listing_id: f"/flat?listing={int(listing_id)}")
    order = [
        column
        for column in ("flat", "url", "title", "address", "geocoded_query", "external_id", "id")
        if column in shown.columns
    ]
    st.dataframe(
        shown[order],
        use_container_width=True,
        hide_index=True,
        column_config={
            "flat": st.column_config.LinkColumn("Flat", display_text="Open", width="small"),
            "url": st.column_config.LinkColumn("Portal", display_text="Open", width="small"),
            "title": st.column_config.TextColumn("Title", width="large"),
            "address": st.column_config.TextColumn("Address", width="medium"),
            "geocoded_query": st.column_config.TextColumn("Last geocoder query", width="medium"),
            "external_id": st.column_config.TextColumn("External id", width="medium"),
            "id": st.column_config.NumberColumn("Listing", format="%d", width="small"),
        },
    )


def location_section(active_only: bool) -> None:
    st.subheader("Location and neighbourhood")
    st.caption(
        "Demographics, indicators and origins need a point inside a Planungsraum. "
        "`immo crawl` and `immo enrich` geocode listings without one and refresh the view."
    )
    table = locations(active_only)
    totals = table.set_index("key")["Total"]
    figures = st.columns(4)
    figures[0].metric("Listings", f"{int(totals.sum()):,}")
    figures[1].metric(
        "Without a point", f"{int(totals['never_tried'] + totals['not_found']):,}"
    )
    figures[2].metric("Not in the view yet", f"{int(totals['not_in_view']):,}")
    figures[3].metric("Outside every Planungsraum", f"{int(totals['outside_lor']):,}")
    sources = [column for column in table.columns if column not in ("key", "state", "Total")]
    st.dataframe(
        table.drop(columns="key"),
        use_container_width=True,
        hide_index=True,
        column_config={
            "state": st.column_config.TextColumn("Location"),
            **{
                name: st.column_config.NumberColumn(source_label(name), format="%d")
                for name in sources
            },
            "Total": st.column_config.NumberColumn("Total", format="%d"),
        },
    )
    open_states = [key for key, _ in LOCATION_STATES if key != "located" and totals[key]]
    if not open_states:
        st.caption("Every listing in this sample has a Planungsraum.")
        return
    with_gaps = [
        name
        for name in sources
        if table.set_index("key").loc[open_states, name].sum() > 0
    ]
    labels = dict(LOCATION_STATES)
    picks = st.columns(2)
    source = picks[0].selectbox(
        "Source", with_gaps, format_func=source_label, key="location-source"
    )
    state = picks[1].selectbox(
        "Location", open_states, format_func=labels.get, key="location-state"
    )
    rows, total = unlocated(source, state, active_only)
    if total == 0:
        st.caption("No listing from this source is in this state.")
        return
    st.caption(
        f"{len(rows):,} of {total:,} listings. The last geocoder query is the most general "
        "one tried when nothing was found."
    )
    listing_table(rows)


def expose_section(item: ExposeCoverage) -> None:
    if item.listings == 0 and item.rows == 0:
        return
    st.markdown(f"**{item.label}**")
    figures = st.columns(3 if item.awaiting_detail is None else 4)
    figures[0].metric("Active listings", f"{item.listings:,}")
    figures[1].metric("No exposé stored", f"{item.without_expose:,}")
    figures[2].metric("Rows stored", f"{item.rows:,}")
    if item.awaiting_detail is not None:
        figures[3].metric("Exposé not fetched", f"{item.awaiting_detail:,}")
        st.caption(
            "ThinkImmo stores a row from the search crawl. "
            "Exposé fields stay blank until that fetch runs."
        )
    if item.gaps.empty:
        st.caption("No exposé rows stored. These listings still need a detail fetch.")
        return
    gap_table(item.gaps)


st.sidebar.header("Sample")
include_removed = st.sidebar.checkbox("Include removed listings", value=False)
active_only = not include_removed
frame = loaded(active_only)

st.title("Data gaps")
st.markdown(
    """
Share of listings where a crawler field is blank. These are values the list crawl
or the detail fetch stores. Scores, elections, demographics and commute are left out
of the column counts; the first section shows which listings still lack a location,
and with it the neighbourhood data. A blank cell is null, or text that is empty,
“none” or “nan”. A stated no on a yes/no field counts as present.
"""
)

location_section(active_only)

if frame.empty:
    st.info("No listings yet. Run `immo crawl` first.")
else:
    gaps = column_gaps(frame)
    st.subheader("By column")
    st.caption("Largest gaps first, across every source in this sample.")
    if gaps.empty:
        st.caption("None of the crawler fields are on these listings.")
    else:
        st.altair_chart(gap_chart(gaps), use_container_width=True)
        gap_table(gaps)

    st.subheader("By source")
    st.caption(
        "Share of that source's listings where the field is blank. "
        "Total is the same share across every listing in this sample. "
        "The number in the column title is how many listings that column covers. "
        "The merged listing can already hold a value filled in from another portal, "
        "including ThinkImmo."
    )
    rates = gaps_by_source(frame)
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
            column_config={
                "information": st.column_config.TextColumn("Field"),
                **percent,
            },
        )

    st.subheader("Listings to check")
    st.caption(
        "Open the portal page to see whether the site published the value. "
        "The flat link opens it in this app."
    )
    sources = source_order(frame)
    if not sources or gaps.empty:
        st.caption("No crawler field is available to check.")
    else:
        picks = st.columns(2)
        source = picks[0].selectbox("Source", sources, format_func=source_label)
        choices = column_gaps(listings_from(frame, source))
        if choices.empty:
            st.caption("No crawler field is available to check.")
        else:
            labels = dict(zip(choices["key"], choices["field"], strict=True))
            field = picks[1].selectbox(
                "Field",
                list(choices["key"]),
                format_func=labels.get,
                key=f"gap-field-{source}",
            )
            rows, total = missing_listings(frame, source, field)
            if total == 0:
                st.caption("This field is filled on every listing from this source.")
            else:
                shown = len(rows)
                st.caption(f"{shown:,} of {total:,} listings where this field is blank.")
                listing_table(rows)

st.subheader("On the crawler's own table")
st.caption(
    "Missing percent among rows already stored by that crawler. "
    "Active listings with no row still need `immo details`. "
    "A stated no on a yes/no field counts as present."
)
for item in coverage():
    expose_section(item)
