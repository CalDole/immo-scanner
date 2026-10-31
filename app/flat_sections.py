"""Sections of the Flat detail page. `listing_detail.render_listing` puts them together."""

import html
import json
from typing import Any

import altair as alt
import pandas as pd
import pydeck as pdk
import streamlit as st

from immo_scanner.config import get_settings
from immo_scanner.explore import data
from immo_scanner.explore.education import education_references
from immo_scanner.explore.flat_detail import (
    delta_to_median,
    gallery,
    match_gymnasium,
    median_of,
    plain_record,
    price_cuts,
    price_history,
    purchase_costs,
    rank,
    score_breakdown,
)
from immo_scanner.explore.listing_copy import (
    NOT_STATED,
    PARTIES,
    age_class_facts,
    availability_facts,
    building_facts,
    commute_facts,
    description_sections,
    feature_checklist,
    grundschule_facts,
    gymnasium_facts,
    headline,
    is_approximate,
    layout_facts,
    listing_market_facts,
    move_in,
    neighbourhood_indicator_facts,
    origin_facts,
    original_url,
    party_shares,
    people_facts,
    platform_links,
    rent_facts,
    source_facts,
    source_label,
    station_facts,
    status_badges,
    street_address,
)
from immo_scanner.explore.origins import kiez_summary, load_origin_stats
from immo_scanner.explore.prices import load_average_price_per_m2, price_comparison
from immo_scanner.explore.score_weights import load_weights, weights_version
from immo_scanner.explore.scoring import avg_commute_minutes

# An Ortsteil with fewer active listings than this is compared with all of Berlin.
MIN_PEERS = 5
TAB_NAMES = [
    "Getting around",
    "Score",
    "Flat and building",
    "Neighbourhood",
    "Schools",
    "Listing text",
    "Sources",
    "Money",
]

FLAT_RGB = (246, 196, 0)
WORKPLACE_RGB = (30, 30, 30)
SBAHN_RGB = (0, 110, 60)
UBAHN_RGB = (0, 90, 170)
GYMNASIUM_RGB = (140, 60, 170)
CATCHMENT_RGB = (40, 120, 200)


@st.cache_data(ttl=600)
def _active_features(weights: str) -> pd.DataFrame:
    return data.load_features(active_only=True)


def active_features() -> pd.DataFrame:
    return _active_features(weights_version())


@st.cache_data(ttl=600)
def _texts(listing_id: int) -> dict[str, Any]:
    return data.load_listing_texts(listing_id)


@st.cache_data(ttl=600)
def _media(listing_id: int) -> list[tuple[str, list[dict[str, Any]]]]:
    return data.load_listing_media(listing_id)


@st.cache_data(ttl=600)
def _extras(thinkimmo_id: str) -> dict[str, Any]:
    return data.load_thinkimmo_extras(thinkimmo_id)


@st.cache_data(ttl=3600)
def _age_class(year_built: int) -> dict[str, Any]:
    return data.load_building_age_class(year_built)


@st.cache_data(ttl=600)
def _points(listing_id: int) -> pd.DataFrame:
    return data.load_listing_points(listing_id)


@st.cache_data(ttl=3600)
def _catchment(bsn: str) -> str | None:
    return data.load_catchment(bsn)


@st.cache_data(ttl=600)
def _sources(listing_id: int) -> dict[str, Any]:
    return data.load_listing_sources(listing_id)


@st.cache_data(ttl=3600)
def _berlin_parties() -> dict[str, float]:
    return data.berlin_party_shares()


@st.cache_data(ttl=3600)
def _indicators(plr_id: str) -> pd.DataFrame:
    return data.load_area_indicators(plr_id)


@st.cache_data(ttl=3600)
def _neighbourhood_references(plr_id: str) -> pd.DataFrame:
    return data.load_neighbourhood_references(plr_id)


@st.cache_data(ttl=3600)
def _education_references(bezirk: str) -> dict[str, dict[str, float | None]]:
    """Bezirk and Berlin qualification shares, blended with eligible-voter turnout."""
    return education_references(bezirk or None)


@st.cache_data(ttl=3600)
def _gymnasiums() -> pd.DataFrame:
    return data.load_gymnasiums()


@st.cache_data(ttl=3600)
def _grundschulen() -> pd.DataFrame:
    return data.load_grundschulen()


@st.cache_data(ttl=3600)
def _workplaces() -> pd.DataFrame:
    return data.load_workplaces()


@st.cache_data(ttl=600)
def _asking_prices() -> pd.DataFrame:
    return load_average_price_per_m2()


