import pandas as pd
import pytest

from immo_scanner.db.models import (
    Immoscout24Expose,
    ImmoweltExpose,
    KleinanzeigenAd,
    ThinkimmoProperty,
)
from immo_scanner.explore.gaps import (
    CRAWLER_FIELDS,
    column_gaps,
    counts_to_gaps,
    filled_count_sql,
    gaps_by_source,
    location_state_counts,
    missing_listings,
)


def test_merged_listing_tracks_expose_columns():
    """Fields the exposé fetch writes onto listing_features, including later additions."""
    keys = {key for key, _ in CRAWLER_FIELDS}
    assert {
        "apartment_type",
        "posted_on",
        "property_condition",
        "flooring",
        "bathroom_features",
        "has_monument_protection",
        "interior_quality",
        "has_guest_toilet",
        "parking_type",
        "parking_price",
    } <= keys


def test_blank_text_counts_as_missing():
    frame = pd.DataFrame(
        {
            "title": ["Flat", "  ", "none", "NaN", None, ""],
            "source": ["immoscout24"] * 6,
        }
    )
    row = column_gaps(frame).set_index("key").loc["title"]
    assert row["filled"] == 1
    assert row["missing"] == 5
    assert row["missing_pct"] == pytest.approx(100 * 5 / 6)


def test_false_is_present_and_null_is_missing():
    frame = pd.DataFrame(
        {
            "has_elevator": pd.Series([False, True, pd.NA], dtype="boolean"),
            "has_parking": pd.Series([False, None, "none"], dtype=object),
            "source": ["immoscout24"] * 3,
        }
    )
    gaps = column_gaps(frame).set_index("key")
    assert gaps.loc["has_elevator", "filled"] == 2
    assert gaps.loc["has_elevator", "missing"] == 1
    assert gaps.loc["has_parking", "filled"] == 1
    assert gaps.loc["has_parking", "missing"] == 2


def test_gaps_by_source_are_the_share_blank_in_each_source():
    frame = pd.DataFrame(
        {
            "source": ["immoscout24"] * 6 + ["neubaukompass"] * 4,
            "bathrooms": [1, 1, 1, 1, 1, 1, None, None, None, None],
            "energy_efficiency_class": ["D"] * 6 + [None] * 4,
            "title": ["a"] * 10,
        }
    )
    rates = gaps_by_source(frame)
    scout = next(column for column in rates.columns if column.startswith("ImmoScout24"))
    nbk = next(column for column in rates.columns if column.startswith("Neubaukompass"))
    total = next(column for column in rates.columns if column.startswith("Total"))
    assert scout == "ImmoScout24 (6)"
    assert total == "Total (10)"
    assert list(rates.columns)[1] == total
    baths = rates.set_index("information")
    assert baths.loc["Bathrooms", nbk] == 100
    assert baths.loc["Bathrooms", scout] == 0
    assert baths.loc["Bathrooms", total] == 40
    assert baths.loc["Energy class", nbk] == 100
    assert baths.loc["Title", scout] == 0
    assert baths.loc["Title", total] == 0
    assert "Year built" not in set(baths.index)
    assert list(rates.columns).index(nbk) > list(rates.columns).index(scout)


def test_largest_gaps_come_first():
    frame = pd.DataFrame(
        {
            "title": ["a", "b"],
            "bathrooms": [None, None],
            "source": ["immoscout24", "immoscout24"],
        }
    )
    assert list(column_gaps(frame)["key"][:2]) == ["bathrooms", "title"]


def test_missing_listings_are_the_blank_rows_for_that_source():
    frame = pd.DataFrame(
        {
            "id": [1, 2, 3],
            "title": ["a", "b", "c"],
            "url": ["https://a.example", "https://b.example", "https://c.example"],
            "external_id": ["a", "b", "c"],
            "source": ["immoscout24", "immoscout24", "neubaukompass"],
            "bathrooms": [None, 1, None],
        }
    )
    rows, total = missing_listings(frame, "immoscout24", "bathrooms")
    assert total == 1
    assert list(rows["id"]) == [1]
    rows, total = missing_listings(frame, "neubaukompass", "bathrooms")
    assert total == 1
    assert list(rows["id"]) == [3]


def test_counts_to_gaps_uses_filled_counts():
    gaps = counts_to_gaps({"n": 4, "has_elevator": 3, "title": 1})
    rows = gaps.set_index("key")
    assert rows.loc["has_elevator", "missing"] == 1
    assert rows.loc["has_elevator", "missing_pct"] == 25
    assert rows.loc["title", "filled"] == 1
    assert list(gaps["key"]) == ["title", "has_elevator"]


def test_expose_counts_keep_false_booleans_and_skip_json():
    sql = str(filled_count_sql(ImmoweltExpose))
    assert 'count("has_elevator") AS "has_elevator"' in sql
    assert 'btrim("description")' in sql
    assert 'btrim("has_elevator")' not in sql
    assert '"facts"' not in sql
    assert "fetched_at" not in sql
    assert '"url"' not in sql
    assert '"listing_id"' not in sql

    scout = str(filled_count_sql(Immoscout24Expose))
    assert 'btrim("address")' in scout
    assert '"media"' not in scout

    thinkimmo = str(filled_count_sql(ThinkimmoProperty))
    assert "detail_fetched_at" not in thinkimmo
    assert '"raw"' not in thinkimmo
    assert '"address"' not in thinkimmo


def test_location_states_pivot_per_source_with_zeros():
    rows = pd.DataFrame(
        {
            "source": ["immowelt", "immowelt", "immoscout24", "immowelt"],
            "state": ["located", "never_tried", "located", "never_tried"],
            "listings": [5, 2, 3, 1],
        }
    )
    table = location_state_counts(rows)
    assert list(table.columns) == ["key", "state", "immowelt", "immoscout24", "Total"]
    assert list(table["key"]) == [
        "located", "never_tried", "not_found", "not_in_view", "outside_lor"
    ]
    by_key = table.set_index("key")
    assert by_key.loc["located", "Total"] == 8
    assert by_key.loc["never_tried", "immowelt"] == 3
    assert by_key.loc["outside_lor", "immoscout24"] == 0


def test_location_states_without_listings_are_all_zero():
    table = location_state_counts(pd.DataFrame(columns=["source", "state", "listings"]))
    assert list(table.columns) == ["key", "state", "Total"]
    assert table["Total"].sum() == 0


def test_date_fields_are_counted_and_timestamps_are_not():
    sql = str(filled_count_sql(KleinanzeigenAd))
    assert 'count("posted_on") AS "posted_on"' in sql
    assert "fetched_at" not in sql
