"""Milieuschutzgebiet rows from the Senate WFS, without the network."""

import json
from datetime import date

import geopandas as gpd
import pytest
from shapely.geometry import box

from immo_scanner.reference.milieuschutz_gebiete import gebiet_rows, require_complete


def test_gebiet_rows_map_wfs_columns():
    frame = gpd.GeoDataFrame(
        {
            "schluessel": ["EM0105", "EM0201"],
            "gebietsname": ["Sparrplatz", "Bergmannkiez"],
            "bezirk": ["Mitte", None],
            "f_in_kraft": ["25.05.2016", None],
            "fl_ha": ["51.3", None],
        },
        geometry=[
            box(13.34, 52.53, 13.36, 52.55),
            box(13.40, 52.48, 13.42, 52.50),
        ],
        crs="EPSG:4326",
    )
    rows = gebiet_rows(frame)
    assert [(row["schluessel"], row["name"]) for row in rows] == [
        ("EM0105", "Sparrplatz"),
        ("EM0201", "Bergmannkiez"),
    ]
    assert rows[0]["bezirk"] == "Mitte"
    assert rows[0]["in_force"] == date(2016, 5, 25)
    assert rows[0]["area_ha"] == 51.3
    assert rows[0]["geom"] is not None
    assert rows[1]["bezirk"] is None
    assert rows[1]["in_force"] is None
    assert rows[1]["area_ha"] is None


def test_truncated_download_is_refused(tmp_path):
    path = tmp_path / "gebiete.geojson"
    path.write_text(
        json.dumps({"type": "FeatureCollection", "numberMatched": 82, "features": []}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="82"):
        require_complete(path)