@st.cache_data(ttl=600)
def _origins(plr_id: str) -> dict[str, Any] | None:
    return kiez_summary(load_origin_stats(plr_id))


@st.cache_data(ttl=600)
def _history(listing_id: int) -> pd.DataFrame:
    return data.load_price_history([listing_id])


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if pd.isna(number) else number


def _id(listing: dict[str, Any]) -> int | None:
    value = listing.get("id")
    return value if isinstance(value, int) else None


def _str(listing: dict[str, Any], key: str) -> str | None:
    value = listing.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def facts(pairs: list[tuple[str, str]]) -> None:
    left, right = st.columns(2)
    for index, (label, value) in enumerate(pairs):
        column = left if index % 2 == 0 else right
        column.markdown(f"**{label}**  \n{html.escape(value)}")


def _prose(text: str) -> str:
    """Listing text as Markdown, with the listing's own characters left as text."""
    return html.escape(text).replace("\n", "  \n")


def _column(frame: pd.DataFrame, column: str) -> pd.Series:
    return frame[column] if column in frame.columns else pd.Series(dtype=float)


def _gym_matches(listing: dict[str, Any]) -> list[tuple[str, float | None, dict | None]]:
    stats = _gymnasiums()
    found = []
    for suffix in ("", "_2", "_3"):
        name = _str(listing, f"nearest_gymnasium{suffix}")
        if name:
            found.append(
                (
                    name,
                    _number(listing.get(f"nearest_gymnasium{suffix}_m")),
                    match_gymnasium(name, stats),
                )
            )
    return found


def _age_class_for(listing: dict[str, Any]) -> dict[str, Any]:
    year = _number(listing.get("year_built"))
    return _age_class(int(year)) if year is not None else {}


# Header ---------------------------------------------------------------------------

_BADGE_COLOURS = {
    "Removed": "red",
    "Rented": "orange",
    "Vacant": "green",
    "Rental status not stated": "gray",
    "Milieuschutz": "orange",
    "Milieuschutz nearby": "gray",
    "Milieuschutz in quarter": "gray",
    "No Milieuschutz": "green",
    "Milieuschutz not stated": "gray",
    "Foreclosure": "red",
    "Leasehold": "orange",
    "Listed monument": "violet",
    "New build": "green",
}


def render_header(listing: dict[str, Any], *, on_page: bool) -> None:
    st.subheader(str(listing.get("title") or "Listing"))
    place = " · ".join(
        part
        for key in ("bezirk", "ortsteil", "prognoseraum", "kiez")
        if (part := _str(listing, key))
    )
    caption = " · ".join(
        part
        for part in (
            street_address(listing.get("address")),
            place,
            source_label(listing.get("source")),
        )
        if part
    )
    if caption:
        st.caption(caption)
    badges = status_badges(listing)
    if badges:
        st.markdown(
            " ".join(f":{_BADGE_COLOURS.get(badge, 'blue')}-badge[{badge}]" for badge in badges)
        )

    listing_id = _id(listing)
    texts = _texts(listing_id) if listing_id is not None else {}
    url = original_url(listing.get("url"), texts.get("ti_platforms"))
    links = platform_links(texts.get("ti_platforms"), skip_url=url)
    buttons = st.columns(max(2, len(links) + 2))
    if url:
        buttons[0].link_button("Original listing", url, type="primary", help=url)
    else:
        buttons[0].caption("No link to the original listing was saved.")
    for column, (name, link) in zip(buttons[1:], links, strict=False):
        column.link_button(name, link, help=link)
    if not on_page and listing_id is not None:
        buttons[-1].page_link(
            "pages/flat.py", label="Open as page", query_params={"listing": str(listing_id)}
        )


_KEY_FIGURE_CSS = """
<style>
.st-key-key-figures [data-testid="stMetricValue"] { font-size: 1.4rem; }
.st-key-key-figures [data-testid="stMetricLabel"] p { font-size: 0.85rem; }
.st-key-key-figures [data-testid="stMetricDelta"] { font-size: 0.8rem; }
</style>
"""


