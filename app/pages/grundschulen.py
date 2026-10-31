"""Grundschulen: map, all-day offer, and Bezirk comparison."""

import json

import pandas as pd
import pydeck as pdk
import streamlit as st

from immo_scanner.explore.data import load_einschulbereiche, load_grundschulen

GANZTAG_COLOURS = {
    "gebunden": [32, 110, 186, 230],
    "offen": [230, 150, 20, 230],
}
MISSING = [140, 140, 140, 220]
GANZTAG_LABELS = {"gebunden": "Bound all-day", "offen": "Open afternoon care"}


@st.cache_data(ttl=3600)
def grundschulen() -> pd.DataFrame:
    return load_grundschulen()


@st.cache_data(ttl=3600)
def catchments() -> pd.DataFrame:
    return load_einschulbereiche()


def _text(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)) or pd.isna(value):
        return "–"
    return str(value)


def _mark(size: int, color: str) -> str:
    return (
        f"<span style='display:inline-block;width:{size}px;height:{size}px;"
        f"border-radius:50%;background:{color};vertical-align:middle'></span>"
    )


def _geojson(frame: pd.DataFrame) -> dict:
    features = []
    for row in frame.itertuples(index=False):
        geometry = row.geojson
        if isinstance(geometry, str):
            geometry = json.loads(geometry)
        if not geometry:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {"name": row.name or ""},
            }
        )
    return {"type": "FeatureCollection", "features": features}


st.sidebar.header("Filters")
df = grundschulen()
if df.empty:
    st.info("No Grundschulen yet. Run `immo load-reference grundschulen`.")
    st.stop()

search = st.sidebar.text_input("Search school")
if search:
    df = df[df["name"].fillna("").str.contains(search, case=False, regex=False)]

bezirke = sorted(df["bezirk"].dropna().unique())
selected_bezirke = st.sidebar.multiselect("Bezirk", bezirke)
if selected_bezirke:
    df = df[df["bezirk"].isin(selected_bezirke)]

arten = sorted(df["schulart"].dropna().unique())
selected_arten = st.sidebar.multiselect("Schulart", arten)
if selected_arten:
    df = df[df["schulart"].isin(selected_arten)]

traeger = sorted(df["traeger"].dropna().unique())
selected_traeger = st.sidebar.multiselect("Träger", traeger)
if selected_traeger:
    df = df[df["traeger"].isin(selected_traeger)]

ganztag_options = ["gebunden", "offen"]
selected_ganztag = st.sidebar.multiselect(
    "Ganztag",
    ganztag_options,
    format_func=lambda value: GANZTAG_LABELS[value],
    help=(
        "Bound means the all-day programme is part of the school day. "
        "Open means afternoon care is optional."
    ),
)
if selected_ganztag:
    df = df[df["ganztag"].isin(selected_ganztag)]

if st.sidebar.checkbox("Europa-Schule"):
    df = df[df["europa_schule"].fillna(False)]
if st.sidebar.checkbox("Bilingual"):
    df = df[df["bilingual"].fillna(False)]
show_catchments = st.sidebar.checkbox(
    "Show Einschulbereiche",
    help=(
        "Catchment of the public school a child is assigned to. "
        "Heavy when every polygon is drawn."
    ),
)

st.title("Berlin Grundschulen")
st.caption(
    "Sites from the Berlin school map. Offers from the Schulporträts. "
    "Colour is the all-day form, not a result: Berlin does not publish VERA-3 by school. "
    "gebunden is a bound all-day school. offen is optional afternoon care."
)

summary = st.columns(4)
summary[0].metric("Schools", f"{len(df):,}")
summary[1].metric("Bound all-day", f"{int(df['ganztag'].eq('gebunden').sum()):,}")
summary[2].metric("Europa-Schule", f"{int(df['europa_schule'].fillna(False).sum()):,}")
ratio = (
    df["students_per_teacher"].dropna()
    if "students_per_teacher" in df.columns
    else pd.Series(dtype=float)
)
summary[3].metric(
    "Median students / teacher",
    f"{ratio.median():.1f}" if not ratio.empty else "–",
)

if df.empty:
    st.info("No schools match these filters.")
    st.stop()

map_df = df.dropna(subset=["lat", "lon"]).copy()
if not map_df.empty:
    map_df["color"] = [
        GANZTAG_COLOURS.get(value, MISSING) for value in map_df["ganztag"]
    ]
    map_df["ganztag_label"] = map_df["ganztag"].map(lambda value: GANZTAG_LABELS.get(value, "–"))
    map_df["europa_label"] = [
        partner if pd.notna(partner) and partner else ("yes" if flag else "–")
        for flag, partner in zip(map_df["europa_schule"], map_df["partner_language"], strict=True)
    ]
    map_df["language_label"] = map_df["first_foreign_language"].map(_text)
    map_df["ratio_label"] = [
        "–" if pd.isna(value) else f"{value:.1f}" for value in map_df["students_per_teacher"]
    ]
    for column in ("bezirk", "ortsteil", "traeger", "schulart"):
        map_df[column] = map_df[column].fillna("")
    map_df["marker"] = "●"

