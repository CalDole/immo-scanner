import math

import pandas as pd
import pytest

from immo_scanner.explore.score_weights import load_weights, save_weights, weights_version
from immo_scanner.explore.scoring import (
    SUBSCORES,
    Weights,
    apply_weights,
    avg_commute_minutes,
    balconies_score,
    basement_score,
    bathrooms_score,
    building_age_class_score,
    building_age_score,
    citizenship_score,
    commute_score,
    crime_score,
    education_score,
    effective_shares,
    energy_class_score,
    floor_area_score,
    group_scores,
    hausgeld_score,
    heating_demand_score,
    incident_score,
    kitchen_score,
    oic_score,
    origin_diversity_score,
    parking_score,
    rooms_score,
    school_type_score,
    tolerance_score,
    with_origin_neighborhood_scores,
    with_safety_neighborhood_scores,
    with_school_type_neighborhood_score,
)


def test_commute_shorter_is_better():
    assert avg_commute_minutes(20, 30) == 24
    assert commute_score(0, 0) == 1
    assert commute_score(30, 30) == 0.5
    assert commute_score(60, 60) == 0
    assert commute_score(90, 90) == 0
    assert commute_score(40, None) == commute_score(40, 40)


def test_energy_and_hausgeld_and_age():
    assert energy_class_score("A+") == energy_class_score("A") == 1.25
    assert energy_class_score("C") == 0.8
    assert energy_class_score("G") == 0
    assert hausgeld_score(300) == 1
    assert hausgeld_score(800) == 0
    assert hausgeld_score(550) == 0.5
    assert building_age_score(0) == 1
    assert building_age_score(50) == 0.5
    assert building_age_score(120) == 0
    assert building_age_class_score(75) == 0.75
    assert heating_demand_score(220) == 0
    assert heating_demand_score(0) == 1


def test_flat_subscores():
    assert rooms_score(2) == 0
    assert rooms_score(3) == 1
    assert rooms_score(4) == 1.5
    assert rooms_score(5) == 2
    assert rooms_score(3.5) == 1.25
    assert floor_area_score(75) == 1
    assert basement_score(True) == 1
    assert basement_score(False) == 0.8
    assert parking_score(False) == 0
    assert balconies_score(2) == 1.5
    assert balconies_score(None, has_balcony=True) == 1
    assert balconies_score(0) == 0
    assert bathrooms_score(2) == 1
    assert bathrooms_score(1) == 0.5
    assert kitchen_score(False) == 0.95


def test_neighborhood_and_total():
    assert education_score([2.4, 2.4, 2.4]) == 1 - 2.4 / 6
    # AfD at 8.7 / 0.29 percent, everyone else 0 → the weighted sum is 8.7 and the score is 1.
    assert abs(tolerance_score({"afd": 8.7 / 0.29}) - 1) < 1e-9
    groups = group_scores(
        {
            "commute_score": 1,
            "energy_class_score": 1,
            "rooms_score": 1,
            "parking_score": 1,
            "education_score": 1,
        }
    )
    assert groups["distance_score"] == 1
    assert groups["flat_score"] == 1
    assert groups["total_score"] == 1
    split = group_scores({"rooms_score": 1, "parking_score": 0})
    assert split["flat_score"] == 0.5


def test_origin_neighborhood_scores_use_fixed_anchors():
    assert origin_diversity_score(78) == 0
    assert origin_diversity_score(83) == 0.5
    assert origin_diversity_score(88) == 1
    assert origin_diversity_score(60) == 0
    assert origin_diversity_score(95) == 1
    assert origin_diversity_score(None) is None
    assert citizenship_score(76) == 1
    assert citizenship_score(69) == 0.5
    assert citizenship_score(83) == 0.5
    assert citizenship_score(62) == 0
    assert citizenship_score(90) == 0
    assert citizenship_score(20) == 0
    assert oic_score(5) == 1
    assert oic_score(0) == 1
    assert oic_score(15) == 0.5
    assert oic_score(25) == 0
    assert oic_score(48) == 0
    assert oic_score(None) is None
    mixed = group_scores({"education_score": 1, "origin_diversity_score": 0})
    assert mixed["neighborhood_score"] == 0.5


def test_school_type_score_lower_is_better():
    assert school_type_score(1) == 1
    assert school_type_score(4) == 0.5
    assert school_type_score(7) == 0
    assert school_type_score(3.4) == pytest.approx(0.6)
    assert school_type_score(0) == 1
    assert school_type_score(9) == 0
    assert school_type_score(None) is None
    mixed = group_scores({"education_score": 1, "school_type_score": 0})
    assert mixed["neighborhood_score"] == 0.5


def test_crime_and_incident_scores_use_fixed_anchors():
    assert crime_score(1400) == 1
    assert crime_score(3000) == 0.5
    assert crime_score(4600) == 0
    assert crime_score(795) == 1
    assert crime_score(9212) == 0
    assert crime_score(None) is None
    assert incident_score(0) == 1
    assert incident_score(0.3) == 0.5
    assert incident_score(0.6) == 0
    assert incident_score(8.8) == 0
    assert incident_score(None) is None
    mixed = group_scores({"education_score": 1, "crime_score": 0})
    assert mixed["neighborhood_score"] == 0.5


