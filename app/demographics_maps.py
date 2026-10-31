"""Choropleth layers, colour bins and legends for the demographics page."""

import html
from collections.abc import Sequence

import numpy as np
import pandas as pd
import pydeck as pdk
import shapely.geometry

VIEW_STATE = pdk.ViewState(latitude=52.505, longitude=13.40, zoom=9.6)
NO_DATA_COLOR = [200, 200, 200, 90]

# Sequential ramps from light to dark; "diverging" is for changes centred on zero.
RAMPS: dict[str, list[tuple[int, int, int]]] = {
    "Blues": [(239, 243, 255), (189, 215, 231), (107, 174, 214), (49, 130, 189), (8, 81, 156)],
    "Greens": [(237, 248, 233), (186, 228, 179), (116, 196, 118), (49, 163, 84), (0, 109, 44)],
    "Yellows": [
        (255, 255, 229),
        (255, 247, 188),
        (254, 227, 145),
        (254, 196, 79),
        (217, 95, 14),
    ],
    "Oranges": [(254, 237, 222), (253, 190, 133), (253, 141, 60), (230, 85, 13), (166, 54, 3)],
    "Purples": [(242, 240, 247), (203, 201, 226), (158, 154, 200), (117, 107, 177), (84, 39, 143)],
    "Reds": [(254, 229, 217), (252, 174, 145), (251, 106, 74), (222, 45, 38), (165, 15, 21)],
    "Greys": [(247, 247, 247), (204, 204, 204), (150, 150, 150), (99, 99, 99), (37, 37, 37)],
    "diverging": [(33, 102, 172), (146, 197, 222), (247, 247, 247), (244, 165, 130), (178, 24, 43)],
}
LAYER_RAMPS = ["Blues", "Yellows", "Oranges", "Greens", "Purples", "Reds", "Greys"]

PARTY_LABELS = {
    "cdu": "CDU",
    "spd": "SPD",
    "gruene": "Grüne",
    "linke": "Die Linke",
    "afd": "AfD",
    "fdp": "FDP",
    "bsw": "BSW",
    "volt": "Volt",
    "voltdeutschland": "Volt",
    "tierschutzpartei": "Tierschutzpartei",
    "parteimenschklimatierschutz": "Tierschutzpartei",
    "diepartei": "Die PARTEI",
    "freiewahler": "Freie Wähler",
    "teamtodenhofer": "Team Todenhöfer",
    "bundnisdeutschland": "Bündnis Deutschland",
    "mera25": "MERA25",
    "mlpd": "MLPD",
    "buso": "BüSo",
    "sgp": "SGP",
    "pdf": "Partei des Fortschritts",
    "parteidesfortschritts": "Partei des Fortschritts",
    "okologischdemokratischepartei": "ÖDP",
    "deutschekommunistischepartei": "DKP",
    "dieurbaneeinehiphoppartei": "Die Urbane",
    "sozialistischegleichheitsparteivierteinternationale": "SGP",
    "dieheimat": "Die Heimat",
    "bergparteidieuberpartei": "Bergpartei",
    "parteifurarbeitrechtsstaattierschutzelitenforderungundbasisdemokratischeinitiative": (
        "Die PARTEI"
    ),
}
PARTY_COLORS: dict[str, tuple[int, int, int]] = {
    "cdu": (45, 45, 45),
    "spd": (227, 0, 15),
    "gruene": (70, 150, 43),
    "linke": (190, 48, 117),
    "afd": (0, 158, 224),
    "fdp": (255, 214, 0),
    "bsw": (121, 35, 80),
}
OTHER_PARTY_COLOR = (150, 150, 150)


def party_label(key: str) -> str:
    return PARTY_LABELS.get(key, key.title())


def _party_ramp(rgb: tuple[int, int, int]) -> list[tuple[int, int, int]]:
    """Five stops from near-white to the party colour."""
    return [
        tuple(round(255 + (channel - 255) * t) for channel in rgb)
        for t in (0.12, 0.32, 0.55, 0.78, 1.0)
    ]


