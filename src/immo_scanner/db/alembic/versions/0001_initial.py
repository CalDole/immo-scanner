"""Initial schema: listings, snapshots, reference data and listing_features view

Revision ID: 0001
Revises:
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def point():
    return Geometry("POINT", srid=4326, spatial_index=False)


def multipolygon():
    return Geometry("MULTIPOLYGON", srid=4326, spatial_index=False)


def gist(table: str) -> None:
    op.create_index(f"ix_{table}_geom", table, ["geom"], postgresql_using="gist")


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
    s.floor,
    s.year_built,
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


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    op.create_table(
        "listings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source", sa.String(32), nullable=False, index=True),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("title", sa.Text),
        sa.Column("address", sa.Text),
        sa.Column("postcode", sa.String(5), index=True),
        sa.Column("district", sa.String(128)),
        sa.Column("geom", point()),
        sa.Column("geo_precision", sa.String(16)),
        sa.Column("geocoded_query", sa.Text),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active", index=True),
        sa.Column("is_new_build", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("source", "external_id"),
    )
    gist("listings")

    op.create_table(
        "listing_snapshots",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "listing_id",
            sa.Integer,
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("scraped_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("price", sa.Float),
        sa.Column("living_area", sa.Float),
        sa.Column("rooms", sa.Float),
        sa.Column("floor", sa.Integer),
        sa.Column("year_built", sa.Integer),
        sa.Column("hausgeld", sa.Float),
        sa.Column("raw", JSONB, nullable=False, server_default="{}"),
    )

    op.create_table(
        "geocode_cache",
        sa.Column("query", sa.Text, primary_key=True),
        sa.Column("lat", sa.Float),
        sa.Column("lon", sa.Float),
        sa.Column("precision", sa.String(16)),
        sa.Column("raw", JSONB),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "places",
        sa.Column("name", sa.String(64), primary_key=True),
        sa.Column("address", sa.Text),
        sa.Column("geom", point(), nullable=False),
    )

    op.create_table(
        "stations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("osm_id", sa.BigInteger, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("is_sbahn", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_ubahn", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("lines", sa.Text),
        sa.Column("geom", point(), nullable=False),
    )
    gist("stations")

    op.create_table(
        "lor_planungsraeume",
        sa.Column("plr_id", sa.String(8), primary_key=True),
        sa.Column("plr_name", sa.Text, nullable=False),
        sa.Column("bzr_name", sa.Text),
        sa.Column("pgr_name", sa.Text),
        sa.Column("bezirk", sa.Text),
        sa.Column("population", sa.Integer),
        sa.Column("share_under_18_pct", sa.Float),
        sa.Column("share_18_to_64_pct", sa.Float),
        sa.Column("share_65_plus_pct", sa.Float),
        sa.Column("population_date", sa.String(16)),
        sa.Column("demographics", JSONB),
        sa.Column("geom", multipolygon(), nullable=False),
    )
    gist("lor_planungsraeume")

    op.create_table(
        "wahlbezirke",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("election", sa.Text, nullable=False),
        sa.Column("eligible_voters", sa.Integer),
        sa.Column("voters", sa.Integer),
        sa.Column("valid_votes", sa.Integer),
        sa.Column("turnout_pct", sa.Float),
        sa.Column("party_shares", JSONB, nullable=False, server_default="{}"),
        sa.Column("geom", multipolygon(), nullable=False),
    )
    gist("wahlbezirke")

    op.create_table(
        "commute_times",
        sa.Column(
            "listing_id",
            sa.Integer,
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("minutes", sa.Float),
        sa.Column("mode", sa.String(32), nullable=False, server_default="transit"),
        sa.Column("origin_lat", sa.Float, nullable=False),
        sa.Column("origin_lon", sa.Float, nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.execute(LISTING_FEATURES_SQL)
    op.execute("CREATE UNIQUE INDEX ix_listing_features_id ON listing_features (id)")
    op.execute("CREATE INDEX ix_listing_features_geom ON listing_features USING gist (geom)")
    op.execute("REFRESH MATERIALIZED VIEW listing_features")


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS listing_features")
    for table in (
        "commute_times",
        "wahlbezirke",
        "lor_planungsraeume",
        "stations",
        "places",
        "geocode_cache",
        "listing_snapshots",
        "listings",
    ):
        op.drop_table(table)