def test_weights_change_groups_and_total():
    parts = {"rooms_score": 1, "parking_score": 0, "commute_score": 0.5, "education_score": 1}
    weights = Weights()
    weights.subscores["rooms_score"] = 3
    weights.groups["flat_score"] = 2
    groups = group_scores(parts, weights)
    assert groups["flat_score"] == 0.75
    # Distance 0.5 (weight 1), flat 0.75 (weight 2), neighbourhood 1 (weight 1); building missing.
    assert groups["total_score"] == pytest.approx((0.5 + 2 * 0.75 + 1) / 4)


def test_zero_weight_switches_an_input_off():
    weights = Weights()
    weights.subscores["parking_score"] = 0
    assert group_scores({"rooms_score": 1, "parking_score": 0}, weights)["flat_score"] == 1
    weights.groups["distance_score"] = 0
    groups = group_scores({"commute_score": 0, "education_score": 1}, weights)
    assert groups["total_score"] == 1
    only_zero = Weights()
    only_zero.subscores["commute_score"] = 0
    assert group_scores({"commute_score": 1}, only_zero)["distance_score"] is None


def test_frame_weights_match_the_scalar_path():
    rows = [
        {"commute_score": 0.4, "rooms_score": 1.5, "parking_score": None, "oic_score": 0.2},
        {"commute_score": None, "energy_class_score": 1.25, "hausgeld_score": 0.1},
        {"education_score": None},
    ]
    frame = pd.DataFrame(rows, columns=[sub.column for sub in SUBSCORES]).astype(float)
    weights = Weights()
    weights.subscores["energy_class_score"] = 0.5
    weights.groups["neighborhood_score"] = 3
    scored = apply_weights(frame, weights)
    for index, row in enumerate(rows):
        expected = group_scores(row, weights)
        for column, value in expected.items():
            got = scored.loc[index, column]
            if value is None:
                assert math.isnan(got)
            else:
                assert got == pytest.approx(value)


def test_equal_weights_keep_the_plain_means():
    frame = pd.DataFrame({"rooms_score": [1.0, None], "parking_score": [0.0, 1.0]})
    scored = apply_weights(frame)
    assert list(scored["flat_score"]) == [0.5, 1.0]
    assert list(scored["total_score"]) == [0.5, 1.0]


def test_effective_shares_sum_to_one():
    weights = Weights()
    weights.groups["distance_score"] = 2
    shares = effective_shares(weights)
    assert sum(shares.values()) == pytest.approx(1)
    assert shares["commute_score"] == pytest.approx(2 / 5)


def test_weights_file_round_trip_and_fallback(tmp_path):
    path = tmp_path / "weights.json"
    assert load_weights(path).to_dict() == Weights().to_dict()
    path.write_text("not json", encoding="utf-8")
    assert load_weights(path).to_dict() == Weights().to_dict()
    path.write_text(
        '{"groups": {"flat_score": 2, "bogus": 9}, "subscores": {"rooms_score": -1}}',
        encoding="utf-8",
    )
    loaded = load_weights(path)
    assert loaded.group("flat_score") == 2
    assert loaded.group("distance_score") == 1
    assert loaded.subscore("rooms_score") == 0
    assert "bogus" not in loaded.groups
    before = weights_version(path)
    loaded.groups["flat_score"] = 3
    save_weights(loaded, path)
    assert load_weights(path).group("flat_score") == 3
    assert weights_version(path) != before


def test_origin_scores_are_added_to_the_listing_features_sql():
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "src/immo_scanner/db/alembic/versions/0026_grundschulen.py"
    )
    spec = spec_from_file_location("grundschulen_sql", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    original = migration._listing_features_sql()
    sql = with_origin_neighborhood_scores(original)
    assert "AS origin_diversity_score" in sql
    assert "AS citizenship_score" in sql
    assert "AS oic_score" in sql
    assert "origin_key = 'simpson'" in sql
    assert "origin_key = 'oic'" in sql
    assert "indicator = 'german_share_mss'" in sql
    assert "(s.origin_diversity_score IS NOT NULL)::int" in sql
    assert "AS origin_diversity_score" not in original
    with pytest.raises(RuntimeError):
        with_origin_neighborhood_scores(sql)


def test_safety_scores_are_added_to_the_listing_features_sql():
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "src/immo_scanner/db/alembic/versions/0026_grundschulen.py"
    )
    spec = spec_from_file_location("grundschulen_sql", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    origin = with_origin_neighborhood_scores(migration._listing_features_sql())
    sql = with_safety_neighborhood_scores(origin)
    assert "AS crime_score" in sql
    assert "AS incident_score" in sql
    assert "offense = 'neighbourhood'" in sql
    assert "geo_precision IN ('street', 'station')" in sql
    assert "INTERVAL '3 years'" in sql
    assert "lor_population < 200" in sql
    assert "(s.crime_score IS NOT NULL)::int" in sql
    assert "(s.incident_score IS NOT NULL)::int" in sql
    assert "AS crime_score" not in origin
    with pytest.raises(RuntimeError):
        with_safety_neighborhood_scores(sql)


def test_school_type_score_is_added_to_the_listing_features_sql():
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "src/immo_scanner/db/alembic/versions/0036_engelvoelkers_exposes.py"
    )
    spec = spec_from_file_location("engelvoelkers_sql", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    current = migration._listing_features_sql()
    sql = with_school_type_neighborhood_score(current)
    assert "AS school_type_level" in sql
    assert "AS school_type_score" in sql
    assert "indicator = 'nearby_school_type_level'" in sql
    assert "(7.0 - school_type.value) / 6.0" in sql
    assert "(s.school_type_score IS NOT NULL)::int" in sql
    assert "AS school_type_score" not in current
    with pytest.raises(RuntimeError):
        with_school_type_neighborhood_score(sql)
