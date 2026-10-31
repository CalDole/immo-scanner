"""Grundschulen, their catchments, and the nearest ones on each listing

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CATCHMENT_COLUMNS = """
    catchment.name AS grundschule_name,
    catchment.bsn AS grundschule_bsn,
    gs.name_1 AS nearest_grundschule,
    round(gs.dist_1) AS nearest_grundschule_m,
    gs.name_2 AS nearest_grundschule_2,
    round(gs.dist_2) AS nearest_grundschule_2_m,
    gs.name_3 AS nearest_grundschule_3,
    round(gs.dist_3) AS nearest_grundschule_3_m,"""

CATCHMENT_JOINS = """
LEFT JOIN LATERAL (
    SELECT g.name, g.bsn
    FROM einschulbereiche e
    JOIN grundschulen g ON g.bsn = e.bsn
    WHERE l.geom IS NOT NULL AND ST_Intersects(e.geom, l.geom)
    ORDER BY ST_Area(e.geom::geography)
    LIMIT 1
) catchment ON true
LEFT JOIN LATERAL (
    SELECT
        (array_agg(name ORDER BY dist))[1] AS name_1,
        (array_agg(dist ORDER BY dist))[1] AS dist_1,
        (array_agg(name ORDER BY dist))[2] AS name_2,
        (array_agg(dist ORDER BY dist))[2] AS dist_2,
        (array_agg(name ORDER BY dist))[3] AS name_3,
        (array_agg(dist ORDER BY dist))[3] AS dist_3
    FROM (
        SELECT DISTINCT ON (bsn) name, dist
        FROM (
            SELECT c.bsn, c.name, ST_Distance(c.geom::geography, l.geom::geography) AS dist
            FROM (
                SELECT bsn, name, geom FROM grundschulen
                WHERE l.geom IS NOT NULL
                ORDER BY geom <-> l.geom
                LIMIT 25
            ) c
        ) measured
        ORDER BY bsn, dist
    ) unique_schools
) gs ON true
"""

_KITA_COLUMNS = """    kita.name AS nearest_montessori_kita,
    round(kita.dist) AS nearest_montessori_kita_m,"""

_KITA_JOIN = """) kita ON true
"""


def _volt_migration():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("0024_volt_party_key.py")
    spec = importlib.util.spec_from_file_location("volt_party_key", path)
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    return previous


def _listing_features_sql() -> str:
    sql = _volt_migration()._listing_features_sql()
    if sql.count(_KITA_COLUMNS) != 1 or sql.count(_KITA_JOIN) != 1:
        raise RuntimeError("listing_features SQL no longer matches the Grundschulen migration")
    sql = sql.replace(_KITA_COLUMNS, _KITA_COLUMNS + "\n" + CATCHMENT_COLUMNS, 1)
    return sql.replace(_KITA_JOIN, _KITA_JOIN + CATCHMENT_JOINS, 1)


def _create_view(sql: str) -> None:
    _volt_migration()._yes_only_migration()._listings_migration()._create_view(sql)


def upgrade() -> None:
    op.create_table(
        "grundschulen",
        sa.Column("bsn", sa.String(8), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("schulart", sa.String(64), nullable=False),
        sa.Column("traeger", sa.String(32)),
        sa.Column("address", sa.Text),
        sa.Column("bezirk", sa.Text),
        sa.Column("ortsteil", sa.Text),
        sa.Column("school_year", sa.String(16)),
        sa.Column("ganztag", sa.String(16)),
        sa.Column("europa_schule", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("partner_language", sa.String(64)),
        sa.Column("first_foreign_language", sa.String(64)),
        sa.Column("bilingual", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("students", sa.Integer),
        sa.Column("teachers", sa.Integer),
        sa.Column("geom", Geometry("POINT", srid=4326, spatial_index=False), nullable=False),
    )
    op.create_index("ix_grundschulen_geom", "grundschulen", ["geom"], postgresql_using="gist")
    op.create_table(
        "einschulbereiche",
        sa.Column("esb", sa.String(8), primary_key=True),
        sa.Column("bsn", sa.String(8)),
        sa.Column("bezirk", sa.Text),
        sa.Column("school_year", sa.String(16)),
        sa.Column("geom", Geometry("MULTIPOLYGON", srid=4326, spatial_index=False), nullable=False),
    )
    op.create_index(
        "ix_einschulbereiche_geom", "einschulbereiche", ["geom"], postgresql_using="gist"
    )
    op.create_index("ix_einschulbereiche_bsn", "einschulbereiche", ["bsn"])
    _create_view(_listing_features_sql())


def downgrade() -> None:
    _create_view(_volt_migration()._listing_features_sql())
    op.drop_index("ix_einschulbereiche_bsn", table_name="einschulbereiche")
    op.drop_index("ix_einschulbereiche_geom", table_name="einschulbereiche")
    op.drop_table("einschulbereiche")
    op.drop_index("ix_grundschulen_geom", table_name="grundschulen")
    op.drop_table("grundschulen")
