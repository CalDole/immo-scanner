"""Sidebar filters shared by the listing dashboards."""

import pandas as pd
import streamlit as st
from filter_memory import FilterMemory, covers_full_span, slider_ceiling

from immo_scanner.explore.data import titles_are_rohling

# Certificate order. A++ is kept distinct from A+ when a portal stores it that way.
ENERGY_CLASS_ORDER = ("A++", "A+", "A", "B", "C", "D", "E", "F", "G", "H")


def range_filter(
    df: pd.DataFrame,
    column: str,
    label: str,
    step: float,
    *,
    whole: bool = False,
    memory: FilterMemory | None = None,
    help: str | None = None,
) -> pd.DataFrame:
    if column not in df.columns:
        return df
    values = df[column].dropna()
    if values.empty:
        return df
    if whole:
        low, high = int(values.min()), int(values.max())
        step_value: int | float = int(step)
    else:
        low, high = float(values.min()), float(values.max())
        step_value = step
    if low == high:
        return df
    if memory is None:
        selected = st.sidebar.slider(label, low, high, (low, high), step=step_value, help=help)
    else:
        selected = memory.span(label, column, low, high, step_value, whole=whole, help=help)
    if covers_full_span(selected, low, high, step_value):
        return df
    return df[df[column].between(*selected)]


def bathroom_toilet_counts(frame: pd.DataFrame) -> pd.Series:
    """Bathroom count, plus one when the listing states a guest toilet.

    A missing bathroom count stays missing. An unstated guest toilet adds nothing.
    """
    if "bathrooms" not in frame.columns:
        return pd.Series(float("nan"), index=frame.index, dtype="float64")
    baths = pd.to_numeric(frame["bathrooms"], errors="coerce")
    if "has_guest_toilet" not in frame.columns:
        return baths
    guest = frame["has_guest_toilet"].fillna(False).astype(bool).astype(float)
    return baths + guest


def filter_bathrooms_toilets(
    frame: pd.DataFrame, memory: FilterMemory | None = None
) -> pd.DataFrame:
    """Range slider for bathrooms plus a stated guest toilet (Gäste-WC)."""
    counts = bathroom_toilet_counts(frame)
    if counts.dropna().empty:
        return frame
    tagged = frame.assign(bathrooms_toilets=counts)
    kept = range_filter(
        tagged,
        "bathrooms_toilets",
        "Bathrooms / toilets",
        0.5,
        memory=memory,
        help="Bathrooms, plus one when the listing has a guest toilet (Gäste-WC).",
    )
    return frame.loc[kept.index]


