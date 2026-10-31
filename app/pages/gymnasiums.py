"""Gymnasium statistics: map, demand, and Bezirk comparison."""

import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st

from immo_scanner.explore.data import load_gymnasiums

COLOUR_COLUMN = "abi_average"
COLOUR_FORMAT = "{:.2f}"
COLOUR_CAPTION = (
    "Abitur average. Green is a stronger result (lower average), relative to the schools shown."
)


@st.cache_data(ttl=3600)
def gymnasiums() -> pd.DataFrame:
    """Statistics plus intake year, Europa-Schule and profile flags."""
    return load_gymnasiums()


def _text(value: object, fmt: str) -> str:
    if pd.isna(value):
        return "–"
    return fmt.format(value)


def _joined_flags(frame: pd.DataFrame, pairs: tuple[tuple[str, str], ...]) -> list[str]:
    labels = []
    for _, row in frame.iterrows():
        present = [
            label
            for column, label in pairs
            if column in row.index and pd.notna(row[column]) and bool(row[column])
        ]
        labels.append(", ".join(present) if present else "–")
    return labels


def _with_year(value: object, fmt: str, year: object) -> str:
    text = _text(value, fmt)
    if text == "–" or pd.isna(year):
        return text
    return f"{text} ({year})"


_GREEN = (40, 176, 48)
_YELLOW = (246, 196, 0)
_RED = (214, 40, 40)
_MISSING = (140, 140, 140)


def _mix(start: tuple[int, int, int], end: tuple[int, int, int], share: float) -> list[int]:
    return [int(start[i] + (end[i] - start[i]) * share) for i in range(3)] + [220]


def traffic_color(position: float) -> list[int]:
    """Green at 0, yellow at 0.5, red at 1."""
    if position <= 0.5:
        return _mix(_GREEN, _YELLOW, position / 0.5)
    return _mix(_YELLOW, _RED, (position - 0.5) / 0.5)


def scale_colors(values: pd.Series) -> tuple[float, float, list[list[int]]]:
    """Traffic light from green (low) to red (high), clipped at the 5th and 95th percentile."""
    finite = values.astype(float).replace([np.inf, -np.inf], np.nan).dropna()
    missing = [[*_MISSING, 220]] * len(values)
    if finite.empty:
        return float("nan"), float("nan"), missing
    low, high = float(np.percentile(finite, 5)), float(np.percentile(finite, 95))
    if high == low:
        return low, high, missing
    scaled = ((values.astype(float) - low) / (high - low)).clip(0, 1)
    colors = [
        [*_MISSING, 220] if pd.isna(item) else traffic_color(float(item)) for item in scaled
    ]
    return low, high, colors


def _mark(size: int, color: str) -> str:
    return (
        f"<span style='display:inline-block;width:{size}px;height:{size}px;"
        f"border-radius:50%;background:{color};vertical-align:middle'></span>"
    )