def render_key_figures(listing: dict[str, Any], peers: pd.DataFrame) -> None:
    figures = dict(headline(listing))
    ortsteil = _str(listing, "ortsteil")
    local = (
        peers[peers["ortsteil"] == ortsteil]
        if ortsteil and "ortsteil" in peers
        else peers.iloc[0:0]
    )
    area_peers, area_name = (local, "Ortsteil") if len(local) >= MIN_PEERS else (peers, "Berlin")

    peer_commute = [
        avg_commute_minutes(first, second)
        for first, second in zip(
            _column(peers, "commute_minutes"),
            _column(peers, "commute_workplace2_minutes"),
            strict=False,
        )
    ]
    own_commute = avg_commute_minutes(
        listing.get("commute_minutes"), listing.get("commute_workplace2_minutes")
    )
    m2_delta = delta_to_median(listing.get("price_per_m2"), _column(area_peers, "price_per_m2"))
    commute_delta = delta_to_median(own_commute, peer_commute)
    place = rank(listing.get("total_score"), _column(peers, "total_score"))
    market = _number(listing.get("thinkimmo_price_to_market_pct"))

    st.markdown(_KEY_FIGURE_CSS, unsafe_allow_html=True)
    box = st.container(key="key-figures")
    columns = box.columns(4) + box.columns(4)
    columns[0].metric("Price", figures["Price"], border=True)
    columns[1].metric(
        "€/m²",
        figures["€/m²"],
        delta=None if m2_delta is None else f"{m2_delta:+,.0f}",
        delta_color="inverse",
        delta_description=f"vs {area_name} median",
        help=f"Compared with the median of active listings in the {area_name.lower()}.",
        border=True,
    )
    columns[2].metric("Living area", figures["Living area"], border=True)
    columns[3].metric("Rooms", figures["Rooms"], border=True)
    columns[4].metric(
        "Score",
        figures["Score"],
        delta=None if place is None else f"#{place[0]} of {place[1]}",
        delta_color="off",
        delta_arrow="off",
        help="Weighted mean of the distance, building, flat and neighbourhood scores; "
        "the weights are set on the Scores page. "
        "The rank is among all active listings. The Score tab explains each part.",
        border=True,
    )
    columns[5].metric(
        "Of market value",
        figures["Of market value"],
        delta=None if market is None else f"{market - 100:+.0f}%",
        delta_color="inverse",
        delta_description="vs ThinkImmo estimate",
        help="Asking price as a share of ThinkImmo's market value estimate. "
        "Below 100% is cheaper than the estimate.",
        border=True,
    )
    work = _number(listing.get("commute_minutes"))
    work2 = _number(listing.get("commute_workplace2_minutes"))
    split = " · ".join(
        f"{label} {value:.0f} min"
        for label, value in (("Workplace 1", work), ("Workplace 2", work2))
        if value is not None
    )
    columns[6].metric(
        "Commute",
        figures["Commute"],
        delta=None if commute_delta is None else f"{commute_delta:+.0f} min",
        delta_color="inverse",
        delta_description="vs median",
        help="Public transport, weighted 3:2 between workplace 1 and 2. "
        + (split or "No commute time computed yet."),
        border=True,
    )
    status, when, reason = move_in(listing)
    columns[7].metric(
        "Move-in",
        status,
        delta=when,
        delta_color="off",
        delta_arrow="off",
        help=reason,
        border=True,
    )


# Photos and map -------------------------------------------------------------------


def _image_grid(items: list, *, per_row: int = 3) -> None:
    for start in range(0, len(items), per_row):
        columns = st.columns(per_row)
        for column, item in zip(columns, items[start : start + per_row], strict=False):
            column.image(item.image, caption=item.caption, width="stretch")


def render_media(listing: dict[str, Any]) -> None:
    listing_id = _id(listing)
    if listing_id is None:
        return
    photos, floor_plans = gallery(_media(listing_id), listing.get("source"))
    if not photos and not floor_plans:
        return
    names = [name for name, items in (("Photos", photos), ("Floor plan", floor_plans)) if items]
    for tab, name in zip(st.tabs(names), names, strict=True):
        with tab:
            if name == "Floor plan":
                for item in floor_plans:
                    st.image(item.image, caption=item.caption, width="stretch")
                continue
            st.caption(f"{len(photos)} photos from {source_label(photos[0].origin)}.")
            _image_grid(photos)
            if photos[0].origin == "thinkimmo":
                st.caption(
                    "ThinkImmo keeps at most five photos per flat. The original listing has "
                    "all of them; `immo details` saves the full set for ImmoScout24, "
                    "Kleinanzeigen and Neubaukompass listings."
                )


def _point_layer(frame: pd.DataFrame, layer_id: str, rgb: tuple[int, int, int], radius: int):
    return pdk.Layer(
        "ScatterplotLayer",
        data=frame,
        id=layer_id,
        get_position=["lon", "lat"],
        get_fill_color=[*rgb, 230],
        get_line_color=[255, 255, 255, 255],
        stroked=True,
        line_width_min_pixels=1,
        get_radius=radius,
        radius_min_pixels=5,
        pickable=True,
    )


