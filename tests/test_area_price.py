import numpy as np
import pandas as pd
import pytest

from immo_scanner.explore.area_price import (
    MIN_LISTINGS,
    fit_area_price,
    plr_turnout,
    weighted_correlations,
)
from immo_scanner.explore.prices import area_listing_stats


def _weighted_r(x, y, weights) -> float:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    weights = np.asarray(weights, dtype=float)
    x_mean = np.average(x, weights=weights)
    y_mean = np.average(y, weights=weights)
    cov = np.average((x - x_mean) * (y - y_mean), weights=weights)
    x_scale = np.sqrt(np.average((x - x_mean) ** 2, weights=weights))
    y_scale = np.sqrt(np.average((y - y_mean) ** 2, weights=weights))
    return float(cov / (x_scale * y_scale))


def _areas(**columns) -> pd.DataFrame:
    n = len(next(iter(columns.values())))
    frame = pd.DataFrame(columns)
    frame["area_id"] = [f"a{i}" for i in range(n)]
    if "listings" not in frame.columns:
        frame["listings"] = MIN_LISTINGS
    return frame


def test_correlation_weights_listings_and_skips_thin_areas():
    x = [0.0, 1.0, 2.0]
    y = [0.0, 10.0, 1.0]
    weights = [3.0, 3.0, 30.0]
    frame = _areas(
        listings=[*weights, 2],
        price_per_m2=[*y, 1_000_000.0],
        agh_vote_afd=[*x, 0.0],
    )

    corr = weighted_correlations(frame).set_index("key")

    assert corr.loc["agh_vote_afd", "areas"] == 3
    assert corr.loc["agh_vote_afd", "r"] == pytest.approx(_weighted_r(x, y, weights))
    assert corr.loc["agh_vote_afd", "r"] != pytest.approx(pd.Series(x).corr(pd.Series(y)))


def test_fit_recovers_a_standard_deviation_effect():
    x = np.linspace(-1, 1, 41)
    weights = np.full(41, 4.0)
    mean = np.average(x, weights=weights)
    scale = np.sqrt(np.average((x - mean) ** 2, weights=weights))
    z = (x - mean) / scale
    frame = _areas(listings=weights, price_per_m2=5_000 + 250 * z, agh_vote_afd=x)

    model = fit_area_price(frame)

    assert model.error is None
    assert model.n_used == 41
    terms = model.terms.set_index("key")
    coef = terms.loc["agh_vote_afd"]
    assert coef["coefficient"] == pytest.approx(250)
    assert coef["ci_low"] <= coef["coefficient"] <= coef["ci_high"]
    per_unit = 250 / scale
    assert coef["step"] == "1 percentage point"
    assert coef["per_unit"] == pytest.approx(per_unit)
    assert coef["per_unit_low"] == pytest.approx(coef["ci_low"] / scale)
    assert coef["per_unit_high"] == pytest.approx(coef["ci_high"] / scale)
    assert coef["pct_of_average"] == pytest.approx(per_unit / 5_000 * 100)
    assert coef["pct_low"] == pytest.approx(coef["per_unit_low"] / 5_000 * 100)
    intercept = terms.loc["intercept"]
    assert intercept["coefficient"] == pytest.approx(5_000)
    assert intercept["step"] == ""
    assert np.isnan(intercept["per_unit"])
    assert np.isnan(intercept["pct_of_average"])
    assert np.average(model.areas["residual"], weights=model.areas["listings"]) == pytest.approx(
        0, abs=1e-6
    )


def test_mostly_blank_factor_stays_out_of_the_fit_and_in_the_correlations():
    n = 40
    low_wage = np.linspace(0, 1, n)
    low_wage[:12] = np.nan
    frame = _areas(
        listings=np.full(n, 4),
        price_per_m2=np.linspace(3_000, 8_000, n),
        agh_vote_afd=np.linspace(0, 20, n),
        low_wage=low_wage,
    )

    model = fit_area_price(frame)

    assert model.n_used == n
    assert "low_wage" not in set(model.terms["key"])
    assert any("Low-wage share" in note for note in model.notes)
    low = model.correlations.set_index("key").loc["low_wage"]
    assert low["areas"] == n - 12
    assert not low["in_model"]


def test_factor_missing_on_a_quarter_of_areas_stays_in_the_fit():
    n = 40
    low_wage = np.arange(n, dtype=float) ** 2
    low_wage[:10] = np.nan
    frame = _areas(
        listings=np.full(n, 4),
        price_per_m2=np.linspace(3_000, 8_000, n),
        agh_vote_afd=np.linspace(0, 20, n),
        low_wage=low_wage,
    )

    model = fit_area_price(frame)

    assert "low_wage" in set(model.terms["key"])
    assert model.n_used == n - 10
    assert model.notes == ()


def test_duplicate_factor_is_dropped_as_collinear():
    x = np.linspace(0, 5, 25)
    frame = _areas(
        listings=np.full(25, 4),
        price_per_m2=2_000 + 100 * x,
        agh_vote_afd=x,
        agh_vote_gruene=x.copy(),
    )

    model = fit_area_price(frame)

    assert model.error is None
    assert "agh_vote_gruene" in model.omitted
    assert "agh_vote_afd" not in model.omitted
    assert model.n_used == 25


def test_plr_turnout_weights_by_the_votes_falling_in_the_area():
    weights = pd.DataFrame(
        {
            "scope": ["abgeordnetenhaus", "abgeordnetenhaus", "bundestag"],
            "district_id": ["d1", "d2", "d1"],
            "plr_id": ["p", "p", "p"],
            "weight": [1.0, 3.0, 100.0],
        }
    )
    districts = pd.DataFrame(
        {
            "scope": ["abgeordnetenhaus", "abgeordnetenhaus", "bundestag"],
            "area_id": ["d1", "d2", "d1"],
            "turnout_pct": [40.0, 80.0, 1.0],
        }
    )

    result = plr_turnout(weights, districts)

    assert len(result) == 1
    assert result.loc[0, "area_id"] == "p"
    assert result.loc[0, "value"] == pytest.approx(70.0)


def _listing(**overrides: object) -> dict:
    row = {
        "source": "immoscout24",
        "title": "3-Zimmer-Wohnung",
        "price": 300_000,
        "living_area": 60,
        "year_built": 1990,
        "plr_id": "01100101",
        "bezirk": "Mitte",
    }
    row.update(overrides)
    return row


def test_area_listing_stats_medians_and_exclusions():
    frame = pd.DataFrame(
        [
            _listing(price=200_000, living_area=40, year_built=1900),
            _listing(price=300_000, living_area=60, year_built=2000),
            _listing(price=450_000, living_area=90, year_built=None),
            _listing(source="thinkimmo", price=1, living_area=1, year_built=2020),
            _listing(source="ThinkImmo", price=1, living_area=1, year_built=2020),
            _listing(title="Rohling am Park", price=1, living_area=1, year_built=1800),
            _listing(plr_id="01200202", price=100_000, living_area=50, year_built=2010),
        ]
    )

    stats = area_listing_stats(frame).set_index("area_id")

    assert stats.loc["01100101", "listings"] == 3
    assert stats.loc["01100101", "value"] == 5_000
    assert stats.loc["01100101", "median_living_area"] == 60
    assert stats.loc["01100101", "median_year_built"] == 1950
    assert stats.loc["01200202", "listings"] == 1
    assert stats.loc["01200202", "median_year_built"] == 2010
    assert "thinkimmo" not in set(stats.index)
