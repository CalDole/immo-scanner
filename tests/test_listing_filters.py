import pandas as pd
from listing_filters import (
    energy_class_options,
    filter_energy_class,
    filter_rohling,
    has_commute_time,
    normalized_energy_class,
)

from immo_scanner.explore.data import titles_are_rohling


def test_has_commute_time_keeps_a_listing_with_any_destination():
    frame = pd.DataFrame(
        {
            "commute_minutes": [None, 20.0, None, None],
            "commute_workplace2_minutes": [None, None, 35.0, None],
            "commute_citycenter_minutes": [None, None, None, 12.0],
        }
    )
    assert has_commute_time(frame).tolist() == [False, True, True, True]


def test_has_commute_time_ignores_missing_columns():
    frame = pd.DataFrame({"title": ["a", "b"]})
    assert has_commute_time(frame).tolist() == [True, True]


def test_normalized_energy_class_accepts_certificate_labels():
    assert normalized_energy_class("A+") == "A+"
    assert normalized_energy_class("a_plus") == "A+"
    assert normalized_energy_class("A++") == "A++"
    assert normalized_energy_class(" d ") == "D"
    assert normalized_energy_class("NO_INFORMATION") is None
    assert normalized_energy_class(None) is None
    assert normalized_energy_class(float("nan")) is None


def test_energy_class_options_follow_certificate_order():
    frame = pd.DataFrame({"energy_efficiency_class": ["H", "A+", "D", None, "nope", "A++"]})
    assert energy_class_options(frame) == ["A++", "A+", "D", "H"]


def test_filter_energy_class_keeps_selected_labels_only():
    frame = pd.DataFrame(
        {
            "title": ["plus", "mid", "blank", "junk"],
            "energy_efficiency_class": ["A_PLUS", "D", None, "Fernwärme"],
        }
    )
    kept = filter_energy_class(frame, ["A+"])
    assert kept["title"].tolist() == ["plus"]
    assert filter_energy_class(frame, []).equals(frame)


def test_titles_are_rohling_matches_the_whole_word():
    titles = pd.Series(
        [
            "Altbau Rohling in Kreuzberg",
            "schöner rohling",
            "ROHLING, bezugsfrei",
            "Rohlinge im Paket",
            "Wohnungsrohling",
            None,
        ]
    )
    assert titles_are_rohling(titles).tolist() == [True, True, True, False, False, False]


def test_filter_rohling_keeps_the_selected_kind():
    frame = pd.DataFrame(
        {
            "title": ["Rohling", "fertig", "noch ein Rohling"],
            "is_rohling": [True, False, True],
        }
    )
    assert filter_rohling(frame, ["Rohling"])["title"].tolist() == ["Rohling", "noch ein Rohling"]
    assert filter_rohling(frame, ["not Rohling"])["title"].tolist() == ["fertig"]
    assert filter_rohling(frame, []).equals(frame)
    assert filter_rohling(frame, ["Rohling", "not Rohling"]).equals(frame)
