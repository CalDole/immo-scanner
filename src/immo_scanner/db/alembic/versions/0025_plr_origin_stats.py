"""Residents by origin per Planungsraum and reference date

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plr_origin_stats",
        sa.Column("plr_id", sa.String(8), primary_key=True),
        sa.Column("ref_date", sa.Date, primary_key=True),
        sa.Column("origin_key", sa.String(32), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("count", sa.Integer),
        sa.Column("share", sa.Float),
        sa.Column("label", sa.Text, nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
    )
    op.create_index("ix_plr_origin_stats_key_date", "plr_origin_stats", ["origin_key", "ref_date"])


def downgrade() -> None:
    op.drop_table("plr_origin_stats")