def normalized_energy_class(value: object) -> str | None:
    """Certificate label as A++ … H. Blank and unreadable labels are missing."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    text = str(value).strip().upper().replace(" ", "").replace("_PLUS", "+").replace("_", "")
    if text in ENERGY_CLASS_ORDER:
        return text
    return None


def energy_class_options(frame: pd.DataFrame) -> list[str]:
    if "energy_efficiency_class" not in frame.columns:
        return []
    present = {
        label
        for label in frame["energy_efficiency_class"].map(normalized_energy_class)
        if label is not None
    }
    return [level for level in ENERGY_CLASS_ORDER if level in present]


def filter_energy_class(frame: pd.DataFrame, selected: list[str]) -> pd.DataFrame:
    if not selected or "energy_efficiency_class" not in frame.columns:
        return frame
    labels = frame["energy_efficiency_class"].map(normalized_energy_class)
    return frame[labels.isin(selected)]


ROHLING_OPTIONS = ("Rohling", "not Rohling")


def filter_rohling(frame: pd.DataFrame, selected: list[str]) -> pd.DataFrame:
    """Keep listings whose Rohling flag is one of the chosen options.

    An empty selection leaves the frame unchanged.
    """
    if not selected or "is_rohling" not in frame.columns:
        return frame
    flags = frame["is_rohling"].fillna(False).astype(bool)
    mask = pd.Series(False, index=frame.index)
    if "Rohling" in selected:
        mask |= flags
    if "not Rohling" in selected:
        mask |= ~flags
    return frame[mask]


def energy_class_filter(
    frame: pd.DataFrame, memory: FilterMemory | None = None
) -> pd.DataFrame:
    options = energy_class_options(frame)
    if not options:
        return frame
    selected = _multiselect("Energy class", options, memory, "energy_class")
    return filter_energy_class(frame, selected)


def area_filter(
    frame: pd.DataFrame, column: str, label: str, memory: FilterMemory | None = None
) -> pd.DataFrame:
    if column not in frame.columns:
        return frame
    options = sorted(frame[column].dropna().unique())
    selected = _multiselect(label, options, memory, column)
    if selected:
        return frame[frame[column].isin(selected)]
    return frame


COMMUTE_COLUMNS = (
    "commute_minutes",
    "commute_workplace2_minutes",
    "commute_citycenter_minutes",
)


def has_commute_time(frame: pd.DataFrame) -> pd.Series:
    """True when a listing has a time to work, work 2, or the city centre."""
    columns = [column for column in COMMUTE_COLUMNS if column in frame.columns]
    if not columns:
        return pd.Series(True, index=frame.index)
    return frame[columns].notna().any(axis=1)


def max_filter(
    df: pd.DataFrame,
    column: str,
    label: str,
    step: float,
    memory: FilterMemory | None = None,
) -> pd.DataFrame:
    if column not in df.columns:
        return df
    values = df[column].dropna()
    if values.empty:
        return df
    # The slider ends on a step, so its default is the right-hand end. That end
    # means no limit: a listing past it still passes until the user moves the thumb down.
    high = slider_ceiling(float(values.max()), step)
    if high <= 0:
        return df
    if memory is None:
        limit = st.sidebar.slider(label, 0.0, high, high, step=step)
    else:
        limit = memory.limit(label, column, high, step)
    if limit >= high:
        return df
    return df[df[column] <= limit]


OUTDATED_FILTER = "hide_outdated"
OUTDATED_LABEL = "Hide outdated listings"
OUTDATED_HELP = "Leave out listings that are no longer available on the source site."


def exclude_outdated(frame: pd.DataFrame) -> pd.DataFrame:
    """Drop listings the source site no longer carries (`status` removed)."""
    if frame.empty or "status" not in frame.columns:
        return frame
    return frame.loc[~frame["status"].eq("removed")]


def hide_outdated_selected(memory: FilterMemory | None = None) -> bool:
    """Whether outdated listings should be left out, before the checkbox is drawn.

    The filter is on unless this session or the saved Flats cookie turned it off.
    Call this before loading so switching it off actually brings removed rows in.
    """
    import streamlit as st

    key = _outdated_widget_key(memory)
    stored = st.session_state.get(key, None)
    if isinstance(stored, bool):
        return stored
    if memory is not None:
        saved = memory.saved.get(OUTDATED_FILTER, True)
        if isinstance(saved, bool):
            return saved
    return True


def apply_listing_filters(
    df: pd.DataFrame, memory: FilterMemory | None = None
) -> pd.DataFrame:
    """Narrow listings with the Flats sidebar controls.

    "Hide outdated listings" is on by default. Callers that can load removed rows
    should use ``hide_outdated_selected`` for that load, so switching the filter
    off shows listings that are no longer on the source site.
    Pass ``memory`` on the Flats page so those controls are restored from the browser cookie.
    """
    if _hide_outdated_checkbox(memory):
        df = exclude_outdated(df)

    search = _text("Search title / address", "search", memory).strip()
    if search:
        mask = df["title"].fillna("").str.contains(search, case=False) | df["address"].fillna(
            ""
        ).str.contains(search, case=False)
        df = df[mask]

    sources = _multiselect("Source", sorted(df["source"].unique()), memory, "sources")
    if sources:
        df = df[df["source"].isin(sources)]

    df = area_filter(df, "bezirk", "Bezirk", memory)
    df = area_filter(df, "ortsteil", "Ortsteil", memory)
    df = area_filter(df, "prognoseraum", "Prognoseraum", memory)
    df = area_filter(df, "kiez", "Kiez", memory)
    df = range_filter(
        df,
        "german_citizen_share",
        "German citizens (%)",
        0.5,
        memory=memory,
        help="Share of Planungsraum residents who are German citizens (MSS, 31.12.2024).",
    )
    df = range_filter(
        df,
        "mh_share",
        "Migration background (%)",
        0.5,
        memory=memory,
        help="Share of Planungsraum residents with a migration background (A I 16).",
    )
    df = range_filter(
        df,
        "oic_share",
        "OIC / Islamic countries (%)",
        0.5,
        memory=memory,
        help=(
            "Share of Planungsraum residents with a migration background from "
            "Islamic countries (OIC)."
        ),
    )
    df = range_filter(
        df,
        "incident_rate",
        "Berliner Register incidents",
        0.01,
        memory=memory,
        help=(
            "Street- and station-placed incidents per 1,000 residents per year, "
            "over the last three years."
        ),
    )
    df = range_filter(
        df,
        "school_type_level",
        "Schultypisierung",
        0.1,
        memory=memory,
        help=(
            "Distance-weighted Schultypisierungsstufe of nearby schools. "
            "1 is the lightest load, 7 the heaviest."
        ),
    )
    df = range_filter(
        df,
        "nearby_school_abi_average",
        "Abiturdurchschnitt",
        0.05,
        memory=memory,
        help=(
            "Distance-weighted Abitur average of nearby schools, on the Gymnasium scale. "
            "1.0 is the best grade."
        ),
    )
    df = range_filter(
        df,
        "abi_pass_rate_pct",
        "Abitur pass rate (%)",
        1.0,
        memory=memory,
        help="Published Abitur pass rate of the nearest Gymnasium.",
    )

    df = range_filter(df, "price", "Price (EUR)", 10_000.0, memory=memory)
    df = range_filter(df, "price_per_m2", "Price per m² (EUR)", 100.0, memory=memory)
    df = range_filter(df, "living_area", "Living area (m²)", 1.0, memory=memory)
    df = range_filter(df, "rooms", "Rooms", 0.5, memory=memory)
    df = filter_bathrooms_toilets(df, memory)
    df = range_filter(
        df, "building_age_years", "Building age (years)", 1.0, whole=True, memory=memory
    )
    df = energy_class_filter(df, memory)
    df = area_filter(df, "energy_source", "Energy source", memory)
    df = max_filter(df, "commute_minutes", "Max commute (minutes)", 5.0, memory)
    if _checkbox(
        "Only with commute time",
        "only_with_commute",
        False,
        memory,
        help="Hide listings that have no commute to work, work 2, or the city centre.",
    ):
        df = df[has_commute_time(df)]
    df = max_filter(df, "nearest_rail_m", "Max distance to S/U-Bahn (m)", 100.0, memory)
    df = max_filter(
        df, "nearest_gymnasium_m", "Max distance to nearest Gymnasium (m)", 100.0, memory
    )
    df = max_filter(
        df, "nearest_montessori_kita_m", "Max distance to Montessori Kita (m)", 100.0, memory
    )

    if "title" in df.columns:
        df = df.copy()
        df["is_rohling"] = titles_are_rohling(df["title"])
    rohling = _multiselect(
        "Rohling",
        list(ROHLING_OPTIONS),
        memory,
        "rohling",
        help="A Rohling is a listing whose title contains the word Rohling.",
    )
    df = filter_rohling(df, rohling)

    if _checkbox("New builds only", "new_builds", False, memory):
        df = df[df["is_new_build"]]
    if _checkbox("Only with elevator", "elevator", False, memory):
        df = df[df["has_elevator"].fillna(False)]
    if _checkbox("Only with balcony", "balcony", False, memory):
        df = df[df["has_balcony"].fillna(False)]
    if _checkbox("Only with parking", "parking", False, memory):
        df = df[df["has_parking"].fillna(False)]
    if _checkbox("Only with cellar", "cellar", False, memory):
        df = df[df["has_cellar"].fillna(False)]
    rental = _multiselect(
        "Rented", ["unvermietet", "vermietet", "unknown"], memory, "rented"
    )
    if rental and "is_rented" in df.columns:
        mask = pd.Series(False, index=df.index)
        if "unvermietet" in rental:
            mask |= df["is_rented"].eq(False)
        if "vermietet" in rental:
            mask |= df["is_rented"].eq(True)
        if "unknown" in rental:
            mask |= df["is_rented"].isna()
        df = df[mask]
    if "is_foreclosure" in df.columns and _checkbox(
        "Hide foreclosures (Zwangsversteigerungen)",
        "hide_foreclosures",
        True,
        memory,
        help="ThinkImmo flags foreclosures; their price is a minimum bid or appraised value.",
    ):
        df = df[~df["is_foreclosure"].fillna(False).astype(bool)]
    if _checkbox("Only exact / street-level locations", "exact_locations", False, memory):
        df = df[df["geo_precision"].isin(["source", "exact", "street"])]
    if _checkbox("Only listings with price drops", "price_drops", False, memory):
        df = df[df["price_change_pct"] < 0]
    return df


def _text(label: str, name: str, memory: FilterMemory | None) -> str:
    if memory is None:
        return st.sidebar.text_input(label)
    return memory.text(label, name)


def _multiselect(
    label: str,
    options: list,
    memory: FilterMemory | None,
    name: str,
    **kwargs,
) -> list:
    if memory is None:
        return st.sidebar.multiselect(label, options, **kwargs)
    return memory.choices(label, name, list(options), **kwargs)


def _checkbox(
    label: str,
    name: str,
    default: bool,
    memory: FilterMemory | None,
    **kwargs,
) -> bool:
    if memory is None:
        return bool(st.sidebar.checkbox(label, value=default, **kwargs))
    return memory.checkbox(label, name, default, **kwargs)


def _outdated_widget_key(memory: FilterMemory | None) -> str:
    if memory is not None:
        return f"flat_filter_{OUTDATED_FILTER}"
    return "scores_filter_hide_outdated"


def _hide_outdated_checkbox(memory: FilterMemory | None) -> bool:
    if memory is not None:
        return memory.checkbox(OUTDATED_LABEL, OUTDATED_FILTER, True, help=OUTDATED_HELP)
    key = _outdated_widget_key(memory)
    if key not in st.session_state:
        st.session_state[key] = True
    return bool(st.sidebar.checkbox(OUTDATED_LABEL, key=key, help=OUTDATED_HELP))
