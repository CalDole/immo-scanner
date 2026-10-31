import geopandas as gpd
from shapely.geometry import box

from immo_scanner.reference.ortsteile import ortsteil_rows


def test_ortsteil_rows_use_the_official_four_digit_id():
    frame = gpd.GeoDataFrame(
        {"sch": ["110000020202", "110000010101"], "nam": ["Kreuzberg", "Mitte"]},
        geometry=[box(13.3, 52.4, 13.5, 52.5), box(13.3, 52.5, 13.4, 52.6)],
        crs="EPSG:4326",
    )
    rows = ortsteil_rows(frame)
    assert [(row["id"], row["name"]) for row in rows] == [("0202", "Kreuzberg"), ("0101", "Mitte")]
    assert rows[0]["geom"] is not None
