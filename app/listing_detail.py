"""Detail view for one flat: the dashboard on the Flat detail page, a short dialog elsewhere."""

from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd
import streamlit as st
from flat_sections import (
    active_features,
    render_header,
    render_key_figures,
    render_map,
    render_media,
    render_tabs,
)

from immo_scanner.explore.listing_copy import listing_id_from_chart, listing_id_from_map

_ACTIVE = "listing_detail_active"
_DISMISSED = "listing_detail_dismissed"
# Last listing opened from the map or the scores chart. The Flat detail page
# shows it when the URL does not name one.
LISTING_PAGE_ID = "listing_page_id"


def _dismiss() -> None:
    st.session_state[_DISMISSED] = st.session_state.get(_ACTIVE)


@st.dialog("Flat", width="large", on_dismiss=_dismiss)
def _dialog(listing: dict[str, Any]) -> None:
    render_listing(listing)


def listing_payload(row: pd.Series) -> dict[str, Any]:
    """JSON-ish scalars. Lists such as the map colour are left out."""
    payload: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, list | dict | np.ndarray):
            continue
        try:
            if pd.isna(value):
                payload[str(key)] = None
                continue
        except (TypeError, ValueError):
            pass
        if isinstance(value, np.generic):
            value = value.item()
        if isinstance(value, datetime):
            value = value.date().isoformat()
        elif isinstance(value, date):
            value = value.isoformat()
        payload[str(key)] = value
    return payload


def _match(frame: pd.DataFrame, listing_id: int) -> pd.Series | None:
    if frame.empty or "id" not in frame.columns:
        return None
    ids = pd.to_numeric(frame["id"], errors="coerce")
    match = frame.loc[ids == listing_id]
    if match.empty:
        return None
    return match.iloc[0]


def open_listing(frame: pd.DataFrame, listing_id: int | None, origin: str) -> None:
    """Open the detail dialog for a clicked point.

    Closing the dialog keeps it closed for that same click. Choosing another
    point, or clearing the selection and clicking again, opens it.
    """
    if listing_id is None:
        # Clearing this chart's selection lets the same point be opened again.
        # Another page's dismissal is left alone.
        dismissed = st.session_state.get(_DISMISSED)
        if isinstance(dismissed, str) and dismissed.startswith(f"{origin}:"):
            st.session_state.pop(_DISMISSED, None)
        return
    st.session_state[LISTING_PAGE_ID] = listing_id
    signature = f"{origin}:{listing_id}"
    if st.session_state.get(_DISMISSED) == signature:
        return
    row = _match(frame, listing_id)
    if row is None:
        return
    st.session_state[_ACTIVE] = signature
    _dialog(listing_payload(row))


def open_from_map(frame: pd.DataFrame, selection: object) -> None:
    open_listing(frame, listing_id_from_map(selection), "map")


def open_from_chart(frame: pd.DataFrame, selection: object) -> None:
    open_listing(frame, listing_id_from_chart(selection), "scores")


def render_listing(
    listing: dict[str, Any], *, on_page: bool = False, peers: pd.DataFrame | None = None
) -> None:
    """The whole dashboard on the page; header and key figures only in the dialog.

    `peers` are the active listings that medians and ranks compare against.
    """
    if peers is None:
        peers = active_features()
    render_header(listing, on_page=on_page)
    render_key_figures(listing, peers)
    if not on_page:
        return
    render_media(listing)
    render_map(listing)
    render_tabs(listing, peers)