def _legend(items: list[tuple[str, tuple[int, int, int]]]) -> None:
    marks = "".join(
        f"<span style='margin-right:1rem'><span style='display:inline-block;width:12px;"
        f"height:12px;border-radius:50%;background:rgb{rgb};vertical-align:middle'></span> "
        f"{label}</span>"
        for label, rgb in items
    )
    st.markdown(f"<div style='font-size:0.9rem'>{marks}</div>", unsafe_allow_html=True)


def render_map(listing: dict[str, Any]) -> None:
    lat, lon = _number(listing.get("lat")), _number(listing.get("lon"))
    if lat is None or lon is None:
        st.info("This listing has no map position yet.")
        return
    if is_approximate(listing):
        st.warning(
            "Location approximate: the listing was placed at its "
            f"{listing.get('geo_precision')} centre. Commute times, schools and "
            "neighbourhood figures are for that point, not the building."
        )
    layers = []
    legend = [("Flat", FLAT_RGB)]
    bsn = _str(listing, "grundschule_bsn")
    geojson = _catchment(bsn) if bsn else None
    if geojson:
        layers.append(
            pdk.Layer(
                "GeoJsonLayer",
                data={
                    "type": "Feature",
                    "geometry": json.loads(geojson),
                    "properties": {"name": f"Catchment of {listing.get('grundschule_name')}"},
                },
                id="catchment",
                get_fill_color=[*CATCHMENT_RGB, 35],
                get_line_color=[*CATCHMENT_RGB, 200],
                line_width_min_pixels=2,
                pickable=True,
            )
        )
        legend.append(("School catchment", CATCHMENT_RGB))
    listing_id = _id(listing)
    points = _points(listing_id) if listing_id is not None else pd.DataFrame()
    for kind, label, rgb in (
        ("gymnasium", "Gymnasium", GYMNASIUM_RGB),
        ("sbahn", "S-Bahn", SBAHN_RGB),
        ("ubahn", "U-Bahn", UBAHN_RGB),
    ):
        rows = points[points["kind"] == kind] if not points.empty else points
        if not rows.empty:
            layers.append(_point_layer(rows[["name", "lat", "lon"]], kind, rgb, 40))
            legend.append((label, rgb))
    workplaces = _workplaces()
    if not workplaces.empty:
        layers.append(
            _point_layer(workplaces.assign(name="Workplace"), "workplaces", WORKPLACE_RGB, 80)
        )
        legend.append(("Workplace", WORKPLACE_RGB))
    flat = pd.DataFrame([{"name": str(listing.get("title") or "Flat"), "lat": lat, "lon": lon}])
    layers.append(_point_layer(flat, "flat", FLAT_RGB, 60))
    _legend(legend)
    st.pydeck_chart(
        pdk.Deck(
            layers=layers,
            initial_view_state=pdk.ViewState(latitude=lat, longitude=lon, zoom=13.5),
            map_style="light",
            tooltip={"text": "{name}"},
        ),
        width="stretch",
        height=420,
        key=f"flat-detail-map-{listing_id}",
    )


# Tabs -----------------------------------------------------------------------------


def _getting_around(listing: dict[str, Any]) -> None:
    pairs = commute_facts(listing)
    if pairs:
        st.markdown("**Commute by public transport**")
        facts(pairs)
        st.caption(
            "Door to door at the configured departure hour. The weighted average counts "
            "workplace 1 three times and workplace 2 twice; it is the input to the distance score."
        )
    else:
        st.caption("No commute times computed yet. Run `immo enrich`.")
    listing_id = _id(listing)
    points = _points(listing_id) if listing_id is not None else pd.DataFrame()
    lines = {}
    if not points.empty:
        lines = {
            str(row.name): str(row.lines)
            for row in points.itertuples()
            if isinstance(row.lines, str) and row.lines.strip()
        }
    stations = station_facts(listing, lines)
    if stations:
        st.markdown("**Stations**")
        facts(stations)
        st.caption("Straight-line distance. Lines are shown where OpenStreetMap lists them.")


