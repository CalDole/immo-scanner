"""Kleinanzeigen ad details on each listing

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KA_COLUMNS = """    d.balcony_count,
    ka.bedrooms,
    ka.bathrooms,
    ka.apartment_type,
    ka.provision,
    ka.posted_on,
    ka.has_terrace,
    ka.has_fitted_kitchen,
"""
KA_JOIN = (
    "LEFT JOIN listing_details d ON d.listing_id = l.id\n"
    "LEFT JOIN kleinanzeigen_ads ka ON ka.listing_id = l.id\n"
)


def _listing_features_sql() -> str:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("0007_schools.py")
    spec = importlib.util.spec_from_file_location("schools", path)
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    sql = previous._listing_features_sql()
    balcony = "    d.balcony_count,\n"
    details = "LEFT JOIN listing_details d ON d.listing_id = l.id\n"
    if balcony not in sql or details not in sql:
        raise RuntimeError("listing_features SQL no longer matches the Kleinanzeigen migration")
    sql = sql.replace(balcony, KA_COLUMNS, 1)
    return sql.replace(details, KA_JOIN, 1)


def _create_view(sql: str) -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS listing_features")
    op.execute(sql)
    op.execute("CREATE UNIQUE INDEX ix_listing_features_id ON listing_features (id)")
    op.execute("CREATE INDEX ix_listing_features_geom ON listing_features USING gist (geom)")
    op.execute("REFRESH MATERIALIZED VIEW listing_features")


def upgrade() -> None:
    op.create_table(
        "kleinanzeigen_ads",
        sa.Column(
            "listing_id",
            sa.Integer,
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ad_id", sa.String(32), index=True),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("title", sa.Text),
        sa.Column("address", sa.Text),
        sa.Column("posted_on", sa.Date),
        sa.Column("living_area", sa.Float),
        sa.Column("rooms", sa.Float),
        sa.Column("bedrooms", sa.Float),
        sa.Column("bathrooms", sa.Float),
        sa.Column("floor", sa.Integer),
        sa.Column("apartment_type", sa.Text),
        sa.Column("year_built", sa.Integer),
        sa.Column("provision", sa.Text),
        sa.Column("has_balcony", sa.Boolean),
        sa.Column("has_terrace", sa.Boolean),
        sa.Column("has_fitted_kitchen", sa.Boolean),
        sa.Column("has_elevator", sa.Boolean),
        sa.Column("description", sa.Text),
        sa.Column("photo_count", sa.Integer),
        sa.Column("media_dir", sa.Text),
        sa.Column("media", JSONB, nullable=False, server_default="[]"),
        sa.Column("facts", JSONB, nullable=False, server_default="{}"),
        sa.Column("features", JSONB, nullable=False, server_default="[]"),
    )
    _create_view(_listing_features_sql())


def downgrade() -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("0007_schools.py")
    spec = importlib.util.spec_from_file_location("schools", path)
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    _create_view(previous._listing_features_sql())
    op.drop_table("kleinanzeigen_ads")
