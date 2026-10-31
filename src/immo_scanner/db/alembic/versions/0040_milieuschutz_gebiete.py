"""Official Milieuschutzgebiet polygons

One row per Erhaltungsgebiet § 172 Abs. 1 Nr. 2 BauGB. Filled by
`immo load-reference milieuschutz-areas`.

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "milieuschutz_gebiete",
        sa.Column("schluessel", sa.String(16), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("bezirk", sa.Text),
        sa.Column("in_force", sa.Date),
        sa.Column("area_ha", sa.Float),
        sa.Column("geom", Geometry("MULTIPOLYGON", srid=4326, spatial_index=False), nullable=False),
    )
    op.create_index(
        "ix_milieuschutz_gebiete_geom",
        "milieuschutz_gebiete",
        ["geom"],
        postgresql_using="gist",
    )


def downgrade() -> None:
    op.drop_index("ix_milieuschutz_gebiete_geom", table_name="milieuschutz_gebiete")
    op.drop_table("milieuschutz_gebiete")
