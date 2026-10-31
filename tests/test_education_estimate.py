import pandas as pd

from immo_scanner.explore.education import (
    allocate_people,
    blend_turnout,
    estimate_areas,
    qualification_mix,
)
from immo_scanner.reference.party_education import COLUMNS, ROWS

PROFILES = {
    "a": {"hauptschule_pct": 10.0, "mittlere_reife_pct": 20.0, "abitur_pct": 30.0, "hochschule_pct": 40.0},
    "b": {"hauptschule_pct": 0.0, "mittlere_reife_pct": 0.0, "abitur_pct": 50.0, "hochschule_pct": 50.0},
}


def _published_profiles() -> dict[str, dict[str, float]]:
    columns = COLUMNS[2:]
    return {
        row[0]: dict(zip(columns, row[2:], strict=True))
        for row in ROWS
    }


def test_mix_renormalizes_parties_without_a_profile():
    mix = qualification_mix({"a": 30.0, "b": 20.0, "other": 50.0}, PROFILES)
    assert mix is not None
    assert mix.covered_vote_pct == 50
    assert mix.pct["hauptschule"] == 6
    assert mix.pct["mittlere_reife"] == 12
    assert mix.pct["abitur"] == 38
    assert mix.pct["hochschule"] == 44


def test_mix_is_missing_when_no_profiled_party_has_votes():
    assert qualification_mix({}, PROFILES) is None
    assert qualification_mix({"other": 80.0}, PROFILES) is None


def test_published_profiles_weight_by_vote_share():
    mix = qualification_mix({"gruene": 75.0, "afd": 25.0}, _published_profiles())
    assert mix is not None
    # Greens 27 / AfD 6, and Greens 45 / AfD 4.
    assert mix.pct["abitur"] == 0.75 * 27 + 0.25 * 6
    assert mix.pct["hochschule"] == 0.75 * 45 + 0.25 * 4
    assert round(sum(mix.pct.values()), 6) == 100


def test_planungsraum_counts_sum_to_adults():
    shares = pd.DataFrame(
        [
            {"area_id": "01020304", "party": "a", "value": 30.0},
            {"area_id": "01020304", "party": "b", "value": 20.0},
            {"area_id": "01020304", "party": "other", "value": 50.0},
            {"area_id": "02030405", "party": "a", "value": 100.0},
        ]
    )
    population = pd.DataFrame(
        [
            {
                "area_id": "01020304",
                "name": "Beispiel",
                "bezirk": "Mitte",
                "population": 1000,
                "share_18_to_64_pct": 60.0,
                "share_65_plus_pct": 20.0,
            },
            {
                "area_id": "02030405",
                "name": "Ohne Alter",
                "bezirk": "Pankow",
                "population": 100,
                "share_18_to_64_pct": None,
                "share_65_plus_pct": None,
            },
        ]
    )
    frame = estimate_areas(shares, PROFILES, population)
    first = frame[frame["area_id"] == "01020304"]
    assert list(first["level"]) == ["hauptschule", "mittlere_reife", "abitur", "hochschule"]
    assert first["adults"].iloc[0] == 800
    assert int(first["people"].sum()) == 800
    assert first["covered_vote_pct"].iloc[0] == 50
    assert list(first["people"]) == [48, 96, 304, 352]

    second = frame[frame["area_id"] == "02030405"]
    assert second["adults"].iloc[0] == 100
    assert int(second["people"].sum()) == 100


def test_volt_alias_uses_the_volt_profile():
    profiles = _published_profiles()
    mix = qualification_mix({"voltdeutschland": 100.0}, profiles)
    assert mix is not None
    assert mix.covered_vote_pct == 100
    assert mix.pct["hauptschule"] == 4
    assert mix.pct["abitur"] == 27
    assert mix.pct["hochschule"] == 45


def test_bsw_is_included_in_the_voter_mix():
    profiles = _published_profiles()
    mix = qualification_mix({"bsw": 40.0, "other": 60.0}, profiles)
    assert mix is not None
    assert mix.covered_vote_pct == 40
    assert mix.pct["hauptschule"] == 17
    assert mix.pct["mittlere_reife"] == 45
    assert mix.pct["abitur"] == 24
    assert mix.pct["hochschule"] == 14


def test_nichtwaehler_is_not_read_from_party_shares():
    profiles = _published_profiles()
    mix = qualification_mix({"gruene": 100.0, "nichtwaehler": 50.0}, profiles)
    voter = qualification_mix({"gruene": 100.0}, profiles)
    assert mix is not None and voter is not None
    assert mix.pct == voter.pct
    assert mix.covered_vote_pct == 100


def test_turnout_blends_toward_the_nichtwaehler_profile():
    profiles = _published_profiles()
    voter = qualification_mix({"gruene": 100.0}, profiles)
    assert voter is not None
    blended = blend_turnout(voter, 60, profiles)
    voted = 0.6
    assert blended.pct["hochschule"] == voted * 45 + (1 - voted) * 5
    assert blended.pct["hauptschule"] == voted * 4 + (1 - voted) * 38
    assert round(sum(blended.pct.values()), 6) == 100
    assert blend_turnout(voter, None, profiles).pct == voter.pct
    assert blend_turnout(voter, float("nan"), profiles) is voter


def test_missing_area_turnout_leaves_the_voter_mix():
    profiles = _published_profiles()
    shares = pd.DataFrame([{"area_id": "01020304", "party": "gruene", "value": 100.0}])
    population = pd.DataFrame(
        [
            {
                "area_id": "01020304",
                "name": "Beispiel",
                "bezirk": "Mitte",
                "population": 100,
                "share_18_to_64_pct": 100.0,
                "share_65_plus_pct": 0.0,
            }
        ]
    )
    plain = estimate_areas(shares, profiles, population)
    blended = estimate_areas(shares, profiles, population, {"01020304": 60.0})
    assert plain.loc[plain["level"] == "hochschule", "pct"].iloc[0] == 45
    assert blended.loc[blended["level"] == "hochschule", "pct"].iloc[0] == 0.6 * 45 + 0.4 * 5


def test_allocate_people_uses_largest_remainder():
    counts = allocate_people(10, {"a": 33.0, "b": 33.0, "c": 34.0})
    assert counts == {"a": 3, "b": 3, "c": 4}
    assert sum(counts.values()) == 10
