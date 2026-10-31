from types import SimpleNamespace

import pandas as pd

from immo_scanner.enrich.commute import ORIGIN_BATCH, commute_rows


def test_commute_rows_keep_unreachable_places_empty():
    places = [SimpleNamespace(name="workplace"), SimpleNamespace(name="citycenter")]
    matrix = pd.DataFrame(
        {"from_id": [7, 7], "to_id": ["workplace", "citycenter"], "travel_time": [25, float("nan")]}
    )
    rows = commute_rows([(7, 13.4, 52.5)], places, matrix)
    by_place = {row["place_name"]: row for row in rows}
    assert by_place["workplace"]["minutes"] == 25
    assert by_place["citycenter"]["minutes"] is None
    assert by_place["workplace"]["origin_lon"] == 13.4


def test_one_batch_stays_below_the_postgres_parameter_limit():
    assert ORIGIN_BATCH * 3 * 7 < 65_535
