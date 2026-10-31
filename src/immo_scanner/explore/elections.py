"""Election results per voting district, and their shares projected onto Planungsräume."""

import json

import pandas as pd
from sqlalchemy import text

from immo_scanner.db.session import get_engine
from immo_scanner.explore.scoring import tolerance_score

# Loader keys that name a party under a longer official name.
PARTY_ALIASES = {"voltdeutschland": "volt"}


def load_election_districts() -> pd.DataFrame:
    """One row per district and election scope, with GeoJSON geometry and its Bezirk."""
    query = """
        SELECT w.scope, w.election, w.id AS area_id, w.id AS name, b.bezirk,
               w.eligible_voters AS population, w.valid_votes, w.turnout_pct, w.party_shares,
               ST_AsGeoJSON(ST_SimplifyPreserveTopology(w.geom, 0.00005), 5) AS geometry
        FROM wahlbezirke w
        LEFT JOIN LATERAL (
            SELECT p.bezirk FROM lor_planungsraeume p
            WHERE ST_Contains(p.geom, ST_PointOnSurface(w.geom))
            LIMIT 1
        ) b ON true
    """
    with get_engine().connect() as conn:
        df = pd.read_sql(text(query), conn)
    df["geometry"] = df["geometry"].map(json.loads)
    df["party_shares"] = df["party_shares"].map(lambda v: v or {})
    return df


def load_plr_weights() -> pd.DataFrame:
    """scope, district id, plr_id and the district's valid votes falling into that PLR.

    Votes are assumed to be spread evenly over a district's area.
    """
    query = """
        SELECT w.scope, w.id AS district_id, p.plr_id,
               w.valid_votes * ST_Area(ST_Intersection(w.geom, p.geom))
                   / NULLIF(ST_Area(w.geom), 0) AS weight
        FROM wahlbezirke w
        JOIN lor_planungsraeume p ON ST_Intersects(w.geom, p.geom)
        WHERE w.valid_votes > 0
    """
    with get_engine().connect() as conn:
        return pd.read_sql(text(query), conn)


def load_plr_turnout() -> pd.Series:
    """Eligible-voter-weighted AGH turnout (percent) per Planungsraum.

    A district is split by area overlap, the same way its votes are. Weighting by
    eligible voters keeps a low-turnout district in the non-voter share.
    """
    query = """
        SELECT p.plr_id AS area_id,
               SUM(w.voters * ST_Area(ST_Intersection(w.geom, p.geom))
                   / NULLIF(ST_Area(w.geom), 0))
                   / NULLIF(SUM(w.eligible_voters * ST_Area(ST_Intersection(w.geom, p.geom))
                       / NULLIF(ST_Area(w.geom), 0)), 0) * 100 AS turnout_pct
        FROM wahlbezirke w
        JOIN lor_planungsraeume p ON ST_Intersects(w.geom, p.geom)
        WHERE w.scope = 'abgeordnetenhaus'
          AND w.eligible_voters > 0
          AND w.voters IS NOT NULL
        GROUP BY p.plr_id
    """
    with get_engine().connect() as conn:
        frame = pd.read_sql(text(query), conn)
    return frame.set_index("area_id")["turnout_pct"]


def party_shares_long(districts: pd.DataFrame) -> pd.DataFrame:
    """scope, district_id, party, share from the party_shares dicts."""
    rows = [
        {"scope": r.scope, "district_id": r.area_id, "party": party, "share": share}
        for r in districts.itertuples(index=False)
        for party, share in r.party_shares.items()
    ]
    return pd.DataFrame(rows, columns=["scope", "district_id", "party", "share"])


def plr_party_shares(weights: pd.DataFrame, shares: pd.DataFrame) -> pd.DataFrame:
    """Vote-weighted party shares per PLR: scope, area_id, party, value."""
    merged = weights.merge(shares, on=["scope", "district_id"])
    merged = merged[merged["weight"] > 0]
    merged["weighted"] = merged["weight"] * merged["share"]
    grouped = merged.groupby(["scope", "plr_id", "party"])[["weighted", "weight"]].sum()
    result = (grouped["weighted"] / grouped["weight"]).rename("value").reset_index()
    return result.rename(columns={"plr_id": "area_id"})


def tolerance_scores(shares: pd.DataFrame, id_column: str) -> pd.DataFrame:
    """Tolerance score (docs/scoring.md) per area from long party shares.

    `shares` has `id_column`, party and a share column (`share` or `value`) in percent.
    """
    share_column = "share" if "share" in shares.columns else "value"
    scores = []
    for area_id, group in shares.groupby(id_column, sort=False):
        by_party = {
            PARTY_ALIASES.get(party, party): share
            for party, share in zip(group["party"], group[share_column], strict=True)
        }
        scores.append({"area_id": area_id, "value": tolerance_score(by_party)})
    return pd.DataFrame(scores, columns=["area_id", "value"])


def strongest_party(shares: dict[str, float]) -> tuple[str | None, float | None, float | None]:
    """(party, share, lead in percentage points over the runner-up)."""
    ranked = sorted(
        ((party, share) for party, share in shares.items() if share is not None),
        key=lambda item: item[1],
        reverse=True,
    )
    if not ranked:
        return None, None, None
    lead = ranked[0][1] - ranked[1][1] if len(ranked) > 1 else ranked[0][1]
    return ranked[0][0], ranked[0][1], round(lead, 2)