for _party, _rgb in PARTY_COLORS.items():
    RAMPS[f"party_{_party}"] = _party_ramp(_rgb)
# "<ramp>_r" runs from dark to light, so low values get the strongest colour.
for _name in list(RAMPS):
    RAMPS[f"{_name}_r"] = RAMPS[_name][::-1]


def reversed_ramp(ramp: str, reverse: bool) -> str:
    return f"{ramp}_r" if reverse else ramp


UNIT_SUFFIX = {"pct": " %", "pp": " pp", "eur": " €"}


def format_value(value: object, unit: str) -> str:
    if value is None or pd.isna(value):
        return "no data"
    if unit == "eur":
        return f"{value:,.0f} €".replace(",", ".")
    if unit in {"count", "rate"}:
        return f"{value:,.0f}".replace(",", ".")
    if unit == "score":
        return f"{value:.2f}"
    if unit in ("grade", "per_1k"):
        return f"{value:.2f}"
    sign = "+" if unit == "pp" and value > 0 else ""
    return f"{sign}{value:.1f}{UNIT_SUFFIX.get(unit, '')}"


def quantile_bins(values: pd.Series, classes: int = 5) -> list[float]:
    """Bin edges (classes + 1, deduplicated) at quantiles of the non-missing values."""
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return []
    edges = np.unique(np.quantile(clean, np.linspace(0, 1, classes + 1)))
    if len(edges) == 1:
        return [float(edges[0]), float(edges[0])]
    return [float(edge) for edge in edges]


def diverging_bins(values: pd.Series, classes: int = 5) -> list[float]:
    """Edges symmetric around zero so the middle colour means "no change"."""
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return []
    limit = float(np.quantile(clean.abs(), 0.95)) or float(clean.abs().max()) or 1.0
    return [float(edge) for edge in np.linspace(-limit, limit, classes + 1)]


def bin_colors(edges: Sequence[float], ramp: str) -> list[tuple[int, int, int]]:
    """One colour per bin, sampled evenly from the ramp."""
    stops = RAMPS[ramp]
    bins = max(len(edges) - 1, 1)
    if bins == 1:
        return [stops[-1]]
    return [stops[round(i * (len(stops) - 1) / (bins - 1))] for i in range(bins)]


def color_for(value: object, edges: Sequence[float], ramp: str, alpha: int) -> list[int]:
    if value is None or pd.isna(value) or len(edges) < 2:
        return NO_DATA_COLOR
    colors = bin_colors(edges, ramp)
    index = int(np.searchsorted(edges[1:-1], value, side="right"))
    return [*colors[min(index, len(colors) - 1)], alpha]


