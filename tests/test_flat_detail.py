from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from immo_scanner.explore.flat_detail import (
    delta_to_median,
    gallery,
    match_gymnasium,
    median_of,
    parse_commission,
    plain_record,
    price_cuts,
    price_history,
    purchase_costs,
    rank,
    score_breakdown,
)


def test_rank_counts_better_values_and_skips_missing_ones():
    assert rank(0.7, [0.9, 0.7, 0.5, None, float("nan")]) == (2, 3)
    assert rank(20, [30, 20, 10], higher_is_better=False) == (2, 3)
    assert rank(None, [1, 2]) is None
    assert rank(1, []) is None


def test_median_and_delta():
    assert median_of([1, 3, None, 2]) == 2
    assert median_of([1, 2, 3, 4]) == 2.5
    assert median_of([]) is None
    assert delta_to_median(5200, [5000, 4800, 5200]) == 200
    assert delta_to_median(None, [1]) is None


def test_score_breakdown_names_inputs_and_counts_known_ones():
    row = {
        "commute_minutes": 20,
        "commute_workplace2_minutes": 30,
        "commute_score": 0.6,
        "distance_score": 0.6,
        "hausgeld": 412,
        "hausgeld_score": 0.776,
        "building_age_years": 53,
        "building_age_score": 0.47,
        "building_age_class_score": 0.25,
        "heating_demand_score": 0.14,
        "building_score": 0.41,
        "has_cellar": False,
        "basement_score": 0.8,
        "agh_vote_afd": 16.0,
        "tolerance_score": 1.2,
        "origin_diversity_index": 83.4,
        "origin_diversity_score": 0.54,
        "german_citizen_share": 76,
        "citizenship_score": 1,
        "oic_share": 11.8,
        "oic_score": 0.66,
        "crime_rate": 2738,
        "crime_year": 2025,
        "crime_score": 0.58,
        "incident_count": 3,
        "incident_rate": 0.42,
        "incident_score": 0.3,
        "school_type_level": 3.4,
        "school_type_score": 0.6,
    }
    age_class = {
        "class_code": "F",
        "year_from": 1969,
        "year_to": 1978,
        "climate_risk_score": 25,
        "typical_heating_demand": "160 – 220 kWh/(m²a)",
    }
    groups = {group.label: group for group in score_breakdown(row, age_class, [2.1, None, 2.4])}
    distance = groups["Distance"]
    assert distance.score == 0.6
    assert distance.parts[0].value == "24 min"
    building = {part.label: part for part in groups["Building"].parts}
    assert building["Hausgeld"].value == "412 €/month"
    assert building["Age class"].value == "Class F (1969–1978), climate risk score 25 of 100"
    assert building["Energy class"].value is None
    assert groups["Building"].known == 4
    flat = {part.label: part for part in groups["Flat"].parts}
    assert flat["Cellar"].value == "No"
    assert flat["Cellar"].score == 0.8
    assert groups["Flat"].known == 1
    neighbourhood = {part.label: part for part in groups["Neighbourhood"].parts}
    assert neighbourhood["Education"].value == "2.10, –, 2.40"
    assert neighbourhood["Education"].score is None
    assert neighbourhood["Schultypisierung"].value == "3.40"
    assert neighbourhood["Schultypisierung"].score == 0.6
    assert neighbourhood["Tolerance"].value == "AfD 16%"
    assert neighbourhood["Origin diversity"].value == "83.4 of 100"
    assert neighbourhood["Origin diversity"].score == 0.54
    assert neighbourhood["Citizenship"].value == "76.0%"
    assert neighbourhood["Citizenship"].score == 1
    assert neighbourhood["OIC origin"].value == "11.8%"
    assert neighbourhood["OIC origin"].score == 0.66
    assert neighbourhood["Crime"].value == "2,738 per 100,000 (2025)"
    assert neighbourhood["Crime"].score == 0.58
    assert neighbourhood["Racist incidents"].value == "3 incidents, 0.42 per 1,000 per year"
    assert neighbourhood["Racist incidents"].score == 0.3
    assert groups["Neighbourhood"].known == 7
    one = score_breakdown({"incident_count": 1, "incident_rate": 0.2, "incident_score": 0.67})
    incidents = {part.label: part for group in one for part in group.parts}
    assert incidents["Racist incidents"].value == "1 incident, 0.20 per 1,000 per year"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("3,57 % inkl. MwSt.", 3.57),
        ("3.57%", 3.57),
        ("Nein", 0.0),
        ("provisionsfrei; keine Provision, courtagefrei", 0.0),
        ("nach Vereinbarung", None),
        (None, None),
        ("  ", None),
    ],
)
def test_parse_commission(text, expected):
    assert parse_commission(text) == expected


def test_purchase_costs_add_tax_notary_and_commission():
    costs = purchase_costs(400_000, "3,57 %", None, tax_pct=6.0, notary_pct=2.0)
    amounts = {line.label: line.amount for line in costs.lines}
    assert amounts["Grunderwerbsteuer"] == pytest.approx(24_000)
    assert amounts["Notary and land registry"] == pytest.approx(8_000)
    assert amounts["Buyer's commission"] == pytest.approx(14_280)
    assert costs.total == pytest.approx(446_280)
    assert costs.commission_known


