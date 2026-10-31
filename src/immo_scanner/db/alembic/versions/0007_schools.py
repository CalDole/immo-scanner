"""Nearest Gymnasiums and Montessori kindergarten on each listing

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SCHOOL_COLUMNS = """
    gym.name_1 AS nearest_gymnasium,
    round(gym.dist_1) AS nearest_gymnasium_m,
    gym.name_2 AS nearest_gymnasium_2,
    round(gym.dist_2) AS nearest_gymnasium_2_m,
    gym.name_3 AS nearest_gymnasium_3,
    round(gym.dist_3) AS nearest_gymnasium_3_m,
    kita.name AS nearest_montessori_kita,
    round(kita.dist) AS nearest_montessori_kita_m,"""

SCHOOL_JOINS = """
LEFT JOIN LATERAL (
    SELECT
        (array_agg(name ORDER BY dist))[1] AS name_1,
        (array_agg(dist ORDER BY dist))[1] AS dist_1,
        (array_agg(name ORDER BY dist))[2] AS name_2,
        (array_agg(dist ORDER BY dist))[2] AS dist_2,
        (array_agg(name ORDER BY dist))[3] AS name_3,
        (array_agg(dist ORDER BY dist))[3] AS dist_3
    FROM (
        SELECT DISTINCT ON (name) name, dist
        FROM (
            SELECT c.name, ST_Distance(c.geom::geography, l.geom::geography) AS dist
            FROM (
                SELECT name, geom FROM schools
                WHERE kind = 'gymnasium' AND l.geom IS NOT NULL
                ORDER BY geom <-> l.geom
                LIMIT 25
            ) c
        ) measured
        ORDER BY name, dist
    ) unique_schools
) gym ON true
LEFT JOIN LATERAL (
    SELECT c.name, ST_Distance(c.geom::geography, l.geom::geography) AS dist
    FROM (
        SELECT name, geom FROM schools
        WHERE kind = 'montessori_kindergarten' AND l.geom IS NOT NULL
        ORDER BY geom <-> l.geom LIMIT 5
    ) c
    ORDER BY dist LIMIT 1
) kita ON true
"""


def _listing_features_sql() -> str:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("0006_areas.py")
    spec = importlib.util.spec_from_file_location("areas", path)
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    rail = "    round(LEAST(sb.dist, ub.dist)) AS nearest_rail_m,\n"
    ub_join = ") ub ON true\n"
    sql = previous.LISTING_FEATURES_SQL
    if rail not in sql or ub_join not in sql:
        raise RuntimeError("listing_features SQL no longer matches the schools migration")
    sql = sql.replace(rail, rail + SCHOOL_COLUMNS + "\n", 1)
    return sql.replace(ub_join, ub_join + SCHOOL_JOINS, 1)


def _create_view(sql: str) -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS listing_features")
    op.execute(sql)
    op.execute("CREATE UNIQUE INDEX ix_listing_features_id ON listing_features (id)")
    op.execute("CREATE INDEX ix_listing_features_geom ON listing_features USING gist (geom)")
    op.execute("REFRESH MATERIALIZED VIEW listing_features")


def upgrade() -> None:
    op.create_table(
        "schools",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("osm_type", sa.String(16), nullable=False),
        sa.Column("osm_id", sa.BigInteger, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("geom", Geometry("POINT", srid=4326, spatial_index=False), nullable=False),
        sa.UniqueConstraint("osm_type", "osm_id"),
    )
    op.create_index("ix_schools_geom", "schools", ["geom"], postgresql_using="gist")
    op.create_index("ix_schools_kind", "schools", ["kind"])
    _create_view(_listing_features_sql())


def downgrade() -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("0006_areas.py")
    spec = importlib.util.spec_from_file_location("areas", path)
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    op.execute("DROP MATERIALIZED VIEW IF EXISTS listing_features")
    op.drop_index("ix_schools_kind", table_name="schools")
    op.drop_index("ix_schools_geom", table_name="schools")
    op.drop_table("schools")
    _create_view(previous.LISTING_FEATURES_SQL)