st.markdown(
    f"""
    <div style="font-size:0.9rem;margin:0.2rem 0 0.6rem">
      {_mark(14, 'rgb(32,110,186)')} {GANZTAG_LABELS['gebunden']}
      <span style="margin-left:1rem">{_mark(14, 'rgb(230,150,20)')} {GANZTAG_LABELS['offen']}</span>
      <span style="margin-left:1rem">{_mark(14, 'rgb(140,140,140)')} no all-day form</span>
    </div>
    """,
    unsafe_allow_html=True,
)

layers = []
if show_catchments:
    selected = set(df["bsn"].dropna())
    areas = catchments()
    if not areas.empty:
        areas = areas[areas["bsn"].isin(selected)]
    collection = (
        _geojson(areas)
        if not areas.empty
        else {"type": "FeatureCollection", "features": []}
    )
    if collection["features"]:
        layers.append(
            pdk.Layer(
                "GeoJsonLayer",
                data=collection,
                stroked=True,
                filled=True,
                get_fill_color=[32, 110, 186, 28],
                get_line_color=[32, 90, 160, 160],
                line_width_min_pixels=1,
                pickable=False,
            )
        )
    else:
        st.caption("None of the selected schools have an Einschulbereich.")

if map_df.empty:
    st.info("None of the selected schools have a map location.")
else:
    layers.append(
        pdk.Layer(
            "TextLayer",
            data=map_df,
            get_position=["lon", "lat"],
            get_text="marker",
            get_size=18,
            get_color="color",
            get_text_anchor="'middle'",
            get_alignment_baseline="'center'",
            font_family="'Segoe UI Symbol'",
            character_set=("●",),
            size_units="'pixels'",
            pickable=True,
        )
    )
    st.pydeck_chart(
        pdk.Deck(
            layers=layers,
            initial_view_state=pdk.ViewState(latitude=52.52, longitude=13.40, zoom=10.3),
            map_style="light",
            tooltip={
                "html": "<b>{name}</b><br/>{bezirk} · {ortsteil}<br/>"
                "{schulart} · {traeger}<br/>"
                "Ganztag: {ganztag_label}<br/>"
                "Europa-Schule: {europa_label}<br/>"
                "First foreign language: {language_label}<br/>"
                "Students / teacher: {ratio_label}"
            },
        ),
        use_container_width=True,
    )

st.subheader("All-day offer by Bezirk")
st.caption(
    "Schools in the current filter. A school with no published all-day form is counted as none."
)
ganztag = df.assign(form=df["ganztag"].map(lambda value: GANZTAG_LABELS.get(value, "None")))
counts = (
    ganztag.dropna(subset=["bezirk"])
    .groupby(["bezirk", "form"])
    .size()
    .unstack(fill_value=0)
)
order = [
    label
    for label in ("Bound all-day", "Open afternoon care", "None")
    if label in counts.columns
]
if counts.empty:
    st.caption("No Bezirk for the selected schools.")
else:
    st.bar_chart(counts[order])

table_columns = [
    "name", "bsn", "bezirk", "ortsteil", "schulart", "traeger", "ganztag",
    "europa_schule", "partner_language", "bilingual", "first_foreign_language",
    "students", "teachers", "students_per_teacher", "address",
]
st.dataframe(
    df[[column for column in table_columns if column in df.columns]].sort_values("name"),
    use_container_width=True,
    hide_index=True,
    column_config={
        "name": st.column_config.TextColumn("School"),
        "bsn": st.column_config.TextColumn("BSN"),
        "schulart": st.column_config.TextColumn("Schulart"),
        "traeger": st.column_config.TextColumn("Träger"),
        "ganztag": st.column_config.TextColumn("Ganztag"),
        "europa_schule": st.column_config.CheckboxColumn("Europa-Schule"),
        "partner_language": st.column_config.TextColumn("Europa language"),
        "bilingual": st.column_config.CheckboxColumn("Bilingual"),
        "first_foreign_language": st.column_config.TextColumn("First foreign language"),
        "students": st.column_config.NumberColumn("Students", format="%d"),
        "teachers": st.column_config.NumberColumn("Teachers", format="%d"),
        "students_per_teacher": st.column_config.NumberColumn("Students / teacher", format="%.1f"),
        "address": st.column_config.TextColumn("Address"),
    },
)
