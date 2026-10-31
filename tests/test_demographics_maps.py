import pandas as pd
import pytest
from demographics_maps import (
    NO_DATA_COLOR,
    RAMPS,
    area_id_from_query,
    choropleth_layer,
    color_for,
    diverging_bins,
    feature_collection,
    focus_layer,
    focus_view,
    format_value,
    intersect,
    legend_html,
    quantile_bins,
    reversed_ramp,
    threshold_hide,
)

AREAS = pd.DataFrame(
    {
        "area_id": ["a", "b", "c"],
        "name": ["Alpha", "Beta", "Gamma <x>"],
        "bezirk": ["Mitte", "Mitte", "Pankow"],
        "geometry": [{"type": "Polygon", "coordinates": []}] * 3,
    }
)


def test_quantile_bins_handle_ties_and_empty_input():
    assert quantile_bins(pd.Series([1, 2, 3, 4, 5, 6])) == pytest.approx([1, 2, 3, 4, 5, 6])
    assert quantile_bins(pd.Series([2.0, 2.0, None])) == [2.0, 2.0]
    assert quantile_bins(pd.Series([None, None], dtype=float)) == []


def test_diverging_bins_are_symmetric_around_zero():
    edges = diverging_bins(pd.Series([-1.0, 0.5, 2.0, -3.0]))
    assert edges[0] == pytest.approx(-edges[-1])
    middle = len(edges) // 2
    assert edges[middle - 1] < 0 < edges[middle]
    assert color_for(0.0, edges, "diverging", 255)[:3] == list(RAMPS["diverging"][2])


def test_color_for_picks_bins_and_greys_out_missing():
    edges = [0, 10, 20, 30, 40, 50]
    assert color_for(5, edges, "Blues", 200) == [*RAMPS["Blues"][0], 200]
    assert color_for(50, edges, "Blues", 200) == [*RAMPS["Blues"][-1], 200]
    assert color_for(None, edges, "Blues", 200) == NO_DATA_COLOR
    assert color_for(5, [], "Blues", 200) == NO_DATA_COLOR


def test_threshold_and_intersection():
    first = pd.DataFrame({"area_id": ["a", "b", "c"], "value": [10.0, 30.0, None]})
    second = pd.DataFrame({"area_id": ["a", "b", "c"], "value": [5.0, 1.0, 9.0]})
    assert threshold_hide(first, low=20).to_dict() == {"a": True, "b": False, "c": True}
    assert threshold_hide(first, high=20).to_dict() == {"a": False, "b": True, "c": True}
    assert threshold_hide(first, 10, 20).to_dict() == {"a": False, "b": True, "c": True}
    assert intersect([(first, 20, None), (second, None, 2)]) == {"b"}
    assert intersect([(first, 5, None), (second, 4, None)]) == {"a"}
    assert intersect([(first, 5, 15), (second, 4, 6)]) == {"a"}
    assert intersect([]) == set()


def test_reversed_ramps_run_dark_to_light():
    assert RAMPS["Blues_r"] == RAMPS["Blues"][::-1]
    assert reversed_ramp("Blues", True) == "Blues_r"
    assert reversed_ramp("Blues", False) == "Blues"
    edges = [0, 10, 20, 30, 40, 50]
    assert color_for(5, edges, "Blues_r", 200) == [*RAMPS["Blues"][-1], 200]


def test_feature_collection_hides_areas_and_marks_bezirk_values():
    values = pd.DataFrame(
        {"area_id": ["a", "b", "c"], "value": [10.0, 20.0, 30.0], "coarse": [False, True, False]}
    )
    hide = pd.Series({"a": True, "b": False, "c": False})
    collection = feature_collection(
        AREAS, values, [10, 20, 30], "Reds", 0.5, "pct", "Share", hide=hide
    )
    features = {f["properties"]["name"]: f["properties"] for f in collection["features"]}
    assert features["Alpha"]["fill"] == [0, 0, 0, 0]
    assert features["Beta"]["tooltip"] == "Share: 20.0 % (Bezirk value)"
    assert features["Beta"]["fill"][3] == 128
    assert "Gamma &lt;x&gt;" in features


def test_feature_collection_tooltip_override():
    values = pd.DataFrame({"area_id": ["a"], "value": [1.0]})
    collection = feature_collection(
        AREAS, values, [0, 1], "Blues", 1.0, "pct", "Share", tooltips={"a": "custom"}
    )
    tooltips = [f["properties"]["tooltip"] for f in collection["features"]]
    assert tooltips == ["custom", "Share: no data", "Share: no data"]


def test_choropleth_can_be_non_interactive_when_used_behind_listing_markers():
    layer = choropleth_layer({"type": "FeatureCollection", "features": []}, "areas", pickable=False)
    assert layer.pickable is False
    assert layer.auto_highlight is False


def test_area_id_from_query_keeps_only_known_ids():
    known = {"01100101", "01100102"}
    assert area_id_from_query("01100102", known) == "01100102"
    assert area_id_from_query(["01100101"], known) == "01100101"
    assert area_id_from_query("  01100101  ", known) == "01100101"
    assert area_id_from_query("nope", known) is None
    assert area_id_from_query(None, known) is None


def test_focus_view_and_layer_use_the_named_area():
    square = {
        "type": "Polygon",
        "coordinates": [[[13.2, 52.4], [13.4, 52.4], [13.4, 52.6], [13.2, 52.6], [13.2, 52.4]]],
    }
    areas = pd.DataFrame({"area_id": ["a", "b"], "geometry": [square, square]})
    view = focus_view(areas, "a")
    assert view is not None
    assert view.latitude == pytest.approx(52.5)
    assert view.longitude == pytest.approx(13.3)
    assert focus_view(areas, "missing") is None
    layer = focus_layer(areas, "b")
    assert layer is not None
    assert layer.id == "focus_plr"
    assert focus_layer(areas, None) is None


def test_format_value_and_legend():
    assert format_value(1.234, "pp") == "+1.2 pp"
    assert format_value(4210, "eur") == "4.210 €"
    assert format_value(11226.8, "rate") == "11.227"
    assert format_value(None, "pct") == "no data"
    legend = legend_html([0, 10, 20], "Greens", "pct", "Children")
    assert "Children" in legend and "0.0 % – 10.0 %" in legend and "no data" in legend
    assert legend_html([], "Greens", "pct", "x") == ""
