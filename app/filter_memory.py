"""Remember the Flats sidebar filters in a browser cookie.

Streamlit drops widget state on a full page load. The cookie is written from the
page and comes back through ``st.context.cookies`` on the next visit.
"""

from __future__ import annotations

import base64
import json
import math
from collections.abc import Mapping
from typing import Any

COOKIE_NAME = "immo_flat_filters"
_MAX_AGE_SECONDS = 60 * 60 * 24 * 365
_SESSION_KEY = "_flat_filters_saved"


def encode_filters(state: Mapping[str, Any]) -> str:
    raw = json.dumps(_plain(dict(state)), separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_filters(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        padded = value + "=" * (-len(value) % 4)
        parsed = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def cookie_script(payload: str) -> str:
    assignment = (
        f'document.cookie="{COOKIE_NAME}={payload}; Path=/; '
        f"Max-Age={_MAX_AGE_SECONDS}; SameSite=Lax\";"
    )
    return f"<script>{assignment}</script>"


def selection_in_options(selected: object, options: list[Any]) -> list[Any]:
    if not isinstance(selected, list | tuple):
        return []
    allowed = set(options)
    return [item for item in selected if item in allowed]


def clamp_span(
    saved: object,
    low: float,
    high: float,
    step: float,
    *,
    whole: bool,
) -> tuple[int, int] | tuple[float, float]:
    """Restore a range slider, or the full data span when nothing was saved."""
    if not _is_pair(saved):
        return _cast(low, whole), _cast(high, whole)
    start = _bound(float(saved[0]), low, high)
    end = _bound(float(saved[1]), low, high)
    if start > end:
        start, end = end, start
    start = _bound(_snap(start, low, step, whole), low, high)
    end = _bound(_snap(end, low, step, whole), low, high)
    if start > end:
        start, end = end, start
    return _cast(start, whole), _cast(end, whole)


def slider_ceiling(high: float, step: float) -> float:
    """Largest step at or below `high`.

    A max slider cannot sit past its last step. Using that step as the slider's
    end keeps the default on the right, instead of one step short of an off-grid
    data maximum. A range shorter than one step keeps the data maximum.
    """
    high = float(high)
    step = abs(float(step))
    if step <= 0 or high <= step:
        return high
    snapped = math.floor(high / step + 1e-9) * step
    if snapped >= high - 1e-6:
        return high
    return round(snapped, 6)


def clamp_limit(saved: object, high: float, step: float) -> float:
    """Restore a max slider. A missing value means the current data maximum."""
    if isinstance(saved, bool) or not isinstance(saved, int | float):
        return float(high)
    if float(saved) >= float(high):
        return float(high)
    snapped = _snap(_bound(float(saved), 0.0, high), 0.0, step, whole=False)
    return float(min(float(high), max(0.0, snapped)))


def covers_full_span(selected: object, low: float, high: float, step: float) -> bool:
    """True when the thumbs sit on the data ends.

    A range slider snaps to its step, so the upper thumb can stop just short of
    an off-grid maximum. That position is still the full span.
    """
    if not _is_pair(selected):
        return True
    start, end = float(selected[0]), float(selected[1])
    tolerance = abs(float(step))
    return start <= float(low) + tolerance / 2 and end >= float(high) - tolerance


def stored_span(
    selected: tuple[Any, Any] | list[Any], low: float, high: float, step: float
) -> list[Any] | None:
    """Persist a range only when it is narrower than the data currently shown."""
    if covers_full_span(selected, low, high, step):
        return None
    return [_plain(selected[0]), _plain(selected[1])]


def stored_limit(selected: float, high: float) -> int | float | None:
    if selected >= high:
        return None
    return _plain(selected)


class FilterMemory:
    """Widget defaults loaded once per Streamlit session, written back on change."""

    def __init__(self, saved: dict[str, Any]) -> None:
        self.saved = saved
        self.current = dict(saved)

    @classmethod
    def load(cls) -> FilterMemory:
        import streamlit as st

        if _SESSION_KEY not in st.session_state:
            st.session_state[_SESSION_KEY] = decode_filters(st.context.cookies.get(COOKIE_NAME))
        return cls(st.session_state[_SESSION_KEY])

    def write(self) -> None:
        import streamlit as st

        encoded = encode_filters(self.current)
        if encoded == encode_filters(self.saved) or len(encoded) > 3500:
            return
        stored = decode_filters(encoded)
        st.session_state[_SESSION_KEY] = stored
        self.saved = stored
        st.sidebar.html(cookie_script(encoded), unsafe_allow_javascript=True)

    def text(self, label: str, name: str) -> str:
        import streamlit as st

        key = _widget_key(name)
        if key not in st.session_state:
            stored = self.saved.get(name, "")
            st.session_state[key] = stored if isinstance(stored, str) else ""
        value = st.sidebar.text_input(label, key=key)
        self.current[name] = value
        return value

    def checkbox(self, label: str, name: str, default: bool, **kwargs: Any) -> bool:
        import streamlit as st

        key = _widget_key(name)
        if key not in st.session_state:
            stored = self.saved.get(name, default)
            st.session_state[key] = stored if isinstance(stored, bool) else default
        value = bool(st.sidebar.checkbox(label, key=key, **kwargs))
        self.current[name] = value
        return value

    def choices(self, label: str, name: str, options: list[Any], **kwargs: Any) -> list[Any]:
        import streamlit as st

        key = _widget_key(name)
        options = list(options)
        picked = selection_in_options(st.session_state.get(key, self.saved.get(name)), options)
        if st.session_state.get(key) != picked:
            st.session_state[key] = picked
        selected = list(st.sidebar.multiselect(label, options, key=key, **kwargs))
        self.current[name] = selected
        return selected

    def span(
        self,
        label: str,
        name: str,
        low: float,
        high: float,
        step: float,
        *,
        whole: bool,
        help: str | None = None,
    ) -> tuple[Any, Any]:
        import streamlit as st

        key = _widget_key(name)
        raw = st.session_state[key] if key in st.session_state else self.saved.get(name)
        chosen = clamp_span(raw, low, high, step, whole=whole)
        if st.session_state.get(key) != chosen:
            st.session_state[key] = chosen
        kwargs = {"help": help} if help else {}
        selected = st.sidebar.slider(label, low, high, step=step, key=key, **kwargs)
        self.current[name] = stored_span(selected, low, high, step)
        return selected

    def limit(
        self,
        label: str,
        name: str,
        high: float,
        step: float,
        help: str | None = None,
    ) -> float:
        import streamlit as st

        key = _widget_key(name)
        raw = st.session_state[key] if key in st.session_state else self.saved.get(name)
        chosen = clamp_limit(raw, high, step)
        if st.session_state.get(key) != chosen:
            st.session_state[key] = chosen
        kwargs = {"help": help} if help else {}
        selected = float(st.sidebar.slider(label, 0.0, high, step=step, key=key, **kwargs))
        self.current[name] = stored_limit(selected, high)
        return selected


def _widget_key(name: str) -> str:
    return f"flat_filter_{name}"


def _is_pair(value: object) -> bool:
    return (
        isinstance(value, list | tuple)
        and len(value) == 2
        and all(isinstance(item, int | float) and not isinstance(item, bool) for item in value)
    )


def _bound(value: float, low: float, high: float) -> float:
    return min(max(value, float(low)), float(high))


def _snap(value: float, origin: float, step: float, whole: bool) -> float:
    if step <= 0:
        snapped = float(value)
    else:
        steps = round((float(value) - float(origin)) / float(step))
        snapped = float(origin) + steps * float(step)
    if whole:
        return float(int(round(snapped)))
    return round(snapped, 6)


def _cast(value: float, whole: bool) -> int | float:
    if whole:
        return int(round(float(value)))
    return float(value)


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if value != value:
            return None
        if value.is_integer():
            return int(value)
        return round(value, 6)
    return value
