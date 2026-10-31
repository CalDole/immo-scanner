import pandas as pd
import pytest
from demographics_maps import (
    NO_DATA_COLOR,
    PARTY_COLORS,
    RAMPS,
    category_collection,
    category_legend_html,
    party_label,
)

from immo_scanner.explore.elections import (
    party_shares_long,
    plr_party_shares,
    strongest_party,
    tolerance_scores,
)
from immo_scanner.explore.scoring import tolerance_score


def test_tolerance_scores_match_listing_formula_and_read_volt_alias():
    shares = pd.DataFrame(
        {
            "district_id": ["d1", "d1", "d1", "d2"],
            "party": ["afd", "gruene", "voltdeutschland", "afd"],
            "share": [10.0, 30.0, 5.0, 0.0],
        }
    )
    scores = tolerance_scores(shares, "district_id").set_index("area_id")["value"]
    expected = tolerance_score({"afd": 10.0, "gruene": 30.0, "volt": 5.0})
    assert scores["d1"] == pytest.approx(expected)
    assert scores["d1"] == pytest.approx(1 / ((2.9 + 0.6 + 0.05) / 8.7))
    assert pd.isna(scores["d2"])


def test_strongest_party_and_lead():
    assert strongest_party({"cdu": 20.0, "afd": 31.5, "spd": 18.0}) == ("afd", 31.5, 11.5)
    assert strongest_party({"gruene": 40.0}) == ("gruene", 40.0, 40.0)
    assert strongest_party({}) == (None, None, None)


def test_plr_party_shares_weight_by_votes_in_overlap():
    districts = pd.DataFrame(
        {
            "scope": ["agh", "agh"],
            "area_id": ["d1", "d2"],
            "party_shares": [{"afd": 10.0, "gruene": 30.0}, {"afd": 40.0, "gruene": 0.0}],
        }
    )
    weights = pd.DataFrame(
        {
            "scope": ["agh", "agh", "agh"],
            "district_id": ["d1", "d2", "d2"],
            "plr_id": ["p1", "p1", "p2"],
            "weight": [300.0, 100.0, 0.0],
        }
    )
    result = plr_party_shares(weights, party_shares_long(districts)).set_index(
        ["area_id", "party"]
    )["value"]
    assert result[("p1", "afd")] == pytest.approx(17.5)
    assert result[("p1", "gruene")] == pytest.approx(22.5)
    assert "p2" not in result.index.get_level_values("area_id")


def test_party_ramps_end_in_party_colour():
    for party, rgb in PARTY_COLORS.items():
        assert RAMPS[f"party_{party}"][-1] == rgb
    assert party_label("gruene") == "Grüne"
    assert party_label("neuepartei") == "Neuepartei"


def test_category_collection_colours_and_missing():
    areas = pd.DataFrame(
        {
            "area_id": ["a", "b", "c"],
            "name": ["A", "B", "C"],
            "bezirk": ["Mitte", None, "Pankow"],
            "geometry": [{"type": "Polygon", "coordinates": []}] * 3,
        }
    )
    collection = category_collection(
        areas, {"a": "afd", "b": "volt", "c": None}, PARTY_COLORS, 1.0, {"a": "tip"}
    )
    fills = [f["properties"]["fill"] for f in collection["features"]]
    assert fills[0] == [*PARTY_COLORS["afd"], 255]
    assert fills[1][:3] == [150, 150, 150]
    assert fills[2] == NO_DATA_COLOR
    assert collection["features"][0]["properties"]["tooltip"] == "tip"
    legend = category_legend_html([("AfD", PARTY_COLORS["afd"], 12)], "Strongest party")
    assert "AfD (12)" in legend
