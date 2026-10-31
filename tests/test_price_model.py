import numpy as np
import pandas as pd
import pytest

from immo_scanner.explore.price_model import (
    clean_energy_class,
    clean_rental,
    contributions,
    fit_price_model,
    formula_lines,
    missing_rates_by_source,
)


def test_clean_energy_class():
    assert clean_energy_class("A+") == "A+"
    assert clean_energy_class("a") == "A"
    assert clean_energy_class("A++") == "A+"
    assert clean_energy_class("B") == "B"
    assert clean_energy_class("H") == "H"
    assert clean_energy_class(None) == "unknown"
    assert clean_energy_class("") == "unknown"
    assert clean_energy_class(float("nan")) == "unknown"


def test_clean_rental_treats_a_blank_as_unknown():
    assert clean_rental(True) == "rented"
    assert clean_rental(False) == "unrented"
    assert clean_rental(None) == "unknown"
    assert clean_rental(float("nan")) == "unknown"
    assert clean_rental("yes") == "rented"


# Each euro of living area at the leave-one-out Planungsraum rate.
LOCAL_EUROS = 0.5
RENTED_EUROS = 12_000
UNKNOWN_RENTAL_EUROS = 8_000


def _planungsraum(n: int) -> list[str]:
    groups = max(2, min(6, n // 2))
    return [f"{i % groups:02d}" for i in range(n)]


def _exogenous(
    area: np.ndarray,
    age: np.ndarray,
    rooms: np.ndarray,
    baths: np.ndarray,
    elevator: np.ndarray,
    balcony: np.ndarray,
    parking: np.ndarray,
    cellar: np.ndarray,
    energy: np.ndarray,
    rental: np.ndarray,
) -> np.ndarray:
    return (
        100_000
        + 5_000 * area
        - 800 * age
        + 20_000 * rooms
        + 15_000 * baths
        + 25_000 * elevator
        + 10_000 * balcony
        + 30_000 * parking
        + 5_000 * cellar
        + np.where(energy == "A+", 50_000, 0)
        + np.where(energy == "A", 40_000, 0)
        + np.where(energy == "G", -20_000, 0)
        + np.where(rental == "rented", RENTED_EUROS, 0)
        + np.where(rental == "unknown", UNKNOWN_RENTAL_EUROS, 0)
    )


def _rental_values(n: int) -> list[object]:
    pattern: list[object] = [False, True, None]
    return [pattern[i % 3] for i in range(n)]


def _rental_labels(values: pd.Series) -> np.ndarray:
    labels = []
    for value in values.tolist():
        if value is True:
            labels.append("rented")
        elif value is False:
            labels.append("unrented")
        else:
            labels.append("unknown")
    return np.array(labels)


def _price_with_local(
    area: pd.Series, plr: pd.Series, exogenous: pd.Series, gamma: float = LOCAL_EUROS
) -> pd.Series:
    """Asking prices whose leave-one-out local euros carry the coefficient `gamma`.

    Within each Planungsraum the €/m² values are the solution of
    price / area = exogenous / area + gamma × (the leave-one-out €/m²).
    """
    price = pd.Series(np.nan, index=exogenous.index, dtype=float)
    grouped = pd.DataFrame({"area": area, "plr": plr, "exo": exogenous})
    for labels in grouped.groupby("plr", sort=False).groups.values():
        idx = list(labels)
        areas = grouped.loc[idx, "area"].to_numpy(dtype=float)
        per_m2 = grouped.loc[idx, "exo"].to_numpy(dtype=float) / areas
        count = len(idx)
        total = float(per_m2.sum()) / (1 - gamma)
        sqm = (per_m2 * (count - 1) + gamma * total) / (count - 1 + gamma)
        price.loc[idx] = sqm * areas
    return price


def _sample(n: int = 240, *, noise: float = 0.0, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    area = rng.uniform(40, 140, n)
    age = rng.uniform(0, 120, n)
    rooms = rng.integers(2, 6, n).astype(float)
    baths = rng.integers(1, 4, n).astype(float)
    elevator = rng.integers(0, 2, n).astype(float)
    balcony = rng.integers(0, 2, n).astype(float)
    parking = rng.integers(0, 2, n).astype(float)
    cellar = rng.integers(0, 2, n).astype(float)
    energy = np.resize(np.array(["D", "A+", "A", "G", "unknown"]), n)
    rental = _rental_values(n)
    exogenous = _exogenous(
        area,
        age,
        rooms,
        baths,
        elevator,
        balcony,
        parking,
        cellar,
        energy,
        _rental_labels(pd.Series(rental)),
    )
    exogenous = exogenous + rng.normal(0, noise, n)
    frame = pd.DataFrame(
        {
            "id": np.arange(n),
            "title": [f"Flat {i}" for i in range(n)],
            "url": "https://example.test/flat",
            "source": "immoscout24",
            "bezirk": "Mitte",
            "living_area": area,
            "building_age_years": age,
            "rooms": rooms,
            "bathrooms": baths,
            "has_elevator": elevator.astype(bool),
            "has_balcony": balcony.astype(bool),
            "has_parking": parking.astype(bool),
            "has_cellar": cellar.astype(bool),
            "energy_efficiency_class": energy,
            "is_rented": rental,
            "plr_id": _planungsraum(n),
        }
    )
    frame["price"] = _price_with_local(
        frame["living_area"], frame["plr_id"], pd.Series(exogenous, index=frame.index)
    )
    return frame


def test_fit_recovers_known_effects_and_keeps_unknown_energy():
    frame = _sample()
    incomplete = frame.iloc[[0]].copy()
    incomplete["bathrooms"] = np.nan
    incomplete["plr_id"] = None
    incomplete["id"] = 999
    frame = pd.concat([frame, incomplete], ignore_index=True)

    model = fit_price_model(frame)

    assert model.error is None
    assert model.n_input == 241
    assert model.n_used == 240
    assert model.reference == "D"
    assert int(model.missing.loc[model.missing["feature"] == "Bathrooms", "missing"].iloc[0]) == 1
    coef = dict(zip(model.terms["key"], model.terms["coefficient"], strict=True))
    assert coef["living_area"] == pytest.approx(5_000, abs=1e-3)
    assert coef["building_age_years"] == pytest.approx(-800, abs=1e-3)
    assert coef["rooms"] == pytest.approx(20_000, abs=1e-3)
    assert coef["bathrooms"] == pytest.approx(15_000, abs=1e-3)
    assert coef["area_price_euros"] == pytest.approx(LOCAL_EUROS, abs=1e-3)
    assert coef["has_elevator"] == pytest.approx(25_000, abs=1e-3)
    assert coef["has_balcony"] == pytest.approx(10_000, abs=1e-3)
    assert coef["has_parking"] == pytest.approx(30_000, abs=1e-3)
    assert coef["has_cellar"] == pytest.approx(5_000, abs=1e-3)
    assert coef["energy:A+"] == pytest.approx(50_000, abs=1e-3)
    assert coef["energy:A"] == pytest.approx(40_000, abs=1e-3)
    assert coef["energy:G"] == pytest.approx(-20_000, abs=1e-3)
    assert coef["energy:unknown"] == pytest.approx(0, abs=1e-3)
    assert "energy:D" not in coef
    assert model.rental_reference == "unrented"
    assert coef["rental:rented"] == pytest.approx(RENTED_EUROS, abs=1e-3)
    assert coef["rental:unknown"] == pytest.approx(UNKNOWN_RENTAL_EUROS, abs=1e-3)
    assert "rental:unrented" not in coef
    assert model.rsquared == pytest.approx(1, abs=1e-8)
    assert model.rmse == pytest.approx(0, abs=1e-3)
    listing = model.listings.index[0]
    shown = contributions(model, listing)
    local = shown.loc[shown["term"] == "Planungsraum average", "input"].iloc[0]
    assert "€/m² ×" in str(local)
    assert any("0.50 € per euro" in line for line in formula_lines(model))


def test_contributions_add_up_to_the_prediction():
    model = fit_price_model(_sample(noise=25, seed=2))
    listing = model.listings.index[0]
    table = contributions(model, listing)
    predicted = float(table.loc[table["term"] == "Predicted price", "euros"].iloc[0])
    parts = table.loc[table["term"] != "Predicted price", "euros"].sum()
    assert predicted == pytest.approx(parts)
    assert predicted == pytest.approx(float(model.listings.loc[listing, "predicted"]))
    lines = formula_lines(model)
    assert lines[0].startswith("Asking price ≈")
    assert any("per m² of living area" in line for line in lines)
    assert any("energy class is A+" in line for line in lines)
    assert "Energy class D is the baseline (0 €)." in lines
    assert lines[-1] == "Unrented is the baseline (0 €)."
    assert any("when the flat is rented rather than unrented" in line for line in lines)


def test_coverage_counts_a_complete_sample():
    model = fit_price_model(_sample())
    assert model.n_used == 240
    stated = model.coverage.loc[model.coverage["feature"] == "Living area", "stated"].iloc[0]
    assert int(stated) == 240
    assert list(model.by_source["source"]) == ["immoscout24"]
    assert int(model.by_source.loc[0, "used"]) == 240
    assert not any(str(key).startswith("missing:") for key in model.terms["key"])


def test_blank_feature_keeps_its_euro_effect():
    frame = _sample(n=200, seed=3)
    missing_baths = frame.index[:40]
    missing_lift = frame.index[40:80]
    exogenous = pd.Series(
        _exogenous(
            frame["living_area"].to_numpy(),
            frame["building_age_years"].to_numpy(),
            frame["rooms"].to_numpy(),
            frame["bathrooms"].to_numpy(dtype=float),
            frame["has_elevator"].astype(float).to_numpy(),
            frame["has_balcony"].astype(float).to_numpy(),
            frame["has_parking"].astype(float).to_numpy(),
            frame["has_cellar"].astype(float).to_numpy(),
            frame["energy_efficiency_class"].to_numpy(),
            _rental_labels(frame["is_rented"]),
        ),
        index=frame.index,
    )
    exogenous.loc[missing_baths] = (
        exogenous.loc[missing_baths] - 15_000 * frame.loc[missing_baths, "bathrooms"] + 7_000
    )
    lift = frame.loc[missing_lift, "has_elevator"].astype(float)
    exogenous.loc[missing_lift] = exogenous.loc[missing_lift] - 25_000 * lift + 4_000
    frame["price"] = _price_with_local(frame["living_area"], frame["plr_id"], exogenous)
    frame.loc[missing_baths, "bathrooms"] = np.nan
    frame["has_elevator"] = frame["has_elevator"].astype("boolean")
    frame.loc[missing_lift, "has_elevator"] = pd.NA

    model = fit_price_model(frame)
    assert model.error is None
    assert model.n_used == 200
    coef = dict(zip(model.terms["key"], model.terms["coefficient"], strict=True))
    assert coef["bathrooms"] == pytest.approx(15_000, abs=1e-2)
    assert coef["has_elevator"] == pytest.approx(25_000, abs=1e-2)
    mean_baths = float(frame["bathrooms"].mean())
    assert coef["missing:bathrooms"] == pytest.approx(7_000 - 15_000 * mean_baths, abs=1e-2)
    assert coef["missing:has_elevator"] == pytest.approx(4_000, abs=1e-2)
    listing = model.listings.index[0]
    table = contributions(model, listing)
    predicted = float(table.loc[table["term"] == "Predicted price", "euros"].iloc[0])
    parts = float(table.loc[table["term"] != "Predicted price", "euros"].sum())
    assert predicted == pytest.approx(parts, abs=1e-4)


def test_missing_rates_are_the_share_blank_in_each_source():
    frame = _sample(n=10)
    frame["source"] = ["immoscout24"] * 6 + ["neubaukompass"] * 4
    frame.loc[frame["source"] == "neubaukompass", "bathrooms"] = np.nan
    frame.loc[frame["source"] == "neubaukompass", "energy_efficiency_class"] = None
    rates = missing_rates_by_source(frame)
    scout = next(column for column in rates.columns if column.startswith("ImmoScout24"))
    nbk = next(column for column in rates.columns if column.startswith("Neubaukompass"))
    assert scout.startswith("ImmoScout24 (6)")
    baths = rates.set_index("information")
    assert baths.loc["Bathrooms", nbk] == 100
    assert baths.loc["Bathrooms", scout] == 0
    assert baths.loc["Energy class", nbk] == 100
    assert list(rates.columns).index(nbk) > list(rates.columns).index(scout)


def test_thinkimmo_rows_are_left_out_of_the_fit():
    frame = _sample(n=80)
    repeated = frame.iloc[:10].copy()
    repeated["source"] = "ThinkImmo"
    repeated["price"] = 9_000_000
    repeated["id"] = repeated["id"] + 10_000
    combined = pd.concat([frame, repeated], ignore_index=True)

    model = fit_price_model(combined)
    original = fit_price_model(frame)

    assert model.n_input == len(frame)
    assert model.n_used == original.n_used
    assert set(model.by_source["source"]) == {"immoscout24"}
    coef = dict(zip(model.terms["key"], model.terms["coefficient"], strict=True))
    base = dict(zip(original.terms["key"], original.terms["coefficient"], strict=True))
    assert coef["area_price_euros"] == pytest.approx(base["area_price_euros"])
    rates = missing_rates_by_source(combined)
    assert all("thinkimmo" not in column.lower() for column in rates.columns)


def test_a_shell_title_is_left_out_and_a_compound_stays():
    frame = _sample(n=80)
    shell = frame.iloc[:8].copy()
    shell["title"] = "Günstiger Rohling in Kreuzberg"
    shell["price"] = 50_000
    shell["id"] = shell["id"] + 20_000
    words = [
        "Baugenehmigung liegt vor",
        "Unausgebautes Dachgeschoss",
        "DG unausgebaut",
        "Nur mit Bauvorbescheid",
    ]
    more = pd.concat(
        [
            frame.iloc[[i]].assign(title=title, id=40_000 + i, price=50_000)
            for i, title in enumerate(words)
        ],
        ignore_index=True,
    )
    compound = frame.iloc[[0]].copy()
    compound["title"] = "Rohlingwohnung"
    compound["id"] = 30_000
    combined = pd.concat([frame, shell, more, compound], ignore_index=True)

    model = fit_price_model(combined)

    assert model.n_input == len(frame) + 1
    assert 30_000 in set(model.listings["id"])
    titles = model.listings["title"]
    assert not titles.str.contains(r"\bRohling\b", case=False).any()
    assert not titles.str.contains(r"\bBaugenehmigung\b", case=False).any()
    assert not titles.str.contains(r"\bUnausgebaut(?:es)?\b", case=False).any()
    assert not titles.str.contains(r"\bBauvorbescheid\b", case=False).any()


def test_feature_with_no_data_is_left_out():
    frame = _sample(n=80).drop(columns=["plr_id"])
    model = fit_price_model(frame)
    assert model.error is None
    assert model.n_used == 80
    assert "area_price_euros" in model.omitted
    assert "area_price_euros" not in set(model.terms["key"])