def _score(listing: dict[str, Any], peers: pd.DataFrame) -> None:
    abi = [match["abi_average"] if match else None for _, _, match in _gym_matches(listing)]
    groups = score_breakdown(listing, _age_class_for(listing), abi, load_weights())
    chart = pd.DataFrame(
        [
            {
                "Group": group.label,
                "Score": group.score,
                "Median of active listings": median_of(_column(peers, group.column)),
            }
            for group in groups
        ]
    )
    total = _number(listing.get("total_score"))
    place = rank(total, _column(peers, "total_score"))
    if total is not None:
        st.markdown(
            f"**Total score {total:.2f}**"
            + (f", place {place[0]} of {place[1]} active listings" if place else "")
        )
    weights = ", ".join(f"{group.label} {group.weight:g}" for group in groups)
    st.caption(
        f"The total is the weighted mean of the four group scores ({weights}). Each group is "
        "the weighted mean of its sub-scores that could be computed; a missing input is left "
        "out, not counted as 0. Weights are set on the Scores page. "
        "The black tick is the median of all active listings."
    )
    base = alt.Chart(chart).encode(y=alt.Y("Group:N", sort=None, title=None))
    bars = base.mark_bar(color="#f6c400").encode(
        x=alt.X("Score:Q", title="Score"),
        tooltip=["Group", alt.Tooltip("Score:Q", format=".2f")],
    )
    ticks = base.mark_tick(color="black", thickness=2, size=24).encode(
        x="Median of active listings:Q",
        tooltip=["Group", alt.Tooltip("Median of active listings:Q", format=".2f")],
    )
    st.altair_chart(bars + ticks, width="stretch")

    for group in groups:
        score = "–" if group.score is None else f"{group.score:.2f}"
        title = f"{group.label}: {score} · {group.known} of {len(group.parts)} inputs known"
        with st.expander(title):
            table = pd.DataFrame(
                [
                    {
                        "Part": part.label,
                        "Input": part.value or NOT_STATED,
                        "Score": "–" if part.score is None else f"{part.score:.2f}",
                        "Weight": f"{part.weight:g}",
                        "Rule": part.rule,
                    }
                    for part in group.parts
                ]
            )
            st.dataframe(table, hide_index=True, width="stretch")
    st.caption("Formulas in docs/scoring.md.")


_STATE_ICONS = {"Yes": ":material/check_circle:", "No": ":material/cancel:"}


def _flat_and_building(listing: dict[str, Any]) -> None:
    st.markdown("#### The flat")
    layout = layout_facts(listing)
    if layout:
        facts(layout)
    st.markdown("**Features**")
    checklist = feature_checklist(listing)
    columns = st.columns(3)
    for index, (label, state) in enumerate(checklist):
        icon = _STATE_ICONS.get(state.split(" ")[0], ":material/help:")
        columns[index % 3].markdown(f"{icon} {label}: {state}")
    st.caption("“Not stated” means the listing does not say, not that the flat lacks it.")
    availability = availability_facts(listing)
    if availability:
        st.markdown("**Availability**")
        facts(availability)
        if listing.get("is_rented") is True:
            st.warning("The flat is let. Moving in yourself needs the tenancy to end first.")

    st.markdown("#### Building and energy")
    building = building_facts(listing)
    if building:
        facts(building)
    age_class = _age_class_for(listing)
    age_pairs = age_class_facts(age_class)
    if age_pairs:
        st.markdown("**Typical for the construction period**")
        facts(age_pairs)
        st.caption(
            "Typical values for buildings of this age in Germany, not measurements of this "
            "building. U-values are in W/(m²K); lower insulates better."
        )
    obligations = age_class.get("legal_obligations_on_purchase") if age_class else None
    if isinstance(obligations, str) and obligations.strip():
        message = f"Retrofit obligations on purchase (GEG): {obligations}"
        if obligations.lower().startswith(("yes", "possibly")):
            st.warning(message)
        else:
            st.info(message)


def _party_chart(listing: dict[str, Any]) -> None:
    shares = party_shares(listing)
    if not shares:
        return
    berlin = _berlin_parties()
    labels = dict(PARTIES)
    rows = [
        {"Party": label, "Area": "Voting district", "Share": share} for _, label, share in shares
    ]
    rows += [
        {"Party": labels[key], "Area": "Berlin", "Share": berlin[key]}
        for key, _, _ in shares
        if key in berlin
    ]
    chart = (
        alt.Chart(pd.DataFrame(rows))
        .mark_bar()
        .encode(
            x=alt.X("Party:N", sort=[label for _, label in PARTIES], title=None),
            xOffset=alt.XOffset("Area:N", sort=["Voting district", "Berlin"]),
            y=alt.Y("Share:Q", title="Share of votes (%)"),
            color=alt.Color(
                "Area:N",
                sort=["Voting district", "Berlin"],
                scale=alt.Scale(range=["#f6c400", "#9e9e9e"]),
                title=None,
            ),
            tooltip=["Party", "Area", alt.Tooltip("Share:Q", format=".1f")],
        )
    )
    st.markdown("**Abgeordnetenhaus 2026, Zweitstimmen**")
    st.altair_chart(chart, width="stretch")
    st.caption(
        "These shares are the input to the tolerance score. AfD counts most, then BSW; "
        "Berlin is the vote-weighted mean of all voting districts."
    )


