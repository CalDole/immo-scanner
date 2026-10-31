"""Ortsteil, Prognoseraum and Kiez on each listing

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LISTING_FEATURES_SQL = """
CREATE MATERIALIZED VIEW listing_features AS
WITH latest AS (
    SELECT DISTINCT ON (listing_id) *
    FROM listing_snapshots
    ORDER BY listing_id, scraped_at DESC
),
first_snap AS (
    SELECT DISTINCT ON (listing_id) listing_id, price AS first_price
    FROM listing_snapshots
    WHERE price IS NOT NULL
    ORDER BY listing_id, scraped_at
),
snap_counts AS (
    SELECT listing_id, count(*) AS snapshot_count
    FROM listing_snapshots
    GROUP BY listing_id
)
SELECT
    l.id,
    l.source,
    l.external_id,
    l.url,
    l.title,
    l.address,
    l.postcode,
    l.district,
    l.status,
    l.is_new_build,
    l.first_seen,
    l.last_seen,
    round((extract(epoch FROM
        (CASE WHEN l.status = 'removed' THEN l.last_seen ELSE now() END) - l.first_seen
    ) / 86400)::numeric, 1)::float8 AS days_on_market,
    s.price,
    s.living_area,
    s.rooms,
    COALESCE(d.floor, s.floor) AS floor,
    d.total_floors,
    COALESCE(d.year_built, s.year_built) AS year_built,
    extract(year FROM now())::int - COALESCE(d.year_built, s.year_built) AS building_age_years,
    d.last_renovation_year,
    extract(year FROM now())::int - d.last_renovation_year AS years_since_renovation,
    d.heating_type,
    d.energy_source,
    d.has_elevator,
    d.has_parking,
    d.has_cellar,
    d.has_balcony,
    d.balcony_count,
    d.fetched_at AS details_fetched_at,
    s.hausgeld,
    CASE WHEN s.living_area > 0 THEN round(s.price / s.living_area) END AS price_per_m2,
    f.first_price,
    CASE WHEN f.first_price > 0
        THEN round(((s.price - f.first_price) / f.first_price * 100)::numeric, 1)::float8 END
        AS price_change_pct,
    sc.snapshot_count,
    l.geo_precision,
    ST_Y(l.geom) AS lat,
    ST_X(l.geom) AS lon,
    l.geom,
    sb.name AS nearest_sbahn,
    round(sb.dist) AS nearest_sbahn_m,
    ub.name AS nearest_ubahn,
    round(ub.dist) AS nearest_ubahn_m,
    round(LEAST(sb.dist, ub.dist)) AS nearest_rail_m,
    round(ST_Distance(l.geom::geography, wp.geom::geography)) AS distance_to_work_m,
    round(ct.minutes) AS commute_minutes,
    round(ct2.minutes) AS commute_workplace2_minutes,
    round(ct3.minutes) AS commute_citycenter_minutes,
    lor.plr_id,
    lor.plr_name,
    lor.bzr_name,
    lor.bezirk,
    ot.name AS ortsteil,
    lor.pgr_name AS prognoseraum,
    lor.plr_name AS kiez,
    lor.population AS lor_population,
    lor.share_under_18_pct,
    lor.share_65_plus_pct,
    agh.id AS agh_wahlbezirk,
    agh.turnout_pct AS agh_turnout_pct,
    (agh.party_shares ->> 'linke')::float AS agh_vote_linke,
    (agh.party_shares ->> 'spd')::float AS agh_vote_spd,
    (agh.party_shares ->> 'gruene')::float AS agh_vote_gruene,
    (agh.party_shares ->> 'cdu')::float AS agh_vote_cdu,
    (agh.party_shares ->> 'afd')::float AS agh_vote_afd,
    (agh.party_shares ->> 'fdp')::float AS agh_vote_fdp,
    (agh.party_shares ->> 'bsw')::float AS agh_vote_bsw,
    agh.party_shares AS agh_party_shares,
    wb.id AS bt_wahlbezirk,
    wb.turnout_pct AS bt_turnout_pct,
    (wb.party_shares ->> 'linke')::float AS bt_vote_linke,
    (wb.party_shares ->> 'spd')::float AS bt_vote_spd,
    (wb.party_shares ->> 'gruene')::float AS bt_vote_gruene,
    (wb.party_shares ->> 'cdu')::float AS bt_vote_cdu,
    (wb.party_shares ->> 'afd')::float AS bt_vote_afd,
    (wb.party_shares ->> 'fdp')::float AS bt_vote_fdp,
    (wb.party_shares ->> 'bsw')::float AS bt_vote_bsw,
    wb.party_shares AS bt_party_shares
