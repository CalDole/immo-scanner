import numpy as np
import pandas as pd
import pytest

from immo_scanner.explore.location_model import (
    ReferenceFlat,
    age_band,
    attribute_importance,
    fit_location_model,
    flat_contributions,
    quarter_contributions,
)

YEAR = 2026
OLD_IN_AREA = 0.40
CENTRE_PER_KM = -0.03
ELEVATOR = 0.05
BALCONY = 0.03
BATHROOM = 0.04
LOG_AREA = -0.10
# Log-point premium of each band over pre-1919.
BANDS = {
    "pre_1919": (1900, 0.0),
    "1949_1969": (1960, -0.15),
    "1970_1989": (1980, -0.12),
    "2010_recent": (2015, 0.10),
}


def _sample(
    n_areas: int = 40, seed: int = 3, noise: float = 0.05
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Listings whose Planungsraum level rises with its share of pre-1919 buildings."""
    rng = np.random.default_rng(seed)
    rows = []
    area_rows = []
    for a in range(n_areas):
        area_id = f"{a:08d}"
        old_share = rng.uniform(0.05, 0.9)
        centre = rng.uniform(1, 15)
        premium = rng.normal(0, 0.03)
        level = 8.3 + OLD_IN_AREA * old_share + CENTRE_PER_KM * centre + premium
        area_rows.append(
            {"area_id": area_id, "name": f"Area {a}", "bezirk": "Mitte", "centre_km": centre}
        )
        for _ in range(int(rng.integers(25, 45))):
            band = (
                "pre_1919"
                if rng.uniform() < old_share
                else ["1949_1969", "1970_1989", "2010_recent"][int(rng.integers(0, 3))]
            )
            year, band_effect = BANDS[band]
            living = rng.uniform(35, 140)
            elevator = float(rng.integers(0, 2))
            balcony = float(rng.integers(0, 2))
            baths = float(rng.integers(1, 3))
            log_m2 = (
                level
                + band_effect
                + LOG_AREA * np.log(living)
                + ELEVATOR * elevator
                + BALCONY * balcony
                + BATHROOM * baths
                + rng.normal(0, noise)
            )
            rows.append(
                {
                    "id": len(rows),
                    "title": f"Flat {len(rows)}",
                    "url": "https://example.test/flat",
                    "source": "immoscout24",
                    "bezirk": "Mitte",
                    "plr_id": area_id,
                    "living_area": living,
                    "price": float(np.exp(log_m2) * living),
                    "building_age_years": YEAR - year,
                    "rooms": max(1.0, round(living / 30)),
                    "bathrooms": baths,
                    "has_elevator": bool(elevator),
                    "has_balcony": bool(balcony),
                    "has_parking": False,
                    "has_cellar": True,
                    "energy_efficiency_class": "D",
                    "is_rented": False,
                }
            )
    areas = pd.DataFrame(area_rows)
    areas = pd.concat(
        [
            areas,
            pd.DataFrame(
                [{"area_id": "99999999", "name": "Empty", "bezirk": "Pankow", "centre_km": 8.0}]
            ),
        ],
        ignore_index=True,
    )
    return pd.DataFrame(rows), areas


@pytest.fixture(scope="module")
def fitted():
    features, areas = _sample()
    return fit_location_model(features, areas, quarter_keys=("centre_km",), current_year=YEAR)


def test_age_band_edges():
    assert age_band(1918, YEAR) == "pre_1919"
    assert age_band(1919, YEAR) == "1919_1948"
    assert age_band(1969, YEAR) == "1949_1969"
    assert age_band(2010, YEAR) == "2010_recent"
    assert age_band(YEAR - 1, YEAR) == "new_build"
    assert age_band(None, YEAR) == "unknown"
    assert age_band(1200, YEAR) == "unknown"


def test_pooled_fit_is_confounded_but_within_fits_are_not(fitted):
    comparison = fitted.comparison.set_index("key")
    truth = BANDS["1949_1969"][1]
    assert comparison.loc["age:1949_1969", "naive_coefficient"] < truth - 0.05
    assert comparison.loc["age:1949_1969", "fixed_coefficient"] == pytest.approx(truth, abs=0.02)
    assert comparison.loc["age:1949_1969", "mundlak_coefficient"] == pytest.approx(truth, abs=0.02)


def test_flat_effects_recovered_and_both_estimates_agree(fitted):
    comparison = fitted.comparison.set_index("key")
    expected = {
        "has_elevator": ELEVATOR,
        "has_balcony": BALCONY,
        "bathrooms": BATHROOM,
        "log_living_area": LOG_AREA,
        "age:1970_1989": BANDS["1970_1989"][1],
        "age:2010_recent": BANDS["2010_recent"][1],
    }
    for key, value in expected.items():
        assert comparison.loc[key, "fixed_coefficient"] == pytest.approx(value, abs=0.02), key
        assert comparison.loc[key, "mundlak_coefficient"] == pytest.approx(value, abs=0.02), key
    assert not comparison["flagged"].any()
    gaps = (comparison["mundlak_coefficient"] - comparison["fixed_coefficient"]).abs()
    assert float(gaps.max()) < 1e-3


def test_quarter_attribute_and_building_stock_recovered(fitted):
    terms = fitted.terms.set_index("key")
    centre = fitted.quarter_design["z:centre_km"]
    sampled = fitted.listings["area_id"].unique()
    _, areas = _sample()
    raw = areas.set_index("area_id")["centre_km"].reindex(sampled)
    scale = float(raw.std(ddof=0))
    assert terms.loc["z:centre_km", "coefficient"] == pytest.approx(CENTRE_PER_KM * scale, rel=0.25)
    assert centre.reindex(sampled).mean() == pytest.approx(0.0, abs=1e-9)
    # Pre-1919 is the baseline band, so a larger share of post-war listings means a
    # smaller pre-1919 share and a cheaper area. Listing shares are noisy, so only the sign is fixed.
    assert terms.loc["mean:age:1949_1969", "coefficient"] < 0


def test_area_without_listings_gets_attribute_only_reference(fitted):
    quarters = fitted.quarters.set_index("area_id")
    empty = quarters.loc["99999999"]
    assert bool(empty["from_attributes_only"])
    assert empty["premium"] == 0.0
    assert empty["listings"] == 0
    assert empty["low"] < empty["reference_per_m2"] < empty["high"]
    sampled = quarters.loc[~quarters["from_attributes_only"]]
    assert ((sampled["high"] - sampled["low"]) < (empty["high"] - empty["low"])).all()


def test_flat_contributions_add_up_to_prediction(fitted):
    index = fitted.listings.index[7]
    table = flat_contributions(fitted, index)
    predicted = float(fitted.listings.loc[index, "predicted_price"])
    assert table["euros"].iloc[:-1].sum() == pytest.approx(predicted, rel=1e-9)
    assert table["euros"].iloc[-1] == pytest.approx(predicted, rel=1e-9)


def test_quarter_contributions_add_up_to_reference(fitted):
    area_id = fitted.listings["area_id"].iloc[0]
    table = quarter_contributions(fitted, area_id)
    reference = fitted.quarters.set_index("area_id").loc[area_id, "reference_per_m2"]
    body = table.iloc[1:-1]
    assert body["log_points"].sum() == pytest.approx(table["log_points"].iloc[-1])
    assert table["per_m2"].iloc[-1] == pytest.approx(reference, rel=1e-9)
    short = quarter_contributions(fitted, area_id, detail=False)
    assert (short["group"] == "Building stock").sum() == 1
    assert short["log_points"].iloc[-1] == pytest.approx(table["log_points"].iloc[-1])


def test_attribute_importance_shares_add_to_one(fitted):
    table = attribute_importance(fitted)
    assert table["share"].sum() == pytest.approx(1.0)
    assert {"Location and access", "Building stock", "Unexplained location premium"} <= set(
        table["group"]
    )


def test_variance_diagnostics(fitted):
    assert 0 < fitted.icc < 1
    assert fitted.explained_by_area_terms > 0.5
    assert fitted.explained_by_attributes > 0


def test_missing_attribute_gets_not_stated_dummy():
    features, areas = _sample(n_areas=20, seed=5)
    areas.loc[[0, 1, 2], "centre_km"] = np.nan
    model = fit_location_model(features, areas, quarter_keys=("centre_km",), current_year=YEAR)
    assert model.error is None
    assert "zmissing:centre_km" in model.params.index
    design = model.quarter_design
    blank = areas.loc[[0, 1, 2], "area_id"]
    assert (design.loc[blank, "z:centre_km"] == 0.0).all()
    assert (design.loc[blank, "zmissing:centre_km"] == 1.0).all()


def test_reference_flat_changes_level_not_ranking(fitted):
    from immo_scanner.explore.location_model import quarter_reference

    larger = quarter_reference(fitted, ReferenceFlat(living_area=120, has_elevator=True))
    base = fitted.quarters
    ratio = larger["reference_per_m2"] / base["reference_per_m2"]
    assert float(ratio.std()) == pytest.approx(0.0, abs=1e-9)