def feature_collection(
    areas: pd.DataFrame,
    values: pd.DataFrame,
    edges: Sequence[float],
    ramp: str,
    opacity: float,
    unit: str,
    label: str,
    hide: pd.Series | None = None,
    tooltips: dict[str, str] | None = None,
) -> dict:
    """GeoJSON features for areas with a fill colour and tooltip text per area.

    `values` has area_id, value and optionally coarse. Areas in `hide` (a boolean Series
    indexed by area_id) are drawn transparent, which is how thresholds are applied.
    `tooltips` replaces the per-area tooltip HTML, e.g. to list several overlays.
    """
    merged = areas.merge(values, on="area_id", how="left")
    alpha = int(round(opacity * 255))
    features = []
    for record in merged.itertuples(index=False):
        hidden = hide is not None and bool(hide.get(record.area_id, False))
        fill = [0, 0, 0, 0] if hidden else color_for(record.value, edges, ramp, alpha)
        coarse = bool(getattr(record, "coarse", False)) and not pd.isna(record.value)
        text = format_value(record.value, unit) + (" (Bezirk value)" if coarse else "")
        tooltip = (
            tooltips[record.area_id]
            if tooltips and record.area_id in tooltips
            else f"{html.escape(label)}: {text}"
        )
        features.append(
            {
                "type": "Feature",
                "geometry": record.geometry,
                "properties": {
                    "area_id": str(record.area_id),
                    "name": html.escape(str(record.name)),
                    "bezirk": html.escape(str(record.bezirk or "")),
                    "fill": fill,
                    "tooltip": tooltip,
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def category_collection(
    areas: pd.DataFrame,
    categories: dict[str, str | None],
    colors: dict[str, tuple[int, int, int]],
    opacity: float,
    tooltips: dict[str, str],
    default_color: tuple[int, int, int] = OTHER_PARTY_COLOR,
) -> dict:
    """GeoJSON features coloured by a category per area_id (e.g. the strongest party)."""
    alpha = int(round(opacity * 255))
    features = []
    for record in areas.itertuples(index=False):
        category = categories.get(record.area_id)
        fill = NO_DATA_COLOR if category is None else [*colors.get(category, default_color), alpha]
        features.append(
            {
                "type": "Feature",
                "geometry": record.geometry,
                "properties": {
                    "name": html.escape(str(record.name)),
                    "bezirk": html.escape(str(record.bezirk or "")),
                    "fill": fill,
                    "tooltip": tooltips.get(record.area_id, ""),
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def category_legend_html(items: list[tuple[str, tuple[int, int, int], int]], title: str) -> str:
    """Swatches for (label, colour, count) items."""
    swatches = "".join(
        f"<span style='display:inline-flex;align-items:center;margin-right:12px'>"
        f"<span style='width:14px;height:14px;background:rgb({r},{g},{b});"
        f"border:1px solid #999;margin-right:4px;display:inline-block'></span>"
        f"{html.escape(label)} ({count})</span>"
        for label, (r, g, b), count in items
    )
    return (
        f"<div style='font-size:0.85rem;line-height:1.8'><b>{html.escape(title)}</b><br/>"
        f"{swatches}</div>"
    )


def choropleth_layer(
    collection: dict, layer_id: str, stroked: bool = True, pickable: bool = True
) -> pdk.Layer:
    return pdk.Layer(
        "GeoJsonLayer",
        data=collection,
        id=layer_id,
        get_fill_color="properties.fill",
        get_line_color=[90, 90, 90, 120],
        line_width_min_pixels=0.4 if stroked else 0,
        stroked=stroked,
        filled=True,
        pickable=pickable,
        auto_highlight=pickable,
    )


def scatter_layer(points: pd.DataFrame, layer_id: str) -> pdk.Layer:
    """Dots from lon, lat and an RGBA `color` column; name, bezirk, tooltip feed the tooltip."""
    return pdk.Layer(
        "ScatterplotLayer",
        data=points,
        id=layer_id,
        get_position=["lon", "lat"],
        get_fill_color="color",
        get_radius=45,
        radius_min_pixels=2.5,
        radius_max_pixels=9,
        stroked=False,
        pickable=True,
        auto_highlight=True,
    )


def heatmap_layer(points: pd.DataFrame, layer_id: str) -> pdk.Layer:
    return pdk.Layer(
        "HeatmapLayer",
        data=points,
        id=layer_id,
        get_position=["lon", "lat"],
        radius_pixels=35,
        intensity=1,
        threshold=0.04,
        color_range=[list(rgb) for rgb in RAMPS["Reds"]] + [[103, 0, 13]],
        pickable=False,
    )


def outline_layer(
    areas: pd.DataFrame,
    layer_id: str = "outline",
    *,
    color: Sequence[int] = (40, 40, 40, 200),
    width: float = 1.2,
) -> pdk.Layer:
    """Thin unfilled outlines, e.g. Bezirk borders over a PLR map."""
    collection = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": geometry, "properties": {}}
            for geometry in areas["geometry"]
        ],
    }
    return pdk.Layer(
        "GeoJsonLayer",
        data=collection,
        id=layer_id,
        filled=False,
        stroked=True,
        get_line_color=list(color),
        line_width_min_pixels=width,
        pickable=False,
    )


def area_id_from_query(raw: object, known: set[str]) -> str | None:
    """Planungsraum id from a query parameter, when it is one of `known`."""
    if isinstance(raw, list):
        raw = raw[-1] if raw else None
    if not isinstance(raw, str):
        return None
    area_id = raw.strip()
    return area_id if area_id in known else None


def focus_view(areas: pd.DataFrame, area_id: str | None) -> pdk.ViewState | None:
    """Map view centred on one Planungsraum."""
    if not area_id or areas.empty or "area_id" not in areas.columns:
        return None
    match = areas.loc[areas["area_id"] == area_id]
    if match.empty:
        return None
    try:
        point = shapely.geometry.shape(match.iloc[0]["geometry"]).centroid
    except (TypeError, ValueError, shapely.errors.GEOSException):
        return None
    if point.is_empty:
        return None
    return pdk.ViewState(latitude=float(point.y), longitude=float(point.x), zoom=12.8)


def focus_layer(
    areas: pd.DataFrame, area_id: str | None, layer_id: str = "focus_plr"
) -> pdk.Layer | None:
    """Thick outline of one Planungsraum, drawn above the choropleth."""
    if not area_id or areas.empty or "area_id" not in areas.columns:
        return None
    match = areas.loc[areas["area_id"] == area_id]
    if match.empty:
        return None
    collection = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": geometry, "properties": {}}
            for geometry in match["geometry"]
        ],
    }
    return pdk.Layer(
        "GeoJsonLayer",
        data=collection,
        id=layer_id,
        filled=False,
        stroked=True,
        get_line_color=[20, 70, 160, 255],
        line_width_min_pixels=3,
        pickable=False,
    )


def deck(
    layers: list[pdk.Layer],
    tooltip_html: str | None = None,
    view_state: pdk.ViewState | None = None,
) -> pdk.Deck:
    return pdk.Deck(
        layers=layers,
        initial_view_state=view_state or VIEW_STATE,
        map_style="light",
        tooltip={"html": tooltip_html or "<b>{name}</b> · {bezirk}<br/>{tooltip}"},
    )


def legend_html(edges: Sequence[float], ramp: str, unit: str, title: str) -> str:
    """A horizontal colour key with one swatch per bin and its value range."""
    if len(edges) < 2:
        return ""
    swatches = []
    for i, (r, g, b) in enumerate(bin_colors(edges, ramp)):
        low, high = format_value(edges[i], unit), format_value(edges[i + 1], unit)
        swatches.append(
            f"<span style='display:inline-flex;align-items:center;margin-right:10px'>"
            f"<span style='width:14px;height:14px;background:rgb({r},{g},{b});"
            f"border:1px solid #999;margin-right:4px;display:inline-block'></span>"
            f"{low} – {high}</span>"
        )
    no_data = (
        "<span style='display:inline-flex;align-items:center'>"
        "<span style='width:14px;height:14px;background:rgba(200,200,200,0.5);"
        "border:1px solid #999;margin-right:4px;display:inline-block'></span>no data</span>"
    )
    return (
        f"<div style='font-size:0.85rem;line-height:1.8'><b>{html.escape(title)}</b><br/>"
        + "".join(swatches)
        + no_data
        + "</div>"
    )


def threshold_hide(
    values: pd.DataFrame, low: float | None = None, high: float | None = None
) -> pd.Series:
    """True for areas outside [low, high] (or with no value), indexed by area_id.

    A missing bound is open, so `low` alone means "at or above" and `high` alone "at or below".
    """
    series = values.set_index("area_id")["value"]
    passes = series.notna()
    if low is not None:
        passes &= series >= low
    if high is not None:
        passes &= series <= high
    return ~passes.fillna(False).astype(bool)


def intersect(conditions: list[tuple[pd.DataFrame, float | None, float | None]]) -> set[str]:
    """Area ids that pass every (values, low, high) condition."""
    matching: set[str] | None = None
    for values, low, high in conditions:
        hide = threshold_hide(values, low, high)
        passing = set(hide.index[~hide])
        matching = passing if matching is None else matching & passing
    return matching or set()
