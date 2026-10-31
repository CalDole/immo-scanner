import math

import pandas as pd
import pytest

from immo_scanner.reference.gymnasium_education import (
    INDICATOR,
    TYPE_INDICATOR,
    weighted_abi_rows,
    weighted_type_level_rows,
)


def test_weighted_abi_rows_uses_six_nearest_current_averages():
    areas = pd.DataFrame([{"plr_id": "01010101", "x": 0.0, "y": 0.0}])
    schools = pd.DataFrame(
        [
            {
                "name": f"School {i}",
                "x": i * 1_000.0,
                "y": 0.0,
                "abi_average": grade,
                "abi_year": 2025,
                "school_type": "gymnasium",
            }
            for i, grade in enumerate([1.5, 1.7, 1.9, 2.1, 2.3, 2.5, 4.0])
        ]
    )

    rows = weighted_abi_rows(areas, schools)

    weights = [math.exp(-distance / 3_000) for distance in range(0, 6_000, 1_000)]
    expected = sum(
        w * grade for w, grade in zip(weights, [1.5, 1.7, 1.9, 2.1, 2.3, 2.5], strict=True)
    ) / sum(weights)
    assert len(rows) == 1
    assert rows[0]["indicator"] == INDICATOR
    assert rows[0]["value"] == pytest.approx(round(expected, 2))
    assert rows[0]["ref_date"] == "2025"
    assert "6 nearest" in rows[0]["label"]


def test_weighted_abi_rows_adjusts_for_the_school_type_average():
    areas = pd.DataFrame([{"plr_id": "01010101", "x": 0.0, "y": 0.0}])
    schools = pd.DataFrame(
        [
            {
                "name": "Gymnasium",
                "x": -1_000.0,
                "y": 0.0,
                "abi_average": 2.2,
                "abi_year": "2025",
                "school_type": "gymnasium",
            },
            {
                "name": "Sekundarschule",
                "x": 1_000.0,
                "y": 0.0,
                "abi_average": 2.5,
                "abi_year": "2024",
                "school_type": "sekundarschule",
            },
        ]
    )

    rows = weighted_abi_rows(areas, schools)

    assert rows[0]["value"] == 2.2
    assert rows[0]["ref_date"] == "2024, 2025"


def test_weighted_abi_rows_ignores_schools_without_an_average():
    areas = pd.DataFrame([{"plr_id": "01010101", "x": 0.0, "y": 0.0}])
    schools = pd.DataFrame(
        [
            {
                "name": "Unknown",
                "x": 0.0,
                "y": 0.0,
                "abi_average": None,
                "abi_year": None,
                "school_type": "gymnasium",
            },
            {
                "name": "Known",
                "x": 1_000.0,
                "y": 0.0,
                "abi_average": 2.0,
                "abi_year": "2025",
                "school_type": "gymnasium",
            },
        ]
    )

    rows = weighted_abi_rows(areas, schools)

    assert rows[0]["value"] == 2.0
    assert "1 nearest" in rows[0]["label"]


def test_weighted_type_level_rows_uses_the_raw_stufe():
    areas = pd.DataFrame([{"plr_id": "01010101", "x": 0.0, "y": 0.0}])
    schools = pd.DataFrame(
        [
            {
                "name": "Gymnasium",
                "x": -1_000.0,
                "y": 0.0,
                "type_level": 2,
                "school_type": "gymnasium",
            },
            {
                "name": "Sekundarschule",
                "x": 1_000.0,
                "y": 0.0,
                "type_level": 6,
                "school_type": "sekundarschule",
            },
            {
                "name": "Unknown",
                "x": 0.0,
                "y": 0.0,
                "type_level": None,
                "school_type": "gymnasium",
            },
        ]
    )

    rows = weighted_type_level_rows(areas, schools)

    assert len(rows) == 1
    assert rows[0]["topic"] == "school_load"
    assert rows[0]["indicator"] == TYPE_INDICATOR
    assert rows[0]["value"] == 4.0
    assert rows[0]["unit"] == "grade"
    assert "2 nearest" in rows[0]["label"]
