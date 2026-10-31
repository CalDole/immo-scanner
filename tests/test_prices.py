import pandas as pd

from immo_scanner.explore.prices import (
    average_price_per_m2,
    averages_for,
    local_price_levels,
    merge_hover_prices,
    overall_mean,
    price_comparison,
)


def _listing(**overrides: object) -> dict:
    row = {
        "source": "immoscout24",
        "title": "3-Zimmer-Wohnung",
        "price": 500_000,
        "living_area": 100,
        "plr_id": "01100101",
        "bezirk": "Mitte",
    }
    row.update(overrides)
    return row


def test_average_is_the_mean_of_price_over_area_per_planungsraum():
    frame = pd.DataFrame(
        [
            _listing(price=200_000, living_area=50),  # 4,000
            _listing(price=300_000, living_area=100),  # 3,000
            _listing(plr_id="01200202", bezirk="Pankow", price=800_000, living_area=80),
        ]
    )

    averages = average_price_per_m2(frame).set_index("area_id")

    assert averages.loc["01100101", "value"] == 3500
    assert averages.loc["01100101", "listings"] == 2
    assert averages.loc["01100101", "bezirk"] == "Mitte"
    assert averages.loc["01200202", "value"] == 10_000
    assert averages.loc["01200202", "listings"] == 1


def test_leave_one_out_rate_excludes_the_listing_itself():
    frame = pd.DataFrame(
        [
            _listing(price=200_000, living_area=50, status="active"),
            _listing(price=300_000, living_area=100, status="active"),
            _listing(price=500_000, living_area=100, status="active"),
            _listing(plr_id="01200202", price=800_000, living_area=80, status="active"),
            _listing(source="thinkimmo", price=50_000, living_area=50, status="active"),
            _listing(title="Ein Rohling", price=50_000, living_area=40, status="active"),
            _listing(price=50_000, living_area=25, status="removed"),
        ]
    )

    levels = local_price_levels(frame)

    assert levels.loc[0, "area_price_per_m2"] == 4_000
    assert levels.loc[1, "area_price_per_m2"] == 4_500
    assert levels.loc[2, "area_price_per_m2"] == 3_500
    assert levels.loc[0, "area_price_euros"] == 4_000 * 50
    assert pd.isna(levels.loc[3, "area_price_per_m2"])
    assert pd.isna(levels.loc[3, "area_price_euros"])
    assert levels.loc[4, "area_price_per_m2"] == 4_000
    assert levels.loc[4, "area_price_euros"] == 4_000 * 50
    assert levels.loc[5, "area_price_per_m2"] == 4_000
    assert levels.loc[6, "area_price_per_m2"] == 4_000
    assert levels.loc[6, "area_price_euros"] == 4_000 * 25


def test_thinkimmo_and_rohling_are_left_out():
    frame = pd.DataFrame(
        [
            _listing(price=200_000, living_area=50),
            _listing(source="thinkimmo", price=1_000_000, living_area=50),
            _listing(source="ThinkImmo", price=1_000_000, living_area=50),
            _listing(title="Günstiger Rohling in Kreuzberg", price=100_000, living_area=50),
            _listing(title="Mehrere Rohlinge", price=100_000, living_area=50),
            _listing(title="Rohlingwohnung", price=100_000, living_area=40),
            _listing(title="Baugenehmigung liegt vor", price=100_000, living_area=50),
            _listing(title="Unausgebautes Dachgeschoss", price=100_000, living_area=50),
            _listing(title="DG unausgebaut", price=100_000, living_area=50),
            _listing(title="Nur mit Bauvorbescheid", price=100_000, living_area=50),
            _listing(price=None, living_area=80),
            _listing(price=100_000, living_area=0),
            _listing(price=100_000, living_area=40, plr_id=None),
        ]
    )

    averages = average_price_per_m2(frame)

    # "Rohlinge" and "Rohlingwohnung" stay in: the word Rohling is not a whole word there.
    assert len(averages) == 1
    assert averages.loc[0, "listings"] == 3
    assert averages.loc[0, "value"] == (4000 + 2000 + 2500) / 3


def test_overall_mean_weights_planungsraeume_by_how_many_listings_they_have():
    averages = pd.DataFrame(
        {
            "area_id": ["a", "b"],
            "value": [10_000.0, 4_000.0],
            "listings": [1, 3],
            "bezirk": ["Mitte", "Mitte"],
        }
    )

    # One listing at 10,000 and three at 4,000, not the mean of the two areas.
    assert overall_mean(averages) == 5500


def test_price_comparison_uses_the_bezirk_and_berlin_listing_means():
    averages = average_price_per_m2(
        pd.DataFrame(
            [
                _listing(price=200_000, living_area=50),  # Mitte, 4,000
                _listing(price=600_000, living_area=100),  # Mitte, 6,000
                _listing(plr_id="01200202", bezirk="Pankow", price=300_000, living_area=100),
            ]
        )
    )

    row = price_comparison(averages, "01100101")

    assert row is not None
    assert row["Indicator"] == "Asking price per m²"
    assert row["Here"] == "5,000 €/m²"
    assert row["Bezirk average"] == "5,000 €/m²"
    assert row["Berlin average"] == "4,333 €/m²"
    assert price_comparison(averages, "99999999") is None


def test_averages_for_lines_up_with_listing_planungsraeume():
    averages = pd.DataFrame(
        {"area_id": ["01100101"], "value": [4200.0], "listings": [4], "bezirk": ["Mitte"]}
    )
    ids = pd.Series(["01100101", None, "01200202"])

    mapped = averages_for(ids, averages)

    assert mapped.tolist() == [4200.0, pd.NA, pd.NA]


def test_merge_hover_prices_keeps_an_area_that_only_has_a_price():
    stats = pd.DataFrame(
        [{"plr_id": "01100101", "nearby_school_abi_average": 1.8}, {"plr_id": None}]
    )
    averages = pd.DataFrame(
        {
            "area_id": ["01100101", "01200202"],
            "value": [4200.0, 5100.0],
            "listings": [4, 2],
        }
    )

    merged = merge_hover_prices(stats, averages).set_index("plr_id", drop=False)

    assert merged.loc["01100101", "avg_price_per_m2"] == 4200
    assert merged.loc["01100101", "nearby_school_abi_average"] == 1.8
    assert merged.loc["01200202", "avg_price_per_m2"] == 5100
    assert pd.isna(merged.loc["01200202", "nearby_school_abi_average"])


def test_empty_listings_stay_empty():
    assert average_price_per_m2(pd.DataFrame()).empty
    assert overall_mean(pd.DataFrame()) is None
    assert price_comparison(pd.DataFrame(), "01100101") is None