FROM listings l
JOIN latest s ON s.listing_id = l.id
LEFT JOIN listing_details d ON d.listing_id = l.id
LEFT JOIN first_snap f ON f.listing_id = l.id
LEFT JOIN snap_counts sc ON sc.listing_id = l.id
LEFT JOIN places wp ON wp.name = 'workplace'
LEFT JOIN commute_times ct ON ct.listing_id = l.id AND ct.place_name = 'workplace'
LEFT JOIN commute_times ct2 ON ct2.listing_id = l.id AND ct2.place_name = 'workplace2'
LEFT JOIN commute_times ct3 ON ct3.listing_id = l.id AND ct3.place_name = 'citycenter'
LEFT JOIN LATERAL (
    SELECT c.name, ST_Distance(c.geom::geography, l.geom::geography) AS dist
    FROM (
        SELECT name, geom FROM stations
        WHERE is_sbahn AND l.geom IS NOT NULL
        ORDER BY geom <-> l.geom LIMIT 5
    ) c
    ORDER BY dist LIMIT 1
) sb ON true
LEFT JOIN LATERAL (
    SELECT c.name, ST_Distance(c.geom::geography, l.geom::geography) AS dist
    FROM (
        SELECT name, geom FROM stations
        WHERE is_ubahn AND l.geom IS NOT NULL
        ORDER BY geom <-> l.geom LIMIT 5
    ) c
    ORDER BY dist LIMIT 1
) ub ON true
LEFT JOIN LATERAL (
    SELECT * FROM lor_planungsraeume p WHERE ST_Intersects(p.geom, l.geom) LIMIT 1
) lor ON true
LEFT JOIN LATERAL (
    SELECT o.name FROM ortsteile o WHERE ST_Intersects(o.geom, l.geom) LIMIT 1
) ot ON true
LEFT JOIN LATERAL (
    SELECT * FROM wahlbezirke w
    WHERE w.scope = 'abgeordnetenhaus' AND ST_Intersects(w.geom, l.geom) LIMIT 1
) agh ON true
LEFT JOIN LATERAL (
    SELECT * FROM wahlbezirke w
    WHERE w.scope = 'bundestag' AND ST_Intersects(w.geom, l.geom) LIMIT 1
) wb ON true
WITH NO DATA
"""


def _create_view(sql: str) -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS listing_features")
    op.execute(sql)
    op.execute("CREATE UNIQUE INDEX ix_listing_features_id ON listing_features (id)")
    op.execute("CREATE INDEX ix_listing_features_geom ON listing_features USING gist (geom)")
    op.execute("REFRESH MATERIALIZED VIEW listing_features")


def upgrade() -> None:
    op.create_table(
        "ortsteile",
        sa.Column("id", sa.String(4), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column(
            "geom", Geometry("MULTIPOLYGON", srid=4326, spatial_index=False), nullable=False
        ),
    )
    op.create_index("ix_ortsteile_geom", "ortsteile", ["geom"], postgresql_using="gist")
    _create_view(LISTING_FEATURES_SQL)


def downgrade() -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("0005_agh_election.py")
    spec = importlib.util.spec_from_file_location("agh_election", path)
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    op.execute("DROP MATERIALIZED VIEW IF EXISTS listing_features")
    op.drop_index("ix_ortsteile_geom", table_name="ortsteile")
    op.drop_table("ortsteile")
    _create_view(previous.LISTING_FEATURES_SQL)
