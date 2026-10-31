"""Geoportal school sites used to place Sekundarschulen

One row per Berlin school number from the WFS layer `schulen`. Filled by
`immo load-reference grundschulen`.

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "schulen",
        sa.Column("bsn", sa.String(8), primary_key=True),
        sa.Column("name", sa.Text),
        sa.Column("schulart", sa.Text),
        sa.Column("bezirk", sa.Text),
        sa.Column("ortsteil", sa.Text),
        sa.Column("geom", Geometry("POINT", srid=4326, spatial_index=False), nullable=False),
    )
    op.create_index("ix_schulen_geom", "schulen", ["geom"], postgresql_using="gist")


def downgrade() -> None:
    op.drop_index("ix_schulen_geom", table_name="schulen")
    op.drop_table("schulen")