def _neighbourhood(listing: dict[str, Any]) -> None:
    name = _str(listing, "plr_name")
    plr_id = _str(listing, "plr_id")
    st.markdown(f"#### Planungsraum {name}" if name else "#### Planungsraum")
    if plr_id:
        st.page_link(
            "pages/demographics.py",
            label="Demographics",
            query_params={"plr": plr_id},
            help=(
                f"Open the Demographics page for {name}."
                if name
                else "Open the Demographics page for this Planungsraum."
            ),
        )
    indicators = _indicators(plr_id) if plr_id else pd.DataFrame()
    area_values = (
        dict(zip(indicators["indicator"], indicators["value"], strict=True))
        if not indicators.empty
        else {}
    )
    references = _neighbourhood_references(plr_id) if plr_id else pd.DataFrame()
    reference_values = {
        str(row.indicator): {
            "bezirk": row.bezirk_average,
            "berlin": row.berlin_average,
        }
        for row in references.itertuples(index=False)
    }
    bezirk = listing.get("bezirk")
    reference_values.update(_education_references(bezirk if isinstance(bezirk, str) else ""))
    comparison = neighbourhood_indicator_facts(listing, area_values, reference_values)
    if plr_id and (price_row := price_comparison(_asking_prices(), plr_id)):
        comparison = [price_row, *comparison]
    if comparison:
        st.markdown("**Key indicators**")
        st.dataframe(
            pd.DataFrame(comparison),
            hide_index=True,
            width="stretch",
            column_config={
                "Indicator": st.column_config.TextColumn("Indicator"),
                "Here": st.column_config.TextColumn("Here"),
                "Bezirk average": st.column_config.TextColumn("Bezirk average"),
                "Berlin average": st.column_config.TextColumn("Berlin average"),
            },
        )
        st.caption(
            "Here: AGH Zweitstimmen and the estimated Abitur and Hochschule shares use the "
            "listing's voting district; other values use its Planungsraum. Asking price per m² "
            "is the mean of price divided by living area for active listings there, excluding "
            "ThinkImmo and titles that contain the word Rohling. Its Bezirk and Berlin figures "
            "use those same listings. References: AGH votes are weighted by valid votes; "
            "estimated qualification shares blend that mix with local turnout and the "
            "Nichtwähler profile. The school Abitur average, Schultypisierung, "
            "citizenship and unemployment by Planungsraum population; "
            "OIC from summed origin and resident counts. The Abitur average uses six nearby "
            "schools, adjusted for the Berlin-wide Gymnasium/Sekundarschule difference and "
            "weighted by distance. Schultypisierung is those schools' Stufe on the same "
            "distance weights, without a shift between school types. "
            "Neighbourhood crime: Kieztaten per 100,000 residents in the listing's "
            "Bezirksregion"
            + (f" ({int(year)})" if (year := _number(listing.get("crime_year"))) else "")
            + ", against the police's Bezirk and Berlin rates (Kriminalitätsatlas). "
            "Berliner Register: incidents placed at a street or station per 1,000 residents "
            "per year over the last three years; Bezirk and Berlin pool all their "
            "Planungsräume. Lower is better for both."
        )
    people = people_facts(listing)
    if people:
        facts(people)
    summary = _origins(plr_id) if plr_id else None
    origins = origin_facts(summary)
    if origins:
        st.markdown("**Origin**")
        facts(origins)
        st.caption(
            f"Planungsraum, {summary['ref_date']:%d.%m.%Y}: shares of all residents, top 5 "
            "countries of origin, and Vietnam even when it is outside that list. "
            "Country charts on the Demographics page, Origins tab. "
            "The diversity map is on the People tab."
        )
    _party_chart(listing)
    if not indicators.empty:
        st.markdown("**Area indicators**")
        units = {"pct": "%", "pp": " pp", "eur": " €"}
        table = pd.DataFrame(
            {
                "Topic": indicators["topic"].str.capitalize(),
                "Indicator": indicators["label"],
                "Here": [
                    f"{value:.1f}{units.get(unit, '')}" if pd.notna(value) else "–"
                    for value, unit in zip(indicators["value"], indicators["unit"], strict=True)
                ],
                "Berlin median": [
                    f"{value:.1f}{units.get(unit, '')}" if pd.notna(value) else "–"
                    for value, unit in zip(
                        indicators["berlin_median"], indicators["unit"], strict=True
                    )
                ],
                "As of": indicators["ref_date"],
                "Source": indicators["source"],
            }
        )
        st.dataframe(table, hide_index=True, width="stretch")
        st.caption("Berlin median: the middle value over all Planungsräume.")
    if not (people or origins or not indicators.empty or party_shares(listing)):
        st.caption("No neighbourhood data for this location.")


