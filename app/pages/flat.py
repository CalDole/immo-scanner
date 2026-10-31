"""One flat, as its own page. The map and scores chart open a short version as a dialog."""

import pandas as pd
import streamlit as st
from listing_detail import LISTING_PAGE_ID, listing_payload, render_listing

from immo_scanner.explore.data import load_features
from immo_scanner.explore.listing_copy import listing_choice
from immo_scanner.explore.score_weights import weights_version


@st.cache_data(ttl=600)
def features(weights: str) -> pd.DataFrame:
    """Every listing, with district vote and turnout blended into education shares."""
    return load_features(active_only=False)


def _choices(frame: pd.DataFrame) -> tuple[list[str], dict[str, str]]:
    scores = (
        pd.to_numeric(frame["total_score"], errors="coerce")
        if "total_score" in frame.columns
        else pd.Series(float("nan"), index=frame.index)
    )
    # Best score first; listings without a score go to the end, then by title.
    ordered = frame.assign(
        _score=scores, _title=frame["title"].fillna("").str.casefold()
    ).sort_values(
        ["_score", "_title", "id"],
        ascending=[False, True, True],
        na_position="last",
        kind="stable",
    )
    options: list[str] = []
    labels: dict[str, str] = {}
    for row in ordered.to_dict("records"):
        listing_id = str(int(row["id"]))
        options.append(listing_id)
        label = listing_choice(
            row.get("title"),
            row.get("address"),
            row.get("ortsteil"),
            row.get("source"),
            listing_id,
        )
        score = row.get("_score")
        labels[listing_id] = f"{score:.2f} · {label}" if pd.notna(score) else f"– · {label}"
    # Two listings can share a title and address. The id keeps both selectable.
    counts: dict[str, int] = {}
    for label in labels.values():
        counts[label] = counts.get(label, 0) + 1
    for listing_id, label in labels.items():
        if counts[label] > 1:
            labels[listing_id] = f"{label} · {listing_id}"
    return options, labels


def _from_url(options: set[str]) -> str | None:
    raw = st.query_params.get("listing")
    if isinstance(raw, list):
        raw = raw[-1] if raw else None
    if raw is not None and str(raw) in options:
        return str(raw)
    return None


st.title("Flat detail")

frame = features(weights_version())
if frame.empty or "id" not in frame.columns:
    st.info("No data yet. Run `immo crawl` and `immo enrich` first.")
    st.stop()

frame = frame.copy()
frame["id"] = pd.to_numeric(frame["id"], errors="coerce")
frame = frame.dropna(subset=["id"])
frame["id"] = frame["id"].astype(int)
if frame.empty:
    st.info("No data yet. Run `immo crawl` and `immo enrich` first.")
    st.stop()

options, labels = _choices(frame)
option_set = set(options)
# The widget key is only filled in before the selectbox exists. A later change
# comes from the selectbox itself; the URL is updated from that choice below.
if "listing_choice" not in st.session_state:
    chosen = _from_url(option_set)
    if chosen is None:
        stored = st.session_state.get(LISTING_PAGE_ID)
        if isinstance(stored, int) and str(stored) in option_set:
            chosen = str(stored)
    if chosen is not None:
        st.session_state["listing_choice"] = chosen

choice = st.selectbox(
    "Listing",
    options,
    index=None,
    placeholder="Choose a listing",
    format_func=lambda listing_id: labels.get(listing_id, listing_id),
    key="listing_choice",
)
if choice is None:
    st.caption("Choose a listing, or open one from the map or the scores chart.")
    st.stop()

st.session_state[LISTING_PAGE_ID] = int(choice)
st.query_params["listing"] = choice
match = frame.loc[frame["id"] == int(choice)]
if match.empty:
    st.warning("This listing is not in the current data.")
    st.stop()

peers = frame.loc[frame["status"] == "active"] if "status" in frame.columns else frame
render_listing(listing_payload(match.iloc[0]), on_page=True, peers=peers)
