"""English construction-period classes (Baualtersklassen)

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from immo_scanner.reference.building_age import COLUMNS, ROWS

    table = op.create_table(
        "building_age_classes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("class_code", sa.String(1), nullable=False),
        sa.Column("year_from", sa.Integer),
        sa.Column("year_to", sa.Integer),
        sa.Column("exterior_wall_u_value", sa.Text, nullable=False),
        sa.Column("window_u_value", sa.Text, nullable=False),
        sa.Column("roof_ceiling_u_value", sa.Text, nullable=False),
        sa.Column("typical_heating_demand", sa.Text, nullable=False),
        sa.Column("climate_risk_score", sa.Integer, nullable=False),
        sa.Column("risk_level", sa.Text, nullable=False),
        sa.Column("legal_obligations_on_purchase", sa.Text, nullable=False),
    )
    op.bulk_insert(table, [dict(zip(COLUMNS, row, strict=True)) for row in ROWS])


def downgrade() -> None:
    op.drop_table("building_age_classes")