def test_purchase_costs_leave_an_unknown_commission_out_of_the_total():
    costs = purchase_costs(400_000, "nach Vereinbarung", None, tax_pct=6.0, notary_pct=2.0)
    assert not costs.commission_known
    assert costs.total == pytest.approx(432_000)
    from_thinkimmo = purchase_costs(400_000, None, 3.0, tax_pct=6.0, notary_pct=2.0)
    assert from_thinkimmo.commission_known
    assert from_thinkimmo.total == pytest.approx(444_000)
    assert purchase_costs(None, None, None, tax_pct=6.0, notary_pct=2.0).total is None


def test_price_history_merges_both_origins_in_date_order():
    own = pd.DataFrame(
        {
            "scraped_at": pd.to_datetime(["2026-09-01", "2026-09-20"], utc=True),
            "price": [420_000, 399_000],
        }
    )
    thinkimmo = [
        {"buyingPrice": 430_000, "creationDate": "2026-08-15T10:00:00Z", "platformName": "is24"},
        {"buyingPrice": None, "creationDate": "2026-08-16T10:00:00Z", "platformName": "is24"},
    ]
    history = price_history(own, thinkimmo)
    assert history["price"].tolist() == [430_000, 420_000, 399_000]
    assert history["origin"].tolist() == ["is24", "Scanner", "Scanner"]
    assert price_cuts(history) == 2
    assert price_history(pd.DataFrame(), None).empty
    assert price_cuts(price_history(pd.DataFrame(), None)) == 0


def test_gallery_prefers_local_files_and_the_listings_own_portal():
    is24 = [
        {"type": "PICTURE", "path": "data/media/a.webp", "url": "https://x/a", "caption": "Bad"},
        {"type": "PICTURE", "path": "data/media/gone.webp", "url": "https://x/b"},
        {"type": "FLOORPLAN", "path": None, "url": "https://x/plan"},
        {"type": "DOCUMENT", "url": "https://x/doc.pdf"},
    ]
    thinkimmo = [
        {"originalUrl": "https://t/1", "title": "", "floorPlan": False},
        {"originalUrl": "https://t/2", "title": "Grundriss", "floorPlan": True},
    ]
    kleinanzeigen = [{"type": "PICTURE", "url": "https://k/1"}]
    groups = [("thinkimmo", thinkimmo), ("kleinanzeigen", kleinanzeigen), ("immoscout24", is24)]

    def exists(path: Path) -> bool:
        return path.name == "a.webp"

    photos, plans = gallery(groups, "immoscout24", exists)
    assert [item.image for item in photos] == ["data/media/a.webp", "https://x/b"]
    assert photos[0].caption == "Bad"
    assert [item.image for item in plans] == ["https://x/plan"]

    assert {item.origin for item in photos} == {"immoscout24"}

    photos, plans = gallery([("thinkimmo", thinkimmo)], "immowelt", exists)
    assert [item.image for item in photos] == ["https://t/1"]
    assert photos[0].caption is None
    assert [(item.image, item.caption) for item in plans] == [("https://t/2", "Grundriss")]
    assert gallery([], None, exists) == ([], [])


def test_gallery_takes_all_photos_from_the_portal_with_the_most():
    one = [{"type": "PICTURE", "url": "https://p/1"}]
    many = [{"originalUrl": f"https://t/{n}", "floorPlan": False} for n in range(5)]
    photos, _ = gallery([("immoscout24", one), ("thinkimmo", many)], "immoscout24")
    assert len(photos) == 5
    assert photos[0].origin == "thinkimmo"
    forty = [{"type": "PICTURE", "url": f"https://p/{n}"} for n in range(40)]
    photos, _ = gallery([("thinkimmo", many), ("neubaukompass", forty)], "neubaukompass")
    assert len(photos) == 40


def test_match_gymnasium_prefers_the_exact_name():
    stats = pd.DataFrame(
        {
            "name": ["Max-Planck-Gymnasium", "Max-Planck-Gymnasium Mitte", "Andreas-Gymnasium"],
            "abi_average": [2.3, 1.9, np.nan],
            "starts_year_5": [np.True_, np.False_, np.False_],
        }
    )
    exact = match_gymnasium("Max-Planck-Gymnasium", stats)
    assert exact["abi_average"] == 2.3
    assert exact["starts_year_5"] is True
    assert match_gymnasium("Andreas-Gymnasium Friedrichshain", stats)["abi_average"] is None
    assert match_gymnasium("Unknown", stats) is None
    assert match_gymnasium(None, stats) is None


def test_nearest_gymnasium_pass_rates_use_the_matched_school():
    from immo_scanner.explore.flat_detail import nearest_gymnasium_pass_rates

    stats = pd.DataFrame(
        {
            "name": ["Max-Planck-Gymnasium", "Max-Planck-Gymnasium Mitte", "Andreas-Gymnasium"],
            "abi_average": [2.3, 1.9, 2.1],
            "abi_pass_rate_pct": [90.0, 70.0, 80.0],
        }
    )
    frame = pd.DataFrame(
        {
            "nearest_gymnasium": [
                "Max-Planck-Gymnasium",
                "Andreas-Gymnasium Friedrichshain",
                None,
                "Unknown",
            ]
        }
    )
    rates = nearest_gymnasium_pass_rates(frame, stats)
    assert rates.iloc[0] == 90.0
    assert rates.iloc[1] == 80.0
    assert rates.iloc[2:].isna().all()
    assert nearest_gymnasium_pass_rates(pd.DataFrame({"title": ["a"]}), stats).isna().all()


def test_plain_record_turns_numpy_values_into_python_ones():
    record = plain_record(pd.Series({"a": np.int64(3), "b": np.float64("nan"), "c": "x"}))
    assert record == {"a": 3, "b": None, "c": "x"}
    assert type(record["a"]) is int
