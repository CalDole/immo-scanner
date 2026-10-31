"""Demographic indicators per Planungsraum or Bezirk

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "area_indicators",
        sa.Column("level", sa.String(8), primary_key=True),
        sa.Column("area_id", sa.String(64), primary_key=True),
        sa.Column("topic", sa.String(32), primary_key=True),
        sa.Column("indicator", sa.String(64), primary_key=True),
        sa.Column("label", sa.Text, nullable=False),
        sa.Column("value", sa.Float),
        sa.Column("unit", sa.String(8), nullable=False),
        sa.Column("ref_date", sa.String(32)),
        sa.Column("source", sa.String(64), nullable=False),
    )
    op.create_index("ix_area_indicators_topic", "area_indicators", ["topic"])


def downgrade() -> None:
    op.drop_table("area_indicators")