def _schools(listing: dict[str, Any]) -> None:
    if is_approximate(listing):
        st.warning("The location is approximate, so the assigned school and distances may be off.")
    bsn = _str(listing, "grundschule_bsn")
    assigned = _str(listing, "grundschule_name")
    st.markdown("#### Assigned Grundschule")
    if assigned:
        schools = _grundschulen()
        match = schools[schools["bsn"] == bsn] if bsn else schools.iloc[0:0]
        with st.container(border=True):
            st.markdown(f"**{html.escape(assigned)}**")
            pairs = grundschule_facts(plain_record(match.iloc[0]) if not match.empty else None)
            if pairs:
                facts(pairs)
            st.caption(
                "Public primary schools admit by catchment area (Einschulbereich). "
                "The flat lies in this school's area for the current school year."
            )
    else:
        st.caption("No catchment area found for this location.")
    nearest = [
        f"{name} ({distance:,.0f} m)" if distance is not None else name
        for suffix in ("", "_2", "_3")
        if (name := _str(listing, f"nearest_grundschule{suffix}"))
        for distance in [_number(listing.get(f"nearest_grundschule{suffix}_m"))]
    ]
    if nearest:
        st.markdown("**Nearest Grundschulen:** " + html.escape(", ".join(nearest)))

    gyms = _gym_matches(listing)
    if gyms:
        st.markdown("#### Nearest Gymnasien")
        columns = st.columns(len(gyms))
        for column, (name, distance, stats) in zip(columns, gyms, strict=True):
            with column.container(border=True):
                st.markdown(f"**{html.escape(name)}**")
                if distance is not None:
                    st.caption(f"{distance:,.0f} m away")
                pairs = gymnasium_facts(stats)
                for label, value in pairs:
                    st.markdown(f"**{label}**  \n{html.escape(value)}")
                if not pairs:
                    st.caption("No published statistics found for this school.")
        st.caption(
            "Lower Abitur averages are better. The mean over these three feeds the education "
            "score. First choices above places means the school is oversubscribed. "
            "NdH: share of students whose first language is not German."
        )
    kita = _str(listing, "nearest_montessori_kita")
    if kita:
        distance = _number(listing.get("nearest_montessori_kita_m"))
        suffix = f" ({distance:,.0f} m)" if distance is not None else ""
        st.markdown(f"**Nearest Montessori Kita:** {html.escape(kita)}{suffix}")


def _listing_text(listing: dict[str, Any]) -> None:
    listing_id = _id(listing)
    texts = _texts(listing_id) if listing_id is not None else {}
    sections = description_sections(listing.get("source"), texts)
    if not sections:
        st.caption("The description text has not been saved. The original listing has it.")
    for index, (heading, text) in enumerate(sections):
        with st.expander(heading, expanded=index == 0):
            st.markdown(_prose(text))


def _sources_tab(listing: dict[str, Any]) -> None:
    listing_id = _id(listing)
    sources = _sources(listing_id) if listing_id is not None else {}
    facts(source_facts(listing, sources))
    st.caption("Shows where each figure on this page came from and how current it is.")


def _market_chart(listing: dict[str, Any], extras: dict[str, Any]) -> None:
    own = _number(listing.get("price_per_m2"))
    district = extras.get("district_name")
    marks = [
        ("This flat", own),
        ("Market estimate", _number(extras.get("market_price_per_m2"))),
        ("Market low", _number(extras.get("market_price_per_m2_min"))),
        ("Market high", _number(extras.get("market_price_per_m2_max"))),
        (
            f"District ({district})" if district else "District",
            _number(extras.get("district_price_per_m2")),
        ),
        ("Berlin", _number(extras.get("city_price_per_m2"))),
        ("Similar listings", _number(extras.get("similar_price_per_m2"))),
    ]
    rows = [{"Comparison": label, "€/m²": value} for label, value in marks if value is not None]
    if len(rows) < 2:
        return
    frame = pd.DataFrame(rows)
    frame["Kind"] = [
        "This flat" if row["Comparison"] == "This flat" else "Reference" for row in rows
    ]
    chart = (
        alt.Chart(frame)
        .mark_circle(size=160)
        .encode(
            x=alt.X("€/m²:Q", scale=alt.Scale(zero=False)),
            y=alt.Y("Comparison:N", sort=None, title=None),
            color=alt.Color(
                "Kind:N",
                scale=alt.Scale(domain=["This flat", "Reference"], range=["#f6c400", "#616161"]),
                legend=None,
            ),
            tooltip=["Comparison", alt.Tooltip("€/m²:Q", format=",.0f")],
        )
    )
    st.markdown("**Price per m² compared with the market**")
    st.altair_chart(chart, width="stretch")
    st.caption("Market estimate, range, district, Berlin and similar listings come from ThinkImmo.")


