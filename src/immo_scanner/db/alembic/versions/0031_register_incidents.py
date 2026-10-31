"""Incidents from the Berliner Register chronicle

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "register_incidents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("incident_date", sa.Date, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("bezirk", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("source", sa.Text),
        sa.Column("geom", Geometry("POINT", srid=4326, spatial_index=False)),
        sa.Column("geo_precision", sa.String(16)),
        sa.Column("geocoded_query", sa.Text),
        sa.Column("plr_id", sa.String(8)),
        sa.Column(
            "scraped_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_register_incidents_geom", "register_incidents", ["geom"], postgresql_using="gist"
    )
    op.create_index("ix_register_incidents_incident_date", "register_incidents", ["incident_date"])


def downgrade() -> None:
    op.drop_index("ix_register_incidents_incident_date", table_name="register_incidents")
    op.drop_index("ix_register_incidents_geom", table_name="register_incidents")
    op.drop_table("register_incidents")
