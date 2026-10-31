"""Listing details from detail pages; expose them in listing_features

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0002"
down_revision: str | None = "0001"
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
    lor.plr_id,
    lor.plr_name,
    lor.bzr_name,
    lor.bezirk,
    lor.population AS lor_population,
    lor.share_under_18_pct,
    lor.share_65_plus_pct,
    wb.id AS wahlbezirk,
    wb.turnout_pct,
    (wb.party_shares ->> 'cdu')::float AS vote_cdu,
    (wb.party_shares ->> 'spd')::float AS vote_spd,
    (wb.party_shares ->> 'gruene')::float AS vote_gruene,
    (wb.party_shares ->> 'linke')::float AS vote_linke,
    (wb.party_shares ->> 'afd')::float AS vote_afd,
    (wb.party_shares ->> 'fdp')::float AS vote_fdp,
    (wb.party_shares ->> 'bsw')::float AS vote_bsw
FROM listings l
JOIN latest s ON s.listing_id = l.id
LEFT JOIN listing_details d ON d.listing_id = l.id
LEFT JOIN first_snap f ON f.listing_id = l.id
LEFT JOIN snap_counts sc ON sc.listing_id = l.id
LEFT JOIN places wp ON wp.name = 'workplace'
LEFT JOIN commute_times ct ON ct.listing_id = l.id
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
    SELECT * FROM wahlbezirke w WHERE ST_Intersects(w.geom, l.geom) LIMIT 1
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
        "listing_details",
        sa.Column(
            "listing_id",
            sa.Integer,
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("year_built", sa.Integer),
        sa.Column("last_renovation_year", sa.Integer),
        sa.Column("floor", sa.Integer),
        sa.Column("total_floors", sa.Integer),
        sa.Column("heating_type", sa.Text),
        sa.Column("energy_source", sa.Text),
        sa.Column("has_elevator", sa.Boolean),
        sa.Column("has_parking", sa.Boolean),
        sa.Column("has_cellar", sa.Boolean),
        sa.Column("has_balcony", sa.Boolean),
        sa.Column("balcony_count", sa.Integer),
        sa.Column("description", sa.Text),
        sa.Column("raw", JSONB, nullable=False, server_default="{}"),
    )
    _create_view(LISTING_FEATURES_SQL)


def downgrade() -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("0001_initial.py")
    spec = importlib.util.spec_from_file_location("initial", path)
    initial = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(initial)
    _create_view(initial.LISTING_FEATURES_SQL)
    op.drop_table("listing_details")
