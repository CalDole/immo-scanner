"""Cases and frequency from the Berlin crime atlas

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "crime_stats",
        sa.Column("level", sa.String(8), primary_key=True),
        sa.Column("area_id", sa.String(64), primary_key=True),
        sa.Column("year", sa.Integer, primary_key=True),
        sa.Column("offense", sa.String(32), primary_key=True),
        sa.Column("cases", sa.Integer),
        sa.Column("rate", sa.Float),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("label", sa.Text, nullable=False),
    )
    op.create_index("ix_crime_stats_offense_year", "crime_stats", ["offense", "year"])


def downgrade() -> None:
    op.drop_table("crime_stats")