def _money(listing: dict[str, Any]) -> None:
    settings = get_settings()
    tid = _str(listing, "thinkimmo_id")
    extras = _extras(tid) if tid else {}

    st.markdown("#### Cash needed to buy")
    costs = purchase_costs(
        listing.get("price"),
        listing.get("provision"),
        extras.get("commission_pct"),
        tax_pct=settings.grunderwerbsteuer_pct,
        notary_pct=settings.notary_pct,
    )
    if costs.total is not None:
        st.metric(
            "Total cash needed",
            f"{costs.total:,.0f} €",
            help="Price plus purchase costs. Financing is not included. The rates are "
            "IMMO_GRUNDERWERBSTEUER_PCT and IMMO_NOTARY_PCT in the settings.",
        )
        table = pd.DataFrame(
            [
                {
                    "Item": line.label,
                    "Rate": "" if line.pct is None else f"{line.pct:.2f}%",
                    "Amount": "not stated" if line.amount is None else f"{line.amount:,.0f} €",
                }
                for line in costs.lines
            ]
        )
        st.dataframe(table, hide_index=True, width="stretch")
        if not costs.commission_known:
            st.caption("The buyer's commission is not stated, so the total leaves it out.")
    else:
        st.caption("No price, so no purchase costs.")

    st.markdown("#### Monthly")
    monthly: list[tuple[str, str]] = []
    hausgeld = _number(listing.get("hausgeld"))
    if hausgeld is not None:
        monthly.append(("Hausgeld", f"{hausgeld:,.2f} €"))
    cash_flow = _number(listing.get("thinkimmo_cash_flow"))
    if cash_flow is not None:
        monthly.append(("Cash flow if let (ThinkImmo)", f"{cash_flow:,.0f} €"))
    if monthly:
        facts(monthly)
        if cash_flow is not None:
            st.caption(
                "ThinkImmo's cash flow uses the financing assumptions in "
                "IMMO_THINKIMMO_FINANCE_QUERY (equity, interest, repayment)."
            )
    else:
        st.caption("No Hausgeld or cash flow known.")

    _market_chart(listing, extras)

    rent = rent_facts(listing, extras)
    if rent:
        st.markdown("#### If you let it")
        facts(rent)

    st.markdown("#### Price history")
    listing_id = _id(listing)
    own = _history(listing_id) if listing_id is not None else pd.DataFrame()
    history = price_history(own, extras.get("buying_price_history"))
    market_pairs = listing_market_facts(listing, extras)
    cuts = price_cuts(history)
    if not history.empty:
        market_pairs.append(("Price cuts", str(cuts)))
    if market_pairs:
        facts(market_pairs)
    if not history.empty and history["date"].nunique() > 1:
        chart = (
            alt.Chart(history)
            .mark_line(point=True, interpolate="step-after")
            .encode(
                x=alt.X("date:T", title=None),
                y=alt.Y("price:Q", title="Asking price (€)", scale=alt.Scale(zero=False)),
                color=alt.Color("origin:N", title="Seen on"),
                tooltip=[
                    alt.Tooltip("date:T"),
                    alt.Tooltip("price:Q", format=",.0f"),
                    "origin",
                ],
            )
        )
        st.altair_chart(chart, width="stretch")
    if cuts:
        st.caption("A long listing time with price cuts usually means room to negotiate.")


def render_tabs(listing: dict[str, Any], peers: pd.DataFrame) -> None:
    tabs = dict(zip(TAB_NAMES, st.tabs(TAB_NAMES), strict=True))
    with tabs["Getting around"]:
        _getting_around(listing)
    with tabs["Score"]:
        _score(listing, peers)
    with tabs["Flat and building"]:
        _flat_and_building(listing)
    with tabs["Neighbourhood"]:
        _neighbourhood(listing)
    with tabs["Schools"]:
        _schools(listing)
    with tabs["Listing text"]:
        _listing_text(listing)
    with tabs["Sources"]:
        _sources_tab(listing)
    with tabs["Money"]:
        _money(listing)
