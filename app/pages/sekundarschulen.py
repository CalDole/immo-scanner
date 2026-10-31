"""Sekundarschule statistics: map, demand, and Bezirk comparison."""

import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st

from immo_scanner.explore.data import load_sekundarschulen

COLOUR_COLUMN = "abi_average"
COLOUR_FORMAT = "{:.2f}"
COLOUR_CAPTION = (
    "Abitur average. Green is a stronger result (lower average), relative to the schools shown."
)


@st.cache_data(ttl=3600)
def sekundarschulen() -> pd.DataFrame:
    """Statistics plus the official school site, where the school number matches."""
    return load_sekundarschulen()


def _text(value: object, fmt: str) -> str:
    if pd.isna(value):
        return "–"
    return fmt.format(value)


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
df = sekundarschulen()
if df.empty:
    st.info("No Sekundarschule statistics yet. Run `immo load-reference sekundarschule-stats`.")
    st.stop()

search = st.sidebar.text_input("Search school")
if search:
    df = df[df["name"].fillna("").str.contains(search, case=False, regex=False)]

bezirke = sorted(df["bezirk"].dropna().unique())
selected_bezirke = st.sidebar.multiselect("Bezirk", bezirke)
if selected_bezirke:
    df = df[df["bezirk"].isin(selected_bezirke)]

if "schulart" in df.columns:
    arten = sorted(df["schulart"].dropna().unique())
    selected_arten = st.sidebar.multiselect("Schulart", arten)
    if selected_arten:
        df = df[df["schulart"].isin(selected_arten)]

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

st.title("Berlin Sekundarschulen")
st.caption(
    "Figures from sekundarschulen-berlin.de. A lower Abitur average is a stronger result. "
    "Locations come from the Berlin school map, matched by school number."
)

summary = st.columns(4)
summary[0].metric("Schools", f"{len(df):,}")
summary[1].metric("Median Abitur average", _metric(df, "abi_average", "{:.2f}"))
summary[2].metric("Median first-choice %", _metric(df, "first_choice_pct", "{:.0f}%"))
summary[3].metric("Median NdH %", _metric(df, "migration_background_pct", "{:.1f}%"))

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
    map_df["ndh_label"] = [
        _with_year(value, "{:.1f}%", year)
        for value, year in zip(
            map_df["migration_background_pct"], map_df["migration_year"], strict=True
        )
    ]
    map_df["type_label"] = [_text(value, "{:.0f}") for value in map_df["type_level"]]
    for column in ("bezirk", "ortsteil", "schulart"):
        if column in map_df.columns:
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
                "{schulart}<br/>"
                "Abitur {abi_label} · pass rate {pass_label}<br/>"
                "Places {places_label} · first choices {choices_label} ({demand_label})<br/>"
                "NdH {ndh_label} · type level {type_label}"
            },
        ),
        use_container_width=True,
    )

unlocated = df.loc[df["lat"].isna(), "name"].sort_values()
if not unlocated.empty:
    st.caption(
        f"{len(unlocated)} schools are not on the map because the school map has no matching "
        f"site: {', '.join(unlocated)}"
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
    "name", "bsn", "bezirk", "ortsteil", "schulart",
    "abi_average", "abi_year", "abi_pass_rate_pct", "abi_pass_rate_prev_pct",
    "abi_pass_rate_year", "admission_places", "first_choices", "first_choice_pct",
    "admissions_year", "migration_background_pct", "migration_background_prev_pct",
    "migration_year", "type_level",
]
st.dataframe(
    df[[column for column in table_columns if column in df.columns]].sort_values(
        "abi_average", na_position="last"
    ),
    use_container_width=True,
    hide_index=True,
    column_config={
        "name": st.column_config.TextColumn("School"),
        "bsn": st.column_config.TextColumn("BSN"),
        "schulart": st.column_config.TextColumn("Schulart"),
        "abi_average": st.column_config.NumberColumn("Abitur average", format="%.2f"),
        "abi_year": st.column_config.TextColumn("Abitur year"),
        "abi_pass_rate_pct": st.column_config.NumberColumn("Pass rate %", format="%.0f"),
        "abi_pass_rate_prev_pct": st.column_config.NumberColumn(
            "Pass rate previous %", format="%.0f"
        ),
        "abi_pass_rate_year": st.column_config.TextColumn("Pass-rate year"),
        "admission_places": st.column_config.NumberColumn("Places", format="%d"),
        "first_choices": st.column_config.NumberColumn("First choices", format="%d"),
        "first_choice_pct": st.column_config.NumberColumn("First choices %", format="%.0f"),
        "admissions_year": st.column_config.TextColumn("Admissions year"),
        "migration_background_pct": st.column_config.NumberColumn("NdH %", format="%.1f"),
        "migration_background_prev_pct": st.column_config.NumberColumn(
            "NdH previous %", format="%.1f"
        ),
        "migration_year": st.column_config.TextColumn("NdH year"),
        "type_level": st.column_config.NumberColumn("Type level", format="%d"),
    },
)