def map_legend(low: float, high: float, fmt: str, caption: str, *, missing: bool) -> None:
    if np.isfinite(low) and np.isfinite(high):
        low_label, high_label = fmt.format(low), fmt.format(high)
    else:
        low_label, high_label = "low", "high"
    missing_label = ""
    if missing:
        swatch = _mark(12, "rgb(140,140,140)")
        missing_label = f"<span style='margin-left:1rem'>{swatch} no Abitur average</span>"
    st.markdown(
        f"""
        <div style="font-size:0.9rem;margin:0.2rem 0 0.6rem">
          <div style="display:flex;align-items:center;gap:0.6rem;max-width:420px">
            <span>{low_label}</span>
            <div style="flex:1;height:12px;border-radius:6px;background:linear-gradient(
              to right, rgb(40,176,48), rgb(246,196,0), rgb(214,40,40))"></div>
            <span>{high_label}</span>
          </div>
          <div style="color:#444;margin-top:0.15rem">{caption}</div>
          <div style="margin-top:0.35rem">
            {_mark(14, 'rgb(40,176,48)')} stronger{missing_label}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _metric(frame: pd.DataFrame, column: str, fmt: str) -> str:
    values = frame[column].dropna()
    if values.empty:
        return "–"
    return fmt.format(values.median())


st.sidebar.header("Filters")
df = gymnasiums()
if df.empty:
    st.info("No Gymnasium statistics yet. Run `immo load-reference gymnasium-stats`.")
    st.stop()

search = st.sidebar.text_input("Search school")
if search:
    df = df[df["name"].fillna("").str.contains(search, case=False, regex=False)]

bezirke = sorted(df["bezirk"].dropna().unique())
selected_bezirke = st.sidebar.multiselect("Bezirk", bezirke)
if selected_bezirke:
    df = df[df["bezirk"].isin(selected_bezirke)]

levels = sorted(int(level) for level in df["type_level"].dropna().unique())
selected_levels = st.sidebar.multiselect(
    "Schultypisierungsstufe",
    levels,
    help=(
        "A higher Stufe means a more disadvantaged student body and more extra resources, "
        "not a weaker school."
    ),
)
if selected_levels:
    df = df[df["type_level"].isin(selected_levels)]

OFFER_FILTERS = (
    ("starts_year_5", "Year 5 (grundständig)", "A track that starts in year 5."),
    (
        "starts_year_7",
        "Year 7 (Regel)",
        "A Regelplatz. The Anmeldezahlen table excludes grundständige places.",
    ),
    ("schnelllerner", "Schnelllernerklassen", None),
    ("europa_schule", "Europa-Schule", "Staatliche Europa-Schule Berlin."),
)
for column, label, help_text in OFFER_FILTERS:
    if column in df.columns and st.sidebar.checkbox(label, help=help_text):
        df = df[df[column]]

PROFILE_COLUMNS = (
    ("profile_language", "Language"),
    ("profile_music_art", "Music/Art"),
    ("profile_mint", "MINT"),
    ("profile_sport", "Sports"),
)
selected_profiles = st.sidebar.multiselect(
    "Profile",
    [label for _, label in PROFILE_COLUMNS],
    help="Language, Music/Art (Musik or Kunst), MINT (maths/science or IT) and Sports.",
)
if selected_profiles:
    mask = pd.Series(False, index=df.index)
    for column, label in PROFILE_COLUMNS:
        if label in selected_profiles and column in df.columns:
            mask = mask | df[column].fillna(False)
    df = df[mask]

st.title("Berlin Gymnasiums")
st.caption(
    "Figures from gymnasium-berlin.net. A lower Abitur average is a stronger result. "
    "Year 5 is a grundständiger Zug. Year 7 is a Regelplatz."
)

summary = st.columns(4)
summary[0].metric("Schools", f"{len(df):,}")
summary[1].metric("Median Abitur average", _metric(df, "abi_average", "{:.2f}"))
summary[2].metric("Median first-choice %", _metric(df, "first_choice_pct", "{:.0f}%"))
summary[3].metric("Median students / teacher", _metric(df, "students_per_teacher", "{:.1f}"))

if df.empty:
    st.info("No schools match these filters.")
    st.stop()

map_df = df.dropna(subset=["lat", "lon"]).copy()
colour_values = map_df[COLOUR_COLUMN] if not map_df.empty else pd.Series(dtype=float)
low, high, colors = scale_colors(colour_values)
if not map_df.empty:
    map_df["color"] = colors
    map_df["abi_label"] = [
        _with_year(value, "{:.2f}", year)
        for value, year in zip(map_df["abi_average"], map_df["abi_year"], strict=True)
    ]
    map_df["pass_label"] = [
        _with_year(value, "{:.0f}%", year)
        for value, year in zip(
            map_df["abi_pass_rate_pct"], map_df["abi_pass_rate_year"], strict=True
        )
    ]
    map_df["places_label"] = [_text(value, "{:.0f}") for value in map_df["admission_places"]]
    map_df["choices_label"] = [_text(value, "{:.0f}") for value in map_df["first_choices"]]
    map_df["demand_label"] = [_text(value, "{:.0f}%") for value in map_df["first_choice_pct"]]
    map_df["students_label"] = [
        _with_year(value, "{:.0f}", year)
        for value, year in zip(map_df["students"], map_df["students_year"], strict=True)
    ]
    map_df["teachers_label"] = [_text(value, "{:.0f}") for value in map_df["teachers"]]
    map_df["ratio_label"] = [_text(value, "{:.1f}") for value in map_df["students_per_teacher"]]
    map_df["ndh_label"] = [
        _with_year(value, "{:.1f}%", year)
        for value, year in zip(
            map_df["migration_background_pct"], map_df["migration_year"], strict=True
        )
    ]
    map_df["type_label"] = [_text(value, "{:.0f}") for value in map_df["type_level"]]
    map_df["intake_label"] = _joined_flags(
        map_df,
        (
            ("starts_year_5", "year 5"),
            ("starts_year_7", "year 7"),
            ("schnelllerner", "Schnelllerner"),
            ("europa_schule", "Europa-Schule"),
        ),
    )
    map_df["profile_label"] = _joined_flags(map_df, PROFILE_COLUMNS)
    for column in ("bezirk", "ortsteil"):
        map_df[column] = map_df[column].fillna("")
    map_df["marker"] = "●"

map_legend(
    low,
    high,
    COLOUR_FORMAT,
    COLOUR_CAPTION,
    missing=bool(map_df[COLOUR_COLUMN].isna().any()) if not map_df.empty else False,
)
if map_df.empty:
    st.info("None of the selected schools have a map location.")
else:
    st.pydeck_chart(
        pdk.Deck(
            layers=[
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
            ],
            initial_view_state=pdk.ViewState(latitude=52.52, longitude=13.40, zoom=10.3),
            map_style="light",
            tooltip={
                "html": "<b>{name}</b><br/>{bezirk} · {ortsteil}<br/>"
                "Abitur {abi_label} · pass rate {pass_label}<br/>"
                "Places {places_label} · first choices {choices_label} ({demand_label})<br/>"
                "Students {students_label} · teachers {teachers_label} · "
                "{ratio_label} per teacher<br/>"
                "NdH {ndh_label} · type level {type_label}<br/>"
                "{intake_label}<br/>Profile: {profile_label}"
            },
        ),
        use_container_width=True,
    )

unlocated = df.loc[df["lat"].isna(), "name"].sort_values()
if not unlocated.empty:
    st.caption(
        f"{len(unlocated)} schools are not on the map because OpenStreetMap has no matching "
        f"point: {', '.join(unlocated)}"
    )

left, right = st.columns(2)
with left:
    st.subheader("Demand and results")
    st.caption(
        "Further right means more first-choice applications per place. "
        "Lower on the chart is a stronger Abitur average."
    )
    scatter = df.dropna(subset=["first_choice_pct", "abi_average"]).copy()
    if scatter.empty:
        st.caption("No schools with both an Abitur average and a first-choice share.")
    else:
        scatter["bezirk"] = scatter["bezirk"].fillna("Unknown")
        st.scatter_chart(scatter, x="first_choice_pct", y="abi_average", color="bezirk")
with right:
    st.subheader("Abitur average by Bezirk")
    st.caption("Median Abitur average. Lower is a stronger result.")
    medians = (
        df.dropna(subset=["bezirk", "abi_average"])
        .groupby("bezirk")["abi_average"]
        .median()
        .sort_values()
    )
    if medians.empty:
        st.caption("No Abitur average for the selected schools.")
    else:
        st.bar_chart(medians)

table_columns = [
    "name", "bezirk", "ortsteil", "starts_year_5", "starts_year_7", "schnelllerner",
    "europa_schule", "profile_language", "profile_music_art", "profile_mint", "profile_sport",
    "abi_average", "abi_year", "abi_pass_rate_pct",
    "abi_pass_rate_year", "admission_places", "first_choices", "first_choice_pct",
    "admissions_year", "students", "teachers", "students_per_teacher", "students_year",
    "teachers_year", "migration_background_pct", "migration_year", "type_level",
]
st.dataframe(
    df[[column for column in table_columns if column in df.columns]].sort_values(
        "abi_average", na_position="last"
    ),
    use_container_width=True,
    hide_index=True,
    column_config={
        "name": st.column_config.TextColumn("School"),
        "starts_year_5": st.column_config.CheckboxColumn("Year 5"),
        "starts_year_7": st.column_config.CheckboxColumn("Year 7"),
        "schnelllerner": st.column_config.CheckboxColumn("Schnelllerner"),
        "europa_schule": st.column_config.CheckboxColumn("Europa-Schule"),
        "profile_language": st.column_config.CheckboxColumn("Language"),
        "profile_music_art": st.column_config.CheckboxColumn("Music/Art"),
        "profile_mint": st.column_config.CheckboxColumn("MINT"),
        "profile_sport": st.column_config.CheckboxColumn("Sports"),
        "abi_average": st.column_config.NumberColumn("Abitur average", format="%.2f"),
        "abi_year": st.column_config.TextColumn("Abitur year"),
        "abi_pass_rate_pct": st.column_config.NumberColumn("Pass rate %", format="%.0f"),
        "abi_pass_rate_year": st.column_config.TextColumn("Pass-rate year"),
        "admission_places": st.column_config.NumberColumn("Places", format="%d"),
        "first_choices": st.column_config.NumberColumn("First choices", format="%d"),
        "first_choice_pct": st.column_config.NumberColumn("First choices %", format="%.0f"),
        "admissions_year": st.column_config.TextColumn("Admissions year"),
        "students": st.column_config.NumberColumn("Students", format="%d"),
        "teachers": st.column_config.NumberColumn("Teachers", format="%d"),
        "students_per_teacher": st.column_config.NumberColumn("Students / teacher", format="%.1f"),
        "students_year": st.column_config.TextColumn("Students year"),
        "teachers_year": st.column_config.TextColumn("Teachers year"),
        "migration_background_pct": st.column_config.NumberColumn("NdH %", format="%.1f"),
        "migration_year": st.column_config.TextColumn("NdH year"),
        "type_level": st.column_config.NumberColumn("Type level", format="%d"),
    },
)
