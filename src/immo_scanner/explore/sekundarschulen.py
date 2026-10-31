"""Match published Sekundarschule statistics to official school sites."""

import pandas as pd

SITE_COLUMNS = ("bsn", "schulart", "bezirk", "ortsteil", "lat", "lon")


def attach_locations(stats: pd.DataFrame, sites: pd.DataFrame) -> pd.DataFrame:
    """Left-join each statistic row to the school site with the same Berlin school number.

    A school with no number, or a number absent from the site list, stays in the
    frame without coordinates.
    """
    located = stats.copy()
    if "bsn" not in located.columns:
        located["bsn"] = None
    points = sites.copy()
    if points.empty:
        points = pd.DataFrame(columns=list(SITE_COLUMNS))
    else:
        points = points.drop_duplicates("bsn", keep="first")
    keep = [column for column in SITE_COLUMNS if column == "bsn" or column not in located.columns]
    return located.merge(points[keep], on="bsn", how="left")
